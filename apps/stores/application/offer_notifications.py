"""Persistent delivery boundary for operational StoreOffer notifications.

The module deliberately owns delivery state, not StoreOffer business state.  A
failed outbound message is therefore observable and retryable without looking
like a store rejection.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models import Max
from django.utils import timezone

from apps.conversations.application.services import get_or_create_active_conversation
from apps.messaging.application.orchestration import send_conversation_text
from apps.messaging.domain.enums import MessagingChannel
from apps.messaging.domain.exceptions import (
    MessagingAuthenticationError, MessagingConfigurationError, MessagingError,
    MessagingPermanentError, MessagingRateLimitError, MessagingTemporaryError,
)
from apps.stores.application.offer_actions import StoreRecipientResolver, present_offer
from apps.stores.domain.enums import (
    StoreOfferNotificationAttemptStatus, StoreOfferNotificationStatus,
    StoreOfferStatus,
)
from apps.stores.infrastructure.models import (
    StoreOffer, StoreOfferNotification, StoreOfferNotificationAttempt,
)
from apps.whatsapp.domain.enums import WhatsAppOutboundDecision, WhatsAppTemplateKey
from apps.whatsapp.domain.exceptions import (
    WhatsAppTemplateConfigurationError, WhatsAppTemplateRequired,
    WhatsAppTemplateValidationError,
)
from apps.whatsapp.domain.outbound_policy import WhatsAppOutboundPolicy

logger = logging.getLogger(__name__)


class ProviderErrorCategory(StrEnum):
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_RECIPIENT = "INVALID_RECIPIENT"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    TEMPLATE_REQUIRED = "TEMPLATE_REQUIRED"
    TEMPLATE_REJECTED = "TEMPLATE_REJECTED"
    CONFIGURATION = "CONFIGURATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, kw_only=True)
class ErrorClassification:
    category: ProviderErrorCategory
    retryable: bool
    retry_after: int | None = None


def classify_provider_error(exc: Exception) -> ErrorClassification:
    if isinstance(exc, MessagingRateLimitError):
        return ErrorClassification(category=ProviderErrorCategory.RATE_LIMIT, retryable=True, retry_after=exc.retry_after)
    if isinstance(exc, MessagingTemporaryError):
        category = ProviderErrorCategory.TIMEOUT if "timeout" in str(exc).lower() else ProviderErrorCategory.PROVIDER_UNAVAILABLE
        return ErrorClassification(category=category, retryable=True)
    if isinstance(exc, WhatsAppTemplateRequired):
        return ErrorClassification(category=ProviderErrorCategory.TEMPLATE_REQUIRED, retryable=False)
    if isinstance(exc, (WhatsAppTemplateConfigurationError, MessagingConfigurationError)):
        return ErrorClassification(category=ProviderErrorCategory.CONFIGURATION, retryable=False)
    if isinstance(exc, WhatsAppTemplateValidationError):
        return ErrorClassification(category=ProviderErrorCategory.TEMPLATE_REJECTED, retryable=False)
    if isinstance(exc, MessagingAuthenticationError):
        return ErrorClassification(category=ProviderErrorCategory.AUTH, retryable=False)
    if isinstance(exc, MessagingPermanentError):
        return ErrorClassification(category=ProviderErrorCategory.INVALID_PAYLOAD, retryable=False)
    return ErrorClassification(category=ProviderErrorCategory.UNKNOWN, retryable=isinstance(exc, MessagingError) and exc.retryable)


class StoreConversationResolver:
    """Uses the existing multichannel Conversation; no parallel StoreConversation."""

    def get_or_create_store_conversation(self, *, recipient, channel_identity, store):
        if channel_identity.user_id != recipient.id:
            raise ValueError("Channel identity does not belong to recipient.")
        # A member's direct chat is distinct from a customer's conversation by
        # ChannelIdentity.  Store scope remains on the notification/offer.
        return get_or_create_active_conversation(channel_identity=channel_identity)


@dataclass(frozen=True, kw_only=True)
class OutboundInstruction:
    channel: str
    kind: str  # interactive, template, text
    text: str
    actions: tuple[dict, ...]
    template_key: str | None = None


class StoreOfferNotificationRenderer:
    """Channel-aware rendering only; it never calls a provider."""

    def render(self, notification: StoreOfferNotification) -> OutboundInstruction:
        snapshot = notification.presentation_snapshot
        text = self._text(snapshot)
        actions = tuple(snapshot.get("actions", ()))
        if notification.channel == MessagingChannel.WHATSAPP:
            if WhatsAppOutboundPolicy().decide(notification.conversation) == WhatsAppOutboundDecision.TEMPLATE_REQUIRED:
                return OutboundInstruction(channel=notification.channel, kind="template", text=text, actions=(), template_key=WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED)
            return OutboundInstruction(channel=notification.channel, kind="interactive", text=text, actions=actions)
        return OutboundInstruction(channel=notification.channel, kind="interactive", text=text, actions=actions)

    @staticmethod
    def _text(snapshot: dict) -> str:
        items = "\n".join(f"- {item}" for item in snapshot.get("items", ()))
        return f"Nouvelle commande {snapshot.get('order_reference', '')}\n{items}\nRépondez avant {snapshot.get('expires_at', '')}."


class StoreOfferNotificationService:
    def __init__(self, *, recipient_resolver=None, conversation_resolver=None):
        self.recipient_resolver = recipient_resolver or StoreRecipientResolver()
        self.conversation_resolver = conversation_resolver or StoreConversationResolver()

    def create_for_offer(self, offer: StoreOffer, *, correlation_id: str | None = None):
        if offer.status != StoreOfferStatus.PENDING:
            return ()
        notifications = []
        for identity in self.recipient_resolver.resolve(offer.store):
            conversation, _ = self.conversation_resolver.get_or_create_store_conversation(
                recipient=identity.user, channel_identity=identity, store=offer.store
            )
            key = f"store-offer:{offer.id}:identity:{identity.id}"
            defaults = {
                "recipient": identity.user, "channel_identity": identity,
                "conversation": conversation, "channel": identity.channel,
                "presentation_snapshot": self._snapshot(offer),
            }
            try:
                notification, _ = StoreOfferNotification.objects.get_or_create(idempotency_key=key, defaults=defaults)
            except IntegrityError:  # concurrent logical creation
                notification = StoreOfferNotification.objects.get(idempotency_key=key)
            notifications.append(notification)
            # The task is queued only once the persistent intent is committed;
            # a broker restart can later be repaired from this row.
            if notification.status == StoreOfferNotificationStatus.PENDING:
                transaction.on_commit(self._enqueue(notification.id, correlation_id))
        return tuple(notifications)

    @staticmethod
    def _enqueue(notification_id, correlation_id):
        def callback():
            from apps.stores.tasks import deliver_store_offer_notification
            try:
                deliver_store_offer_notification.delay(str(notification_id), correlation_id)
            except Exception:
                # The committed PENDING row is the recovery source of truth;
                # broker enqueue failure must not undo the business transaction.
                logger.exception("store_offer_notification_enqueue_failed", extra={"notification_id": str(notification_id)})
        return callback

    def cancel_unsent_for_offer(self, offer: StoreOffer) -> int:
        return StoreOfferNotification.objects.filter(
            offer=offer,
            status__in=(StoreOfferNotificationStatus.PENDING, StoreOfferNotificationStatus.SENDING, StoreOfferNotificationStatus.FAILED_RETRYABLE),
        ).update(status=StoreOfferNotificationStatus.CANCELLED, next_retry_at=None)

    @staticmethod
    def recovery_candidates(*, now=None):
        """DB-backed input for the future 7.6D sweep; it does not send."""
        now = now or timezone.now()
        stale_seconds = getattr(settings, "STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS", 900)
        stale_before = now - timedelta(seconds=stale_seconds)
        return StoreOfferNotification.objects.filter(
            Q(
                status__in=(StoreOfferNotificationStatus.PENDING, StoreOfferNotificationStatus.FAILED_RETRYABLE),
            ) & (Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
            | Q(
                status=StoreOfferNotificationStatus.SENDING,
                attempts__status=StoreOfferNotificationAttemptStatus.STARTED,
                attempts__started_at__lt=stale_before,
            )
        ).distinct()

    @staticmethod
    def _snapshot(offer: StoreOffer) -> dict:
        presentation = present_offer(offer)
        # The presentation intentionally excludes phone, precise GPS, address,
        # raw conversation and any provider secrets.
        return {
            "offer_id": presentation.offer_id, "order_reference": presentation.order_reference,
            "items": list(presentation.items), "distance_meters": presentation.distance_meters,
            "expires_at": presentation.expires_at,
            "actions": [{"id": action, "offer_id": presentation.offer_id} for action in presentation.available_actions],
        }

    @transaction.atomic
    def claim_attempt(self, notification_id, *, correlation_id: str | None = None):
        notification = StoreOfferNotification.objects.select_for_update().select_related("offer", "conversation", "channel_identity").get(pk=notification_id)
        if notification.status not in (
            StoreOfferNotificationStatus.PENDING,
            StoreOfferNotificationStatus.FAILED_RETRYABLE,
        ):
            return None
        if notification.offer.status != StoreOfferStatus.PENDING:
            notification.status = StoreOfferNotificationStatus.CANCELLED
            notification.next_retry_at = None
            notification.save(update_fields=["status", "next_retry_at", "updated_at"])
            return None
        number = (notification.attempts.aggregate(maximum=Max("attempt_number"))["maximum"] or 0) + 1
        attempt = StoreOfferNotificationAttempt.objects.create(
            notification=notification, attempt_number=number, provider=notification.channel,
            channel=notification.channel, correlation_id=correlation_id,
        )
        notification.status = StoreOfferNotificationStatus.SENDING
        notification.save(update_fields=["status", "updated_at"])
        return attempt

    @transaction.atomic
    def record_success(self, attempt_id, result):
        attempt = StoreOfferNotificationAttempt.objects.select_for_update().select_related("notification").get(pk=attempt_id)
        if attempt.status != StoreOfferNotificationAttemptStatus.STARTED:
            return attempt.notification
        now = timezone.now()
        attempt.status = StoreOfferNotificationAttemptStatus.SUCCEEDED
        attempt.completed_at = now
        attempt.provider_message_id = result.external_message_id
        attempt.message_event = result.message_event
        attempt.save(update_fields=["status", "completed_at", "provider_message_id", "message_event", "updated_at"])
        notification = attempt.notification
        notification.status, notification.sent_at, notification.next_retry_at = StoreOfferNotificationStatus.SENT, now, None
        notification.save(update_fields=["status", "sent_at", "next_retry_at", "updated_at"])
        return notification

    @transaction.atomic
    def record_failure(self, attempt_id, classification: ErrorClassification, *, delay_seconds: int | None = None):
        attempt = StoreOfferNotificationAttempt.objects.select_for_update().select_related("notification").get(pk=attempt_id)
        if attempt.status != StoreOfferNotificationAttemptStatus.STARTED:
            return attempt.notification
        now = timezone.now()
        attempt.status, attempt.completed_at = StoreOfferNotificationAttemptStatus.FAILED, now
        attempt.error_category, attempt.retryable = classification.category, classification.retryable
        attempt.save(update_fields=["status", "completed_at", "error_category", "retryable", "updated_at"])
        notification = attempt.notification
        notification.last_error_category = classification.category
        notification.failed_at = now
        if classification.retryable and delay_seconds is not None:
            notification.status = StoreOfferNotificationStatus.FAILED_RETRYABLE
            notification.next_retry_at = now + timedelta(seconds=delay_seconds)
        else:
            notification.status = StoreOfferNotificationStatus.FAILED_PERMANENT
            notification.next_retry_at = None
        notification.save(update_fields=["status", "last_error_category", "failed_at", "next_retry_at", "updated_at"])
        return notification


def retry_delay_seconds(attempt_number: int, retry_after: int | None = None) -> int:
    if retry_after is not None:
        return retry_after
    base = getattr(settings, "STORE_OFFER_NOTIFICATION_RETRY_BASE_SECONDS", 30)
    maximum = getattr(settings, "STORE_OFFER_NOTIFICATION_RETRY_MAX_SECONDS", 900)
    return min(base * (2 ** max(attempt_number - 1, 0)), maximum)
