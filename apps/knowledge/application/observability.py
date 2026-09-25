"""Low-cardinality RAG telemetry; document and query text are never exported."""

import logging

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

RETRIEVALS = Counter(
    "whatassoh_rag_retrievals_total",
    "RAG retrieval attempts.",
    ("strategy", "cache_hit", "outcome"),
)
RETRIEVAL_DURATION = Histogram(
    "whatassoh_rag_retrieval_duration_seconds",
    "RAG retrieval latency.",
    ("strategy",),
)
INGESTIONS = Counter(
    "whatassoh_rag_ingestions_total",
    "Knowledge source ingestion decisions.",
    ("status", "source_type"),
)
EMBEDDINGS = Counter(
    "whatassoh_rag_embeddings_total",
    "Knowledge embedding operations.",
    ("status", "provider"),
)


def observe_retrieval(*, strategy, cache_hit, outcome, duration_seconds) -> None:
    try:
        label = str(getattr(strategy, "value", strategy)).lower()[:40]
        RETRIEVALS.labels(
            strategy=label,
            cache_hit=str(bool(cache_hit)).lower(),
            outcome=str(outcome).lower()[:40],
        ).inc()
        RETRIEVAL_DURATION.labels(strategy=label).observe(max(0, duration_seconds))
    except Exception:  # pragma: no cover - telemetry is best effort
        logger.exception("knowledge_retrieval_metric_failed")


def observe_ingestion(*, status: str, source_type: str) -> None:
    try:
        INGESTIONS.labels(
            status=status.lower()[:24], source_type=source_type.lower()[:24]
        ).inc()
    except Exception:  # pragma: no cover
        logger.exception("knowledge_ingestion_metric_failed")


def observe_embedding(*, status: str, provider: str) -> None:
    try:
        EMBEDDINGS.labels(
            status=status.lower()[:24], provider=provider.lower()[:24]
        ).inc()
    except Exception:  # pragma: no cover
        logger.exception("knowledge_embedding_metric_failed")
