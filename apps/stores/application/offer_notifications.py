"""Persistent delivery boundary for operational StoreOffer notifications.

The module deliberately owns delivery state, not StoreOffer business state.  A
failed outbound message is therefore observable and retryable without looking
like a store rejection.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.conversations.application.services import get_or_create_active_conversation
from apps.messaging.application.failures import FailureDecision
from apps.messaging.domain.enums import MessagingChannel
from apps.messaging.domain.exceptions import MessagingConfigurationError
from apps.stores.application.offer_actions import (
    StoreRecipientResolver,
    encode_store_offer_action,
    present_offer,
)
from apps.stores.domain.enums import (
    StoreOfferNotificationAttemptStatus,
    StoreOfferNotificationStatus,
    StoreOfferStatus,
)
from apps.stores.infrastructure.models import (
    StoreOffer,
    StoreOfferNotification,
    StoreOfferNotificationAttempt,
)
from apps.whatsapp.domain.enums import WhatsAppOutboundDecision, WhatsAppTemplateKey
from apps.whatsapp.domain.outbound_policy import WhatsAppOutboundPolicy
from common.async_tasks import enqueue_after_commit

logger = logging.getLogger(__name__)


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
            if notification.conversation is None:
                raise MessagingConfigurationError(
                    "Store offer notification has no conversation."
                )
            if (
                WhatsAppOutboundPolicy().decide(notification.conversation)
                == WhatsAppOutboundDecision.TEMPLATE_REQUIRED
            ):
                return OutboundInstruction(
                    channel=notification.channel,
                    kind="template",
                    text=text,
                    actions=(),
                    template_key=WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                )
            return OutboundInstruction(
                channel=notification.channel,
                kind="interactive",
                text=text,
                actions=actions,
            )
        return OutboundInstruction(
            channel=notification.channel, kind="interactive", text=text, actions=actions
        )

    @staticmethod
    def _text(snapshot: dict) -> str:
        items = "\n".join(f"- {item}" for item in snapshot.get("items", ()))
        distance = snapshot.get("distance_meters")
        distance_line = ""
        if distance is not None:
            kilometers = f"{distance / 1000:.1f}".replace(".", ",")
            distance_line = f"\nDistance : {kilometers} km"
        return (
            f"Nouvelle commande {snapshot.get('order_reference', '')}\n\n"
            f"{items}{distance_line}\n\nSouhaitez-vous accepter ?"
        )


class StoreOfferNotificationService:
    def __init__(self, *, recipient_resolver=None, conversation_resolver=None):
        self.recipient_resolver = recipient_resolver or StoreRecipientResolver()
        self.conversation_resolver = (
            conversation_resolver or StoreConversationResolver()
        )

    def create_for_offer(self, offer: StoreOffer, *, correlation_id: str | None = None):
        if offer.status != StoreOfferStatus.PENDING:
            return ()
        notifications = []
        for identity in self.recipient_resolver.resolve(offer.store):
            conversation, _ = (
                self.conversation_resolver.get_or_create_store_conversation(
                    recipient=identity.user,
                    channel_identity=identity,
                    store=offer.store,
                )
            )
            key = f"store-offer:{offer.id}:identity:{identity.id}"
            defaults = {
                "offer": offer,
                "recipient": identity.user,
                "channel_identity": identity,
                "conversation": conversation,
                "channel": identity.channel,
                "presentation_snapshot": self._snapshot(offer),
            }
            try:
                notification, created = StoreOfferNotification.objects.get_or_create(
                    idempotency_key=key, defaults=defaults
                )
            except IntegrityError:  # concurrent logical creation
                notification = StoreOfferNotification.objects.get(idempotency_key=key)
                created = False
            notifications.append(notification)
            if created:
                logger.info(
                    "store_offer_notification_created",
                    extra={
                        "order_id": str(offer.order_id),
                        "offer_id": str(offer.id),
                        "store_id": str(offer.store_id),
                        "rank": offer.rank,
                        "notification_id": str(notification.id),
                        "correlation_id": correlation_id,
                    },
                )
            # The task is queued only once the persistent intent is committed;
            # a broker restart can later be repaired from this row.
            if notification.status == StoreOfferNotificationStatus.PENDING:
                self._enqueue(notification.id, correlation_id)
        return tuple(notifications)

    @staticmethod
    def _enqueue(notification_id, correlation_id):
        def enqueue():
            from apps.stores.tasks import deliver_store_offer_notification

            deliver_store_offer_notification.delay(str(notification_id), correlation_id)

        enqueue_after_commit(
            enqueue,
            task_name="stores.deliver_store_offer_notification",
            entity_id=str(notification_id),
            correlation_id=correlation_id,
        )

    def cancel_unsent_for_offer(self, offer: StoreOffer) -> int:
        return StoreOfferNotification.objects.filter(
            offer=offer,
            status__in=(
                StoreOfferNotificationStatus.PENDING,
                StoreOfferNotificationStatus.SENDING,
                StoreOfferNotificationStatus.FAILED_RETRYABLE,
            ),
        ).update(status=StoreOfferNotificationStatus.CANCELLED, next_retry_at=None)

    @staticmethod
    def recovery_candidates(*, now=None):
        """DB-backed input for the future 7.6D sweep; it does not send."""
        now = now or timezone.now()
        stale_seconds = getattr(
            settings, "STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS", 900
        )
        stale_before = now - timedelta(seconds=stale_seconds)
        return (
            StoreOfferNotification.objects.filter(
                Q(
                    status__in=(
                        StoreOfferNotificationStatus.PENDING,
                        StoreOfferNotificationStatus.FAILED_RETRYABLE,
                    ),
                )
                & (Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
                | Q(
                    status=StoreOfferNotificationStatus.SENDING,
                    attempts__status=StoreOfferNotificationAttemptStatus.STARTED,
                    attempts__started_at__lt=stale_before,
                )
            )
            .filter(
                offer__status=StoreOfferStatus.PENDING,
                offer__expires_at__gt=now,
            )
            .exclude(
                offer__order__store_offers__status=StoreOfferStatus.ACCEPTED,
            )
            .distinct()
        )

    @staticmethod
    def _snapshot(offer: StoreOffer) -> dict:
        presentation = present_offer(offer)
        # The presentation intentionally excludes phone, precise GPS, address,
        # raw conversation and any provider secrets.
        return {
            "offer_id": presentation.offer_id,
            "order_reference": presentation.order_reference,
            "items": list(presentation.items),
            "distance_meters": presentation.distance_meters,
            "expires_at": presentation.expires_at,
            "actions": [
                {
                    "action": action,
                    "id": encode_store_offer_action(action, presentation.offer_id),
                    "offer_id": presentation.offer_id,
                }
                for action in presentation.available_actions
            ],
        }

    @transaction.atomic
    def claim_attempt(self, notification_id, *, correlation_id: str | None = None):
        # Lock the delivery intent only.  Joining nullable ``conversation``
        # here produces an OUTER JOIN and PostgreSQL rejects FOR UPDATE on its
        # nullable side.  Business relations are loaded in separate queries
        # after the notification row is safely locked.
        notification = StoreOfferNotification.objects.select_for_update().get(
            pk=notification_id
        )
        if notification.status not in (
            StoreOfferNotificationStatus.PENDING,
            StoreOfferNotificationStatus.FAILED_RETRYABLE,
        ):
            return None
        if (
            notification.status == StoreOfferNotificationStatus.FAILED_RETRYABLE
            and notification.next_retry_at is not None
            and notification.next_retry_at > timezone.now()
        ):
            return None
        offer = StoreOffer.objects.get(pk=notification.offer_id)
        if (
            offer.status != StoreOfferStatus.PENDING
            or offer.expires_at <= timezone.now()
            or StoreOffer.objects.filter(
                order_id=offer.order_id, status=StoreOfferStatus.ACCEPTED
            ).exists()
        ):
            notification.status = StoreOfferNotificationStatus.CANCELLED
            notification.next_retry_at = None
            notification.save(update_fields=["status", "next_retry_at", "updated_at"])
            return None
        if notification.conversation_id is None:
            identity = notification.channel_identity
            conversation, _ = (
                self.conversation_resolver.get_or_create_store_conversation(
                    recipient=notification.recipient,
                    channel_identity=identity,
                    store=offer.store,
                )
            )
            notification.conversation = conversation
            notification.save(update_fields=["conversation", "updated_at"])
        number = (
            notification.attempts.aggregate(maximum=Max("attempt_number"))["maximum"]
            or 0
        ) + 1
        attempt = StoreOfferNotificationAttempt.objects.create(
            notification=notification,
            attempt_number=number,
            provider=notification.channel,
            channel=notification.channel,
            correlation_id=correlation_id,
        )
        notification.status = StoreOfferNotificationStatus.SENDING
        notification.save(update_fields=["status", "updated_at"])
        logger.info(
            "store_offer_notification_claimed",
            extra={
                "order_id": str(offer.order_id),
                "offer_id": str(offer.id),
                "notification_id": str(notification.id),
                "attempt_id": str(attempt.id),
                "correlation_id": correlation_id,
            },
        )
        logger.info(
            "store_offer_notification_attempt_created",
            extra={
                "offer_id": str(offer.id),
                "notification_id": str(notification.id),
                "attempt_id": str(attempt.id),
                "correlation_id": correlation_id,
            },
        )
        return attempt

    @transaction.atomic
    def record_success(self, attempt_id, result):
        attempt = (
            StoreOfferNotificationAttempt.objects.select_for_update()
            .select_related("notification")
            .get(pk=attempt_id)
        )
        if attempt.status != StoreOfferNotificationAttemptStatus.STARTED:
            return attempt.notification
        now = timezone.now()
        attempt.status = StoreOfferNotificationAttemptStatus.SUCCEEDED
        attempt.completed_at = now
        attempt.provider_message_id = result.external_message_id
        attempt.message_event = result.message_event
        attempt.save(
            update_fields=[
                "status",
                "completed_at",
                "provider_message_id",
                "message_event",
                "updated_at",
            ]
        )
        notification = attempt.notification
        notification.status, notification.sent_at, notification.next_retry_at = (
            StoreOfferNotificationStatus.SENT,
            now,
            None,
        )
        notification.save(
            update_fields=["status", "sent_at", "next_retry_at", "updated_at"]
        )
        logger.info(
            "store_offer_notification_sent",
            extra={
                "offer_id": str(notification.offer_id),
                "notification_id": str(notification.id),
                "attempt_id": str(attempt.id),
                "correlation_id": attempt.correlation_id,
            },
        )
        return notification

    @transaction.atomic
    def record_failure(
        self,
        attempt_id,
        classification: FailureDecision,
        *,
        delay_seconds: int | None = None,
    ):
        attempt = (
            StoreOfferNotificationAttempt.objects.select_for_update()
            .select_related("notification")
            .get(pk=attempt_id)
        )
        if attempt.status != StoreOfferNotificationAttemptStatus.STARTED:
            return attempt.notification
        now = timezone.now()
        attempt.status, attempt.completed_at = (
            StoreOfferNotificationAttemptStatus.FAILED,
            now,
        )
        attempt.error_category, attempt.retryable = (
            classification.category,
            classification.retryable,
        )
        attempt.save(
            update_fields=[
                "status",
                "completed_at",
                "error_category",
                "retryable",
                "updated_at",
            ]
        )
        notification = attempt.notification
        notification.last_error_category = classification.category
        notification.failed_at = now
        if classification.retryable and delay_seconds is not None:
            notification.status = StoreOfferNotificationStatus.FAILED_RETRYABLE
            notification.next_retry_at = now + timedelta(seconds=delay_seconds)
        else:
            notification.status = StoreOfferNotificationStatus.FAILED_PERMANENT
            notification.next_retry_at = None
        notification.save(
            update_fields=[
                "status",
                "last_error_category",
                "failed_at",
                "next_retry_at",
                "updated_at",
            ]
        )
        logger.warning(
            "store_offer_notification_failed",
            extra={
                "offer_id": str(notification.offer_id),
                "notification_id": str(notification.id),
                "attempt_id": str(attempt.id),
                "error_category": classification.category,
                "retryable": classification.retryable,
                "correlation_id": attempt.correlation_id,
            },
        )
        return notification

    @transaction.atomic
    def close_stale_sending(self, notification_id, *, now=None):
        """Stop ambiguous stale sends without risking a provider duplicate."""

        now = now or timezone.now()
        notification = StoreOfferNotification.objects.select_for_update().get(
            pk=notification_id
        )
        if notification.status != StoreOfferNotificationStatus.SENDING:
            return notification
        attempt = (
            notification.attempts.select_for_update()
            .filter(status=StoreOfferNotificationAttemptStatus.STARTED)
            .order_by("-attempt_number")
            .first()
        )
        if attempt is not None:
            attempt.status = StoreOfferNotificationAttemptStatus.FAILED
            attempt.completed_at = now
            attempt.error_category = "AMBIGUOUS_DELIVERY"
            attempt.retryable = False
            attempt.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "error_category",
                    "retryable",
                    "updated_at",
                ]
            )
        notification.status = StoreOfferNotificationStatus.FAILED_PERMANENT
        notification.failed_at = now
        notification.last_error_category = "AMBIGUOUS_DELIVERY"
        notification.next_retry_at = None
        notification.save(
            update_fields=[
                "status",
                "failed_at",
                "last_error_category",
                "next_retry_at",
                "updated_at",
            ]
        )
        return notification


def retry_delay_seconds(attempt_number: int, retry_after: int | None = None) -> int:
    if retry_after is not None:
        return retry_after
    base = getattr(settings, "STORE_OFFER_NOTIFICATION_RETRY_BASE_SECONDS", 30)
    maximum = getattr(settings, "STORE_OFFER_NOTIFICATION_RETRY_MAX_SECONDS", 900)
    return min(base * (2 ** max(attempt_number - 1, 0)), maximum)
