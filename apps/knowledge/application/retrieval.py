"""ACL-first lexical, trigram and semantic retrieval with RRF fusion."""

import logging
from collections import defaultdict
from dataclasses import replace
from time import monotonic
from uuid import UUID

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db import DatabaseError, connection
from django.db.models import Q

from apps.knowledge.application.access import acl_fingerprint, visible_sources
from apps.knowledge.application.cache import (
    get_cached_candidate_ids,
    query_hash,
    retrieval_cache_key,
    set_cached_candidate_ids,
)
from apps.knowledge.application.embeddings import PostgresVectorStore
from apps.knowledge.application.observability import observe_retrieval
from apps.knowledge.application.security import safe_evidence_text
from apps.knowledge.domain.dtos import (
    KnowledgeChunkResult,
    KnowledgeNoAnswerReason,
    KnowledgeQuery,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalStrategy,
)
from apps.knowledge.infrastructure.models import (
    KnowledgeChunk,
    KnowledgeRetrievalAudit,
)

logger = logging.getLogger(__name__)
LANGUAGE_CONFIGS = {"fr": "french", "en": "english"}


class HybridKnowledgeRetriever:
    """Retrieve only authorized chunks, then fuse independent ranked lists."""

    def __init__(self, *, embedding_provider=None, vector_store=None) -> None:
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store or PostgresVectorStore()

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        started = monotonic()
        text = " ".join((query.text or "").split())[
            : settings.AI_RAG_MAX_QUERY_CHARACTERS
        ]
        digest = query_hash(text)
        acl_digest = acl_fingerprint(query.principal)
        if not text:
            return self._finish(
                query=query,
                result=KnowledgeRetrievalResult(
                    chunks=(),
                    strategy=KnowledgeRetrievalStrategy.POSTGRES_FALLBACK,
                    query_hash=digest,
                    acl_fingerprint=acl_digest,
                    no_answer_reason=KnowledgeNoAnswerReason.EMPTY_QUERY,
                ),
                started=started,
            )
        limit = max(1, min(query.limit, settings.AI_RAG_MAX_RESULTS))
        sources = visible_sources(
            principal=query.principal,
            namespace=query.namespace,
            language=query.language,
        )
        source_ids = list(sources.values_list("id", flat=True))
        if not source_ids:
            return self._finish(
                query=query,
                result=KnowledgeRetrievalResult(
                    chunks=(),
                    strategy=KnowledgeRetrievalStrategy.POSTGRES_FALLBACK,
                    query_hash=digest,
                    acl_fingerprint=acl_digest,
                    no_answer_reason=KnowledgeNoAnswerReason.NO_AUTHORIZED_SOURCE,
                ),
                started=started,
            )

        cache_key = retrieval_cache_key(
            namespace=query.namespace,
            language=query.language,
            query_digest=digest,
            acl_digest=acl_digest,
            limit=limit,
        )
        cached_ids = get_cached_candidate_ids(cache_key)
        if cached_ids is not None:
            cached = self._rehydrate(cached_ids, source_ids)
            if len(cached) == len(cached_ids):
                return self._finish(
                    query=query,
                    result=KnowledgeRetrievalResult(
                        chunks=tuple(cached[:limit]),
                        strategy=KnowledgeRetrievalStrategy.CACHE,
                        query_hash=digest,
                        acl_fingerprint=acl_digest,
                        cache_hit=True,
                        no_answer_reason=(
                            KnowledgeNoAnswerReason.LOW_RELEVANCE
                            if not cached
                            else None
                        ),
                    ),
                    started=started,
                )

        candidate_limit = max(limit, settings.AI_RAG_CANDIDATE_LIMIT)
        lexical, lexical_strategy = self._lexical(
            text=text,
            source_ids=source_ids,
            language=query.language,
            limit=candidate_limit,
        )
        semantic = self._semantic(
            text=text, source_ids=source_ids, limit=candidate_limit
        )
        chunks, strategy = self._fuse(
            lexical=lexical,
            semantic=semantic,
            source_ids=source_ids,
            limit=limit,
            lexical_strategy=lexical_strategy,
        )
        no_answer_reason = None if chunks else KnowledgeNoAnswerReason.LOW_RELEVANCE
        set_cached_candidate_ids(cache_key, [str(chunk.chunk_id) for chunk in chunks])
        return self._finish(
            query=query,
            result=KnowledgeRetrievalResult(
                chunks=tuple(chunks),
                strategy=strategy,
                query_hash=digest,
                acl_fingerprint=acl_digest,
                no_answer_reason=no_answer_reason,
            ),
            started=started,
        )

    def _lexical(self, *, text, source_ids, language, limit):
        chunks = KnowledgeChunk.objects.filter(
            source_id__in=source_ids,
            injection_risk__lt=settings.AI_RAG_MAX_CHUNK_INJECTION_RISK,
        ).select_related("source")
        if connection.vendor == "postgresql":
            try:
                config = LANGUAGE_CONFIGS.get(language.split("-", 1)[0], "simple")
                search_query = SearchQuery(text, config=config, search_type="websearch")
                ranked = list(
                    chunks.annotate(
                        lexical_score=SearchRank(
                            "search_vector",
                            search_query,
                            cover_density=True,
                            normalization=2,
                        )
                    )
                    .filter(lexical_score__gte=settings.AI_RAG_MIN_LEXICAL_SCORE)
                    .order_by("-lexical_score", "id")[:limit]
                )
                if ranked:
                    return (
                        [(row.id, float(row.lexical_score)) for row in ranked],
                        KnowledgeRetrievalStrategy.POSTGRES_FULL_TEXT,
                    )
                trigram = list(
                    chunks.annotate(
                        lexical_score=TrigramSimilarity("content", text)
                        + TrigramSimilarity("source__title", text)
                    )
                    .filter(lexical_score__gte=settings.AI_RAG_MIN_TRIGRAM_SCORE)
                    .order_by("-lexical_score", "id")[:limit]
                )
                return (
                    [(row.id, float(row.lexical_score)) for row in trigram],
                    KnowledgeRetrievalStrategy.POSTGRES_TRIGRAM,
                )
            except DatabaseError:
                logger.exception("knowledge_postgres_lexical_search_failed")

        terms = [term for term in text.split() if len(term) >= 3][:8]
        if not terms:
            return [], KnowledgeRetrievalStrategy.POSTGRES_FALLBACK
        filters = Q()
        for term in terms:
            filters |= Q(content__icontains=term) | Q(source__title__icontains=term)
        rows = list(chunks.filter(filters).order_by("source_id", "chunk_index")[:limit])
        return (
            [(row.id, 1.0 / (rank + 1)) for rank, row in enumerate(rows)],
            KnowledgeRetrievalStrategy.POSTGRES_FALLBACK,
        )

    def _semantic(self, *, text, source_ids, limit):
        if self.embedding_provider is None or not self.vector_store.available():
            return []
        try:
            vector = self.embedding_provider.embed([text])[0]
            return [
                (match.chunk_id, match.score)
                for match in self.vector_store.search(
                    vector=vector,
                    source_ids=source_ids,
                    model=self.embedding_provider.model,
                    limit=limit,
                )
                if match.score >= settings.AI_RAG_MIN_SEMANTIC_SCORE
            ]
        except Exception as exc:
            logger.warning(
                "knowledge_semantic_retrieval_failed",
                extra={"error_category": exc.__class__.__name__},
            )
            return []

    def _fuse(self, *, lexical, semantic, source_ids, limit, lexical_strategy):
        if not lexical and not semantic:
            return [], lexical_strategy
        scores: dict[UUID, float] = defaultdict(float)
        lexical_scores = dict(lexical)
        semantic_scores = dict(semantic)
        rank_constant = settings.AI_RAG_RRF_RANK_CONSTANT
        for rank, (chunk_id, _) in enumerate(lexical, start=1):
            scores[chunk_id] += settings.AI_RAG_LEXICAL_WEIGHT / (rank_constant + rank)
        for rank, (chunk_id, _) in enumerate(semantic, start=1):
            scores[chunk_id] += settings.AI_RAG_SEMANTIC_WEIGHT / (rank_constant + rank)
        ordered_ids = sorted(scores, key=lambda value: (-scores[value], str(value)))
        rows = self._rehydrate([str(value) for value in ordered_ids], source_ids)
        row_by_id = {row.chunk_id: row for row in rows}
        selected = []
        per_source: dict[UUID, int] = defaultdict(int)
        for chunk_id in ordered_ids:
            row = row_by_id.get(chunk_id)
            if (
                row is None
                or per_source[row.source_id] >= settings.AI_RAG_MAX_CHUNKS_PER_SOURCE
            ):
                continue
            selected.append(
                replace(
                    row,
                    score=scores[chunk_id],
                    lexical_score=lexical_scores.get(chunk_id),
                    semantic_score=semantic_scores.get(chunk_id),
                )
            )
            per_source[row.source_id] += 1
            if len(selected) == limit:
                break
        strategy = (
            KnowledgeRetrievalStrategy.HYBRID_RRF
            if lexical and semantic
            else KnowledgeRetrievalStrategy.POSTGRES_VECTOR
            if semantic
            else lexical_strategy
        )
        return selected, strategy

    @staticmethod
    def _rehydrate(chunk_ids, source_ids):
        if not chunk_ids:
            return []
        rows = KnowledgeChunk.objects.filter(
            id__in=chunk_ids,
            source_id__in=source_ids,
            injection_risk__lt=settings.AI_RAG_MAX_CHUNK_INJECTION_RISK,
        ).select_related("source")
        row_by_id = {str(row.id): row for row in rows}
        result = []
        for index, chunk_id in enumerate(chunk_ids):
            row = row_by_id.get(str(chunk_id))
            if row is None:
                continue
            result.append(
                KnowledgeChunkResult(
                    chunk_id=row.id,
                    source_id=row.source_id,
                    source_key=row.source.key,
                    source_title=safe_evidence_text(row.source.title, maximum=255),
                    source_version=row.source.version,
                    canonical_url=row.source.canonical_url,
                    content=row.content,
                    chunk_index=row.chunk_index,
                    section_path=row.section_path,
                    score=1.0 / (index + 1),
                )
            )
        return result

    @staticmethod
    def _finish(*, query, result, started):
        duration = monotonic() - started
        outcome = result.no_answer_reason or "retrieved"
        observe_retrieval(
            strategy=result.strategy,
            cache_hit=result.cache_hit,
            outcome=outcome,
            duration_seconds=duration,
        )
        if getattr(settings, "AI_RAG_AUDIT_ENABLED", True):
            try:
                KnowledgeRetrievalAudit.objects.create(
                    query_hash=result.query_hash,
                    acl_fingerprint=result.acl_fingerprint,
                    namespace=query.namespace,
                    language=query.language[:10],
                    strategy=result.strategy.value,
                    chunk_ids=[str(item.chunk_id) for item in result.chunks],
                    source_ids=list(
                        dict.fromkeys(str(item.source_id) for item in result.chunks)
                    ),
                    candidate_count=len(result.chunks),
                    cache_hit=result.cache_hit,
                    no_answer_reason=(
                        result.no_answer_reason.value if result.no_answer_reason else ""
                    ),
                    latency_ms=round(duration * 1_000),
                    correlation_id=query.correlation_id,
                )
            except Exception:
                logger.exception("knowledge_retrieval_audit_failed")
        return result
