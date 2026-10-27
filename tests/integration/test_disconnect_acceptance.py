"""Disconnect closes pending stages without late transcript or speaker output."""
import asyncio

import pytest
from roma.services.streaming_voice import StreamingInput
from starlette.websockets import WebSocketDisconnect
from tests.services.test_streaming_voice import StreamLLM, stream_voice
from tests.services.test_voice_prototype import transcript


@pytest.mark.parametrize('phase', ['asr', 'generation', 'synthesis', 'playback'])
def test_transport_disconnect_cancels_pending_stage_without_task_or_output_leak(phase):
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()
        provider = StreamLLM()
        voice = stream_voice(provider)
        before = set(asyncio.all_tasks())
        released = []

        async def release():
            released.append(True)

        voice.release_on_disconnect = release

        async def pending(*args, **kwargs):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                stopped.set()

        if phase == 'generation':
            async def generate(request):
                await pending()
                yield  # asynchronous provider contract
            provider.generate = generate
        elif phase == 'synthesis':
            voice.synthesis.synthesize = pending
        elif phase == 'playback':
            voice.speaker.play = pending

        class Speech:
            class Scorer:
                def probability(self, pcm):
                    return 0.0
                def reset(self):
                    pass
            scorer = Scorer()
            async def feed(self, pcm):
                if phase == 'asr':
                    await pending()
                return (transcript('haan'),)
            async def finish(self, **kwargs):
                return ()
            def reset(self):
                pass

        async def source():
            yield b'\0\0' * 320
            await started.wait()
            raise WebSocketDisconnect()

        await asyncio.wait_for(StreamingInput(voice).run(source(), Speech()), .5)
        assert stopped.is_set() and voice.disconnected and released == [True]
        assert not voice.speaker.played
        assert set(asyncio.all_tasks()) == before
        if phase == 'asr':
            assert voice.state.turn_count == 0
    asyncio.run(run())


def test_normal_audio_source_eof_drains_pending_recognition_and_final_response():
    async def run():
        voice = stream_voice(StreamLLM())
        class Speech:
            class Scorer:
                def probability(self, pcm):
                    return 0.0
                def reset(self):
                    pass
            scorer = Scorer()
            async def feed(self, pcm):
                await asyncio.sleep(.01)
                return (transcript('haan'),)
            async def finish(self, **kwargs):
                return ()
            def reset(self):
                pass
        async def source():
            yield b'\0\0' * 320
        outcomes = await asyncio.wait_for(StreamingInput(voice).run(source(), Speech()), .5)
        assert len(outcomes) == 1 and outcomes[0].status == 'played'
        assert voice.speaker.played and not voice.disconnected
    asyncio.run(run())
