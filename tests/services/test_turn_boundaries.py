import asyncio
from contextlib import AsyncExitStack

import pytest


def test_harness_drives_real_speech_router_controller_and_half_duplex_device():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            harness = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે"]))
            result = await harness.run_audio(harness.audio((1, 2000), (0, 1200)))
            assert result.status == "played"
            assert len(harness.asr.requests) == 1
            assert harness.state.turn_count == 1
            assert harness.state.language_router.current.value == "GUJARATI"
            assert harness.backend.software_muted_before_playback
            assert harness.clock() > 2

    asyncio.run(run())


def test_turn_policy_rejects_batch_gap_at_or_above_endpoint_window():
    import pytest
    from tests.support.voice_turns import VoiceHarness

    with pytest.raises(ValueError, match="batch gap"):
        VoiceHarness(policy_values={"batch_gap_min_ms": 512})




@pytest.mark.parametrize("pause", [256, 384, 448, 512, 640, 800])
def test_pause_matrix_never_discards_the_second_sentence(pause):
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            harness = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે", "I would like course information"]))
            await harness.drain_audio(harness.audio((1, 2000), (0, pause), (2, 2000), (0, 1400)))
            captured = b"".join(request.audio for request in harness.asr.requests)
            assert captured.count(b"\x01\0") == 2000 * 16
            assert captured.count(b"\x02\0") == 2000 * 16
            assert 1 <= len(harness.asr.requests) <= 2
            assert harness.policy.batch_gap_min_ms < harness.policy.endpoint_silence_ms

    asyncio.run(run())


def test_continuation_grace_merges_800ms_pause_before_any_asr_or_playback():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            harness = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે I would like course information"]))
            await harness.run_audio(harness.audio((1, 2000), (0, 800), (2, 2000), (0, 1400)))
            assert len(harness.asr.requests) == 1
            assert harness.asr.requests[0].audio.count(b"\x01\0") == 2000 * 16
            assert harness.asr.requests[0].audio.count(b"\x02\0") == 2000 * 16
            assert harness.backend.software_muted_before_playback
            assert harness.clock() < 7

    asyncio.run(run())


def test_grace_wait_is_bounded_and_respects_the_session_ceiling():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            short = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે"], policy_values={"continuation_grace_ms": 0}))
            long = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે"]))
            pcm = short.audio((1, 2000), (0, 1400))
            await short.capture(pcm)
            await long.capture(pcm)
            assert 0.30 <= long.clock() - short.clock() <= 0.34
            long.clock.now = 300
            with pytest.raises(RuntimeError, match="five-minute"):
                await long.voice.respond(long.last_transcript)

    asyncio.run(run())


def test_capture_deadline_flushes_partial_source_audio_before_reset():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            harness = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે"]))
            result = await harness.run_audio(harness.audio((1, 5000)), max_seconds=1)
            assert result.status == "played"
            assert harness.last_transcript.reason == "deadline"
            assert harness.last_transcript.duration_ms <= 1000
            assert b"".join(r.audio for r in harness.asr.requests) == harness.audio((1, 1000))
            assert harness.backend.input_open
            assert harness.device.capture_mode == "MUTED_HALF_DUPLEX"

    asyncio.run(run())


def test_empty_asr_at_capture_deadline_has_a_distinct_safe_code():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            harness = await devices.enter_async_context(VoiceHarness(scripts=[""]))
            with pytest.raises(RuntimeError, match="CAPTURE-DEADLINE-NO-TRANSCRIPT"):
                await harness.capture(harness.audio((1, 5000)), max_seconds=1)
            assert harness.state.turn_count == 0 and harness.backend.input_open
            assert harness.device.capture_mode == "MUTED_HALF_DUPLEX"

    asyncio.run(run())


def test_deadline_preserves_the_hard_30_second_input_cap():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            harness = await devices.enter_async_context(VoiceHarness(scripts=["મને અભ્યાસ વિશે જાણવું છે"] * 2))
            await harness.capture(harness.audio((1, 31000)), max_seconds=30)
            assert b"".join(r.audio for r in harness.asr.requests) == harness.audio((1, 30000))
            assert harness.last_transcript.reason == "deadline"

    asyncio.run(run())

@pytest.mark.parametrize("route", list(__import__('roma.domain.conversation.language', fromlist=['Language']).Language))
def test_audio_gap_can_recover_unexpected_english_from_every_saved_route(route):
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            h = await devices.enter_async_context(VoiceHarness(route=route, structured_decode=True))
            await h.run_audio(h.audio((1, 2000), (0, 800), (2, 2000), (0, 1400)))
            assert h.asr.batch_calls and not h.asr.single_calls
            assert 'I would like course information' in h.last_transcript.text
            assert b''.join(h.asr.batch_calls[0]) == h.asr.requests[0].audio
    asyncio.run(run())


def test_low_english_probability_keeps_native_script_and_no_gap_keeps_exact_decode():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            h = await devices.enter_async_context(VoiceHarness(structured_decode=True, english_probability=0.2))
            await h.capture(h.audio((1, 2000), (0, 800), (2, 2000), (0, 1400)))
            assert 'I would' not in h.last_transcript.text
            plain = await devices.enter_async_context(VoiceHarness(structured_decode=True))
            await plain.capture(plain.audio((1, 3000), (0, 1400)))
            assert not plain.asr.batch_calls
            assert plain.asr.single_calls == [plain.asr.requests[0].audio]
    asyncio.run(run())


def test_saved_route_recovers_only_after_two_matching_informative_turns():
    from roma.domain.conversation.language import Language
    from roma.domain.conversation.state import CallState
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            h = await devices.enter_async_context(VoiceHarness(scripts=['I would like course information'] * 2))
            h.state.language_router.recent = [Language.GUJARATI] * 2
            pcm = h.audio((1, 2000), (0, 1400))
            await h.run_audio(pcm)
            assert h.state.language_router.current == Language.GUJARATI
            restored = CallState.from_dict(h.state.to_dict())
            h.state = h.speech.call_state = h.voice.state = restored
            await h.run_audio(pcm)
            assert h.state.language_router.current == Language.ENGLISH
    asyncio.run(run())


def test_batch_minimum_turn_is_configurable_and_short_turns_keep_single_call():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            h = await devices.enter_async_context(VoiceHarness(scripts=['મને અભ્યાસ વિશે જાણવું છે'], structured_decode=True,
                             policy_values={'batch_min_turn_ms': 10000}))
            await h.capture(h.audio((1, 2000), (0, 800), (2, 2000), (0, 1400)))
            assert not h.asr.batch_calls
            assert h.asr.single_calls == [h.asr.requests[0].audio]
    asyncio.run(run())


def test_all_rejected_batch_rows_leave_controller_unchanged_with_distinct_asr_code():
    from tests.providers.ai.test_batch_rejection import row
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            h = await devices.enter_async_context(VoiceHarness(structured_decode=True))
            h.asr.batch_rows = [row(no_speech_prob=0.9), row(compression_ratio=3)]
            result = await h.run_audio(h.audio((1, 2000), (0, 800), (2, 2000), (0, 1400)))
            assert result.error_code == 'ASR-BATCH-REJECTED'
            assert result.status == 'failed' and h.state.turn_count == 0
            assert not h.backend.software_muted_before_playback
    asyncio.run(run())


def test_speech_to_first_write_budget_excludes_full_playback_duration():
    from tests.support.voice_turns import VoiceHarness

    async def run():
        async with AsyncExitStack() as devices:
            h = await devices.enter_async_context(VoiceHarness(scripts=['મને અભ્યાસ વિશે જાણવું છે']))
            result = await h.run_audio(h.audio((1, 2000), (0, 1400)))
            timings = result.timings
            assert h.last_transcript.speech_end_at is not None
            expected = (h.backend.first_write_at - h.last_transcript.speech_end_at) * 1000
            assert timings['speech_to_first_audio_ms'] == pytest.approx(expected)
            assert 800 <= timings['endpoint_wait_ms'] <= 860
            assert timings['asr_ms'] == pytest.approx(5)
            assert {'llm_ms', 'safety_ms', 'tts_ms', 'playback_start_ms'} <= timings.keys()
            assert result.budget == {'configured': False, 'over_budget': []}
    asyncio.run(run())


def test_turn_budget_has_no_threshold_until_explicitly_configured():
    from roma.domain.audio.turn_budget import TurnBudget
    assert TurnBudget().evaluate({'asr_ms': 99999}) == {'configured': False, 'over_budget': []}
    assert TurnBudget({'asr_ms': 10}).evaluate({'asr_ms': 11})['over_budget'] == ['asr_ms']
    with pytest.raises(ValueError):
        TurnBudget({'asr_ms': float('nan')})
