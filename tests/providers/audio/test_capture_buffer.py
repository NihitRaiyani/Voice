"""Bounded PCM callback handoff under mutation and producer contention."""

import threading

import pytest
from roma.providers.audio.capture_buffer import CaptureBuffer


def test_callback_copies_pcm_and_discards_old_epochs_and_muted_generations():
    buffer = CaptureBuffer(2)
    buffer.push(b"\0\0" * 320, 320, 0, True)
    generation = buffer.activate(1)
    pcm = bytearray(b"\1\0" * 320)
    buffer.push(pcm, 320, 1)
    pcm[:] = bytes([9, 0]) * 320
    buffer.push(b"\2\0" * 320, 320, 0)
    assert buffer.pop(generation, 1).pcm == b"\1\0" * 320
    assert buffer.pop(generation, 1) is None
    buffer.push(b"\3\0" * 320, 320, 1)
    buffer.mute()
    next_generation = buffer.activate(1)
    assert next_generation > generation and buffer.pop(next_generation, 1) is None


def test_contended_callback_returns_without_waiting_and_poisoned_turn_fails():
    buffer = CaptureBuffer(2)
    generation = buffer.activate(1)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    class PausedCopy:
        def __len__(self):
            return 640

        def __bytes__(self):
            entered.set()
            assert release.wait(2)
            return b"\1\0" * 320

    first = threading.Thread(target=lambda: buffer.push(PausedCopy(), 320, 1))
    second = threading.Thread(
        target=lambda: (buffer.push(b"\2\0" * 320, 320, 1), returned.set())
    )
    first.start()
    assert entered.wait(1)
    try:
        second.start()
        assert returned.wait(0.1), "Callback waited for the consumer/producer lock"
    finally:
        release.set()
        first.join(1)
        second.join(1)
    with pytest.raises(ValueError, match="overflow"):
        buffer.pop(generation, 1)
    assert buffer.active_overflows == 1
