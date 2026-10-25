"""Owner-only, single-writer durable intent for bounded provider erasure."""

import fcntl
import json
import os
import stat
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from typing import TypedDict, cast
from uuid import uuid4

STATES = {
    "pending",
    "attempting",
    "deleted",
    "absent",
    "transient_pending",
    "authentication_manual",
    "provider_manual",
}


def resource_key(kind: str, sid: str) -> str:
    return sha256((kind + ":" + sid).encode()).hexdigest()


class LedgerData(TypedDict):
    version: int
    manifest_digest: str
    account_digest: str
    verified: bool
    steps: dict[str, str]


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ErasureLedger:
    def __init__(self, path: Path, *, digest: str, account: str, keys: list[str]):
        self.path = Path(path).absolute()
        self.expected: LedgerData = {
            "version": 1,
            "manifest_digest": digest,
            "account_digest": sha256(account.encode()).hexdigest(),
            "verified": True,
            "steps": dict.fromkeys(keys, "pending"),
        }
        self.data: LedgerData = self.expected.copy()
        self.fd: int | None = None

    @staticmethod
    def _check_file(fd: int) -> None:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise OSError("Private ledger file required.")

    def __enter__(self) -> "ErasureLedger":
        missing = []
        for directory in reversed([self.path.parent, *self.path.parent.parents]):
            if directory.is_symlink():
                raise OSError("Symlink ledger directories are not allowed.")
            if not directory.exists():
                missing.append(directory)
        for directory in missing:
            directory.mkdir(mode=0o700)
            _sync_directory(directory.parent)
        info = self.path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("Owner-only ledger directory required.")
        self.fd = os.open(
            self.path.with_name(self.path.name + ".lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        try:
            self._check_file(self.fd)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._load()
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def _load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            self.existing = False
            return
        try:
            self._check_file(fd)
            if os.fstat(fd).st_size > 65536:
                raise ValueError("Invalid bounded ledger.")
            with os.fdopen(fd, closefd=False) as stream:
                data = json.loads(stream.read(65537))
        finally:
            os.close(fd)
        if (
            not isinstance(data, dict)
            or set(data) != set(self.expected)
            or data["version"] != 1
            or data["manifest_digest"] != self.expected["manifest_digest"]
            or data["account_digest"] != self.expected["account_digest"]
            or type(data["version"]) is not int
            or data["verified"] is not True
            or not isinstance(data["steps"], dict)
            or set(data["steps"]) != set(self.expected["steps"])
            or any(value not in STATES for value in data["steps"].values())
        ):
            raise ValueError("Ledger does not match this account and manifest.")
        self.data, self.existing = cast(LedgerData, data), True

    def save(self) -> None:
        """Atomic fsync before effects; failure leaves the previous durable intent."""
        temporary = self.path.with_name("." + self.path.name + "." + uuid4().hex)
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            _sync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
