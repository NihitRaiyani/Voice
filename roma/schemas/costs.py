"""Bounded UTC daily/monthly analytics selection."""

from datetime import date

from pydantic import BaseModel, Field, model_validator


class CostQuery(BaseModel):
    day: date | None = None
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    provider: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_period(self):
        if self.day and self.month:
            raise ValueError("choose day or month")
        if self.day and self.day.year >= 9999:
            raise ValueError("date is outside supported range")
        if self.month:
            month_day = date.fromisoformat(f"{self.month}-01")
            if month_day.year >= 9999:
                raise ValueError("month is outside supported range")
        return self
