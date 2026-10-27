"""Call-owned deadline and cancellation around the governed answer provider.

Never release partial model JSON. The validated short quote travels downstream through
sentence aggregation and the existing final speech filter.
"""
import asyncio
from contextlib import suppress
from time import perf_counter

from roma.core.logging import LogEvent, log_event
from roma.core.turn_epoch import TurnEpoch, epoch_scope
from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
from roma.services.knowledge_answer import KnowledgeAnswer


class LiveKnowledge:
    def __init__(self, source, *, timeout_secs=10, filler_after_secs=.15, epoch=None):
        if not 0 < timeout_secs <= 30 or not 0 < filler_after_secs <= 2:
            raise ValueError('Invalid live knowledge policy')
        self.source, self.timeout_secs = source, timeout_secs
        self.filler_after_secs, self.epoch = filler_after_secs, epoch or TurnEpoch()
        self._pending = None

    async def answer_turn(self, state, text, topic, *, output_language='code_mix', on_wait=None):
        if self._pending is not None:
            return KnowledgeAnswer(NO_EVIDENCE_LINE, reason='busy')
        epoch, started = self.epoch.current, perf_counter()
        async def source():
            with epoch_scope(self.epoch, epoch):
                return await self.source.answer_turn(state, text, topic, output_language=output_language)
        task = asyncio.create_task(source())
        self._pending = task
        answer = KnowledgeAnswer(NO_EVIDENCE_LINE, reason='unavailable')
        try:
            async with asyncio.timeout(self.timeout_secs):
                done, _ = await asyncio.wait({task}, timeout=self.filler_after_secs)
                self.epoch.require_current(epoch)
                if not done and on_wait is not None:
                    await on_wait()  # trusted cached PCM callback, never generated filler
                answer = await task
                self.epoch.require_current(epoch)
                return answer
        except TimeoutError:
            return KnowledgeAnswer(NO_EVIDENCE_LINE, reason='timeout')
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            if self._pending is task:
                self._pending = None
            log_event(LogEvent.KNOWLEDGE_ROUTED, route='fallback' if answer.fallback else 'knowledge',
                      chunk_ids=[str(item.chunk_id) for item in answer.evidence],
                      latency_ms=(perf_counter() - started) * 1000)

    async def interrupt(self, *, advance_epoch=True):
        if advance_epoch:
            self.epoch.advance()
        pending = self._pending
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending

    async def aclose(self):
        await self.interrupt()
