"""Objection classifier (docs/03): each of the Proposed-7 categories fires, clean/positive
utterances do not (false positives derail the close), and it never raises."""

import pytest
from roma.domain.conversation.objection import (
    OBJECTION_PHRASES,
    Objection,
    classify_objection,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ye to bahut mehenga hai", Objection.COST),
        ("fees zyada lag rahi hai", Objection.COST),
        ("abhi bahut busy hoon", Objection.NO_TIME),
        ("mere paas time nahi hai", Objection.NO_TIME),
        ("ghar mein baat karke bataunga", Objection.ASK_FAMILY),
        ("papa se puchna padega", Objection.ASK_FAMILY),
        ("center bahut door hai", Objection.DISTANCE),
        ("aana mushkil hoga", Objection.DISTANCE),
        ("online ho sakta hai kya", Objection.MODE),
        ("job milegi iski guarantee", Objection.PLACEMENT_DOUBT),
        ("main sochkar batata hoon", Objection.THINK_ABOUT_IT),
        ("abhi dekhta hoon baad mein", Objection.THINK_ABOUT_IT),
    ],
)
def test_each_category_fires(text, expected):
    assert classify_objection(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "haan Monday theek rahega",
        "theek hai chaliye",
        "बहुत अच्छा",
        "Roma ji namaste",
        "",
    ],
)
def test_clean_or_positive_utterances_do_not_fire(text):
    assert classify_objection(text) is None


def test_token_boundary_no_substring_false_positive():
    assert classify_objection("indoor class hai") is None


def test_devanagari_cue_fires():
    assert classify_objection("ये तो महंगा है") == Objection.COST


def test_never_raises_on_odd_input():
    assert classify_objection("😀 !!! ---") is None


def test_cost_takes_precedence_when_multiple_could_fire():
    assert classify_objection("mehenga hai aur time bhi nahi") == Objection.COST


@pytest.mark.parametrize(
    "text,expected",
    [
        ("આ તો બહુ મોંઘું છે", Objection.COST),
        ("મારી પાસે સમય નથી", Objection.NO_TIME),
        ("ઘરે વાત કરીને કહીશ", Objection.ASK_FAMILY),
        ("સેન્ટર બહુ દૂર છે", Objection.DISTANCE),
        ("ઓનલાઇન થઈ શકે", Objection.MODE),
        ("જોબ મળશે કે નહીં", Objection.PLACEMENT_DOUBT),
        ("પછી જોઈશ", Objection.THINK_ABOUT_IT),
    ],
)
def test_gujarati_objections_fire(text, expected):
    assert classify_objection(text) == expected


COST_UTTERANCES = [
    "ફી બહુ વધારે છે",
    "ફીસ કેટલી છે",
    "પૈસા નથી",
    "પૈસા ની તકલીફ છે",
    "बजट नहीं है",
    "fees zyada hai",
    "महंगा है",
    "મોંઘું છે",
    "budget nahi hai",
]


@pytest.mark.parametrize("text", COST_UTTERANCES)
def test_money_objection_classifies_in_every_script(text):
    assert classify_objection(text) is Objection.COST


NOT_OBJECTIONS = [
    "હું ઘરે જ છું",
    "મેં ઓનલાઇન ફોર્મ ભર્યું હતું",
    "I filled the online form",
    "door band karo",
    "હા જી",
    "મને ડિજિટલ માર્કેટિંગ માં રસ છે",
    "",
]


@pytest.mark.parametrize("text", NOT_OBJECTIONS)
def test_ordinary_speech_is_not_an_objection(text):
    assert classify_objection(text) is None


STILL_CLASSIFIED = [
    ("ઘરે વાત કરીને કહીશ", Objection.ASK_FAMILY),
    ("ઘરે પૂછીને કહીશ", Objection.ASK_FAMILY),
    ("ઓનલાઇન થાય તો સારું", Objection.MODE),
    ("ઓનલાઇન ચાલે", Objection.MODE),
    ("online ho sakta hai kya", Objection.MODE),
    ("bahut door hai", Objection.DISTANCE),
    ("બહુ દૂર છે", Objection.DISTANCE),
]


@pytest.mark.parametrize("text,expected", STILL_CLASSIFIED)
def test_demoted_keywords_are_still_covered_by_phrases(text, expected):
    assert classify_objection(text) is expected


def test_every_phrase_classifies_to_its_own_category():
    """Structural guard: phrases are checked BEFORE keywords, so a phrase must never be
    shadowed into a different category by another category's keyword."""
    for cat, phrases in OBJECTION_PHRASES.items():
        for phrase in phrases:
            got = classify_objection(" ".join(phrase))
            assert got is cat, f"{phrase} -> {got}, expected {cat}"


NOT_OBJECTIONS = [
    "placement ke liye",
    "job dhoondh raha hoon",
    "abhi job karta hoon",
    "naukri chhod di thi",
    "placement wala batch",
]

STILL_OBJECTIONS = [
    "placement milega ya nahi",
    "job milegi iski guarantee",
    "naukri pakka milegi kya",
    "જોબ મળશે કે નહીં",
    "placement guarantee hai kya",
]


@pytest.mark.parametrize("text", NOT_OBJECTIONS)
def test_naming_placement_is_not_objecting_to_it(text):
    assert classify_objection(text) is None, f"{text!r} would derail the call into P6"


@pytest.mark.parametrize("text", STILL_OBJECTIONS)
def test_a_real_placement_doubt_is_still_caught(text):
    """The other half — without this the gate above would simply disable the category."""
    assert classify_objection(text) is Objection.PLACEMENT_DOUBT
