"""Redis dies DURING the flow — every other failure test breaks it before the flow starts.

The 2026-08-08 audit inventoried all twelve Redis touchpoints and found the suite's failure
tests all substitute a permanently-broken stub up front. That proves each guard exists; it
never proves a call SURVIVES the transition from working to broken — which is what a real
outage is. `_FlipClient` below starts as a working fakeredis and dies on `kill()`.

The audit also found the two writes with no guard at all, both fixed and pinned here:

  * `CallStatusStore.put_dialing` surfaced a Redis error as a 500 AFTER the phone was
    already ringing (`webapi._put_dialing_guarded` now swallows it — the record is not
    the call).
  * `RedisLeadStore.put` failing aborted the dial (correctly — Roma must not answer a call
    knowing nothing) but surfaced as `502 carrier refused (ConnectionError)`, sending the
    operator to the Vobiz dashboard for a Redis outage. Now a typed `DialPrereqError` → 503
    naming the store.
"""

import asyncio
import tempfile
from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


class _FlipClient:
    """Delegates to a live fakeredis until `kill()`, then raises like a dropped socket."""

    def __init__(self, decode_responses=True):
        self._real = fakeredis.aioredis.FakeRedis(decode_responses=decode_responses)
        self._dead = False

    def kill(self):
        self._dead = True

    def __getattr__(self, name):
        real_attr = getattr(self._real, name)

        async def _maybe(*args, **kwargs):
            if self._dead:
                raise ConnectionError("redis went away mid-call")
            return await real_attr(*args, **kwargs)

        return _maybe


# --- the conversation survives its checkpoint store dying ---------------------------------


def test_a_checkpoint_failure_mid_call_does_not_end_the_turn():
    """`advance_turn` checkpoints on durable events; the checkpoint is best-effort, the
    turn is not (`turn.py`). Turn one saves for real; then Redis dies and turn two must
    still classify, transition and return — a lead mid-objection cannot be dropped because
    a checkpoint write failed."""
    from roma.controller.state import CallState
    from roma.controller.store import RedisCallStateStore
    from roma.controller.turn import advance_turn

    client = _FlipClient()
    store = RedisCallStateStore(client=client)
    state = CallState(call_sid="CA_flip", phase="p5_pivot")

    t1 = asyncio.run(
        advance_turn(state, "ye to bahut mehenga hai", client=None, now=NOW, store=store)
    )
    assert t1.next_phase == "p6_objection"
    assert asyncio.run(store.load("CA_flip")) is not None, "turn one must have checkpointed"

    client.kill()
    t2 = asyncio.run(
        advance_turn(state, "phir bhi mehenga lagta hai", client=None, now=NOW, store=store)
    )
    assert t2.next_phase == "p5_pivot" and t2.hard_pivot is True, (
        "the machine must keep advancing on a dead checkpoint store"
    )
    assert state.turn_count == 2


def test_a_lead_store_that_dies_after_the_dial_degrades_to_no_lead():
    """The record was written at dial time; Redis dies before pickup. The call must still
    connect — Roma simply knows nothing about the person, the pre-token inbound behaviour."""
    from roma.dialer.leadstore import OutboundLead, RedisLeadStore
    from roma.telephony.media import _load_triggered_lead

    client = _FlipClient()
    store = RedisLeadStore(client=client)
    asyncio.run(store.put("tok-1", OutboundLead(phone="+919876543210", branch="Vadodara")))
    app = SimpleNamespace(state=SimpleNamespace(lead_store=store))
    assert asyncio.run(_load_triggered_lead(app, "tok-1")) is not None

    client.kill()
    assert asyncio.run(_load_triggered_lead(app, "tok-1")) is None, (
        "a dead store must degrade to 'no lead record', never raise into the call"
    )


# --- the two writes the audit found unguarded ---------------------------------------------


def test_the_status_write_guard_swallows_a_dead_store():
    """`put_dialing` runs when the phone is ALREADY ringing; a Redis error there must not
    become a 500 for a dial that actually fired."""
    from roma.dialer.callstatus import CallStatusStore
    from roma.telephony.webapi import _put_dialing_guarded

    client = _FlipClient()
    client.kill()
    store = CallStatusStore(client=client, ttl=60)
    asyncio.run(_put_dialing_guarded(store, "uuid-1", "tok-1", "+919876543210"))


def test_a_dead_lead_store_fails_the_dial_closed_before_the_carrier_is_touched():
    """Fail-closed AND in order: the lead record write precedes the Vobiz POST, so when it
    fails the carrier must never have been asked to do anything."""
    from roma.dialer.leadstore import OutboundLead, RedisLeadStore
    from roma.dialer.trigger import DialPrereqError, trigger_outbound_call

    client = _FlipClient()
    client.kill()
    fired = []

    class _WatchingVobiz:
        def create_call(self, **kw):
            fired.append(kw)
            return {"request_uuid": "u-1"}

    with pytest.raises(DialPrereqError):
        asyncio.run(
            trigger_outbound_call(
                OutboundLead(phone="+919876543210", branch="Vadodara"),
                store=RedisLeadStore(client=client),
                client=_WatchingVobiz(),
                from_number="+917971543192",
                base_url="https://example.test",
            )
        )
    assert fired == [], "the dial must be refused before the carrier is touched"


def test_a_redis_outage_surfaces_as_503_not_carrier_refused(monkeypatch):
    """End-to-end through the endpoint: Redis unreachable at dial time is OUR outage, and
    the response must say so — not blame Vobiz. `REDIS_URL` points at a closed port and the
    Vobiz credentials are present, so the first thing to fail is the lead-record write."""
    from roma.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    # Same wall-clock pin as `test_api_call._app`: the TRAI window would refuse every dial
    # outside 09:00-21:00 IST before the Redis failure under test is ever reached.
    monkeypatch.setattr("roma.dialer.precall.in_calling_window", lambda now: True)
    env = {
        "VOBIZ_FROM_NUMBER": "+917971543192",
        "VOBIZ_AUTH_ID": "MA_TEST",
        "VOBIZ_AUTH_TOKEN": "tok_test",
        "SARVAM_API_KEY": "sk-test",
        "OPENAI_API_KEY": "sk-test",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "PUBLIC_BASE_URL": "https://example.test",
        "WEB_ORIGIN": "http://localhost:3030",
        "ROMA_DATA_DIR": tempfile.mkdtemp(prefix="roma-midcall-"),
        "OPENAI_BUDGET_INR": "1000000",
        "API_TOKEN": "test-token-abc",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        from roma.telephony.webapi import mount_web_api

        async def _reachable_ok(base_url, **kw):
            return True, ""

        app = FastAPI()
        mount_web_api(app, reachable_fn=_reachable_ok)
        res = TestClient(app).post(
            "/api/call",
            json={"to_number": "+919876543210"},
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert res.status_code == 503, res.text
        reason = res.json()["detail"]["reason"]
        assert "redis" in reason.lower(), (
            f"the operator must be pointed at the store, not the carrier: {reason!r}"
        )
    finally:
        get_settings.cache_clear()


# --- the worker outlives an outage --------------------------------------------------------


def test_the_worker_survives_a_queue_outage(monkeypatch, tmp_path):
    """A Redis blip used to propagate out of `run_worker` and kill the whole process —
    stranding every job behind the failure. It now backs off and keeps polling."""
    from roma.postcall import worker as worker_mod
    from roma.postcall.store import LocalRecordingStore
    from roma.postcall.worker import WorkerDeps, run_worker

    monkeypatch.setattr(worker_mod, "QUEUE_ERROR_BACKOFF_SECS", 0.0)

    class _FlakyQueue:
        def __init__(self, failures):
            self.failures = failures

        async def recover_inflight(self):
            return 0

        async def reserve(self, timeout_secs):
            if self.failures:
                self.failures -= 1
                raise ConnectionError("redis went away")
            return None

    deps = WorkerDeps(
        queue=_FlakyQueue(failures=2),
        store=LocalRecordingStore(tmp_path / "recordings"),
        media_root=tmp_path / "media",
        consent_ok=lambda: True,
        retention_days=90,
        now_fn=lambda: NOW,
    )
    stats = asyncio.run(run_worker(deps, drain=True))
    assert stats.queue_errors == 2, "both outage polls must be survived and counted"
    assert stats.processed == 0
