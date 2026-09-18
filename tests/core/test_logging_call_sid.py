"""Every log line carries the call it belongs to (roma.core.logging).

Until this existed, exactly two lines in the system named a call: the stream-start line and
the spend-ledger row. Everything from `roma.domain.conversation`, `roma.domain.safety`, `pretts`,
`silence`, `opening` and `phase_controller` logged through module-level loggers with no call
identity at all — readable only because every diagnosis so far has been done on a log with
exactly ONE call in it.
"""

import asyncio
import logging

from roma.core.logging import CallSidFilter, current_call_sid


def _record():
    return logging.LogRecord("roma.domain.conversation", logging.INFO, __file__, 1, "hi", None, None)


def test_a_record_inside_a_call_is_stamped():
    token = current_call_sid.set("CA00417672674d8c789f1a36872ff27bbb")
    try:
        rec = _record()
        CallSidFilter().filter(rec)
        assert rec.call_sid == "CA00417672674d8c789f1a36872ff27bbb"
    finally:
        current_call_sid.reset(token)


def test_a_record_outside_a_call_still_formats():
    """The format string names `call_sid` unconditionally, so a record that never saw a call
    must still carry the attribute — otherwise startup logging raises inside the handler."""
    rec = _record()
    CallSidFilter().filter(rec)
    assert rec.call_sid == "-"
    fmt = logging.Formatter("%(name)s [%(call_sid)s] %(message)s")
    assert fmt.format(rec) == "roma.domain.conversation [-] hi"


def test_the_sid_does_not_leak_between_concurrent_calls():
    """The point of the whole change. Two calls run as two asyncio tasks on one loop; a
    module-level global would give both the last SID set."""

    async def call(sid, seen):
        current_call_sid.set(sid)
        await asyncio.sleep(0.01)
        rec = _record()
        CallSidFilter().filter(rec)
        seen.append(rec.call_sid)

    async def run():
        seen = []
        await asyncio.gather(call("CA_one", seen), call("CA_two", seen))
        return seen

    assert sorted(asyncio.run(run())) == ["CA_one", "CA_two"]


def test_tasks_inherit_the_calling_context():
    """The pipeline's per-call processors run in tasks created inside the call, so they must
    pick the SID up without being told."""

    async def run():
        current_call_sid.set("CA_parent")

        async def child():
            rec = _record()
            CallSidFilter().filter(rec)
            return rec.call_sid

        return await asyncio.create_task(child())

    assert asyncio.run(run()) == "CA_parent"
