import pytest
from roma.domain.conversation.state import CallState
from roma.domain.knowledge.routing import KnowledgeRoute, classify_turn


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("Course ke modules kya hain?", "knowledge"),
        ("Batch schedule kya hai?", "knowledge"),
        ("Branch address kya hai?", "knowledge"),
        ("Policy kya hai?", "knowledge"),
        ("Fees kya hai?", "safety"),
        ("salary achhi hai?", "safety"),
        ("placement support?", "safety"),
        ("discount milega?", "safety"),
        ("Reschedule my appointment", "booking"),
        ("Cancel my appointment", "booking"),
        ("Book a visit", "booking"),
        ("Who won the cricket match?", "no_evidence"),
    ],
)
def test_explicit_routes_are_code_owned(text, route):
    assert classify_turn(CallState(stage="value"), text).route.value == route


def test_discovery_name_and_stage_inputs_stay_with_controller():
    assert (
        classify_turn(CallState(stage="discover"), "Mera naam Amit hai").route
        == KnowledgeRoute.STATE
    )
    assert (
        classify_turn(CallState(stage="discover", lead_name="Amit"), "I work").route
        == KnowledgeRoute.STATE
    )


def test_safety_wins_over_knowledge_and_booking_keywords():
    decision = classify_turn(
        CallState(stage="pivot"), "Book appointment and tell fees for course"
    )
    assert decision.route == KnowledgeRoute.SAFETY


def test_unknown_never_defaults_to_knowledge():
    assert (
        classify_turn(CallState(stage="value"), "something unknown").route
        == KnowledgeRoute.NO_EVIDENCE
    )


def test_runtime_unknown_and_safety_skip_model_and_discovery_extraction():
    import asyncio
    from datetime import UTC, datetime

    from roma.services.text_conversation_service import TextConversationService

    class Never:
        async def generate(self, request):
            raise AssertionError("unapproved model call")
            yield

    async def run():
        for text, route in [
            ("Fees kya hai?", "safety"),
            ("Who won cricket?", "no_evidence"),
            ("Branch address?", "knowledge"),
        ]:
            state = CallState(stage="discover", lead_name="Amit")
            trace = await TextConversationService(Never()).turn(
                state, text, now=datetime.now(UTC)
            )
            assert trace.route == route
            assert state.current_status is None and not state.slot_attempts
            assert trace.raw_model_output == ""
            assert trace.final_response

    asyncio.run(run())


def test_static_source_is_bounded_and_does_not_repeat_facts():
    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE, approved_static_reply

    state = CallState(stage="value")
    assert "SEO" in approved_static_reply(state, "Course modules?")
    assert approved_static_reply(state, "Course accreditation?") == NO_EVIDENCE_LINE
    state.record_facts({"modules"})
    assert approved_static_reply(state, "Course modules?") == NO_EVIDENCE_LINE


def test_discovery_course_answer_is_not_a_knowledge_request():
    assert (
        classify_turn(CallState(stage="discover"), "Main course kar raha hoon").route
        == KnowledgeRoute.STATE
    )


def test_realtime_routes_before_extraction_and_never_forwards_knowledge_context():
    import asyncio
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from pipecat.frames.frames import LLMContextFrame, TextFrame
    from pipecat.processors.aggregators.llm_context import LLMContext
    from roma.realtime.stage_controller import StageControllerProcessor

    async def run():
        for text, expected in [
            ("Fees kya hai?", "safety"),
            ("Course modules?", "knowledge"),
            ("Who won cricket?", "no_evidence"),
            ("Branch address?", "knowledge"),
        ]:
            state = CallState(stage="discover", lead_name="Amit")
            extract = AsyncMock(
                side_effect=AssertionError("Factual text cannot fill discovery")
            )
            proc = StageControllerProcessor(
                state, now_fn=lambda: datetime.now(UTC), extract_discovery=extract
            )
            proc.push_frame = AsyncMock()
            frame = LLMContextFrame(LLMContext([{"role": "user", "content": text}]))
            await proc._drive(frame)
            assert proc._route_decision.route.value == expected
            assert await proc._maybe_short_circuit(frame)
            assert not state.slot_attempts and state.current_status is None
            extract.assert_not_awaited()
            assert any(
                isinstance(call.args[0], TextFrame) for call in proc.push_frame.call_args_list
            )
            assert not any(
                isinstance(call.args[0], LLMContextFrame)
                for call in proc.push_frame.call_args_list
            )

    asyncio.run(run())


def test_factual_interruptions_preserve_global_deadline_and_committed_booking():
    import asyncio
    from datetime import UTC, datetime

    from roma.domain.conversation.confirmguard import SAFE_SIGNOFF_LINE
    from roma.services.text_conversation_service import TextConversationService

    class Never:
        async def generate(self, request):
            raise AssertionError("Factual interruption cannot invoke provider")
            yield

    async def run():
        state = CallState(stage="discover", lead_name="Amit")
        await TextConversationService(Never()).turn(
            state, "Course modules kya hain?", now=datetime.now(UTC), elapsed_secs=181
        )
        assert state.stage == "pivot" and not state.slot_attempts
        state = CallState(stage="discover", lead_name="Amit")
        trace = await TextConversationService(Never()).turn(
            state, "Course modules kya hain?", now=datetime.now(UTC), elapsed_secs=301
        )
        assert state.lead_wants_out and trace.final_response == SAFE_SIGNOFF_LINE
        state = CallState(
            stage="close",
            booking_required=True,
            appointment_id="7",
            locked_slot="2026-10-26T11:00:00+05:30",
            slot_status="locked",
        )
        trace = await TextConversationService(Never()).turn(
            state, "Course modules kya hain?", now=datetime.now(UTC)
        )
        assert "SEO" not in trace.final_response and trace.booking_committed

    asyncio.run(run())


def test_imperative_requests_do_not_fill_discovery():
    for stage in ["open", "discover"]:
        assert (
            classify_turn(CallState(stage=stage), "Tell me about course duration").route
            == KnowledgeRoute.KNOWLEDGE
        )


def test_static_course_facts_are_scoped_to_the_reviewed_digital_marketing_course():
    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE, approved_static_reply

    assert (
        approved_static_reply(CallState(course="python"), "Course duration?")
        == NO_EVIDENCE_LINE
    )
    assert approved_static_reply(CallState(), "Python course duration?") == NO_EVIDENCE_LINE
    assert approved_static_reply(CallState(), "MBA course modules?") == NO_EVIDENCE_LINE


_INFORMATIONAL_BOOKING_TURNS = [
    ("What is the branch visit policy?", KnowledgeRoute.KNOWLEDGE),
    ("appointment document requirements", KnowledgeRoute.NO_EVIDENCE),
    ("course duration before I book a visit?", KnowledgeRoute.KNOWLEDGE),
    ("How do I cancel my appointment?", KnowledgeRoute.NO_EVIDENCE),
    ("Tell me about the booking process", KnowledgeRoute.NO_EVIDENCE),
    ("appointment cancellation policy", KnowledgeRoute.KNOWLEDGE),
]


@pytest.mark.parametrize(("text", "route"), _INFORMATIONAL_BOOKING_TURNS)
@pytest.mark.parametrize("stage", ["open", "discover", "value", "pivot", "close"])
def test_booking_information_is_not_a_booking_command(stage, text, route):
    assert classify_turn(CallState(stage=stage, lead_name="Amit"), text).route == route


@pytest.mark.parametrize(
    "text",
    [
        "Book a visit",
        "Please book an appointment",
        "Can you book an appointment?",
        "Could you please reschedule my appointment?",
        "Cancel my appointment",
        "appointment book kar do",
        "Kya appointment book kar sakte ho?",
    ],
)
def test_explicit_booking_commands_still_reach_controller(text):
    assert classify_turn(CallState(stage="discover"), text).route == KnowledgeRoute.BOOKING


@pytest.mark.parametrize("stage", ["pivot", "close"])
@pytest.mark.parametrize("text", ["Tuesday", "3 baje", "haan bilkul confirm"])
def test_booking_slot_and_consent_answers_still_reach_controller(stage, text):
    assert classify_turn(CallState(stage=stage), text).route == KnowledgeRoute.BOOKING


def test_booking_information_never_mutates_profile_or_calls_text_provider():
    import asyncio
    from datetime import UTC, datetime

    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
    from roma.services.text_conversation_service import TextConversationService

    class Never:
        async def generate(self, request):
            raise AssertionError("Booking information cannot invoke provider")
            yield

    async def run():
        for text, expected in _INFORMATIONAL_BOOKING_TURNS:
            state = CallState(stage="discover", lead_name="Amit")
            trace = await TextConversationService(Never()).turn(
                state, text, now=datetime.now(UTC)
            )
            assert trace.route == expected.value
            assert state.stage == "discover" and state.lead_name == "Amit"
            assert state.current_status is None and state.education is None
            assert state.passing_year is None and state.city is None
            assert not state.slot_attempts and not state.accepted_slot
            assert trace.raw_model_output == ""
            assert trace.final_response == NO_EVIDENCE_LINE

    asyncio.run(run())


def test_booking_information_never_mutates_profile_or_forwards_live_context():
    import asyncio
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from pipecat.frames.frames import LLMContextFrame, TextFrame
    from pipecat.processors.aggregators.llm_context import LLMContext
    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
    from roma.realtime.stage_controller import StageControllerProcessor

    async def run():
        for text, expected in _INFORMATIONAL_BOOKING_TURNS:
            state = CallState(stage="discover", lead_name="Amit")
            extract = AsyncMock(side_effect=AssertionError("Not a discovery answer"))
            proc = StageControllerProcessor(
                state, now_fn=lambda: datetime.now(UTC), extract_discovery=extract
            )
            proc.push_frame = AsyncMock()
            frame = LLMContextFrame(LLMContext([{"role": "user", "content": text}]))
            await proc._drive(frame)
            assert proc._route_decision.route == expected
            assert await proc._maybe_short_circuit(frame)
            assert state.stage == "discover" and state.lead_name == "Amit"
            assert state.current_status is None and state.education is None
            assert state.passing_year is None and state.city is None
            assert not state.slot_attempts and not state.accepted_slot
            extract.assert_not_awaited()
            frames = [call.args[0] for call in proc.push_frame.call_args_list]
            assert any(isinstance(item, TextFrame) and item.text == NO_EVIDENCE_LINE for item in frames)
            assert not any(isinstance(item, LLMContextFrame) for item in frames)

    asyncio.run(run())


@pytest.mark.parametrize(("elapsed", "stage", "leaving"), [
    (0, "discover", False),
    (180, "pivot", False),
    (240, "pivot", False),
    (300, "pivot", True),
])
def test_factual_booking_words_preserve_controller_deadlines(elapsed, stage, leaving):
    import asyncio
    from datetime import UTC, datetime

    from roma.domain.conversation.turn import advance_turn

    state = CallState(stage="discover", lead_name="Amit", lead_time_asks=1)
    asyncio.run(advance_turn(
        state,
        "What is the branch visit policy?",
        now=datetime.now(UTC),
        elapsed_secs=elapsed,
        controller_only=True,
    ))
    assert state.stage == stage and state.lead_wants_out is leaving
    assert state.lead_time_asks == 1
    assert not state.slot_attempts and state.current_status is None
