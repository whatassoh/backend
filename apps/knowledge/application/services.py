"""Stable public imports for the knowledge application layer."""

from apps.knowledge.application.answers import KnowledgeAnswerService
from apps.knowledge.application.chunking import chunk_content
from apps.knowledge.application.ingestion import (
    KnowledgeGrantInput,
    KnowledgeIngestionService,
    KnowledgeSourceInput,
)
from apps.knowledge.application.retrieval import HybridKnowledgeRetriever
from apps.knowledge.domain.dtos import KnowledgePrincipal, KnowledgeQuery


class PostgresKnowledgeRetriever(HybridKnowledgeRetriever):
    """Backward-compatible adapter around the ACL-first hybrid retriever."""

    def retrieve(self, query, *, language=None, limit=5):
        if isinstance(query, KnowledgeQuery):
            return super().retrieve(query)
        return super().retrieve(
            KnowledgeQuery(
                text=str(query or ""),
                principal=KnowledgePrincipal(),
                language=language or "fr",
                limit=limit,
            )
        )


__all__ = [
    "HybridKnowledgeRetriever",
    "KnowledgeAnswerService",
    "KnowledgeGrantInput",
    "KnowledgeIngestionService",
    "KnowledgeSourceInput",
    "PostgresKnowledgeRetriever",
    "chunk_content",
]
