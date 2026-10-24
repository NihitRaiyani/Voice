"""Strict bounded JSON governance contracts. No file/network/model ingestion."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

KnowledgeCategory = Literal["course", "policy", "branch", "schedule"]
KnowledgeLanguage = Literal["hi", "en", "gu", "hi-en"]


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class UploadKnowledgeRequest(KnowledgeRequest):
    source_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")
    source_reference: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")
    title: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1, le=1000000)
    category: KnowledgeCategory
    language: KnowledgeLanguage
    owner_id: UUID = Field(strict=False)
    course_id: UUID | None = Field(default=None, strict=False)
    branch_id: UUID | None = Field(default=None, strict=False)
    effective_from: AwareDatetime = Field(strict=False)
    effective_until: AwareDatetime | None = Field(default=None, strict=False)
    content: str = Field(min_length=1, max_length=20000, repr=False)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("Effective end must follow start")
        return self


class ApproveKnowledgeRequest(KnowledgeRequest):
    approval_reference: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")


class KnowledgeActionRequest(KnowledgeRequest):
    expected_version: int = Field(ge=1, le=1000000)


class RetrievalMetadata(KnowledgeRequest):
    request_id: UUID = Field(strict=False)
    call_id: UUID | None = Field(default=None, strict=False)
    turn_id: UUID | None = Field(default=None, strict=False)
    chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)
    scores: list[Annotated[FiniteFloat, Field(ge=-1, le=1)]] = Field(
        default_factory=list, max_length=20
    )
    fallback_used: bool
    route: Literal["knowledge", "no_evidence"]
    category: KnowledgeCategory
    outcome: Literal["eligible", "no_evidence", "rejected", "unavailable"]
    eligible_count: int = Field(ge=0, le=100)
    selected_document_id: UUID | None = Field(default=None, strict=False)
    latency_ms: int = Field(ge=0, le=60000)

    @model_validator(mode="after")
    def matching_evidence(self):
        if len(self.chunk_ids) != len(self.scores) or len(set(self.chunk_ids)) != len(
            self.chunk_ids
        ):
            raise ValueError("Chunk scores must identify unique evidence")
        if self.turn_id is not None and self.call_id is None:
            raise ValueError("Turn evidence requires call identity")
        return self


class ChunkMetadata(KnowledgeRequest):
    source_section: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")
    document_version: int = Field(ge=1, le=1000000)
