"""Resolve only caller-independent relative runtime references, never symlink escapes."""

import os
import re
import stat
from pathlib import Path, PurePosixPath

from roma.core.field_encryption import PrivacyError


def safe_runtime_path(root, reference):
    if (
        not isinstance(reference, str)
        or not reference
        or "\\" in reference
        or any(part in {"", ".", ".."} for part in reference.split("/"))
    ):
        raise PrivacyError("Invalid runtime reference.")
    relative = PurePosixPath(reference)
    if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        raise PrivacyError("Invalid runtime reference.")
    root = Path(root).absolute()
    if root.is_symlink():
        raise PrivacyError("Symlink runtime root is not allowed.")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise PrivacyError("Symlink runtime references are not allowed.")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise PrivacyError("Invalid runtime reference.")
    return candidate


def safe_call_sid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise PrivacyError("Invalid call identifier.")
    return value


def read_private_recording(path, *, root=None, max_bytes=20_000_000):
    descriptors = []
    try:
        if root is None:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        else:
            root = Path(root).absolute()
            parts = Path(path).relative_to(root).parts
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(directory)
            for part in parts[:-1]:
                directory = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                descriptors.append(directory)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
                raise PrivacyError("Recording is unavailable.")
            value = stream.read(max_bytes + 1)
            if len(value) > max_bytes:
                raise PrivacyError("Recording is unavailable.")
            return value
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
