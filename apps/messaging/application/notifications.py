"""Generic transactional notifications layered above Messaging Core."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.conversations.application.services import (
    get_or_create_active_conversation,
    record_outbound_message,
)
from apps.messaging.application.failures import FailureDecision
from apps.messaging.application.orchestration import send, send_conversation_text
from apps.messaging.domain.dtos import NormalizedOutboundMessage
from apps.messaging.domain.enums import (
    MessageType,
    MessagingChannel,
    NotificationAttemptOutcome,
    NotificationKind,
    NotificationStatus,
)
from apps.messaging.domain.exceptions import MessagingPermanentError
from apps.messaging.infrastructure.models import (
    ChannelIdentity,
    OperationalNotification,
    OperationalNotificationAttempt,
)
from common.async_tasks import enqueue_after_commit

logger = logging.getLogger(__name__)


class NotificationRecipientResolver:
    """Choose one active identity deterministically; never trust caller contact data."""

    def resolve(self, *, recipient, conversation=None, preferred_channel=None):
        if conversation and conversation.user_id == recipient.id and conversation.status == "ACTIVE":
            return conversation.channel_identity, conversation
        identities = ChannelIdentity.objects.filter(user=recipient, is_active=True)
        if preferred_channel:
            identities = identities.filter(channel=preferred_channel)
        identity = identities.order_by("channel", "id").first()
        if identity is None:
            return None, None
        conversation, _ = get_or_create_active_conversation(channel_identity=identity)
        return identity, conversation


class NotificationService:
    """Owns intent/delivery state only; it never changes business entity state."""

    def __init__(self, *, recipient_resolver=None):
        self.recipient_resolver = recipient_resolver or NotificationRecipientResolver()

    def create_intent(
        self, *, kind, recipient, business_entity_type, business_entity_id,
        idempotency_key, payload=None, conversation=None, preferred_channel=None,
        correlation_id=None,
    ):
        identity, resolved_conversation = self.recipient_resolver.resolve(
            recipient=recipient, conversation=conversation, preferred_channel=preferred_channel
        )
        if identity is None:
            logger.info("notification_cancelled_no_recipient", extra={"kind": kind, "recipient_id": str(recipient.id), "correlation_id": correlation_id})
            return None
        defaults = {
            "recipient": recipient, "channel_identity": identity,
            "conversation": resolved_conversation, "channel": identity.channel,
            "business_entity_type": business_entity_type,
            "business_entity_id": str(business_entity_id), "payload": payload or {},
            "correlation_id": correlation_id,
        }
        try:
            notification, created = OperationalNotification.objects.get_or_create(
                kind=kind, idempotency_key=idempotency_key, defaults=defaults
            )
        except IntegrityError:
            notification = OperationalNotification.objects.get(idempotency_key=idempotency_key)
            created = False
        if created:
            self.enqueue(notification)
            logger.info("notification_created", extra={"notification_id": str(notification.id), "kind": kind, "channel": identity.channel, "correlation_id": correlation_id})
        return notification

    def enqueue(self, notification):
        from apps.messaging.tasks import deliver_operational_notification

        enqueue_after_commit(
            lambda: deliver_operational_notification.delay(str(notification.id), notification.correlation_id),
            task_name="messaging.deliver_operational_notification",
            entity_id=str(notification.id), correlation_id=notification.correlation_id,
        )

    @transaction.atomic
    def cancel(self, notification_id):
        notification = OperationalNotification.objects.select_for_update().get(pk=notification_id)
        if notification.status in (NotificationStatus.PENDING, NotificationStatus.FAILED_RETRYABLE):
            notification.status = NotificationStatus.CANCELLED
            notification.save(update_fields=["status", "updated_at"])
        return notification

    @transaction.atomic
    def deliver(self, notification_id, *, correlation_id=None):
        notification = OperationalNotification.objects.select_for_update(of=("self",)).select_related(
            "conversation", "channel_identity"
        ).get(pk=notification_id)
        if notification.status not in (NotificationStatus.PENDING, NotificationStatus.FAILED_RETRYABLE):
            return None
        if not self._is_current(notification):
            notification.status = NotificationStatus.CANCELLED
            notification.save(update_fields=["status", "updated_at"])
            return None
        number = (notification.attempts.aggregate(maximum=Max("attempt_number"))["maximum"] or 0) + 1
        attempt = OperationalNotificationAttempt.objects.create(
            notification=notification, attempt_number=number, channel=notification.channel,
            provider=notification.channel,  # opaque provider family, not a credential
        )
        notification.status = NotificationStatus.PROCESSING
        notification.save(update_fields=["status", "updated_at"])
        return attempt

    @transaction.atomic
    def record_success(self, attempt_id, result):
        attempt = OperationalNotificationAttempt.objects.select_for_update().select_related("notification").get(pk=attempt_id)
        if attempt.outcome != NotificationAttemptOutcome.STARTED:
            return attempt.notification
        now = timezone.now()
        attempt.outcome = NotificationAttemptOutcome.DELIVERED
        attempt.completed_at = now
        attempt.provider_message_id = result.external_message_id
        attempt.message_event = result.message_event
        attempt.save(update_fields=["outcome", "completed_at", "provider_message_id", "message_event", "updated_at"])
        notification = attempt.notification
        notification.status, notification.delivered_at = NotificationStatus.DELIVERED, now
        notification.next_retry_at = None
        notification.save(update_fields=["status", "delivered_at", "next_retry_at", "updated_at"])
        return notification

    @transaction.atomic
    def record_failure(self, attempt_id, decision: FailureDecision, *, next_retry_at=None, retry_after_seconds=None):
        attempt = OperationalNotificationAttempt.objects.select_for_update().select_related("notification").get(pk=attempt_id)
        if attempt.outcome != NotificationAttemptOutcome.STARTED:
            return attempt.notification
        now = timezone.now()
        attempt.outcome, attempt.completed_at = NotificationAttemptOutcome.FAILED, now
        attempt.error_category = decision.category
        attempt.retry_after_seconds = retry_after_seconds
        attempt.save(update_fields=["outcome", "completed_at", "error_category", "retry_after_seconds", "updated_at"])
        notification = attempt.notification
        notification.status = NotificationStatus.FAILED_RETRYABLE if decision.retryable else NotificationStatus.FAILED_PERMANENT
        notification.failed_at = now
        notification.last_error_category = decision.category
        notification.next_retry_at = next_retry_at
        notification.save(update_fields=["status", "failed_at", "last_error_category", "next_retry_at", "updated_at"])
        return notification

    @transaction.atomic
    def replay(self, notification_id):
        """Re-open the same logical intent after a configuration/provider fix."""
        notification = OperationalNotification.objects.select_for_update().get(pk=notification_id)
        if notification.status not in (NotificationStatus.FAILED_PERMANENT, NotificationStatus.FAILED_RETRYABLE):
            return None
        if not self._is_current(notification):
            notification.status, notification.next_retry_at = NotificationStatus.CANCELLED, None
            notification.save(update_fields=["status", "next_retry_at", "updated_at"])
            return None
        notification.status, notification.next_retry_at = NotificationStatus.PENDING, None
        notification.save(update_fields=["status", "next_retry_at", "updated_at"])
        self.enqueue(notification)
        return notification

    def reconciliation_candidates(self, *, limit=None):
        now = timezone.now()
        lease = now - timedelta(seconds=settings.NOTIFICATION_PROCESSING_LEASE_SECONDS)
        return OperationalNotification.objects.filter(
            Q(status__in=(NotificationStatus.PENDING, NotificationStatus.FAILED_RETRYABLE))
            | Q(status=NotificationStatus.PROCESSING, updated_at__lte=lease)
        ).order_by("updated_at", "id")[: limit or settings.WORKFLOW_RECONCILIATION_BATCH_SIZE]

    @transaction.atomic
    def recover(self, notification_id):
        notification = OperationalNotification.objects.select_for_update().get(pk=notification_id)
        if not self._is_current(notification):
            notification.status, notification.next_retry_at = NotificationStatus.CANCELLED, None
            notification.save(update_fields=["status", "next_retry_at", "updated_at"])
            return "CANCELLED"
        if notification.status == NotificationStatus.PROCESSING:
            notification.status = NotificationStatus.FAILED_RETRYABLE
            # Lease recovery publishes immediately below.  Keep a short DB
            # marker so a concurrent reconciler does not publish a duplicate.
            notification.next_retry_at = timezone.now() + timedelta(
                seconds=settings.NOTIFICATION_PROCESSING_LEASE_SECONDS
            )
            notification.save(update_fields=["status", "next_retry_at", "updated_at"])
        elif notification.status == NotificationStatus.FAILED_RETRYABLE and notification.next_retry_at and notification.next_retry_at > timezone.now():
            return "NOOP"
        if notification.status in (NotificationStatus.PENDING, NotificationStatus.FAILED_RETRYABLE):
            self.enqueue(notification)
            return "REQUEUED"
        return "NOOP"

    def render_and_send(self, notification, *, correlation_id=None):
        """Presentation is rebuilt from DB-backed payload, without provider data in intent."""
        text, actions = self._render(notification)
        metadata = {"notification_id": str(notification.id), "kind": notification.kind, "actions": actions}
        if actions and notification.channel == MessagingChannel.TELEGRAM:
            message = NormalizedOutboundMessage(
                channel=notification.channel,
                recipient_external_id=notification.conversation.external_chat_id or notification.channel_identity.external_user_id,
                message_type=MessageType.INTERACTIVE, text=text, metadata=metadata,
            )
            result = send(message)
            record_outbound_message(conversation=notification.conversation, message=message, message_event=result.message_event)
            return result
        if notification.channel == MessagingChannel.WHATSAPP:
            from apps.whatsapp.application.outbound import (
                send_whatsapp_text_with_template_fallback,
            )
            from apps.whatsapp.domain.enums import WhatsAppTemplateKey

            template_key = {
                NotificationKind.DRIVER_OFFER: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.STORE_OFFER: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.DRIVER_LOCATION_REQUIRED: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.DRIVER_LOCATION_REFRESH_REQUIRED: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.ORDER_PICKED_UP: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.ORDER_IN_DELIVERY: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.ORDER_DELIVERED: WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                NotificationKind.ORDER_STORE_ACCEPTED: WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                NotificationKind.ORDER_NO_STORE_AVAILABLE: WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                NotificationKind.ORDER_CANCELLED: WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                NotificationKind.STORE_PREPARATION_STARTED: WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                NotificationKind.ORDER_READY_FOR_PICKUP: WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
            }.get(notification.kind)
            if template_key is None:
                raise MessagingPermanentError("No WhatsApp template is mapped to notification kind.")
            return send_whatsapp_text_with_template_fallback(
                conversation=notification.conversation, free_form_text=text,
                template_key=template_key,
                template_body_parameters={"1": notification.payload.get("reference", "notification")},
                metadata=metadata,
            )
        return send_conversation_text(
            conversation=notification.conversation, text=text, metadata=metadata,
            correlation_id=correlation_id or notification.correlation_id,
        )

    @staticmethod
    def _is_current(notification):
        if notification.kind == NotificationKind.DRIVER_OFFER:
            from apps.drivers.domain.enums import DriverOfferStatus
            from apps.drivers.infrastructure.models import DriverOffer
            return DriverOffer.objects.filter(pk=notification.business_entity_id, status=DriverOfferStatus.PENDING).exists()
        if notification.kind == NotificationKind.STORE_OFFER:
            from apps.stores.domain.enums import StoreOfferStatus
            from apps.stores.infrastructure.models import StoreOffer
            return StoreOffer.objects.filter(pk=notification.business_entity_id, status=StoreOfferStatus.PENDING).exists()
        if notification.business_entity_type == "drivers.DriverLocationReminder":
            from apps.drivers.infrastructure.models import DriverLocationReminder
            return DriverLocationReminder.objects.filter(pk=notification.business_entity_id, resolved_at__isnull=True, exhausted_at__isnull=True).exists()
        return True

    @staticmethod
    def _render(notification):
        payload = notification.payload
        if notification.kind == NotificationKind.DRIVER_OFFER:
            return "Nouvelle livraison disponible.", ["driver_offer.accept", "driver_offer.reject"]
        if notification.kind == NotificationKind.STORE_OFFER:
            return "Nouvelle commande à traiter.", ["store_offer.accept", "store_offer.reject"]
        if notification.kind == NotificationKind.DRIVER_LOCATION_REQUIRED:
            return "Partagez votre position pour recevoir des livraisons.", []
        if notification.kind == NotificationKind.DRIVER_LOCATION_REFRESH_REQUIRED:
            return "Partagez votre position actuelle pour maintenir le suivi.", []
        if notification.kind == NotificationKind.ORDER_PICKED_UP:
            return payload.get("text", "Commande récupérée par le livreur."), []
        if notification.kind == NotificationKind.ORDER_IN_DELIVERY:
            return payload.get("text", "Votre commande est en route."), []
        if notification.kind == NotificationKind.ORDER_DELIVERED:
            return payload.get("text", "Votre commande a été livrée."), []
        return payload.get("text", "Mise à jour de votre commande."), payload.get("actions", [])
