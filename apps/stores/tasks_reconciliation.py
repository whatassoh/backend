from celery import shared_task

from apps.stores.application.offer_notifications import StoreOfferNotificationService
from apps.stores.application.reconciliation import StoreMatchingReconciliationService
from apps.stores.tasks import recover_store_offer_notification
from common.async_tasks import WhatassohTask


@shared_task(bind=True, base=WhatassohTask, name="stores.reconcile_store_matching", acks_late=True, reject_on_worker_lost=True)
def reconcile_store_matching(self, correlation_id=None):
    decisions = StoreMatchingReconciliationService().reconcile_stuck_attempts(correlation_id=correlation_id)
    for notification in StoreOfferNotificationService.recovery_candidates():
        recover_store_offer_notification.delay(str(notification.id), correlation_id)
    return {"attempts": len(decisions)}
