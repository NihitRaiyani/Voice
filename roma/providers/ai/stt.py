"""Bounded, opt-in speech adapters; importing this module never loads a model.

Local loaders are cache-only. Inference runs in one daemon worker per adapter; a
cancelled/timed-out caller cannot free its slot until the native work actually
exits. Native inference cannot be forcibly interrupted in-process.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import re
import threading
import unicodedata
import wave
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from roma.providers.ai.contracts import ProviderUnavailable, STTRequest, STTResult

Inference = Callable[[bytes, str | None], STTResult]
Loader = Callable[[], Inference]
_INDIC = frozenset(
    "as bn brx doi gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur".split()
)
_PROJECT = frozenset({"gu", "hi", "en"})
_MAX_TEXT = 32768


def normalize_transcript(text: str) -> str:
    """Normalize NFC, whitespace and decimal digits without rewriting course names."""
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        raise ValueError("STT transcript must be bounded text")
    normalized = unicodedata.normalize("NFC", text)
    normalized = " ".join(
        "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in normalized).split()
    )
    if len(normalized) > _MAX_TEXT:
        raise ValueError("STT transcript must be bounded text after normalization")
    return normalized


def _language(value: str | None, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    # Accept ISO language and India BCP-47 hints; do not guess unrelated locales.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][Ii][Nn])?", value):
        raise ValueError("unsupported STT language hint")
    language = value.replace("_", "-").split("-")[0].lower()
    if language not in allowed:
        raise ValueError(
            "unsupported STT language; English requires the Whisper benchmark adapter"
        )
    return language


def _limits(timeout_secs: float, max_audio_secs: float) -> None:
    if not math.isfinite(timeout_secs) or not 0 < timeout_secs <= 300:
        raise ValueError("STT timeout_secs must be finite and in (0, 300]")
    if not math.isfinite(max_audio_secs) or not 0 < max_audio_secs <= 300:
        raise ValueError("STT max_audio_secs must be finite and in (0, 300]")


def _validate_audio(request: STTRequest, max_audio_secs: float) -> None:
    fmt = request.format
    if (
        type(fmt.sample_rate_hz) is not int
        or type(fmt.channels) is not int
        or (fmt.sample_rate_hz, fmt.channels, fmt.encoding) != (16000, 1, "pcm_s16le")
    ):
        raise ValueError("STT requires mono 16000 Hz pcm_s16le audio; convert before inference")
    if not isinstance(request.audio, bytes) or not request.audio or len(request.audio) % 2:
        raise ValueError("STT audio must be nonempty, sample-aligned PCM16 bytes")
    if len(request.audio) > int(max_audio_secs * 32000):
        raise ValueError("STT audio exceeds the configured duration limit")


def _result(result: STTResult) -> STTResult:
    if not isinstance(result, STTResult):
        raise ValueError("invalid STT model result")
    # A language-detection probability is not a transcript confidence score.
    return STTResult(normalize_transcript(result.text), result.language, None)


class _STTSetupError(ProviderUnavailable):
    """Internal fixed-message setup failure safe to show to an operator."""


class _LocalSTT:
    def __init__(self, loader: Loader, timeout_secs: float, max_audio_secs: float) -> None:
        _limits(timeout_secs, max_audio_secs)
        self.timeout_secs = timeout_secs
        self.max_audio_secs = max_audio_secs
        self._loader = loader
        self._inference: Inference | None = None
        self._slot = threading.BoundedSemaphore(1)

    async def _run(self, request: STTRequest, language: str | None) -> STTResult:
        if not self._slot.acquire(blocking=False):
            raise ProviderUnavailable("STT worker is busy; wait for native inference to finish")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[STTResult] = loop.create_future()

        def deliver(result: STTResult | None, error: BaseException | None) -> None:
            if future.done():
                return
            if error is not None:
                future.set_exception(error)
            else:
                assert result is not None
                future.set_result(result)

        def work() -> None:
            result = None
            error = None
            try:
                if self._inference is None:
                    self._inference = self._loader()
                result = _result(self._inference(request.audio, language))
            except _STTSetupError as exc:
                error = ProviderUnavailable(str(exc))
            except Exception:  # noqa: BLE001 -- SDK exception messages can contain PII/secrets.
                error = ProviderUnavailable(
                    "STT model loading or inference failed; check cached files and runtime compatibility"
                )
            finally:
                self._slot.release()
            try:
                loop.call_soon_threadsafe(deliver, result, error)
            except RuntimeError:
                pass  # Caller loop has already closed after cancellation/timeout.

        worker = threading.Thread(target=work, name="roma-stt", daemon=True)
        try:
            worker.start()
        except RuntimeError:
            self._slot.release()
            raise ProviderUnavailable("STT worker could not start") from None
        try:
            async with asyncio.timeout(self.timeout_secs):
                return await future
        except TimeoutError:
            raise ProviderUnavailable(
                "STT inference timed out; native worker remains busy until it exits"
            ) from None


class IndicConformerSTT(_LocalSTT):
    """AI4Bharat 600M multilingual model, using its documented callable API.

    Official card: https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual
    Remote model code is opt-in and must be pinned/reviewed. HF_HUB_OFFLINE=1 is
    required before importing the Hub SDK, including downloads made by custom code.
    """

    def __init__(
        self,
        *,
        model_id: str = "ai4bharat/indic-conformer-600m-multilingual",
        revision: str | None = None,
        language: str = "hi",
        device: str = "cpu",
        decoder: str = "ctc",
        timeout_secs: float = 30.0,
        max_audio_secs: float = 30.0,
        loader: Loader | None = None,
        trust_remote_code: bool = False,
    ) -> None:
        self.model_id, self.revision, self.device = model_id, revision, device
        self.language = _language(language, _INDIC)
        if decoder not in {"ctc", "rnnt"}:
            raise ValueError("IndicConformer decoder must be ctc or rnnt")
        if device != "cpu":
            raise ValueError(
                "IndicConformer adapter currently supports the documented CPU runtime"
            )
        self.decoder, self.trust_remote_code = decoder, trust_remote_code
        super().__init__(loader or self._load, timeout_secs, max_audio_secs)

    def _load(self) -> Inference:
        if (
            not self.trust_remote_code
            or not self.revision
            or not re.fullmatch(r"[a-fA-F0-9]{40}", self.revision)
        ):
            raise _STTSetupError(
                "IndicConformer requires reviewed custom code, trust_remote_code=True and an immutable 40-character revision"
            )
        if os.environ.get("HF_HUB_OFFLINE") != "1":
            raise _STTSetupError(
                "Set HF_HUB_OFFLINE=1 before starting Python; IndicConformer only uses pre-provisioned cached model files"
            )
        try:
            from huggingface_hub import constants

            if not constants.HF_HUB_OFFLINE:
                raise _STTSetupError(
                    "Restart Python with HF_HUB_OFFLINE=1 before loading IndicConformer"
                )
            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoConfig, AutoModel
        except ImportError:
            raise _STTSetupError(
                "Install the optional IndicConformer runtime separately and provision its gated model cache; no model is downloaded automatically"
            ) from None
        try:
            snapshot = Path(
                snapshot_download(
                    self.model_id,
                    revision=self.revision,
                    local_files_only=True,
                )
            )
            if not snapshot.is_dir() or snapshot.name.casefold() != self.revision.casefold():
                raise ValueError("unverified IndicConformer snapshot")
            config = AutoConfig.from_pretrained(
                str(snapshot),
                trust_remote_code=True,
                local_files_only=True,
            )
            # The upstream custom from_pretrained performs its own unpinned snapshot lookup.
            # Constructing from the reviewed config keeps every asset on this exact revision.
            config.ts_folder = str(snapshot)
            model = AutoModel.from_config(
                config,
                trust_remote_code=True,
            )
        except Exception:  # noqa: BLE001 -- strip SDK paths/tokens and gate responses.
            raise _STTSetupError(
                "IndicConformer cache/runtime unavailable; accept model access terms and provision the pinned model and ONNX dependencies separately"
            ) from None

        def infer(audio: bytes, language: str | None) -> STTResult:
            import sys

            samples = array("h")
            samples.frombytes(audio)
            if sys.byteorder != "little":
                samples.byteswap()
            waveform = torch.tensor(samples, dtype=torch.float32).unsqueeze(0) / 32768.0
            with torch.inference_mode():
                text = model(waveform, language, self.decoder)
            return STTResult(text=text, language=language)

        return infer

    async def transcribe(self, request: STTRequest) -> STTResult:
        _validate_audio(request, self.max_audio_secs)
        language = _language(request.language_hint, _INDIC) or self.language
        return await self._run(request, language)


class WhisperSTT(_LocalSTT):
    """faster-whisper comparison baseline; no VAD or translation inside this seam."""

    def __init__(
        self,
        *,
        model_id: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        timeout_secs: float = 30.0,
        max_audio_secs: float = 30.0,
        loader: Loader | None = None,
    ) -> None:
        self.model_id, self.device, self.compute_type = model_id, device, compute_type
        super().__init__(loader or self._load, timeout_secs, max_audio_secs)

    def _load(self) -> Inference:
        try:
            import numpy as np
            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model
        except ImportError:
            raise _STTSetupError(
                "Install the optional faster-whisper runtime separately and provision its model cache; no model is downloaded automatically"
            ) from None
        try:
            from pathlib import Path

            model_path = (
                self.model_id
                if Path(self.model_id).is_dir()
                else download_model(self.model_id, local_files_only=True)
            )
            if not all(
                (Path(model_path) / name).is_file()
                for name in ("model.bin", "config.json", "tokenizer.json")
            ):
                raise ValueError("incomplete cache")
            model = WhisperModel(
                model_path,
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
                cpu_threads=2,
                num_workers=1,
            )
        except Exception:  # noqa: BLE001 -- never include SDK exception text.
            raise _STTSetupError(
                "Whisper cache/runtime unavailable; provision converted model.bin, config.json and tokenizer.json separately"
            ) from None

        def infer(audio: bytes, language: str | None) -> STTResult:
            samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
            segments, info = model.transcribe(
                samples,
                language=language,
                task="transcribe",
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            # The iterator performs native inference: consume it inside this worker.
            text = " ".join(segment.text for segment in segments)
            return STTResult(text=text, language=info.language)

        return infer

    async def transcribe(self, request: STTRequest) -> STTResult:
        _validate_audio(request, self.max_audio_secs)
        language = _language(request.language_hint, _PROJECT)
        return await self._run(request, language)


class HuggingFaceSTT:
    """Explicit lab endpoint with a CUSTOM handler, not generic HF serverless ASR.

    POST JSON: {"inputs": {"audio": "<base64 WAV>", "language": "hi", "decoder": "ctc"}}.
    Response JSON: {"text": "...", "language": "hi"}. No endpoint is created here.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        token: str,
        language: str = "hi",
        decoder: str = "ctc",
        timeout_secs: float = 30.0,
        max_audio_secs: float = 30.0,
        transport: Any = None,
    ) -> None:
        _limits(timeout_secs, max_audio_secs)
        parsed = urlsplit(endpoint_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "STT endpoint must be HTTPS without credentials, query or fragment"
            )
        if not token or token.strip() != token or any(c.isspace() for c in token):
            raise ValueError("STT endpoint requires an explicit access token")
        if decoder not in {"ctc", "rnnt"}:
            raise ValueError("IndicConformer decoder must be ctc or rnnt")
        self.endpoint_url, self._token = endpoint_url, token
        self.language, self.decoder = _language(language, _INDIC), decoder
        self.timeout_secs, self.max_audio_secs = timeout_secs, max_audio_secs
        self._transport = transport
        self._slot = threading.BoundedSemaphore(1)

    async def transcribe(self, request: STTRequest) -> STTResult:
        _validate_audio(request, self.max_audio_secs)
        language = _language(request.language_hint, _INDIC) or self.language
        if not self._slot.acquire(blocking=False):
            raise ProviderUnavailable("STT endpoint is busy")
        try:
            try:
                import httpx
            except ImportError:
                raise _STTSetupError(
                    "Install the optional httpx runtime for the explicit STT endpoint"
                ) from None
            wav = io.BytesIO()
            with wave.open(wav, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(request.audio)
            payload = {
                "inputs": {
                    "audio": base64.b64encode(wav.getvalue()).decode("ascii"),
                    "language": language,
                    "decoder": self.decoder,
                }
            }
            async with asyncio.timeout(self.timeout_secs):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self.timeout_secs,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint_url,
                        headers={
                            "Authorization": f"Bearer {self._token}",
                            "Accept-Encoding": "identity",
                        },
                        json=payload,
                    ) as response:
                        if response.status_code != 200:
                            raise _STTSetupError(
                                "STT endpoint unavailable; check access, handler and service health"
                            )
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise _STTSetupError(
                                "STT endpoint must return an uncompressed bounded JSON response"
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16384):
                            body.extend(chunk)
                            if len(body) > 131072:
                                raise _STTSetupError("STT endpoint response exceeds the limit")
            result = json.loads(body)
            if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                raise ValueError("invalid endpoint response")
            reported_language = _language(result.get("language", language), _INDIC)
            return _result(STTResult(result["text"], reported_language))
        except asyncio.CancelledError:
            raise
        except _STTSetupError as exc:
            raise ProviderUnavailable(str(exc)) from None
        except Exception:  # noqa: BLE001 -- URLs, bodies and SDK errors can contain secrets.
            raise _STTSetupError(
                "STT endpoint request failed; check timeout, handler and service health"
            ) from None
        finally:
            self._slot.release()


__all__ = ["IndicConformerSTT", "WhisperSTT", "HuggingFaceSTT", "normalize_transcript"]
