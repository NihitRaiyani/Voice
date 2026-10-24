"""Approval/eligibility rules shared by SQL storage and offline tests."""

import json
import re
import unicodedata
from datetime import UTC, datetime
from hashlib import sha256

from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import CallState
from roma.domain.knowledge.routing import KnowledgeRoute, classify_turn
from roma.domain.safety.filter import screen
from roma.domain.safety.inputs import injection_attempt
from roma.domain.safety.lexicon import BlockCategory
from roma.domain.safety.normalize import tokens


class KnowledgeError(ValueError):
    def __init__(self, code="KNOWLEDGE_INVALID", status_code=422):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def validate_content(value: str) -> None:
    """Uploads are evidence, never instructions, protected claims or caller records."""
    if not isinstance(value, str) or not value.strip() or len(value) > 20000:
        raise KnowledgeError()
    if any(unicodedata.category(char) in {"Cc", "Cf"} and char not in "\n\t" for char in value):
        raise KnowledgeError()
    if injection_attempt(value) or not screen(value).allowed:
        raise KnowledgeError("KNOWLEDGE_UNSAFE")
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+?\d[\s().-]*){8,}", value):
        raise KnowledgeError("KNOWLEDGE_PRIVATE_CONTENT")
    # Protected categories stay code-owned even where a lexical screen finds no amount.
    decision = classify_turn(CallState(stage=ConversationStage.VALUE), value)
    if decision.route == KnowledgeRoute.SAFETY and (
        decision.category != BlockCategory.CERT
        or set(tokens(value)) & {"certificate", "certification", "प्रमाणपत्र"}
    ):
        raise KnowledgeError("KNOWLEDGE_UNSAFE")
    if set(tokens(value)) & {"guaranteed", "guarantee", "guarantees", "गारंटी", "ગેરંટી"}:
        raise KnowledgeError("KNOWLEDGE_UNSAFE")


def content_hash(document) -> str:
    fields = (
        "source_key",
        "source_reference",
        "title",
        "version",
        "category",
        "language",
        "owner_id",
        "course_id",
        "branch_id",
        "effective_from",
        "effective_until",
        "content",
    )
    payload = {}
    for key in fields:
        value = getattr(document, key)
        payload[key] = (
            value.astimezone(UTC).isoformat()
            if isinstance(value, datetime)
            else str(value)
            if value is not None
            else None
        )
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def eligible(document, *, now: datetime, language: str, course_id=None, branch_id=None) -> bool:
    if now.tzinfo is None:
        raise KnowledgeError()
    return bool(
        document.status == "active"
        and document.approved_by is not None
        and document.approved_at is not None
        and document.approved_at <= now
        and document.approval_reference
        and document.approved_hash == document.content_hash == content_hash(document)
        and document.effective_from <= now
        and (document.effective_until is None or now < document.effective_until)
        and document.language == language
        and (document.course_id is None or document.course_id == course_id)
        and (document.branch_id is None or document.branch_id == branch_id)
    )


def approve(document, *, actor_id, reference: str, now: datetime) -> None:
    if document.status != "draft":
        raise KnowledgeError("KNOWLEDGE_STATE_CONFLICT", 409)
    validate_content(document.content)
    if document.content_hash != content_hash(document):
        raise KnowledgeError("KNOWLEDGE_CONTENT_CHANGED", 409)
    document.status = "approved"
    document.approved_by, document.approved_at = actor_id, now
    document.approval_reference, document.approved_hash = reference, document.content_hash


def activate(document, *, now: datetime) -> None:
    if not (
        document.status == "approved"
        and document.approved_by is not None
        and document.approved_at is not None
        and document.approved_at <= now
        and document.approval_reference
        and document.approved_hash == document.content_hash == content_hash(document)
    ):
        raise KnowledgeError("KNOWLEDGE_STATE_CONFLICT", 409)
    if document.effective_from > now or (
        document.effective_until and document.effective_until <= now
    ):
        raise KnowledgeError("KNOWLEDGE_OUTSIDE_EFFECTIVE_DATES", 409)
    document.status, document.activated_at = "active", now


def archive(document, *, now: datetime) -> None:
    if document.status != "active":
        raise KnowledgeError("KNOWLEDGE_STATE_CONFLICT", 409)
    document.status, document.archived_at = "archived", now
