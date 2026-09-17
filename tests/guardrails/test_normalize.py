from roma.guardrails.normalize import normalize


def test_lowercases_and_collapses_whitespace():
    assert normalize("  Fees   ₹5000  ") == "fees ₹5000"


def test_nfc_normalizes():
    decomposed = "क़"
    precomposed = "क़"
    assert normalize(decomposed) == normalize(precomposed)


def test_strips_zero_width_chars():
    assert normalize("fe​es") == "fees"


def test_casefolds_unicode():
    assert normalize("LPA") == "lpa"


def test_brand_fold_variants_to_weltec():
    for variant in ("Valtech", "Welltech", "Well-Tech", "Weltech"):
        assert normalize(f"{variant} course") == "weltec course"


def test_empty_input():
    assert normalize("") == ""
