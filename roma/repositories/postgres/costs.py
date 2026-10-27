"""Atomic usage receipts and precise cost projections over existing evidence tables."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Numeric, cast, distinct, func, select
from sqlalchemy.dialects.postgresql import insert

from roma.domain.costs.usage import (
    COUNT_FIELDS,
    SERVICE_FIELDS,
    PricedUsage,
    UsageEvent,
    decimal_text,
)
from roma.domain.persistence import PersistenceConflict
from roma.repositories.postgres.models import Call, CallCost, ProviderUsage


class CostPostgresRepository:
    def __init__(self, session):
        self.session = session

    async def record(self, event: UsageEvent, priced: PricedUsage) -> bool:
        provenance = None
        if priced.provenance:
            provenance = asdict(priced.provenance)
            provenance["verified_on"] = priced.provenance.verified_on.isoformat()
        metadata = {
            "cost_engine": "v1",
            "digest": event.digest,
            "usage": event.canonical(),
            "pricing": {
                "priced": priced.amount is not None,
                "currency": priced.currency,
                "unpriced_reason": priced.unpriced_reason,
                "provenance": provenance,
            },
        }
        primary = next(
            (name for name in COUNT_FIELDS if getattr(event, name) is not None), None
        )
        usage_id = uuid4()
        inserted = await self.session.scalar(
            insert(ProviderUsage)
            .values(
                id=usage_id,
                call_id=event.call_id,
                turn_id=event.turn_id,
                provider=event.provider,
                service=event.service,
                model=event.model,
                measured_units=Decimal(getattr(event, primary)) if primary else Decimal(0),
                unit_name=primary or "unknown",
                provider_metadata=metadata,
                occurred_at=event.occurred_at,
                idempotency_key=event.event_key,
            )
            .on_conflict_do_nothing(index_elements=[ProviderUsage.idempotency_key])
            .returning(ProviderUsage.id)
        )
        if inserted is None:
            previous = await self.session.scalar(
                select(ProviderUsage.provider_metadata).where(
                    ProviderUsage.idempotency_key == event.event_key
                )
            )
            if previous is None or previous.get("digest") != event.digest:
                raise PersistenceConflict("usage event key conflicts with recorded usage")
            return False
        for index, line in enumerate(priced.lines):
            # Existing amount is a compatibility four-decimal projection. The exact
            # Decimal is preserved and aggregated from metadata, without float loss.
            self.session.add(
                CallCost(
                    id=uuid4(),
                    call_id=event.call_id,
                    provider_usage_id=usage_id,
                    provider=event.provider,
                    service=event.service,
                    model=event.model,
                    quantity=line.quantity / line.units,
                    unit=line.metric,
                    unit_price=line.unit_price,
                    amount=line.amount,
                    currency=priced.currency,
                    pricing_version=priced.provenance.version
                    if priced.provenance
                    else "provider-observed-v1",
                    metadata_={
                        "cost_engine": "v1",
                        "exact_amount": decimal_text(line.amount),
                        "raw_quantity": decimal_text(line.quantity),
                        "rate_units": decimal_text(line.units),
                        "provenance": provenance,
                    },
                    occurred_at=event.occurred_at,
                    idempotency_key=f"cost:{usage_id}:{index}",
                )
            )
        await self.session.flush()
        return True

    async def call_exists(self, call_id: UUID) -> bool:
        return await self.session.scalar(select(Call.id).where(Call.id == call_id)) is not None

    def _conditions(self, start, end, provider, call_id, turn_id=None):
        conditions = [ProviderUsage.provider_metadata["cost_engine"].astext == "v1"]
        if start is not None:
            conditions += [ProviderUsage.occurred_at >= start, ProviderUsage.occurred_at < end]
        if provider is not None:
            conditions.append(ProviderUsage.provider == provider)
        if call_id is not None:
            conditions.append(ProviderUsage.call_id == call_id)
        if turn_id is not None:
            conditions.append(ProviderUsage.turn_id == turn_id)
        return conditions

    async def group_values(
        self, group: str, *, start=None, end=None, provider=None, call_id=None
    ):
        column = ProviderUsage.provider if group == "provider" else ProviderUsage.turn_id
        return (
            await self.session.scalars(
                select(distinct(column))
                .where(*self._conditions(start, end, provider, call_id))
                .order_by(column)
            )
        ).all()

    async def summary(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        provider: str | None = None,
        call_id: UUID | None = None,
        turn_id: UUID | None = None,
    ) -> dict:
        conditions = self._conditions(start, end, provider, call_id, turn_id)
        priced_flag = ProviderUsage.provider_metadata["pricing"]["priced"].astext == "true"
        stats = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(~priced_flag),
                    func.count(distinct(ProviderUsage.call_id)),
                ).where(*conditions)
            )
        ).one()
        units = {}
        unknown = {}
        for metric in COUNT_FIELDS:
            value = cast(ProviderUsage.provider_metadata["usage"][metric].astext, Numeric)
            relevant_services = [
                service for service, fields in SERVICE_FIELDS.items() if metric in fields
            ]
            total, count, relevant_count = (
                await self.session.execute(
                    select(
                        func.sum(value),
                        func.count(value),
                        func.count().filter(ProviderUsage.service.in_(relevant_services)),
                    ).where(*conditions)
                )
            ).one()
            if count:
                units[metric] = decimal_text(total)
            unknown[metric] = relevant_count - count
        exact_amount = cast(CallCost.metadata_["exact_amount"].astext, Numeric)
        money = (
            await self.session.execute(
                select(
                    CallCost.currency,
                    func.sum(exact_amount),
                    func.count(distinct(ProviderUsage.id)),
                    func.count(distinct(ProviderUsage.call_id)),
                )
                .join(ProviderUsage, CallCost.provider_usage_id == ProviderUsage.id)
                .where(*conditions, CallCost.metadata_["cost_engine"].astext == "v1")
                .group_by(CallCost.currency)
                .order_by(CallCost.currency)
            )
        ).all()
        return {
            "usage_events": stats[0],
            "unpriced_events": stats[1],
            "complete": stats[0] > 0 and stats[1] == 0,
            "call_count": stats[2],
            "units": units,
            "unknown_units": unknown,
            "currencies": [
                {
                    "currency": currency,
                    "amount": decimal_text(amount),
                    "priced_events": count,
                    "calls_with_priced_usage": calls,
                    "average_per_call": decimal_text(amount / calls) if calls else None,
                }
                for currency, amount, count, calls in money
            ],
        }
