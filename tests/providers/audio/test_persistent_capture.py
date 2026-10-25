"""Persistent callback capture contracts with fake native streams; no audio hardware."""

import asyncio
from types import SimpleNamespace

import pytest
from roma.core.voice_errors import VoiceFailure
from roma.providers.audio.local_device import LocalAudioDevice
from roma.services.speech_input import SpeechTranscript


class CallbackBackend:
    def __init__(self):
        self.created = 0
        self.events = []
        self.callback = None

    def RawInputStream(self, **kwargs):
        self.created += 1
        self.callback = kwargs["callback"]
        backend = self

        class Stream:
            def start(self):
                backend.events.append("start")

            def abort(self):
                backend.events.append("abort")

            def close(self):
                backend.events.append("close")

        return Stream()

    def emit(self, value=1, *, overflow=False):
        self.callback(
            bytes([value, 0]) * 320,
            320,
            SimpleNamespace(),
            SimpleNamespace(input_overflow=overflow),
        )


class Speech:
    def __init__(self):
        self.received = []
        self.resets = 0
        self.on_utterance_finalizing = None

    async def feed(self, pcm):
        self.received.append(pcm)
        if self.on_utterance_finalizing:
            self.on_utterance_finalizing()
        return (SpeechTranscript("synthetic", "en", 20, "silence", 0),)

    def reset(self):
        self.resets += 1


def test_one_explicit_input_stream_serves_two_active_captures_then_closes_once():
    async def run():
        backend, speech = CallbackBackend(), Speech()
        device = LocalAudioDevice(backend=backend)
        await device.start()
        backend.emit(9)  # muted input must not be reused when listening opens.
        first, _ = await device.listen(speech, on_capture_started=lambda: backend.emit(1))
        backend.emit(8)
        second, _ = await device.listen(speech, on_capture_started=lambda: backend.emit(2))
        assert first.text == second.text == "synthetic"
        assert speech.received == [b"\1\0" * 320, b"\2\0" * 320]
        assert speech.resets == 2
        assert backend.created == 1 and backend.events == ["start"]
        await device.aclose()
        await device.aclose()
        assert backend.events == ["start", "abort", "close"]

    asyncio.run(run())


def test_callback_stall_has_a_distinct_fixed_code_and_never_reaches_asr():
    async def run():
        backend, speech = CallbackBackend(), Speech()
        device = LocalAudioDevice(backend=backend, capture_watchdog_secs=0.02)
        await device.start()
        try:
            with pytest.raises(VoiceFailure) as error:
                await device.listen(speech, max_seconds=0.1)
            assert error.value.code.value == "DEVICE-CAPTURE-STALLED"
            assert speech.received == [] and speech.resets == 1
            assert device.capture_mode == "MUTED_HALF_DUPLEX"
        finally:
            await device.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("overflow_source", ["native", "capacity"])
def test_active_input_loss_rejects_turn_without_submitting_partial_audio(overflow_source):
    async def run():
        backend, speech = CallbackBackend(), Speech()
        device = LocalAudioDevice(backend=backend, capture_capacity=1)
        await device.start()

        def opened():
            backend.emit(1, overflow=overflow_source == "native")
            if overflow_source == "capacity":
                backend.emit(2)

        try:
            with pytest.raises(ValueError, match="overflow"):
                await device.listen(speech, on_capture_started=opened)
            assert speech.received == [] and speech.resets == 1
            assert device.capture_mode == "MUTED_HALF_DUPLEX"
        finally:
            await device.aclose()

    asyncio.run(run())


def test_muted_overflow_and_synthesis_echo_never_poison_next_capture():
    async def run():
        backend, speech = CallbackBackend(), Speech()
        device = LocalAudioDevice(backend=backend, capture_capacity=1)
        await device.start()
        backend.emit(9, overflow=True)

        class FinalSpeech(Speech):
            async def feed(self, pcm):
                self.received.append(pcm)
                self.on_utterance_finalizing()
                for _ in range(10):
                    backend.emit(8, overflow=True)
                await asyncio.sleep(0)
                return (SpeechTranscript("synthetic", "en", 20, "silence", 0),)

        speech = FinalSpeech()
        try:
            result, _ = await device.listen(speech, on_capture_started=lambda: backend.emit(1))
            assert result.text == "synthetic" and speech.received == [b"\1\0" * 320]
            assert device.capture.active_overflows == 0
            result, _ = await device.listen(speech, on_capture_started=lambda: backend.emit(2))
            assert speech.received[-1] == b"\2\0" * 320
        finally:
            await device.aclose()

    asyncio.run(run())


def test_cancelled_listen_mutes_and_flushes_without_reopening_input():
    async def run():
        backend, speech = CallbackBackend(), Speech()
        device = LocalAudioDevice(backend=backend)
        await device.start()
        opened = asyncio.Event()
        task = asyncio.create_task(device.listen(speech, on_capture_started=opened.set))
        await opened.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert device.capture_mode == "MUTED_HALF_DUPLEX" and speech.resets == 1
        assert backend.events == ["start"]
        backend.emit(9)
        result, _ = await device.listen(speech, on_capture_started=lambda: backend.emit(3))
        assert result.text == "synthetic" and speech.received == [b"\3\0" * 320]
        await device.aclose()
        assert backend.created == 1

    asyncio.run(run())


def test_old_epoch_capture_is_discarded_before_speech_decoder():
    from roma.core.turn_epoch import TurnEpoch

    async def run():
        backend, speech, epochs = CallbackBackend(), Speech(), TurnEpoch()
        device = LocalAudioDevice(backend=backend, epoch_guard=epochs)
        await device.start()

        def opened():
            backend.emit(1)
            epochs.advance()

        try:
            with pytest.raises(asyncio.CancelledError):
                await device.listen(speech, on_capture_started=opened)
            assert speech.received == []
            result, _ = await device.listen(speech, on_capture_started=lambda: backend.emit(2))
            assert result.text == "synthetic" and speech.received == [b"\2\0" * 320]
        finally:
            await device.aclose()

    asyncio.run(run())


def test_call_close_finishes_native_cleanup_even_when_cancelled_twice():
    import threading

    async def run():
        entered, release = threading.Event(), threading.Event()
        backend = CallbackBackend()
        original = backend.RawInputStream

        def stream_factory(**kwargs):
            stream = original(**kwargs)

            def abort():
                backend.events.append("abort")
                entered.set()
                assert release.wait(2)

            stream.abort = abort
            return stream

        backend.RawInputStream = stream_factory
        device = LocalAudioDevice(backend=backend)
        await device.start()
        task = asyncio.create_task(device.aclose())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await device.aclose()
        assert backend.events == ["start", "abort", "close"]
        assert device.capture_mode == "CLOSED"

    asyncio.run(run())


def test_cancelled_native_start_is_aborted_joined_and_closed_once():
    import threading

    async def run():
        entered, release = threading.Event(), threading.Event()
        backend = CallbackBackend()
        original = backend.RawInputStream

        def stream_factory(**kwargs):
            stream = original(**kwargs)

            def start():
                backend.events.append("start")
                entered.set()
                assert release.wait(2)

            def abort():
                backend.events.append("abort")
                release.set()

            stream.start, stream.abort = start, abort
            return stream

        backend.RawInputStream = stream_factory
        device = LocalAudioDevice(backend=backend)
        task = asyncio.create_task(device.start())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await device.aclose()
        assert backend.events == ["start", "abort", "close"]
        with pytest.raises(RuntimeError):
            await device.start()

    asyncio.run(run())


@pytest.mark.parametrize("consumer_wakes_at", [1.0, 1.2])
@pytest.mark.parametrize("late_frames", [0, 3, 80])
@pytest.mark.parametrize("on_time_frames", [25, 50])
def test_deadline_drains_received_frames_without_admitting_later_audio(
    consumer_wakes_at, late_frames, on_time_frames,
):
    async def run():
        now = [0.0]
        backend = CallbackBackend()
        device = LocalAudioDevice(backend=backend, clock=lambda: now[0], capture_capacity=64)

        class DeadlineSpeech(Speech):
            async def feed(self, pcm):
                self.received.append(pcm)
                return ()

            async def finish(self, *, pad_tail, endpoint_reason):
                assert pad_tail and endpoint_reason == "deadline"
                assert device.capture_mode == "MUTED_HALF_DUPLEX"
                return (SpeechTranscript("synthetic", "en", len(b"".join(self.received)) / 32,
                                         endpoint_reason, 0),)

        speech = DeadlineSpeech()

        def opened():
            # Callback timestamps describe completed 20 ms frames. The consumer
            # resumes at/after its deadline with bounded, already captured PCM.
            for index in range(1, on_time_frames + 1):
                now[0] = index / 50
                backend.emit(index)
            for index in range(1, late_frames + 1):
                now[0] = 1 + index / 50
                backend.emit(100 + index)
            now[0] = max(now[0], consumer_wakes_at)

        await device.start()
        try:
            result, _ = await device.listen(speech, max_seconds=1, on_capture_started=opened)
            expected = b"".join(bytes([index, 0]) * 320 for index in range(1, on_time_frames + 1))
            assert b"".join(speech.received) == expected
            assert result.reason == "deadline" and result.duration_ms == on_time_frames * 20
            assert speech.resets == 1 and device.capture_mode == "MUTED_HALF_DUPLEX"
        finally:
            await device.aclose()
        assert backend.created == 1 and backend.events == ["start", "abort", "close"]

    asyncio.run(run())
