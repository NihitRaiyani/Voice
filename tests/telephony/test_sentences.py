"""The sentence aggregator must not carry a cancelled sentence into the next turn.

The defect being fixed is in pipecat 1.6.0's `SentenceAggregator`: its buffer is cleared on
an emitted sentence and on `EndFrame`, and an interruption is neither. The first test below
asserts the defect is still present upstream, so that the day pipecat fixes it this suite
says so rather than quietly keeping a redundant subclass forever.

The dirty buffer is set directly. That is deliberate and it is the honest way to express
this: `FrameProcessorQueue` gives system frames priority, so an `InterruptionFrame` queued
alongside TextFrames is always processed BEFORE them — the "text, then interruption"
ordering the live pipeline has cannot be produced by queueing both. A pre-set
`_aggregation` is exactly the state a cancelled generation leaves behind, which is what
these tests are about.
"""

import asyncio

from pipecat.frames.frames import InterruptionFrame, TextFrame
from pipecat.processors.aggregators.sentence import SentenceAggregator
from pipecat.tests.utils import run_test

from roma.telephony.sentences import InterruptibleSentenceAggregator

CANCELLED = "Aap kis saal"
NEXT_TURN = "Achha, aap shop mein kaam kar rahe ho."


def _texts(agg, frames):
    """The TextFrames the aggregator pushed downstream, driven through pipecat's own
    processor harness (same shape as tests/telephony/test_pretts.py)."""
    down, _ = asyncio.run(run_test(agg, frames_to_send=frames, expected_down_frames=None))
    return [f.text for f in down if isinstance(f, TextFrame)]


def _interrupted(agg):
    """A half-sentence buffered, then an interruption, then the next turn."""
    agg._aggregation = CANCELLED
    return _texts(agg, [InterruptionFrame(), TextFrame(NEXT_TURN)])


def test_pipecat_still_keeps_the_buffer_across_an_interruption():
    """The upstream defect, pinned. If this ever fails, pipecat has fixed it and
    `InterruptibleSentenceAggregator` can go away."""
    assert _interrupted(SentenceAggregator()) == [CANCELLED + NEXT_TURN]


def test_an_interrupted_sentence_is_dropped():
    """THE regression. The lead heard a question they had already answered, welded to the
    front of an unrelated sentence with no space between them — and called it Roma
    forgetting what they had told her."""
    assert _interrupted(InterruptibleSentenceAggregator()) == [NEXT_TURN]


def test_an_uninterrupted_sentence_is_untouched():
    """The fix must not cost the ordinary path: partial text still accumulates across
    frames and emits once at the end of the sentence."""
    got = _texts(
        InterruptibleSentenceAggregator(),
        [TextFrame("Achha,"), TextFrame(" bohot accha."), TextFrame(" Aage?")],
    )
    assert got[0] == "Achha, bohot accha."


def test_an_interruption_with_nothing_buffered_is_harmless():
    got = _texts(InterruptibleSentenceAggregator(), [InterruptionFrame(), TextFrame(NEXT_TURN)])
    assert got == [NEXT_TURN]


def test_a_trailing_sentence_still_flushes_on_end():
    """`EndFrame` handling is inherited and must survive the override — a sign-off with no
    full stop must not be swallowed."""
    assert _texts(InterruptibleSentenceAggregator(), [TextFrame("Milte hain")]) == [
        "Milte hain"
    ]


def test_the_dropped_text_is_never_logged(caplog):
    """It is Roma's own line, but it quotes the turn the lead just interrupted, and this
    fires on every barge-in. Length only."""
    import logging

    with caplog.at_level(logging.INFO, logger="roma.telephony"):
        _interrupted(InterruptibleSentenceAggregator())
    assert any("sentence aggregator" in r.getMessage() for r in caplog.records)
    for r in caplog.records:
        assert CANCELLED not in r.getMessage()
