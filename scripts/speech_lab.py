"""Speech-input lab. Default synthetic; real ASR needs an explicit provider and WAV."""

import argparse
import asyncio
import json
import sys
import wave
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.speech_settings import SpeechSettings  # noqa: E402
from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector  # noqa: E402
from roma.domain.conversation.language import Language, route_language  # noqa: E402
from roma.domain.conversation.state import CallState  # noqa: E402
from roma.eval.speech import report  # noqa: E402
from roma.providers.ai.local_routing import LocalLanguageSTT  # noqa: E402
from roma.providers.ai.registry import build_stt_provider  # noqa: E402
from roma.providers.ai.silero import SileroFrameProbability  # noqa: E402
from roma.services.speech_input import SpeechInputService  # noqa: E402


async def run(args) -> dict:
    if args.provider == "mock":
        if args.wav or args.allow_network:
            raise ValueError("Synthetic mode uses no file, endpoint or credentials")
        return await report()
    if args.wav is None:
        raise ValueError("Select a caller-provided --wav for real ASR")
    if (
        args.provider in {"hf_indic_conformer", "hf_public_lab", "hf_whisper"}
        and not args.allow_network
    ):
        raise ValueError(
            "Hosted inference requires explicit --allow-network; charges may apply"
        )
    if args.provider == "hf_public_lab" and not args.public_benchmark:
        raise ValueError("Public Spaces accept only explicitly declared public benchmark audio")
    cfg = SpeechSettings(stt_provider=args.provider)
    state = CallState(call_sid="speech-lab")
    # Seed the first hint explicitly; subsequent accepted transcripts update the route.
    if cfg.stt_language == "gu":
        state.language_router.current = Language.GUJARATI
    elif cfg.stt_language == "en":
        state.language_router.current = Language.ENGLISH
    provider = build_stt_provider(cfg)
    if args.provider == "whisper_mlx":
        provider = LocalLanguageSTT(provider)
    service = SpeechInputService(
        provider,
        SileroFrameProbability(),
        state,
        stt_metadata={"data_classification": "public_benchmark"}
        if args.public_benchmark
        else None,
        endpoint=EndpointDetector(
            EndpointConfig(max_utterance_ms=int(cfg.stt_max_audio_secs * 1000))
        ),
    )
    rows = []

    def accept(results):
        for result in results:
            row = asdict(result)
            if result.text and not result.error:
                route_language(state.language_router, result.text)
            row["route"] = state.language_router.current.value
            if not args.show_transcripts:
                row.pop("text")
            rows.append(row)

    with wave.open(str(args.wav), "rb") as source:
        if (
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
            source.getcomptype(),
        ) != (1, 2, 16000, "NONE"):
            raise ValueError("WAV must contain uncompressed mono 16 kHz PCM16")
        if source.getnframes() > 16000 * 300:
            raise ValueError("Speech lab accepts at most five minutes of WAV audio")
        while pcm := source.readframes(320):
            accept(await service.feed(pcm))
        accept(await service.finish(pad_tail=True))
    return {
        "provider": args.provider,
        "segments": rows,
        "native_review_passed": False,
        "errors": sum(bool(row["error"]) for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=(
            "mock",
            "indic_conformer",
            "whisper",
            "whisper_mlx",
            "hf_indic_conformer",
            "hf_public_lab",
            "hf_whisper",
        ),
        default="mock",
    )
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument(
        "--public-benchmark",
        action="store_true",
        help="Declare the supplied WAV is public benchmark audio, suitable for public demo upload",
    )
    parser.add_argument(
        "--show-transcripts",
        action="store_true",
        help="Print caller text in this explicit local diagnostic only",
    )
    args = parser.parse_args()
    try:
        payload = asyncio.run(run(args))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError, wave.Error, EOFError, ImportError):
        # SDK/paths may contain secrets or caller identifiers: expose no raw exception.
        print(
            "Speech lab failed: check WAV format, provider prerequisites and endpoint access; see docs/runbook.md.",
            file=sys.stderr,
        )
        return 1
    return 1 if payload.get("errors", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
