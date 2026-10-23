"""Supervised durable milestones with a TTL-bounded Redis projection.

The teaching writer is awaited, not a detached task: one immutable pending snapshot
per call, serialized retries, no database session during inference or audio.
"""

import asyncio
import math
from enum import StrEnum
from time import monotonic

from sqlalchemy.exc import OperationalError

from roma.core.logging import LogEvent, log_context, log_event
from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.domain.conversation.state import CallState
from roma.domain.persistence import PersistenceUnavailable


class CheckpointMilestone(StrEnum):
    CALL_STARTED = 'call_started'
    STATE_CHANGED = 'state_changed'
    TURN_COMPLETED = 'turn_completed'
    STAGE_CHANGED = 'stage_changed'
    SAFETY_TRIGGERED = 'safety_triggered'
    BOOKING_CONFIRMED = 'booking_confirmed'
    CALL_ENDED = 'call_ended'


async def checkpoint_milestone(store, state, milestone):
    """Optional lifecycle hook; existing store callers keep their old save contract."""
    checkpoint = getattr(store, 'checkpoint', None)
    if checkpoint is not None:
        await checkpoint(state, milestone)


class LiveCallStateStore:
    def __init__(self, hot, durable, *, lease_factory=None, timeout=1.0, retry_delay=0.05, clock=monotonic):
        if not math.isfinite(timeout) or not 0 < timeout <= 1.5:
            raise ValueError('Checkpoint timeout must be 0..1.5 seconds')
        if not math.isfinite(retry_delay) or not 0 <= retry_delay <= 0.25:
            raise ValueError('Checkpoint retry delay must be 0..0.25 seconds')
        self.hot, self.durable = hot, durable
        self.lease_factory, self.lease = lease_factory, None
        self._lease_acquired = False
        self.timeout, self.retry_delay, self.clock = timeout, retry_delay, clock
        self._sid = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._permanent_failure = False
        self._pending = None
        self._pending_at = None
        self._previous = None
        self._counts = dict(checkpoints=0, failures=0, retries=0, redis_failures=0, recoveries=0)
        self.milestones = {milestone: 0 for milestone in CheckpointMilestone}

    def metrics(self):
        return {**self._counts, 'pending': int(self._pending is not None),
                'lag_ms': max(0, (self.clock() - self._pending_at) * 1000) if self._pending_at is not None else 0}

    async def _claim(self, sid):
        if self._closed or not isinstance(sid, str) or not 0 < len(sid) <= 128:
            raise VoiceFailure(VoiceErrorCode.DB)
        if self._sid is not None and self._sid != sid:
            raise ValueError('One live state store belongs to one call')
        self._sid = sid
        if self.lease_factory is None:
            return  # Synthetic stores may omit distributed ownership.
        try:
            async with asyncio.timeout(self.timeout):
                if self.lease is None:
                    self.lease = self.lease_factory(sid)
                if not self._lease_acquired:
                    # A lost SET reply may still have acquired our token. Never
                    # reacquire after a previously established lease expires.
                    self._lease_acquired = await self.lease.owned() or await self.lease.acquire()
                    if not self._lease_acquired:
                        raise VoiceFailure(VoiceErrorCode.DB)
                if not await self.lease.owned():
                    raise VoiceFailure(VoiceErrorCode.DB)
        except Exception:  # noqa: BLE001 -- ownership failure is PII-safe and fail-closed.
            raise VoiceFailure(VoiceErrorCode.DB) from None

    async def _cache(self, state):
        try:
            async with asyncio.timeout(self.timeout):
                await self.hot.save(state)
        except Exception:  # noqa: BLE001 -- SQL already owns the durable milestone.
            self._counts['redis_failures'] += 1
            log_event(LogEvent.STATE_CACHE_FAILED, error_code=VoiceErrorCode.DB.value)

    async def load(self, sid):
        async with self._lock:
            await self._claim(sid)
            if self._pending is not None:
                await self._flush()
            try:
                async with asyncio.timeout(self.timeout):
                    hot = await self.hot.load(sid)
            except Exception:  # noqa: BLE001 -- expiry/corruption/outage is a cache miss.
                hot = None
                self._counts['redis_failures'] += 1
            try:
                async with asyncio.timeout(self.timeout):
                    durable = await self.durable.load(sid)
            except Exception:  # noqa: BLE001 -- never start fresh on unreadable SQL.
                log_event(LogEvent.CHECKPOINT_FAILED, error_code=VoiceErrorCode.DB.value)
                raise VoiceFailure(VoiceErrorCode.DB) from None
            # Restore is infrequent. SQL wins even when an old Redis key survived a
            # failed refresh; turn numbers alone cannot detect same-turn slot changes.
            if durable is not None:
                if hot is None or hot.to_dict() != durable.to_dict():
                    self._counts['recoveries'] += 1
                    await self._cache(durable)
                self._previous = durable.to_dict()
                return durable
            return None

    async def save(self, state):
        await self.checkpoint(state, CheckpointMilestone.STATE_CHANGED)

    async def checkpoint(self, state, milestone):
        milestone = CheckpointMilestone(milestone)
        snapshot = CallState.from_dict(state.to_dict())
        async with self._lock:
            await self._claim(snapshot.call_sid)
            if self._pending is not None:
                await self._flush()  # Older retry cannot overwrite a newer accepted state.
            self._pending = (snapshot, milestone)
            self._pending_at = self.clock()
            await self._flush()

    async def _flush(self):
        if self._pending is None:
            return
        if self._permanent_failure:
            raise VoiceFailure(VoiceErrorCode.DB)
        snapshot, milestone = self._pending
        with log_context(call_id=snapshot.call_sid, turn_id=max(1, snapshot.turn_count), stage=snapshot.stage.value):
            for attempt in range(2):
                try:
                    await self._claim(snapshot.call_sid)
                    async with asyncio.timeout(self.timeout):
                        await self.durable.save(snapshot)
                    break
                except (OSError, TimeoutError, OperationalError, PersistenceUnavailable):
                    self._counts['failures'] += 1
                    log_event(LogEvent.CHECKPOINT_FAILED, error_code=VoiceErrorCode.DB.value,
                              checkpoint_metrics=self.metrics())
                    if attempt == 1:
                        raise VoiceFailure(VoiceErrorCode.DB) from None
                    self._counts['retries'] += 1
                    await asyncio.sleep(self.retry_delay)
                except VoiceFailure:
                    # A temporary ownership read error must not poison a frozen
                    # snapshot. The next attempt still has to prove the same lease.
                    self._counts['failures'] += 1
                    log_event(LogEvent.CHECKPOINT_FAILED, error_code=VoiceErrorCode.DB.value,
                              checkpoint_metrics=self.metrics())
                    raise
                except Exception:  # noqa: BLE001 -- validation/conflict is not a retryable write.
                    self._permanent_failure = True
                    self._counts['failures'] += 1
                    log_event(LogEvent.CHECKPOINT_FAILED, error_code=VoiceErrorCode.DB.value,
                              checkpoint_metrics=self.metrics())
                    raise VoiceFailure(VoiceErrorCode.DB) from None
            self._counts['checkpoints'] += 1
            self.milestones[milestone] += 1
            current = snapshot.to_dict()
            if self._previous is not None:
                if self._previous['stage'] != current['stage'] and milestone != CheckpointMilestone.STAGE_CHANGED:
                    self.milestones[CheckpointMilestone.STAGE_CHANGED] += 1
                if not self._previous.get('appointment_id') and current.get('appointment_id') and milestone != CheckpointMilestone.BOOKING_CONFIRMED:
                    self.milestones[CheckpointMilestone.BOOKING_CONFIRMED] += 1
            self._previous = current
            lag_ms = max(0, (self.clock() - self._pending_at) * 1000)
            self._pending = self._pending_at = None
            log_event(LogEvent.CHECKPOINT_COMPLETED, latency_ms=lag_ms, checkpoint_metrics=self.metrics())
            await self._cache(snapshot)

    async def aclose(self):
        async with self._lock:
            if self._closed:
                return
            try:
                await self._flush()
            finally:
                self._closed = True
                try:
                    if self.lease is not None:
                        async with asyncio.timeout(self.timeout):
                            await self.lease.release()
                finally:
                    close = getattr(self.hot, 'aclose', None)
                    if close:
                        await close()


async def save_checkpoint(store, state, milestone):
    checkpoint = getattr(store, 'checkpoint', None)
    if checkpoint is not None:
        await checkpoint(state, milestone)
    else:
        await store.save(state)


class CheckpointSafetyEventStore:
    """Audit commits before its state milestone; failures remain visible to safety."""
    def __init__(self, audit_store, state_store, state):
        self.audit_store, self.state_store, self.state = audit_store, state_store, state

    async def record(self, context, events):
        await self.audit_store.record(context, events)
        if events:
            await self.state_store.checkpoint(self.state, CheckpointMilestone.SAFETY_TRIGGERED)
