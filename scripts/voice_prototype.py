"""Section 20: offline integration by default; explicit local microphone mode."""

import argparse
import asyncio
import importlib.util
import json
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.claude_llm_settings import ClaudeLLMSettings  # noqa: E402
from roma.core.cpu_worker import CpuWorker  # noqa: E402
from roma.core.field_encryption import configure_field_encryption  # noqa: E402
from roma.core.logging import configure_logging  # noqa: E402
from roma.core.migration_settings import MigrationSettings, current_schema_head  # noqa: E402
from roma.core.privacy_settings import PrivacySettings  # noqa: E402
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
from roma.repositories.redis.call_lease import RedisCallLease  # noqa: E402
from roma.repositories.redis.conversation_state import RedisCallStateStore  # noqa: E402
from roma.services.booking_service import BookingService  # noqa: E402
from roma.services.booking_session import BookingSession  # noqa: E402
from roma.services.live_call_state import (  # noqa: E402
    CheckpointMilestone,
    CheckpointSafetyEventStore,
    LiveCallStateStore,
)
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


def lab_call_sid(value):
    if not re.fullmatch(r"voice-lab-[A-Za-z0-9_-]{1,90}", value):
        raise argparse.ArgumentTypeError("Expected an isolated voice-lab call identity")
    return value


class LabDeadline:
    """The original five minutes include suspension; timing metrics stay monotonic."""
    def __init__(self, started_at, *, wall_clock=None, clock=perf_counter):
        self.started_at = started_at
        self.wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self.clock = clock
        elapsed = max(0.0, (self.wall_clock() - started_at).total_seconds())
        self.started_tick = clock() - elapsed

    def remaining(self):
        elapsed = max(
            (self.wall_clock() - self.started_at).total_seconds(),
            self.clock() - self.started_tick,
        )
        return max(0.0, 300 - elapsed)

    def require_remaining(self):
        remaining = self.remaining()
        if remaining <= 0:
            raise VoiceFailure(VoiceErrorCode.CAPTURE)
        return remaining


def capture_started(sid, attempt):
    print(json.dumps({"event": "CAPTURE_STARTED", "call_sid": sid, "attempt": attempt}), flush=True)


async def capture_turn(device, speech, voice, policy, deadline):
    """Never turn a stale capture into a new controller or paid-provider request."""
    def opened():
        deadline.require_remaining()
        capture_started(voice.state.call_sid, voice.attempt + 1)

    guard = getattr(voice, "epochs", None)
    epoch = guard.advance() if guard is not None else None
    options = {"epoch": epoch} if guard is not None else {}
    try:
        with voice_boundary(VoiceErrorCode.CAPTURE):
            remaining = deadline.require_remaining()
            transcript, timings = await asyncio.wait_for(
                device.listen(
                    speech,
                    max_seconds=min(policy.max_utterance_ms / 1000, remaining),
                    on_capture_started=opened,
                    **options,
                ),
                timeout=remaining,
            )
            # Re-read wall time after suspension, before business effects or AI.
            remaining = deadline.require_remaining()
            if guard is not None:
                guard.require_current(epoch)
            if transcript is None:
                from roma.services.speech_input import SpeechTranscript
                transcript = SpeechTranscript("", None, 0, "listen_limit", 0,
                                              "listen-timeout", turn_epoch=epoch)
            return await asyncio.wait_for(voice.respond(transcript, timings), timeout=remaining)
    except BaseException:
        if guard is not None and guard.is_current(epoch):
            guard.advance()
        clear = getattr(device, "clear", None)
        if clear is not None:
            await clear()
        raise



async def duplex_turns(device, speech, voice, policy, deadline, *, max_turns, on_result=None, on_started=None, on_recovery=None):
    from roma.services.duplex_voice import DuplexVoiceSession

    def opened():
        deadline.require_remaining()
        capture_started(voice.state.call_sid, voice.attempt + 1)
        if on_started is not None:
            on_started()

    session = DuplexVoiceSession(voice, speech, policy=policy, max_turns=max_turns,
                                remaining=deadline.remaining, on_result=on_result)
    recoveries = 0
    async def recover():
        nonlocal recoveries
        deadline.require_remaining()
        await session.recover_capture()
        recoveries += 1
        if on_recovery is not None:
            on_recovery(recoveries)
        print(json.dumps({"event": "CAPTURE_RETRY", "recovery_count": recoveries}), flush=True)
        print("Audio discontinuity: incomplete speech discarded. Please repeat your full request.", flush=True)

    source = device.frames(max_seconds=deadline.require_remaining(), on_capture_started=opened,
                           on_discontinuity=recover)
    with voice_boundary(VoiceErrorCode.CAPTURE):
        return await session.run(source)

async def resume_lab_call(factory, live_state, sid, *, now=None, data_root=None):
    """Claim an unfinished lab and restore SQL truth without extending its lifetime."""
    from roma.core.privacy_paths import safe_runtime_path
    from roma.repositories.postgres.models import Call, CallEvent, PrivacyTombstone
    from roma.repositories.postgres.privacy import SQLPrivacyRepository
    from sqlalchemy import func, select

    lab_call_sid(sid)
    if data_root is not None and safe_runtime_path(
        data_root, f"privacy/tombstones/{sid}.json"
    ).exists():
        raise VoiceFailure(VoiceErrorCode.DB)
    # Deny a restored pre-deletion checkpoint before it can repopulate Redis.
    async with asyncio.timeout(3):
        if await SQLPrivacyRepository(factory).is_deleted(sid):
            raise VoiceFailure(VoiceErrorCode.DB)
    # load() claims distributed ownership before reading the durable checkpoint.
    state = await live_state.load(sid)
    now = now or datetime.now(UTC)
    async with asyncio.timeout(3):
        async with factory() as session:
            if await session.scalar(select(PrivacyTombstone.id).where(
                PrivacyTombstone.provider_call_id == sid
            )):
                raise VoiceFailure(VoiceErrorCode.DB)
            call = await session.scalar(select(Call).where(Call.provider_call_id == sid))
            if (call is None or state is None or state.call_sid != sid
                    or call.status != "in_progress" or call.deleted_at is not None
                    or call.ended_at is not None or call.retention_until is None
                    or call.retention_until <= now or call.started_at is None
                    or not 0 <= (now - call.started_at).total_seconds() < 300):
                raise VoiceFailure(VoiceErrorCode.DB)
            attempts = await session.scalar(select(
                func.coalesce(func.max(CallEvent.payload["attempt"].as_integer()), 0)
            ).where(CallEvent.call_id == call.id, CallEvent.event_type == "voice_lab.turn"))
            started_at = call.started_at
    return state, started_at, attempts


async def pause_lab_call(factory, live_state, state, attempt):
    """An explicit operator pause is unfinished, unlike error/disconnect/completion."""
    from roma.repositories.postgres.models import Call, CallEvent
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    await live_state.checkpoint(state, CheckpointMilestone.STATE_CHANGED)
    async with asyncio.timeout(3):
        async with factory() as session, session.begin():
            call_id = select(Call.id).where(
                Call.provider_call_id == state.call_sid, Call.status == "in_progress",
                Call.deleted_at.is_(None), Call.ended_at.is_(None),
            ).scalar_subquery()
            await session.execute(insert(CallEvent).values(
                id=uuid4(), call_id=call_id, event_type="voice_lab.paused",
                conversation_stage=state.stage.value,
                payload={"status": "paused", "attempt": attempt, "source": "microphone_lab"},
                occurred_at=datetime.now(UTC),
            ))


async def ready_local_models(stt, tts, scorer, *, observer=None):
    # Synthetic readiness only: no participant audio, microphone, paid LLM or DB.
    for language in ("gu", "hi", "en"):
        with voice_boundary(VoiceErrorCode.ASR):
            await stt.transcribe(STTRequest(b"\0\0" * 16000, AudioFormat(16000), language))
    safe = await SafetyService().check("नमस्ते।", SafetyContext("voice-lab-preflight", 1, "open"))
    tts_started = perf_counter()
    with voice_boundary(VoiceErrorCode.TTS):
        request = TTSRequest(safe.text, language="hi")
        warmup = getattr(tts, "warmup", None)
        if warmup is not None:
            await warmup(request)
        else:
            await tts.synthesize(request)
    if observer is not None:
        observer.on_event("tts_warmup", {"tts_readiness_ms": (perf_counter() - tts_started) * 1000})
    scorer.probability(b"\0\0" * 512)
    scorer.reset()


def optional_knowledge(factory, llm, safety, cleanup):
    """Opt in to governed evidence while sharing the call's existing Claude budget."""
    from roma.core.rag_settings import RAGSettings
    if not RAGSettings().rag_enabled:
        return None
    from roma.core.rag_settings import KnowledgeRuntimeSettings
    from roma.services.knowledge_runtime import (
        bind_knowledge_answers,
        build_knowledge_components,
    )
    settings = KnowledgeRuntimeSettings()
    components = build_knowledge_components(factory, settings)
    close = getattr(components.provider, "aclose", None)
    if close is not None:
        cleanup.push_async_callback(close)
    return bind_knowledge_answers(factory, components, llm, safety=safety,
                                  timeout_secs=settings.rag_generation_timeout_secs)


async def local_run(args):
    with voice_boundary(VoiceErrorCode.DB):
        return await _local_run(args)


async def _local_run(args):
    cfg, llm_cfg, tts_cfg = local_preflight(args)
    acceptance = getattr(args, "acceptance_observer", None)
    if acceptance is not None:
        acceptance.require_target(MigrationSettings().require_url())
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
    if acceptance is not None:
        acceptance.prepare_state(state)
    failed, paused = True, False
    started_at, previous_attempts = None, 0
    cleanup = AsyncExitStack()
    cleanup.push_async_callback(engine.dispose)
    cleanup.push_async_callback(redis.aclose)
    try:
        async with engine.connect() as connection:
            if (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                != current_schema_head()
            ):
                raise ValueError("Schema head prerequisite is missing; no migration attempted")
        if not args.resume_call:
            await require_booking_data(factory, branch=state.branch)
        await redis.ping()
        live_state = LiveCallStateStore(
            RedisCallStateStore(redis_url=RedisSettings().redis_url.get_secret_value()),
            PostgresConversationStateStore(factory),
            lease_factory=lambda sid: RedisCallLease(redis, sid),
        )
        cleanup.push_async_callback(live_state.aclose)
        privacy_cfg = PrivacySettings()
        configure_field_encryption(privacy_cfg)
        store = PostgresVoiceLabStore(factory, persist_transcripts=args.persist_transcripts)
        if args.resume_call:
            state, started_at, previous_attempts = await resume_lab_call(
                factory, live_state, args.resume_call, data_root=privacy_cfg.roma_data_dir
            )
        booking = BookingSession(
            BookingService(PostgresBookingStore(factory), RedisAppointmentHolds(redis)), state
        )
        cleanup.push_async_callback(booking.aclose)
        if args.resume_call:
            await booking.restore()
            if state.booking_error == "BOOKING_UNAVAILABLE":
                raise VoiceFailure(VoiceErrorCode.DB)
            if state.appointment_id:
                # Reconcile a commit whose confirmation/playback was interrupted.
                # SQL truth is reported without capturing consent or committing again.
                await live_state.checkpoint(state, CheckpointMilestone.CALL_ENDED)
                await store.finish(state.call_sid)
                print(json.dumps({"event": "BOOKING_RESTORED", "call_sid": state.call_sid,
                                  "status": "completed", "appointment_id": state.appointment_id}), flush=True)
                return
            await require_booking_data(factory, branch=state.branch)
        echo = None
        if input_duplex(args):
            from roma.providers.audio.echo import WebRTCEchoCanceller
            with voice_boundary(VoiceErrorCode.DEVICE):
                # Reference timestamps already include DAC latency; let AEC estimate residual delay.
                echo = WebRTCEchoCanceller(stream_delay_ms=0)
                echo.preflight()
        with voice_boundary(VoiceErrorCode.ASR):
            stt = build_stt_provider(cfg)
        with voice_boundary(VoiceErrorCode.TTS):
            tts = build_tts_provider(tts_cfg)
        if getattr(tts, "aclose", None) is not None:
            cleanup.push_async_callback(tts.aclose)
        stt = LocalLanguageSTT(stt)
        # Read/verify provisioned TTS artifacts before opening the microphone.
        with voice_boundary(VoiceErrorCode.TTS):
            await tts.cache_identity(TTSRequest("नमस्ते", language="hi"))
        scorer = SileroFrameProbability()
        if acceptance is None:
            await ready_local_models(stt, tts, scorer)
        else:
            await ready_local_models(stt, tts, scorer, observer=acceptance)
        if started_at is None:
            started_at = datetime.now(UTC)
            await store.start(state.call_sid)

        async def finish_call():
            if not paused:
                await store.finish(state.call_sid, failed=failed)

        cleanup.push_async_callback(finish_call)

        async def finish_state():
            if not paused:
                await live_state.checkpoint(state, CheckpointMilestone.CALL_ENDED)

        cleanup.push_async_callback(finish_state)
        if not args.resume_call:
            await live_state.checkpoint(state, CheckpointMilestone.CALL_STARTED)
        safety = SafetyService(CheckpointSafetyEventStore(
            PostgresSafetyEventStore(factory, source="voice"), live_state, state
        ))
        with voice_boundary(VoiceErrorCode.LLM):
            llm = (
                MockLLMProvider(response="नमस्ते।")
                if args.llm_provider == "mock"
                else build_llm_provider(llm_cfg)
            )
        if getattr(llm, "aclose", None) is not None:
            cleanup.push_async_callback(llm.aclose)
        knowledge = optional_knowledge(factory, llm, safety, cleanup)
        conversation = TextConversationService(
            TimedLLMProvider(llm),
            knowledge=knowledge,
            safety=safety,
            store=live_state,
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
        device_type = acceptance.device_type(LocalAudioDevice) if acceptance else LocalAudioDevice
        device = device_type(
            input_device=args.input_device, output_device=args.output_device, policy=cfg.turn_policy(),
            duplex=input_duplex(args), echo=echo,
        )
        speech = SpeechInputService(stt, scorer, state, policy=cfg.turn_policy())
        cpu_worker = None
        if sentence_streaming(args):
            cpu_worker = CpuWorker()
            cleanup.push_async_callback(cpu_worker.aclose)
            await cpu_worker.run(int, 0)
        voice_type = acceptance.voice_type(VoicePrototype) if acceptance else VoicePrototype
        voice = voice_type(state, conversation, synthesis, device, store, budget=budget,
                               streaming=StreamConfig(minimum_speech_ms=200 if input_duplex(args) else 600)
                               if sentence_streaming(args) else None, cpu_worker=cpu_worker)
        voice.release_on_disconnect = booking.aclose
        voice.attempt = previous_attempts
        # The persisted SQL start includes downtime and resumed model readiness.
        # Neither controller nor provider deadlines receive a fresh five minutes.
        deadline = LabDeadline(started_at)
        device.check_active = deadline.require_remaining
        device.bind_epoch_guard(voice.epochs)
        speech.epochs = voice.epochs
        cleanup.push_async_callback(device.aclose)
        await device.start()
        voice._started = deadline.started_tick
        if sentence_streaming(args):
            await voice.prime_filler()
        print(
            ("Full duplex with local WebRTC echo cancellation. " if input_duplex(args)
             else "Diagnostic half duplex. ") + "Local ASR/TTS; "
            + (
                "mock LLM, no cloud inference."
                if args.llm_provider == "mock"
                else "only Claude inference is remote."
            )
            + " Ctrl+C stops. No audio recording."
        )
        print("Voice lab id: " + state.call_sid, flush=True)
        if input_duplex(args):
            def report_turn(outcome):
                if acceptance is not None:
                    acceptance.on_result(outcome, state)
                print(json.dumps({"call_sid": state.call_sid, **asdict(outcome)},
                                 ensure_ascii=False), flush=True)
            turns = min(args.max_turns, args.pause_after_turn or args.max_turns)
            outcomes = await duplex_turns(device, speech, voice, cfg.turn_policy(), deadline,
                                          max_turns=turns, on_result=report_turn,
                                          on_started=(lambda: acceptance.on_event("capture_started", {}))
                                          if acceptance else None,
                                          on_recovery=(lambda count: acceptance.on_event(
                                              "capture_recovered", {"recovery_count": count}))
                                          if acceptance else None)
            if (args.pause_after_turn is not None and len(outcomes) >= args.pause_after_turn
                    and not state.lead_wants_out and deadline.remaining() > 0):
                await pause_lab_call(factory, live_state, state, voice.attempt)
                paused = True
                print(json.dumps({"event": "CALL_PAUSED", "call_sid": state.call_sid,
                                  "attempt": voice.attempt}), flush=True)
            failed = False
            return
        for invocation_attempt in range(1, args.max_turns + 1):
            if deadline.remaining() <= 0 or state.lead_wants_out:
                break
            outcome = await capture_turn(device, speech, voice, cfg.turn_policy(), deadline)
            print(
                json.dumps({"call_sid": state.call_sid, **asdict(outcome)}, ensure_ascii=False),
                flush=True,
            )
            if (args.pause_after_turn == invocation_attempt and not state.lead_wants_out
                    and deadline.remaining() > 0):
                await pause_lab_call(factory, live_state, state, voice.attempt)
                paused = True
                print(json.dumps({"event": "CALL_PAUSED", "call_sid": state.call_sid,
                                  "attempt": voice.attempt}), flush=True)
                break
        failed = False
    finally:
        await cleanup.aclose()


def input_duplex(args):
    """The local calling path listens during playback unless explicitly diagnosed muted."""
    return args.mode == "local" and not args.half_duplex


def sentence_streaming(args):
    """Sentence delivery is independent of continuous full-duplex input."""
    return bool(args.streaming or (args.mode == "local" and not args.buffered_speech))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    speech_mode = p.add_mutually_exclusive_group()
    speech_mode.add_argument("--streaming", action="store_true", help="Audited sentence streaming; default in local mode")
    speech_mode.add_argument("--buffered-speech", action="store_true", help="Wait for the complete reply; input remains full duplex")
    p.add_argument("--mode", choices=("mock", "local"), default="mock")
    p.add_argument("--half-duplex", action="store_true", help="Diagnostic: mute capture while replying instead of full duplex")
    p.add_argument("--llm-provider", choices=("claude", "mock"), default="claude")
    p.add_argument("--allow-microphone", action="store_true")
    p.add_argument("--allow-paid-api", action="store_true")
    p.add_argument("--allow-database-write", action="store_true")
    p.add_argument("--persist-transcripts", action="store_true")
    p.add_argument("--resume-call", type=lab_call_sid, help="Resume one unfinished lab within its original five-minute limit")
    p.add_argument("--pause-after-turn", type=int, choices=range(1, 61), metavar="1..60", help="Leave the call resumable after N attempts in this invocation")
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
