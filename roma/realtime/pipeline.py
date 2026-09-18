"""Media Streams handler — the bare Pipecat transport (docs/10 Step 1, docs/02).

Twilio dials the callee (see dialer.py) and opens a Media Streams websocket to
``<PUBLIC_WSS_BASE>/ws``. This module accepts that socket, reads the initial
``connected``/``start`` events to learn the stream/call SIDs, and runs the pipeline:

    input -> VADProcessor -> InboundAudioCounter -> SarvamSTT -> TranscriptionLogger
          -> PickupGreeter -> NoiseGate -> OpeningTurnGuard
          -> user_agg -> OpenAILLM -> usage -> InterruptibleSentenceAggregator
          -> PreTTSFilter -> BulbulTTS -> output -> assistant_agg

with Silero VAD as its own pipeline stage (docs/05 Layer 1) — NOT as a transport param,
which is where it lived while being silently discarded; see `vad_stage`. Audio-IN is counted,
transcripts flow into the LLM context (user aggregator), the LLM streams a reply,
each sentence is routed through the pre-TTS filter (`safe_output`, docs/04) before
Bulbul speaks it, and the assistant aggregator records Roma's line. On connect the
canned consent line plays; Roma's opening turn (P1) is started by `PickupGreeter` when the
lead makes a sound — or after its short timeout if they say nothing — rather than at
connect, so the call opens the way a phone call does.
Every line Roma speaks passes `safe_output()` — canned lines at load, LLM lines at
the PreTTSFilter.

Step 4 — the 7-phase switch: a PhaseControllerProcessor sits between the user aggregator
and the LLM. Each user turn it runs the deterministic machine (docs/03), swaps the phase
prompt + max_tokens, and checkpoints call-state to Redis (docs/06). On connect the last
checkpoint is loaded so a dropped/reconnected call resumes at the phase reached.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    CancelFrame,
    InputAudioRawFrame,
    MetricsFrame,
    OutputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from websockets.protocol import State

from roma.api.v1.health import mount_health_route
from roma.api.v1.twilio_webhooks import mount_answer_route
from roma.core.config import get_settings
from roma.core.logging import RedactionFilter, current_call_sid
from roma.domain.calls import consent_signed_off
from roma.domain.conversation import CallState, RedisCallStateStore
from roma.domain.conversation.prompts import (
    assemble_system_prompt,
    opening_line,
    phase_max_tokens,
)
from roma.domain.conversation.state import spoken_slot
from roma.domain.costs.spend import SpendLedger, Usage
from roma.providers.calendar.google import build_calendar
from roma.providers.telephony.twilio.auth import external_url, valid_twilio_signature
from roma.realtime import canned
from roma.realtime.backchannel import (
    BACKCHANNEL_MAX_SECS,
    ENDPOINT_CONTINUATION_SECS,
    ENDPOINT_DEFAULT_SECS,
    ENDPOINT_TERMINAL_SECS,
)
from roma.realtime.closing import CallCloser
from roma.realtime.filler import FillerPicker, load_fillers, load_holding
from roma.realtime.health import CallHealth
from roma.realtime.opening import (
    NoiseGate,
    OpeningTurnGuard,
    PickupGreeter,
    opening_posture,
)
from roma.realtime.phase_controller import PhaseControllerProcessor
from roma.realtime.phrasecache import PhraseCache, saved_inr
from roma.realtime.pretts import PreTTSFilterProcessor
from roma.realtime.recorder import (
    RECORDING_CHANNELS,
    RECORDING_SAMPLE_RATE,
    attach_recorder,
    build_recorder,
)
from roma.realtime.sentences import InterruptibleSentenceAggregator
from roma.realtime.silence import SilenceWatchdog
from roma.realtime.transcript import TranscriptionLogger
from roma.realtime.tts import TTSAudioSanitizer
from roma.realtime.turnflight import TurnFlight
from roma.realtime.turntaking import (
    AdaptiveEndpointStopStrategy,
    BackchannelAwareUserTurnStartStrategy,
)
from roma.repositories.redis.leads import RedisLeadStore
from roma.repositories.redis.opener_audio import OpenerStore
from roma.repositories.redis.postcall_queue import RedisPostcallQueue
from roma.workers.postcall.job import PostcallJob, outcome_for
from roma.workers.postcall.paths import job_spool_dir, media_dir, spend_ledger_path
from roma.workers.postcall.spool import JobSpool, SpoolFallbackQueue

_log = logging.getLogger("roma.realtime")

_RATE = canned.SAMPLE_RATE

# Mirrors `Settings.vad_stop_secs` (retuned 0.75 -> 0.45 on 2026-08-04; the reasoning and
# the measured risk window live on the Settings field). Kept in step by
# `test_the_shipped_default_is_the_tuned_value` — two sources of truth for one knob is how
# a retune half-lands.
VAD_STOP_SECS = 0.45
STT_MODEL = "saaras:v3"
STT_MODE = "codemix"
STT_LANGUAGE = None

LLM_MODEL = "gpt-4o"
TTS_MODEL = "bulbul:v3"
TTS_VOICE = "ishita"
TTS_PACE = 1.05
TTS_LANGUAGE = Language.HI_IN
OPENING_PHASE = "p1_open"
# Roma is inbound: the caller rang us and we know nothing about them. No name is seeded —
# it is the first discovery slot and stays None until the caller says it (docs/03).
DEFAULT_LEAD = {
    "branch": "Vadodara",
}


def _call_status_store(app):
    """The backend API's status store, or None when there is no Redis.

    Built lazily per use rather than held on `app.state`: these two writes happen twice per
    call at most, and a connection kept open for the life of the process to serve a browser
    poll is not worth the failure mode.
    """
    settings = getattr(app.state, "settings", None)
    url = getattr(settings, "redis_url", None) if settings is not None else None
    if url is None:
        return None
    from roma.repositories.redis.call_status import CallStatusStore

    return CallStatusStore(url.get_secret_value(), ttl=settings.call_status_ttl_secs)


async def _mark_call_connected(app, lead_token: "str | None") -> None:
    """Say the callee picked up. Never raises — a UI label is not worth a call."""
    if not lead_token:
        return
    try:
        store = _call_status_store(app)
        if store is not None:
            await store.mark_connected(lead_token)
    except Exception:  # noqa: BLE001 — cosmetic state must never reach the media path
        _log.warning("could not record call-connected status")


async def _mark_call_ended(app, lead_token: "str | None", reason: str = "") -> None:
    if not lead_token:
        return
    try:
        store = _call_status_store(app)
        if store is not None:
            await store.mark_ended(lead_token, reason)
    except Exception:  # noqa: BLE001
        _log.warning("could not record call-ended status")


async def _load_prerendered_opener(app, lead_token: "str | None"):
    """The opener clip rendered during the ring, or None (`dialer.openerstore`).

    Same posture as `_load_triggered_lead` and for the same reason: this runs with the
    callee already on the line. Absence, a truncated clip, Redis down — all return None and
    route `PickupGreeter` back to synthesising the line, which is what it did before this
    existed. The fast path is additive; nothing here may cost a call.
    """
    if not lead_token:
        return None
    store = getattr(app.state, "opener_store", None)
    if store is None:
        return None
    try:
        return await store.get(lead_token)
    except Exception:  # noqa: BLE001 — never let a cache read end a ringing call
        _log.warning("opener cache unavailable; the greeting will be synthesized")
        return None


async def _load_triggered_lead(app, lead_token: "str | None"):
    """The lead record for a call WE placed, or None for an inbound call.

    Never raises and never blocks the call. Redis is not running on the dev laptop and the
    store may be absent entirely; a call that cannot find its lead record still connects and
    still works — Roma simply knows nothing about the person, which is the inbound behaviour
    the whole system was built on until yesterday. Failing the call here would trade a
    degraded conversation for a dropped one, with the callee already on the line.
    """
    if not lead_token:
        return None
    store = getattr(app.state, "lead_store", None)
    if store is None:
        _log.warning("a lead token arrived but no lead store is configured; ignoring it")
        return None
    try:
        return await store.get(lead_token)
    except Exception:  # noqa: BLE001 — see docstring: degrade, never drop a live call
        _log.warning("lead record lookup failed; continuing without lead variables")
        return None


@dataclass
class CallHandles:
    """Per-connection observability handles (docs/08: keyed by the call's stream, never a
    process-global 'last call' slot that concurrent calls would clobber)."""

    counter: "InboundAudioCounter"
    transcript: "TranscriptionLogger"
    pretts: "PreTTSFilterProcessor"
    phase_ctrl: "PhaseControllerProcessor"
    recorder: object = None
    usage: object = None
    health: object = None


def active_calls(app) -> "dict[str, CallHandles]":
    """The live per-connection handles, keyed by stream SID (docs/08 isolation)."""
    return getattr(app.state, "calls", {})


def sole_call(app) -> "CallHandles | None":
    """The single active call's handles, for single-call test/debug observability.

    Returns None if zero OR more than one call is active — there is deliberately no global
    'last call' (docs/08): with multiple concurrent calls a debug reader must name a stream,
    not silently read whichever call happened to connect last.
    """
    calls = active_calls(app)
    return next(iter(calls.values())) if len(calls) == 1 else None


async def finalize_call(
    *,
    state,
    store,
    recorder=None,
    queue=None,
    media_root=None,
    now=None,
) -> str:
    """Everything that must happen once a call ends. Returns the post-call status string.

    Extracted from the websocket handler's `finally:` block so it can actually be tested —
    the handler itself cannot be driven end-to-end (Starlette's TestClient deadlocks on it,
    see test_media_isolation.py), which is exactly why teardown logic must not live inside
    the closure.

    Every step is independently guarded. This runs in a `finally:` after a call the lead
    has already hung up on: an exception here would mask the real teardown and buy nothing,
    because there is no one left to serve.

    The order matters — `recorder.close()` comes FIRST so the job never references a
    half-flushed file, and the queue is closed before the store only because they are
    independent clients. Giving the queue its own Redis connection is what removes the
    old ordering hazard entirely: `store.aclose()` used to be able to close the connection
    a later queue push still needed, and ordering discipline inside a `finally:` rots.
    """
    ended_at = now or datetime.now(UTC)
    status = "skipped"

    if recorder is not None:
        try:
            recorder.close()
        except Exception:  # noqa: BLE001 — teardown continues regardless
            _log.warning("recording close failed")

    try:
        await store.save(state)
    except Exception:  # noqa: BLE001
        _log.warning("final call-state save failed")

    if queue is not None and recorder is not None and not recorder.is_empty():
        try:
            started = recorder.started_at
            job = PostcallJob(
                call_sid=state.call_sid,
                recording_ref=recorder.relative_ref(media_root),
                locked_slot=state.locked_slot,
                started_at=started.isoformat(),
                ended_at=ended_at.isoformat(),
                duration_secs=max(0.0, (ended_at - started).total_seconds()),
                audio_secs=recorder.audio_secs(),
                outcome=outcome_for(state),
                sample_rate=RECORDING_SAMPLE_RATE,
                num_channels=RECORDING_CHANNELS,
            )
            status = await queue.push(job) or "queued"
        except Exception:  # noqa: BLE001 — post-call bookkeeping never fails a teardown
            _log.exception("post-call enqueue failed for %s", state.call_sid)
            status = "error"

    for closeable in (queue, store):
        aclose = getattr(closeable, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass

    return status


@contextlib.asynccontextmanager
async def _registered_call(app, stream_sid: str, handles: "CallHandles"):
    """Register a call's handles for the connection's lifetime, guaranteeing removal on ANY
    exit — normal, error, or cancellation (docs/08 isolation: the registry never leaks, and a
    task that dies before `run()` returns still deregisters)."""
    app.state.calls[stream_sid] = handles
    try:
        yield
    finally:
        app.state.calls.pop(stream_sid, None)


class InboundAudioCounter(FrameProcessor):
    """Counts inbound audio frames (proof of audio-IN) and passes them through.

    Also counts VAD events, because "is there a VAD" turned out to be a question the logs
    could not answer. It sits immediately after `vad_stage`, so each VAD frame crosses it
    exactly once — the processor's upstream sibling goes to `transport.input()` and never
    reaches here — which is what makes these counts unambiguous with no direction filtering.
    """

    def __init__(self) -> None:
        super().__init__()
        self.frame_count = 0
        self.byte_count = 0
        self.vad_starts = 0
        self.vad_stops = 0

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.frame_count += 1
            self.byte_count += len(frame.audio)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.vad_starts += 1
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.vad_stops += 1
        await self.push_frame(frame, direction)


_MAX_PRELUDE_MESSAGES = 10
_PRELUDE_TIMEOUT_SECS = 10.0


@dataclass(frozen=True)
class StreamStart:
    """Identity and format announced by Twilio's Media Streams start event."""

    stream_id: str
    account_id: str | None
    call_id: str | None
    encoding: str | None
    sample_rate: int | None
    custom_parameters: dict[str, str]


async def _read_start(websocket: WebSocket) -> StreamStart:
    """Consume Twilio's leading events until `start`; return the stream identity.

    Raises ValueError on a malformed, missing or absent start so the caller can reject the
    connection instead of crashing on a KeyError.

    Each read is bounded. The message COUNT was already capped, but every individual
    `receive_text()` was unbounded, so a peer that connects and then says nothing held a
    websocket, a task and a Silero ONNX session open for as long as it liked — a resource
    leak reachable by anyone who can reach the port, with no call ever starting.
    """
    for _ in range(_MAX_PRELUDE_MESSAGES):
        try:
            raw = await asyncio.wait_for(
                websocket.receive_text(), timeout=_PRELUDE_TIMEOUT_SECS
            )
        except TimeoutError as exc:
            raise ValueError("stream prelude timed out before the start event") from exc
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(message, dict) and message.get("event") == "start":
            start = message.get("start") or {}
            stream_id = start.get("streamSid") or message.get("streamSid")
            if not stream_id:
                raise ValueError("Twilio start event missing streamSid")
            fmt = start.get("mediaFormat") or {}
            custom = start.get("customParameters") or {}
            return StreamStart(
                stream_id=str(stream_id),
                account_id=start.get("accountSid"),
                call_id=start.get("callSid"),
                encoding=fmt.get("encoding"),
                sample_rate=fmt.get("sampleRate"),
                custom_parameters={str(k): str(v) for k, v in custom.items()},
            )
    raise ValueError("no well-formed Twilio start event within prelude bound")


def build_vad(settings) -> SileroVADAnalyzer:
    """Silero VAD at the telephony rate (model-based, docs/05 Layer 1). `stop_secs`
    carries the turn-final wait (850ms default) since the semantic smart-turn
    analyzer lands with the LLM context aggregator later (D1).

    `stop_secs` comes from settings, not the module constant, because this is THE number
    docs/10 Step 7 exists to tune and docs/05 calls it "a starting value to re-tune on real
    Twilio audio". Tuning it must not require a code edit on the production origin.
    """
    return SileroVADAnalyzer(
        sample_rate=_RATE,
        params=VADParams(
            stop_secs=settings.vad_stop_secs,
            confidence=settings.vad_confidence,
            min_volume=settings.vad_min_volume,
        ),
    )


def vad_stage(vad_analyzer) -> list:
    """The VAD, as a pipeline element. `[]` when there is no analyzer.

    **If this list is empty, there is no VAD.** That is the whole contract, and it is stated
    this bluntly because the previous wiring had no such contract and nobody noticed for
    four live calls.

    What went wrong: the analyzer was passed as `FastAPIWebsocketParams(vad_analyzer=...)`.
    In pipecat 1.6.0 `vad_analyzer` is NOT a field on `TransportParams` — a pydantic
    `BaseModel` carrying pydantic's default `extra='ignore'` — so the analyzer was accepted
    by the constructor and silently dropped on the floor. `'vad_analyzer' in
    FastAPIWebsocketParams.model_fields` is `False`.

    One line, four symptoms, all previously chased as unrelated bugs:

    * no `VADUserStartedSpeakingFrame`, so `BackchannelAwareUserTurnStartStrategy`'s fast arm
      never armed — zero "barge-in: user speech exceeded" across four calls;
    * no `VADUserStoppedSpeakingFrame`, so `_last_span` stayed None and EVERY final
      transcript opened a turn — the "two agents clashing" and "lag" the lead reported;
    * `AdaptiveEndpointStopStrategy` inert (`_rearm_to_deadline` returns early when
      `_vad_stopped_time is None`), so the Step-7 endpoint knobs governed nothing;
    * Sarvam STT never flushed — it calls `flush()` only on `VADUserStoppedSpeakingFrame`
      (`sarvam/stt.py:444`) — so every reply waited on Sarvam's own server-side endpointing.

    A `VADProcessor` in the pipeline list is used rather than the alternative
    `LLMUserAggregatorParams(vad_analyzer=...)`, which would also work: the aggregator
    broadcasts VAD frames upstream as well as downstream, so Sarvam would still flush. It is
    rejected because that delivery runs BACKWARDS through Roma's own input guards, and
    because the aggregator only ever sees audio that survived `NoiseGate`, `OpeningTurnGuard`
    and Sarvam's `audio_passthrough` — three switches that would kill VAD silently if any
    were ever flipped. A processor at index 1 has only `transport.input()` upstream of it,
    and its presence is a structural fact a test can assert.
    """
    return [VADProcessor(vad_analyzer=vad_analyzer)] if vad_analyzer is not None else []


# This link drops roughly one Sarvam handshake in six. Three attempts, spaced, because the
# failures are bursty rather than permanent — a probe that failed twice in a row succeeded
# 1.15s later on the third try.
STT_CONNECT_ATTEMPTS = 7
STT_CONNECT_RETRY_SECS = 1.0
# 7 attempts with exponential backoff spans ~31s instead of the old 3-in-2s. Sized against
# the measured link: Sarvam's handshake ran p50 3.0s / max 14.7s with 2 of 5 timing out
# outright, so a 2-second window was never going to catch a recovery.
STT_CONNECT_RETRY_MAX_SECS = 8.0


class NonBlockingStartSarvamSTT(SarvamSTTService):
    """Sarvam STT that does not hold the pipeline hostage while it connects.

    ## The late greeting, finally

    Pipecat calls `start(StartFrame)` and only forwards the frame once it returns, and
    `SarvamSTTService.start` is `await super().start(frame)` then `await self._connect()`.
    So NOTHING downstream — TTS included — can emit audio until Sarvam's websocket is up.

    Measured on live call d9ff7ff5 (2026-08-02):

        09:24:29.972  media stream started
        09:24:30.495  greeter fired (0.5s — correct)
        09:24:42.531  StartFrame reached the end of the pipeline
        09:24:43.98   Roma's first audio

    Twelve seconds, none of it Roma's. It was 3.4s the call before. Every "late greeting"
    reported this session traces here, and moving the greeter earlier could not fix it
    because the greeting's own frames still queue behind this connect.

    Connecting in the background is safe for the ONE reason that matters: STT is not needed
    until the lead speaks, which is necessarily after Roma has greeted them. Audio arriving
    before the socket is up is dropped by the parent's own `_websocket is None` guards — the
    same thing that happens today, except the call is now audible while it resolves.
    """

    async def start(self, frame):
        await super(SarvamSTTService, self).start(frame)
        self.create_task(self._connect_with_retry())

    async def _connect_with_retry(self) -> None:
        """Connect in the background, retrying a failed handshake.

        The retry is only possible BECAUSE the connect is off the critical path — blocking
        the pipeline for three attempts would be worse than the failure. This link drops
        roughly one Sarvam handshake in six ("timed out during opening handshake"), and on
        calls c5672a23 and dd8a351b a single failed attempt ended the call: no STT means
        `health.deaf`, which means `CallCloser` hangs up on a lead who is still talking.

        Attempts are spaced because the failures are bursty, not permanent — a probe that
        failed twice in a row succeeded 1.15s later on the third try.
        """
        delay = STT_CONNECT_RETRY_SECS
        for attempt in range(1, STT_CONNECT_ATTEMPTS + 1):
            try:
                await self._connect()
                if attempt > 1:
                    _log.info("sarvam STT connected on attempt %d", attempt)
                return
            except Exception:  # noqa: BLE001
                if attempt == STT_CONNECT_ATTEMPTS:
                    # DO NOT RAISE. Re-raising pushes an ErrorFrame into the pipeline, and
                    # on call 335aa291 that ended the call twice over: `stt_alive` went
                    # False so `CallCloser` hung up on a lead who had said nothing wrong,
                    # and with no transcripts arriving the silence watchdog nudged the
                    # opening turn and Roma said her greeting a SECOND time, 18s after the
                    # first. Two symptoms the lead reported as separate bugs, one cause.
                    #
                    # A call with no STT is degraded, not doomed — she can still speak, and
                    # `CallHealth` already exists to decide what to do about a deaf leg.
                    # Killing the pipeline takes that decision away from it.
                    _log.exception(
                        "sarvam STT failed to connect after %d attempts; the call continues "
                        "without STT rather than being torn down",
                        attempt,
                    )
                    return
                _log.warning(
                    "sarvam STT connect attempt %d failed; retrying in %.1fs", attempt, delay
                )
                await asyncio.sleep(delay)
                # Exponential: the failures are bursty, and 3 tries 1s apart covered a 2s
                # window against a link whose Sarvam handshake was measured at p50 3.0s with
                # 40% outright timeouts. That is not a retry, it is a formality.
                delay = min(delay * 2, STT_CONNECT_RETRY_MAX_SECS)


def build_stt(settings) -> SarvamSTTService:
    """Sarvam Saaras streaming STT in code-mix mode (docs/01). The Sarvam API key is
    read via `.get_secret_value()` here — the exact secret boundary (docs/07)."""
    return NonBlockingStartSarvamSTT(
        api_key=settings.sarvam_api_key.get_secret_value(),
        mode=STT_MODE,
        settings=SarvamSTTService.Settings(model=STT_MODEL, language=STT_LANGUAGE),
    )


# How long the FIRST attempt at a completion may take before we give up on it and fire a
# second one. Normal TTFB on this path is 0.8-3.0s (measured live 2026-08-01), so 5s is well
# clear of a merely slow turn and only trips on a request that has actually stalled.
LLM_FIRST_ATTEMPT_TIMEOUT_SECS = 5.0


def build_llm(settings) -> OpenAILLMService:
    """OpenAI streaming LLM (`LLM_MODEL`, gpt-4o). `max_tokens` is the phase word cap ceiling
    (docs/02). Secret read via `.get_secret_value()` here — the boundary (docs/07).

    ## Why the retry is ON

    Live call acf8e78f (2026-08-01) was SILENT. The lead answered, said "Hello", and heard
    nothing at all before hanging up at thirty seconds:

        OpenAILLMService#0 TTFB: 63.978s
        openai._base_client  Retrying request to /chat/completions

    One request stalled and nothing capped it. The OpenAI SDK's own retry eventually fired,
    64 seconds in, which is 62 seconds after the call was lost. Note the asymmetry this fixes:
    `build_slot_client` below has carried explicit connect/read timeouts all along, so the
    THROWAWAY extraction call was protected and the one Roma actually speaks with was not.

    Pipecat ships the mitigation and defaults it off (`retry_on_timeout=False`): cap the first
    attempt, and on timeout re-issue once, uncapped. A retried turn is billed twice; a silent
    call is lost entirely. This matters beyond this laptop — the same stall on the Vadodara
    host would cost a real lead.
    """
    return OpenAILLMService(
        api_key=settings.openai_api_key.get_secret_value(),
        retry_on_timeout=True,
        retry_timeout_secs=LLM_FIRST_ATTEMPT_TIMEOUT_SECS,
        settings=OpenAILLMService.Settings(
            model=LLM_MODEL, max_tokens=phase_max_tokens(OPENING_PHASE)
        ),
    )


SLOT_CONNECT_TIMEOUT_SECS = 2.0
SLOT_READ_TIMEOUT_SECS = 8.0
SLOT_KEEPALIVE_SECS = 300.0


def build_slot_client(settings) -> AsyncOpenAI:
    """Raw AsyncOpenAI client for the controller's structured-output slot extraction
    (docs/03) — separate from the pipeline LLM service. Secret read at this boundary
    (docs/07). Construction is lazy; no network until the first extraction call.

    Built ONCE per process and shared by every call — see `build_media_app`. It used to be
    per websocket, which meant a fresh httpx pool per call and therefore a guaranteed cold
    handshake on each call's first extraction (4058ms cold vs 1932ms warm), papered over by
    a prewarm task that raced the lead's first answer.
    """
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=httpx.Timeout(
            SLOT_READ_TIMEOUT_SECS,
            connect=SLOT_CONNECT_TIMEOUT_SECS,
        ),
        max_retries=1,
        http_client=httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=SLOT_KEEPALIVE_SECS,
            ),
            timeout=httpx.Timeout(
                SLOT_READ_TIMEOUT_SECS,
                connect=SLOT_CONNECT_TIMEOUT_SECS,
            ),
        ),
    )


async def prewarm_slot_client(client) -> None:
    """Open the HTTPS connection to OpenAI before the lead's first answer needs it.

    "Lazy; no network until the first extraction" is correct and it is also the problem:
    that first extraction then pays DNS + TCP + TLS on top of the request, on the critical
    path of a live turn. Measured on this host, 2026-07-28:

        cold (first call, handshake included)   4058 ms
        warm (pooled connection)                1932 ms p50

    Two full seconds, once per call, spent on a handshake that could have happened while the
    line was still ringing. `httpx` keeps the connection pooled on the client, and the client
    lives for the whole websocket, so one throwaway request buys it for every later turn.

    Never raises and never blocks the call: a failed prewarm just means the old cold path,
    which is what happened before this existed.
    """
    try:
        await client.models.list()
    except Exception:  # noqa: BLE001 — a warm-up that breaks the call is worse than a slow turn
        _log.warning("slot client prewarm failed; the first extraction will pay the handshake")


def build_store(settings) -> RedisCallStateStore:
    """Redis call-state store (docs/06). `from_url` is lazy — no connection until a
    load/save. Secret read at this boundary (docs/07)."""
    return RedisCallStateStore(settings.redis_url.get_secret_value())


class NonBlockingStartSarvamTTS(SarvamTTSService):
    """Sarvam TTS that does not hold the pipeline hostage while it connects either.

    `NonBlockingStartSarvamSTT` took the STT connect off the startup path and the greeting
    went 12.6s -> 2.9s. It left the SECOND copy of the same bug one processor downstream:
    `SarvamTTSService.start` is `await super().start(frame)` then `await self._connect()`,
    so `StartFrame` still could not finish traversing the pipeline until Bulbul's socket was
    up — and the greeter's TextFrame queues behind `StartFrame`.

    Measured on live call 16835e83 (2026-08-02), which the lead cut for a late greeting:

        19:58:09.546  pipeline waiting for StartFrame to traverse
        19:58:09.564  STT connect begins
        19:58:11.114  STT connected                      (+1.55s, in the BACKGROUND)
        19:58:13.082  TTS websocket connected            (+1.97s, ON the startup path)
        19:58:13.116  greeting finally generated
        19:58:13.184  StartFrame reaches the end

    The greeting line itself costs nothing to produce — it is a canned template with no LLM
    call (`opening.py`). All 3.3s between the greeter firing and the first byte of audio was
    this connect.

    Safe for a reason that is in the parent, not in hope: `SarvamTTSService.run_tts` opens
    with `if not self._websocket or self._websocket.state is State.CLOSED: await
    self._connect()`. A text frame arriving before the background connect lands does exactly
    what it does today — it connects, then speaks. The task only removes the wait from the
    path where nobody is talking yet.
    """

    def __init__(self, *args, phrases=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._connect_lock = asyncio.Lock()
        # The fixed-phrase cache (telephony.phrasecache). Checked in `run_tts` BEFORE the
        # socket, so a hit costs a dict lookup instead of a Bulbul round trip while every
        # frame-lifecycle guarantee of the real path is preserved.
        self._phrases = phrases
        self.phrase_hits = 0
        self.phrase_chars = 0

    async def run_tts(self, text, context_id):
        pcm = self._phrases.lookup(text) if self._phrases else None
        if pcm is not None and self.audio_context_available(context_id):
            # Mirrors what `_receive_messages` does for socket audio — same context, same
            # stop frame — so downstream (BotStoppedSpeaking, the closer, the watchdog)
            # cannot tell a cached line from a synthesized one. A raw-audio bypass that
            # skipped this bookkeeping is the 0ce455b0 silent-call bug class.
            await self.stop_ttfb_metrics()
            from pipecat.frames.frames import TTSAudioRawFrame, TTSStoppedFrame

            await self.append_to_audio_context(
                context_id,
                TTSAudioRawFrame(pcm, self.sample_rate, 1, context_id=context_id),
            )
            await self.append_to_audio_context(
                context_id, TTSStoppedFrame(context_id=context_id)
            )
            await self.remove_audio_context(context_id)
            self.phrase_hits += 1
            self.phrase_chars += len(text)
            _log.info("phrase cache HIT (%d chars) — Bulbul round trip skipped", len(text))
            yield None
            return
        async for frame in super().run_tts(text, context_id):
            yield frame

    async def start(self, frame):
        # Skips SarvamTTSService.start, so its one piece of real setup is reproduced here:
        # `_send_config` reads `_speech_sample_rate`, and the websocket API wants a string.
        await super(SarvamTTSService, self).start(frame)
        self._speech_sample_rate = str(self.sample_rate)
        self.create_task(self._connect_in_background())

    async def _connect(self) -> None:
        """Serialised and idempotent. THIS is what makes the prewarm safe.

        `run_tts` opens with "if the socket is missing or closed, connect", and the prewarm
        connects concurrently — so on call 36598d8f both ran in the same tick:

            20:05:32.346  Connected to Sarvam TTS Websocket   (prewarm)
            20:05:32.346  Generating TTS [Hello Nihit ji...]
            20:05:32.347  Connected to Sarvam TTS Websocket   (run_tts, AGAIN)

        The second connect replaced `self._websocket`, so the greeting was sent on one
        socket while the receive task was bound to the other. No TTFB, no audio, and the
        lead heard a completely silent call. The lock closes the window; the re-check makes
        the loser of the race a no-op instead of a second socket.
        """
        async with self._connect_lock:
            if self._websocket is not None and self._websocket.state is not State.CLOSED:
                return
            await super()._connect()

    async def _connect_in_background(self) -> None:
        """Connect off the critical path. Never raises: `run_tts` reconnects on demand, so a
        failure here must not take down pipeline startup."""
        try:
            await self._connect()
        except Exception:  # noqa: BLE001
            _log.warning("sarvam TTS prewarm connect failed; run_tts will reconnect on demand")


def build_tts(settings, phrases=None) -> SarvamTTSService:
    """Sarvam Bulbul v3 streaming TTS over its persistent websocket. v3 natively renders
    code-mix (Hinglish) — v2 rendered the Hindi/English switch poorly on a live call.
    TOKEN aggregation: the pre-TTS filter already emits whole sentences, so speak each as it
    arrives. v3 forces preprocessing on (number/mixed-language normalization). Secret read
    at this boundary (docs/07)."""
    return NonBlockingStartSarvamTTS(
        phrases=phrases,
        api_key=settings.sarvam_api_key.get_secret_value(),
        sample_rate=_RATE,
        text_aggregation_mode=TextAggregationMode.TOKEN,
        settings=SarvamTTSService.Settings(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            language=TTS_LANGUAGE,
            pace=TTS_PACE,
            enable_preprocessing=True,
        ),
    )


def build_postcall_queue(settings):
    """The post-call job producer for one call (docs/09).

    A `SpoolFallbackQueue` wrapping Redis: push to `queue:postcall`, and on ANY failure
    write the job to the local spool instead. Redis is not running on the dev box, so the
    spool is the path that actually runs today — and either way `push` never raises, which
    is what keeps post-call bookkeeping out of the call's teardown.

    Its own Redis client, deliberately: the call-state store closes its client at teardown,
    and sharing one would make the enqueue depend on the order of two `aclose()` calls
    inside a `finally:` block.
    """
    return SpoolFallbackQueue(
        RedisPostcallQueue(settings.redis_url.get_secret_value()),
        JobSpool(job_spool_dir(settings)),
    )


def build_user_params(enable_barge_in: bool = False, settings=None) -> LLMUserAggregatorParams:
    """User-turn config. Two shapes, chosen by ENABLE_BARGE_IN (docs/05 Layer 3).

    `settings` supplies the Step-7 tuning knobs when barge-in is on. It is optional so the
    OFF branch needs nothing, and so the strategies keep their own docs/05 defaults when no
    settings are passed.

    **The lead can always interrupt, on both branches.** This used to be the difference
    between them, on the reasoning that with no AEC the arm would fire on Roma's own echo.
    That reasoning was wrong, and measurement is what showed it: across all twenty recorded
    calls — including every call made with barge-in ON — the lead channel's RMS while Roma
    speaks never exceeds 0.78x its RMS while Roma is silent. Her voice does not come back
    up the inbound leg at all; the carrier already suppresses it. There was never an echo
    for the VAD to fire on.

    Turning interruptions off cost a live call. On CA3e7f4c58 Roma spoke for fifty-six
    unbroken seconds while the lead talked over her twelve times, because
    `enable_interruptions=False` still lets a lead utterance START a user turn — a new
    generation fires and queues its audio — while broadcasting nothing to flush the audio
    already queued. The queue only grows, so she runs further behind real time with every
    interjection and never stops. Interruption is what drains it, which makes it a
    correctness requirement on a phone call, not a tuning knob.

    **OFF.** Stock start strategies, interruptions ON, and a plain speech-timeout stop
    strategy. (No longer the default — `config.py` ships `enable_barge_in = True`; this
    branch remains for hosts where the ON branch misbehaves.)

    That stop strategy is now pinned EXPLICITLY. Leaving `stop` unset makes
    `UserTurnStrategies.__post_init__` install `TurnAnalyzerUserTurnStopStrategy(
    LocalSmartTurnAnalyzerV3())` — an ONNX model on the audio path. It was harmless only by
    accident: with no VAD in the pipeline it never received `vad_user_speaking=True` and
    never fired, so turns ended via its transcript fallback. Putting the VAD where it belongs
    (see `vad_stage`) would have switched that model on for the first time ever, in the
    branch whose docstring promises Step 3's configuration byte-for-byte. A latent behaviour
    change that only activates when an unrelated bug is fixed is exactly the kind that gets
    blamed on the fix, so it is named here instead of inherited.

    **ON (Step 5B, needs a clean audio path).** One `BackchannelAwareUserTurnStartStrategy`
    replaces BOTH stock start strategies, and `AdaptiveEndpointStopStrategy` replaces
    smart-turn.

    `VADUserTurnStartStrategy` is not merely redundant when barge-in is on — it is
    actively destructive. `UserTurnController._trigger_user_turn_start` opens with
    `if self._user_turn: return`, so the first strategy to fire in a turn wins outright.
    VAD fires at speech onset, before any transcript exists, so leaving it in the list
    would pre-empt the backchannel guard on every single turn: the guard would still be
    constructed, still be tested, and never once decide anything.

    `TranscriptionUserTurnStartStrategy` is likewise folded in: the new strategy already
    handles `TranscriptionFrame` as its slow arm, with the lexicon check the stock one
    lacks.
    """
    start_kwargs, stop_kwargs = {}, {}
    if settings is not None:
        start_kwargs["backchannel_max_secs"] = settings.backchannel_max_secs
        stop_kwargs["user_speech_timeout"] = settings.endpoint_default_secs
        stop_kwargs["terminal_secs"] = settings.endpoint_terminal_secs
        stop_kwargs["continuation_secs"] = settings.endpoint_continuation_secs

    # Endpointing and barge-in are SEPARATE concerns (docs/05 Layers 2 and 3) and are wired
    # separately here. They used not to be: the early return below built
    # `SpeechTimeoutUserTurnStopStrategy(VAD_STOP_SECS)` and every tuned endpoint value
    # vanished with the flag, silently. "When did the user's turn end?" does not become a
    # different question because Roma may not interrupt — and the fourth time in this repo
    # that behaviour disappeared with a flag or a frame nobody thought was load-bearing is
    # three times too many.
    _log.info(
        "turn-taking: barge_in=%s endpoint_default=%.2fs terminal=%.2fs continuation=%.2fs "
        "backchannel_max=%.2fs%s",
        enable_barge_in,
        stop_kwargs.get("user_speech_timeout", ENDPOINT_DEFAULT_SECS),
        stop_kwargs.get("terminal_secs", ENDPOINT_TERMINAL_SECS),
        stop_kwargs.get("continuation_secs", ENDPOINT_CONTINUATION_SECS),
        start_kwargs.get("backchannel_max_secs", BACKCHANNEL_MAX_SECS),
        "" if enable_barge_in else " (backchannel_max inactive: it only guards Roma's turn)",
    )

    if not enable_barge_in:
        return LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[
                    VADUserTurnStartStrategy(enable_interruptions=True),
                    TranscriptionUserTurnStartStrategy(enable_interruptions=True),
                ],
                # Adaptive endpointing regardless of the flag. It is a `stop` strategy — it
                # decides when the LEAD finished, never whether Roma may be cut off. That is
                # the `start` strategies' `enable_interruptions`, which is what the flag owns.
                stop=[AdaptiveEndpointStopStrategy(**stop_kwargs)],
            )
        )

    return LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            start=[
                BackchannelAwareUserTurnStartStrategy(enable_interruptions=True, **start_kwargs)
            ],
            stop=[AdaptiveEndpointStopStrategy(**stop_kwargs)],
        )
    )


def _summarize_secs(samples: "list[float]") -> dict:
    """n/p50/max for a list of durations, or {} when nothing was measured.

    No p95 — see `_UsageLogger.ttfb_summary` for why a per-call p95 is just the maximum
    wearing a percentile's name.
    """
    if not samples:
        return {}
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "p50": round(ordered[len(ordered) // 2], 3),
        "max": round(ordered[-1], 3),
    }


class _UsageLogger(FrameProcessor):
    """Log OpenAI token usage and per-stage TTFB per turn. Passes every frame through.

    Token usage is the ₹100 testing cap (docs/02/docs/10). TTFB is the Step 7 tuning data,
    and it is read from pipecat's OWN `MetricsFrame` rather than measured here.

    That is a correction. The first version of this timed VAD-stop -> transcript inside
    `TranscriptionLogger`, and it recorded NOTHING on two live calls (`stt_finalize={}`):
    `VADUserStoppedSpeakingFrame` is consumed by the turn controller and never travels down
    the pipeline to that processor. Worse, it was redundant — pipecat already emits TTFB for
    every service, which is strictly better data. Measuring what the framework already
    measures is how you end up with two numbers that disagree.

    Why TTFB is the number that matters: on call CA9c5f7cf the OpenAI TTFB was 1.21s and
    Bulbul's 0.46s, against an endpoint wait of 0.85s. The LLM alone costs more than the
    knob docs/10 Step 7 set out to tune — so tuning 850ms down is not where the latency win
    is, and that is exactly the conclusion the measurement exists to force.
    """

    def __init__(
        self, ledger=None, call_sid: "str | None" = None, prefix_fn=None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.ttfb: dict[str, list[float]] = {}
        self._ledger = ledger
        self._call_sid = call_sid
        # Read at frame time, like the pre-TTS filter's state callables: the digest belongs
        # to the turn being billed, and the controller has already swapped it by now.
        self._prefix_fn = prefix_fn
        self.call_inr = 0.0

    def _prefix(self) -> "str | None":
        if self._prefix_fn is None:
            return None
        try:
            return self._prefix_fn()
        except Exception:  # noqa: BLE001 — a diagnostic must never cost a charge its row
            return None

    def ttfb_summary(self) -> dict:
        """n/p50/max TTFB per stage. Empty when nothing was measured — an honest nothing,
        not a zero that reads as instant.

        The tail is reported as `max`, and that is a correction. This briefly printed a
        `p95`, which on a per-call sample was not one: nearest-rank p95 returns the last
        index until about forty samples, and a call has fifteen to twenty-five turns. On
        CA00417672's n=17 the "p95" and the max were the same number by construction, so the
        field claimed a percentile while reporting the single slowest turn. `max` says what
        it is. A real p95 needs aggregation across calls and belongs in a time-series store.
        """
        return {
            name: _summarize_secs(samples) for name, samples in self.ttfb.items() if samples
        }

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for d in frame.data:
                if isinstance(d, LLMUsageMetricsData):
                    v = d.value
                    usage = Usage(
                        input_tokens=v.prompt_tokens or 0,
                        output_tokens=v.completion_tokens or 0,
                        cached_input_tokens=v.cache_read_input_tokens or 0,
                    )
                    cost = 0.0
                    if self._ledger is not None:
                        cost = self._ledger.record(
                            usage, d.model, call_sid=self._call_sid, prefix=self._prefix()
                        )
                        self.call_inr += cost
                    _log.info(
                        "llm usage: model=%s prompt=%s completion=%s total=%s cached=%s "
                        "prefix=%s cost=₹%.2f call=₹%.2f",
                        d.model,
                        v.prompt_tokens,
                        v.completion_tokens,
                        v.total_tokens,
                        v.cache_read_input_tokens,
                        self._prefix() or "-",
                        cost,
                        self.call_inr,
                    )
                elif isinstance(d, TTFBMetricsData):
                    self.ttfb.setdefault(d.processor, []).append(d.value)
        await self.push_frame(frame, direction)


def _warn_if_logging_unconfigured() -> None:
    """Say so, loudly, when `configure_logging()` has not run.

    Without it Python's last-resort handler emits WARNING and above only, so every
    `roma.realtime` INFO line disappears — including `media stream started`, which the
    runbook treats as the BINARY proof that a call's socket reached this process.

    That combination lies to you. Booting the bare factory and ringing the number produces a
    call that connects, builds its pipeline, opens Sarvam STT and TTS, spends credit and runs
    normally — while the log shows nothing at all. The obvious reading is "the socket never
    arrived", so the next hour goes into the tunnel and the carrier. Measured exactly that way
    on 2026-07-31.

    A WARNING survives the last-resort handler, which is the only reason this can be reported
    through `logging` at all.
    """
    root = logging.getLogger()
    configured = any(
        any(isinstance(f, RedactionFilter) for f in handler.filters)
        for handler in root.handlers
    )
    if not configured:
        _log.warning(
            "logging is not configured: roma INFO lines (including 'media stream started', "
            "the proof a call reached this process) will be DROPPED, and secret redaction "
            "is not installed. Call roma.core.logging.configure_logging() first — the "
            "production entrypoint roma.main:create_app does."
        )


def build_media_app(
    auto_hang_up: bool = False,
    build_stt_fn=build_stt,
    build_vad_fn=build_vad,
    build_llm_fn=build_llm,
    build_tts_fn=build_tts,
    build_store_fn=build_store,
    build_slot_client_fn=build_slot_client,
    build_queue_fn=build_postcall_queue,
    build_calendar_fn=build_calendar,
) -> FastAPI:
    """FastAPI app exposing Twilio's `/answer`, `/ws`, and `/health` routes.

    `auto_hang_up` controls whether Pipecat ends the Twilio call through the REST API when
    the pipeline finishes. Production enables it; offline tests leave it disabled.
    `build_stt_fn` builds the STT stage per connection (injected so offline tests /
    verify_media can pass a stub instead of opening a live Sarvam socket).
    `build_vad_fn` builds the input VAD per connection; it returns an analyzer or
    None. Offline callers pass `lambda _s: None` — Silero holds a background inference
    thread that blocks clean process teardown under Starlette's TestClient. Returning None
    now means **there is no VAD in the pipeline at all** (see `vad_stage`), not merely "no
    Silero thread": no endpointing, no barge-in, and no STT flush. That is fine for the
    offline tests, which drive transcripts directly, and is exactly what must never happen
    live.
    `build_llm_fn` / `build_tts_fn` build the Step-3 LLM and TTS per connection
    (injected so offline tests / verify_media pass stubs instead of opening live
    OpenAI / Sarvam sockets).
    `build_calendar_fn` picks what Roma books against — the Google diary when it is
    configured, else branch hours (`roma.providers.calendar.google.build_calendar`). Built once per
    app, not per call: it is stateless apart from a short busy-cache, and sharing that
    cache across concurrent calls is the point — two leads offered the same 11 AM is the
    thing it exists to notice.
    """
    _warn_if_logging_unconfigured()
    app = FastAPI()
    settings = get_settings()
    app.state.calls = {}
    app.state.calendar = build_calendar_fn(settings)
    app.state.slot_client = build_slot_client_fn(settings)

    app.state.consent_line = canned.consent_line() if consent_signed_off() else None
    if app.state.consent_line is None:
        _log.warning(
            "consent wording is still the compliance placeholder — no recording "
            "disclosure will be spoken, and post-call recordings stay discarded"
        )

    # Loaded once at build, not per call: it is the same bytes every time and it sits on the
    # one path where a disk read is measurable. A missing asset must NOT take the app down —
    # a call that opens in silence is bad, a server that will not boot is worse — so it
    # degrades to the generated opening turn and says so.
    try:
        app.state.opening_line = canned.opening_line()
    except (FileNotFoundError, ValueError):
        app.state.opening_line = None
        _log.error(
            "inbound opener asset missing or refused; the first turn will be GENERATED "
            "instead, which measured 3.28s of dead air at answer (CAfe5a00b). Render it "
            "with scripts/make_opening_clip.py."
        )

    if settings.enable_barge_in:
        _log.warning(
            "ENABLE_BARGE_IN is ON: using the Step 5B turn-taking strategies "
            "(backchannel-aware start, adaptive endpoint). These were live on CA4a69d27, "
            "where an unexplained screech ended the call. Interruption itself does NOT "
            "depend on this flag — the lead can always interrupt (docs/05)."
        )

    app.state.fillers = load_fillers() if settings.enable_filler else []
    # Fixed-phrase audio cache (docs/06; telephony.phrasecache), loaded once at build like
    # the fillers — same bytes every call, so the per-call cost is a dict lookup. Missing
    # assets degrade to live TTS inside the loader.
    app.state.phrase_cache = PhraseCache()
    # Held-line clips are NOT gated on `enable_filler`: that flag governs the
    # acknowledgement token, and holding the line during a stall is a different job
    # (a live silent channel reads as a dropped agent, whatever the filler policy).
    app.state.holding = load_holding()

    # Where a triggered outbound call's dynamic variables live between the dial and the
    # pickup. Redis rather than a dict, deliberately: `PendingStreams` above is already an
    # in-process map and already the reason this process cannot be replicated, and a lead
    # record has to survive far longer than a token — through ringing, a carrier retry, or a
    # redeploy mid-campaign. Absent Redis is a supported configuration (it is not running on
    # the dev laptop); inbound never reads this, and outbound degrades to knowing nothing.
    try:
        # `.get_secret_value()` — `redis_url` is a SecretStr (docs/07), and passing the
        # wrapper straight through fails deep inside redis-py as `'SecretStr' object has no
        # attribute 'decode'`. The broad except below turned that plain bug into a
        # "no Redis configured" warning, which is exactly the kind of silent degradation
        # this module keeps getting caught by.
        app.state.lead_store = RedisLeadStore(settings.redis_url.get_secret_value())
    except Exception:  # noqa: BLE001 — no redis package, or an unusable URL
        app.state.lead_store = None
        _log.exception("no lead store: outbound dynamic variables will not be available")

    # Read back by `_call_status_store` for the backend API's two status writes. Stored
    # rather than re-read via `get_settings()` so those helpers stay testable with a stub app.
    app.state.settings = settings

    try:
        # Separate client from the lead store: this one holds AUDIO and must not decode
        # responses as utf-8. Same URL, same TTL, different codec.
        app.state.opener_store = OpenerStore(settings.redis_url.get_secret_value())
    except Exception:  # noqa: BLE001 — the pre-rendered opener is additive, never required
        app.state.opener_store = None
        _log.warning("no opener store: outbound greetings will be synthesized live")

    mount_health_route(app)
    mount_answer_route(app, settings=settings, on_connected=_mark_call_connected)

    @app.websocket("/ws")
    async def media_stream(websocket: WebSocket) -> None:
        signature_url = external_url(
            settings.public_base_url,
            websocket.url.path,
            websocket.url.query,
            websocket=True,
        )
        if not valid_twilio_signature(
            signature_url,
            {},
            websocket.headers.get("x-twilio-signature", ""),
            settings.twilio_auth_token.get_secret_value(),
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()

        try:
            start = await _read_start(websocket)
        except (ValueError, WebSocketDisconnect) as exc:
            _log.warning("rejecting media stream: %s", exc)
            await websocket.close(code=1008)
            return

        stream_sid = start.stream_id
        call_sid = start.call_id
        if start.account_id != settings.twilio_account_sid.get_secret_value():
            _log.warning("rejecting media stream: Twilio account SID mismatch")
            await websocket.close(code=1008)
            return
        lead_token = start.custom_parameters.get("lead")
        is_outbound = bool(lead_token)

        current_call_sid.set(call_sid)

        # The inbound half of Gate 0's money check. `dialer/precall.py` refuses a call the
        # budget cannot pay for, but it runs in the DIAL path — and inbound never dials, so
        # until this existed the cap was measured at teardown and enforced nowhere. Anyone
        # who has the number can ring it, so "we only call test handsets" is no longer a
        # spending limit.
        #
        # Answer time is the one clean place to refuse: precall's docstring rules out an
        # in-call check because it "would have to hang up on a lead mid-sentence", and that
        # objection does not apply before the pipeline exists. Nothing has been spent yet.
        if SpendLedger(spend_ledger_path(settings)).exhausted(settings.openai_budget_inr):
            _log.error(
                "refusing inbound call %s: the ₹%.2f testing budget is spent. Raise "
                "OPENAI_BUDGET_INR *and* the OpenAI dashboard hard limit (docs/02).",
                call_sid,
                settings.openai_budget_inr,
            )
            await websocket.close(code=1013)  # try again later
            return

        _log.info("media stream started: stream_sid=%s call_sid=%s", stream_sid, call_sid)
        # The callee's "hello" window opens HERE, not when the pipeline finishes building —
        # see `PickupGreeter._remaining_wait`.
        connected_at = time.monotonic()

        serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            account_sid=settings.twilio_account_sid.get_secret_value(),
            auth_token=settings.twilio_auth_token.get_secret_value(),
            params=TwilioFrameSerializer.InputParams(auto_hang_up=auto_hang_up),
        )
        vad = build_vad_fn(settings)
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                audio_in_sample_rate=_RATE,
                audio_out_sample_rate=_RATE,
                serializer=serializer,
            ),
        )

        counter = InboundAudioCounter()
        stt = build_stt_fn(settings)
        health = CallHealth()
        flight = TurnFlight()
        transcript = TranscriptionLogger(flight=flight)
        # The opener clip rendered during the ring (`dialer.openerstore`), read HERE because
        # BOTH opening processors are configured from whether it exists — and the guard is
        # built on the very next line. Read once, not inside the greeter's callable: `_greet`
        # fires on the lead's first sound, and a Redis round trip at that moment would sit in
        # front of the audio it exists to make instant.
        opener_pcm = (
            await _load_prerendered_opener(app, lead_token)
            if is_outbound
            else None
        )
        # TWO questions, not one. Conflating them cost call 0ce455b0 its entire five minutes.
        #
        #   already_spoken       Did something ELSE say the opener at connect? Inbound only —
        #                        it is the "Hello, Weltec Institute" an answerer says at t=0.
        #                        Outbound the CALLEE speaks first, so playing it would talk
        #                        over their "hello?". Decides whether PickupGreeter stands
        #                        down; a greeter that stands down NEVER SPEAKS AT ALL.
        #
        #   opener_is_raw_audio  Will the opener finish without a `BotStoppedSpeakingFrame`?
        #                        True of ANY raw-audio opener — inbound canned or outbound
        #                        pre-rendered. Decides whether OpeningTurnGuard starts open;
        #                        a guard left shut HOLDS EVERY TRANSCRIPT for the whole call.
        #
        # A pre-rendered outbound opener answers NO to the first and YES to the second, and
        # one flag cannot say that. The single `opening_is_canned` that fed both said the
        # wrong thing to each: `outbound_frames=0` and six `held a transcript` lines while
        # the lead said "बोलिए" into silence. Roma neither spoke nor listened for 5m13s.
        #
        # Computed in `opening.opening_posture` rather than here, because inline in this
        # handler it was untestable — which is exactly how it shipped wrong.
        posture = opening_posture(
            is_outbound=is_outbound,
            has_canned_line=app.state.opening_line is not None,
            has_prerendered_opener=bool(opener_pcm),
        )
        opening_guard = OpeningTurnGuard(opener_is_raw_audio=posture.opener_is_raw_audio)
        noise_gate = NoiseGate()

        recorder = capture = queue = None
        if settings.recording_enabled:
            recorder = build_recorder(settings, call_sid or stream_sid)
            capture = attach_recorder(recorder)
            queue = build_queue_fn(settings)

        store = build_store_fn(settings)
        state = None
        if call_sid:
            try:
                state = await store.load(call_sid)
            except Exception:  # noqa: BLE001 - resume is best-effort; never block the call
                _log.warning("call-state load failed; starting fresh")
        if state is None:
            # The dynamic variables from the trigger, if this call was one we placed. Only on
            # a FRESH state: a resumed call already carries what it learned, and re-seeding
            # would overwrite a name the lead corrected mid-call with the one the CRM had.
            seed = dict(DEFAULT_LEAD)
            lead = await _load_triggered_lead(app, lead_token)
            if lead is not None:
                seed.update(lead.as_state_seed())
                _log.info(
                    "outbound lead attached: segment=%s named=%s",
                    lead.segment or "-",
                    bool(lead.lead_name),
                )
            state = CallState(call_sid=call_sid or "", **seed)

        context = LLMContext(
            messages=[
                {
                    "role": "system",
                    "content": assemble_system_prompt(state.as_prompt_vars(), state.phase),
                }
            ]
        )
        aggregators = LLMContextAggregatorPair(
            context,
            user_params=build_user_params(settings.enable_barge_in, settings),
        )
        slot_client = app.state.slot_client
        prewarm_task = None
        if not getattr(app.state, "slot_prewarmed", False):
            app.state.slot_prewarmed = True
            prewarm_task = asyncio.create_task(prewarm_slot_client(slot_client))
        phase_ctrl = PhaseControllerProcessor(
            state,
            client=slot_client,
            store=store,
            flight=flight,
            health=health,
            fillers=(
                FillerPicker(getattr(app.state, "fillers", None))
                if settings.enable_filler
                else None
            ),
            holding=FillerPicker(getattr(app.state, "holding", None)),
            calendar=app.state.calendar,
        )
        watchdog = SilenceWatchdog(flight=flight)
        closer = CallCloser(
            lambda: phase_ctrl.won, phase_ctrl.elapsed, deaf_fn=lambda: health.deaf
        )
        # Outbound speaks its opener from the template instead of generating it — the one
        # turn whose text is fully determined is the one turn that must not wait on a network
        # round trip (see `llm.prompts.opening_line`). Inbound keeps `None`: its opener is the
        # canned `.ulaw` and this processor stands down entirely.
        #
        # `opener_pcm` was loaded up with the two opening predicates, because the guard is
        # constructed from it ~100 lines above this one.
        if opener_pcm:
            _log.info("opener cache HIT (%d bytes) — greeting will skip TTS", len(opener_pcm))
            # Raw audio produces no TextFrame, so the assistant aggregator has nothing to
            # record and Roma's own opener would be missing from the history she reasons
            # over. Inbound tolerates that (its opener is a statement); outbound's ASKS a
            # question — "kya abhi 2 minute baat ho sakti hai?" — and a Roma who cannot see
            # that she asked it re-introduces herself on turn two.
            context.get_messages().append(
                {"role": "assistant", "content": opening_line(state.lead_name)}
            )
        elif is_outbound:
            _log.info("opener cache miss — greeting will be synthesized live")
        # `opening_already_spoken`, NOT `opener_is_raw_audio`. This greeter OWNS the
        # pre-rendered clip — `opening_audio_fn` below is how it gets played — so telling it
        # the opener was already handled is telling it to drop the only line it had.
        greeter = PickupGreeter(
            opening_already_spoken=posture.already_spoken,
            opening_text_fn=(lambda: opening_line(state.lead_name)) if is_outbound else None,
            opening_audio_fn=(lambda: opener_pcm) if opener_pcm else None,
            connected_at=connected_at,
        )
        llm = build_llm_fn(settings)
        usage = _UsageLogger(
            ledger=SpendLedger(spend_ledger_path(settings)),
            call_sid=call_sid or stream_sid,
            prefix_fn=lambda: phase_ctrl.prefix_hash,
        )
        sentence_agg = InterruptibleSentenceAggregator()
        pretts = PreTTSFilterProcessor(
            lambda: state.slot_status,
            lambda: state.phase,
            lambda: phase_ctrl.filler_this_turn,
            lambda: state.lead_wants_out,
            lambda: state.facts_said,
            lambda: spoken_slot(state.accepted_slot),
        )
        tts = build_tts_fn(settings, getattr(app.state, "phrase_cache", None))
        sanitizer = TTSAudioSanitizer()

        task = PipelineTask(
            Pipeline(
                [
                    transport.input(),
                    *vad_stage(vad),
                    counter,
                    # BEFORE stt, deliberately. `SarvamSTTService.start()` awaits its own
                    # websocket connect before forwarding StartFrame, so anything downstream
                    # of it cannot even arm a timer until Sarvam is up — 3.4s on call
                    # baf9cbe9, which made a 1.0s greeting timeout fire 4.3s after pickup.
                    # The greeter needs no transcript: VADUserStartedSpeakingFrame from
                    # `vad_stage` already tells it the lead made a sound.
                    greeter,
                    stt,
                    transcript,
                    noise_gate,
                    opening_guard,
                    aggregators.user(),
                    phase_ctrl,
                    llm,
                    sentence_agg,
                    pretts,
                    tts,
                    usage,
                    sanitizer,
                    transport.output(),
                    *([capture] if capture is not None else []),
                    watchdog,
                    closer,
                    aggregators.assistant(),
                ]
            ),
            params=PipelineParams(
                audio_in_sample_rate=_RATE,
                audio_out_sample_rate=_RATE,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        @task.event_handler("on_pipeline_error")
        async def _on_pipeline_error(_task, frame) -> None:
            """Every service failure on this call, recorded and made loud.

            Pipecat logs a non-fatal `ErrorFrame` at WARNING under its own logger and
            carries on. That is right for a hiccup and wrong for a service that never came
            up: on CA34ca96e the STT connect timed out at second eleven and the only trace
            was one line in a 300 KB debug log, while the lead spent the call talking into
            a line that could not hear them.
            """
            health.record(frame)

        @transport.event_handler("on_client_connected")
        async def _on_connected(_transport, _client) -> None:
            # Inbound: WE are the one who answered, so we speak first and immediately. Both
            # of these are bytes off disk — no LLM, no TTS — because the caller is holding
            # the phone to their ear waiting to hear the call rang through, and the cold
            # first generated turn costs 3.28s (CAfe5a00b).
            # Outbound: say NOTHING at connect. The callee has just picked up and is about
            # to say "hello?"; the opener is inbound's answering line and PickupGreeter owns
            # the first turn here instead. Consent, when it exists, still leads on both.
            opening = [
                line
                for line in (
                    app.state.consent_line,
                    None if is_outbound else app.state.opening_line,
                )
                if line is not None
            ]
            try:
                if opening:
                    await task.queue_frames(
                        [
                            OutputAudioRawFrame(
                                audio=line.pcm, sample_rate=_RATE, num_channels=1
                            )
                            for line in opening
                        ]
                    )
            except Exception:  # noqa: BLE001 — a dead-air call is worse than a dropped one
                _log.exception("call opening failed; cancelling stream_sid=%s", stream_sid)
                await task.queue_frames([CancelFrame()])

        handles = CallHandles(
            counter=counter,
            transcript=transcript,
            pretts=pretts,
            phase_ctrl=phase_ctrl,
            recorder=recorder,
            usage=usage,
            health=health,
        )
        async with _registered_call(app, stream_sid, handles):
            try:
                await PipelineRunner(handle_sigint=False).run(task)
            finally:
                if prewarm_task is not None:
                    prewarm_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await prewarm_task
                # Before `_registered_call`'s own finally: pops this call out of
                # `app.state.calls` and it stops existing anywhere in the process (docs/13).
                await _mark_call_ended(app, lead_token, "won" if phase_ctrl.won else "")
                postcall = await finalize_call(
                    state=state,
                    store=store,
                    recorder=recorder,
                    queue=queue,
                    media_root=media_dir(settings),
                )
                _log.info(
                    "media stream ended: stream_sid=%s inbound_frames=%d inbound_bytes=%d "
                    "outbound_frames=%d outbound_bytes=%d clears=%d "
                    "phase=%s won=%s recorded_bytes=%d postcall=%s",
                    stream_sid,
                    counter.frame_count,
                    counter.byte_count,
                    getattr(serializer, "play_frames", 0),
                    getattr(serializer, "play_bytes", 0),
                    getattr(serializer, "clear_events", 0),
                    state.phase,
                    phase_ctrl.won,
                    recorder.bytes_written if recorder is not None else 0,
                    postcall,
                )
                _log.info(
                    "endpoint timing: stream_sid=%s vad=%s vad_stop_secs=%s "
                    "vad_starts=%d vad_stops=%d barge_in=%s stt_alive=%s "
                    "service_errors=%d degraded=%s fillers_played=%s signoff_holds=%d time_talk_holds=%d "
                    "nudges=%d dupe_drops=%d pacer_clips=%d repeat_skips=%d "
                    "prefix=%s prefix_changes=%d short_circuits=%d deflections=%d "
                    "phrase_cache_hits=%d phrase_saved=₹%.2f "
                    "ttfb=%s slot_extract=%s turn_latency=%s heard_latency=%s "
                    "cost=₹%.2f phase_spent=₹%.2f/%.2f",
                    stream_sid,
                    type(vad).__name__ if vad is not None else "none",
                    f"{vad.params.stop_secs:.2f}" if vad is not None else "n/a",
                    counter.vad_starts,
                    counter.vad_stops,
                    settings.enable_barge_in,
                    not health.deaf,
                    len(health.errors),
                    health.degraded or "-",
                    getattr(phase_ctrl._fillers, "played", "off"),
                    pretts.signoff_holds,
                    pretts.time_talk_holds,
                    watchdog.nudges,
                    pretts.dupe_drops,
                    phase_ctrl.pacer_clips,
                    phase_ctrl.repeat_skips,
                    phase_ctrl.prefix_hash or "-",
                    phase_ctrl.prefix_changes,
                    phase_ctrl.short_circuits,
                    phase_ctrl.deflections,
                    getattr(tts, "phrase_hits", 0),
                    saved_inr(getattr(tts, "phrase_chars", 0)),
                    usage.ttfb_summary(),
                    _summarize_secs(phase_ctrl.advance_secs),
                    flight.summary(),
                    flight.heard_summary(),
                    usage.call_inr,
                    SpendLedger(spend_ledger_path(settings)).spent_inr,
                    settings.openai_budget_inr,
                )
                if vad is not None and counter.vad_stops == 0 and counter.frame_count > 0:
                    _log.warning(
                        "VAD produced no stop events across %d inbound audio frames — "
                        "endpointing ran on STT alone, barge-in was dead, and Sarvam never "
                        "flushed. This is the CA1bf16a bug class; check `vad_stage`.",
                        counter.frame_count,
                    )

    return app


__all__ = [
    "build_media_app",
    "InboundAudioCounter",
    "CallHandles",
    "active_calls",
    "sole_call",
    "build_vad",
    "vad_stage",
    "build_stt",
    "build_llm",
    "build_tts",
    "build_store",
    "build_slot_client",
]
