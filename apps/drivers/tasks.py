from celery import shared_task
from django.db import InterfaceError, OperationalError
from django.utils import timezone

from apps.drivers.domain.enums import DriverOfferNotificationStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverOffer, DriverOfferNotification
from apps.messaging.application.orchestration import send_conversation_interactive
from common.async_tasks import WhatassohTask


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="drivers.start_driver_matching",
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(OperationalError, InterfaceError),
    retry_backoff=True,
    retry_jitter=True,
)
def start_driver_matching_for_order(self, order_id, correlation_id=None):
    from apps.drivers.application.dispatch import DriverMatchingOrchestrator
    from apps.orders.domain.enums import OrderStatus
    from apps.orders.infrastructure.models import Order

    order = Order.objects.filter(pk=order_id).only("id", "status").first()
    if order is None:
        return {"status": "NOOP", "reason": "ORDER_NOT_FOUND"}
    if order.status not in {OrderStatus.READY_FOR_PICKUP, OrderStatus.MATCHING_DRIVERS}:
        return {"status": "NOOP", "reason": f"ORDER_{order.status}"}
    attempt = DriverMatchingOrchestrator().start_matching(order.id, correlation_id=correlation_id)
    return {"status": "STARTED", "attempt_id": str(attempt.id), "attempt_status": attempt.status}


@shared_task(bind=True, base=WhatassohTask, name="drivers.deliver_driver_offer_notification", acks_late=True, reject_on_worker_lost=True)
def deliver_driver_offer_notification(self, notification_id, correlation_id=None):
    notification = DriverOfferNotification.objects.select_related("offer", "conversation").filter(pk=notification_id).first()
    if notification is None or notification.status not in (DriverOfferNotificationStatus.PENDING, DriverOfferNotificationStatus.FAILED_RETRYABLE): return {"status":"NOOP"}
    if notification.offer.status != DriverOfferStatus.PENDING:
        notification.status = DriverOfferNotificationStatus.CANCELLED; notification.save(update_fields=["status", "updated_at"]); return {"status":"NOOP"}
    notification.status = DriverOfferNotificationStatus.SENDING; notification.save(update_fields=["status", "updated_at"])
    snapshot = notification.presentation_snapshot
    try:
        text = (
            f"Nouvelle livraison disponible\n\nCommande : {snapshot.get('order_reference', '')}"
            f"\nRetrait : {snapshot.get('pickup_store_name', '')}"
            f"\nRépondez avant {snapshot.get('expires_at', '')}."
        )
        metadata = {"driver_offer_id": snapshot["offer_id"], "actions": snapshot["actions"]}
        actions = [
            {"id": item["id"], "label": "Accepter" if item["action"].endswith("accept") else "Refuser"}
            for item in snapshot["actions"]
        ]
        if notification.channel == "WHATSAPP":
            from apps.whatsapp.application.outbound import (
                send_whatsapp_interactive_reply_buttons,
                send_whatsapp_template_to_conversation,
            )
            from apps.whatsapp.domain.dtos import WhatsAppReplyButton
            from apps.whatsapp.domain.enums import WhatsAppOutboundDecision, WhatsAppTemplateKey
            from apps.whatsapp.domain.outbound_policy import WhatsAppOutboundPolicy

            if WhatsAppOutboundPolicy().decide(notification.conversation) == WhatsAppOutboundDecision.TEMPLATE_REQUIRED:
                result = send_whatsapp_template_to_conversation(
                    conversation=notification.conversation,
                    template_key=WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                    body_parameters={"recipient_name": notification.recipient.first_name or "partenaire"},
                    metadata=metadata,
                )
            else:
                result = send_whatsapp_interactive_reply_buttons(
                    conversation=notification.conversation,
                    body=text,
                    buttons=[WhatsAppReplyButton(action_id=item["id"], label=item["label"]) for item in actions],
                    metadata=metadata,
                )
        else:
            result = send_conversation_interactive(
                conversation=notification.conversation,
                text=text,
                actions=actions,
                metadata=metadata,
                correlation_id=correlation_id,
            )
    except Exception:
        notification.status = DriverOfferNotificationStatus.FAILED_RETRYABLE; notification.failed_at = timezone.now(); notification.save(update_fields=["status", "failed_at", "updated_at"]); raise
    notification.status = DriverOfferNotificationStatus.SENT; notification.sent_at = timezone.now(); notification.save(update_fields=["status", "sent_at", "updated_at"])
    return {"status":"SENT", "message_id": result.external_message_id}


@shared_task(bind=True, base=WhatassohTask, name="drivers.expire_driver_offer", acks_late=True, reject_on_worker_lost=True)
def expire_driver_offer(self, offer_id, correlation_id=None):
    return _expire_driver_offer(offer_id, correlation_id)


def _expire_driver_offer(offer_id, correlation_id=None):
    from apps.drivers.application.dispatch import DriverMatchingOrchestrator
    from apps.drivers.application.offers import DriverOfferService
    offer = DriverOffer.objects.filter(pk=offer_id).first()
    if offer is None or offer.status != DriverOfferStatus.PENDING or offer.expires_at > timezone.now(): return {"status":"NOOP"}
    try: DriverOfferService().expire_offer(offer)
    except ValueError: return {"status":"NOOP"}
    attempt = getattr(offer.order, "driver_matching_attempt", None)
    if attempt: DriverMatchingOrchestrator().advance_matching(attempt.id, correlation_id=correlation_id)
    return {"status":"EXPIRED"}


@shared_task(bind=True, base=WhatassohTask, name="drivers.sweep_expired_driver_offers", acks_late=True, reject_on_worker_lost=True)
def sweep_expired_driver_offers(self, correlation_id=None):
    from django.conf import settings

    limit = settings.OFFER_EXPIRY_SWEEP_BATCH_SIZE
    ids = list(DriverOffer.objects.filter(status=DriverOfferStatus.PENDING, expires_at__lte=timezone.now()).order_by("expires_at", "id").values_list("id", flat=True)[:limit])
    results = [_expire_driver_offer(offer_id, correlation_id) for offer_id in ids]
    return {"found": len(ids), "expired": sum(item["status"] == "EXPIRED" for item in results)}


@shared_task(bind=True, base=WhatassohTask, name="drivers.consider_location_reminder", acks_late=True, reject_on_worker_lost=True)
def consider_location_reminder(self, driver_id, correlation_id=None):
    from apps.drivers.application.location_reminders import (
        DriverLocationReminderService,
    )

    notification = DriverLocationReminderService().consider(driver_id, correlation_id=correlation_id)
    return {"status": "CREATED" if notification else "NOOP"}


@shared_task(bind=True, base=WhatassohTask, name="drivers.sweep_location_reminders", acks_late=True, reject_on_worker_lost=True)
def sweep_location_reminders(self, correlation_id=None):
    from django.conf import settings
    from django.db.models import Q

    from apps.drivers.application.location_reminders import (
        DriverLocationReminderService,
    )
    from apps.drivers.domain.enums import DriverAvailability
    from apps.drivers.infrastructure.models import DriverProfile

    ids = list(DriverProfile.objects.filter(Q(availability_status=DriverAvailability.AVAILABLE) | Q(assignments__is_active=True)).distinct().order_by("updated_at", "id").values_list("id", flat=True)[:settings.DRIVER_LOCATION_REMINDER_SWEEP_BATCH_SIZE])
    service = DriverLocationReminderService()
    created = sum(service.consider(driver_id, correlation_id=correlation_id) is not None for driver_id in ids)
    return {"scanned": len(ids), "created": created}


@shared_task(bind=True, base=WhatassohTask, name="drivers.reconcile_driver_matching", acks_late=True, reject_on_worker_lost=True)
def reconcile_driver_matching(self, correlation_id=None):
    from apps.drivers.application.reconciliation import (
        DriverMatchingReconciliationService,
    )
    return {"results": list(DriverMatchingReconciliationService().reconcile_running(correlation_id=correlation_id))}
