"""End-to-end drive of the phase controller across a whole call (docs/03).

Feeds a scripted P1→P7 conversation through the real PhaseControllerProcessor (real machine,
real objection lexicon, real prompt assembler; only slot extraction is faked). Proves the
system prompt swapped into the context matches the phase the machine landed on at EVERY turn,
that an objection detour routes P5→P6→P5, and that a confirmed readback fires the WIN.

This is the offline stand-in for a live call; it exercises the exact integration seam
(user aggregator → PhaseController → LLM) without a Twilio socket.

One fresh processor per turn (run_test starts+ends the processor it drives, so a processor
is single-use) — the shared CallState carries the call's progress across turns."""

import asyncio
from datetime import datetime

from pipecat.frames.frames import LLMContextFrame, LLMUpdateSettingsFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

from roma.controller.slots import DiscoveryValue, TimeSlot
from roma.controller.state import CallState
from roma.controller.timeresolve import IST
from roma.llm.prompts import assemble_system_prompt, phase_max_tokens
from roma.telephony.phase_controller import PhaseControllerProcessor

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
    proc = PhaseControllerProcessor(
        state,
        client=object(),
        now_fn=lambda: NOW,
        extract_discovery=_fake_discovery,
        extract_time=_fake_time,
    )
    ctx = LLMContext(messages=[*messages, {"role": "user", "content": user_text}])
    down, _ = asyncio.run(run_test(proc, frames_to_send=[LLMContextFrame(ctx)]))
    return ctx, down, proc


def test_full_call_prompt_tracks_the_phase_the_machine_lands_on():
    state = CallState(call_sid="CA_e2e", phase="p1_open", **VARS)
    messages = [
        {"role": "system", "content": assemble_system_prompt(state.as_prompt_vars(), "p1_open")}
    ]

    script = [
        ("haan ji, digital marketing ke liye hi baat ki thi", "p2_discover"),
        ("bcom kiya hai", "p2_discover"),
        ("2020 mein", "p2_discover"),
        ("abhi job kar raha hoon", "p2_discover"),
        ("Vadodara", "p3_value"),
        ("achha, samajh gayi", "p3_value"),
        ("ye course kya hai", "p3_value"),
        ("kitne mahine ka hai", "p3_value"),
        ("iske baad kya kar sakte hain", "p4_structure"),
        ("theek hai", "p5_pivot"),
        ("Monday accept hai", "p7_close"),
    ]

    for utterance, expected_phase in script:
        ctx, down, _ = _drive_turn(state, messages, utterance)

        assert state.phase == expected_phase, f"after {utterance!r}"
        assert _system(ctx) == assemble_system_prompt(state.as_prompt_vars(), expected_phase)
        settings = [f for f in down if isinstance(f, LLMUpdateSettingsFrame)]
        assert settings and settings[0].delta.max_tokens == phase_max_tokens(expected_phase)
        messages.append({"role": "user", "content": utterance})
        messages.append({"role": "assistant", "content": "(roma line)"})

    _, _, proc = _drive_turn(state, messages, "haan confirm, theek hai")
    assert proc.won is True
    assert state.locked_slot is not None and state.locked_slot == state.accepted_slot


def test_objection_detour_midcall_routes_p5_to_p6_and_back():
    state = CallState(call_sid="CA_obj_e2e", phase="p5_pivot", **VARS)
    base = [
        {
            "role": "system",
            "content": assemble_system_prompt(state.as_prompt_vars(), "p5_pivot"),
        }
    ]

    ctx, _, _ = _drive_turn(state, base, "ye course bahut mehenga hai")
    assert state.phase == "p6_objection"
    assert _system(ctx) == assemble_system_prompt(state.as_prompt_vars(), "p6_objection")

    ctx, _, _ = _drive_turn(state, base, "achha theek hai, samajh gaya")
    assert state.phase == "p5_pivot"
    assert _system(ctx) == assemble_system_prompt(state.as_prompt_vars(), "p5_pivot")
