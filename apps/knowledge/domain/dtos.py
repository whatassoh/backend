from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class KnowledgeRetrievalStrategy(StrEnum):
    POSTGRES_FULL_TEXT = "POSTGRES_FULL_TEXT"
    POSTGRES_FALLBACK = "POSTGRES_FALLBACK"


@dataclass(frozen=True, kw_only=True)
class KnowledgeChunkResult:
    chunk_id: UUID
    source_id: UUID
    source_key: str
    source_title: str
    content: str
    chunk_index: int
    score: float


@dataclass(frozen=True, kw_only=True)
class KnowledgeRetrievalResult:
    chunks: tuple[KnowledgeChunkResult, ...]
    strategy: KnowledgeRetrievalStrategy


@dataclass(frozen=True, kw_only=True)
class GroundedKnowledgeAnswer:
    answer: str
    grounded: bool
    insufficient_knowledge: bool
    source_ids: tuple[UUID, ...]
    chunk_ids: tuple[UUID, ...]
    confidence: float | None = None
