"""Typed public names for Roma's seven conversation stages.

The realtime pipeline still uses the historic prompt phase ids (`p1_open`, ...), because those
ids address prompt files and log lines. `ConversationStage` is the business-facing vocabulary:
the application owns these stages; the LLM only writes inside the selected stage.
"""

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


STAGE_TO_PHASE: dict[ConversationStage, str] = {
    ConversationStage.OPEN: "p1_open",
    ConversationStage.DISCOVER: "p2_discover",
    ConversationStage.VALUE: "p3_value",
    ConversationStage.STRUCTURE: "p4_structure",
    ConversationStage.PIVOT: "p5_pivot",
    ConversationStage.OBJECTION: "p6_objection",
    ConversationStage.CLOSE: "p7_close",
}

PHASE_TO_STAGE: dict[str, ConversationStage] = {
    phase: stage for stage, phase in STAGE_TO_PHASE.items()
}


def phase_for(stage: ConversationStage | str) -> str:
    """Return the legacy prompt phase id for a public stage name."""
    if isinstance(stage, ConversationStage):
        return STAGE_TO_PHASE[stage]
    return STAGE_TO_PHASE[ConversationStage(stage)]


def stage_for(phase: str) -> ConversationStage:
    """Return the public stage for a legacy prompt phase id."""
    try:
        return PHASE_TO_STAGE[phase]
    except KeyError as exc:
        raise ValueError(f"unknown conversation phase {phase!r}") from exc


__all__ = ["ConversationStage", "PHASE_TO_STAGE", "STAGE_TO_PHASE", "phase_for", "stage_for"]
