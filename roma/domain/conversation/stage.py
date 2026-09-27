"""The single vocabulary for Roma's seven conversation stages."""

from __future__ import annotations

from enum import StrEnum


class ConversationStage(StrEnum):
    OPEN = "open"
    DISCOVER = "discover"
    VALUE = "value"
    STRUCTURE = "structure"
    PIVOT = "pivot"
    OBJECTION = "objection"
    CLOSE = "close"


__all__ = ["ConversationStage"]
