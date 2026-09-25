"""Fail-open cache for reconstructible AI and messaging artefacts."""

import hashlib
import json
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class EphemeralCache:
    """Never authorizes actions and never replaces PostgreSQL state."""

    namespace = "whatassoh:v1"

    @classmethod
    def fingerprint(cls, *parts) -> str:
        encoded = json.dumps(parts, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def get(cls, category, key):
        try:
            return cache.get(f"{cls.namespace}:{category}:{key}")
        except Exception:
            logger.warning("ephemeral_cache_read_failed", extra={"category": category})
            return None

    @classmethod
    def set(cls, category, key, value, *, timeout):
        try:
            cache.set(f"{cls.namespace}:{category}:{key}", value, timeout=timeout)
        except Exception:
            logger.warning("ephemeral_cache_write_failed", extra={"category": category})

    @classmethod
    def delete(cls, category, key):
        try:
            cache.delete(f"{cls.namespace}:{category}:{key}")
        except Exception:
            logger.warning(
                "ephemeral_cache_delete_failed", extra={"category": category}
            )
