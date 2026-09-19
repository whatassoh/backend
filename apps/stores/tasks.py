"""Celery delivery for persistent StoreOffer notifications."""
from celery import shared_task
from django.conf import settings

from apps.messaging.application.orchestration import send_conversation_text
from apps.stores.application.offer_notifications import (
    StoreOfferNotificationRenderer, StoreOfferNotificationService,
    classify_provider_error, retry_delay_seconds,
)
from apps.stores.domain.enums import StoreOfferNotificationStatus
from apps.stores.infrastructure.models import StoreOfferNotification


def _dispatch(notification, instruction, correlation_id):
    metadata = {
        "store_offer_id": str(notification.offer_id), "actions": list(instruction.actions),
        "delivery_kind": instruction.kind, "template_key": str(instruction.template_key or ""),
    }
    if notification.channel == "WHATSAPP":
        from apps.whatsapp.application.outbound import (
            send_whatsapp_interactive_reply_buttons, send_whatsapp_template_to_conversation,
        )
        from apps.whatsapp.domain.dtos import WhatsAppReplyButton
        if instruction.kind == "template":
            return send_whatsapp_template_to_conversation(
                conversation=notification.conversation, template_key=instruction.template_key,
                body_parameters={"1": notification.presentation_snapshot.get("order_reference", "")}, metadata=metadata,
            )
        buttons = [WhatsAppReplyButton(action_id=item["id"], label="Accepter" if item["id"].endswith("accept") else "Refuser") for item in instruction.actions]
        return send_whatsapp_interactive_reply_buttons(
            conversation=notification.conversation, body=instruction.text, buttons=buttons, metadata=metadata,
        )
    return send_conversation_text(
        conversation=notification.conversation, text=instruction.text, metadata=metadata,
        correlation_id=correlation_id,
    )


@shared_task(bind=True, name="stores.deliver_store_offer_notification")
def deliver_store_offer_notification(self, notification_id, correlation_id=None):
    service = StoreOfferNotificationService()
    attempt = service.claim_attempt(notification_id, correlation_id=correlation_id)
    if attempt is None:
        return {"status": "NOOP"}
    notification = StoreOfferNotification.objects.select_related("conversation").get(pk=attempt.notification_id)
    try:
        instruction = StoreOfferNotificationRenderer().render(notification)
        result = _dispatch(notification, instruction, correlation_id)
    except Exception as exc:
        classification = classify_provider_error(exc)
        max_attempts = getattr(settings, "STORE_OFFER_NOTIFICATION_MAX_ATTEMPTS", 3)
        retryable = classification.retryable and attempt.attempt_number < max_attempts
        if retryable:
            delay = retry_delay_seconds(attempt.attempt_number, classification.retry_after)
            service.record_failure(attempt.id, classification, delay_seconds=delay)
            deliver_store_offer_notification.apply_async(args=[str(notification_id), correlation_id], countdown=delay)
            return {"status": "RETRY", "delay_seconds": delay}
        service.record_failure(attempt.id, classification, delay_seconds=None)
        return {"status": "FAILED", "category": classification.category}
    service.record_success(attempt.id, result)
    return {"status": "SENT"}


@shared_task(name="stores.recover_store_offer_notification")
def recover_store_offer_notification(notification_id, correlation_id=None):
    """Safe re-enqueue entry point for the future reconciliation sweep."""
    notification = StoreOfferNotification.objects.filter(pk=notification_id).first()
    if notification is None:
        return {"status": "NOOP"}
    if notification.status == StoreOfferNotificationStatus.SENDING:
        # Provider may have accepted the request just before a worker crash.
        # Do not blindly resend and create an untraceable duplicate.
        return {"status": "AMBIGUOUS_DELIVERY"}
    if notification.status not in (StoreOfferNotificationStatus.PENDING, StoreOfferNotificationStatus.FAILED_RETRYABLE):
        return {"status": "NOOP"}
    deliver_store_offer_notification.delay(str(notification_id), correlation_id)
    return {"status": "ENQUEUED"}
