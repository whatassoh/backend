"""Stable, privacy-preserving rollout assignment for active RAG responses."""

import hashlib
import hmac

from django.conf import settings


def rag_enabled_for(user_id) -> bool:
    percent = max(0, min(int(settings.AI_RAG_ROLLOUT_PERCENT), 100))
    if percent == 0 or user_id is None:
        return False
    if percent == 100:
        return True
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        f"rag-rollout-v1:{user_id}".encode(),
        hashlib.sha256,
    ).digest()
    return int.from_bytes(digest[:8], "big") % 100 < percent
