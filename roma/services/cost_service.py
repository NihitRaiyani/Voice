"""Cost analytics use cases; conservative spend admission remains independent."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from roma.domain.costs.usage import UsageEvent, anthropic_price_card, price_usage
from roma.repositories.postgres.costs import CostPostgresRepository


class CostCallNotFound(LookupError):
    pass


class CostService:
    def __init__(self, session_factory, *, price_card_resolver=anthropic_price_card):
        self.session_factory = session_factory
        self.price_card_resolver = price_card_resolver

    async def record(self, event: UsageEvent) -> bool:
        priced = price_usage(event, self.price_card_resolver(event))
        async with self.session_factory() as session, session.begin():
            return await CostPostgresRepository(session).record(event, priced)

    async def record_llm(
        self,
        final,
        request_id: str,
        *,
        call_id: UUID | None = None,
        turn_id: UUID | None = None,
        occurred_at: datetime | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> bool:
        """Normalize the existing Claude adapter's cache-inclusive input total.

        charged_inr is a conservative safety reservation, not an invoice. Never
        substitute it for missing measured usage or convert it to USD analytics.
        """
        known = final is not None and final.usage_known
        provider = getattr(final, "provider", None) or provider or "unknown"
        ordinary_input = final.input_tokens if known else None
        read = final.cached_input_tokens if known else None
        write = getattr(final, "cache_creation_input_tokens", None) if known else None
        if provider == "claude":
            provider = "anthropic"
            ordinary_input = (
                ordinary_input - read - write
                if ordinary_input is not None and read is not None and write is not None
                else None
            )
            if ordinary_input is not None and ordinary_input < 0:
                ordinary_input = None
        return await self.record(
            UsageEvent(
                event_key=f"llm:{request_id}",
                provider=provider,
                service="llm",
                model=getattr(final, "model", None) or model,
                call_id=call_id,
                turn_id=turn_id,
                occurred_at=occurred_at or datetime.now(UTC),
                occurred_at_is_receipt=occurred_at is None,
                input_tokens=ordinary_input,
                output_tokens=final.output_tokens if known else None,
                cache_read_tokens=read,
                cache_write_tokens=write,
            )
        )

    async def record_speech(
        self,
        service: Literal["stt", "tts"],
        request_id: str,
        *,
        call_id: UUID | None = None,
        turn_id: UUID | None = None,
        audio_seconds=None,
        characters: int = 0,
        cache_hit: bool = False,
        gpu_seconds=None,
        model: str | None = None,
        provider: str = "unknown",
        occurred_at: datetime | None = None,
    ) -> bool:
        """Map measured speech counters without inventing GPU time or local rates."""
        if service not in ("stt", "tts"):
            raise ValueError("speech service must be stt or tts")
        return await self.record(
            UsageEvent(
                event_key=f"{service}:{request_id}",
                provider=provider,
                service=service,
                model=model,
                call_id=call_id,
                turn_id=turn_id,
                occurred_at=occurred_at or datetime.now(UTC),
                occurred_at_is_receipt=occurred_at is None,
                audio_seconds=Decimal(str(audio_seconds))
                if audio_seconds is not None
                else None,
                gpu_seconds=Decimal(str(gpu_seconds)) if gpu_seconds is not None else None,
                characters=characters if service == "tts" else None,
                cache_hits=int(cache_hit) if service == "tts" else None,
            )
        )

    @staticmethod
    def _window(day: date | None, month: str | None, call_id):
        if day is not None and month is not None:
            raise ValueError("choose day or month")
        if month is not None:
            try:
                if len(month) != 7 or month[4] != "-":
                    raise ValueError
                day = date.fromisoformat(f"{month}-01")
            except (TypeError, ValueError) as exc:
                raise ValueError("month must be YYYY-MM") from exc
            end_day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
        elif day is None and call_id is not None:
            return None, None
        else:
            day = day or datetime.now(UTC).date()
            end_day = day + timedelta(days=1)
        return datetime.combine(day, time.min, UTC), datetime.combine(end_day, time.min, UTC)

    async def summary(
        self,
        *,
        day: date | None = None,
        month: str | None = None,
        provider: str | None = None,
        call_id: UUID | None = None,
        group_by: str | None = None,
    ) -> dict:
        start, end = self._window(day, month, call_id)
        filters = dict(start=start, end=end, provider=provider, call_id=call_id)
        async with self.session_factory() as session:
            repo = CostPostgresRepository(session)
            if call_id is not None and not await repo.call_exists(call_id):
                raise CostCallNotFound("Call was not found.")
            result = await repo.summary(**filters)
            result["period"] = {
                "start": start.isoformat() if start else None,
                "end_exclusive": end.isoformat() if end else None,
                "timezone": "UTC",
            }
            if group_by is not None:
                if group_by not in ("provider", "turn"):
                    raise ValueError("unsupported cost grouping")
                result["groups"] = []
                for group in await repo.group_values(group_by, **filters):
                    if group is None:
                        continue
                    group_filters = filters | {
                        "provider" if group_by == "provider" else "turn_id": group
                    }
                    result["groups"].append(
                        {group_by: str(group), **await repo.summary(**group_filters)}
                    )
            return result
