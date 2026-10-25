"""Minimal metadata and explicit injectable provider erasure contract."""

from dataclasses import dataclass
from typing import Literal, Protocol

ResourceKind = Literal["call", "recording", "transcription"]


@dataclass(frozen=True)
class ResourceMetadata:
    account_sid: str
    sid: str
    status: str
    call_sid: str | None = None
    recording_sid: str | None = None


FailureCode = Literal[
    "absent",
    "authentication_manual",
    "transient_pending",
    "provider_manual",
    "ownership_or_state_denied",
    "ownership_unverified",
]


class ProviderFailure(Exception):
    def __init__(self, code: FailureCode):
        allowed = {
            "absent",
            "authentication_manual",
            "transient_pending",
            "provider_manual",
            "ownership_or_state_denied",
            "ownership_unverified",
        }
        self.code = code if code in allowed else "provider_manual"
        super().__init__("Provider erasure requires attention.")


class ErasureProvider(Protocol):
    @property
    def account_sid(self) -> str: ...
    async def fetch(self, kind: ResourceKind, sid: str) -> ResourceMetadata: ...
    async def delete(self, kind: ResourceKind, sid: str) -> None: ...
