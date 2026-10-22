"""Pinned, cache-only Whisper Turbo on Apple Silicon; no cascades or Hub fallback."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from roma.domain.audio.turn_policy import TurnPolicy
from roma.providers.ai.contracts import STTRequest, STTResult
from roma.providers.ai.mlx_batch import decode_batch, turn_chunks
from roma.providers.ai.silero import SileroFrameProbability
from roma.providers.ai.stt import (
    _PROJECT,
    Inference,
    Loader,
    _language,
    _LocalSTT,
    _STTSetupError,
    _validate_audio,
)

ARTIFACT_SPEC = (
    Path(__file__).resolve().parents[3] / "benchmarks/asr/whisper-turbo-mlx-artifacts.json"
)
DEFAULT_MODEL = "var/roma/models/whisper-large-v3-turbo-mlx-4bit"
# mlx-whisper's ModelHolder is process-global; prevent cross-adapter races as well
# as retaining _LocalSTT's per-adapter timeout/cancellation admission protection.
_NATIVE_SLOT = threading.BoundedSemaphore(1)


def verify_mlx_artifacts(folder: Path) -> dict:
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("Set HF_HUB_OFFLINE=1 before starting local Whisper MLX")
    spec = json.loads(ARTIFACT_SPEC.read_text())
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Approved Whisper MLX snapshot is unavailable")
    if {p.name for p in folder.iterdir() if p.is_file()} != set(spec["files"]):
        raise ValueError("Approved Whisper MLX snapshot file set differs")
    for name, identity in spec["files"].items():
        file = folder / name
        if file.is_symlink() or not file.is_file() or file.stat().st_size != identity["bytes"]:
            raise ValueError("Approved Whisper MLX snapshot size/type differs")
        checksum = hashlib.sha256()
        with file.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                checksum.update(block)
        if checksum.hexdigest() != identity["sha256"]:
            raise ValueError("Approved Whisper MLX snapshot checksum differs")
    return spec


class WhisperMLXSTT(_LocalSTT):
    """One local model with bounded mixed-speech batching; MLX imports stay lazy."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL,
        timeout_secs: float = 30,
        max_audio_secs: float = 30,
        loader: Loader | None = None,
        policy: TurnPolicy | None = None,
    ) -> None:
        self.model_id = model_id
        self.policy = policy or TurnPolicy()
        super().__init__(loader or self._load, timeout_secs, max_audio_secs)

    def _load(self) -> Inference:
        folder = Path(self.model_id)
        try:
            verify_mlx_artifacts(folder)
        except (ValueError, OSError) as exc:
            raise _STTSetupError(
                str(exc)
                if isinstance(exc, ValueError)
                else "Approved Whisper MLX snapshot is unavailable"
            ) from None
        try:
            import mlx.core as mx
            import mlx_whisper
            import numpy as np
        except ImportError:
            raise _STTSetupError(
                "Install the optional mlx-asr runtime on Apple Silicon; no model download is attempted"
            ) from None

        scorer = SileroFrameProbability()

        def infer(audio: bytes, language: str | None) -> STTResult:
            if not _NATIVE_SLOT.acquire(blocking=False):
                raise _STTSetupError("Whisper MLX worker is busy; no queued native inference")
            try:
                mx.set_cache_limit(256 * 1024 * 1024)
                mixed = language in {"gu-en", "hi-en"}
                base = language.split("-")[0] if mixed and language is not None else language
                parts = turn_chunks(audio, scorer, language, self.policy)
                if len(parts) == 2:
                    return STTResult(decode_batch(str(folder.resolve()), parts, base), base)
                samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
                result = mlx_whisper.transcribe(
                    samples,
                    path_or_hf_repo=str(folder.resolve()),
                    language=base,
                    task="transcribe",
                    temperature=0,
                    condition_on_previous_text=False,
                    without_timestamps=True,
                    sample_len=440,
                    verbose=None,
                )
                return STTResult(result["text"], result["language"])
            finally:
                try:
                    mx.clear_cache()
                finally:
                    _NATIVE_SLOT.release()

        return infer

    async def transcribe(self, request: STTRequest) -> STTResult:
        _validate_audio(request, self.max_audio_secs)
        base = _language(request.language_hint, _PROJECT)
        route = request.metadata.get("local_language_route")
        if route is not None and not isinstance(route, str):
            raise ValueError("Invalid local ASR language route metadata")
        if route in {"gu-en", "hi-en"}:
            if route.split("-")[0] != base:
                raise ValueError("Local ASR route disagrees with its model language hint")
            return await self._run(request, route)
        return await self._run(request, base)
