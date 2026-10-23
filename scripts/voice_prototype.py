"""Section 20: offline integration by default; explicit local microphone mode."""

import argparse
import asyncio
import importlib.util
import json
import sys
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.claude_llm_settings import ClaudeLLMSettings  # noqa: E402
from roma.core.cpu_worker import CpuWorker  # noqa: E402
from roma.core.logging import configure_logging  # noqa: E402
from roma.core.migration_settings import MigrationSettings  # noqa: E402
from roma.core.speech_settings import SpeechSettings  # noqa: E402
from roma.core.tokenizer import require_nltk_data  # noqa: E402
from roma.core.tts_settings import TTSSettings  # noqa: E402
from roma.core.voice_errors import (  # noqa: E402
    VoiceErrorCode,
    VoiceFailure,
    map_voice_error,
    voice_boundary,
)
from roma.domain.conversation.language import Language  # noqa: E402
from roma.domain.conversation.state import CallState  # noqa: E402
from roma.domain.safety.audit import SafetyContext  # noqa: E402
from roma.eval.speech import ScriptedProbability, ScriptedSTT  # noqa: E402
from roma.eval.tts import SyntheticPCMProvider  # noqa: E402
from roma.providers.ai.contracts import AudioFormat, STTRequest, TTSRequest  # noqa: E402
from roma.providers.ai.local_routing import ROUTER_VERSION, LocalLanguageSTT  # noqa: E402
from roma.providers.ai.mocks import MockLLMProvider  # noqa: E402
from roma.providers.ai.registry import (  # noqa: E402
    build_llm_provider,
    build_stt_provider,
    build_tts_provider,
)
from roma.providers.ai.silero import SileroFrameProbability  # noqa: E402
from roma.providers.audio.local_device import LocalAudioDevice  # noqa: E402
from roma.repositories.postgres.booking import PostgresBookingStore  # noqa: E402
from roma.repositories.postgres.conversation_state import (  # noqa: E402
    PostgresConversationStateStore,
)
from roma.repositories.postgres.safety import PostgresSafetyEventStore  # noqa: E402
from roma.repositories.postgres.voice_lab import (  # noqa: E402
    PostgresVoiceLabStore,
    require_booking_data,
)
from roma.repositories.redis.appointment_holds import RedisAppointmentHolds  # noqa: E402
from roma.services.booking_service import BookingService  # noqa: E402
from roma.services.booking_session import BookingSession  # noqa: E402
from roma.services.safety_service import SafetyService  # noqa: E402
from roma.services.speech_input import SpeechInputService  # noqa: E402
from roma.services.streaming_text import StreamConfig  # noqa: E402
from roma.services.streaming_voice import StreamingInput  # noqa: E402
from roma.services.text_conversation_service import TextConversationService  # noqa: E402
from roma.services.tts_service import SpeechSynthesisService  # noqa: E402
from roma.services.voice_prototype import (  # noqa: E402
    InMemoryVoiceTurnStore,
    TimedLLMProvider,
    VoicePrototype,
)


class SyntheticSpeaker:
    def __init__(self):
        self.played = 0

    async def play(self, result):
        if not result.audio:
            raise ValueError("Missing synthetic PCM")
        self.played += 1


async def mock_lab(*, streaming=False, cpu_worker=None):
    cases = []
    for language, text in (
        ("gu", "અપોઈન્ટમેન્ટ ફી 50000"),
        ("hi", "अपॉइंटमेंट फीस 50000"),
        ("en", "fees 50000"),
        ("code_mix", "fees kitni hai 50000"),
    ):
        state = CallState(call_sid="voice-lab-" + uuid4().hex)
        state.language_router.current = {
            "gu": Language.GUJARATI, "hi": Language.HINDI,
            "en": Language.ENGLISH, "code_mix": Language.HINDI_ENGLISH,
        }[language]
        speaker, store = SyntheticSpeaker(), InMemoryVoiceTurnStore()
        safety = SafetyService()
        text_service = TextConversationService(
            TimedLLMProvider(MockLLMProvider(response="नमस्ते।")),
            safety=safety,
            dialogue_language="voice_hi",
        )
        synth = SpeechSynthesisService(
            SyntheticPCMProvider(), safety, model_version="synthetic-pcm-v1"
        )
        voice = VoicePrototype(state, text_service, synth, speaker, store,
                               streaming=StreamConfig() if streaming else None, cpu_worker=cpu_worker)
        decoder = ScriptedSTT((text,))
        speech = SpeechInputService(
            LocalLanguageSTT(decoder), ScriptedProbability(), state
        )
        if streaming:
            async def audio_source():
                for speaking in [1] * 4 + [0] * 20:
                    yield bytes([speaking, 0]) * 512
                    await asyncio.sleep(0)
            outcomes = await StreamingInput(voice).run(audio_source(), speech)
            if len(outcomes) != 1:
                raise ValueError("Synthetic endpoint did not finalize")
            outcome = outcomes[0]
        else:
            results = []
            for speaking in [1] * 4 + [0] * 20:
                results.extend(await speech.feed(bytes([speaking, 0]) * 512))
            if len(results) != 1:
                raise ValueError("Synthetic endpoint did not finalize")
            outcome = await voice.respond(results[0])
        cases.append(
            {
                "input_language": language,
                "decoder_calls": decoder.calls,
                **asdict(outcome),
                "speaker_outputs": speaker.played,
                "durable_database": False,
                "saved_phases": len(store.records),
                **({"queue_metrics": voice.queue_metrics, "streaming": True} if streaming else {}),
                "restored_state": CallState.from_dict(state.to_dict()).to_dict()
                == state.to_dict(),
            }
        )
    return {
        "schema": "section20-voice-prototype-v1",
        "local_router_version": ROUTER_VERSION,
        "synthetic_only": True,
        "microphone_used": False,
        "network_calls": False,
        "model_downloads": False,
        "gate_passed": False,
        "cases": cases,
    }


def local_preflight(args):
    require_nltk_data()
    if not (args.allow_microphone and args.allow_database_write):
        raise VoiceFailure(VoiceErrorCode.CAPTURE)
    if args.llm_provider == "claude" and not args.allow_paid_api:
        raise VoiceFailure(VoiceErrorCode.LLM)
    with voice_boundary(VoiceErrorCode.ASR):
        cfg = SpeechSettings()
    if cfg.stt_provider not in {"whisper_mlx", "whisper"}:
        raise VoiceFailure(VoiceErrorCode.ASR)
    packages = ["mlx_whisper" if cfg.stt_provider == "whisper_mlx" else "faster_whisper", "sounddevice"]
    if any(importlib.util.find_spec(package) is None for package in packages):
        raise VoiceFailure(VoiceErrorCode.ASR)
    if cfg.stt_provider == "whisper_mlx":
        from roma.providers.ai.whisper_mlx import verify_mlx_artifacts
        with voice_boundary(VoiceErrorCode.ASR):
            verify_mlx_artifacts(Path(cfg.whisper_mlx_model_dir))
    if args.llm_provider == "mock":
        with voice_boundary(VoiceErrorCode.TTS):
            return cfg, None, TTSSettings(tts_provider="indic_tts")
    with voice_boundary(VoiceErrorCode.LLM):
        llm_cfg = ClaudeLLMSettings(llm_provider="claude")
    if not llm_cfg.anthropic_api_key.get_secret_value() or not llm_cfg.anthropic_model:
        raise VoiceFailure(VoiceErrorCode.LLM)
    with voice_boundary(VoiceErrorCode.TTS):
        return cfg, llm_cfg, TTSSettings(tts_provider="indic_tts")


def initial_lab_state(language, branch):
    state = CallState(call_sid="voice-lab-" + uuid4().hex, branch=branch, booking_required=True)
    state.language_router.current = {
        "gu": Language.GUJARATI,
        "hi": Language.HINDI_ENGLISH,
        "en": Language.ENGLISH,
    }[language]
    return state


async def ready_local_models(stt, tts, scorer):
    # Synthetic readiness only: no participant audio, microphone, paid LLM or DB.
    for language in ("gu", "hi", "en"):
        with voice_boundary(VoiceErrorCode.ASR):
            await stt.transcribe(STTRequest(b"\0\0" * 16000, AudioFormat(16000), language))
    safe = await SafetyService().check("नमस्ते।", SafetyContext("voice-lab-preflight", 1, "open"))
    with voice_boundary(VoiceErrorCode.TTS):
        await tts.synthesize(TTSRequest(safe.text, language="hi"))
    scorer.probability(b"\0\0" * 512)
    scorer.reset()


async def local_run(args):
    with voice_boundary(VoiceErrorCode.DB):
        return await _local_run(args)


async def _local_run(args):
    cfg, llm_cfg, tts_cfg = local_preflight(args)
    from roma.domain.audio.turn_budget import TurnBudget
    budget = TurnBudget(cfg.voice_turn_budget_ms)
    from pydantic import SecretStr
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from redis.asyncio import Redis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    class RedisSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env", extra="ignore", hide_input_in_errors=True
        )
        redis_url: SecretStr

    engine = create_async_engine(
        MigrationSettings().require_url(),
        echo=False,
        hide_parameters=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=3,
        connect_args={"timeout": 3, "command_timeout": 3},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(
        RedisSettings().redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1
    )
    llm, booking, store = None, None, None
    state = initial_lab_state(cfg.stt_language, args.branch)
    failed = True
    cleanup = AsyncExitStack()
    cleanup.push_async_callback(engine.dispose)
    cleanup.push_async_callback(redis.aclose)
    try:
        async with engine.connect() as connection:
            if (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                != "20261004_0003"
            ):
                raise ValueError("Schema head prerequisite is missing; no migration attempted")
        await require_booking_data(factory, branch=state.branch)
        await redis.ping()
        with voice_boundary(VoiceErrorCode.ASR):
            stt = build_stt_provider(cfg)
        with voice_boundary(VoiceErrorCode.TTS):
            tts = build_tts_provider(tts_cfg)
        stt = LocalLanguageSTT(stt)
        # Read/verify provisioned TTS artifacts before opening the microphone.
        with voice_boundary(VoiceErrorCode.TTS):
            await tts.cache_identity(TTSRequest("नमस्ते", language="hi"))
        scorer = SileroFrameProbability()
        await ready_local_models(stt, tts, scorer)
        store = PostgresVoiceLabStore(factory, persist_transcripts=args.persist_transcripts)
        await store.start(state.call_sid)

        async def finish_call():
            await store.finish(state.call_sid, failed=failed)

        cleanup.push_async_callback(finish_call)
        safety = SafetyService(PostgresSafetyEventStore(factory, source="voice"))
        booking = BookingSession(
            BookingService(PostgresBookingStore(factory), RedisAppointmentHolds(redis)), state
        )
        cleanup.push_async_callback(booking.aclose)
        with voice_boundary(VoiceErrorCode.LLM):
            llm = (
                MockLLMProvider(response="नमस्ते।")
                if args.llm_provider == "mock"
                else build_llm_provider(llm_cfg)
            )
        if getattr(llm, "aclose", None) is not None:
            cleanup.push_async_callback(llm.aclose)
        conversation = TextConversationService(
            TimedLLMProvider(llm),
            safety=safety,
            store=PostgresConversationStateStore(factory),
            booking_session=booking,
            max_tokens=llm_cfg.anthropic_max_output_tokens if llm_cfg is not None else 128,
            temperature=0.7,
            dialogue_language="voice_hi",
        )
        synthesis = SpeechSynthesisService(
            tts,
            safety,
            model_version="manifest-validated",
            timeout_secs=tts_cfg.tts_timeout_secs,
            max_audio_secs=tts_cfg.tts_max_audio_secs,
            default_voice=tts_cfg.tts_voice,
            cache_entries=tts_cfg.tts_cache_entries,
            cache_bytes=tts_cfg.tts_cache_bytes,
            max_text_bytes=tts_cfg.tts_max_text_bytes,
            max_concurrency=tts_cfg.tts_max_concurrency,
        )
        device = LocalAudioDevice(
            input_device=args.input_device, output_device=args.output_device, policy=cfg.turn_policy()
        )
        speech = SpeechInputService(stt, scorer, state, policy=cfg.turn_policy())
        cpu_worker = None
        if args.streaming:
            cpu_worker = CpuWorker()
            cleanup.push_async_callback(cpu_worker.aclose)
            await cpu_worker.run(int, 0)
        voice = VoicePrototype(state, conversation, synthesis, device, store, budget=budget,
                               streaming=StreamConfig() if args.streaming else None, cpu_worker=cpu_worker)
        voice.release_on_disconnect = booking.aclose
        if args.streaming:
            await voice.prime_filler()
        print(
            "Headphones required. Local ASR/TTS; "
            + (
                "mock LLM, no cloud inference."
                if args.llm_provider == "mock"
                else "only Claude inference is remote."
            )
            + " Ctrl+C stops. No audio recording."
        )
        print("Voice lab id: " + state.call_sid)
        tick = perf_counter()
        for _ in range(args.max_turns):
            if perf_counter() - tick >= 300 or state.lead_wants_out:
                break
            with voice_boundary(VoiceErrorCode.CAPTURE):
                transcript, timings = await asyncio.wait_for(
                    device.listen(speech, max_seconds=min(cfg.turn_policy().max_utterance_ms / 1000, 300 - (perf_counter() - tick))),
                    timeout=max(0.01, 300 - (perf_counter() - tick)),
                )
            if transcript is None:
                # Silent attempts are recorded without feeding silence into the controller.
                from roma.services.speech_input import SpeechTranscript

                transcript = SpeechTranscript("", None, 0, "listen_limit", 0, "listen-timeout")
            outcome = await voice.respond(transcript, timings)
            print(
                json.dumps({"call_sid": state.call_sid, **asdict(outcome)}, ensure_ascii=False)
            )
        failed = False
    finally:
        await cleanup.aclose()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--streaming", action="store_true", help="Opt-in audited sentence streaming; microphone remains half duplex")
    p.add_argument("--mode", choices=("mock", "local"), default="mock")
    p.add_argument("--llm-provider", choices=("claude", "mock"), default="claude")
    p.add_argument("--allow-microphone", action="store_true")
    p.add_argument("--allow-paid-api", action="store_true")
    p.add_argument("--allow-database-write", action="store_true")
    p.add_argument("--persist-transcripts", action="store_true")
    p.add_argument("--branch", choices=("Vadodara", "Ahmedabad"), default="Vadodara")
    p.add_argument("--input-device", type=int)
    p.add_argument("--output-device", type=int)
    p.add_argument("--max-turns", type=int, default=30, choices=range(1, 61), metavar="1..60")
    return p


def main():
    args = parser().parse_args()
    try:
        require_nltk_data()
        configure_logging()
        if args.mode == "mock":
            if args.streaming:
                async def streamed_mock():
                    worker = CpuWorker()
                    try:
                        return await mock_lab(streaming=True, cpu_worker=worker)
                    finally:
                        await worker.aclose()
                report = asyncio.run(streamed_mock())
            else:
                report = asyncio.run(mock_lab())
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            asyncio.run(local_run(args))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception as exc:  # noqa: BLE001 -- one fixed category, never raw details.
        print(map_voice_error(exc).message, file=sys.stderr)
        return 1



if __name__ == "__main__":
    raise SystemExit(main())
