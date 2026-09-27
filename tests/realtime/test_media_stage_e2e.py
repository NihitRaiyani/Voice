"""End-to-end drive of the stage controller across a whole call (docs/03).

Feeds a scripted open→close conversation through the real StageControllerProcessor (real machine,
real objection lexicon, real prompt assembler; only slot extraction is faked). Proves the
system prompt swapped into the context matches the stage the machine landed on at EVERY turn,
that an objection detour routes pivot→objection→pivot, and that a confirmed readback fires the WIN.

This is the offline stand-in for a live call; it exercises the exact integration seam
(user aggregator → StageController → LLM) without a Twilio socket.

One fresh processor per turn (run_test starts+ends the processor it drives, so a processor
is single-use) — the shared CallState carries the call's progress across turns."""

import asyncio
from datetime import datetime

from pipecat.frames.frames import LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from roma.domain.appointments.slots import DiscoveryValue, TimeSlot
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.prompts import assemble_system_prompt, stage_max_tokens
from roma.domain.conversation.state import CallState
from roma.realtime.stage_controller import StageControllerProcessor

NOW = datetime(2026, 7, 25, 10, 0, tzinfo=IST)
VARS = {"branch": "Vadodara", "lead_name": "ji"}


async def _fake_discovery(client, text, slot_name):
    return DiscoveryValue(value=text, confidence=0.95)


async def _fake_time(client, text, *, offered=None):
    if "accept" in text:
        return TimeSlot(accepted=True, day_offset=1, hour=5, period="evening", confidence=0.95)
    if "confirm" in text:
        return TimeSlot(readback_confirmed=True, confidence=0.95)
    return TimeSlot(confidence=0.0)


def _system(ctx):
    return next(m["content"] for m in ctx.get_messages() if m["role"] == "system")


def _drive_turn(state, messages, user_text):
    """Fresh processor, one user turn. Returns (context, downstream_frames, processor)."""
    proc = StageControllerProcessor(
        state,
        client=object(),
        now_fn=lambda: NOW,
        extract_discovery=_fake_discovery,
        extract_time=_fake_time,
    )
    ctx = LLMContext(messages=[*messages, {"role": "user", "content": user_text}])
    down, _ = asyncio.run(run_test(proc, frames_to_send=[LLMContextFrame(ctx)]))
    return ctx, down, proc


def test_full_call_prompt_tracks_the_stage_the_machine_lands_on():
    state = CallState(call_sid="CA_e2e", stage="open", **VARS)
    messages = [
        {"role": "system", "content": assemble_system_prompt(state.as_prompt_vars(), "open")}
    ]

    script = [
        ("haan ji, digital marketing ke liye hi baat ki thi", "discover"),
        ("bcom kiya hai", "discover"),
        ("2020 mein", "discover"),
        ("abhi job kar raha hoon", "discover"),
        ("Vadodara", "value"),
        ("achha, samajh gayi", "value"),
        ("ye course kya hai", "value"),
        ("kitne mahine ka hai", "value"),
        ("iske baad kya kar sakte hain", "structure"),
        ("theek hai", "pivot"),
        ("Monday accept hai", "close"),
    ]

    for utterance, expected_stage in script:
        ctx, down, _ = _drive_turn(state, messages, utterance)

        assert state.stage == expected_stage, f"after {utterance!r}"
        assert _system(ctx) == assemble_system_prompt(state.as_prompt_vars(), expected_stage)
        settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
        assert settings and settings[0].delta.max_tokens == stage_max_tokens(expected_stage)
        messages.append({"role": "user", "content": utterance})
        messages.append({"role": "assistant", "content": "(roma line)"})

    _, _, proc = _drive_turn(state, messages, "haan confirm, theek hai")
    assert proc.won is True
    assert state.locked_slot is not None and state.locked_slot == state.accepted_slot


def test_objection_detour_midcall_routes_pivot_to_objection_and_back():
    state = CallState(call_sid="CA_obj_e2e", stage="pivot", **VARS)
    base = [
        {
            "role": "system",
            "content": assemble_system_prompt(state.as_prompt_vars(), "pivot"),
        }
    ]

    ctx, _, _ = _drive_turn(state, base, "ye course bahut mehenga hai")
    assert state.stage == "objection"
    assert _system(ctx) == assemble_system_prompt(state.as_prompt_vars(), "objection")

    ctx, _, _ = _drive_turn(state, base, "achha theek hai, samajh gaya")
    assert state.stage == "pivot"
    assert _system(ctx) == assemble_system_prompt(state.as_prompt_vars(), "pivot")
