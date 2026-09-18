"""The OpenAI prefix-cache contract (docs/11): `persona -> hard_rules` is byte-identical.

This property is load-bearing and, until this file, entirely untested. `phase_controller`
mutates the system message in place on every turn precisely so this span does not move
(`phase_controller.py:7-9`), and `roma/spend.py` shows it working: across 296 live gpt-4o
requests, 71.6% of all input tokens billed as cached, 93-96% on the turns where the cache
holds.

The failure this guards is silent and expensive. Adding one `{{var}}` to `persona.md` or
`hard_rules.md` that varies per turn — a slot status, a fact list, an elapsed-time band —
would move the prefix on every turn, drop cached input to near zero, and change NOTHING a
test currently checks. Roma would sound identical and cost roughly double.

The rule these tests encode: **anything that varies within a call belongs in the phase
fragment, which is appended after the cached span.** `{{branch}}` is the one variable
allowed in the prefix because it is fixed for the life of a call.
"""

import re

from roma.domain.conversation.prompts import (
    PHASE_WORD_CAPS,
    assemble_system_prompt,
    cache_prefix,
)
from roma.domain.conversation.state import CallState
from roma.realtime.phase_controller import _swap_system_prompt

_VAR = re.compile(r"\{\{(\w+)\}\}")

# OpenAI does not cache a prefix below this, so a prefix that shrinks under it stops
# caching entirely rather than caching less.
OPENAI_CACHE_MIN_TOKENS = 1024

# The repo's own estimate (`prompts._TOKENS_PER_WORD` is 4 tokens/word for output caps);
# for prompt text chars/4 is the closer approximation and needs no new dependency.
_CHARS_PER_TOKEN = 4


def _states():
    """CallStates that differ in every way a turn can differ WITHIN one call.

    Deliberately not varying `branch`: that is the one prefix variable, it is set once from
    the lead record at connect, and it does not change while the call is up.
    """
    base = dict(branch="Vadodara", call_sid="CAtest")
    early = CallState(**base)

    named = CallState(**base, lead_name="Nihit")

    mid = CallState(**base, lead_name="Nihit", city="Vadodara", current_status="student")
    mid.slot_status = "unclear"
    mid.elapsed_secs = 120
    mid.record_facts(["modules"])

    late = CallState(**base, lead_name="Nihit", city="Surat", current_status="working")
    late.slot_status = "accepted"
    late.accepted_slot = "2026-08-04T11:00:00+05:30"
    late.elapsed_secs = 260
    late.record_facts(["modules", "duration", "faculty"])

    refused = CallState(**base, lead_name="Nihit")
    refused.slot_status = "out_of_hours"
    refused.lead_wants_out = True
    refused.elapsed_secs = 300

    return [early, named, mid, late, refused]


def test_the_prefix_is_byte_identical_across_every_phase_and_state():
    """The whole contract, in one assertion.

    5 states x 7 phases = 35 assemblies that a real call moves through. Every one must open
    with the same bytes, or the cache re-pays the prefix from that turn onward.
    """
    prefixes = {
        cache_prefix(state.as_prompt_vars()) for state in _states() for _ in PHASE_WORD_CAPS
    }
    assert len(prefixes) == 1, (
        "the persona->hard_rules prefix moved between turns of one call — OpenAI's prompt "
        "cache keys on this span, so a variable that changes per turn must live in the "
        "phase fragment instead"
    )


def test_every_assembled_prompt_actually_starts_with_that_prefix():
    """`cache_prefix` must be a true prefix of what is sent, not a parallel construction.

    If these two ever drift apart, the controller's instrumentation would report a stable
    hash for a span the model never sees, which is worse than no instrumentation.
    """
    for state in _states():
        variables = state.as_prompt_vars()
        prefix = cache_prefix(variables)
        for phase in PHASE_WORD_CAPS:
            prompt = assemble_system_prompt(variables, phase)
            assert prompt.startswith(prefix), (
                f"assemble_system_prompt({phase}) does not begin with cache_prefix() — "
                "the cached span and the sent prompt have diverged"
            )


def test_hard_rules_has_no_template_variables_at_all():
    from roma.domain.conversation.prompts import _read

    assert _VAR.findall(_read("hard_rules.md")) == [], (
        "hard_rules.md is fully static and must stay that way; put per-turn state in the "
        "phase fragment"
    )


def test_branch_is_the_only_variable_the_persona_may_carry():
    """A named allowlist, so adding a var to `persona.md` fails HERE with the reason.

    `{{branch}}` is admissible only because it is constant for a call. Any other variable
    in this file is a per-turn value in a per-call span.
    """
    from roma.domain.conversation.prompts import _read

    assert set(_VAR.findall(_read("persona.md"))) == {"branch"}, (
        "a new variable appeared in persona.md — if it can change mid-call it breaks the "
        "prefix cache; move it to the phase fragment"
    )


def test_the_prefix_clears_openais_minimum_cacheable_length():
    prefix = cache_prefix(_states()[0].as_prompt_vars())
    approx_tokens = len(prefix) / _CHARS_PER_TOKEN
    assert approx_tokens > OPENAI_CACHE_MIN_TOKENS, (
        f"prefix is ~{approx_tokens:.0f} tokens, under OpenAI's {OPENAI_CACHE_MIN_TOKENS}-"
        "token cache minimum — below this nothing caches at all, not merely less"
    )


def test_the_phase_fragment_is_where_per_turn_state_actually_lands():
    """The positive half of the contract: the volatile values ARE being sent, just later.

    Without this, a refactor could satisfy every assertion above by dropping the dynamic
    state entirely — a perfectly cached prompt that has stopped telling Roma anything.
    """
    from roma.domain.conversation.prompts import _read

    variables = _states()[3].as_prompt_vars()
    seen = set()
    for phase in PHASE_WORD_CAPS:
        tail = assemble_system_prompt(variables, phase)[len(cache_prefix(variables)) :]
        # Each fragment declares which values it wants; every one it declares must arrive.
        for name in _VAR.findall(_read(f"phases/{phase}.md")):
            assert variables[name] in tail, (
                f"{phase} declares {{{{{name}}}}} but its rendered value is not in the "
                "uncached tail — the per-turn state is not reaching the model"
            )
            seen.add(name)
    # The three that actually vary turn to turn, so this cannot pass on static vars alone.
    assert {"slot_status", "said", "pacing"} <= seen


def test_swapping_the_system_prompt_mutates_in_place():
    """The mechanism, not just the content.

    `_swap_system_prompt` must edit the existing dict rather than rebuild the list. A
    reassignment would also "work" — same text to the model — while discarding the
    conversation history the aggregators appended, which is the bug the in-place write
    exists to prevent (`phase_controller.py:95-104`).
    """

    class _Ctx:
        def __init__(self, messages):
            self._messages = messages

        def get_messages(self):
            return self._messages

    system = {"role": "system", "content": "old"}
    history = [{"role": "user", "content": "hello"}]
    messages = [system, *history]
    context = _Ctx(messages)

    _swap_system_prompt(context, "new")

    assert context.get_messages() is messages, "the messages list was reassigned"
    assert context.get_messages()[0] is system, "the system dict was replaced, not edited"
    assert system["content"] == "new"
    assert context.get_messages()[1:] == history, "conversation history was disturbed"


def test_a_context_with_no_system_message_gets_one_at_the_front():
    """The insert branch — a cache prefix that lands anywhere but index 0 caches nothing."""

    class _Ctx:
        def __init__(self, messages):
            self._messages = messages

        def get_messages(self):
            return self._messages

    context = _Ctx([{"role": "user", "content": "hello"}])
    _swap_system_prompt(context, "seeded")

    assert context.get_messages()[0] == {"role": "system", "content": "seeded"}
