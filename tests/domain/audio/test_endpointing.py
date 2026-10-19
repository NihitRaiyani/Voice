import math
from dataclasses import replace
from itertools import pairwise

import pytest
from roma.domain.audio.endpointing import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    EndpointConfig,
    EndpointDetector,
)
from roma.domain.audio.pcm import AudioFormatError, pack


def frame(value=1):
    return pack([value] * FRAME_SAMPLES)


def feed(detector, probabilities):
    return tuple(
        utterance
        for index, probability in enumerate(probabilities, 1)
        for utterance in detector.feed(frame(index), probability)
    )


def test_silence_only_never_emits_and_idle_memory_is_bounded():
    detector = EndpointDetector()
    assert feed(detector, [0.1] * 10000) == ()
    assert detector.state == "idle"
    assert detector.buffered_bytes == 8 * FRAME_BYTES
    assert detector.flush() == ()
    assert detector.buffered_bytes == 0


def test_short_acknowledgement_retains_preroll_and_hangover_without_minimum_veto():
    detector = EndpointDetector()
    assert feed(detector, [0.1] * 10) == ()
    assert feed(detector, [0.8]) == ()
    assert detector.state == "starting"
    assert feed(detector, [0.8]) == ()
    assert detector.state == "speaking"
    assert feed(detector, [0.1] * 15) == ()
    assert detector.state == "ending"
    result = detector.feed(frame(99), 0.1)[0]
    assert result.reason == "silence"
    assert result.start_sample == 4 * FRAME_SAMPLES
    assert result.end_sample == 16 * FRAME_SAMPLES
    assert result.speech_frames == 2
    assert result.duration_ms == 384
    assert len(result.pcm) == 12 * FRAME_BYTES
    assert result.pcm[:FRAME_BYTES] == frame(5)
    assert result.pcm[-FRAME_BYTES:] == frame(4)
    assert detector.state == "idle" and detector.buffered_bytes == 0


def test_hysteresis_retains_trailing_quiet_words_and_restarts_silence_timer():
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0))
    assert feed(detector, [0.6, 0.6] + [0.1] * 15 + [0.35] * 4 + [0.1] * 15) == ()
    result = detector.feed(frame(99), 0.1)[0]
    assert result.reason == "silence"
    assert result.speech_frames == 6
    assert len(result.pcm) == (2 + 15 + 4 + 4) * FRAME_BYTES
    assert result.pcm[20 * FRAME_BYTES : 21 * FRAME_BYTES] == frame(21)


def test_consecutive_start_threshold_rejects_isolated_spikes_but_flush_keeps_short_tail():
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0))
    assert feed(detector, [0.8, 0.1, 0.8, 0.1]) == ()
    assert detector.state == "idle"
    assert detector.flush() == ()
    detector.feed(frame(50), 0.8)
    utterance = detector.flush()[0]
    assert utterance.reason == "flush" and utterance.speech_frames == 1
    assert utterance.pcm == frame(50)
    assert detector.flush() == ()


def test_max_duration_splits_continuous_speech_without_loss_overlap_or_unbounded_memory():
    config = EndpointConfig(pre_roll_ms=0, max_utterance_ms=193)
    detector = EndpointDetector(config)
    results = []
    for index in range(1000):
        results.extend(detector.feed(frame(index), 0.9))
        assert detector.buffered_bytes <= 6 * FRAME_BYTES
    results.extend(detector.flush())
    assert all(result.duration_ms <= 193 for result in results)
    assert all(result.reason == "max_duration" for result in results[:-1])
    assert b"".join(result.pcm for result in results) == b"".join(frame(i) for i in range(1000))
    assert all(a.end_sample == b.start_sample for a, b in pairwise(results))


def test_flush_emits_without_waiting_for_missing_silence_and_reset_discards():
    detector = EndpointDetector()
    feed(detector, [0.8] * 3)
    result = detector.flush()[0]
    assert result.reason == "flush" and len(result.pcm) == 3 * FRAME_BYTES
    assert detector.flush() == ()
    feed(detector, [0.8] * 3)
    detector.reset()
    assert detector.state == "idle" and detector.position_samples == 0
    assert detector.buffered_bytes == 0 and detector.flush() == ()


def test_discontinuity_emits_old_segment_and_never_bridges_the_gap_or_old_preroll():
    detector = EndpointDetector()
    feed(detector, [0.8] * 3)
    old = detector.feed(frame(99), 0.8, discontinuity=True)[0]
    assert old.reason == "discontinuity" and old.end_sample == 3 * FRAME_SAMPLES
    assert detector.state == "starting"
    new = detector.flush()[0]
    assert new.start_sample == old.end_sample
    assert new.pcm == frame(99)
    assert detector.discontinuity() == ()
    feed(detector, [0.1] * 4)
    assert detector.discontinuity() == ()
    assert detector.buffered_bytes == 0


@pytest.mark.parametrize(
    "probability", [-0.1, 1.1, math.nan, math.inf, -math.inf, True, "0.5", None]
)
def test_invalid_probabilities_fail_before_state_mutation(probability):
    detector = EndpointDetector()
    detector.feed(frame(), 0.8)
    before = detector.position_samples, detector.buffered_bytes, detector.state
    with pytest.raises(AudioFormatError):
        detector.feed(frame(), probability, discontinuity=True)
    assert (detector.position_samples, detector.buffered_bytes, detector.state) == before


@pytest.mark.parametrize(
    "pcm,metadata",
    [
        (b"", {}),
        (b"x", {}),
        (b"x" * 1022, {}),
        (b"x" * 1026, {}),
        (bytearray(1024), {}),
        (frame(), {"sample_rate": 8000}),
        (frame(), {"channels": 2}),
        (frame(), {"sample_width": 4}),
        (frame(), {"channels": True}),
        (frame(), {"discontinuity": 1}),
    ],
)
def test_invalid_format_fails_explicitly_and_keeps_pending_audio(pcm, metadata):
    detector = EndpointDetector()
    detector.feed(frame(), 0.8)
    with pytest.raises(AudioFormatError):
        detector.feed(pcm, 0.8, **metadata)
    assert detector.position_samples == FRAME_SAMPLES
    assert detector.flush()[0].pcm == frame()


@pytest.mark.parametrize(
    "changes",
    [
        {"speech_threshold": 0.2},
        {"silence_threshold": 0},
        {"speech_threshold": math.nan},
        {"silence_threshold": math.inf},
        {"start_ms": 0},
        {"start_ms": -1},
        {"start_ms": 1.5},
        {"pre_roll_ms": True},
        {"silence_ms": 0},
        {"hangover_ms": 513},
        {"max_utterance_ms": 256},
        {"max_utterance_ms": 120001},
    ],
)
def test_invalid_config_is_rejected(changes):
    with pytest.raises(AudioFormatError):
        EndpointDetector(replace(EndpointConfig(), **changes))


def test_non_frame_aligned_waits_round_up_and_hard_max_rounds_down():
    detector = EndpointDetector(
        EndpointConfig(
            start_ms=33,
            pre_roll_ms=0,
            hangover_ms=1,
            silence_ms=33,
            max_utterance_ms=193,
        )
    )
    assert feed(detector, [0.8]) == ()
    assert detector.state == "starting"
    assert feed(detector, [0.8, 0.1]) == ()
    result = detector.feed(frame(), 0.1)[0]
    assert result.reason == "silence" and len(result.pcm) == 3 * FRAME_BYTES


def test_short_flush_keeps_exact_preroll_and_config_enforces_hard_max():
    detector = EndpointDetector()
    feed(detector, [0.1] * 20)
    detector.feed(frame(50), 0.8)
    utterance = detector.flush()[0]
    assert len(utterance.pcm) == 7 * FRAME_BYTES
    assert utterance.start_sample == 14 * FRAME_SAMPLES
    with pytest.raises(AudioFormatError):
        EndpointConfig(max_utterance_ms=257)


def test_max_duration_preserves_hysteresis_for_quiet_continuing_speech():
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193))
    results = list(feed(detector, [0.9, 0.9] + [0.4] * 8))
    results.extend(detector.flush())
    assert [result.reason for result in results] == ["max_duration", "flush"]
    assert b"".join(result.pcm for result in results) == b"".join(
        frame(i) for i in range(1, 11)
    )
    assert sum(result.speech_frames for result in results) == 10
    assert all(result.duration_ms <= 193 for result in results)
    assert results[0].end_sample == results[1].start_sample


def test_silence_timer_spans_max_split_and_requires_fresh_onset_after_real_end():
    detector = EndpointDetector(
        EndpointConfig(
            pre_roll_ms=0,
            hangover_ms=32,
            silence_ms=320,
            max_utterance_ms=193,
        )
    )
    results = feed(detector, [0.9, 0.9] + [0.1] * 4)
    assert len(results) == 1 and results[0].reason == "max_duration"
    assert detector.state == "ending"
    (final,) = feed(detector, [0.1] * 6)
    assert final.reason == "silence" and final.pcm == b""
    assert detector.state == "idle"
    assert feed(detector, [0.4] * 2) == ()
    assert detector.flush() == ()


@pytest.mark.parametrize("operation", ["flush", "discontinuity", "reset"])
def test_explicit_stream_boundary_clears_max_split_hysteresis(operation):
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193))
    assert len(feed(detector, [0.9] * 6)) == 1
    getattr(detector, operation)()
    assert feed(detector, [0.4] * 4) == ()
    assert detector.flush() == ()


def test_speech_returning_before_silence_limit_after_max_uses_continuation_threshold():
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193))
    assert len(feed(detector, [0.9] * 6)) == 1
    assert feed(detector, [0.1] * 15) == ()
    assert feed(detector, [0.4] * 2) == ()
    (tail,) = detector.flush()
    assert len(tail.pcm) == 2 * FRAME_BYTES and tail.speech_frames == 2


def test_exact_max_split_emits_one_empty_final_boundary_at_true_silence():
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193))
    (chunk,) = feed(detector, [0.9] * 6)
    assert chunk.reason == "max_duration" and len(chunk.pcm) == 6 * FRAME_BYTES
    assert feed(detector, [0.1] * 15) == ()
    (final,) = feed(detector, [0.1])
    assert final.reason == "silence" and final.pcm == b""
    assert final.duration_ms == 0 and final.speech_frames == 0
    assert detector.state == "idle"
    assert detector.flush() == detector.discontinuity() == ()


@pytest.mark.parametrize("operation", ["flush", "discontinuity"])
def test_exact_max_split_explicit_boundary_finalizes_once(operation):
    detector = EndpointDetector(EndpointConfig(pre_roll_ms=0, max_utterance_ms=193))
    feed(detector, [0.9] * 6)
    (boundary,) = getattr(detector, operation)()
    assert boundary.reason == operation and boundary.pcm == b""
    assert detector.flush() == detector.discontinuity() == ()
