"""Step 6 wiring: `finalize_call` and the recorder's place in the pipeline (docs/09).

Follows test_media_isolation.py's rule — the full `/ws` handler cannot be driven through
Starlette's TestClient (it deadlocks), so this exercises the EXTRACTED function. That is
precisely why teardown was lifted out of the handler's closure: inside it, none of the
guarantees below could be tested at all.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from roma.controller.state import CallState
from roma.controller.store import InMemoryCallStateStore
from roma.postcall.job import OUTCOME_LOCKED, OUTCOME_NO_LOCK, PostcallJob
from roma.postcall.spool import JobSpool, SpoolFallbackQueue
from roma.telephony.media import CallHandles, finalize_call
from roma.telephony.recorder import CallRecorder

NOW = datetime(2026, 7, 26, 10, 6, tzinfo=UTC)
STARTED = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)


class _Queue:
    def __init__(self, fail=False):
        self.pushed = []
        self.fail = fail
        self.closed = False

    async def push(self, job):
        if self.fail:
            raise ConnectionError("redis down")
        self.pushed.append(job)
        return "queued"

    async def aclose(self):
        self.closed = True


def _recorder(tmp_path, *, audio=b"\x01\x02\x03\x04" * 8000):
    r = CallRecorder(tmp_path / "media" / "2026-07-26" / "CA_fin.s16le", started_at=STARTED)
    if audio:
        r.append(audio)
    return r


def _state(locked=None):
    s = CallState(call_sid="CA_fin", phase="p7_close")
    s.locked_slot = locked
    return s


def _finalize(**kw):
    kw.setdefault("now", NOW)
    return asyncio.run(finalize_call(**kw))


def test_finalize_enqueues_a_job_describing_the_recording(tmp_path):
    q = _Queue()
    r = _recorder(tmp_path)
    status = _finalize(
        state=_state(locked="2026-07-27T15:00:00+05:30"),
        store=InMemoryCallStateStore(),
        recorder=r,
        queue=q,
        media_root=tmp_path / "media",
    )

    assert status == "queued"
    (job,) = q.pushed
    assert job.call_sid == "CA_fin"
    assert job.recording_ref == "2026-07-26/CA_fin.s16le"
    assert job.outcome == OUTCOME_LOCKED
    assert job.duration_secs == pytest.approx(360.0)
    assert job.audio_secs == pytest.approx(1.0)
    assert job.num_channels == 2


def test_a_call_that_did_not_lock_is_recorded_as_no_lock(tmp_path):
    """CLAUDE.md: a soft "dekhta hoon" is NOT a win, and the metadata must say so."""
    q = _Queue()
    _finalize(
        state=_state(locked=None),
        store=InMemoryCallStateStore(),
        recorder=_recorder(tmp_path),
        queue=q,
        media_root=tmp_path / "media",
    )
    assert q.pushed[0].outcome == OUTCOME_NO_LOCK


def test_the_recorder_is_closed_before_the_job_is_enqueued(tmp_path):
    """Otherwise the job points at a file whose tail is still in the buffer, and the worker
    can convert a truncated recording."""
    closed_at_push = {}
    r = _recorder(tmp_path)

    class _Q(_Queue):
        async def push(self, job):
            closed_at_push["closed"] = r._closed
            return await super().push(job)

    _finalize(
        state=_state(),
        store=InMemoryCallStateStore(),
        recorder=r,
        queue=_Q(),
        media_root=tmp_path / "media",
    )
    assert closed_at_push["closed"] is True


def test_finalize_never_raises_when_the_queue_is_down(tmp_path):
    """This runs in a `finally:` after the lead has already hung up. An exception here
    would mask the real teardown and serve nobody."""
    status = _finalize(
        state=_state(),
        store=InMemoryCallStateStore(),
        recorder=_recorder(tmp_path),
        queue=_Queue(fail=True),
        media_root=tmp_path / "media",
    )
    assert status == "error"


def test_a_down_queue_still_spools_the_job(tmp_path):
    """The production wiring: `SpoolFallbackQueue` in front of Redis. Redis is not running
    on this box, so THIS is the path that actually executes today."""
    spool = JobSpool(tmp_path / "spool")
    status = _finalize(
        state=_state(),
        store=InMemoryCallStateStore(),
        recorder=_recorder(tmp_path),
        queue=SpoolFallbackQueue(_Queue(fail=True), spool),
        media_root=tmp_path / "media",
    )

    assert status == "spooled"
    (path,) = spool.pending()
    assert PostcallJob.from_raw(path.read_text()).call_sid == "CA_fin"


def test_finalize_never_raises_when_the_state_store_is_down(tmp_path):
    class _BadStore:
        async def save(self, state):
            raise ConnectionError("redis down")

    status = _finalize(
        state=_state(),
        store=_BadStore(),
        recorder=_recorder(tmp_path),
        queue=_Queue(),
        media_root=tmp_path / "media",
    )
    assert status == "queued"


def test_finalize_still_saves_state_when_recording_is_off():
    """The flag removes the feature, it does not change the rest of teardown."""
    store = InMemoryCallStateStore()
    state = _state()
    status = _finalize(state=state, store=store, recorder=None, queue=None)

    assert status == "skipped"
    assert asyncio.run(store.load("CA_fin")) is not None


def test_no_job_is_enqueued_when_nothing_was_captured(tmp_path):
    """A call that captured no audio (an immediate hangup, or a failed recorder) must not
    hand the worker a job whose file does not exist."""
    q = _Queue()
    status = _finalize(
        state=_state(),
        store=InMemoryCallStateStore(),
        recorder=_recorder(tmp_path, audio=b""),
        queue=q,
        media_root=tmp_path / "media",
    )
    assert status == "skipped"
    assert q.pushed == []


def test_no_job_is_enqueued_when_the_recorder_failed(tmp_path):
    """A recorder that hit a disk error wrote nothing, so there is nothing to store."""
    r = _recorder(tmp_path, audio=b"")
    r.failed = True
    q = _Queue()
    _finalize(
        state=_state(),
        store=InMemoryCallStateStore(),
        recorder=r,
        queue=q,
        media_root=tmp_path / "media",
    )
    assert q.pushed == []


def test_both_clients_are_closed(tmp_path):
    """The queue holds its OWN Redis client precisely so this does not depend on the order
    of two `aclose()` calls inside a `finally:` block."""
    q = _Queue()

    class _Store(InMemoryCallStateStore):
        closed = False

        async def aclose(self):
            _Store.closed = True

    _finalize(
        state=_state(),
        store=_Store(),
        recorder=_recorder(tmp_path),
        queue=q,
        media_root=tmp_path / "media",
    )
    assert q.closed and _Store.closed


def test_call_handles_carries_the_recorder():
    """Per-call, keyed by stream SID (docs/08) — never a process-global slot that
    concurrent calls would clobber."""
    h = CallHandles(counter=1, transcript=2, pretts=3, phase_ctrl=4, recorder="rec")
    assert h.recorder == "rec"
    assert CallHandles(counter=1, transcript=2, pretts=3, phase_ctrl=4).recorder is None


def test_the_capture_processor_sits_after_the_output_transport():
    """The load-bearing placement decision (see telephony/recorder.py), and nothing else
    guards it. Before `transport.output()` the recording would contain TTS that was
    interrupted and never played, with Roma's timeline compressed — useless as a
    compliance artifact, and wrong in a way no other test would catch."""
    import inspect

    from roma.telephony import media

    src = inspect.getsource(media)
    out_idx = src.index("transport.output(),")
    capture_idx = src.index("*([capture] if capture is not None else []),")
    assistant_idx = src.index("aggregators.assistant(),")
    assert out_idx < capture_idx < assistant_idx


def test_recording_can_be_switched_off_entirely():
    from roma.config import Settings

    assert Settings.model_fields["recording_enabled"].default is True
    assert Settings.model_fields["recording_retention_days"].default == 90
    assert Settings.model_fields["roma_data_dir"].default == "var/roma"
