"""Embedding boundary and pgvector persistence without ORM coupling.

The physical vector column is managed by a PostgreSQL-only migration. Keeping
it outside Django model state lets lexical retrieval and non-PostgreSQL tooling
continue to work when the optional vector capability is unavailable.
"""

import json
import logging
import math
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.db import connection

from apps.knowledge.domain.exceptions import KnowledgeEmbeddingError

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider:
    is_external = True

    def __init__(
        self,
        client=None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        is_external: bool = True,
    ) -> None:
        api_key = api_key or settings.OPENAI_API_KEY
        if not api_key:
            raise KnowledgeEmbeddingError("An embedding API key is required.")
        if not settings.AI_RAG_EMBEDDING_MODEL:
            raise KnowledgeEmbeddingError("AI_RAG_EMBEDDING_MODEL is required.")
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
        self.client = client
        self.is_external = is_external

    @property
    def model(self) -> str:
        return settings.AI_RAG_EMBEDDING_MODEL

    @property
    def dimensions(self) -> int:
        return settings.AI_RAG_EMBEDDING_DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not value.strip() for value in texts):
            raise KnowledgeEmbeddingError("Embedding input must contain text.")
        request = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
            "timeout": settings.AI_TIMEOUT_SECONDS,
        }
        if settings.AI_RAG_EMBEDDING_SEND_DIMENSIONS:
            request["dimensions"] = self.dimensions
        response = self.client.embeddings.create(**request)
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(texts))):
            raise KnowledgeEmbeddingError(
                "Embedding provider returned invalid result indexes."
            )
        vectors = [list(item.embedding) for item in ordered]
        _validate_vectors(vectors, expected=len(texts), dimensions=self.dimensions)
        return vectors


def get_embedding_provider():
    provider = settings.AI_RAG_EMBEDDING_PROVIDER.lower()
    if provider == "openai":
        return OpenAIEmbeddingProvider()
    if provider == "openai_compatible":
        if not settings.AI_RAG_EMBEDDING_BASE_URL:
            raise KnowledgeEmbeddingError(
                "AI_RAG_EMBEDDING_BASE_URL is required for a local adapter."
            )
        return OpenAIEmbeddingProvider(
            api_key=settings.AI_RAG_EMBEDDING_API_KEY or "local",
            base_url=settings.AI_RAG_EMBEDDING_BASE_URL,
            is_external=False,
        )
    raise KnowledgeEmbeddingError(
        f"No embedding adapter is configured for provider '{provider}'."
    )


def _validate_vectors(
    vectors: list[list[float]], *, expected: int, dimensions: int
) -> None:
    if len(vectors) != expected:
        raise KnowledgeEmbeddingError("Embedding provider returned the wrong count.")
    for vector in vectors:
        if len(vector) != dimensions:
            raise KnowledgeEmbeddingError(
                "Embedding provider returned an unexpected dimension."
            )
        if any(not math.isfinite(float(value)) for value in vector):
            raise KnowledgeEmbeddingError("Embedding contains a non-finite value.")


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    chunk_id: UUID
    score: float


class PostgresVectorStore:
    """Small SQL adapter around the optional ``vector`` column."""

    def available(self) -> bool:
        if connection.vendor != "postgresql":
            return False
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'vector'
                    ) AND EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'knowledge_knowledgechunk'
                          AND column_name = 'embedding'
                    )
                    """
                )
                return bool(cursor.fetchone()[0])
        except Exception:
            logger.warning("knowledge_vector_capability_check_failed")
            return False

    def upsert(
        self,
        *,
        chunk_id: UUID,
        vector: list[float],
        model: str,
        content_hash: str,
    ) -> bool:
        payload = json.dumps(vector, separators=(",", ":"))
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE knowledge_knowledgechunk
                SET embedding = %s::vector,
                    embedding_model = %s,
                    embedding_content_hash = %s,
                    embedding_status = 'READY',
                    embedding_error = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                [payload, model, content_hash, chunk_id],
            )
            return cursor.rowcount == 1

    def search(
        self,
        *,
        vector: list[float],
        source_ids: list[UUID],
        model: str,
        limit: int,
    ) -> list[SemanticMatch]:
        if not source_ids:
            return []
        payload = json.dumps(vector, separators=(",", ":"))
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, 1 - (embedding <=> %s::vector) AS similarity
                FROM knowledge_knowledgechunk
                WHERE source_id = ANY(%s::uuid[])
                  AND embedding IS NOT NULL
                  AND embedding_status = 'READY'
                  AND embedding_model = %s
                  AND injection_risk < %s
                ORDER BY embedding <=> %s::vector, id
                LIMIT %s
                """,
                [
                    payload,
                    [str(value) for value in source_ids],
                    model,
                    settings.AI_RAG_MAX_CHUNK_INJECTION_RISK,
                    payload,
                    limit,
                ],
            )
            return [
                SemanticMatch(chunk_id=row[0], score=max(0.0, float(row[1])))
                for row in cursor.fetchall()
            ]
