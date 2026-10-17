import asyncio
from uuid import uuid4

from pipecat.frames.frames import TextFrame
from pipecat.processors.frame_processor import FrameDirection
from roma.domain.conversation.state import CallState
from roma.realtime.pretts import PreTTSFilterProcessor
from roma.services.safety_service import InMemorySafetyEventStore, SafetyService


def test_pretts_requires_committed_identity_even_when_checkpoint_says_locked():
    async def run():
        state = CallState(booking_required=True, stage="close", slot_status="locked")
        events = InMemorySafetyEventStore()
        spoken = []
        proc = PreTTSFilterProcessor(
            lambda: state.slot_status,
            lambda: state.stage,
            booking_state_fn=lambda: state,
            safety=SafetyService(events),
        )

        async def capture(frame, direction):
            if isinstance(frame, TextFrame):
                spoken.append(frame.text)

        proc.push_frame = capture
        claim = "Aapki visit confirm ho gayi hai."
        await proc.process_frame(TextFrame(claim), FrameDirection.DOWNSTREAM)
        assert spoken and spoken[-1] != claim
        assert any(event.rule_id == "control.confirmation.v2" for _, event in events.entries)
        state.appointment_id = str(uuid4())
        await proc.process_frame(TextFrame(claim), FrameDirection.DOWNSTREAM)
        assert spoken[-1] == claim

    asyncio.run(run())


def test_real_conflict_is_not_rewritten_as_unlimited_availability():
    async def run():
        state = CallState(booking_required=True, stage="close", slot_status="unavailable")
        spoken = []
        proc = PreTTSFilterProcessor(
            lambda: state.slot_status, lambda: state.stage, booking_state_fn=lambda: state
        )

        async def capture(frame, direction):
            if isinstance(frame, TextFrame):
                spoken.append(frame.text)

        proc.push_frame = capture
        line = "Woh slot available nahi hai."
        await proc.process_frame(TextFrame(line), FrameDirection.DOWNSTREAM)
        assert spoken == [line]

    asyncio.run(run())


def test_unreadable_booking_identity_fails_towards_unconfirmed_speech():
    async def run():
        captured = []

        def broken():
            raise RuntimeError("synthetic unreadable state")

        proc = PreTTSFilterProcessor(lambda: "locked", lambda: "close", booking_state_fn=broken)

        async def capture(frame, direction):
            if isinstance(frame, TextFrame):
                captured.append(frame.text)

        proc.push_frame = capture
        claim = "Visit confirm ho gayi hai."
        await proc.process_frame(TextFrame(claim), FrameDirection.DOWNSTREAM)
        assert captured and captured[0] != claim

    asyncio.run(run())
