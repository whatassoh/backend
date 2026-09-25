"""Idempotent background indexing and retention tasks for the RAG corpus."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.knowledge.application.cache import bump_corpus_revision
from apps.knowledge.application.embeddings import (
    PostgresVectorStore,
    get_embedding_provider,
)
from apps.knowledge.application.observability import observe_embedding
from apps.knowledge.domain.enums import (
    KnowledgeEmbeddingStatus,
    KnowledgeSensitivity,
    KnowledgeSourceStatus,
)
from apps.knowledge.domain.exceptions import KnowledgeEmbeddingError
from apps.knowledge.infrastructure.models import (
    KnowledgeChunk,
    KnowledgeRetrievalAudit,
    KnowledgeSource,
)

logger = logging.getLogger(__name__)


def index_source_embeddings(source_id, *, provider=None, vector_store=None) -> int:
    """Index stale chunks in bounded batches and leave failures retryable."""

    source = KnowledgeSource.objects.filter(
        id=source_id, status=KnowledgeSourceStatus.ACTIVE
    ).first()
    if source is None or not settings.AI_RAG_SEMANTIC_ENABLED:
        return 0
    provider = provider or get_embedding_provider()
    vector_store = vector_store or PostgresVectorStore()
    if provider.dimensions != settings.AI_RAG_EMBEDDING_DIMENSIONS:
        raise KnowledgeEmbeddingError("Embedding dimension configuration drifted.")
    if not vector_store.available():
        raise KnowledgeEmbeddingError("PostgreSQL vector capability is unavailable.")
    if (
        getattr(provider, "is_external", True)
        and source.sensitivity != KnowledgeSensitivity.PUBLIC
        and not source.metadata.get("external_embedding_allowed")
    ):
        source.chunks.update(embedding_status=KnowledgeEmbeddingStatus.SKIPPED)
        logger.warning(
            "knowledge_external_embedding_denied",
            extra={"source_id": str(source.id), "sensitivity": source.sensitivity},
        )
        return 0

    total = 0
    batch_size = max(1, min(settings.AI_RAG_EMBEDDING_BATCH_SIZE, 128))
    while True:
        with transaction.atomic():
            chunks = list(
                KnowledgeChunk.objects.select_for_update(skip_locked=True)
                .filter(source=source)
                .filter(
                    Q(embedding_status__in=["PENDING", "FAILED"])
                    | (
                        Q(embedding_status__in=["READY", "SKIPPED"])
                        & (
                            ~Q(embedding_model=provider.model)
                            | ~Q(embedding_content_hash=F("content_hash"))
                        )
                    )
                )
                .filter(embedding_attempts__lt=settings.AI_RAG_EMBEDDING_MAX_ATTEMPTS)
                .order_by("chunk_index")[:batch_size]
            )
            if not chunks:
                break
            chunk_ids = [chunk.id for chunk in chunks]
            KnowledgeChunk.objects.filter(id__in=chunk_ids).update(
                embedding_status=KnowledgeEmbeddingStatus.INDEXING,
                embedding_attempts=F("embedding_attempts") + 1,
                embedding_error="",
                updated_at=timezone.now(),
            )
        try:
            vectors = provider.embed([chunk.content for chunk in chunks])
            for chunk, vector in zip(chunks, vectors, strict=True):
                stored = vector_store.upsert(
                    chunk_id=chunk.id,
                    vector=vector,
                    model=provider.model,
                    content_hash=chunk.content_hash,
                )
                total += int(stored is not False)
            observe_embedding(status="ready", provider=provider.__class__.__name__)
        except Exception as exc:
            KnowledgeChunk.objects.filter(id__in=chunk_ids).update(
                embedding_status=KnowledgeEmbeddingStatus.FAILED,
                embedding_error=exc.__class__.__name__[:80],
                updated_at=timezone.now(),
            )
            observe_embedding(status="failed", provider=provider.__class__.__name__)
            raise KnowledgeEmbeddingError("Embedding batch failed.") from exc
    if total:
        bump_corpus_revision(source.namespace)
    return total


@shared_task(
    name="knowledge.index_source",
    autoretry_for=(KnowledgeEmbeddingError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=4,
)
def index_knowledge_source(source_id: str) -> int:
    return index_source_embeddings(source_id)


@shared_task(name="knowledge.reconcile_embeddings")
def reconcile_embeddings() -> int:
    if not settings.AI_RAG_SEMANTIC_ENABLED:
        return 0
    stale_before = timezone.now() - timedelta(
        seconds=settings.AI_RAG_EMBEDDING_LEASE_SECONDS
    )
    KnowledgeChunk.objects.filter(
        embedding_status=KnowledgeEmbeddingStatus.INDEXING,
        updated_at__lt=stale_before,
    ).update(
        embedding_status=KnowledgeEmbeddingStatus.FAILED,
        embedding_error="STALE_INDEXING_LEASE",
        updated_at=timezone.now(),
    )
    source_ids = list(
        KnowledgeSource.objects.filter(
            status=KnowledgeSourceStatus.ACTIVE,
            chunks__embedding_status__in=[
                KnowledgeEmbeddingStatus.PENDING,
                KnowledgeEmbeddingStatus.FAILED,
            ],
            chunks__embedding_attempts__lt=settings.AI_RAG_EMBEDDING_MAX_ATTEMPTS,
        )
        .order_by("updated_at")
        .values_list("id", flat=True)
        .distinct()[:100]
    )
    for source_id in source_ids:
        index_knowledge_source.delay(str(source_id))
    return len(source_ids)


@shared_task(name="knowledge.expire_retrieval_audits")
def expire_retrieval_audits() -> int:
    cutoff = timezone.now() - timedelta(days=settings.AI_RAG_AUDIT_RETENTION_DAYS)
    deleted, _ = KnowledgeRetrievalAudit.objects.filter(created_at__lt=cutoff).delete()
    return deleted
