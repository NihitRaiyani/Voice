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

import os
from pathlib import Path

MEDIA_SUBDIR = "media"
JOB_SPOOL_SUBDIR = "spool/postcall"
RECORDINGS_SUBDIR = "recordings"
SPEND_LEDGER_FILE = "spend.jsonl"

DIR_MODE = 0o700
FILE_MODE = 0o600


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


def ensure_private_dir(path: Path) -> Path:
    """Create `path` (and parents) and force it owner-only. Returns the path.

    The `os.chmod` is NOT redundant with `mkdir(mode=...)`, and this is the single most
    common way PII ends up world-readable: the `mode` argument to `mkdir` is masked by the
    process umask, so under a typical `umask 022` a `mkdir(mode=0o700)` yields 0o700 but
    `mkdir(mode=0o770)` silently yields 0o750 — and any parent created along the way gets
    the default mode regardless. `chmod` is applied after the fact and is not masked, so
    it is the only thing that actually guarantees the permission.

    Parents are chmod'ed too: a 0o700 leaf under a 0o755 `var/roma` still leaks the
    directory listing (call SIDs and dates) to every local user.
    """
    path = Path(path)
    created: list[Path] = []
    probe = path
    while not probe.exists():
        created.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent

    path.mkdir(parents=True, exist_ok=True)
    for d in created:
        os.chmod(d, DIR_MODE)
    os.chmod(path, DIR_MODE)
    return path


def open_private(path: Path, mode: str = "wb"):
    """Open `path` for writing and force it owner-only, creating its parent if needed.

    Same umask caveat as `ensure_private_dir`: `os.open(..., 0o600)` is masked, so the
    mode is set explicitly after the file exists. Chmod-after-create leaves a moment where
    the file is umask-default, which is acceptable here because the containing directory is
    already 0o700 — nobody else can traverse into it to see the file at all.
    """
    path = Path(path)
    ensure_private_dir(path.parent)
    fh = open(path, mode)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:  # pragma: no cover - platform without chmod support
        fh.close()
        raise
    return fh


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
