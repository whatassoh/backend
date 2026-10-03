"""Channel-neutral preparation transitions for the store that won an order."""

import logging
from enum import StrEnum
from uuid import UUID

from django.db import transaction

from apps.audit.domain.enums import AuditActorType
from apps.orders.application.services import mark_preparing, mark_ready_for_pickup
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import OrderReadyForPickup
from apps.orders.domain.exceptions import InvalidOrderTransition
from apps.orders.infrastructure.models import Order
from apps.stores.application.offer_actions import StoreOfferAuthorizationPolicy
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer
from common.events import publish

logger = logging.getLogger(__name__)

STORE_ORDER_ACTIONS = (
    "store_order.start_preparation",
    "store_order.mark_ready",
)
STORE_ORDER_CALLBACK_ALIASES = {
    "store_order.start_preparation": "store_order.start",
    "store_order.mark_ready": "store_order.ready",
}
STORE_ORDER_CALLBACK_ACTIONS = {
    callback_id: action_id
    for action_id, callback_id in STORE_ORDER_CALLBACK_ALIASES.items()
}


def encode_store_order_action(action_id: str, order_id) -> str:
    if action_id not in STORE_ORDER_ACTIONS:
        raise ValueError("Unsupported store order action.")
    return f"{STORE_ORDER_CALLBACK_ALIASES[action_id]}:{UUID(str(order_id))}"


def decode_store_order_action(callback_id: str) -> tuple[str, UUID] | None:
    action_id, separator, raw_order_id = (callback_id or "").partition(":")
    canonical_action = STORE_ORDER_CALLBACK_ACTIONS.get(action_id)
    if not separator or canonical_action is None:
        return None
    try:
        return canonical_action, UUID(raw_order_id)
    except (ValueError, AttributeError):
        return None


class StorePreparationResult(StrEnum):
    STARTED = "STARTED"
    READY = "READY"
    ALREADY_PREPARING = "ALREADY_PREPARING"
    ALREADY_READY = "ALREADY_READY"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class StorePreparationService:
    """The shared service for chat and Dashboard; never trusts a client store id."""

    def __init__(self, *, authorization_policy=None):
        self.authorization_policy = authorization_policy or StoreOfferAuthorizationPolicy()

    def start_preparation(self, *, actor, order_id, correlation_id=None):
        return self._transition(
            actor=actor, order_id=order_id, target=OrderStatus.PREPARING,
            correlation_id=correlation_id,
        )

    def mark_ready_for_pickup(self, *, actor, order_id, correlation_id=None):
        return self._transition(
            actor=actor, order_id=order_id, target=OrderStatus.READY_FOR_PICKUP,
            correlation_id=correlation_id,
        )

    def available_order_for(self, *, actor, target):
        """Resolve a chat button without accepting a store id from its payload."""
        orders = self.available_orders_for(actor=actor, target=target)
        return orders[0].id if len(orders) == 1 else None

    def available_orders_for(self, *, actor, target):
        """List authorized candidates for text-action clarification."""
        statuses = [OrderStatus.STORE_ACCEPTED] if target == OrderStatus.PREPARING else [OrderStatus.PREPARING]
        offers = StoreOffer.objects.filter(
            status=StoreOfferStatus.ACCEPTED, order__status__in=statuses
        ).select_related("order", "store")
        allowed = [offer for offer in offers if self.authorization_policy.can_view_offer(actor, offer)]
        return tuple(offer.order for offer in allowed)

    def _transition(self, *, actor, order_id, target, correlation_id):
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if order is None:
                logger.info("store_preparation_action_denied", extra={"order_id": str(order_id), "correlation_id": correlation_id})
                return StorePreparationResult.NOT_AUTHORIZED
            offer = StoreOffer.objects.select_for_update().select_related("store").filter(
                order=order, status=StoreOfferStatus.ACCEPTED
            ).first()
            if offer is None or not self.authorization_policy.can_view_offer(actor, offer):
                logger.info("store_preparation_action_denied", extra={"order_id": str(order.id), "correlation_id": correlation_id})
                return StorePreparationResult.NOT_AUTHORIZED
            if target == OrderStatus.PREPARING and order.status == OrderStatus.PREPARING:
                return StorePreparationResult.ALREADY_PREPARING
            if target == OrderStatus.READY_FOR_PICKUP and order.status == OrderStatus.READY_FOR_PICKUP:
                return StorePreparationResult.ALREADY_READY
            try:
                if target == OrderStatus.PREPARING:
                    mark_preparing(order, actor=actor, actor_type=AuditActorType.USER, correlation_id=correlation_id)
                    from apps.orders.application.customer_notifications import (
                        notify_store_preparation_started,
                    )

                    transaction.on_commit(
                        lambda: notify_store_preparation_started(
                            order_id=order.id, correlation_id=correlation_id
                        )
                    )
                    logger.info("store_order_preparing", extra={"order_id": str(order.id), "store_id": str(offer.store_id), "correlation_id": correlation_id})
                    return StorePreparationResult.STARTED
                mark_ready_for_pickup(order, actor=actor, actor_type=AuditActorType.USER, correlation_id=correlation_id)
            except InvalidOrderTransition:
                logger.info("store_preparation_transition_conflict", extra={"order_id": str(order.id), "correlation_id": correlation_id})
                return StorePreparationResult.NOT_AVAILABLE
            transaction.on_commit(lambda: publish(OrderReadyForPickup(
                order_id=order.id, store_id=offer.store_id, customer_id=order.customer_id,
                correlation_id=correlation_id,
            )))
            logger.info("store_order_ready", extra={"order_id": str(order.id), "store_id": str(offer.store_id), "correlation_id": correlation_id})
            return StorePreparationResult.READY
