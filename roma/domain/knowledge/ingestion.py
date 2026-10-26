"""Bounded Markdown/plain-text preparation. Content is evidence, never executable instructions."""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from hashlib import sha256

from roma.core.ai_contracts import EmbeddingIdentity
from roma.domain.safety.inputs import injection_attempt

PIPELINE_VERSION = "markdown-sections-rows-v1"
_INSTRUCTION_MARKUP = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:system|developer|assistant|tool)\s*:|```|~~~|<\s*/?\s*(?:script|iframe|system|assistant|tool)\b",
    re.I | re.M,
)


class IngestionError(ValueError):
    def __init__(self, reason: str, *, quarantine: bool = False):
        self.reason, self.quarantine = reason, quarantine
        super().__init__(reason)


@dataclass(frozen=True)
class IngestionChunk:
    ordinal: int
    content: str
    content_hash: str
    language: str
    source_section: str
    quarantined: bool = False


def pipeline_identity(identity: EmbeddingIdentity) -> str:
    payload = {"pipeline": PIPELINE_VERSION, "embedding": asdict(identity)}
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_document(content: str) -> str:
    if not isinstance(content, str) or not content.strip() or len(content) > 20000:
        raise IngestionError("KNOWLEDGE_CONTENT_INVALID")
    if any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\r\t" for c in content):
        raise IngestionError("KNOWLEDGE_CONTENT_INVALID", quarantine=True)
    content = unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in content.splitlines()).strip()


def chunk_document(
    content: str, *, language: str, max_chars: int = 2000
) -> tuple[IngestionChunk, ...]:
    """Repeat section/table headers while keeping every table row whole and ordered."""
    if language not in {"hi", "en", "gu", "hi-en"} or not 64 <= max_chars <= 2000:
        raise IngestionError("KNOWLEDGE_CHUNK_CONFIG_INVALID")
    normalized = normalize_document(content)
    quarantined = injection_attempt(" ".join(normalized.split())) or bool(
        _INSTRUCTION_MARKUP.search(normalized)
    )
    headings: list[tuple[int, str]] = []
    pieces: list[tuple[str, str]] = []
    current = ""
    table_header: list[str] = []

    def context():
        return "\n".join(line for _, line in headings)

    def section():
        return "section-" + sha256(context().encode()).hexdigest()[:24]

    def emit():
        nonlocal current
        if current:
            pieces.append((current, section()))
            current = ""

    def add(line: str, *, row: bool = False):
        nonlocal current
        prefix = context()
        repeated = "\n".join(table_header) if row else ""
        base = "\n".join(value for value in (prefix, repeated) if value)
        if len(base) + bool(base) + len(line) > max_chars:
            if row:
                raise IngestionError("KNOWLEDGE_ROW_TOO_LARGE")
            # Split ordinary prose at whitespace, preserving Indic combining marks.
            words = line.split()
            if len(words) < 2:
                raise IngestionError("KNOWLEDGE_TEXT_TOO_LARGE")
            for word in words:
                if len(prefix) + bool(prefix) + len(word) > max_chars:
                    raise IngestionError("KNOWLEDGE_TEXT_TOO_LARGE")
                candidate = (
                    (current + " " + word)
                    if current
                    else "\n".join(filter(None, (prefix, word)))
                )
                if len(candidate) > max_chars:
                    emit()
                    current = "\n".join(filter(None, (prefix, word)))
                else:
                    current = candidate
            return
        candidate = current + "\n" + line if current else "\n".join(filter(None, (base, line)))
        if len(candidate) > max_chars:
            emit()
            current = "\n".join(filter(None, (base, line)))
        else:
            current = candidate

    lines = normalized.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^(#{1,6})\s+\S.*$", line)
        if heading:
            emit()
            level = len(heading[1])
            headings = [(n, value) for n, value in headings if n < level]
            headings.append((level, line))
            if len(context()) >= max_chars:
                raise IngestionError("KNOWLEDGE_HEADING_TOO_LARGE")
            table_header = []
            i += 1
            continue
        is_table = (
            "|" in line and i + 1 < len(lines) and bool(re.match(r"^[\s|:-]+$", lines[i + 1]))
        )
        if is_table:
            emit()
            table_header = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                add(lines[i], row=True)
                i += 1
            emit()
            table_header = []
            continue
        if line.strip():
            add(line)
        i += 1
    emit()
    if not pieces:
        raise IngestionError("KNOWLEDGE_EMPTY_CHUNKS")
    if len(pieces) > 100:
        raise IngestionError("KNOWLEDGE_TOO_MANY_CHUNKS")
    return tuple(
        IngestionChunk(
            i, text, sha256(text.encode()).hexdigest(), language, source, quarantined
        )
        for i, (text, source) in enumerate(pieces)
    )


def matches_canonical_chunk(
    canonical: tuple[IngestionChunk, ...],
    *,
    ordinal: int,
    content: str,
    content_hash: str,
    language: str,
    source_section: str,
) -> bool:
    """A self-hash proves consistency; these fields prove derivation from the original."""
    if type(ordinal) is not int or not 0 <= ordinal < len(canonical):
        return False
    expected = canonical[ordinal]
    return not expected.quarantined and (
        ordinal,
        content,
        content_hash,
        language,
        source_section,
    ) == (
        expected.ordinal,
        expected.content,
        expected.content_hash,
        expected.language,
        expected.source_section,
    )
