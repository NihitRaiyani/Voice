import asyncio
from types import SimpleNamespace

import pytest
from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
from roma.providers.ai.contracts import AudioFormat, TTSResult
from roma.providers.audio.local_device import LocalAudioDevice
from roma.services.speech_input import SpeechTranscript


class Stream:
    def __init__(self, *, overflow=False, underflow=False):
        self.events = []
        self.overflow = overflow
        self.underflow = underflow

    def start(self):
        self.events.append("start")

    def abort(self):
        self.events.append("abort")

    def close(self):
        self.events.append("close")

    def stop(self):
        self.events.append("stop")

    def read(self, count):
        return b"\0\0" * count, self.overflow

    def write(self, pcm):
        self.events.append("write")
        return self.underflow


class Speech:
    def __init__(self):
        self.reset_called = False

    async def feed(self, pcm):
        return (SpeechTranscript("public", None, 20, "silence", 1),)

    def reset(self):
        self.reset_called = True


def device(stream, **kwargs):
    return LocalAudioDevice(
        backend=SimpleNamespace(
            RawInputStream=lambda **kwargs: stream, RawOutputStream=lambda **kwargs: stream
        ),
        **kwargs,
    )


def test_microphone_closes_before_returning_a_turn():
    async def run():
        stream, speech = Stream(), Speech()
        result, timing = await device(stream).listen(speech)
        assert result.text == "public" and timing["capture_ms"] >= 0
        assert stream.events == ["start", "abort", "close"] and speech.reset_called

    asyncio.run(run())


def test_overflow_discards_incomplete_audio_and_closes():
    async def run():
        stream, speech = Stream(overflow=True), Speech()
        with pytest.raises(ValueError, match="overflow"):
            await device(stream).listen(speech)
        assert stream.events[-2:] == ["abort", "close"] and speech.reset_called

    asyncio.run(run())


@pytest.mark.parametrize("underflow", [False, True])
def test_playback_writes_pcm_and_always_closes(underflow):
    async def run():
        stream = Stream(underflow=underflow)
        if underflow:
            with pytest.raises(ValueError, match="underflow"):
                await device(stream).play(TTSResult(b"\0\0" * 800, AudioFormat(8000)))
        else:
            await device(stream).play(TTSResult(b"\0\0" * 800, AudioFormat(8000)))
        assert stream.events[-2:] == ["abort", "close"] and "write" in stream.events

    asyncio.run(run())


def test_invalid_output_never_opens_a_stream():
    async def run():
        stream = Stream()
        with pytest.raises(ValueError):
            await device(stream).play(TTSResult(b"x", AudioFormat(8000)))
        assert not stream.events

    asyncio.run(run())


def test_cancelled_capture_aborts_and_joins_native_reader():
    import threading

    class Blocking(Stream):
        def __init__(self):
            super().__init__()
            self.waiting = threading.Event()
            self.release = threading.Event()

        def read(self, count):
            self.waiting.set()
            self.release.wait(2)
            return super().read(count)

        def abort(self):
            super().abort()
            self.release.set()

    async def run():
        stream, speech = Blocking(), Speech()
        task = asyncio.create_task(device(stream).listen(speech))
        await asyncio.to_thread(stream.waiting.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.events[-2:] == ["abort", "close"] and speech.reset_called

    asyncio.run(run())


def test_capture_started_callback_follows_open_before_first_read():
    async def run():
        events = []

        class Observed(Stream):
            def start(self):
                events.append("opened")
                super().start()

            def read(self, count):
                events.append("read")
                return super().read(count)

        stream = Observed()
        await device(stream).listen(Speech(), on_capture_started=lambda: events.append("cue"))
        assert events == ["opened", "cue", "read"]

    asyncio.run(run())


def test_capture_started_callback_is_absent_when_stream_open_fails():
    async def run():
        cues = []

        class Broken(Stream):
            def start(self):
                raise OSError("scripted device unavailable")

        with pytest.raises(OSError):
            await device(Broken()).listen(
                Speech(), on_capture_started=lambda: cues.append(True)
            )
        assert cues == []

    asyncio.run(run())


def test_expired_playback_after_native_start_never_writes_audio():
    async def run():
        expired = [False]

        class Delayed(Stream):
            def start(self):
                super().start()
                expired[0] = True

        def guard():
            if expired[0]:
                raise VoiceFailure(VoiceErrorCode.CAPTURE)

        stream = Delayed()
        with pytest.raises(VoiceFailure):
            await device(stream, check_active=guard).play(
                TTSResult(b"\0\0" * 800, AudioFormat(8000))
            )
        assert "write" not in stream.events and stream.events[-2:] == ["abort", "close"]

    asyncio.run(run())


def test_expiry_between_playback_blocks_stops_further_native_writes():
    async def run():
        expired = [False]

        class Paused(Stream):
            def write(self, pcm):
                result = super().write(pcm)
                expired[0] = True
                return result

        def guard():
            if expired[0]:
                raise VoiceFailure(VoiceErrorCode.CAPTURE)

        stream = Paused()
        with pytest.raises(VoiceFailure):
            await device(stream, check_active=guard).play(
                TTSResult(b"\0\0" * 4000, AudioFormat(8000))
            )
        assert stream.events.count("write") == 1
        assert stream.events[-2:] == ["abort", "close"]

    asyncio.run(run())


def test_expired_native_capture_read_is_not_submitted_to_asr():
    async def run():
        expired = [False]

        class Paused(Stream):
            def read(self, count):
                result = super().read(count)
                expired[0] = True
                return result

        class NoInference(Speech):
            async def feed(self, pcm):
                pytest.fail("Expired native capture must not reach VAD or ASR")

        def guard():
            if expired[0]:
                raise VoiceFailure(VoiceErrorCode.CAPTURE)

        stream, speech = Paused(), NoInference()
        with pytest.raises(VoiceFailure):
            await device(stream, check_active=guard).listen(speech)
        assert stream.events[-2:] == ["abort", "close"] and speech.reset_called

    asyncio.run(run())
