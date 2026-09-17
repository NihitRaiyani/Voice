from roma.guardrails import lexicon as lex
from roma.guardrails.lexicon import BlockCategory


def test_every_category_has_a_substitution_line():
    for cat in BlockCategory:
        assert cat in lex.SUBSTITUTIONS
        assert lex.SUBSTITUTIONS[cat].strip()


def test_permitted_money_line_present():
    assert lex.PERMITTED_MONEY_LINE == "No-cost EMI available hai."


def test_hard_fail_line_nonempty():
    assert lex.HARD_FAIL_LINE.strip()


def test_negation_window_is_positive_int():
    assert isinstance(lex.NEGATION_WINDOW, int) and lex.NEGATION_WINDOW >= 1


def test_cert_block_enabled_default_on():
    assert lex.CERT_BLOCK_ENABLED is True


def test_rebuttal_cues_include_core_negations():
    assert {"nahi", "नहीं"} <= lex.REBUTTAL_CUES
