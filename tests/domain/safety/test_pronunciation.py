import asyncio
from dataclasses import replace
from hashlib import sha256

import pytest
from roma.domain.safety import pronunciation as bindings
from roma.domain.safety.audit import SafetyContext
from roma.domain.safety.filter import FilterVerdict
from roma.domain.safety.lexicon import BlockCategory
from roma.services.safety_service import SafetyService

CTX = SafetyContext("synthetic-pronunciation", 1, "lab")


def test_only_six_exact_still_approved_literal_bindings_exist():
    assert len(bindings.PRONUNCIATIONS) == 6
    approved = {
        *bindings.lexicon.SUBSTITUTIONS.values(),
        bindings.lexicon.HARD_FAIL_LINE,
        bindings.lexicon.PERMITTED_MONEY_LINE,
    }
    assert {item.canonical_text for item in bindings.PRONUNCIATIONS} == approved
    for item in bindings.PRONUNCIATIONS:
        assert bindings.pronunciation_for(item.canonical_text) == item
        assert sha256(item.canonical_text.encode()).hexdigest() == item.canonical_sha256
        assert sha256(item.spoken_text.encode()).hexdigest() == item.spoken_sha256
        assert item.language == "hi"
        assert bindings.pronunciation_for(item.canonical_text + " additional claim") is None
        assert bindings.pronunciation_for(item.canonical_text.lower()) is None
        assert bindings.pronunciation_for(item.spoken_text) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_sha256", "0" * 64),
        ("spoken_text", "फीस 50000"),
        ("spoken_sha256", "0" * 64),
        ("language", "gu"),
        ("version", "unreviewed-v9"),
    ],
)
def test_changed_registry_record_is_not_implicitly_approved(monkeypatch, field, value):
    item = bindings.PRONUNCIATIONS[0]
    monkeypatch.setattr(bindings, "PRONUNCIATIONS", (replace(item, **{field: value}),))
    with pytest.raises(ValueError, match="binding"):
        bindings.pronunciation_for(item.canonical_text)


def test_changed_canonical_lexicon_needs_new_reviewed_binding(monkeypatch):
    item = bindings.PRONUNCIATIONS[0]
    updated = item.canonical_text + " Changed."
    substitutions = {
        **bindings.lexicon.SUBSTITUTIONS,
        BlockCategory.FEE: updated,
        BlockCategory.DISCOUNT: updated,
    }
    monkeypatch.setattr(bindings.lexicon, "SUBSTITUTIONS", substitutions)
    with pytest.raises(ValueError, match="binding"):
        bindings.pronunciation_for(item.canonical_text)
    with pytest.raises(ValueError, match="reviewed pronunciation"):
        bindings.pronunciation_for(updated)


@pytest.mark.parametrize("changed", ["name", "spoken_text", "canonical_text"])
def test_caller_created_record_cannot_unlock_approval(changed):
    item = bindings.PRONUNCIATIONS[0]
    forged = replace(item, **{changed: getattr(item, changed) + " fake"})
    with pytest.raises(ValueError):
        asyncio.run(SafetyService().check_pronunciation(forged, CTX))


@pytest.mark.parametrize(
    "category",
    [BlockCategory.FEE, BlockCategory.SALARY, BlockCategory.PLACEMENT, BlockCategory.DISCOUNT],
)
def test_exact_certificate_adjudication_rejects_other_violations(monkeypatch, category):
    import roma.services.safety_service as safety_module

    item = next(item for item in bindings.PRONUNCIATIONS if item.name == "certificate")
    verdict = FilterVerdict(
        False,
        category,
        "replacement",
        "block:" + category.value,
        (),
        (category, BlockCategory.CERT),
    )
    monkeypatch.setattr(
        safety_module, "check_sentence", lambda text: ("replacement", verdict, ())
    )
    with pytest.raises(ValueError, match="independent rule check"):
        asyncio.run(SafetyService().check_pronunciation(item, CTX))


def test_independent_rule_check_still_rejects_money_with_valid_recomputed_digest(monkeypatch):
    # Even a reviewed-table mistake with a matching digest cannot hide a fee amount.
    item = next(item for item in bindings.PRONUNCIATIONS if item.name == "certificate")
    text = item.spoken_text + " फीस 50000।"
    forged = replace(item, spoken_text=text, spoken_sha256=sha256(text.encode()).hexdigest())
    monkeypatch.setattr(bindings, "PRONUNCIATIONS", (forged,))
    with pytest.raises(ValueError, match="independent rule check"):
        asyncio.run(SafetyService().check_pronunciation(forged, CTX))


def test_rule_checker_failure_cannot_be_certificate_exception(monkeypatch):
    import roma.services.safety_service as safety_module

    item = next(item for item in bindings.PRONUNCIATIONS if item.name == "certificate")
    verdict = FilterVerdict(False, None, "replacement", "filter-error:unavailable", ())
    monkeypatch.setattr(
        safety_module, "check_sentence", lambda text: ("replacement", verdict, ())
    )
    with pytest.raises(ValueError, match="independent rule check"):
        asyncio.run(SafetyService().check_pronunciation(item, CTX))


def test_legacy_check_does_not_acquire_a_pronunciation_allowlist():
    item = next(item for item in bindings.PRONUNCIATIONS if item.name == "certificate")
    checked = asyncio.run(SafetyService().check(item.spoken_text, CTX))
    assert checked.text == item.canonical_text
    assert checked.events[0].category == "CERT"
