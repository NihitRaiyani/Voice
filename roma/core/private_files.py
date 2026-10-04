"""Owner-only filesystem helpers for Roma runtime data.

Spend ledgers, recordings and spool files need the same privacy guarantees, but
their modules should not import each other just to chmod directories correctly.
"""

import os
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` and parents, then force owner-only permissions."""
    path = Path(path)
    created: list[Path] = []
    probe = path
    while not probe.exists():
        created.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent

    path.mkdir(parents=True, exist_ok=True)
    for directory in created:
        os.chmod(directory, DIR_MODE)
    os.chmod(path, DIR_MODE)
    return path


def open_private(path: Path, mode: str = "wb"):
    """Open ``path`` for writing and force owner-only file permissions."""
    path = Path(path)
    ensure_private_dir(path.parent)
    handle = open(path, mode)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:  # pragma: no cover - platform without chmod support
        handle.close()
        raise
    return handle


__all__ = ["DIR_MODE", "FILE_MODE", "ensure_private_dir", "open_private"]
