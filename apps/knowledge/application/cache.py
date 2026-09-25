"""Content-free cache entries for authorized retrieval candidates."""

import hashlib
import hmac
import logging
import time
from uuid import UUID

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)
CORPUS_REVISION_PREFIX = "knowledge:corpus-revision"


def query_hash(value: str) -> str:
    normalized = " ".join(value.casefold().split()).encode()
    return hmac.new(
        settings.SECRET_KEY.encode(), normalized, hashlib.sha256
    ).hexdigest()


def corpus_revision(namespace: str) -> str:
    key = f"{CORPUS_REVISION_PREFIX}:{namespace}"
    try:
        revision = cache.get(key)
        if revision is None:
            cache.add(key, 1, timeout=None)
            revision = cache.get(key) or 1
        return str(revision)
    except Exception:
        logger.warning("knowledge_cache_revision_unavailable")
        return "uncached"


def bump_corpus_revision(namespace: str) -> None:
    key = f"{CORPUS_REVISION_PREFIX}:{namespace}"
    try:
        if not cache.add(key, 1, timeout=None):
            cache.incr(key)
    except Exception:
        logger.warning("knowledge_cache_invalidation_failed")


def retrieval_cache_key(
    *, namespace: str, language: str, query_digest: str, acl_digest: str, limit: int
) -> str:
    material = ":".join(
        (
            "rag-v2",
            namespace,
            language,
            query_digest,
            acl_digest,
            str(limit),
            corpus_revision(namespace),
        )
    )
    return f"knowledge:retrieval:{hashlib.sha256(material.encode()).hexdigest()}"


def get_cached_candidate_ids(key: str) -> list[str] | None:
    try:
        value = cache.get(key)
    except Exception:
        logger.warning("knowledge_cache_read_failed")
        return None
    if not isinstance(value, dict) or value.get("schema") != 1:
        return None
    ids = value.get("chunk_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return None
    if len(ids) > min(settings.AI_RAG_CANDIDATE_LIMIT, 500):
        return None
    if len(ids) != len(set(ids)):
        return None
    try:
        for item in ids:
            UUID(item)
    except ValueError:
        return None
    return ids


def set_cached_candidate_ids(key: str, chunk_ids: list[str]) -> None:
    try:
        cache.set(
            key,
            {"schema": 1, "chunk_ids": chunk_ids, "cached_at": int(time.time())},
            timeout=settings.AI_RAG_CACHE_SECONDS,
        )
    except Exception:
        logger.warning("knowledge_cache_write_failed")
