"""DB-backed read/authorization foundation for driver delivery execution.

No conversational context, LLM output, or caller-provided store identifier is
authoritative here.  Mutating delivery actions intentionally remain outside
lot 11.1.
"""

import logging
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.audit.domain.enums import AuditActorType
from apps.drivers.domain.dtos import (
    DeliveryConfirmationResult,
    DriverMissionDto,
    DriverMissionLocationDto,
    PickupConfirmationResult,
    StartDeliveryResult,
)
from apps.drivers.domain.enums import DriverStatus
from apps.drivers.infrastructure.models import DriverAssignment, DriverProfile
from apps.orders.application.services import (
    fail_order,
    mark_delivered,
    mark_in_delivery,
    mark_picked_up,
)
from apps.orders.domain.enums import OrderFailureCode, OrderStatus
from apps.orders.domain.events import (
    DeliveryFailed,
    DeliveryIssueReported,
    DeliveryStarted,
    OrderDelivered,
    OrderPickedUp,
)
from apps.stores.domain.enums import StoreOfferStatus
from common.events import publish

logger = logging.getLogger(__name__)

ACTIVE_DELIVERY_ORDER_STATUSES = frozenset({
    OrderStatus.DRIVER_ASSIGNED,
    OrderStatus.PICKED_UP,
    OrderStatus.IN_DELIVERY,
})

ALLOWED_ACTIONS_BY_ORDER_STATUS = {
    OrderStatus.DRIVER_ASSIGNED: ("delivery.confirm_pickup",),
    OrderStatus.PICKED_UP: ("delivery.start_delivery",),
    OrderStatus.IN_DELIVERY: (
        "delivery.confirm_delivered",
        "delivery.report_issue",
    ),
}

DELIVERY_ASSIGNMENT_ACTIONS = frozenset(
    {
        "delivery.confirm_pickup",
        "delivery.start_delivery",
        "delivery.confirm_delivered",
    }
)


def encode_delivery_action(action_id: str, assignment_id: UUID | str) -> str:
    """Bind a conversational delivery action to one durable assignment."""
    if action_id not in DELIVERY_ASSIGNMENT_ACTIONS:
        raise ValueError("Unsupported delivery action.")
    return f"{action_id}:{UUID(str(assignment_id))}"


def decode_delivery_action(callback_id: str) -> tuple[str, UUID] | None:
    action_id, separator, raw_assignment_id = (callback_id or "").partition(":")
    if not separator or action_id not in DELIVERY_ASSIGNMENT_ACTIONS:
        return None
    try:
        return action_id, UUID(raw_assignment_id)
    except (ValueError, AttributeError):
        return None


class DeliveryMissionNotFound(LookupError):
    """There is no active delivery mission matching the requested truth."""


class DeliveryMissionAuthorizationError(PermissionError):
    """The supplied user/identity cannot act on this assignment."""


class DeliveryExecutionService:
    """Resolves the durable mission owned by an authenticated driver user."""

    def get_active_mission(self, actor: object) -> DriverMissionDto:
        driver = self.validate_driver_actor(actor)
        assignment = self._active_assignments().filter(driver=driver).first()
        if assignment is None:
            logger.info("delivery_mission_not_found", extra={"reason": "no_active_assignment"})
            raise DeliveryMissionNotFound("No active delivery mission.")
        logger.info("delivery_mission_loaded", extra={"status": assignment.order.status})
        return self._to_dto(assignment)

    def get_mission(
        self,
        actor: object,
        *,
        assignment_id: UUID | str | None = None,
        order_id: UUID | str | None = None,
    ) -> DriverMissionDto:
        """Load an active mission only if its durable ownership matches actor.

        IDs are selectors, never authority.  Supplying both is supported and
        requires they identify the same assignment.
        """
        if assignment_id is None and order_id is None:
            return self.get_active_mission(actor)
        driver = self.validate_driver_actor(actor)
        assignments = self._active_assignments().filter(driver=driver)
        if assignment_id is not None:
            assignments = assignments.filter(pk=assignment_id)
        if order_id is not None:
            assignments = assignments.filter(order_id=order_id)
        assignment = assignments.first()
        if assignment is None:
            # Do not disclose whether a forged id exists or who owns it.
            logger.info("delivery_mission_authorization_failed", extra={"reason": "not_owned_or_inactive"})
            raise DeliveryMissionAuthorizationError("Delivery mission is not available to this driver.")
        logger.info("delivery_mission_loaded", extra={"status": assignment.order.status})
        return self._to_dto(assignment)

    def validate_driver_actor(self, actor: object) -> DriverProfile:
        """Accept a User or a ChannelIdentity linked to that User."""
        user = getattr(actor, "user", actor)
        user_id = getattr(user, "id", None)
        if user_id is None:
            logger.info("delivery_mission_authorization_failed", extra={"reason": "invalid_actor"})
            raise DeliveryMissionAuthorizationError("A driver user is required.")
        driver = DriverProfile.objects.select_related("user").filter(user_id=user_id).first()
        if driver is None or driver.status != DriverStatus.ACTIVE:
            logger.info("delivery_mission_authorization_failed", extra={"reason": "driver_unavailable"})
            raise DeliveryMissionAuthorizationError("Driver profile is not active.")
        return driver

    def validate_assignment_active(self, actor: object, assignment_id: UUID | str) -> DriverAssignment:
        driver = self.validate_driver_actor(actor)
        assignment = self._active_assignments().filter(pk=assignment_id, driver=driver).first()
        if assignment is None:
            logger.info("delivery_mission_authorization_failed", extra={"reason": "assignment_not_owned_or_inactive"})
            raise DeliveryMissionAuthorizationError("Delivery assignment is not active for this driver.")
        return assignment

    def confirm_pickup(self, actor: object, *, assignment_id: UUID | str | None = None,
                       order_id: UUID | str | None = None, correlation_id: str | None = None) -> PickupConfirmationResult:
        """Apply the MVP proof: assigned authenticated driver confirmation."""
        driver = self.validate_driver_actor(actor)
        if assignment_id is None:
            assignment_id = self.get_mission(driver.user, order_id=order_id).assignment_id
        # This preliminary lookup establishes a global lock order only.  All
        # authorization is re-evaluated under locks below.
        pointer = DriverAssignment.objects.filter(pk=assignment_id).values("order_id").first()
        if pointer is None:
            self._pickup_rejected("assignment_missing")
        with transaction.atomic():
            order = self._lock_order(pointer["order_id"])
            assignment = (DriverAssignment.objects.select_for_update(of=("self",))
                .select_related("driver").filter(pk=assignment_id, order=order, driver=driver).first())
            if assignment is None or (order_id is not None and str(order.id) != str(order_id)):
                self._pickup_rejected("not_owned_or_mismatched")
            winning_offer = order.store_offers.select_related("store").filter(
                status=StoreOfferStatus.ACCEPTED).first()
            if winning_offer is None:
                self._pickup_rejected("winning_store_missing")
            if order.status == OrderStatus.PICKED_UP:
                if not assignment.is_active:
                    self._pickup_rejected("assignment_inactive")
                logger.info("delivery_pickup_duplicate", extra={"reason": "already_picked_up"})
                return PickupConfirmationResult(mission=self._to_dto(assignment),
                    picked_up_at=order.picked_up_at, duplicate=True)
            if not assignment.is_active:
                self._pickup_rejected("assignment_inactive")
            if order.status != OrderStatus.DRIVER_ASSIGNED:
                self._pickup_rejected("invalid_order_state")
            transitioned = mark_picked_up(order, actor=driver.user,
                actor_type=AuditActorType.USER, correlation_id=correlation_id)
            # `assignment.order` was select_related before the transition;
            # return the post-transition mission, not that stale relation.
            assignment.order = transitioned
            transaction.on_commit(lambda: publish(OrderPickedUp(
                order_id=transitioned.id, assignment_id=assignment.id, driver_id=driver.id,
                picked_up_at=transitioned.picked_up_at, correlation_id=correlation_id)))
            self._create_pickup_notification_intents(
                order=transitioned, store=winning_offer.store, correlation_id=correlation_id)
            logger.info("delivery_pickup_confirmed", extra={"reason": "driver_confirmation"})
            return PickupConfirmationResult(mission=self._to_dto(assignment),
                picked_up_at=transitioned.picked_up_at, duplicate=False)

    def start_delivery(self, actor: object, *, assignment_id: UUID | str | None = None,
                       order_id: UUID | str | None = None, correlation_id: str | None = None) -> StartDeliveryResult:
        """Explicitly start the customer-bound trip; GPS never starts it."""
        driver = self.validate_driver_actor(actor)
        if assignment_id is None:
            assignment_id = self.get_mission(driver.user, order_id=order_id).assignment_id
        pointer = DriverAssignment.objects.filter(pk=assignment_id).values("order_id").first()
        if pointer is None:
            self._start_rejected("assignment_missing")
        with transaction.atomic():
            order = self._lock_order(pointer["order_id"])
            assignment = (DriverAssignment.objects.select_for_update(of=("self",))
                .select_related("driver").filter(pk=assignment_id, order=order, driver=driver).first())
            if assignment is None or (order_id is not None and str(order.id) != str(order_id)):
                self._start_rejected("not_owned_or_mismatched")
            if order.status == OrderStatus.IN_DELIVERY:
                if not assignment.is_active:
                    self._start_rejected("assignment_inactive")
                logger.info("delivery_start_duplicate", extra={"reason": "already_in_delivery"})
                return StartDeliveryResult(mission=self._to_dto(assignment),
                    delivery_started_at=order.delivery_started_at, duplicate=True)
            if not assignment.is_active:
                self._start_rejected("assignment_inactive")
            if order.status != OrderStatus.PICKED_UP:
                self._start_rejected("invalid_order_state")
            transitioned = mark_in_delivery(order, actor=driver.user,
                actor_type=AuditActorType.USER, correlation_id=correlation_id)
            assignment.order = transitioned
            transaction.on_commit(lambda: publish(DeliveryStarted(
                order_id=transitioned.id, assignment_id=assignment.id, driver_id=driver.id,
                delivery_started_at=transitioned.delivery_started_at, correlation_id=correlation_id)))
            self._create_start_notification_intent(order=transitioned, correlation_id=correlation_id)
            logger.info("delivery_started", extra={"reason": "driver_action"})
            return StartDeliveryResult(mission=self._to_dto(assignment),
                delivery_started_at=transitioned.delivery_started_at, duplicate=False)

    def confirm_delivered(self, actor: object, *, assignment_id: UUID | str | None = None,
                          order_id: UUID | str | None = None, correlation_id: str | None = None) -> DeliveryConfirmationResult:
        """MVP proof: explicit confirmation by the authenticated assigned driver."""
        driver = self.validate_driver_actor(actor)
        if assignment_id is None:
            assignment_id = self.get_mission(driver.user, order_id=order_id).assignment_id
        pointer = DriverAssignment.objects.filter(pk=assignment_id).values("order_id").first()
        if pointer is None:
            self._completion_rejected("assignment_missing")
        with transaction.atomic():
            order = self._lock_order(pointer["order_id"])
            assignment = (DriverAssignment.objects.select_for_update(of=("self",))
                .select_related("driver").filter(pk=assignment_id, order=order, driver=driver).first())
            if assignment is None or (order_id is not None and str(order.id) != str(order_id)):
                self._completion_rejected("not_owned_or_mismatched")
            if order.status == OrderStatus.DELIVERED:
                logger.info("delivery_completion_duplicate", extra={"reason": "already_delivered"})
                return DeliveryConfirmationResult(mission=self._to_dto(assignment),
                    delivered_at=order.delivered_at, duplicate=True)
            if not assignment.is_active:
                self._completion_rejected("assignment_inactive")
            if order.status != OrderStatus.IN_DELIVERY:
                self._completion_rejected("invalid_order_state")
            transitioned = mark_delivered(order, actor=driver.user,
                actor_type=AuditActorType.USER, correlation_id=correlation_id)
            assignment.refresh_from_db()
            assignment.order = transitioned
            transaction.on_commit(lambda: publish(OrderDelivered(
                order_id=transitioned.id, assignment_id=assignment.id, driver_id=driver.id,
                delivered_at=transitioned.delivered_at, correlation_id=correlation_id)))
            self._create_completion_notification_intents(order=transitioned, correlation_id=correlation_id)
            logger.info("delivery_completed", extra={"reason": "driver_confirmation"})
            return DeliveryConfirmationResult(mission=self._to_dto(assignment),
                delivered_at=transitioned.delivered_at, duplicate=False)

    def report_issue(self, actor: object, *, assignment_id: UUID | str, issue_type: str,
                     note: str | None = None, correlation_id: str | None = None):
        from apps.drivers.domain.enums import DeliveryIssueStatus, DeliveryIssueType
        from apps.drivers.infrastructure.models import DeliveryIssue
        driver = self.validate_driver_actor(actor)
        if issue_type not in DeliveryIssueType.values:
            self._issue_rejected("invalid_issue_type")
        pointer = DriverAssignment.objects.filter(pk=assignment_id).values("order_id").first()
        if pointer is None:
            self._issue_rejected("assignment_missing")
        with transaction.atomic():
            order = self._lock_order(pointer["order_id"])
            assignment = DriverAssignment.objects.select_for_update(of=("self",)).filter(
                pk=assignment_id, order=order, driver=driver, is_active=True).first()
            if assignment is None:
                self._issue_rejected("not_owned_or_inactive")
            allowed = {OrderStatus.DRIVER_ASSIGNED, OrderStatus.PICKED_UP, OrderStatus.IN_DELIVERY}
            if order.status not in allowed or (issue_type == DeliveryIssueType.PICKUP_PROBLEM and order.status != OrderStatus.DRIVER_ASSIGNED):
                self._issue_rejected("invalid_order_state")
            issue, created = DeliveryIssue.objects.get_or_create(
                assignment=assignment, issue_type=issue_type, status=DeliveryIssueStatus.OPEN,
                defaults={"order": order, "reported_by": driver.user, "note": (note or "").strip() or None},
            )
            if created:
                transaction.on_commit(lambda: publish(DeliveryIssueReported(
                    order_id=order.id, assignment_id=assignment.id, issue_id=issue.id,
                    issue_type=issue.issue_type, correlation_id=correlation_id)))
                logger.info("delivery_issue_reported", extra={"issue_type": issue.issue_type, "stage": order.status})
            return issue, created

    def resolve_issue(self, actor: object, *, issue_id: UUID | str, resolution: str | None = None):
        from apps.drivers.domain.enums import DeliveryIssueStatus
        from apps.drivers.infrastructure.models import DeliveryIssue
        driver = self.validate_driver_actor(actor)
        with transaction.atomic():
            issue = DeliveryIssue.objects.select_for_update().select_related("assignment", "order").filter(
                pk=issue_id, assignment__driver=driver).first()
            if issue is None or issue.status != DeliveryIssueStatus.OPEN:
                self._issue_rejected("issue_not_open_or_not_owned")
            issue.status, issue.resolved_at, issue.resolution = DeliveryIssueStatus.RESOLVED, timezone.now(), (resolution or "").strip() or None
            issue.save(update_fields=["status", "resolved_at", "resolution", "updated_at"])
            logger.info("delivery_issue_resolved", extra={"issue_type": issue.issue_type})
            return issue

    def confirm_failure(self, actor: object, *, assignment_id: UUID | str, issue_id: UUID | str,
                        correlation_id: str | None = None):
        from apps.drivers.domain.enums import DeliveryIssueStatus
        from apps.drivers.infrastructure.models import DeliveryIssue
        driver = self.validate_driver_actor(actor)
        pointer = DriverAssignment.objects.filter(pk=assignment_id).values("order_id").first()
        if pointer is None:
            self._completion_rejected("assignment_missing")
        with transaction.atomic():
            order = self._lock_order(pointer["order_id"])
            assignment = DriverAssignment.objects.select_for_update(of=("self",)).filter(pk=assignment_id, order=order, driver=driver).first()
            issue = DeliveryIssue.objects.select_for_update().filter(pk=issue_id, assignment=assignment, status=DeliveryIssueStatus.OPEN).first() if assignment else None
            if assignment is None or issue is None:
                self._completion_rejected("issue_not_owned_or_open")
            if order.status == OrderStatus.FAILED and order.failure_code == OrderFailureCode.DELIVERY_FAILED:
                return order, True
            if order.status not in {OrderStatus.DRIVER_ASSIGNED, OrderStatus.PICKED_UP, OrderStatus.IN_DELIVERY}:
                self._completion_rejected("invalid_order_state")
            transitioned = fail_order(order, OrderFailureCode.DELIVERY_FAILED, actor=driver.user,
                actor_type=AuditActorType.USER, reason=issue.issue_type, correlation_id=correlation_id)
            issue.status = DeliveryIssueStatus.TERMINAL
            issue.resolved_at = timezone.now()
            issue.resolution = "TERMINAL_FAILURE_CONFIRMED"
            issue.save(update_fields=["status", "resolved_at", "resolution", "updated_at"])
            transaction.on_commit(lambda: publish(DeliveryFailed(order_id=transitioned.id, assignment_id=assignment.id,
                driver_id=driver.id, issue_id=issue.id, correlation_id=correlation_id)))
            logger.info("delivery_failed", extra={"issue_type": issue.issue_type, "stage": order.status})
            return transitioned, False

    @staticmethod
    def _lock_order(order_id):
        from apps.orders.infrastructure.models import Order
        return Order.objects.select_for_update().prefetch_related("store_offers__store").get(pk=order_id)

    @staticmethod
    def _pickup_rejected(reason: str):
        logger.info("delivery_pickup_rejected", extra={"reason": reason})
        raise DeliveryMissionAuthorizationError("Pickup confirmation is not available for this mission.")

    @staticmethod
    def _start_rejected(reason: str):
        logger.info("delivery_start_rejected", extra={"reason": reason})
        raise DeliveryMissionAuthorizationError("Delivery start is not available for this mission.")

    @staticmethod
    def _completion_rejected(reason: str):
        logger.info("delivery_completion_rejected", extra={"reason": reason})
        raise DeliveryMissionAuthorizationError("Delivery completion is not available for this mission.")

    @staticmethod
    def _issue_rejected(reason: str):
        logger.info("delivery_issue_rejected", extra={"reason": reason})
        raise DeliveryMissionAuthorizationError("Delivery issue action is not available for this mission.")

    @staticmethod
    def _create_pickup_notification_intents(*, order, store, correlation_id):
        """Persist notification intents atomically; delivery occurs post-commit."""
        from apps.messaging.application.notifications import NotificationService
        from apps.messaging.domain.enums import NotificationKind
        from apps.stores.domain.enums import StoreMembershipStatus
        from apps.stores.infrastructure.models import StoreMembership

        notifications = NotificationService()
        notifications.create_intent(
            kind=NotificationKind.ORDER_PICKED_UP, recipient=order.customer,
            business_entity_type="orders.Order", business_entity_id=order.id,
            idempotency_key=f"order-picked-up:customer:{order.id}",
            payload={"reference": order.reference, "text": "Votre commande a été récupérée par le livreur."},
            correlation_id=correlation_id,
        )
        for membership in StoreMembership.objects.select_related("user").filter(
            store=store, status=StoreMembershipStatus.ACTIVE
        ):
            notifications.create_intent(
                kind=NotificationKind.ORDER_PICKED_UP, recipient=membership.user,
                business_entity_type="orders.Order", business_entity_id=order.id,
                idempotency_key=f"order-picked-up:store:{order.id}:user:{membership.user_id}",
                payload={"reference": order.reference, "text": "Le livreur a récupéré la commande."},
                correlation_id=correlation_id,
            )

    @staticmethod
    def _create_start_notification_intent(*, order, correlation_id):
        from apps.messaging.application.notifications import NotificationService
        from apps.messaging.domain.enums import NotificationKind

        NotificationService().create_intent(
            kind=NotificationKind.ORDER_IN_DELIVERY, recipient=order.customer,
            business_entity_type="orders.Order", business_entity_id=order.id,
            idempotency_key=f"order-in-delivery:customer:{order.id}",
            payload={"reference": order.reference, "text": "Votre commande est en route."},
            correlation_id=correlation_id,
        )

    @staticmethod
    def _create_completion_notification_intents(*, order, correlation_id):
        from apps.messaging.application.notifications import NotificationService
        from apps.messaging.domain.enums import NotificationKind

        NotificationService().create_intent(
            kind=NotificationKind.ORDER_DELIVERED, recipient=order.customer,
            business_entity_type="orders.Order", business_entity_id=order.id,
            idempotency_key=f"order-delivered:customer:{order.id}",
            payload={"reference": order.reference, "text": "Votre commande a été livrée."},
            correlation_id=correlation_id,
        )

    @staticmethod
    def allowed_actions(order_status: str) -> tuple[str, ...]:
        return ALLOWED_ACTIONS_BY_ORDER_STATUS.get(order_status, ())

    @staticmethod
    def _active_assignments():
        return (
            DriverAssignment.objects.filter(
                is_active=True, order__status__in=ACTIVE_DELIVERY_ORDER_STATUSES
            )
            .select_related("order", "driver", "driver__user")
            .prefetch_related("order__store_offers__store")
            .order_by("created_at", "id")
        )

    def _to_dto(self, assignment: DriverAssignment) -> DriverMissionDto:
        order = assignment.order
        winning_offer = next(
            (offer for offer in order.store_offers.all() if offer.status == StoreOfferStatus.ACCEPTED),
            None,
        )
        if winning_offer is None:
            # This is a durable consistency failure, not a reason to trust a
            # caller-provided store id or return an invented pickup point.
            logger.info("delivery_mission_not_found", extra={"reason": "winning_store_missing"})
            raise DeliveryMissionNotFound("Assigned order has no accepted pickup store.")
        store = winning_offer.store
        pickup = DriverMissionLocationDto(
            display_name=store.name,
            address_label=store.address_label,
            latitude=store.location.y if store.location else None,
            longitude=store.location.x if store.location else None,
        )
        destination = DriverMissionLocationDto(
            display_name=None,
            address_label=order.delivery_address_label,
            latitude=order.delivery_location.y if order.delivery_location else None,
            longitude=order.delivery_location.x if order.delivery_location else None,
        )
        return DriverMissionDto(
            assignment_id=str(assignment.id),
            order_id=str(order.id),
            order_reference=order.reference,
            order_status=order.status,
            pickup_store_id=str(store.id),
            pickup=pickup,
            destination=destination,
            coordinates_available=order.delivery_location is not None,
            delivery_instructions=order.delivery_instructions,
            allowed_actions=self.allowed_actions(order.status),
        )
