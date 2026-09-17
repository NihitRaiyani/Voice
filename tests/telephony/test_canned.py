import pytest

from roma.dialer import CONSENT_LINE
from roma.telephony import canned


def test_consent_line_loads_and_matches_gate_constant():
    line = canned.consent_line()
    assert line.text == CONSENT_LINE
    assert len(line.pcm) > 0
    assert len(line.pcm) % 2 == 0


def test_opening_line_loads_clean_through_filter():
    line = canned.opening_line()
    assert line.text == canned.OPENING_LINE
    assert len(line.pcm) > 0


def test_opening_line_passes_the_pretts_filter_unaltered():
    from roma.guardrails import safe_output

    assert safe_output(canned.OPENING_LINE) == canned.OPENING_LINE


def test_line_the_filter_would_alter_is_refused_at_load():
    with pytest.raises(ValueError, match="altered by the pre-TTS filter"):
        canned._load("Course ki fees ₹50000 hai.", "opening.ulaw")


def test_missing_asset_raises_pointing_to_the_script():
    with pytest.raises(FileNotFoundError, match="make_canned_clip"):
        canned._load(canned.OPENING_LINE, "does_not_exist.ulaw")


# --- the inbound opener (docs/decisions.md, LOCKED 2026-07-31) -----------------------------


def test_the_opening_asset_exists_and_decodes():
    """It is played at connect from disk. A missing asset means the caller's first second is
    silence, which is the failure this whole mechanism exists to remove."""
    from roma.telephony.canned import OPENING_LINE, opening_line

    line = opening_line()
    assert line.text == OPENING_LINE
    assert line.pcm


def test_the_opener_is_short_enough_to_be_an_answer_not_a_speech():
    """A person who answers a phone says who they are and stops. Every extra second is the
    caller waiting to say the thing they dialled to say — and it is audio they must barge in
    over. Two seconds is generous; the rendered asset is ~1.5s."""
    from roma.telephony.canned import SAMPLE_RATE, opening_line

    secs = len(opening_line().pcm) / 2 / SAMPLE_RATE
    assert secs < 2.0, f"the opener is {secs:.2f}s — that is a speech, not a greeting"


def test_the_opener_names_weltec():
    """The whole reason it is not a bare "Hello?": a caller who does not hear the business
    name has to ask, which costs a turn. P1's branch exists for when they ask anyway."""
    from roma.telephony.canned import OPENING_LINE

    assert "Weltec" in OPENING_LINE
