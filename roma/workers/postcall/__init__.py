"""Post-call work: the recording queue, the spool, and the worker (docs/09, docs/10 Step 6).

This package is deliberately **pipecat-free**. The worker runs as its own process and must
start without dragging in pipecat -> silero -> torch, so nothing here may import from
`roma.telephony`. It is the same layering the repo already uses for `roma.controller`:
pure logic here, and a thin pipecat adapter (`roma.telephony.recorder`) on the other side.

Flow (docs/09):

    call ends -> pipeline pushes {call_sid, recording_ref, ...} to queue:postcall
              -> worker converts the raw capture, stores it, writes a metadata sidecar
              -> ONLY THEN acks the job

The job is acked only after the recording is durably written, so a worker killed mid-job
loses nothing. If Redis is unreachable when the call ends, the job is spooled to a local
file instead and the worker drains it — teardown never fails and a recording reference is
never silently dropped.
"""

from roma.postcall.job import PostcallJob, outcome_for
from roma.postcall.paths import (
    ensure_private_dir,
    job_spool_dir,
    media_dir,
    open_private,
    recordings_dir,
    spend_ledger_path,
)

__all__ = [
    "PostcallJob",
    "outcome_for",
    "media_dir",
    "job_spool_dir",
    "recordings_dir",
    "spend_ledger_path",
    "ensure_private_dir",
    "open_private",
]
