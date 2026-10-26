"""Bounded evidence and scope contracts; retrieval has no conversation authority."""
import math
from dataclasses import dataclass
from uuid import UUID


def cosine_similarity(left, right):
    if (not left or len(left) != len(right)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in (*left, *right))):
        raise ValueError('Finite equal-size vectors required')
    a, b = math.hypot(*left), math.hypot(*right)
    if not a or not b:
        raise ValueError('Nonzero vectors required')
    return max(-1.0, min(1.0, sum(x/a * (y/b) for x, y in zip(left, right, strict=True))))


@dataclass(frozen=True)
class RetrievalFilters:
    category: str
    language: str
    course_id: UUID | None = None
    branch_id: UUID | None = None
    version: int | None = None
    allow_english: bool = True

    def __post_init__(self):
        if (self.category not in {'course', 'policy', 'branch', 'schedule'}
                or self.language not in {'gu', 'hi', 'en', 'hi-en'}
                or type(self.allow_english) is not bool
                or (self.version is not None and (type(self.version) is not int or self.version < 1))
                or any(v is not None and not isinstance(v, UUID) for v in (self.course_id, self.branch_id))):
            raise ValueError('Invalid knowledge scope')

    @property
    def languages(self):
        return tuple(dict.fromkeys((self.language, 'en') if self.allow_english else (self.language,)))


@dataclass(frozen=True)
class Evidence:
    chunk_id: UUID
    document_id: UUID
    content: str
    content_hash: str
    document_version: int
    language: str
    score: float
    source_section: str = 'root'
