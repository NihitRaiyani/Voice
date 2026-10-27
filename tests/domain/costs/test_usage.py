from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from roma.domain.costs.usage import (
    PriceCard,
    UsageEvent,
    UsageRate,
    anthropic_price_card,
    price_usage,
)


def event(**overrides):
    values = dict(
        event_key="generation-1",
        provider="anthropic",
        service="llm",
        model="claude-haiku-5-5",
        occurred_at=datetime(2026, 10, 26, tzinfo=UTC),
        input_tokens=1000,
        cache_read_tokens=2000,
        cache_write_tokens=3000,
        output_tokens=100,
        cache_write_ttl_seconds=300,
    )
    return UsageEvent(**(values | overrides))


def test_verified_haiku_rates_price_distinct_token_categories_without_float_rounding():
    usage = event()
    priced = price_usage(usage, anthropic_price_card(usage))
    assert priced.amount == Decimal("0.000545")
    assert priced.currency == "USD"
    assert len(priced.lines) == 4
    assert priced.provenance.source_url.startswith("https://platform.claude.com/")
    with pytest.raises(FrozenInstanceError):
        usage.input_tokens = 3


def test_haiku_long_context_threshold_counts_reads_and_writes_and_uses_whole_request_tier():
    usage = event(
        input_tokens=1, cache_read_tokens=100_000, cache_write_tokens=0, output_tokens=1
    )
    priced = price_usage(usage, anthropic_price_card(usage))
    assert priced.amount == Decimal("0.005003")


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "unreviewed-model"},
        {"input_tokens": None},
        {"cache_write_ttl_seconds": None},
    ],
)
def test_missing_rates_usage_and_cache_ttl_are_unpriced_not_zero(overrides):
    usage = event(**overrides)
    priced = price_usage(usage, anthropic_price_card(usage))
    assert priced.amount is None and priced.unpriced_reason


def test_one_hour_cache_write_uses_its_declared_rate():
    usage = event(cache_write_ttl_seconds=3600)
    priced = price_usage(usage, anthropic_price_card(usage))
    assert priced.amount == Decimal("0.00077")


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_tokens": -1},
        {"input_tokens": 1.2},
        {"input_tokens": True},
        {"occurred_at": datetime(2026, 10, 26)},
        {"observed_amount": Decimal("NaN"), "observed_currency": "USD"},
        {"audio_seconds": Decimal(1)},
    ],
)
def test_invalid_usage_never_enters_accounting(overrides):
    with pytest.raises(ValueError):
        event(**overrides)


def test_asr_seconds_and_tts_characters_audio_and_cache_hits_remain_separate():
    asr = UsageEvent(
        "asr-1",
        "local",
        "stt",
        datetime(2026, 10, 26, tzinfo=UTC),
        audio_seconds=Decimal("1.25"),
        gpu_seconds=Decimal("0.5"),
    )
    tts = UsageEvent(
        "tts-1",
        "local",
        "tts",
        datetime(2026, 10, 26, tzinfo=UTC),
        characters=12,
        audio_seconds=Decimal("1.2"),
        cache_hits=1,
    )
    provenance = anthropic_price_card(event()).provenance
    card = PriceCard(
        "local",
        "stt",
        None,
        "INR",
        (
            UsageRate("audio_seconds", Decimal("0.01")),
            UsageRate("gpu_seconds", Decimal("0.02")),
        ),
        provenance,
    )
    assert price_usage(asr, card).amount == Decimal("0.0225")
    assert tts.canonical()["audio_seconds"] == "1.2"
    assert tts.canonical()["characters"] == 12 and tts.canonical()["cache_hits"] == 1
    assert price_usage(tts).amount is None


def test_receipt_timestamps_do_not_change_replay_identity_but_event_timestamps_do():
    from dataclasses import replace
    from datetime import timedelta

    observed = event()
    later = observed.occurred_at + timedelta(seconds=1)
    assert observed.digest != replace(observed, occurred_at=later).digest
    receipt = replace(observed, occurred_at_is_receipt=True)
    assert receipt.digest == replace(receipt, occurred_at=later).digest
    assert receipt.digest != replace(receipt, output_tokens=101).digest
