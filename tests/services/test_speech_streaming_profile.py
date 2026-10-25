"""Audited sentence delivery and retained readiness in the local prototype."""
import asyncio

from roma.services.streaming_text import PunctuationChunker


def test_short_comma_waits_for_a_sentence_and_long_clause_can_stream():
    chunker = PunctuationChunker()
    assert chunker.feed("नमस्ते, ") == ()
    assert chunker.feed("आपका नाम क्या है?") == ("नमस्ते, आपका नाम क्या है?",)
    assert chunker.feed("आप अपनी पसंद के विषय के बारे में बताइए, ") == (
        "आप अपनी पसंद के विषय के बारे में बताइए,",
    )


def test_local_profile_streams_by_default_but_mock_and_buffered_override_do_not():
    from scripts.voice_prototype import parser, sentence_streaming
    assert sentence_streaming(parser().parse_args(["--mode", "local"]))
    assert not sentence_streaming(parser().parse_args([]))
    assert not sentence_streaming(parser().parse_args(["--mode", "local", "--buffered-speech"]))
    assert sentence_streaming(parser().parse_args(["--streaming"]))


def test_next_sentence_synthesizes_while_previous_sentence_is_playing():
    from tests.services.test_streaming_voice import StreamLLM, stream_voice
    from tests.services.test_voice_prototype import transcript

    async def run():
        voice = stream_voice(StreamLLM())
        first_playing, second_synthesized = asyncio.Event(), asyncio.Event()
        calls = 0
        original = voice.synthesis.synthesize
        async def synthesis(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                await asyncio.wait_for(first_playing.wait(), 1)
                second_synthesized.set()
            return await original(*args, **kwargs)
        plays = 0
        async def play(result):
            nonlocal plays
            plays += 1
            if plays == 1:
                first_playing.set()
                await asyncio.wait_for(second_synthesized.wait(), 1)
        voice.synthesis.synthesize, voice.speaker.play = synthesis, play
        result = await voice.respond(transcript("haan"))
        assert result.status == "played" and calls == plays == 2
    asyncio.run(run())


def test_readiness_retains_the_active_voice_instead_of_another_throwaway_synthesis():
    from scripts.voice_prototype import ready_local_models

    async def run():
        events = []
        class STT:
            async def transcribe(self, request):
                events.append("asr-ready")
        class TTS:
            async def warmup(self, request):
                assert request.language == "hi" and request.text == "नमस्ते।"
                events.append("resident-warmup")
            async def synthesize(self, request):
                raise AssertionError("Do not discard another worker after readiness")
        class Scorer:
            def probability(self, pcm):
                events.append("vad-ready")
            def reset(self):
                events.append("vad-reset")
        await ready_local_models(STT(), TTS(), Scorer())
        assert events == ["asr-ready"] * 3 + ["resident-warmup", "vad-ready", "vad-reset"]
    asyncio.run(run())
