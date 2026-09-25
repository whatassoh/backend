"""Asynchronous, idempotent maintenance for conversational memory."""

from celery import shared_task
from django.conf import settings

from apps.ai.application.runtime import get_ai_runtime_policy
from common.async_tasks import WhatassohTask


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="conversations.refresh_memory",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def refresh_conversation_memory(self, conversation_id, correlation_id=None):
    policy = get_ai_runtime_policy()
    if not policy.memory_summary_enabled:
        return {"status": "DISABLED"}
    from apps.ai.application.conversation_memory import IncrementalConversationMemory
    from apps.conversations.infrastructure.models import Conversation

    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        return {"status": "MISSING"}
    try:
        summary = IncrementalConversationMemory().refresh(
            conversation, correlation_id=correlation_id
        )
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=min(300, 15 * (2**self.request.retries))
        ) from exc
    return {
        "status": "CREATED" if summary else "NOOP",
        "summary_id": str(summary.id) if summary else None,
    }


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="conversations.reconcile_memory",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_conversation_memory(self, correlation_id=None):
    if not get_ai_runtime_policy().memory_summary_enabled:
        return {"status": "DISABLED", "queued": 0}
    from django.db.models import Count, F, Max, Q

    from apps.conversations.infrastructure.models import Conversation

    ids = list(
        Conversation.objects.annotate(
            message_count=Count("messages"),
            latest_summary_at=Max("summaries__created_at"),
        )
        .filter(message_count__gte=settings.AI_MEMORY_SUMMARY_MIN_MESSAGES)
        .filter(
            Q(latest_summary_at__isnull=True)
            | Q(last_activity_at__gt=F("latest_summary_at"))
        )
        .order_by("-last_activity_at")
        .values_list("id", flat=True)[: settings.AI_MEMORY_RECONCILIATION_BATCH_SIZE]
    )
    for conversation_id in ids:
        refresh_conversation_memory.delay(str(conversation_id), correlation_id)
    return {"status": "QUEUED", "queued": len(ids)}


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="conversations.expire_memory_facts",
    acks_late=True,
    reject_on_worker_lost=True,
)
def expire_memory_facts(self, correlation_id=None):
    """Soft-delete expired facts in bounded batches for every user."""

    from django.utils import timezone

    from apps.conversations.infrastructure.models import MemoryFact

    ids = list(
        MemoryFact.objects.filter(
            deleted_at__isnull=True, expires_at__lte=timezone.now()
        )
        .order_by("expires_at", "id")
        .values_list("id", flat=True)[: settings.AI_MEMORY_RECONCILIATION_BATCH_SIZE]
    )
    updated = MemoryFact.objects.filter(pk__in=ids, deleted_at__isnull=True).update(
        deleted_at=timezone.now()
    )
    return {"status": "EXPIRED", "expired": updated}
