from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.messaging.application.failures import (
    FailureClassifier,
    NotificationRetryPolicy,
)
from apps.messaging.application.notifications import NotificationService
from common.async_tasks import WhatassohTask


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="messaging.drain_conversation_inbox",
    acks_late=True,
    reject_on_worker_lost=True,
)
def drain_conversation_inbox(self, inbox_item_id, correlation_id=None):
    """Process one ordered turn; durable state decides whether this delivery owns it."""

    from apps.messaging.application.reliable_pipeline import (
        claim_inbox,
        process_claimed_inbox,
        record_inbox_failure,
    )

    token = claim_inbox(inbox_item_id)
    if token is None:
        return {"status": "NOOP"}
    try:
        status = process_claimed_inbox(inbox_item_id, token)
    except Exception as exc:
        status = record_inbox_failure(inbox_item_id, token, exc)
        if status == "FAILED_RETRYABLE":
            raise self.retry(
                exc=exc,
                countdown=_conversation_retry_countdown(self.request.retries),
                max_retries=settings.CONVERSATION_INBOX_MAX_ATTEMPTS - 1,
            ) from exc
        return {"status": status}
    return {"status": status}


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="messaging.deliver_conversation_outbox",
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_conversation_outbox(self, outbox_item_id, correlation_id=None):
    """Deliver one response intent without ever replaying its business turn."""

    from apps.messaging.application.reliable_pipeline import (
        claim_outbox,
        conversation_outbox_retry_delay,
        deliver_claimed_outbox,
        record_outbox_failure,
    )
    from apps.messaging.infrastructure.models import ConversationOutboxItem

    token = claim_outbox(outbox_item_id)
    if token is None:
        return {"status": "NOOP"}
    try:
        status = deliver_claimed_outbox(outbox_item_id, token)
    except Exception as exc:
        status = record_outbox_failure(outbox_item_id, token, exc)
        if status == "FAILED_RETRYABLE":
            attempts = (
                ConversationOutboxItem.objects.only("attempts")
                .get(pk=outbox_item_id)
                .attempts
            )
            raise self.retry(
                exc=exc,
                countdown=conversation_outbox_retry_delay(
                    FailureClassifier.classify(exc), attempts
                ),
                max_retries=settings.CONVERSATION_OUTBOX_MAX_ATTEMPTS - 1,
            ) from exc
        return {"status": status}
    return {"status": status}


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="messaging.reconcile_conversation_pipeline",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_conversation_pipeline(self, correlation_id=None):
    from apps.messaging.application.reliable_pipeline import (
        inbox_reconciliation_candidates,
        outbox_reconciliation_candidates,
    )

    inbox_ids = [str(item.id) for item in inbox_reconciliation_candidates()]
    outbox_ids = [str(item.id) for item in outbox_reconciliation_candidates()]
    for item_id in inbox_ids:
        drain_conversation_inbox.delay(item_id, correlation_id)
    for item_id in outbox_ids:
        deliver_conversation_outbox.delay(item_id, correlation_id)
    return {"inbox_requeued": len(inbox_ids), "outbox_requeued": len(outbox_ids)}


def _conversation_retry_countdown(retries):
    return min(
        settings.CONVERSATION_RETRY_MAX_SECONDS,
        settings.CONVERSATION_RETRY_BASE_SECONDS * (2**retries),
    )


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="messaging.deliver_operational_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_operational_notification(self, notification_id, correlation_id=None):
    service = NotificationService()
    attempt = service.deliver(notification_id, correlation_id=correlation_id)
    if attempt is None:
        return {"status": "NOOP"}
    notification = attempt.notification
    try:
        result = service.render_and_send(notification, correlation_id=correlation_id)
    except Exception as exc:
        decision = FailureClassifier.classify(exc)
        policy = NotificationRetryPolicy()
        can_retry = decision.retryable and attempt.attempt_number < policy.max_attempts
        delay = (
            policy.countdown(attempt.attempt_number, retry_after=decision.retry_after)
            if can_retry
            else None
        )
        notification = service.record_failure(
            attempt.id,
            decision,
            next_retry_at=timezone.now() + timedelta(seconds=delay)
            if delay is not None
            else None,
            retry_after_seconds=delay,
        )
        if can_retry:
            raise self.retry(
                exc=exc, countdown=delay, max_retries=policy.max_attempts - 1
            ) from exc
        return {"status": notification.status}
    service.record_success(attempt.id, result)
    return {"status": "DELIVERED"}


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="messaging.reconcile_operational_notifications",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_operational_notifications(self, correlation_id=None):
    service = NotificationService()
    ids = list(service.reconciliation_candidates().values_list("id", flat=True))
    results = [service.recover(notification_id) for notification_id in ids]
    return {
        "scanned": len(ids),
        "requeued": results.count("REQUEUED"),
        "cancelled": results.count("CANCELLED"),
    }
