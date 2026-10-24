"""StageControllerProcessor (docs/03, docs/11): each user turn it swaps the context system
message to the current stage's prompt and pushes an LLMUpdateSettingsFrame carrying the
stage's max_tokens — forwarding the context frame only after. The opening turn (no user
message) leaves the seeded open prompt untouched. Driven via Pipecat's run_test harness."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from roma.domain.appointments.slots import DiscoveryValue
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.prompts import assemble_system_prompt, stage_max_tokens
from roma.domain.conversation.state import CallState
from roma.realtime.stage_controller import StageControllerProcessor

NOW = datetime(2026, 7, 25, 10, 0, tzinfo=IST)


def _ctx(stage: str, user_text: "str | None"):
    state_vars = CallState(branch="Vadodara", lead_name="ji").as_prompt_vars()
    messages = [{"role": "system", "content": assemble_system_prompt(state_vars, stage)}]
    if user_text is not None:
        messages.append({"role": "user", "content": user_text})
    return LLMContext(messages=messages)


def _system(ctx) -> str:
    return next(m["content"] for m in ctx.get_messages() if m["role"] == "system")


async def _fake_discovery(client, text, slot_name):
    return DiscoveryValue(value=text, confidence=0.95)


def _run(proc, ctx):
    down, _ = asyncio.run(run_test(proc, frames_to_send=[LLMContextFrame(ctx)]))
    return down


def test_open_affirmation_swaps_to_discover_prompt_and_sets_max_tokens():
    state = CallState(call_sid="CA1", stage="open", branch="Vadodara", lead_name="ji")
    proc = StageControllerProcessor(state, client=None, now_fn=lambda: NOW)
    ctx = _ctx("open", "haan ji bilkul")

    down = _run(proc, ctx)

    assert state.stage == "discover"
    assert "STAGE: DISCOVER" in _system(ctx)
    settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
    assert settings and settings[0].delta.max_tokens == stage_max_tokens("discover")


def test_opening_turn_without_user_message_is_left_untouched():
    state = CallState(call_sid="CA2", stage="open")
    proc = StageControllerProcessor(state, client=None, now_fn=lambda: NOW)
    ctx = _ctx("open", None)
    before = _system(ctx)

    down = _run(proc, ctx)

    assert state.stage == "open"
    assert _system(ctx) == before
    assert not [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]


def test_discovery_turn_fills_slot_and_re_sets_stage_prompt():
    state = CallState(call_sid="CA3", stage="discover", lead_name="Asha")
    proc = StageControllerProcessor(
        state, client=object(), now_fn=lambda: NOW, extract_discovery=_fake_discovery
    )
    ctx = _ctx("discover", "12th pass")

    down = _run(proc, ctx)

    assert state.current_status == "12th pass"
    assert "STAGE: DISCOVER" in _system(ctx)
    settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
    assert settings and settings[0].delta.max_tokens == stage_max_tokens("discover")


def test_context_frame_is_forwarded_downstream():
    state = CallState(call_sid="CA4", stage="open")
    proc = StageControllerProcessor(state, client=None, now_fn=lambda: NOW)
    down = _run(proc, _ctx("open", "haan"))
    assert any(isinstance(f, LLMContextFrame) for f in down)


def test_user_message_left_intact_only_system_is_swapped():
    state = CallState(call_sid="CA5", stage="open")
    proc = StageControllerProcessor(state, client=None, now_fn=lambda: NOW)
    ctx = _ctx("open", "haan ji")
    _run(proc, ctx)
    users = [m for m in ctx.get_messages() if m["role"] == "user"]
    assert users and users[-1]["content"] == "haan ji"


def test_completed_turn_is_checkpointed_before_wording_settings_are_forwarded():
    from roma.services.live_call_state import CheckpointMilestone

    async def run():
        state = CallState(call_sid="CA-checkpoint", stage="open")
        observed = []
        async def checkpoint(saved, milestone):
            assert saved.turn_count == 1 and saved.stage == "discover"
            observed.append(milestone)
        store = SimpleNamespace(save=AsyncMock(), checkpoint=checkpoint)
        proc = StageControllerProcessor(state, store=store, now_fn=lambda: NOW)
        async def push(frame, direction):
            assert observed == [CheckpointMilestone.TURN_COMPLETED]
        proc.push_frame = push
        await proc._drive(LLMContextFrame(_ctx("open", "haan ji")))
        assert observed == [CheckpointMilestone.TURN_COMPLETED]
    asyncio.run(run())


@pytest.mark.parametrize("failure_at", ["advance", "checkpoint"])
def test_checkpoint_voice_failure_cannot_continue_to_wording(monkeypatch, failure_at):
    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
    from roma.realtime import stage_controller as module

    async def run():
        state = CallState(call_sid="CA-checkpoint", stage="open")
        store = SimpleNamespace(save=AsyncMock(), checkpoint=AsyncMock())
        if failure_at == "advance":
            monkeypatch.setattr(module, "advance_turn", AsyncMock(side_effect=VoiceFailure(VoiceErrorCode.DB)))
        else:
            store.checkpoint.side_effect = VoiceFailure(VoiceErrorCode.DB)
        proc = StageControllerProcessor(state, store=store, now_fn=lambda: NOW)
        proc.push_frame = AsyncMock()
        with pytest.raises(VoiceFailure):
            await proc._drive(LLMContextFrame(_ctx("open", "haan ji")))
        proc.push_frame.assert_not_awaited()
    asyncio.run(run())


def test_repeated_context_cannot_bypass_a_failed_checkpoint_or_advance_twice():
    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure

    async def run():
        state = CallState(call_sid="CA-checkpoint-repeat", stage="open")
        store = SimpleNamespace(save=AsyncMock(), checkpoint=AsyncMock(side_effect=VoiceFailure(VoiceErrorCode.DB)))
        proc = StageControllerProcessor(state, store=store, now_fn=lambda: NOW)
        proc.push_frame = AsyncMock()
        frame = LLMContextFrame(_ctx("open", "haan ji"))
        for _ in range(2):
            with pytest.raises(VoiceFailure):
                await proc._drive(frame)
        assert state.turn_count == 1 and state.stage == "discover"
        assert store.checkpoint.await_count == 2
        proc.push_frame.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("win", [False, True])
def test_identical_context_recovery_finishes_prompt_settings_and_win_without_readvance(monkeypatch, win):
    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
    from roma.realtime import stage_controller as module

    async def run():
        state = CallState(call_sid="CA-checkpoint-recovery", stage="open")
        store = SimpleNamespace(save=AsyncMock(), checkpoint=AsyncMock(side_effect=[VoiceFailure(VoiceErrorCode.DB), None]))
        if win:
            async def advance(*args, **kwargs):
                state.stage = module.ConversationStage.CLOSE
                state.turn_count += 1
                return SimpleNamespace(win=True)
            driver = AsyncMock(side_effect=advance)
        else:
            driver = AsyncMock(wraps=module.advance_turn)
        monkeypatch.setattr(module, "advance_turn", driver)
        proc = StageControllerProcessor(state, store=store, now_fn=lambda: NOW)
        proc.push_frame = AsyncMock()
        context = _ctx("open", "haan ji")
        frame = LLMContextFrame(context)
        with pytest.raises(VoiceFailure):
            await proc._drive(frame)
        assert "STAGE: OPEN" in _system(context)
        await proc._drive(frame)
        expected = "close" if win else "discover"
        assert f"STAGE: {expected.upper()}" in _system(context)
        driver.assert_awaited_once()
        assert state.turn_count == 1 and proc.won is win
        proc.push_frame.assert_awaited_once()
        assert proc.push_frame.await_args.args[0].delta.max_tokens == stage_max_tokens(expected)
    asyncio.run(run())


def test_turn_budget_starts_only_on_new_context_not_pending_checkpoint_retry():
    from unittest.mock import Mock

    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure

    async def run():
        state = CallState(call_sid="CA-turn-budget", stage="open")
        store = SimpleNamespace(save=AsyncMock(), checkpoint=AsyncMock(side_effect=[VoiceFailure(VoiceErrorCode.DB), None, None]))
        client = SimpleNamespace(begin_turn=Mock())
        proc = StageControllerProcessor(state, client=client, store=store, now_fn=lambda: NOW,
                                        extract_discovery=_fake_discovery)
        proc.push_frame = AsyncMock()
        frame = LLMContextFrame(_ctx("open", "haan ji"))
        with pytest.raises(VoiceFailure):
            await proc._drive(frame)
        await proc._drive(frame)
        client.begin_turn.assert_called_once_with()
        await proc._drive(LLMContextFrame(_ctx("discover", "Amit")))
        assert client.begin_turn.call_count == 2
    asyncio.run(run())
