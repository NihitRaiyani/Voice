"""Opt-in cloud ASR labs; constructors never contact services or load model weights.

The public Indic community Space receives audio and language/decoder only, never an HF
access token. Whisper receives a token only at the fixed HF inference router. Community
availability and provider credits are external prerequisites, not production guarantees.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import threading
import wave
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

if TYPE_CHECKING:
    import httpx
else:
    try:
        import httpx
    except ImportError:
        httpx = None

from roma.providers.ai.contracts import ProviderUnavailable, STTRequest, STTResult
from roma.providers.ai.stt import _language, _limits, _validate_audio, normalize_transcript

_INDIC = frozenset({"hi", "gu"})
_PROJECT = frozenset({"hi", "gu", "en"})
_SPACE = "https://1219naveen-indicconformer-asr.hf.space"
_WHISPER_SPACE = "https://hf-audio-whisper-large-v3.hf.space"
_MODELS = frozenset({"openai/whisper-large-v3", "openai/whisper-large-v3-turbo"})
_MAX_RESPONSE = 128 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class HFASRQuotaExceeded(ProviderUnavailable):
    """Fixed service prerequisite failure; never carries the remote error body."""

    def __init__(self) -> None:
        super().__init__("HF ASR hosted GPU quota exhausted; hosted capacity is required")


class _HFResponseError(ProviderUnavailable):
    """Adapter-owned fixed errors, safe to preserve across the transport boundary."""


def _endpoint(value: str) -> str:
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("Indic ASR endpoint must be an HTTPS hf.space root URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.hf\.space", parsed.hostname)
        or parsed.netloc != parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Indic ASR endpoint must be an HTTPS hf.space root URL")
    return f"https://{parsed.hostname}"


def _public_benchmark(request: STTRequest) -> None:
    if request.metadata.get("data_classification") != "public_benchmark":
        raise ValueError("Public ASR Spaces require an explicit public_benchmark declaration")


def _wav(audio: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(audio)
    return output.getvalue()


def _check_response(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise _HFResponseError("HF ASR access denied; check token and model access")
    if response.status_code == 402:
        raise _HFResponseError("HF ASR credits unavailable")
    if response.status_code == 429:
        raise _HFResponseError("HF ASR rate limit reached")
    if response.status_code != 200:
        raise _HFResponseError("HF ASR service unavailable")
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise _HFResponseError("HF ASR response must be uncompressed")


async def _chunks(response: httpx.Response):
    _check_response(response)
    size = 0
    async for chunk in response.aiter_bytes(chunk_size=4096):
        size += len(chunk)
        if size > _MAX_RESPONSE:
            raise _HFResponseError("HF ASR response exceeds the limit")
        yield chunk


async def _json_response(response: httpx.Response) -> Any:
    body = bytearray()
    async for chunk in _chunks(response):
        body.extend(chunk)
    return json.loads(body)


async def _completed_text(response: httpx.Response, count: int) -> str:
    # Bound bytes before finding/decoding lines: a peer cannot allocate an unbounded
    # SSE line by omitting newline delimiters. The transaction timeout includes heartbeats.
    pending = bytearray()
    event = ""
    data: list[str] = []
    async for chunk in _chunks(response):
        pending.extend(chunk)
        while b"\n" in pending:
            raw_line, _, remainder = pending.partition(b"\n")
            pending = bytearray(remainder)
            line = raw_line.rstrip(b"\r").decode("utf-8")
            if not line:
                if event == "error":
                    raise _HFResponseError("HF ASR community inference failed")
                if event == "complete":
                    result = json.loads("\n".join(data))
                    if (
                        not isinstance(result, list)
                        or len(result) != count
                        or not isinstance(result[0], str)
                        or (count == 2 and not isinstance(result[1], dict))
                    ):
                        raise _HFResponseError("HF ASR response is invalid")
                    return result[0]
                event, data = "", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
    raise _HFResponseError("HF ASR stream ended without a completed transcript")


async def _completed_queue_text(response: httpx.Response, event_id: str) -> str:
    # Gradio's simplified /call SSE drops output.error and returns error:null.
    # Read the SAME submitted job through its isolated queue session instead.
    pending = bytearray()
    async for chunk in _chunks(response):
        pending.extend(chunk)
        while b"\n" in pending:
            raw_line, _, remainder = pending.partition(b"\n")
            pending = bytearray(remainder)
            line = raw_line.rstrip(b"\r").decode("utf-8")
            if not line.startswith("data:"):
                continue
            message = json.loads(line[5:])
            if not isinstance(message, dict):
                raise _HFResponseError("HF ASR response is invalid")
            if message.get("msg") != "process_completed":
                continue
            if message.get("event_id") != event_id:
                raise _HFResponseError("HF ASR event identity differs")
            output = message.get("output")
            if not isinstance(output, dict):
                raise _HFResponseError("HF ASR response is invalid")
            if message.get("success") is not True:
                error = output.get("error")
                if isinstance(error, str) and "exceeded your ZeroGPU runs limit" in error:
                    raise HFASRQuotaExceeded()
                raise _HFResponseError("HF ASR community inference failed")
            result = output.get("data")
            if (
                not isinstance(result, list)
                or len(result) != 1
                or not isinstance(result[0], str)
            ):
                raise _HFResponseError("HF ASR response is invalid")
            return result[0]
    raise _HFResponseError("HF ASR stream ended without a completed transcript")


class _CloudSTT:
    def __init__(self, timeout_secs: float, max_audio_secs: float, transport: Any) -> None:
        _limits(timeout_secs, max_audio_secs)
        self.timeout_secs, self.max_audio_secs = timeout_secs, max_audio_secs
        self._transport = transport
        self._slot = threading.BoundedSemaphore(1)

    async def _run(self, request: STTRequest, language: str | None) -> STTResult:
        if not self._slot.acquire(blocking=False):
            raise _HFResponseError("HF ASR endpoint is busy")
        try:
            if httpx is None:
                raise _HFResponseError("HF ASR requires the optional httpx runtime")
            async with asyncio.timeout(self.timeout_secs):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self.timeout_secs,
                    follow_redirects=False,
                    trust_env=False,
                    headers={
                        "Accept-Encoding": "identity",
                        **getattr(self, "_auth_headers", {}),
                    },
                ) as client:
                    return await self._transcribe(client, _wav(request.audio), language)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise _HFResponseError("HF ASR request timed out") from None
        except HFASRQuotaExceeded:
            raise
        except _HFResponseError as exc:
            raise ProviderUnavailable(str(exc)) from None
        except Exception:  # noqa: BLE001 -- never expose bodies, URLs, tokens or caller audio.
            raise _HFResponseError(
                "HF ASR request failed; check service availability"
            ) from None
        finally:
            self._slot.release()

    async def _transcribe(
        self, client: httpx.AsyncClient, audio: bytes, language: str | None
    ) -> STTResult:
        raise NotImplementedError


class _GradioSTT(_CloudSTT):
    endpoint_url: str
    _output_count: int
    _forced_language: bool
    api_name = "transcribe"
    _detailed_queue = False

    def _data(self, path: str, language: str | None) -> list:
        raise NotImplementedError

    async def _transcribe(
        self, client: httpx.AsyncClient, audio: bytes, language: str | None
    ) -> STTResult:
        async with client.stream(
            "POST",
            f"{self.endpoint_url}/gradio_api/upload",
            files={"files": ("speech.wav", audio, "audio/wav")},
        ) as response:
            paths = await _json_response(response)
        if (
            not isinstance(paths, list)
            or len(paths) != 1
            or not isinstance(paths[0], str)
            or not paths[0].startswith("/")
            or paths[0].startswith("//")
            or len(paths[0]) > 2048
            or any(ord(c) < 32 or ord(c) == 127 for c in paths[0])
        ):
            raise _HFResponseError("HF ASR upload response is invalid")
        payload: dict[str, Any] = {"data": self._data(paths[0], language)}
        session = uuid4().hex
        if self._detailed_queue:
            payload["session_hash"] = session
        async with client.stream(
            "POST",
            f"{self.endpoint_url}/gradio_api/call/{self.api_name}",
            json=payload,
        ) as response:
            result = await _json_response(response)
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if not isinstance(event_id, str) or not _ID.fullmatch(event_id):
            raise _HFResponseError("HF ASR event response is invalid")
        stream_url = (
            f"{self.endpoint_url}/gradio_api/queue/data?session_hash={session}"
            if self._detailed_queue
            else f"{self.endpoint_url}/gradio_api/call/{self.api_name}/{event_id}"
        )
        async with client.stream("GET", stream_url) as response:
            text = (
                await _completed_queue_text(response, event_id)
                if self._detailed_queue
                else await _completed_text(response, self._output_count)
            )
        return STTResult(
            normalize_transcript(text), language if self._forced_language else None
        )


class GradioIndicSTT(_GradioSTT):
    """Public community lab using Gradio's upload/call/SSE protocol, without auth."""

    _output_count = 2
    _forced_language = True

    def __init__(
        self,
        *,
        endpoint_url: str = _SPACE,
        language: str = "hi",
        decoder: str = "ctc",
        timeout_secs: float = 30,
        max_audio_secs: float = 30,
        transport: Any = None,
    ) -> None:
        self.endpoint_url = _endpoint(endpoint_url)
        self.language = _language(language, _INDIC)
        if self.language is None:
            raise ValueError("Indic ASR requires Hindi or Gujarati")
        if decoder not in {"ctc", "rnnt"}:
            raise ValueError("IndicConformer decoder must be ctc or rnnt")
        self.decoder = decoder
        super().__init__(timeout_secs, max_audio_secs, transport)

    async def transcribe(self, request: STTRequest) -> STTResult:
        _public_benchmark(request)
        _validate_audio(request, self.max_audio_secs)
        language = _language(request.language_hint, _INDIC) or self.language
        return await self._run(request, language)

    def _data(self, path: str, language: str | None) -> list:
        return [
            {"path": path, "meta": {"_type": "gradio.FileData"}},
            language,
            self.decoder,
            False,
        ]


class GradioWhisperSTT(_GradioSTT):
    """Official public Whisper Space; auto-recognition, no token or forced hint."""

    _output_count = 1
    _forced_language = False
    _detailed_queue = True

    def __init__(
        self,
        *,
        endpoint_url: str = _WHISPER_SPACE,
        api_name: str = "transcribe",
        token: str | None = None,
        timeout_secs: float = 30,
        max_audio_secs: float = 30,
        transport: Any = None,
    ) -> None:
        self.endpoint_url = _endpoint(endpoint_url)
        if api_name not in {"predict", "transcribe"}:
            raise ValueError("Whisper API must be predict or transcribe")
        self.api_name = api_name
        self._auth_headers: dict[str, str] = {}
        if token is not None:
            if self.endpoint_url not in {
                "https://hf-audio-whisper-large-v3.hf.space",
                "https://openai-whisper.hf.space",
            }:
                raise ValueError("Whisper authentication requires an official reviewed Space")
            if (
                not isinstance(token, str)
                or not token
                or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
            ):
                raise ValueError("Whisper authentication requires a valid HF token")
            # Official gradio_client protocol: HF edge authentication, not app OAuth.
            # No redirects/proxies; never sent to arbitrary or Indic community hosts.
            self._auth_headers = {"X-HF-Authorization": "Bearer " + token}
        super().__init__(timeout_secs, max_audio_secs, transport)

    async def transcribe(self, request: STTRequest) -> STTResult:
        _public_benchmark(request)
        _validate_audio(request, self.max_audio_secs)
        language = _language(request.language_hint, _PROJECT)
        return await self._run(request, language)

    def _data(self, path: str, language: str | None) -> list:
        return [{"path": path, "meta": {"_type": "gradio.FileData"}}, "transcribe"]


class HFWhisperSTT(_CloudSTT):
    """HF inference-router Whisper; hints are validated but do not force recognition."""

    def __init__(
        self,
        *,
        token: str,
        model_id: str = "openai/whisper-large-v3-turbo",
        timeout_secs: float = 30,
        max_audio_secs: float = 30,
        transport: Any = None,
    ) -> None:
        if (
            not isinstance(token, str)
            or not token
            or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
        ):
            raise ValueError("HF Whisper requires an explicit access token")
        if model_id not in _MODELS:
            raise ValueError("HF Whisper requires an approved model")
        self.model_id, self._token = model_id, token
        super().__init__(timeout_secs, max_audio_secs, transport)

    async def transcribe(self, request: STTRequest) -> STTResult:
        _validate_audio(request, self.max_audio_secs)
        language = _language(request.language_hint, _PROJECT)
        return await self._run(request, language)

    async def _transcribe(
        self, client: httpx.AsyncClient, audio: bytes, language: str | None
    ) -> STTResult:
        async with client.stream(
            "POST",
            f"https://router.huggingface.co/hf-inference/models/{self.model_id}",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "audio/wav"},
            content=audio,
        ) as response:
            result = await _json_response(response)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise _HFResponseError("HF ASR response is invalid")
        reported_language = _language(result.get("language"), _PROJECT)
        return STTResult(normalize_transcript(result["text"]), reported_language)


class HFHostedSTT:
    """Explicit public lab composition: Indic hi/gu and official Whisper English.

    ``token`` is accepted for configuration compatibility and deliberately unused.
    Select HFWhisperSTT separately to authorize use of the authenticated HF router.
    """

    def __init__(
        self,
        *,
        token: str = "",
        endpoint_url: str = _SPACE,
        whisper_endpoint_url: str = _WHISPER_SPACE,
        whisper_api_name: str = "transcribe",
        whisper_token: str | None = None,
        language: str = "hi",
        decoder: str = "ctc",
        timeout_secs: float = 30,
        max_audio_secs: float = 30,
        transport: Any = None,
    ) -> None:
        self.language = _language(language, _PROJECT)
        if self.language is None:
            raise ValueError("HF ASR requires a project language")
        self._indic = GradioIndicSTT(
            endpoint_url=endpoint_url,
            language=language if self.language != "en" else "hi",
            decoder=decoder,
            timeout_secs=timeout_secs,
            max_audio_secs=max_audio_secs,
            transport=transport,
        )
        self._whisper = GradioWhisperSTT(
            endpoint_url=whisper_endpoint_url,
            api_name=whisper_api_name,
            token=whisper_token,
            timeout_secs=timeout_secs,
            max_audio_secs=max_audio_secs,
            transport=transport,
        )
        self._slot = threading.BoundedSemaphore(1)

    async def transcribe(self, request: STTRequest) -> STTResult:
        language = _language(request.language_hint, _PROJECT) or self.language
        if not self._slot.acquire(blocking=False):
            raise _HFResponseError("HF ASR endpoint is busy")
        try:
            provider = self._whisper if language == "en" else self._indic
            return await provider.transcribe(request)
        finally:
            self._slot.release()


__all__ = [
    "GradioIndicSTT",
    "GradioWhisperSTT",
    "HFASRQuotaExceeded",
    "HFHostedSTT",
    "HFWhisperSTT",
]
