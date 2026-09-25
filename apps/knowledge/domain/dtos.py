from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class KnowledgeRetrievalStrategy(StrEnum):
    POSTGRES_FULL_TEXT = "POSTGRES_FULL_TEXT"
    POSTGRES_TRIGRAM = "POSTGRES_TRIGRAM"
    POSTGRES_VECTOR = "POSTGRES_VECTOR"
    HYBRID_RRF = "HYBRID_RRF"
    POSTGRES_FALLBACK = "POSTGRES_FALLBACK"
    CACHE = "CACHE"


class KnowledgeNoAnswerReason(StrEnum):
    EMPTY_QUERY = "EMPTY_QUERY"
    DYNAMIC_BUSINESS_DATA = "DYNAMIC_BUSINESS_DATA"
    NO_AUTHORIZED_SOURCE = "NO_AUTHORIZED_SOURCE"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    UNSAFE_CONTEXT = "UNSAFE_CONTEXT"
    MODEL_REJECTED_EVIDENCE = "MODEL_REJECTED_EVIDENCE"


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgePrincipal:
    """Server-derived authorization facts used before retrieval."""

    user_id: UUID | None = None
    roles: tuple[str, ...] = ()
    store_ids: tuple[UUID, ...] = ()
    is_staff: bool = False
    allow_internal: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeQuery:
    text: str
    principal: KnowledgePrincipal
    language: str = "fr"
    namespace: str = "customer-help"
    limit: int = 5
    correlation_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class KnowledgeChunkResult:
    chunk_id: UUID
    source_id: UUID
    source_key: str
    source_title: str
    content: str
    chunk_index: int
    score: float
    source_version: int = 1
    canonical_url: str | None = None
    lexical_score: float | None = None
    semantic_score: float | None = None
    section_path: str = ""


@dataclass(frozen=True, kw_only=True)
class KnowledgeRetrievalResult:
    chunks: tuple[KnowledgeChunkResult, ...]
    strategy: KnowledgeRetrievalStrategy
    query_hash: str = ""
    acl_fingerprint: str = ""
    cache_hit: bool = False
    no_answer_reason: KnowledgeNoAnswerReason | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeCitation:
    ordinal: int
    source_id: UUID
    source_key: str
    source_title: str
    source_version: int
    chunk_ids: tuple[UUID, ...]
    canonical_url: str | None = None


@dataclass(frozen=True, kw_only=True)
class GroundedKnowledgeAnswer:
    answer: str
    grounded: bool
    insufficient_knowledge: bool
    source_ids: tuple[UUID, ...]
    chunk_ids: tuple[UUID, ...]
    confidence: float | None = None
    citations: tuple[KnowledgeCitation, ...] = ()
    no_answer_reason: KnowledgeNoAnswerReason | None = None
