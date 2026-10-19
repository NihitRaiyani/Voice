import pytest
from roma.eval.hosted_asr import edit_distance, normalized, score


def test_known_edit_operations_and_rates():
    assert edit_distance("kitten", "sitting") == 3
    assert score("one two three", "one four three")["wer"] == pytest.approx(1 / 3)
    assert score("one two", "one")["wer"] == 0.5
    assert score("one", "one two")["wer"] == 1
    assert score("", "")["wer"] == 0
    assert score("", "one")["wer"] is None


def test_normalization_preserves_indic_marks_and_normalizes_digits():
    assert (
        normalized("SEO, Google Ads! ગુજરાતી हिन्दी ૨૦૨૬") == "seo google ads ગુજરાતી हिन्दी 2026"
    )
    assert score("હિન્દી", "હિન્દી")["cer"] == 0
    assert score("Hello world!", "hello WORLD")["wer"] == 0


@pytest.mark.parametrize("text", [None, "x" * 4097, "\u0344" * 4096])
def test_evaluation_rejects_unbounded_or_invalid_input(text):
    with pytest.raises(ValueError):
        normalized(text)
