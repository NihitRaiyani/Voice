import asyncio

import pytest


def test_audio_admission_waits_for_actual_write_acknowledgment():
    from roma.realtime.playback_window import PlaybackWindow

    async def run():
        window = PlaybackWindow(max_audio_secs=.02, enqueue_timeout_secs=.1)
        epoch = await window.reserve(320)
        pending = asyncio.create_task(window.reserve(320))
        await asyncio.sleep(0)
        assert not pending.done()
        assert window.queued_bytes == 320
        window.acknowledge(320, epoch=epoch)
        assert await pending == epoch
        assert window.queued_bytes == 320
        assert window.queued_audio_secs == .02

    asyncio.run(run())


def test_slow_successful_writes_fail_closed_without_growing_the_audio_backlog():
    from roma.realtime.playback_window import PlaybackPressure, PlaybackWindow

    async def run():
        closed = []

        async def close():
            closed.append(True)

        window = PlaybackWindow(max_audio_secs=.02, enqueue_timeout_secs=.01, on_pressure=close)
        epoch = await window.reserve(320)

        async def slow_successful_write():
            await asyncio.sleep(.5)
            window.acknowledge(320, epoch=epoch)

        writer = asyncio.create_task(slow_successful_write())
        try:
            with pytest.raises(PlaybackPressure):
                await asyncio.wait_for(window.reserve(320), .1)
            assert not writer.done()
            assert window.queued_bytes == 320
            assert window.failed and closed == [True]
            with pytest.raises(PlaybackPressure):
                await window.reserve(320)
            assert closed == [True]
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

    asyncio.run(run())


def test_interruption_releases_old_waiters_and_stale_writes_cannot_refund_new_audio():
    from roma.realtime.playback_window import PlaybackWindow

    async def run():
        window = PlaybackWindow(max_audio_secs=.02)
        old = await window.reserve(320)
        pending = asyncio.create_task(window.reserve(320, epoch=old))
        await asyncio.sleep(0)
        current = window.reset()
        assert current != old and window.queued_bytes == 0
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, .1)
        assert await window.reserve(320, epoch=current) == current
        window.acknowledge(320, epoch=old)
        assert window.queued_bytes == 320
        window.acknowledge(320, epoch=current)
        assert window.queued_bytes == 0
        with pytest.raises(asyncio.CancelledError):
            await window.reserve(320, epoch=old)

    asyncio.run(run())


def test_concurrent_pressure_closes_once_and_failed_window_stays_closed_after_reset():
    from roma.realtime.playback_window import PlaybackPressure, PlaybackWindow

    async def run():
        closes = []

        async def close():
            closes.append(True)
            await asyncio.sleep(.02)

        window = PlaybackWindow(max_audio_secs=.02, enqueue_timeout_secs=.01, on_pressure=close)
        await window.reserve(320)
        results = await asyncio.gather(*(window.reserve(320) for _ in range(3)), return_exceptions=True)
        assert all(isinstance(item, PlaybackPressure) for item in results)
        assert closes == [True]
        window.reset()
        with pytest.raises(PlaybackPressure):
            await window.reserve(320)

    asyncio.run(run())


@pytest.mark.parametrize('kwargs', [
    {'max_audio_secs': 0}, {'max_audio_secs': float('inf')},
    {'max_audio_secs': float('nan')}, {'max_audio_secs': 6},
    {'enqueue_timeout_secs': 0}, {'enqueue_timeout_secs': float('inf')},
])
def test_window_configuration_cannot_disable_audio_or_time_bounds(kwargs):
    from roma.realtime.playback_window import PlaybackWindow
    with pytest.raises(ValueError):
        PlaybackWindow(**kwargs)


@pytest.mark.parametrize('size', [0, -2, 1, True, 322])
def test_admission_rejects_invalid_or_oversized_pcm_without_consuming_capacity(size):
    from roma.realtime.playback_window import PlaybackWindow
    async def run():
        window = PlaybackWindow(max_audio_secs=.02)
        with pytest.raises(ValueError):
            await window.reserve(size)
        assert window.queued_bytes == 0 and not window.failed
    asyncio.run(run())


def test_raw_cached_audio_is_split_and_reserved_before_transport_admission():
    from pipecat.frames.frames import OutputAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection
    from roma.realtime.playback_window import PlaybackAdmissionProcessor, PlaybackWindow

    async def run():
        window = PlaybackWindow(max_audio_secs=.04)
        gate = PlaybackAdmissionProcessor(window)
        frames = []

        async def push(frame, direction):
            frames.append(frame)

        gate.push_frame = push
        await gate.process_frame(OutputAudioRawFrame(b'\x01\x00' * 320, 8000, 1), FrameDirection.DOWNSTREAM)
        assert [len(frame.audio) for frame in frames] == [320, 320]
        assert b''.join(frame.audio for frame in frames) == b'\x01\x00' * 320
        assert all(frame._roma_playback_generation == window.epoch for frame in frames)
        assert window.queued_bytes == 640

    asyncio.run(run())


def test_producer_reserved_frames_are_not_charged_twice_and_obsolete_frames_are_withdrawn():
    from pipecat.frames.frames import TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection
    from roma.realtime.playback_window import PlaybackAdmissionProcessor, PlaybackWindow

    async def run():
        window = PlaybackWindow(max_audio_secs=.02, enqueue_timeout_secs=.01)
        gate, seen = PlaybackAdmissionProcessor(window), []

        async def push(frame, direction):
            seen.append(frame)

        gate.push_frame = push
        frame = TTSAudioRawFrame(b'\0\0' * 160, 8000, 1)
        frame._roma_playback_generation = await window.reserve(320)
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        assert seen == [frame] and window.queued_bytes == 320
        window.reset()
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        assert seen == [frame] and window.queued_bytes == 0

    asyncio.run(run())


def test_installed_pipecat_clone_path_refunds_only_after_the_real_software_write():
    from types import SimpleNamespace

    from pipecat.frames.frames import EndFrame, TTSAudioRawFrame, TTSStoppedFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.serializers.twilio import TwilioFrameSerializer
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
    from roma.realtime.playback_window import PlaybackWindow, WindowedWebsocketTransport
    from starlette.websockets import WebSocketState

    async def run():
        entered, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        window = PlaybackWindow(max_audio_secs=.02)

        async def send_text(payload):
            entered.set()
            await release.wait()

        async def close():
            socket.client_state = socket.application_state = WebSocketState.DISCONNECTED

        socket = SimpleNamespace(client_state=WebSocketState.CONNECTED,
            application_state=WebSocketState.CONNECTED, send_text=send_text, close=close)
        transport = WindowedWebsocketTransport(socket, FastAPIWebsocketParams(
            audio_out_enabled=True, audio_out_sample_rate=8000, audio_out_end_silence_secs=0,
            serializer=TwilioFrameSerializer(stream_sid='MZ-test',
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False))), window=window)

        class Sink(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, TTSAudioRawFrame):
                    assert not hasattr(frame, '_roma_playback_generation')  # MediaSender cloned it.
                    delivered.set()
                await self.push_frame(frame, direction)

        task = PipelineTask(Pipeline([transport.output(), Sink()]), params=PipelineParams(
            audio_out_sample_rate=8000))
        running = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
        try:
            frame = TTSAudioRawFrame(b'\x01\x00' * 160, 8000, 1)
            frame._roma_playback_generation = await window.reserve(320)
            await task.queue_frames([frame, TTSStoppedFrame()])
            await asyncio.wait_for(entered.wait(), 1)
            assert window.queued_bytes == 320
            release.set()
            await asyncio.wait_for(delivered.wait(), 1)
            assert window.queued_bytes == 0
            await task.queue_frame(EndFrame())
            await asyncio.wait_for(running, 2)
        finally:
            release.set()
            if not running.done():
                await task.cancel()
                await asyncio.wait_for(running, 2)

    asyncio.run(run())


def test_raw_tail_is_sample_preserving_and_charged_for_the_transport_padded_frame():
    from pipecat.frames.frames import OutputAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection
    from roma.realtime.playback_window import PlaybackAdmissionProcessor, PlaybackWindow

    async def run():
        window, seen = PlaybackWindow(max_audio_secs=.02), []
        gate = PlaybackAdmissionProcessor(window)

        async def push(frame, direction):
            seen.append(frame)

        gate.push_frame = push
        original = b'\x01\x00' * 81
        await gate.process_frame(OutputAudioRawFrame(original, 8000, 1), FrameDirection.DOWNSTREAM)
        assert len(seen) == 1 and seen[0].audio == original + bytes(320 - len(original))
        assert window.queued_bytes == 320

    asyncio.run(run())


def test_real_cached_tts_producer_is_bounded_before_sanitizer_and_gate_queues():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pipecat.frames.frames import TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection
    from roma.realtime.local_speech import LocalTTSProcessor
    from roma.realtime.playback_window import (
        PlaybackAdmissionProcessor,
        PlaybackPressure,
        PlaybackWindow,
    )
    from roma.realtime.tts import TTSAudioSanitizer

    async def run():
        closed, admitted = [], []

        async def close():
            closed.append(True)

        window = PlaybackWindow(max_audio_secs=.02, enqueue_timeout_secs=.01, on_pressure=close)
        provider = SimpleNamespace(synthesize=AsyncMock())
        speaker = LocalTTSProcessor(provider, phrases=SimpleNamespace(lookup=lambda _: b'\x01\x00' * 321))
        speaker.playback_window = window
        sanitizer, gate = TTSAudioSanitizer(), PlaybackAdmissionProcessor(window)

        async def into_sanitizer(frame, direction=FrameDirection.DOWNSTREAM):
            await sanitizer.process_frame(frame, direction)

        async def into_gate(frame, direction):
            if isinstance(frame, TTSAudioRawFrame):
                assert frame._roma_playback_generation == window.epoch
            await gate.process_frame(frame, direction)

        async def sink(frame, direction):
            if isinstance(frame, TTSAudioRawFrame):
                admitted.append(frame)

        speaker.push_frame, sanitizer.push_frame, gate.push_frame = into_sanitizer, into_gate, sink
        with pytest.raises(PlaybackPressure):
            await speaker.speak('Namaste.')
        assert len(admitted) == 1 and len(admitted[0].audio) == 320
        assert window.queued_bytes == 320 and window.failed
        assert closed == [True]
        provider.synthesize.assert_not_awaited()

    asyncio.run(run())
