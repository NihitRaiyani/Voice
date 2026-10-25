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

from roma.core.turn_epoch import require_current_epoch
from roma.providers.ai._indic_tts_protocol import (
    MAX_FRAME_BYTES,
    RESIDENT_PROFILE,
    decode_frame,
    encode_frame,
)
from roma.providers.ai.contracts import AudioFormat, ProviderUnavailable, TTSRequest, TTSResult
from roma.providers.reliability import FailureKind, ProviderFailure, remaining_timeout

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
    if request.turn_epoch is not None and (
        type(request.turn_epoch) is not int or request.turn_epoch < 0
    ):
        raise ValueError("TTS epoch must be a nonnegative integer")
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
        self._manifest_slot = threading.BoundedSemaphore(1)
        self._resident_enabled = False
        self._resident_lock = asyncio.Lock()
        self._resident_admission = threading.BoundedSemaphore(2)
        self._resident_process = None
        self._resident_identity = None
        self._request_id = 0
        self._runtime_verified: tuple[tuple, str] | None = None

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
            runtime_snapshot = snapshot[-1]
            if self._runtime_verified is None or self._runtime_verified[0] != runtime_snapshot:
                with interpreter.open("rb") as source:
                    runtime_digest = hashlib.file_digest(source, "sha256").hexdigest()
                self._runtime_verified = (runtime_snapshot, runtime_digest)
            identity = hashlib.sha256(
                raw + str(interpreter.resolve()).encode() + self._runtime_verified[1].encode()
            ).hexdigest()
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

    async def _manifest_async(self, request):
        if remaining_timeout(self.timeout_secs) <= 0:
            raise ProviderFailure(FailureKind.TIMEOUT)
        if not self._manifest_slot.acquire(blocking=False):
            raise ProviderUnavailable("TTS manifest worker is busy")
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def deliver(result, error):
            if future.done():
                return
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

        def work():
            result = error = None
            try:
                result = self._manifest(request)
            except Exception as exc:  # noqa: BLE001 -- preserve existing bounded validation errors.
                error = exc
            finally:
                self._manifest_slot.release()
            try:
                loop.call_soon_threadsafe(deliver, result, error)
            except RuntimeError:
                pass  # The caller loop may close while native hashing finishes.

        try:
            threading.Thread(target=work, name="roma-tts-manifest", daemon=True).start()
        except RuntimeError:
            self._manifest_slot.release()
            raise ProviderUnavailable("TTS manifest worker could not start") from None
        try:
            async with asyncio.timeout(remaining_timeout(self.timeout_secs)):
                return await future
        except TimeoutError:
            raise ProviderFailure(FailureKind.TIMEOUT) from None

    async def cache_identity(self, request: TTSRequest) -> str:
        identity, _ = await self._manifest_async(request)
        profile = ":" + RESIDENT_PROFILE if self._resident_enabled else ""
        return "indic-tts:" + identity + profile

    async def warmup(self, request: TTSRequest | None = None) -> None:
        """Retain one cache-only model and discard a bounded safe warmup inference."""
        request = request or TTSRequest("नमस्ते", voice=self.voice)
        validate_request(request, self.max_text_bytes)
        self._resident_enabled = True
        await self._resident_operation(request, synthesize=True)

    @staticmethod
    async def _kill_and_reap(process):

        # Keep ownership until wait() completes, even after repeated cancellation.
        async def reap():
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            # Drain in fixed chunks after kill: unread PIPE backpressure can keep
            # asyncio's subprocess transport from completing wait(). Never retain logs.
            stdout = getattr(process, "stdout", None)
            if stdout is not None:
                while await stdout.read(MAX_FRAME_BYTES):
                    pass
            await process.wait()

        cleanup = asyncio.create_task(reap())
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop_resident(self):
        if self._resident_process is None:
            return
        try:
            await self._kill_and_reap(self._resident_process)
        finally:
            self._resident_process = None
            self._resident_identity = None

    async def aclose(self) -> None:
        """Release the retained runtime; an active request finishes within its bound."""
        async with self._resident_lock:
            await self._stop_resident()

    async def _exchange(self, payload, expected):
        self._request_id += 1
        request_id = self._request_id
        frame = encode_frame({**payload, "id": request_id})
        process = self._resident_process
        process.stdin.write(frame)
        await process.stdin.drain()
        reply = decode_frame(await process.stdout.readline())
        if reply != {"id": request_id, "status": expected, "turn_epoch": payload["turn_epoch"]}:
            raise ValueError("Invalid Indic-TTS reply")

    async def _start_resident(self):
        env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "OMP_NUM_THREADS": "2",
            "NUMBA_NUM_THREADS": "2",
        }
        self._resident_process = await self._launch_process(
            self.python_path,
            str(Path(__file__).with_name("_indic_tts_worker.py")),
            "--resident",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=MAX_FRAME_BYTES,
        )

    async def _launch_process(self, *args, **kwargs):
        launch = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
        try:
            return await asyncio.shield(launch)
        except asyncio.CancelledError:
            # Creation can finish after caller cancellation; keep native ownership.
            while not launch.done():
                try:
                    await asyncio.shield(launch)
                except asyncio.CancelledError:
                    continue
            await self._kill_and_reap(launch.result())
            raise

    @staticmethod
    def _read_pcm(output, request, max_audio_secs):
        maximum = int(request.format.sample_rate_hz * max_audio_secs) * 2
        if output.is_symlink() or not output.is_file():
            raise ProviderUnavailable("Indic-TTS produced invalid PCM output")
        if not 0 < output.stat().st_size <= maximum:
            raise ProviderUnavailable("Indic-TTS produced empty or oversized PCM")
        with output.open("rb") as source:
            audio = source.read(maximum + 1)
        if len(audio) % 2 or not 0 < len(audio) <= maximum:
            raise ProviderUnavailable("Indic-TTS produced incomplete PCM16")
        return TTSResult(
            audio,
            request.format,
            round(len(audio) / 2 / request.format.sample_rate_hz * 1000),
            turn_epoch=request.turn_epoch,
        )

    async def _resident_operation(self, request, *, synthesize):
        validate_request(request, self.max_text_bytes)
        if not self._resident_admission.acquire(blocking=False):
            raise ProviderUnavailable("Indic-TTS resident worker is busy")
        try:
            async with asyncio.timeout(remaining_timeout(self.timeout_secs)):
                async with self._resident_lock:
                    require_current_epoch()
                    try:
                        identity, payload = await self._manifest_async(request)
                        route = (
                            identity,
                            language_code(request.language),
                            request.voice or self.voice,
                            RESIDENT_PROFILE,
                        )
                        if self._resident_identity != route or (
                            self._resident_process is not None
                            and self._resident_process.returncode is not None
                        ):
                            await self._stop_resident()
                        if self._resident_process is None:
                            await self._start_resident()
                            await self._exchange(
                                {
                                    **payload,
                                    "op": "load",
                                    "voice": route[2],
                                    "profile": RESIDENT_PROFILE,
                                    "turn_epoch": None,
                                },
                                "ready",
                            )
                            self._resident_identity = route
                        if not synthesize:
                            return None
                        with tempfile.TemporaryDirectory(prefix="roma-tts-") as directory:
                            output = Path(directory) / "speech.pcm"
                            await self._exchange(
                                {
                                    "op": "synthesize",
                                    "text": request.text,
                                    "sample_rate": request.format.sample_rate_hz,
                                    "max_audio_secs": self.max_audio_secs,
                                    "output": str(output),
                                    "turn_epoch": request.turn_epoch,
                                },
                                "ok",
                            )
                            require_current_epoch()
                            return self._read_pcm(output, request, self.max_audio_secs)
                    except BaseException:
                        await self._stop_resident()
                        raise
        except TimeoutError:
            raise ProviderFailure(FailureKind.TIMEOUT) from None
        except (OSError, ValueError, EOFError):
            raise ProviderUnavailable("Indic-TTS resident worker failed") from None
        finally:
            self._resident_admission.release()

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        if self._resident_enabled:
            return await self._resident_operation(request, synthesize=True)
        validate_request(request, self.max_text_bytes)
        if not self._resident_admission.acquire(blocking=False):
            raise ProviderUnavailable("Indic-TTS worker is busy")
        try:
            async with asyncio.timeout(remaining_timeout(self.timeout_secs)):
                async with self._resident_lock:
                    # warmup may have activated resident mode during admission.
                    if not self._resident_enabled:
                        require_current_epoch()
                        return await self._synthesize_once(request)
        except TimeoutError:
            raise ProviderFailure(FailureKind.TIMEOUT) from None
        finally:
            self._resident_admission.release()
        return await self._resident_operation(request, synthesize=True)

    async def _synthesize_once(self, request: TTSRequest) -> TTSResult:
        if remaining_timeout(self.timeout_secs) <= 0:
            raise ProviderFailure(FailureKind.TIMEOUT)
        _, payload = await self._manifest_async(request)
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
            if remaining_timeout(self.timeout_secs) <= 0:
                raise ProviderFailure(FailureKind.TIMEOUT)
            process = await self._launch_process(
                self.python_path,
                str(worker),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            try:
                async with asyncio.timeout(remaining_timeout(self.timeout_secs)):
                    await process.communicate(json.dumps(payload).encode())
                if process.returncode != 0 or not output.is_file():
                    raise ProviderUnavailable(
                        "Indic-TTS worker failed: verify pinned runtime/checkpoint compatibility "
                        "and native language dependencies in the isolated environment"
                    )
                require_current_epoch()
                return self._read_pcm(output, request, self.max_audio_secs)
            finally:
                await self._kill_and_reap(process)
