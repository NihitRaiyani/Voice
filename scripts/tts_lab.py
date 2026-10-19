"""Section 19 local TTS lab. Default tones are transport tests, not speech quality evidence."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.tts_settings import TTSSettings  # noqa: E402
from roma.eval.tts import SyntheticPCMProvider, report  # noqa: E402
from roma.providers.ai.registry import build_tts_provider  # noqa: E402
from roma.services.safety_service import InMemorySafetyEventStore, SafetyService  # noqa: E402
from roma.services.tts_service import SpeechSynthesisService  # noqa: E402


async def run(args) -> dict:
    cfg = TTSSettings(tts_provider="indic_tts" if args.provider == "indic_tts" else "mock")
    provider = (
        build_tts_provider(cfg) if args.provider == "indic_tts" else SyntheticPCMProvider()
    )
    service = SpeechSynthesisService(
        provider,
        SafetyService(InMemorySafetyEventStore()),
        model_version="synthetic-pcm-v1"
        if args.provider == "synthetic_pcm"
        else "manifest-validated",
        default_voice=cfg.tts_voice,
        cache_entries=cfg.tts_cache_entries,
        cache_bytes=cfg.tts_cache_bytes,
        max_concurrency=cfg.tts_max_concurrency,
        timeout_secs=cfg.tts_timeout_secs,
        max_text_bytes=cfg.tts_max_text_bytes,
        max_audio_secs=cfg.tts_max_audio_secs,
    )
    if args.audio_dir is not None:
        if not args.audio_dir.resolve().is_relative_to((ROOT / "var" / "roma").resolve()):
            raise ValueError("Lab audio must remain under ignored var/roma")
    languages = ("hi", "gu", "en") if args.language == "all" else (args.language,)
    return await report(
        service, provider=args.provider, languages=languages, audio_dir=args.audio_dir
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=("synthetic_pcm", "indic_tts"), default="synthetic_pcm"
    )
    parser.add_argument("--language", choices=("all", "hi", "gu", "en"), default="all")
    parser.add_argument("--audio-dir", type=Path)
    parser.add_argument(
        "--report", type=Path, help="Atomically save the complete metrics-only report"
    )
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
                prefix=".tts-report-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(rendered)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                temporary_path.replace(args.report)
            finally:
                temporary_path.unlink(missing_ok=True)
        print(rendered, end="")
    except (ValueError, RuntimeError, OSError, TimeoutError):
        print(
            "TTS lab failed: verify the isolated runtime, pinned artifact manifest and request limits; see docs/runbook.md.",
            file=sys.stderr,
        )
        return 1
    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
