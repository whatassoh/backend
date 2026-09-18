from typing import Protocol

from apps.knowledge.domain.dtos import KnowledgeRetrievalResult


class KnowledgeRetriever(Protocol):
    def retrieve(
        self, query: str, *, language: str | None = None, limit: int = 5
    ) -> KnowledgeRetrievalResult: ...
