"""Section 19 reproducible synthetic TTS exercise; no paid/network inference."""

import asyncio
import hashlib
import json
import math
import struct
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.contracts import TTSRequest, TTSResult
from roma.services.tts_service import SpeechSynthesisService, normalize_tts_text

SAMPLES = {
    "hi": (
        "नमस्ते, मैं रोमा हूँ।",
        "आप Digital Marketing में SEO और Google Ads सीख सकते हैं। आपकी कक्षा 2026-11-05 को है।",
        "आप Digital Marketing में SEO और Google Ads सीख सकते हैं। अभ्यास के लिए पहले विषय चुनिए और फिर अपना प्रश्न पूछिए। "
        * 2,
    ),
    "gu": (
        "નમસ્તે, હું રોમા છું.",
        "તમે Digital Marketing માં SEO અને Google Ads શીખી શકો છો. તમારો વર્ગ 2026-11-05 ના રોજ છે.",
        "તમે Digital Marketing માં SEO અને Google Ads શીખી શકો છો. અભ્યાસ માટે પહેલા વિષય પસંદ કરો અને પછી તમારો પ્રશ્ન પૂછો. "
        * 2,
    ),
    "en": (
        "Hello, I am Roma.",
        "You can learn SEO and Google Ads in Digital Marketing. Your class is on 2026-11-05.",
        "You can learn SEO and Google Ads in Digital Marketing. Choose a topic for practice and then ask your question. "
        * 2,
    ),
}


class SyntheticPCMProvider:
    """Deterministic tone tests transport and timings; it never claims to speak text."""

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        rate = request.format.sample_rate_hz
        duration = max(0.1, min(10, len(request.text) / 100))
        audio = struct.pack(
            "<" + "h" * int(rate * duration),
            *(
                round(1000 * math.sin(2 * math.pi * 440 * i / rate))
                for i in range(int(rate * duration))
            ),
        )
        return TTSResult(audio, request.format)


async def report(
    service: SpeechSynthesisService,
    *,
    provider: str = "synthetic_pcm",
    languages: tuple[str, ...] = ("hi", "gu", "en"),
    audio_dir: Path | None = None,
) -> dict:
    if audio_dir is not None:
        await asyncio.to_thread(audio_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
    rows: list[dict] = []
    recorded_at = datetime.now(UTC).isoformat()
    started = perf_counter()
    for lang in languages:
        for index, text in enumerate(SAMPLES[lang]):
            for repetition in range(2):
                name = ("short", "medium", "long")[index]
                case_started = perf_counter()
                version = "unresolved-local-artifacts"
                identity_resolved = False
                try:
                    # These fixed lab prompts normalize exactly as the service's request.
                    # Resolve provenance before inference so a failed attempt keeps it.
                    request = TTSRequest(text, language=lang, voice=service.default_voice)
                    identity_method = getattr(service.provider, "cache_identity", None)
                    async with asyncio.timeout(service.timeout_secs):
                        version = (
                            await identity_method(
                                TTSRequest(
                                    normalize_tts_text(text, lang),
                                    language=lang,
                                    voice=service.default_voice,
                                )
                            )
                            if identity_method
                            else service.model_version
                        )
                    if type(version) is not str or not version or len(version) > 256:
                        version = "unresolved-local-artifacts"
                        raise ValueError("TTS provider returned an invalid model identity")
                    identity_resolved = True
                    result = await service.synthesize(
                        request,
                        SafetyContext("synthetic-tts-lab", len(rows) + 1, "lab"),
                    )
                except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                    rows.append(
                        {
                            "language": lang,
                            "length": name,
                            "repetition": repetition,
                            "characters": len(text),
                            "model_version": version,
                            "model_identity_resolved": identity_resolved,
                            "status": "error",
                            "error_code": type(exc).__name__,
                            "cache_status": "error",
                            "request_elapsed_ms": (perf_counter() - case_started) * 1000,
                        }
                    )
                    continue
                rows.append(
                    {
                        "language": lang,
                        "status": "ok",
                        "length": name,
                        "repetition": repetition,
                        "characters": len(result.text),
                        "model_version": result.model_version,
                        "model_identity_resolved": True,
                        "cache_status": result.cache_status,
                        "synthesis_ms": result.synthesis_ms,
                        "elapsed_ms": (perf_counter() - case_started) * 1000,
                        "rtf": result.rtf,
                        "audio_ms": result.result.duration_ms,
                        "audio_sha256": hashlib.sha256(result.result.audio).hexdigest(),
                        "sample_rate_hz": result.result.format.sample_rate_hz,
                        "voice": service.default_voice,
                        "mulaw_frames": len(result.mulaw_frames),
                        "audit_ok": result.audit_ok,
                    }
                )
                if audio_dir is not None and repetition == 0:
                    path = audio_dir / f"{lang}-{name}.wav"
                    with wave.open(str(path), "wb") as target:
                        target.setnchannels(1)
                        target.setsampwidth(2)
                        target.setframerate(result.result.format.sample_rate_hz)
                        target.writeframes(result.result.audio)
                    path.chmod(0o600)
    manifest_path = getattr(service.provider, "manifest_path", "")
    try:
        manifest_bytes = (
            await asyncio.to_thread(Path(manifest_path).read_bytes)
            if manifest_path
            else b"synthetic-pcm-v1"
            if provider == "synthetic_pcm"
            else b"unavailable-manifest"
        )
    except OSError:
        manifest_bytes = b"unavailable-manifest"
    dataset_hash = hashlib.sha256(
        json.dumps(SAMPLES, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "schema": "section19-tts-v1",
        "recorded_at": recorded_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "model_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "dataset_sha256": dataset_hash,
        "dataset_version": "section19-prompts-v1",
        "provider": provider,
        "synthetic_only": provider == "synthetic_pcm",
        "model_downloads": False,
        "network_calls": False,
        "native_review_passed": False,
        "errors": sum(row["status"] == "error" for row in rows),
        "expected_cases": len(languages) * 6,
        "elapsed_secs": perf_counter() - started,
        "cache": {
            "hits": service.hits,
            "misses": service.misses,
            "stores": service.stores,
            "coalesced": service.coalesced,
        },
        "cases": rows,
    }
