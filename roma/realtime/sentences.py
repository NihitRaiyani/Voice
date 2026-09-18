"""Sentence aggregation that survives a barge-in.

`pipecat.processors.aggregators.sentence.SentenceAggregator` buffers LLM text until it
sees an end-of-sentence, then emits the whole sentence. In pipecat 1.6.0 that buffer is
cleared on exactly two events — an emitted sentence and `EndFrame` — and an interruption
is neither. So when a barge-in kills a generation mid-sentence, the half-sentence stays
in the buffer and is silently prepended to the FIRST sentence of the next turn.

Live call CA9933275 (2026-07-27), Roma's actual output, two turns apart:

    Generating TTS [Achha, shop mein kaam kar rahi ho.  Aap kis saal complete hui thi?]
    barge-in: user speech exceeded 0.60s over Roma
    Generating TTS [Samajh gayi.]
    Generating TTS [Aap kis saalAchha, aap shop mein kaam kar rahe ho. Aap subah free...]
                    ^^^^^^^^^^^^^^ five words from a turn that was cancelled

The lead heard a question they had already answered, welded to the front of an unrelated
sentence, with no space between them. Their word for it was that Roma forgets what they
told her — and from the outside that is exactly what it looks like.

The fix is one line of state management, but it belongs in a named subclass rather than
inline in the pipeline: this is a defect in a specific version of a dependency, and when
pipecat fixes it upstream this class becomes an empty override that says so.
"""

import logging

from pipecat.frames.frames import InterruptionFrame
from pipecat.processors.aggregators.sentence import SentenceAggregator
from pipecat.processors.frame_processor import FrameDirection

_log = logging.getLogger("roma.realtime")


class InterruptibleSentenceAggregator(SentenceAggregator):
    """`SentenceAggregator` that drops its half-sentence when the turn is interrupted.

    A cancelled sentence is cancelled: the lead has started talking, Roma's reply is being
    torn down, and the words she had not finished saying are no longer hers to say. Holding
    them is not "not losing them" — it is speaking them at a moment when they answer
    nothing, in the middle of a different sentence.
    """

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        if isinstance(frame, InterruptionFrame) and self._aggregation:
            _log.info(
                "sentence aggregator: dropped %d chars of an interrupted sentence",
                len(self._aggregation),
            )
            self._aggregation = ""
        await super().process_frame(frame, direction)


__all__ = ["InterruptibleSentenceAggregator"]
