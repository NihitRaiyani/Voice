import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TranscriptionFrame
from roma.providers.ai.contracts import AudioFormat, STTResult


def test_local_stt_resamples_whole_utterance_and_rejects_late_result():
    from roma.realtime.local_speech import LocalSTTProcessor
    async def run():
        provider = SimpleNamespace(transcribe=AsyncMock(return_value=STTResult('ટેસ્ટ કોર્સ', 'gu')))
        processor = LocalSTTProcessor(provider)
        output = []
        async def push(frame, *args): output.append(frame)
        processor.push_frame = push
        await processor.decode(b'\x01\x00'*800, 8000)
        request = provider.transcribe.call_args.args[0]
        assert request.format == AudioFormat(16000) and len(request.audio) >= 3200
        assert any(isinstance(frame, TranscriptionFrame) for frame in output)
        provider.transcribe.reset_mock()
        async def stale(request):
            processor.epoch.advance()
            return STTResult('stale')
        provider.transcribe.side_effect = stale
        output.clear()
        with pytest.raises(asyncio.CancelledError):
            await processor.decode(b'\x01\x00'*800, 8000)
        assert not output
    asyncio.run(run())


def test_local_tts_filters_every_phrase_even_on_cache_hit():
    from roma.realtime.local_speech import LocalTTSProcessor
    async def run():
        calls=[]
        class Cache:
            def lookup(self, text):
                calls.append(text)
                return b'\x00\x00'*160
        provider = SimpleNamespace(synthesize=AsyncMock())
        speech = LocalTTSProcessor(provider, phrases=Cache())
        speech.push_frame = AsyncMock()
        await speech.speak('Course fees 50000 hai.')
        assert 'Course fees 50000 hai.' not in calls
        assert calls and not provider.synthesize.called
    asyncio.run(run())


def test_slow_websocket_send_closes_instead_of_accumulating_output():
    from roma.realtime.media_limits import BoundedMediaWebSocket
    from starlette.websockets import WebSocketDisconnect
    async def run():
        original=SimpleNamespace(send_text=AsyncMock(side_effect=lambda *a:None),close=AsyncMock())
        async def wait(*args): await asyncio.Event().wait()
        original.send_text.side_effect=wait
        socket=BoundedMediaWebSocket(original,send_timeout_secs=.01)
        with pytest.raises(WebSocketDisconnect):
            await socket.send_text('frame')
        original.close.assert_awaited_once_with(code=1013)
    asyncio.run(run())


def test_failed_speech_attempts_are_recorded_as_unknown_not_zero_cost():
    from roma.realtime.local_speech import LocalSTTProcessor, LocalTTSProcessor
    async def run():
        provider = SimpleNamespace(transcribe=AsyncMock(side_effect=RuntimeError('private')), synthesize=AsyncMock(side_effect=RuntimeError('private')))
        asr, tts = LocalSTTProcessor(provider), LocalTTSProcessor(provider)
        asr.usage_sink, tts.usage_sink = AsyncMock(), AsyncMock()
        with pytest.raises(RuntimeError):
            await asr.decode(b'\x01\x00'*800, 8000)
        asr.usage_sink.assert_awaited_once()
        with pytest.raises(RuntimeError):
            await tts.speak('कोर्स में प्रैक्टिकल सिखाया जाता है।')
        tts.usage_sink.assert_awaited_once()
        assert tts.usage_sink.call_args.kwargs['audio_seconds'] is None
    asyncio.run(run())


def test_native_voice_rendering_is_exact_for_deferral_and_controller_confirmation_sentence():
    from roma.domain.appointments.booking import confirmed_line
    from roma.domain.conversation.state import CallState, spoken_slot
    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
    from roma.services.voice_pronunciation import render_voice_sentence
    state = CallState()
    assert render_voice_sentence(NO_EVIDENCE_LINE,state) == 'इसकी अप्रूव्ड जानकारी के लिए काउन्सलिंग टीम से पूछ लीजिए।'
    assert render_voice_sentence('SEO, social media marketing aur Google Ads, teen module hain.',state).startswith('एस ई ओ')
    state.booking_required=True
    state.appointment_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    state.locked_slot='2026-11-02T11:00:00+05:30'
    canonical = confirmed_line(spoken_slot(state.locked_slot),state.branch)
    sentence = canonical.split('. ')[0]+'.'
    rendered = render_voice_sentence(sentence,state)
    assert rendered != sentence and 'कन्फर्म' in rendered
    state.appointment_id=None
    assert render_voice_sentence(sentence,state) == sentence


def test_failed_tts_cannot_emit_cached_fallback_after_epoch_changes():
    from pipecat.frames.frames import TextFrame
    from pipecat.processors.frame_processor import FrameDirection
    from roma.realtime.local_speech import LocalTTSProcessor
    async def run():
        processor = LocalTTSProcessor(SimpleNamespace(), phrases=SimpleNamespace(lookup=lambda _: b"\x01\x00"*160))
        async def obsolete(_):
            processor.epoch.advance()
            raise RuntimeError("late failure")
        processor.speak=obsolete
        processor.push_frame=AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await processor.process_frame(TextFrame("Namaste."),FrameDirection.DOWNSTREAM)
        processor.push_frame.assert_not_awaited()
    asyncio.run(run())


def test_cancelled_native_request_preserves_cancel_when_usage_sink_fails():
    from roma.realtime.local_speech import LocalSTTProcessor, LocalTTSProcessor
    async def run():
        async def cancelled(*_):
            raise asyncio.CancelledError()
        sink=AsyncMock(side_effect=RuntimeError("accounting down"))
        asr=LocalSTTProcessor(SimpleNamespace(transcribe=cancelled))
        tts=LocalTTSProcessor(SimpleNamespace(synthesize=cancelled))
        asr.usage_sink=tts.usage_sink=sink
        for operation in (lambda: asr.decode(b"\x01\x00"*800,8000),lambda: tts.speak("कोर्स में प्रैक्टिकल सिखाया जाता है।")):
            with pytest.raises(asyncio.CancelledError):
                await operation()
    asyncio.run(run())


def test_socket_close_is_bounded_when_the_peer_never_completes_handshake():
    from roma.realtime.media_limits import BoundedMediaWebSocket
    from starlette.websockets import WebSocketDisconnect
    async def run():
        async def blocked(*_,**__): await asyncio.Event().wait()
        socket=BoundedMediaWebSocket(SimpleNamespace(send_text=blocked,close=blocked),send_timeout_secs=.01)
        with pytest.raises(WebSocketDisconnect):
            await asyncio.wait_for(socket.send_text("frame"),.7)
    asyncio.run(run())


def test_media_send_failure_explicitly_cancels_the_pipeline_before_closing():
    from roma.realtime.media_limits import BoundedMediaWebSocket
    from starlette.websockets import WebSocketDisconnect
    async def run():
        order=[]
        async def cancel(): order.append('cancel')
        async def close(**_): order.append('close')
        socket=BoundedMediaWebSocket(SimpleNamespace(send_text=AsyncMock(side_effect=RuntimeError('disconnected')),close=close),on_failure=cancel)
        with pytest.raises(WebSocketDisconnect):
            await socket.send_text('frame')
        assert order == ['cancel','close']
    asyncio.run(run())


def test_local_deferral_is_primed_before_calls_and_served_without_synthesis(tmp_path):
    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
    from roma.providers.ai.contracts import TTSResult
    from roma.realtime.local_speech import LocalTTSProcessor, prime_local_deferral
    from roma.realtime.phrasecache import PhraseCache
    async def run():
        cache=PhraseCache(tmp_path/'missing')
        provider=SimpleNamespace(synthesize=AsyncMock(return_value=TTSResult(b'\x01\x00'*160,AudioFormat(8000))))
        await prime_local_deferral(cache,provider)
        assert provider.synthesize.call_args.args[0].text == 'इसकी अप्रूव्ड जानकारी के लिए काउन्सलिंग टीम से पूछ लीजिए।'
        provider.synthesize.reset_mock()
        speaker=LocalTTSProcessor(provider,phrases=cache)
        speaker.push_frame=AsyncMock()
        await speaker.speak(NO_EVIDENCE_LINE)
        provider.synthesize.assert_not_awaited()
        assert speaker.phrase_hits == 1
    asyncio.run(run())
