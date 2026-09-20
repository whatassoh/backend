from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.messaging.application.failures import (
    FailureClassifier,
    NotificationRetryPolicy,
)
from apps.messaging.application.notifications import NotificationService
from common.async_tasks import WhatassohTask


@shared_task(bind=True, base=WhatassohTask, name="messaging.deliver_operational_notification", acks_late=True, reject_on_worker_lost=True)
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
        delay = policy.countdown(attempt.attempt_number, retry_after=decision.retry_after) if can_retry else None
        notification = service.record_failure(
            attempt.id, decision,
            next_retry_at=timezone.now() + timedelta(seconds=delay) if delay is not None else None,
            retry_after_seconds=delay,
        )
        if can_retry:
            raise self.retry(exc=exc, countdown=delay, max_retries=policy.max_attempts - 1)
        return {"status": notification.status}
    service.record_success(attempt.id, result)
    return {"status": "DELIVERED"}


@shared_task(bind=True, base=WhatassohTask, name="messaging.reconcile_operational_notifications", acks_late=True, reject_on_worker_lost=True)
def reconcile_operational_notifications(self, correlation_id=None):
    service = NotificationService()
    ids = list(service.reconciliation_candidates().values_list("id", flat=True))
    results = [service.recover(notification_id) for notification_id in ids]
    return {"scanned": len(ids), "requeued": results.count("REQUEUED"), "cancelled": results.count("CANCELLED")}
