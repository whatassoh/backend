"""Persistent notification intent for one DriverOffer; provider delivery is outer work."""
from dataclasses import dataclass

from apps.conversations.application.services import get_or_create_active_conversation
from apps.drivers.domain.enums import DriverOfferNotificationStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverOfferNotification
from apps.messaging.infrastructure.models import ChannelIdentity
from common.async_tasks import enqueue_after_commit


class DriverRecipientResolver:
    """One deterministic active identity: channel then creation order."""
    def resolve(self, driver):
        return ChannelIdentity.objects.filter(user=driver.user, is_active=True).order_by("channel", "id").first()


@dataclass(frozen=True, kw_only=True)
class DriverOfferPresentation:
    offer_id: str
    order_reference: str
    pickup_store_name: str
    distance_meters: float | None
    expires_at: str
    actions: tuple[str, ...]


class DriverOfferNotificationService:
    def __init__(self, *, resolver=None): self.resolver = resolver or DriverRecipientResolver()
    def create_for_offer(self, offer, *, correlation_id=None):
        if offer.status != DriverOfferStatus.PENDING: return None
        identity = self.resolver.resolve(offer.driver)
        if identity is None: return None
        conversation, _ = get_or_create_active_conversation(channel_identity=identity)
        conversation.context = {**(conversation.context or {}), "driver_offer_id": str(offer.id)}
        conversation.save(update_fields=["context", "updated_at"])
        snapshot = self._snapshot(offer)
        key = f"driver-offer:{offer.id}:identity:{identity.id}"
        notification, _ = DriverOfferNotification.objects.get_or_create(idempotency_key=key, defaults={"offer": offer, "recipient": offer.driver.user, "channel_identity": identity, "conversation": conversation, "channel": identity.channel, "presentation_snapshot": snapshot})
        if notification.status in (DriverOfferNotificationStatus.PENDING, DriverOfferNotificationStatus.FAILED_RETRYABLE):
            self.enqueue(notification.id, correlation_id)
        return notification
    def enqueue(self, notification_id, correlation_id=None):
        from apps.drivers.tasks import deliver_driver_offer_notification
        enqueue_after_commit(
            lambda: deliver_driver_offer_notification.delay(str(notification_id), correlation_id),
            task_name="drivers.deliver_driver_offer_notification",
            entity_id=str(notification_id),
            correlation_id=correlation_id,
        )
    @staticmethod
    def _snapshot(offer):
        store = offer.order.store_offers.filter(status="ACCEPTED").select_related("store").first()
        return {"offer_id": str(offer.id), "order_reference": offer.order.reference, "pickup_store_name": store.store.name if store else "", "distance_meters": (offer.candidate_snapshot or {}).get("distance_meters"), "expires_at": offer.expires_at.isoformat(), "actions": ["driver_offer.accept", "driver_offer.reject"]}
