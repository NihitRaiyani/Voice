"""Content-free bounded supervisor messages; SQL remains the authority."""

from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class SupervisorCost(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(ge=0, max_digits=24, decimal_places=12)


class SupervisorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["call_update"] = "call_update"
    event_id: UUID = Field(default_factory=uuid4)
    call_id: UUID
    observed_at: AwareDatetime
    stage: Literal[
        "open", "discover", "value", "structure", "pivot", "objection", "close", "unknown"
    ]
    language: Literal["gu", "hi", "en", "code_mix", "unknown"]
    turn: int = Field(ge=0, le=10000)
    duration_seconds: float = Field(ge=0, le=86400)
    booking_confirmed: bool
    safety_hits: int = Field(ge=0)
    latency_p95_ms: float | None = Field(default=None, ge=0)
    costs: list[SupervisorCost] = Field(max_length=10)
