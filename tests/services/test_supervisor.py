import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from roma.domain.conversation.state import CallState
from roma.schemas.supervisor import SupervisorEvent
from roma.services.supervisor import SupervisorHub, relay_supervisor


def event():
    return SupervisorEvent(
        call_id=uuid4(),
        observed_at=datetime.now(UTC),
        stage="discover",
        language="gu",
        turn=2,
        duration_seconds=3,
        booking_confirmed=False,
        safety_hits=0,
        latency_p95_ms=None,
        costs=[],
    )


def test_supervisor_envelope_rejects_content_and_nonfinite_metrics():
    assert "phone" not in event().model_dump_json()
    for extra in (
        {"transcript": "private"},
        {"language": "ar"},
        {"duration_seconds": float("inf")},
        {"stage": "arbitrary"},
        {"costs": [{"currency": "USD", "amount": "NaN"}]},
    ):
        with pytest.raises(ValidationError):
            SupervisorEvent.model_validate({**event().model_dump(), **extra})


def test_supervisor_hub_is_bounded_and_never_enqueues_private_state():
    hub = SupervisorHub(None, None, capacity=1)
    state = CallState(call_sid="private-provider-id", lead_name="Private Person")
    hub.on_state(state)
    hub.on_state(state)
    assert hub.dropped == 1
    queued = hub.queue.get_nowait()
    assert not any(secret in repr(queued) for secret in ("Private Person", "lead_name"))
    assert queued.sid == "private-provider-id"  # local resolver only; never published


def test_supervisor_relay_rechecks_auth_and_releases_resources():
    async def scenario():
        pubsub = type(
            "PubSub",
            (),
            {
                "subscribe": AsyncMock(),
                "aclose": AsyncMock(),
                "get_message": AsyncMock(return_value=None),
            },
        )()
        redis = type("Redis", (), {"pubsub": lambda self: pubsub})()
        websocket = type("Socket", (), {"send_json": AsyncMock()})()
        calls = 0

        async def authorize():
            nonlocal calls
            calls += 1
            return calls < 2

        with pytest.raises(PermissionError):
            await relay_supervisor(websocket, redis, authorize, heartbeat=0.01)
        assert calls == 2
        pubsub.aclose.assert_awaited_once()
        assert websocket.send_json.await_args_list[0].args[0]["kind"] == "resync_required"

    asyncio.run(scenario())


def test_supervisor_relay_ignores_invalid_payload_and_delivers_valid_envelope():
    async def scenario():
        ready = event()
        messages = iter(
            [
                {"type": "message", "data": '{"phone":"sensitive"}'},
                {"type": "message", "data": ready.model_dump_json()},
            ]
        )

        async def get_message(**kw):
            try:
                return next(messages)
            except StopIteration:
                await asyncio.sleep(10)

        pubsub = type(
            "PubSub",
            (),
            {"subscribe": AsyncMock(), "aclose": AsyncMock(), "get_message": get_message},
        )()
        # Bind through an instance attribute so the fake matches redis-py's keyword API.
        pubsub.get_message = get_message
        redis = type("Redis", (), {"pubsub": lambda self: pubsub})()
        delivered = asyncio.Event()

        async def send_json(body):
            if body.get("kind") == "call_update":
                assert body["call_id"] == str(ready.call_id)
                delivered.set()

        websocket = type("Socket", (), {})()
        websocket.send_json = send_json
        task = asyncio.create_task(
            relay_supervisor(websocket, redis, AsyncMock(return_value=True), heartbeat=0.02)
        )
        await asyncio.wait_for(delivered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pubsub.aclose.assert_awaited_once()

    asyncio.run(scenario())


def test_supervisor_relay_closes_instead_of_buffering_a_slow_client():
    async def scenario():
        raw = event().model_dump_json()
        pubsub = type("PubSub", (), {"subscribe": AsyncMock(), "aclose": AsyncMock()})()
        pubsub.get_message = AsyncMock(return_value={"type": "message", "data": raw})
        redis = type("Redis", (), {"pubsub": lambda self: pubsub})()
        websocket = type("Socket", (), {"send_json": AsyncMock()})()
        with pytest.raises(BufferError):
            await relay_supervisor(websocket, redis, AsyncMock(return_value=True), capacity=1)
        pubsub.aclose.assert_awaited_once()

    asyncio.run(scenario())


def test_supervisor_initial_authorization_is_bounded():
    async def run():
        async def stalled():
            await asyncio.sleep(30)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(relay_supervisor(None, None, stalled), 3)

    asyncio.run(run())
