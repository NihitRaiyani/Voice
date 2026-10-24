"""Strict known Voice webhook inputs; provider-added fields remain signature inputs."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from roma.core.call_capabilities import Account, Sid

BoundedText = Annotated[str, StringConstraints(max_length=2048)]


class AnswerPayload(BaseModel):
    # Twilio can add parameters without notice. Bound every raw field at transport,
    # verify the entire original mapping, then validate only business inputs here.
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)
    account_sid: Account = Field(alias="AccountSid")
    call_sid: Sid = Field(alias="CallSid")
    direction: Literal["inbound", "outbound-api", "outbound-dial"] | None = Field(
        default=None, alias="Direction"
    )
    status: (
        Literal[
            "queued",
            "initiated",
            "ringing",
            "in-progress",
            "completed",
            "busy",
            "failed",
            "no-answer",
            "canceled",
        ]
        | None
    ) = Field(default=None, alias="CallStatus")
    from_address: BoundedText | None = Field(default=None, alias="From")
    to_address: BoundedText | None = Field(default=None, alias="To")
