"""Immutable normalized usage and exact pricing, separate from safety reservations.

Rates are estimates for the declared billing basis, never currency conversions or
invoices. Missing usage or rates remain explicitly unpriced.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

Service = Literal["telephony", "stt", "llm", "tts"]
COUNT_FIELDS = (
    "carrier_seconds",
    "audio_seconds",
    "gpu_seconds",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "characters",
    "cache_hits",
)
SERVICE_FIELDS = {
    "telephony": ("carrier_seconds",),
    "stt": ("audio_seconds", "gpu_seconds"),
    "llm": ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"),
    "tts": ("characters", "audio_seconds", "gpu_seconds", "cache_hits"),
}


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    event_key: str
    provider: str
    service: Service
    occurred_at: datetime
    model: str | None = None
    call_id: UUID | None = None
    turn_id: UUID | None = None
    carrier_seconds: Decimal | None = None
    audio_seconds: Decimal | None = None
    gpu_seconds: Decimal | None = None
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    characters: int | None = None
    cache_hits: int | None = None
    cache_write_ttl_seconds: int | None = None
    observed_amount: Decimal | None = None
    observed_currency: str | None = None
    occurred_at_is_receipt: bool = False

    def __post_init__(self):
        if type(self.occurred_at_is_receipt) is not bool:
            raise ValueError("timestamp provenance must be a boolean")
        if self.service not in SERVICE_FIELDS:
            raise ValueError("unsupported usage service")
        for name, limit in (("event_key", 255), ("provider", 128), ("model", 255)):
            value = getattr(self, name)
            if value is None and name == "model":
                continue
            if not isinstance(value, str) or not value or len(value) > limit:
                raise ValueError(f"invalid {name}")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("usage timestamp must include timezone")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        for name in COUNT_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if name not in SERVICE_FIELDS[self.service]:
                raise ValueError(f"{name} does not belong to {self.service}")
            if name.endswith("seconds"):
                if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                    raise ValueError(f"{name} must be a nonnegative finite Decimal")
            elif type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.cache_write_ttl_seconds not in (None, 300, 3600):
            raise ValueError("unsupported cache write TTL")
        if self.observed_amount is not None:
            if (
                not isinstance(self.observed_amount, Decimal)
                or not self.observed_amount.is_finite()
                or self.observed_amount < 0
            ):
                raise ValueError("observed amount must be a nonnegative finite Decimal")
        if (self.observed_currency is None) != (self.observed_amount is None):
            raise ValueError("observed amount requires a currency")
        if self.observed_currency is not None and (
            len(self.observed_currency) != 3
            or not self.observed_currency.isalpha()
            or not self.observed_currency.isupper()
        ):
            raise ValueError("currency must be an uppercase three-letter code")

    def canonical(self) -> dict:
        return {
            key: decimal_text(value)
            if isinstance(value, Decimal)
            else value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
            for key, value in asdict(self).items()
        }

    @property
    def digest(self) -> str:
        identity = self.canonical()
        if self.occurred_at_is_receipt:
            # Helper retries may arrive later; the original receipt owns UTC bucketing.
            # Explicit event timestamps remain part of semantic conflict detection.
            identity.pop("occurred_at")
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PriceProvenance:
    version: str
    source_url: str
    verified_on: date
    billing_basis: str


@dataclass(frozen=True, slots=True)
class UsageRate:
    metric: str
    price: Decimal
    units: Decimal = Decimal(1)

    def __post_init__(self):
        if self.metric not in COUNT_FIELDS:
            raise ValueError("unsupported pricing metric")
        if (
            not isinstance(self.price, Decimal)
            or not self.price.is_finite()
            or self.price < 0
            or not isinstance(self.units, Decimal)
            or not self.units.is_finite()
            or self.units <= 0
        ):
            raise ValueError("invalid rate")


@dataclass(frozen=True, slots=True)
class PriceCard:
    provider: str
    service: Service
    model: str | None
    currency: str
    rates: tuple[UsageRate, ...]
    provenance: PriceProvenance

    def __post_init__(self):
        object.__setattr__(self, "rates", tuple(self.rates))
        if len({rate.metric for rate in self.rates}) != len(self.rates):
            raise ValueError("duplicate metric rates")
        if (
            len(self.currency) != 3
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise ValueError("invalid currency")


@dataclass(frozen=True, slots=True)
class CostLine:
    metric: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    units: Decimal = Decimal(1)


@dataclass(frozen=True, slots=True)
class PricedUsage:
    amount: Decimal | None
    currency: str | None
    lines: tuple[CostLine, ...]
    provenance: PriceProvenance | None
    unpriced_reason: str | None = None


ANTHROPIC_PROVENANCE = PriceProvenance(
    "anthropic-direct-standard-2026-10-26",
    "https://platform.claude.com/docs/en/models/haiku-5-5/overview",
    date(2026, 10, 26),
    "Direct Claude API, standard synchronous global inference; USD per million tokens",
)


def anthropic_price_card(event: UsageEvent) -> PriceCard | None:
    """Exact reviewed model only; never infer aliases, account discounts or FX."""
    if (
        event.provider != "anthropic"
        or event.service != "llm"
        or event.model != "claude-haiku-5-5"
    ):
        return None
    tokens = (event.input_tokens, event.cache_read_tokens, event.cache_write_tokens)
    if any(value is None for value in tokens):
        return None
    if event.cache_write_tokens and event.cache_write_ttl_seconds is None:
        return None
    high = sum(value or 0 for value in tokens) > 100_000
    prices = (
        ("0.50", "0.05", "1" if event.cache_write_ttl_seconds == 3600 else "0.625", "2.50")
        if high
        else (
            "0.10",
            "0.01",
            "0.20" if event.cache_write_ttl_seconds == 3600 else "0.125",
            "0.50",
        )
    )
    return PriceCard(
        event.provider,
        "llm",
        event.model,
        "USD",
        tuple(
            UsageRate(metric, Decimal(price), Decimal(1_000_000))
            for metric, price in zip(SERVICE_FIELDS["llm"], prices, strict=True)
        ),
        ANTHROPIC_PROVENANCE,
    )


def price_usage(event: UsageEvent, card: PriceCard | None = None) -> PricedUsage:
    if event.observed_amount is not None:
        return PricedUsage(
            event.observed_amount,
            event.observed_currency,
            (
                CostLine(
                    "observed_amount", Decimal(1), event.observed_amount, event.observed_amount
                ),
            ),
            None,
        )
    if card is None:
        return PricedUsage(None, None, (), None, "rate_or_billing_basis_unknown")
    if (card.provider, card.service, card.model) != (
        event.provider,
        event.service,
        event.model,
    ):
        raise ValueError("price card does not match usage")
    lines = []
    for rate in card.rates:
        quantity = getattr(event, rate.metric)
        if quantity is None:
            return PricedUsage(None, card.currency, (), card.provenance, "usage_unknown")
        amount = Decimal(quantity) * rate.price / rate.units
        lines.append(CostLine(rate.metric, Decimal(quantity), rate.price, amount, rate.units))
    if not lines:
        return PricedUsage(None, card.currency, (), card.provenance, "rate_unknown")
    return PricedUsage(
        sum((line.amount for line in lines), Decimal(0)),
        card.currency,
        tuple(lines),
        card.provenance,
    )
