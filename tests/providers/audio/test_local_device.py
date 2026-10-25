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


def test_capture_mutes_before_returning_and_call_close_owns_native_teardown():
    from tests.providers.audio.test_persistent_capture import (
        CallbackBackend,
    )
    from tests.providers.audio.test_persistent_capture import (
        Speech as InputSpeech,
    )

    async def run():
        backend, speech = CallbackBackend(), InputSpeech()
        device = LocalAudioDevice(backend=backend)
        await device.start()
        result, timing = await device.listen(speech, on_capture_started=lambda: backend.emit(1))
        assert result.text == "synthetic" and timing["capture_ms"] >= 0
        assert device.capture_mode == "MUTED_HALF_DUPLEX" and speech.resets == 1
        assert backend.events == ["start"]
        await device.aclose()
        assert backend.events == ["start", "abort", "close"]

    asyncio.run(run())


def test_capture_started_callback_follows_running_stream_and_active_mode():
    from tests.providers.audio.test_persistent_capture import (
        CallbackBackend,
    )
    from tests.providers.audio.test_persistent_capture import (
        Speech as InputSpeech,
    )

    async def run():
        backend = CallbackBackend()
        device = LocalAudioDevice(backend=backend)
        await device.start()

        def cue():
            assert device.capture_mode == "ACTIVE" and backend.events == ["start"]
            backend.events.append("cue")
            backend.emit(1)

        await device.listen(InputSpeech(), on_capture_started=cue)
        assert backend.events == ["start", "cue"]
        await device.aclose()

    asyncio.run(run())


def test_capture_start_failure_has_no_listening_cue_and_tears_down_once():
    events, cues = [], []

    class Broken(Stream):
        def start(self):
            events.append("start")
            raise OSError("scripted device unavailable")

        def abort(self):
            events.append("abort")

        def close(self):
            events.append("close")

    async def run():
        dev = device(Broken())
        with pytest.raises(OSError):
            await dev.start()
        with pytest.raises(RuntimeError):
            await dev.listen(Speech(), on_capture_started=lambda: cues.append(True))
        await dev.aclose()
        assert events == ["start", "abort", "close"] and cues == []

    asyncio.run(run())


def test_expired_callback_capture_is_not_submitted_to_asr():
    from tests.providers.audio.test_persistent_capture import (
        CallbackBackend,
    )
    from tests.providers.audio.test_persistent_capture import (
        Speech as InputSpeech,
    )

    async def run():
        expired = [False]

        def guard():
            if expired[0]:
                raise VoiceFailure(VoiceErrorCode.CAPTURE)

        backend, speech = CallbackBackend(), InputSpeech()
        device = LocalAudioDevice(backend=backend, check_active=guard)
        await device.start()

        def cue():
            backend.emit(1)
            expired[0] = True

        try:
            with pytest.raises(VoiceFailure):
                await device.listen(speech, on_capture_started=cue)
            assert speech.received == [] and speech.resets == 1
        finally:
            await device.aclose()

    asyncio.run(run())
