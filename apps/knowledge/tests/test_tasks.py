from types import SimpleNamespace

import pytest

from apps.knowledge.domain.enums import KnowledgeEmbeddingStatus
from apps.knowledge.infrastructure.models import KnowledgeChunk, KnowledgeSource
from apps.knowledge.tasks import index_source_embeddings


class RecordingProvider:
    model = "test-embedding"
    dimensions = 4

    def __init__(self, *, is_external=False):
        self.is_external = is_external
        self.calls = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class RecordingVectorStore:
    def __init__(self):
        self.writes = []

    def available(self):
        return True

    def upsert(self, *, chunk_id, vector, model, content_hash):
        self.writes.append(
            SimpleNamespace(
                chunk_id=chunk_id,
                vector=vector,
                model=model,
                content_hash=content_hash,
            )
        )
        KnowledgeChunk.objects.filter(id=chunk_id).update(
            embedding_status=KnowledgeEmbeddingStatus.READY,
            embedding_model=model,
            embedding_content_hash=content_hash,
        )
        return True


def source_with_chunk(*, status=KnowledgeEmbeddingStatus.PENDING, sensitivity="PUBLIC"):
    source = KnowledgeSource.objects.create(
        key="worker-test",
        title="Worker",
        source_type="GUIDE",
        status="ACTIVE",
        audience="PUBLIC" if sensitivity == "PUBLIC" else "INTERNAL",
        sensitivity=sensitivity,
        content="Validated content.",
    )
    chunk = KnowledgeChunk.objects.create(
        source=source,
        chunk_index=0,
        content="Validated content.",
        content_hash="a" * 64,
        embedding_status=status,
    )
    return source, chunk


@pytest.mark.django_db
def test_embedding_worker_indexes_pending_chunk_once(settings):
    settings.AI_RAG_SEMANTIC_ENABLED = True
    settings.AI_RAG_EMBEDDING_DIMENSIONS = 4
    settings.AI_RAG_EMBEDDING_BATCH_SIZE = 32
    settings.AI_RAG_EMBEDDING_MAX_ATTEMPTS = 5
    source, chunk = source_with_chunk()
    provider = RecordingProvider()
    store = RecordingVectorStore()

    indexed = index_source_embeddings(source.id, provider=provider, vector_store=store)

    chunk.refresh_from_db()
    assert indexed == 1
    assert len(provider.calls) == 1
    assert len(store.writes) == 1
    assert chunk.embedding_status == KnowledgeEmbeddingStatus.READY


@pytest.mark.django_db
def test_embedding_worker_does_not_reclaim_live_indexing_lease(settings):
    settings.AI_RAG_SEMANTIC_ENABLED = True
    settings.AI_RAG_EMBEDDING_DIMENSIONS = 4
    settings.AI_RAG_EMBEDDING_BATCH_SIZE = 32
    settings.AI_RAG_EMBEDDING_MAX_ATTEMPTS = 5
    source, _ = source_with_chunk(status=KnowledgeEmbeddingStatus.INDEXING)
    provider = RecordingProvider()
    store = RecordingVectorStore()

    indexed = index_source_embeddings(source.id, provider=provider, vector_store=store)

    assert indexed == 0
    assert provider.calls == []
    assert store.writes == []


@pytest.mark.django_db
def test_external_provider_cannot_embed_sensitive_source_without_consent(settings):
    settings.AI_RAG_SEMANTIC_ENABLED = True
    settings.AI_RAG_EMBEDDING_DIMENSIONS = 4
    settings.AI_RAG_EMBEDDING_BATCH_SIZE = 32
    settings.AI_RAG_EMBEDDING_MAX_ATTEMPTS = 5
    source, chunk = source_with_chunk(sensitivity="CONFIDENTIAL")
    provider = RecordingProvider(is_external=True)
    store = RecordingVectorStore()

    indexed = index_source_embeddings(source.id, provider=provider, vector_store=store)

    chunk.refresh_from_db()
    assert indexed == 0
    assert provider.calls == []
    assert store.writes == []
    assert chunk.embedding_status == KnowledgeEmbeddingStatus.SKIPPED
