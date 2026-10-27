"""Synthetic local-HTTP voice composition; no model, microphone, carrier or paid API.

The only test adapter in the frame path wraps an already finalized transcript in
LLMContextFrame (the production user aggregator's seam). All routing, HTTP wire
validation, retrieval/quote release, speech safety, PCM and carrier serialization
use the real implementations. SQL lives in an owned random-port disposable DB.
"""

import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
from pipecat.frames.frames import (
    EndFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMContextFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pydantic import SecretStr
from roma.core.ai_contracts import LLMChunk, STTResult, TTSResult
from roma.core.serving_settings import SpeechServingSettings
from roma.core.turn_epoch import TurnEpoch
from roma.domain.audio.g711 import encode
from roma.domain.conversation.prompts import assemble_system_prompt
from roma.domain.conversation.state import CallState
from roma.domain.knowledge.retrieval import Evidence
from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
from roma.domain.safety.lexicon import SUBSTITUTIONS, BlockCategory
from roma.providers.ai.mocks import MockEmbeddingProvider
from roma.providers.ai.speech_http import HTTPSTTProvider, HTTPTTSProvider
from roma.realtime.knowledge import LiveKnowledge
from roma.realtime.local_speech import (
    LocalSTTProcessor,
    LocalTTSProcessor,
    prime_local_deferral,
)
from roma.realtime.media_limits import BoundedMediaWebSocket
from roma.realtime.phrasecache import PhraseCache
from roma.realtime.pipeline import close_call_resources
from roma.realtime.pretts import PreTTSFilterProcessor
from roma.realtime.provider_llm import CallLLMProvider, ProviderLLMService
from roma.realtime.sentences import InterruptibleSentenceAggregator
from roma.realtime.stage_controller import StageControllerProcessor
from roma.repositories.postgres.models import Call, CallTurn
from roma.repositories.postgres.models.organization import (
    Branch,
    BranchCourse,
    Course,
    Institute,
)
from roma.services.knowledge_answer import KnowledgeAnswerService
from roma.services.knowledge_retrieval import KnowledgeSearchService
from roma.services.knowledge_runtime import ScopedKnowledgeAnswers
from roma.services.safety_service import InMemorySafetyEventStore, SafetyService
from roma.serving.app import create_speech_app
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Reuse the native, random-port fixture without any configured database settings.
pytest_plugins = ("tests.repositories.postgres.test_costs",)
pytestmark = pytest.mark.postgres
DOWN = FrameDirection.DOWNSTREAM
PCM = b"\x11\x00" * 800
FACT = "वडोदरा शाखा अलकापुरी में है।"


class _Inference:
    def __init__(self, transcript):
        self.transcript, self.stt_requests, self.tts_requests = transcript, [], []
        self.closed = 0

    async def transcribe(self, request):
        self.stt_requests.append(request)
        return STTResult(self.transcript, "hi", turn_epoch=request.turn_epoch)

    async def synthesize(self, request):
        self.tts_requests.append(request)
        return TTSResult(b"\x22\x00" * 320, request.format, 40, request.turn_epoch)

    async def aclose(self):
        self.closed += 1


class _EvidenceRepository:
    def __init__(self):
        self.evidence = Evidence(
            uuid4(), uuid4(), FACT, hashlib.sha256(FACT.encode()).hexdigest(), 1, "hi", 0.95
        )
        self.searches, self.audits = [], []

    async def search_chunks(self, **kwargs):
        self.searches.append(kwargs)
        return (self.evidence,)

    async def revalidate_evidence(self, **kwargs):
        return (self.evidence,)

    async def record_retrieval(self, metadata, **kwargs):
        self.audits.append(metadata)


class _Selector:
    def __init__(self, repo, *, blocked=False):
        self.repo, self.blocked, self.requests = repo, blocked, []
        self.entered, self.exited = asyncio.Event(), asyncio.Event()

    async def generate(self, request):
        self.requests.append(request)
        self.entered.set()
        try:
            if self.blocked:
                await asyncio.Event().wait()
            yield LLMChunk(
                json.dumps({"chunk_id": str(self.repo.evidence.chunk_id), "quote": FACT}),
                "stop",
            )
        finally:
            self.exited.set()


class _Wording:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        yield LLMChunk("मैं काउन्सलिंग टीम से जानकारी पूछ लेती हूँ।", "stop")


class _UserContext(FrameProcessor):
    def __init__(self, state):
        super().__init__()
        self.state, self.transcripts = state, []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction == DOWN:
            self.transcripts.append(frame.text)
            frame = LLMContextFrame(
                LLMContext(
                    [
                        {
                            "role": "system",
                            "content": assemble_system_prompt(
                                self.state.as_prompt_vars(), self.state.stage
                            ),
                        },
                        {"role": "user", "content": frame.text},
                    ]
                )
            )
        await self.push_frame(frame, direction)


class _Wire(FrameProcessor):
    def __init__(self, serializer, socket):
        super().__init__()
        self.serializer, self.socket = serializer, socket
        self.done, self.texts = asyncio.Event(), []
        self.interrupted = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            payload = await self.serializer.serialize(frame)
            assert payload is not None
            await self.socket.send_text(payload)
        if isinstance(frame, TextFrame):
            self.texts.append(frame.text)
        if isinstance(frame, TTSStoppedFrame):
            self.done.set()
        if isinstance(frame, InterruptionFrame) and direction == DOWN:
            self.interrupted.set()
        await self.push_frame(frame, direction)


async def _seed(factory, stage):
    institute, branch, course, call = (uuid4() for _ in range(4))
    state = CallState(
        call_sid="CA-test-" + uuid4().hex,
        stage=stage,
        branch="TestOnly-" + branch.hex,
        course="test-" + course.hex,
        lead_name="Asha",
    )
    async with factory() as session, session.begin():
        session.add(Institute(id=institute, code=institute.hex, name="TEST ONLY"))
        await session.flush()
        session.add_all(
            [
                Branch(
                    id=branch,
                    institute_id=institute,
                    code=branch.hex,
                    name=state.branch,
                    city="TEST ONLY",
                    timezone="Asia/Kolkata",
                ),
                Course(id=course, institute_id=institute, code=state.course, name="TEST ONLY"),
                Call(
                    id=call,
                    provider_call_id=state.call_sid,
                    direction="inbound",
                    status="in_progress",
                    started_at=datetime.now(UTC),
                    retention_until=datetime(2035, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        await session.flush()
        session.add(BranchCourse(branch_id=branch, course_id=course))
    return state, call, course, branch


@asynccontextmanager
async def _flow(
    url,
    text="Branch address?",
    *,
    stage="discover",
    blocked=False,
    deadline_secs=1,
    cache_dir=None,
):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    state, call, course, branch = await _seed(factory, stage)
    repo, inference, audit = _EvidenceRepository(), _Inference(text), InMemorySafetyEventStore()
    safety, epoch = SafetyService(audit), TurnEpoch()
    selector = _Selector(repo, blocked=blocked)
    search = KnowledgeSearchService(repo, MockEmbeddingProvider(), audit_repository=repo)
    answer = KnowledgeAnswerService(
        search, CallLLMProvider(selector, epoch=epoch), safety=safety
    )
    live = LiveKnowledge(
        ScopedKnowledgeAnswers(factory, answer), timeout_secs=deadline_secs, epoch=epoch
    )
    cfg = SpeechServingSettings(
        _env_file=None, speech_service_key=SecretStr("integration-speech-key-0123456789abcdef")
    )
    warmed = []

    async def warmup():
        warmed.append(True)

    app = create_speech_app(cfg, stt=inference, tts=inference, warmup=warmup)
    sent, closes = [], []

    async def send_text(payload):
        sent.append(json.loads(payload))

    async def close(*, code):
        closes.append(code)

    socket = BoundedMediaWebSocket(SimpleNamespace(send_text=send_text, close=close))
    serializer = TwilioFrameSerializer(
        stream_sid="MZ-synthetic-integration",
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
    await serializer.setup(StartFrame(audio_in_sample_rate=8000, audio_out_sample_rate=8000))
    runner, running = PipelineRunner(handle_sigint=False), None
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client,
        ):
            stt_http, tts_http = (
                HTTPSTTProvider(cfg, client=client),
                HTTPTTSProvider(cfg, client=client),
            )
            assert warmed == [True] and await stt_http.ready() and await tts_http.ready()
            phrases = PhraseCache(cache_dir) if cache_dir is not None else None
            if phrases is not None:
                await prime_local_deferral(phrases, tts_http)
            asr = LocalSTTProcessor(stt_http, epoch=epoch, language_hint="hi")
            user = _UserContext(state)
            controller = StageControllerProcessor(
                state,
                knowledge=live,
                dialogue_language="voice_hi",
                safety=safety,
                turn_epoch=epoch,
            )
            gate = PreTTSFilterProcessor(
                safety=safety,
                slot_status_fn=lambda: state.slot_status,
                stage_fn=lambda: state.stage,
                wants_out_fn=lambda: state.lead_wants_out,
                booking_state_fn=lambda: state,
            )
            tts = LocalTTSProcessor(
                tts_http, phrases=phrases, state=state, safety=safety, epoch=epoch
            )
            wording, wire = _Wording(), _Wire(serializer, socket)
            task = PipelineTask(
                Pipeline(
                    [
                        asr,
                        user,
                        controller,
                        ProviderLLMService(CallLLMProvider(wording, epoch=epoch)),
                        InterruptibleSentenceAggregator(),
                        gate,
                        tts,
                        wire,
                    ]
                ),
                params=PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000),
            )
            running = asyncio.create_task(runner.run(task))
            incoming = await serializer.deserialize(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": "MZ-synthetic-integration",
                        "media": {"payload": base64.b64encode(encode(PCM)).decode()},
                    }
                )
            )
            assert isinstance(incoming, InputAudioRawFrame)
            await task.queue_frames(
                [VADUserStartedSpeakingFrame(), incoming, VADUserStoppedSpeakingFrame()]
            )
            harness = SimpleNamespace(
                state=state,
                call=call,
                course=course,
                branch=branch,
                repo=repo,
                selector=selector,
                inference=inference,
                audit=audit,
                live=live,
                wire=wire,
                task=task,
                running=running,
                sent=sent,
                user=user,
                wording=wording,
                factory=factory,
                tts=tts,
                epoch=epoch,
                closes=closes,
            )
            yield harness
            if not running.done():
                await task.queue_frame(EndFrame())
            await asyncio.wait_for(running, 2)
        assert client.is_closed
        assert inference.closed == 2
    finally:
        if running is not None and not running.done():
            await task.cancel()
            await asyncio.wait_for(running, 2)
        await close_call_resources(live)
        await engine.dispose()


def test_http_pcm_controller_grounded_answer_safety_and_twilio_wire_share_real_audit_ids(
    cost_database,
):
    async def run():
        async with _flow(cost_database) as flow:
            await asyncio.wait_for(flow.wire.done.wait(), 3)
            assert flow.user.transcripts == ["Branch address?"]
            assert len(flow.inference.stt_requests) == len(flow.inference.tts_requests) == 1
            stt = flow.inference.stt_requests[0]
            assert stt.format.sample_rate_hz == 16000 and len(stt.audio) >= len(PCM) * 2
            assert FACT in flow.wire.texts
            assert flow.inference.tts_requests[0].text == FACT
            assert not flow.wording.requests and flow.state.stage == "discover"
            assert flow.state.current_status is None and not flow.state.slot_attempts
            assert len(flow.repo.audits) == 1
            metadata = flow.repo.audits[0]
            expected = uuid5(NAMESPACE_URL, f"roma:{flow.call}:turn:{flow.state.turn_count}")
            assert metadata.call_id == flow.call and metadata.turn_id == expected
            assert isinstance(metadata.request_id, UUID)
            assert metadata.chunk_ids == [flow.repo.evidence.chunk_id]
            assert metadata.selected_document_id == flow.repo.evidence.document_id
            assert not metadata.fallback_used
            scope = flow.repo.searches[0]["filters"]
            assert (scope.course_id, scope.branch_id, scope.language) == (
                flow.course,
                flow.branch,
                "hi",
            )
            async with flow.factory() as session:
                turn = await session.get(CallTurn, expected)
                assert (
                    turn is not None
                    and turn.transcript is None
                    and turn.retention_until.year == 2035
                )
            assert len(flow.sent) == 2
            assert all(
                event["event"] == "media" and event["streamSid"] == "MZ-synthetic-integration"
                for event in flow.sent
            )
            assert all(
                len(base64.b64decode(event["media"]["payload"])) == 160 for event in flow.sent
            )
        assert flow.live._pending is None and flow.running.done()

    asyncio.run(run())


def test_stalled_grounding_uses_readiness_cached_deferral_without_turn_tts(
    cost_database, tmp_path, monkeypatch
):
    scoped_answer = ScopedKnowledgeAnswers.answer_turn

    async def delayed_scope(self, *args, **kwargs):
        # Scope lookup/turn persistence share the outer deadline. Deliberately
        # model scheduling/SQL delay so the test reaches the intended LLM stall
        # without relying on an idle machine completing SQL inside 100 ms.
        await asyncio.sleep(0.15)
        return await scoped_answer(self, *args, **kwargs)

    monkeypatch.setattr(ScopedKnowledgeAnswers, "answer_turn", delayed_scope)

    async def run():
        async with _flow(
            cost_database, blocked=True, deadline_secs=1, cache_dir=tmp_path
        ) as flow:
            await asyncio.wait_for(flow.wire.done.wait(), 3)
            assert flow.selector.entered.is_set() and flow.selector.exited.is_set()
            assert flow.live._pending is None
            assert flow.wire.texts == [NO_EVIDENCE_LINE]
            assert flow.tts.phrase_hits == 1
            # Exactly the readiness synthesis occurred; no inference was needed after timeout.
            assert len(flow.inference.tts_requests) == 1
            assert flow.inference.tts_requests[0].text != FACT
            assert flow.sent and not flow.wording.requests
            assert not flow.repo.audits  # Cancelled evidence was never audited as released.
            assert flow.state.current_status is None and not flow.state.slot_attempts
        assert flow.running.done() and flow.live._pending is None

    asyncio.run(run())


def test_protected_fee_question_bypasses_grounding_and_reaches_safe_http_speech(cost_database):
    async def run():
        async with _flow(cost_database, "What are fees?") as flow:
            await asyncio.wait_for(flow.wire.done.wait(), 3)
            assert flow.wire.texts == [SUBSTITUTIONS[BlockCategory.FEE]]
            assert (
                not flow.repo.searches
                and not flow.selector.requests
                and not flow.wording.requests
            )
            assert len(flow.inference.stt_requests) == len(flow.inference.tts_requests) == 1
            assert flow.inference.tts_requests[0].text != FACT
            assert any(event.category == "FEE" for _, event in flow.audit.entries)
            assert flow.sent and not flow.closes
        assert flow.running.done()

    asyncio.run(run())


def test_interruption_cancels_inflight_grounding_and_never_serializes_late_speech(
    cost_database,
):
    async def run():
        async with _flow(cost_database, blocked=True, deadline_secs=2) as flow:
            await asyncio.wait_for(flow.selector.entered.wait(), 3)
            before = flow.epoch.current
            await flow.task.queue_frame(InterruptionFrame())
            await asyncio.wait_for(flow.selector.exited.wait(), 1)
            await asyncio.wait_for(flow.wire.interrupted.wait(), 1)
            assert flow.epoch.current == before + 1
            assert not flow.inference.tts_requests and not flow.sent and not flow.wire.texts
            assert not flow.repo.audits and not flow.wording.requests
        assert flow.live._pending is None and flow.running.done()
        assert not flow.inference.tts_requests and not flow.sent

    asyncio.run(run())
