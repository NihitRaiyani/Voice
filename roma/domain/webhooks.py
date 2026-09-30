"""Transport-independent contract for one logical call-answer event."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AnswerEvent:
    provider_event_id: str
    provider_call_id: str
    direction: str
    payload_digest: str


class WebhookConflict(Exception):
    """An existing event identity was reused with different business input."""


class WebhookUnavailable(Exception):
    """Durable acceptance could not be confirmed; the provider should retry."""


class WebhookStore(Protocol):
    async def accept_answer(self, event: AnswerEvent) -> bool:
        """Commit receipt and effects together; return False for a committed duplicate."""
        ...
