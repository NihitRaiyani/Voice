"""PhaseControllerProcessor (docs/03, docs/11): each user turn it swaps the context system
message to the current phase's prompt and pushes an LLMUpdateSettingsFrame carrying the
phase's max_tokens — forwarding the context frame only after. The opening turn (no user
message) leaves the seeded P1 prompt untouched. Driven via Pipecat's run_test harness."""

import asyncio
from datetime import datetime

from pipecat.frames.frames import LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

from roma.controller.slots import DiscoveryValue
from roma.controller.state import CallState
from roma.controller.timeresolve import IST
from roma.llm.prompts import assemble_system_prompt, phase_max_tokens
from roma.telephony.phase_controller import PhaseControllerProcessor

NOW = datetime(2026, 7, 25, 10, 0, tzinfo=IST)


def _ctx(phase: str, user_text: "str | None"):
    state_vars = CallState(branch="Vadodara", lead_name="ji").as_prompt_vars()
    messages = [{"role": "system", "content": assemble_system_prompt(state_vars, phase)}]
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


def test_p1_affirmation_swaps_to_p2_prompt_and_sets_max_tokens():
    state = CallState(call_sid="CA1", phase="p1_open", branch="Vadodara", lead_name="ji")
    proc = PhaseControllerProcessor(state, client=None, now_fn=lambda: NOW)
    ctx = _ctx("p1_open", "haan ji bilkul")

    down = _run(proc, ctx)

    assert state.phase == "p2_discover"
    assert "PHASE: DISCOVER" in _system(ctx)
    settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
    assert settings and settings[0].delta.max_tokens == phase_max_tokens("p2_discover")


def test_opening_turn_without_user_message_is_left_untouched():
    state = CallState(call_sid="CA2", phase="p1_open")
    proc = PhaseControllerProcessor(state, client=None, now_fn=lambda: NOW)
    ctx = _ctx("p1_open", None)
    before = _system(ctx)

    down = _run(proc, ctx)

    assert state.phase == "p1_open"
    assert _system(ctx) == before
    assert not [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]


def test_discovery_turn_fills_slot_and_re_sets_phase_prompt():
    state = CallState(call_sid="CA3", phase="p2_discover", lead_name="Asha")
    proc = PhaseControllerProcessor(
        state, client=object(), now_fn=lambda: NOW, extract_discovery=_fake_discovery
    )
    ctx = _ctx("p2_discover", "12th pass")

    down = _run(proc, ctx)

    assert state.current_status == "12th pass"
    assert "PHASE: DISCOVER" in _system(ctx)
    settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
    assert settings and settings[0].delta.max_tokens == phase_max_tokens("p2_discover")


def test_context_frame_is_forwarded_downstream():
    state = CallState(call_sid="CA4", phase="p1_open")
    proc = PhaseControllerProcessor(state, client=None, now_fn=lambda: NOW)
    down = _run(proc, _ctx("p1_open", "haan"))
    assert any(isinstance(f, LLMContextFrame) for f in down)


def test_user_message_left_intact_only_system_is_swapped():
    state = CallState(call_sid="CA5", phase="p1_open")
    proc = PhaseControllerProcessor(state, client=None, now_fn=lambda: NOW)
    ctx = _ctx("p1_open", "haan ji")
    _run(proc, ctx)
    users = [m for m in ctx.get_messages() if m["role"] == "user"]
    assert users and users[-1]["content"] == "haan ji"
