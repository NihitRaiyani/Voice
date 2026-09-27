"""Prompt assembly for Roma's LLM turn (docs/11).

The system prompt is persona -> hard_rules -> stage, in THAT order — load-bearing for
OpenAI prefix caching: the static prefix is byte-identical across a call's turns while
the variable conversation history is the message tail managed by the context
aggregators (not part of this string). Template vars are filled from call-state; an
unresolved var fails loud — never ship a literal placeholder to the model.

`{{branch}}` is the only preloaded one. Roma is inbound, so nothing is known about the caller
at connect: the name is captured in discover and reaches the model through `{{known}}` (the PATA HAI
line) rather than a `{{lead_name}}` substitution, which is what makes it survive a barge-in.
"""

import re
from functools import cache
from pathlib import Path

from roma.domain.conversation.stage import ConversationStage

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# NOT the lever for long turns, checked 2026-08-01 and recorded so it is not "fixed" again:
# these are max_tokens CEILINGS, and on live call 049f0dc1 Roma's longest turn was 79
# completion tokens against a value ceiling of 120 words (~480 tokens). The cap never binds, so
# lowering it changes nothing about how long she talks. `value` was already 70 once and
# was raised because it "bought one thin sentence"
# (`test_the_value_stage_has_room_for_a_real_explanation`).
STAGE_WORD_CAPS = {
    ConversationStage.OPEN: 25,
    ConversationStage.DISCOVER: 20,
    ConversationStage.VALUE: 120,
    ConversationStage.STRUCTURE: 60,
    ConversationStage.PIVOT: 30,
    ConversationStage.OBJECTION: 45,
    ConversationStage.CLOSE: 25,
}
_TOKENS_PER_WORD = 4

_VAR = re.compile(r"\{\{(\w+)\}\}")

# Roma's outbound opener, WORD FOR WORD as `stages/open.md` orders it ("Say exactly this,
# with nothing added"). Held in code as well as in the prompt because on an outbound call it
# is not generated — it is spoken straight from here.
#
# ## Why the LLM is not asked for a line it was given verbatim
#
# Two live calls (acf8e78f, c5672a23, 2026-08-01) were SILENT end to end. Both times the
# lead answered, heard nothing, and hung up; both times the cause was the first completion
# stalling — 64s on one, 18.6s on the other — on a link that was otherwise carrying the call.
# The first turn is where silence costs most, because the callee has just picked up and has
# no reason yet to keep listening.
#
# So the one turn whose text is fully determined is the one turn that must not depend on a
# network round trip to produce it. It still goes through `safe_output` on its way to TTS
# like every other line (the pipeline order guarantees it) — this skips the LLM, never the
# guardrail.
#
# `test_the_spoken_opener_matches_the_open_fragment_word_for_word` holds these two in step.
OPENING_LINE_NAMED = (
    "Hello {name} ji, main Roma baat kar rahi hoon Weltec Institute se — "
    "kya abhi 2 minute baat ho sakti hai?"
)
OPENING_LINE_NAMELESS = (
    "Hello ji, main Roma baat kar rahi hoon Weltec Institute se — "
    "kya abhi 2 minute baat ho sakti hai?"
)


def opening_line(lead_name: "str | None") -> str:
    """The exact sentence Roma opens an outbound call with.

    A blank name falls back to "Hello ji" — the same instruction the fragment carries, and
    the reason `as_prompt_vars` maps an unknown name to "" rather than None: "Hello None ji"
    at a real lead is worse than no name at all.
    """
    name = (lead_name or "").strip()
    return OPENING_LINE_NAMED.format(name=name) if name else OPENING_LINE_NAMELESS


# Cached because this ran on EVERY turn, off the critical path of nothing: nine files,
# ~16KB, re-read from disk between the lead finishing a sentence and the LLM being called.
# The files are packaged data — they cannot change while a call is in flight — so the read
# is pure. `cache_clear()` is exposed for tests that write a fragment to a tmp dir.
@cache
def _read(rel: str) -> str:  # like in input comes open
    return (_PROMPTS_DIR / rel).read_text(encoding="utf-8").strip()


# Horizontal whitespace only — never newlines, which carry the fragments' structure.
_RUN_OF_SPACES = re.compile(r"[^\S\n]{2,}")


def _render(text: str, call_state) -> str:
    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key not in call_state:
            raise KeyError(f"prompt variable {{{{{key}}}}} missing from call_state")
        return str(call_state[key])

    # A variable that renders empty leaves the spaces that surrounded it: `Hello {{lead_name}}
    # ji` becomes `Hello  ji` for a lead whose name we do not have. Harmless to a reader and
    # not harmless here — the fragment is an EXAMPLE the model copies, and a doubled space in
    # the example is a doubled space in what Bulbul is asked to say.
    #
    # Collapsing is safe across the whole corpus: exactly one line in `prompts/` contained a
    # run of spaces before this, and it was a typo in hard rule 1.
    return _RUN_OF_SPACES.sub(" ", _VAR.sub(_sub, text))


def cache_prefix(call_state) -> str:
    """`persona -> hard_rules`: the span that must be BYTE-IDENTICAL across a call's turns.

    This is the unit OpenAI's prompt cache keys on. It is split out from
    `assemble_system_prompt` so the property has one definition that the controller's
    instrumentation and `tests/llm/test_prompt_cache.py` both read, rather than three
    places each re-deriving "the first two fragments".

    It is a function of `call_state` only through `{{branch}}` — the single variable in
    `persona.md`, constant for the life of a call. `hard_rules.md` has no variables at all.
    Anything that varies per TURN belongs in the stage fragment, which is appended after
    this and is deliberately outside the cached span. Measured: 93-96% cached input on the
    turns where the cache holds (`var/roma/spend.jsonl`, 296 gpt-4o requests).
    """
    return "\n\n".join(
        (
            _render(_read("persona.md"), call_state),
            _render(_read("hard_rules.md"), call_state),
        )
    )


def assemble_system_prompt(
    call_state, stage: ConversationStage = ConversationStage.OPEN
) -> str:
    """persona -> hard_rules -> stage, vars substituted. Order is load-bearing (docs/11)."""
    return "\n\n".join(
        (cache_prefix(call_state), _render(_read(f"stages/{stage}.md"), call_state))
    )


def stage_max_tokens(stage: ConversationStage) -> int:
    """Safety ceiling (tokens) for a stage's response — caps monologuing (docs/02)."""
    return STAGE_WORD_CAPS[stage] * _TOKENS_PER_WORD


__all__ = [
    "assemble_system_prompt",
    "cache_prefix",
    "stage_max_tokens",
    "STAGE_WORD_CAPS",
    "OPENING_LINE_NAMED",
    "OPENING_LINE_NAMELESS",
    "opening_line",
]
