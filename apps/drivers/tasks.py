from celery import shared_task
from django.utils import timezone
from apps.drivers.domain.enums import DriverOfferNotificationStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverOffer, DriverOfferNotification
from apps.messaging.application.orchestration import send_conversation_text


@shared_task(name="drivers.deliver_driver_offer_notification")
def deliver_driver_offer_notification(notification_id, correlation_id=None):
    notification = DriverOfferNotification.objects.select_related("offer", "conversation").filter(pk=notification_id).first()
    if notification is None or notification.status not in (DriverOfferNotificationStatus.PENDING, DriverOfferNotificationStatus.FAILED_RETRYABLE): return {"status":"NOOP"}
    if notification.offer.status != DriverOfferStatus.PENDING:
        notification.status = DriverOfferNotificationStatus.CANCELLED; notification.save(update_fields=["status", "updated_at"]); return {"status":"NOOP"}
    notification.status = DriverOfferNotificationStatus.SENDING; notification.save(update_fields=["status", "updated_at"])
    snapshot = notification.presentation_snapshot
    try:
        # send_conversation_text enforces the WhatsApp service-window policy.
        result = send_conversation_text(conversation=notification.conversation, text=f"Nouvelle livraison disponible\nRetrait : {snapshot.get('pickup_store_name', '')}\nRépondez avant {snapshot.get('expires_at', '')}.", metadata={"driver_offer_id": snapshot["offer_id"], "actions": snapshot["actions"]}, correlation_id=correlation_id)
    except Exception:
        notification.status = DriverOfferNotificationStatus.FAILED_RETRYABLE; notification.failed_at = timezone.now(); notification.save(update_fields=["status", "failed_at", "updated_at"]); raise
    notification.status = DriverOfferNotificationStatus.SENT; notification.sent_at = timezone.now(); notification.save(update_fields=["status", "sent_at", "updated_at"])
    return {"status":"SENT", "message_id": result.external_message_id}


@shared_task(name="drivers.expire_driver_offer")
def expire_driver_offer(offer_id, correlation_id=None):
    from apps.drivers.application.dispatch import DriverMatchingOrchestrator
    from apps.drivers.application.offers import DriverOfferService
    offer = DriverOffer.objects.filter(pk=offer_id).first()
    if offer is None or offer.status != DriverOfferStatus.PENDING or offer.expires_at > timezone.now(): return {"status":"NOOP"}
    try: DriverOfferService().expire_offer(offer)
    except ValueError: return {"status":"NOOP"}
    attempt = getattr(offer.order, "driver_matching_attempt", None)
    if attempt: DriverMatchingOrchestrator().advance_matching(attempt.id, correlation_id=correlation_id)
    return {"status":"EXPIRED"}


@shared_task(name="drivers.reconcile_driver_matching")
def reconcile_driver_matching(correlation_id=None):
    from apps.drivers.application.reconciliation import DriverMatchingReconciliationService
    return {"results": list(DriverMatchingReconciliationService().reconcile_running(correlation_id=correlation_id))}
