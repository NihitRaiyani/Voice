"""Offline heuristic evidence, smoothing and checkpoint compatibility; no model calls."""

import asyncio
import json
from datetime import datetime

import pytest
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.language import (
    HISTORY_SIZE,
    Language,
    LanguageRouterState,
    detect_language,
    route_language,
)
from roma.domain.conversation.state import CallState
from roma.domain.conversation.turn import advance_turn


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("મને કોર્સ વિશે જાણવું છે", Language.GUJARATI),
        ("मुझे इसके बारे में जानकारी चाहिए", Language.HINDI),
        ("Please explain the course details", Language.ENGLISH),
        ("મને Digital Marketing શીખવું છે", Language.GUJARATI_ENGLISH),
        ("मुझे Google Ads सीखना है", Language.HINDI_ENGLISH),
        ("મને course details સમજાવો", Language.GUJARATI_ENGLISH),
        ("मुझे course details बताइए", Language.HINDI_ENGLISH),
        ("hu Gujarati ma vaat karva mangu chhu", Language.GUJARATI),
        ("mane aa vishe janvu che", Language.GUJARATI),
        ("mujhe iske baare mein jaankari chahiye", Language.HINDI),
        ("main abhi padhai kar raha hoon", Language.HINDI),
        ("mane Google Ads shikhvu che", Language.GUJARATI_ENGLISH),
        ("mujhe Digital Marketing seekhna hai", Language.HINDI_ENGLISH),
        ("मुझे SEO सीखना है", Language.HINDI),
        ("મને SEO શીખવું છે", Language.GUJARATI),
        ("I want to learn SEO", Language.ENGLISH),
        ("Please explain the main course details", Language.ENGLISH),
        ("Digital Marketing ma interest che", Language.GUJARATI_ENGLISH),
        ("I would like to visit tomorrow", Language.ENGLISH),
    ],
)
def test_supported_scripts_transliteration_and_code_mix(text, expected):
    assert detect_language(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "...?!",
        "12345",
        "ha ji",
        "okay",
        "Asha",
        "SEO",
        "Google Ads",
        "Digital Marketing",
        "main",
        "to",
    ],
)
def test_short_ambiguous_and_course_only_input_does_not_guess(text):
    assert detect_language(text) is None


def test_mixed_native_scripts_use_ratios_then_recent_base_for_near_tie():
    assert detect_language("મને કોર્સ વિશે સમજાવો जी") == Language.GUJARATI
    assert detect_language("मुझे जानकारी बताइए જી") == Language.HINDI
    assert detect_language("હા हा", previous=Language.GUJARATI_ENGLISH) == Language.GUJARATI
    assert detect_language("હા हा", previous=Language.HINDI_ENGLISH) == Language.HINDI


def test_latin_transliteration_uses_recent_base_only_when_both_languages_have_evidence():
    assert detect_language("mane mujhe", previous=Language.GUJARATI) == Language.GUJARATI
    assert detect_language("mane mujhe", previous=Language.HINDI) == Language.HINDI
    assert detect_language("mane mujhe") is None


def test_one_english_word_never_changes_an_established_indic_route():
    state = LanguageRouterState()
    assert route_language(state, "મને કોર્સ વિશે જાણવું છે") == Language.GUJARATI
    for text in ["મને course વિશે જાણવું છે", "મને SEO શીખવું છે"] * 4:
        assert route_language(state, text) == Language.GUJARATI


def test_switch_requires_sustained_informative_turns_and_rejects_one_turn_noise():
    state = LanguageRouterState()
    assert route_language(state, "मुझे जानकारी चाहिए") == Language.HINDI
    assert route_language(state, "Please explain the details") == Language.HINDI
    assert route_language(state, "मुझे जानकारी चाहिए") == Language.HINDI
    assert route_language(state, "Please explain the details") == Language.HINDI
    assert route_language(state, "I want to visit tomorrow") == Language.ENGLISH
    assert route_language(state, "મને કોર્સ વિશે જાણવું છે") == Language.ENGLISH
    assert route_language(state, "તમે મને સમજાવો") == Language.GUJARATI
    assert route_language(state, "મને Google Ads શીખવું છે") == Language.GUJARATI
    assert route_language(state, "મને Digital Marketing સમજાવો") == Language.GUJARATI_ENGLISH


def test_empty_and_ambiguous_turns_do_not_erase_context():
    state = LanguageRouterState()
    route_language(state, "मुझे जानकारी चाहिए")
    before = state.to_dict()
    for text in ["", "  ", "ji", "SEO", "Asha"]:
        assert route_language(state, text) == Language.HINDI
        assert state.to_dict() == before


def test_term_spelling_and_caller_text_are_unchanged_and_not_stored():
    text = "मुझे SEO, Google Ads और Digital Marketing सीखना है"
    state = LanguageRouterState()
    assert route_language(state, text) == Language.HINDI_ENGLISH
    assert text == "मुझे SEO, Google Ads और Digital Marketing सीखना है"
    serialized = json.dumps(state.to_dict())
    for term in ["SEO", "Google Ads", "Digital Marketing", "सीखना"]:
        assert term not in serialized


def test_router_state_stays_bounded_under_long_calls_and_restore():
    state = LanguageRouterState()
    for _ in range(1000):
        route_language(state, "મને કોર્સ વિશે જાણવું છે")
    assert len(state.recent) == HISTORY_SIZE
    restored = LanguageRouterState.from_dict({"current": "HINDI", "recent": ["HINDI"] * 1000})
    assert len(restored.recent) == HISTORY_SIZE
    assert restored.to_dict() == {"current": "HINDI", "recent": ["HINDI"] * HISTORY_SIZE}


@pytest.mark.parametrize(
    "saved",
    [
        None,
        {},
        [],
        "invalid",
        {"current": "UNKNOWN", "recent": "transcript"},
        {"current": {}, "recent": [{}, None, "INVALID"]},
    ],
)
def test_missing_or_invalid_saved_router_state_has_safe_defaults(saved):
    assert LanguageRouterState.from_dict(saved) == LanguageRouterState()


def test_json_call_checkpoint_preserves_pending_switch_and_no_prompt_fields_change():
    state = CallState(call_sid="CA_synthetic")
    prompt_keys = set(state.as_prompt_vars())
    route_language(state.language_router, "मुझे जानकारी चाहिए")
    route_language(state.language_router, "Please explain the details")
    restored = CallState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored == state
    assert (
        route_language(restored.language_router, "I want to visit tomorrow") == Language.ENGLISH
    )
    assert set(restored.as_prompt_vars()) == prompt_keys
    assert "language_router" not in restored.as_prompt_vars()


def test_old_checkpoints_do_not_require_a_schema_migration():
    restored = CallState.from_dict({"call_sid": "CA_old", "stage": "value"})
    assert restored.language_router == LanguageRouterState()
    assert restored.to_dict()["language_router"] == {"current": "HINDI_ENGLISH", "recent": []}
    assert (
        CallState.from_dict({"language_router": None}).language_router == LanguageRouterState()
    )


def test_call_states_do_not_share_language_history():
    one, two = CallState(), CallState()
    route_language(one.language_router, "મને કોર્સ વિશે જાણવું છે")
    assert two.language_router == LanguageRouterState()


def test_finalized_turn_hook_observes_once_before_checkpointing():
    class Store:
        def __init__(self):
            self.saved = None

        async def save(self, state):
            self.saved = state.to_dict()

    state = CallState(call_sid="CA_synthetic")
    store = Store()
    asyncio.run(
        advance_turn(
            state,
            "haan ji mujhe jaankari chahiye",
            now=datetime(2026, 7, 25, 8, tzinfo=IST),
            store=store,
        )
    )
    assert state.turn_count == 1
    assert state.language_router.recent == [Language.HINDI]
    assert store.saved["language_router"] == state.language_router.to_dict()
