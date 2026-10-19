"""Cache-only AI4Bharat FastPitch + HiFiGAN adapter, using an isolated CPU worker.

API source: AI4Bharat/Indic-TTS inference/sample.py and src/inference.py.
The manifest pins the installed runtime version and SHA256 of all five artifacts.
No Hub model names, automatic downloads, hosted calls or production wiring.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

from roma.providers.ai.contracts import AudioFormat, ProviderUnavailable, TTSRequest, TTSResult

FILES = (
    "tts_checkpoint",
    "tts_config_path",
    "tts_speakers_file",
    "vocoder_checkpoint",
    "vocoder_config",
)


def language_code(language: str) -> str:
    if type(language) is not str or language not in {
        "hi",
        "hi-IN",
        "gu",
        "gu-IN",
        "en",
        "en-IN",
        "en-US",
        "en-GB",
    }:
        raise ValueError("TTS supports only explicitly selected Gujarati, Hindi or English")
    return language.split("-")[0]


def validate_request(request: TTSRequest, max_bytes: int = 4096) -> None:
    if not isinstance(request, TTSRequest):
        raise ValueError("TTS requires a TTSRequest")
    language_code(request.language)
    if type(request.text) is not str or not request.text.strip():
        raise ValueError("TTS text must be nonempty")
    if len(request.text.encode("utf-8")) > max_bytes:
        raise ValueError("TTS text exceeds the bounded UTF-8 request size")
    if request.voice is not None and (
        type(request.voice) is not str or request.voice not in {"female", "male"}
    ):
        raise ValueError("TTS requires an explicitly supported female/male voice")
    fmt = request.format
    if (
        not isinstance(fmt, AudioFormat)
        or type(fmt.sample_rate_hz) is not int
        or fmt.sample_rate_hz not in (8000, 16000)
        or type(fmt.channels) is not int
        or fmt.channels != 1
        or type(fmt.encoding) is not str
        or fmt.encoding != "pcm_s16le"
    ):
        raise ValueError("TTS lab accepts only mono PCM16 at integer 8 or 16 kHz")
    if not isinstance(request.metadata, Mapping) or len(request.metadata) > 16:
        raise ValueError("TTS metadata must be a bounded string mapping")
    for key, value in request.metadata.items():
        if (
            type(key) is not str
            or type(value) is not str
            or not key
            or len(key.encode()) > 64
            or len(value.encode()) > 256
        ):
            raise ValueError("TTS metadata keys/values must be bounded strings")


class IndicTTSProvider:
    def __init__(
        self,
        *,
        manifest_path: str = "",
        python_path: str = "",
        voice: str = "female",
        timeout_secs: float = 60,
        max_text_bytes: int = 4096,
        max_audio_secs: float = 30,
    ) -> None:
        if not math.isfinite(timeout_secs) or not 0 < timeout_secs <= 180:
            raise ValueError("TTS timeout must be bounded and finite")
        if not math.isfinite(max_audio_secs) or not 0 < max_audio_secs <= 60:
            raise ValueError("TTS audio duration must be bounded and finite")
        if voice not in {"female", "male"} or not 128 <= max_text_bytes <= 8192:
            raise ValueError("Invalid TTS voice or text bound")
        self.manifest_path = manifest_path
        self.python_path = python_path
        self.voice = voice
        self.timeout_secs = timeout_secs
        self.max_text_bytes = max_text_bytes
        self.max_audio_secs = max_audio_secs
        self._verified: dict[str, tuple[str, dict, tuple]] = {}
        self._manifest_lock = threading.Lock()

    def _manifest(self, request: TTSRequest) -> tuple[str, dict]:
        # Concurrent cache lookups must not hash the same large checkpoint repeatedly.
        with self._manifest_lock:
            return self._read_manifest(request)

    def _read_manifest(self, request: TTSRequest) -> tuple[str, dict]:
        validate_request(request, self.max_text_bytes)
        manifest = Path(self.manifest_path)
        interpreter = Path(self.python_path)
        if (
            not self.manifest_path
            or not manifest.is_file()
            or not self.python_path
            or not interpreter.is_file()
        ):
            raise ProviderUnavailable(
                "Local Indic-TTS needs INDIC_TTS_PYTHON and INDIC_TTS_MANIFEST: provision a "
                "compatible isolated Coqui runtime and reviewed FastPitch/config/speakers "
                "plus HiFiGAN/config artifacts. No weights are downloaded automatically."
            )
        if manifest.stat().st_size > 65536:
            raise ProviderUnavailable("Indic-TTS manifest exceeds its size limit")
        raw = manifest.read_bytes()
        try:
            data = json.loads(raw)
            if data["schema"] != "roma-indic-tts-v1":
                raise ValueError("Unsupported manifest schema")
            if not data["runtime_version"] or data["runtime_distribution"] not in {
                "TTS",
                "coqui-tts",
            }:
                raise ValueError("Pin the installed TTS distribution and version")
            entry = data["languages"][language_code(request.language)]
            voice = request.voice or self.voice
            if voice not in entry["voices"]:
                raise ValueError("Voice unavailable in the selected artifacts")
            paths = {}
            snapshot = []
            for name in FILES:
                artifact = entry["files"][name]
                path = (manifest.parent / artifact["path"]).resolve()
                if not path.is_relative_to(manifest.parent.resolve()) or not path.is_file():
                    raise ValueError("Artifact must exist below the manifest directory")
                digest = artifact["sha256"]
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("Artifact SHA256 required")
                stat = path.stat()
                snapshot.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns))
                paths[name] = str(path)
            runtime_stat = interpreter.stat()
            snapshot.append(
                (
                    str(interpreter),
                    runtime_stat.st_ino,
                    runtime_stat.st_size,
                    runtime_stat.st_mtime_ns,
                )
            )
            identity = hashlib.sha256(raw + str(interpreter.resolve()).encode()).hexdigest()
            key = language_code(request.language)
            previous = self._verified.get(key)
            if previous is None or previous[0] != identity or previous[2] != tuple(snapshot):
                for name, path in paths.items():
                    with open(path, "rb") as source:
                        digest = hashlib.file_digest(source, "sha256").hexdigest()
                    if digest != entry["files"][name]["sha256"]:
                        raise ValueError(
                            "Artifact SHA256 mismatch; update the reviewed manifest"
                        )
                payload = {
                    "paths": paths,
                    "runtime_version": data["runtime_version"],
                    "runtime_distribution": data["runtime_distribution"],
                }
                self._verified[key] = (identity, payload, tuple(snapshot))
            return identity, self._verified[key][1]
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise ProviderUnavailable(
                "Indic-TTS manifest/artifacts invalid: require pinned runtime and matching "
                "SHA256 for acoustic checkpoint/config/speakers and vocoder checkpoint/config"
            ) from exc

    async def cache_identity(self, request: TTSRequest) -> str:
        identity, _ = await asyncio.to_thread(self._manifest, request)
        return "indic-tts:" + identity

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        _, payload = await asyncio.to_thread(self._manifest, request)
        payload = {
            **payload,
            "text": request.text,
            "voice": request.voice or self.voice,
            "sample_rate": request.format.sample_rate_hz,
            "max_audio_secs": self.max_audio_secs,
        }
        worker = Path(__file__).with_name("_indic_tts_worker.py")
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "OMP_NUM_THREADS": "2",
            "NUMBA_NUM_THREADS": "2",
        }
        with tempfile.TemporaryDirectory(prefix="roma-tts-") as directory:
            output = Path(directory) / "speech.pcm"
            payload["output"] = str(output)
            process = await asyncio.create_subprocess_exec(
                self.python_path,
                str(worker),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            try:
                async with asyncio.timeout(self.timeout_secs):
                    await process.communicate(json.dumps(payload).encode())
                if process.returncode != 0 or not output.is_file():
                    raise ProviderUnavailable(
                        "Indic-TTS worker failed: verify pinned runtime/checkpoint compatibility "
                        "and native language dependencies in the isolated environment"
                    )
                maximum = int(request.format.sample_rate_hz * self.max_audio_secs) * 2
                if not 0 < output.stat().st_size <= maximum:
                    raise ProviderUnavailable("Indic-TTS produced empty or oversized PCM")
                audio = output.read_bytes()
                if len(audio) % 2:
                    raise ProviderUnavailable("Indic-TTS produced incomplete PCM16")
                return TTSResult(
                    audio,
                    request.format,
                    round(len(audio) / 2 / request.format.sample_rate_hz * 1000),
                )
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
