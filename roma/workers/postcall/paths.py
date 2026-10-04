"""Where post-call artifacts live on disk, and the permissions they live under (docs/07).

One root (`Settings.roma_data_dir`) with a FIXED subdirectory layout:

    {root}/media/{date}/{call_sid}.s16le    raw capture, written during the call
    {root}/spool/postcall/*.json            job spool — enqueue-failure fallback
    {root}/recordings/{date}/{call_sid}.wav the stored recording + .json sidecar

The layout is constants, not settings: docs/07 requires recordings to be
access-controlled and not world-readable, and that is far easier to guarantee for one
configurable root with a known shape than for five independently-configurable paths.
The date partition is not cosmetic either — it is what lets retention delete a whole
day's directory instead of stat-walking every file (see `store.prune`).
"""

from pathlib import Path

from roma.core.private_files import (
    DIR_MODE,
    FILE_MODE,
    ensure_private_dir,
    open_private,
)

MEDIA_SUBDIR = "media"
JOB_SPOOL_SUBDIR = "spool/postcall"
RECORDINGS_SUBDIR = "recordings"
SPEND_LEDGER_FILE = "spend.jsonl"


def data_root(settings) -> Path:
    """The single configured root for everything post-call writes."""
    return Path(settings.roma_data_dir)


def media_dir(settings) -> Path:
    """Raw call capture, written live by the recorder."""
    return data_root(settings) / MEDIA_SUBDIR


def job_spool_dir(settings) -> Path:
    """Job spool — where a job goes when the queue push fails."""
    return data_root(settings) / JOB_SPOOL_SUBDIR


def recordings_dir(settings) -> Path:
    """The durable store the worker writes into."""
    return data_root(settings) / RECORDINGS_SUBDIR


def spend_ledger_path(settings) -> Path:
    """Cumulative OpenAI spend for the testing phase — the file that makes the ₹100 cap
    survive a `serve_media.py` restart."""
    return data_root(settings) / SPEND_LEDGER_FILE


__all__ = [
    "data_root",
    "media_dir",
    "job_spool_dir",
    "recordings_dir",
    "spend_ledger_path",
    "ensure_private_dir",
    "open_private",
    "MEDIA_SUBDIR",
    "JOB_SPOOL_SUBDIR",
    "RECORDINGS_SUBDIR",
    "DIR_MODE",
    "FILE_MODE",
]
