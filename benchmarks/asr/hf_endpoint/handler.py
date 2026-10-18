"""Standalone HF custom handler template; no Roma or ML imports at module import."""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import threading
import unicodedata
import wave
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Any

_MAX_PCM = 960000  # 30 seconds, mono 16 kHz PCM16.
_MAX_WAV = _MAX_PCM + 65536  # Bound headers/metadata as well as decoded samples.
_MAX_BASE64 = 4 * ((_MAX_WAV + 2) // 3)
_MAX_TEXT = 30000  # Bound model text; serialized JSON is checked separately below.
Inference = Callable[[bytes, str, str], str]
Loader = Callable[[str], Inference]


def _load_model(path: str) -> Inference:
    try:
        from huggingface_hub import constants

        if not constants.HF_HUB_OFFLINE:
            raise RuntimeError("Restart the endpoint with HF_HUB_OFFLINE=1 before SDK imports")
        import torch
        from transformers import AutoModel

        model = AutoModel.from_pretrained(path, trust_remote_code=True, local_files_only=True)
    except Exception:  # noqa: BLE001 -- SDK errors may expose paths or credentials.
        raise RuntimeError(
            "Mounted model unavailable; verify reviewed model files, offline startup and runtime dependencies"
        ) from None

    def infer(pcm: bytes, language: str, decoder: str) -> str:
        import sys

        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        waveform = torch.tensor(samples, dtype=torch.float32).unsqueeze(0) / 32768.0
        with torch.inference_mode():
            return model(waveform, language, decoder)

    return infer


class EndpointHandler:
    """Load the reviewed mounted model once; validate every request before inference.

    The deployment must serialize requests and impose its own native execution
    deadline. A Python handler cannot forcibly cancel a stuck native model call.
    """

    def __init__(self, path: str = "", *, loader: Loader | None = None) -> None:
        if os.environ.get("HF_HUB_OFFLINE") != "1":
            raise RuntimeError("Set HF_HUB_OFFLINE=1 before the endpoint process starts")
        if not path or not Path(path).is_dir():
            raise RuntimeError(
                "Mount the reviewed pinned model directory before endpoint startup"
            )
        try:
            self._infer = (loader or _load_model)(path)
            self._slot = threading.Lock()
        except Exception:  # noqa: BLE001 -- do not expose injected or SDK error text.
            raise RuntimeError(
                "Model initialization failed; check mounted files, offline startup and runtime compatibility"
            ) from None

    def __call__(self, data: dict[str, Any]) -> dict[str, str]:
        if not isinstance(data, dict) or set(data) != {"inputs"}:
            raise ValueError("Expected a JSON object containing only inputs")
        inputs = data["inputs"]
        if not isinstance(inputs, dict) or set(inputs) != {"audio", "language", "decoder"}:
            raise ValueError("inputs must contain audio, language and decoder")
        language, decoder = inputs["language"], inputs["decoder"]
        if language not in ("hi", "gu"):
            raise ValueError(
                "Only hi and gu are supported; English requires the Whisper comparison adapter"
            )
        if decoder not in ("ctc", "rnnt"):
            raise ValueError("decoder must be ctc or rnnt")
        encoded = inputs["audio"]
        if not isinstance(encoded, str) or not encoded or len(encoded) > _MAX_BASE64:
            raise ValueError("audio must be bounded base64 WAV data")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("audio must be valid base64 WAV data") from None
        if len(audio) > _MAX_WAV:
            raise ValueError("WAV payload exceeds its byte limit")
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if (
                    wav.getnchannels(),
                    wav.getframerate(),
                    wav.getsampwidth(),
                    wav.getcomptype(),
                ) != (1, 16000, 2, "NONE"):
                    raise ValueError("WAV must be mono 16000 Hz uncompressed PCM16")
                count = wav.getnframes()
                if not 0 < count <= _MAX_PCM // 2:
                    raise ValueError(
                        "Audio must contain at most 30 seconds of complete PCM16 samples"
                    )
                pcm = wav.readframes(count + 1)
                if len(pcm) != count * 2:
                    raise ValueError("WAV sample data is truncated")
        except (wave.Error, EOFError):
            raise ValueError("audio must contain a valid PCM16 WAV file") from None
        if not self._slot.acquire(blocking=False):
            raise RuntimeError("Speech inference is busy")
        try:
            text = self._infer(pcm, language, decoder)
            if not isinstance(text, str) or len(text) > _MAX_TEXT:
                raise ValueError("invalid transcript")
            text = unicodedata.normalize("NFC", text)
            text = " ".join(
                "".join(
                    str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text
                ).split()
            )
            text.encode("utf-8", errors="strict")
            if (
                len(
                    json.dumps({"text": text, "language": language}, ensure_ascii=True).encode(
                        "ascii"
                    )
                )
                > 131072
            ):
                raise ValueError("transcript exceeds JSON response limit")
        except Exception:  # noqa: BLE001 -- never return model exceptions/transcripts as errors.
            raise RuntimeError(
                "Speech inference failed or returned an invalid transcript"
            ) from None
        finally:
            self._slot.release()
        return {"text": text, "language": language}
