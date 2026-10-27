"""Bounded half-open UTC analytics windows."""

from datetime import UTC, date, datetime, time, timedelta

from pydantic import BaseModel, model_validator


class AnalyticsQuery(BaseModel):
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def valid_window(self):
        start = self.start_date or datetime.now(UTC).date()
        try:
            end = self.end_date or start + timedelta(days=1)
        except OverflowError:
            raise ValueError("date is outside supported range") from None
        if not 0 < (end - start).days <= 366:
            raise ValueError("window must be positive and at most 366 days")
        self.start_date, self.end_date = start, end
        return self

    def bounds(self):
        return (
            datetime.combine(self.start_date, time.min, UTC),
            datetime.combine(self.end_date, time.min, UTC),
        )
