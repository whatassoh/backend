"""Privacy-scoped, pull-based delivery tracking from durable domain truth."""

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from apps.drivers.application.delivery_execution import DeliveryMissionAuthorizationError
from apps.drivers.infrastructure.models import DriverAssignment
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreMembershipStatus, StoreOfferStatus
from apps.stores.infrastructure.models import StoreMembership

logger = logging.getLogger(__name__)


class TrackingLocationStatus(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    FRESH = "FRESH"
    STALE = "STALE"


@dataclass(frozen=True, kw_only=True)
class TrackingPlaceDto:
    display_name: str | None
    address_label: str | None


@dataclass(frozen=True, kw_only=True)
class TrackingDriverDto:
    assigned: bool
    location_status: TrackingLocationStatus
    last_location_at: object | None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True, kw_only=True)
class DeliveryTrackingSnapshot:
    order_id: str
    order_reference: str
    order_status: str
    pickup: TrackingPlaceDto | None
    destination: TrackingPlaceDto
    driver: TrackingDriverDto
    assigned_at: object | None
    picked_up_at: object | None
    delivery_started_at: object | None


class DeliveryTrackingService:
    """Read-only tracking: PostgreSQL locations, never Redis or conversation truth."""

    def get_customer_tracking(self, actor: object, *, order_id: UUID | str | None = None,
                              reference: str | None = None) -> DeliveryTrackingSnapshot:
        user = self._user(actor)
        order = self._order(order_id=order_id, reference=reference)
        if order.customer_id != getattr(user, "id", None):
            self._forbidden("customer_not_owner")
        return self._snapshot(order, expose_driver_position=True)

    def get_driver_tracking(self, actor: object, *, order_id: UUID | str) -> DeliveryTrackingSnapshot:
        user = self._user(actor)
        order = self._order(order_id=order_id)
        assignment = getattr(order, "driver_assignment", None)
        if assignment is None or assignment.driver.user_id != getattr(user, "id", None):
            self._forbidden("driver_not_assigned")
        return self._snapshot(order, expose_driver_position=True)

    def get_store_tracking(self, actor: object, *, order_id: UUID | str) -> DeliveryTrackingSnapshot:
        user = self._user(actor)
        order = self._order(order_id=order_id)
        offer = self._winning_offer(order)
        if offer is None or not StoreMembership.objects.filter(
            store=offer.store, user=user, status=StoreMembershipStatus.ACTIVE
        ).exists():
            self._forbidden("store_not_authorized")
        # Stores receive operational Order status but never driver live GPS.
        return self._snapshot(order, expose_driver_position=False)

    @staticmethod
    def _user(actor):
        return getattr(actor, "user", actor)

    @staticmethod
    def _order(*, order_id=None, reference=None):
        if (order_id is None) == (reference is None):
            raise ValueError("Provide exactly one order id or reference.")
        query = Order.objects.select_related(
            "customer", "driver_assignment__driver", "driver_assignment__driver__user"
        ).prefetch_related("store_offers__store")
        order = query.filter(pk=order_id).first() if order_id is not None else query.filter(reference=reference).first()
        if order is None:
            raise DeliveryMissionAuthorizationError("Order tracking is not available.")
        return order

    @staticmethod
    def _winning_offer(order):
        return next((offer for offer in order.store_offers.all() if offer.status == StoreOfferStatus.ACCEPTED), None)

    def _snapshot(self, order, *, expose_driver_position):
        assignment = getattr(order, "driver_assignment", None)
        offer = self._winning_offer(order)
        pickup = TrackingPlaceDto(display_name=offer.store.name, address_label=offer.store.address_label) if offer else None
        terminal = order.status in {OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.FAILED}
        driver = assignment.driver if assignment else None
        location_status = TrackingLocationStatus.UNAVAILABLE
        latitude = longitude = None
        last_location_at = driver.last_location_at if driver else None
        if driver and driver.last_location is not None and last_location_at is not None:
            age = (timezone.now() - last_location_at).total_seconds()
            location_status = TrackingLocationStatus.FRESH if 0 <= age <= settings.DRIVER_LOCATION_FRESHNESS_SECONDS else TrackingLocationStatus.STALE
            # Exact location is an authenticated customer-only, active-trip
            # capability. Stale points are never represented as current.
            if expose_driver_position and not terminal and order.status == OrderStatus.IN_DELIVERY and location_status == TrackingLocationStatus.FRESH:
                latitude, longitude = driver.last_location.y, driver.last_location.x
        logger.info("delivery_tracking_requested", extra={"location_status": location_status})
        if location_status == TrackingLocationStatus.FRESH:
            logger.info("delivery_tracking_location_fresh")
        elif location_status == TrackingLocationStatus.STALE:
            logger.info("delivery_tracking_location_stale")
        else:
            logger.info("delivery_tracking_location_unavailable")
        return DeliveryTrackingSnapshot(
            order_id=str(order.id), order_reference=order.reference, order_status=order.status,
            pickup=pickup,
            destination=TrackingPlaceDto(display_name=None, address_label=order.delivery_address_label),
            driver=TrackingDriverDto(assigned=assignment is not None, location_status=location_status,
                last_location_at=last_location_at, latitude=latitude, longitude=longitude),
            assigned_at=assignment.created_at if assignment else None,
            picked_up_at=order.picked_up_at, delivery_started_at=order.delivery_started_at,
        )

    @staticmethod
    def _forbidden(reason):
        logger.info("delivery_tracking_forbidden", extra={"reason": reason})
        raise DeliveryMissionAuthorizationError("Order tracking is not available.")
