"""Call-owned turn invalidation, kept outside provider payloads and Redis."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class TurnEpoch:
    def __init__(self):
        self._current = 0

    @property
    def current(self):
        return self._current

    def snapshot(self):
        return self._current

    def advance(self):
        self._current += 1
        return self._current

    def is_current(self, epoch):
        return type(epoch) is int and epoch == self._current

    def require_current(self, epoch):
        if not self.is_current(epoch):
            raise asyncio.CancelledError("Stale turn")


@dataclass(frozen=True)
class _EpochScope:
    guard: TurnEpoch
    epoch: int


_scope: ContextVar[_EpochScope | None] = ContextVar("roma_turn_epoch", default=None)


def current_turn_epoch():
    scope = _scope.get()
    return scope.epoch if scope is not None else None


def require_current_epoch():
    scope = _scope.get()
    if scope is not None:
        scope.guard.require_current(scope.epoch)


@contextmanager
def epoch_scope(guard, epoch):
    guard.require_current(epoch)
    token = _scope.set(_EpochScope(guard, epoch))
    try:
        yield
    finally:
        _scope.reset(token)
