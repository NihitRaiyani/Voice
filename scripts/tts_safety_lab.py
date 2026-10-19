"""Opt-in local safety-pronunciation lab; default tones never claim native speech quality."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.tts_settings import TTSSettings  # noqa: E402
from roma.domain.safety.audit import SafetyContext  # noqa: E402
from roma.domain.safety.pronunciation import (  # noqa: E402
    PRONUNCIATION_VERSION,
    PRONUNCIATIONS,
)
from roma.eval.tts import SyntheticPCMProvider  # noqa: E402
from roma.providers.ai.contracts import TTSRequest  # noqa: E402
from roma.providers.ai.registry import build_tts_provider  # noqa: E402
from roma.services.safety_service import InMemorySafetyEventStore, SafetyService  # noqa: E402
from roma.services.tts_service import SpeechSynthesisService  # noqa: E402

# Synthetic probes exercise actual rule activation without retaining arbitrary input.
_PROBES = {
    "fees": "fees 50000",
    "salary": "salary 50000",
    "placement": "placement guaranteed",
    "certificate": "Google certificate",
}


async def report(service, *, provider, store, audio_dir=None) -> dict:
    started = perf_counter()
    recorded_at = datetime.now(UTC).isoformat()
    rows = []
    if audio_dir is not None:
        if not audio_dir.resolve().is_relative_to((ROOT / "var" / "roma").resolve()):
            raise ValueError("Lab audio must remain under ignored var/roma")
        audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Six distinct Hindi misses, then all three caller routes reuse the actual
    # Hindi model/cache. English's money case covers DISCOUNT's shared FEE line.
    for phase, language in (
        ("cold", "hi"),
        ("repeat", "hi"),
        ("repeat", "gu"),
        ("repeat", "en"),
    ):
        for item in PRONUNCIATIONS:
            text = (
                item.canonical_text
                if phase == "cold"
                else _PROBES.get(item.name, item.canonical_text)
            )
            if phase == "repeat" and language == "en" and item.name == "fees":
                text = "discount"
            context = SafetyContext("synthetic-tts-safety-lab", len(rows) + 1, "lab")
            row = {"name": item.name, "phase": phase, "requested_language": language}
            try:
                speech = await service.synthesize(TTSRequest(text, language=language), context)
                row.update(
                    {
                        "status": "ok",
                        "canonical_text": speech.canonical_text,
                        "canonical_sha256": speech.canonical_sha256,
                        "spoken_text": speech.text,
                        "spoken_sha256": speech.spoken_sha256,
                        "spoken_canonical_text": speech.spoken_canonical_text,
                        "spoken_language": speech.spoken_language,
                        "voice": speech.voice,
                        "pronunciation_version": speech.pronunciation_version,
                        "model_version": speech.model_version,
                        "cache_status": speech.cache_status,
                        "synthesis_ms": speech.synthesis_ms,
                        "elapsed_ms": speech.elapsed_ms,
                        "rtf": speech.rtf,
                        "audio_ms": speech.result.duration_ms,
                        "sample_rate_hz": speech.result.format.sample_rate_hz,
                        "audio_sha256": hashlib.sha256(speech.result.audio).hexdigest(),
                        "mulaw_frames": len(speech.mulaw_frames),
                        "audit_ok": speech.audit_ok,
                    }
                )
                if audio_dir is not None and phase == "cold":
                    path = audio_dir / f"{item.name}-hi.wav"
                    with wave.open(str(path), "wb") as target:
                        target.setnchannels(1)
                        target.setsampwidth(2)
                        target.setframerate(speech.result.format.sample_rate_hz)
                        target.writeframes(speech.result.audio)
                    path.chmod(0o600)
            except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                row.update({"status": "error", "error_code": type(exc).__name__})
            rows.append(row)
    manifest = getattr(service.provider, "manifest_path", "")
    manifest_bytes = await asyncio.to_thread(Path(manifest).read_bytes) if manifest else None
    return {
        "schema": "section19-tts-safety-v1",
        "recorded_at": recorded_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "provider": provider,
        "synthetic_only": provider == "synthetic_pcm",
        "model_downloads": False,
        "network_calls": False,
        "native_review_passed": False,
        "policy": "Hindi-base Hinglish for approved fallback pronunciation",
        "pronunciation_version": PRONUNCIATION_VERSION,
        "model_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_bytes
        else None,
        "expected_cases": 24,
        "errors": sum(row["status"] == "error" for row in rows),
        "elapsed_secs": perf_counter() - started,
        "cache": {
            "hits": service.hits,
            "misses": service.misses,
            "stores": service.stores,
            "coalesced": service.coalesced,
        },
        "audit_sink": "synthetic_in_memory",
        "audit_events": [
            {
                "turn": context.turn_number,
                "rule_id": event.rule_id,
                "category": event.category,
                "canonical_replacement": event.replacement_used,
            }
            for context, event in store.entries
        ],
        "cases": rows,
    }


async def run(args) -> dict:
    cfg = TTSSettings(tts_provider="indic_tts" if args.provider == "indic_tts" else "mock")
    provider = (
        build_tts_provider(cfg) if args.provider == "indic_tts" else SyntheticPCMProvider()
    )
    store = InMemorySafetyEventStore()
    service = SpeechSynthesisService(
        provider,
        SafetyService(store),
        model_version="manifest-validated"
        if args.provider == "indic_tts"
        else "synthetic-pcm-v1",
        default_voice=cfg.tts_voice,
        cache_entries=cfg.tts_cache_entries,
        cache_bytes=cfg.tts_cache_bytes,
        max_concurrency=cfg.tts_max_concurrency,
        timeout_secs=cfg.tts_timeout_secs,
        max_text_bytes=cfg.tts_max_text_bytes,
        max_audio_secs=cfg.tts_max_audio_secs,
    )
    return await report(service, provider=args.provider, store=store, audio_dir=args.audio_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=("synthetic_pcm", "indic_tts"), default="synthetic_pcm"
    )
    parser.add_argument("--audio-dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        payload = asyncio.run(run(args))
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=args.report.parent,
                prefix=".tts-safety-report-",
                delete=False,
            ) as temporary:
                path = Path(temporary.name)
                temporary.write(rendered)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                path.replace(args.report)
            finally:
                path.unlink(missing_ok=True)
        print(rendered, end="")
        return 1 if payload["errors"] else 0
    except (ValueError, RuntimeError, OSError, TimeoutError):
        print(
            "Safety TTS lab failed; verify the pinned runtime/artifacts and limits.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
