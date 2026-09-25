from typing import Protocol

from apps.knowledge.domain.dtos import KnowledgeQuery, KnowledgeRetrievalResult


class KnowledgeRetriever(Protocol):
    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult: ...


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
