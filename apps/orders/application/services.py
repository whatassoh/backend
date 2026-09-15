from decimal import Decimal
from typing import Any

from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.application.services import record_audit_event
from apps.audit.domain.enums import AuditActorType, AuditEntityType, AuditEventType
from apps.catalog.domain.enums import ProductUnit
from apps.catalog.infrastructure.models import Product, ProductVariant
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderFailureCode,
    OrderItemResolutionStatus,
    OrderStatus,
)
from apps.orders.domain.events import (
    OrderCancelled,
    OrderConfirmed,
    OrderCreated,
    OrderFailed,
    OrderRetryRequested,
)
from apps.orders.domain.exceptions import (
    InvalidCancellation,
    InvalidOrderState,
    OrderNotRetryable,
)
from apps.orders.domain.rules import (
    ensure_failure_code_allowed,
    ensure_order_confirmable,
    ensure_transition_allowed,
    get_retry_target,
    validate_order_is_draft_mutable,
)
from apps.orders.domain.services import generate_order_reference
from apps.orders.infrastructure.models import Order, OrderItem
from common.events import publish

MAX_ORDER_REFERENCE_ATTEMPTS = 8


def _lock_order(order: Order) -> Order:
    return Order.objects.select_for_update().prefetch_related("items").get(pk=order.pk)


def _save_status(order: Order, update_fields: list[str]) -> Order:
    if "updated_at" not in update_fields:
        update_fields.append("updated_at")
    order.save(update_fields=update_fields)
    return order


def _transition_order(
    order: Order,
    target_status: OrderStatus | str,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    event_type: AuditEventType = AuditEventType.ORDER_STATUS_CHANGED,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> Order:
    ensure_transition_allowed(order.status, target_status)
    previous_state = order.status
    order.status = target_status
    _save_status(order, ["status"])
    _record_order_audit(
        order,
        event_type=event_type,
        actor=actor,
        actor_type=actor_type,
        previous_state=previous_state,
        new_state=target_status,
        metadata=metadata,
        correlation_id=correlation_id,
    )
    return order


def _record_order_audit(
    order: Order,
    *,
    event_type: AuditEventType,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    previous_state: str | None = None,
    new_state: str | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> None:
    record_audit_event(
        actor=actor,
        actor_type=actor_type,
        entity_type=AuditEntityType.ORDER,
        entity_id=order.id,
        event_type=event_type,
        previous_state=previous_state,
        new_state=new_state,
        metadata=metadata,
        correlation_id=correlation_id,
    )


def _publish_after_commit(event: object) -> None:
    transaction.on_commit(lambda: publish(event))


def _item_metadata(item: OrderItem) -> dict[str, Any]:
    return {
        "order_item_id": str(item.id),
        "requested_name": item.requested_name,
        "quantity": str(item.quantity),
        "unit": item.unit,
        "notes": item.notes,
        "product_id": str(item.product_id) if item.product_id else None,
        "product_variant_id": (
            str(item.product_variant_id) if item.product_variant_id else None
        ),
        "resolution_status": item.resolution_status,
    }


def create_order_draft(
    *,
    customer: Any,
    delivery_location: Point | None = None,
    delivery_address_label: str | None = None,
    delivery_instructions: str | None = None,
    currency: str = "XAF",
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    for _ in range(MAX_ORDER_REFERENCE_ATTEMPTS):
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    reference=generate_order_reference(),
                    customer=customer,
                    status=OrderStatus.DRAFT,
                    delivery_location=delivery_location,
                    delivery_address_label=delivery_address_label,
                    delivery_instructions=delivery_instructions,
                    currency=currency,
                )
                _record_order_audit(
                    order,
                    event_type=AuditEventType.ORDER_CREATED,
                    actor=actor,
                    actor_type=actor_type,
                    new_state=OrderStatus.DRAFT,
                    metadata={
                        "reference": order.reference,
                        "customer_id": str(order.customer_id),
                        "currency": order.currency,
                    },
                    correlation_id=correlation_id,
                )
                _publish_after_commit(
                    OrderCreated(
                        order_id=order.id,
                        customer_id=order.customer_id,
                        correlation_id=correlation_id,
                    )
                )
                return order
        except IntegrityError:
            continue

    raise IntegrityError("Could not generate a unique order reference.")


def add_order_item(
    *,
    order: Order,
    requested_name: str,
    quantity: Decimal | int | str,
    unit: ProductUnit | str,
    notes: str | None = None,
    product: Product | None = None,
    product_variant: ProductVariant | None = None,
    resolution_status: OrderItemResolutionStatus | str = (
        OrderItemResolutionStatus.UNMATCHED
    ),
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> OrderItem:
    with transaction.atomic():
        locked_order = _lock_order(order)
        validate_order_is_draft_mutable(locked_order.status)

        item = OrderItem(
            order=locked_order,
            requested_name=requested_name,
            quantity=quantity,
            unit=unit,
            notes=notes,
            product=product,
            product_variant=product_variant,
            resolution_status=resolution_status,
        )
        item.full_clean()
        item.save()
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_ITEM_ADDED,
            actor=actor,
            actor_type=actor_type,
            metadata=_item_metadata(item),
            correlation_id=correlation_id,
        )

        return item


def update_order_item(
    order_item: OrderItem,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
    **fields: Any,
) -> OrderItem:
    with transaction.atomic():
        item = (
            OrderItem.objects.select_for_update()
            .select_related("order", "product", "product_variant")
            .get(pk=order_item.pk)
        )
        validate_order_is_draft_mutable(item.order.status)

        for field, value in fields.items():
            setattr(item, field, value)

        item.full_clean()
        item.save()
        _record_order_audit(
            item.order,
            event_type=AuditEventType.ORDER_ITEM_UPDATED,
            actor=actor,
            actor_type=actor_type,
            metadata={
                "updated_fields": sorted(fields.keys()),
                **_item_metadata(item),
            },
            correlation_id=correlation_id,
        )

        return item


def remove_order_item(
    order_item: OrderItem,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> int:
    with transaction.atomic():
        item = (
            OrderItem.objects.select_for_update()
            .select_related("order")
            .get(pk=order_item.pk)
        )
        validate_order_is_draft_mutable(item.order.status)

        metadata = _item_metadata(item)
        deleted_count, _ = OrderItem.objects.filter(id=item.id).delete()
        _record_order_audit(
            item.order,
            event_type=AuditEventType.ORDER_ITEM_REMOVED,
            actor=actor,
            actor_type=actor_type,
            metadata=metadata,
            correlation_id=correlation_id,
        )

        return deleted_count


def set_delivery_location(
    *,
    order: Order,
    point: Point,
    address_label: str | None = None,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        validate_order_is_draft_mutable(locked_order.status)
        if point.srid is None:
            point.srid = 4326
        if point.srid != 4326:
            raise ValidationError("Delivery location must use SRID 4326.")

        locked_order.delivery_location = point
        update_fields = ["delivery_location", "updated_at"]

        if address_label is not None:
            locked_order.delivery_address_label = address_label
            update_fields.append("delivery_address_label")

        locked_order.save(update_fields=update_fields)
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_DELIVERY_LOCATION_UPDATED,
            actor=actor,
            actor_type=actor_type,
            metadata={
                "longitude": str(point.x),
                "latitude": str(point.y),
                "srid": point.srid,
                "address_label": locked_order.delivery_address_label,
            },
            correlation_id=correlation_id,
        )

        return locked_order


def set_delivery_instructions(
    order: Order,
    instructions: str | None,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        validate_order_is_draft_mutable(locked_order.status)

        locked_order.delivery_instructions = instructions
        locked_order.save(update_fields=["delivery_instructions", "updated_at"])
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_DELIVERY_INSTRUCTIONS_UPDATED,
            actor=actor,
            actor_type=actor_type,
            metadata={"instructions": instructions},
            correlation_id=correlation_id,
        )

        return locked_order


def mark_awaiting_location(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        if locked_order.status == OrderStatus.AWAITING_LOCATION:
            return locked_order
        return _transition_order(
            locked_order,
            OrderStatus.AWAITING_LOCATION,
            actor=actor,
            actor_type=actor_type,
            event_type=AuditEventType.ORDER_STATUS_CHANGED,
            correlation_id=correlation_id,
        )


def mark_draft(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        if locked_order.status == OrderStatus.DRAFT:
            return locked_order
        return _transition_order(
            locked_order,
            OrderStatus.DRAFT,
            actor=actor,
            actor_type=actor_type,
            event_type=AuditEventType.ORDER_STATUS_CHANGED,
            correlation_id=correlation_id,
        )


def confirm_order(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        if locked_order.status == OrderStatus.CONFIRMED:
            return locked_order

        ensure_order_confirmable(locked_order)
        ensure_transition_allowed(locked_order.status, OrderStatus.CONFIRMED)

        previous_state = locked_order.status
        locked_order.status = OrderStatus.CONFIRMED
        update_fields = ["status"]
        if locked_order.confirmed_at is None:
            locked_order.confirmed_at = timezone.now()
            update_fields.append("confirmed_at")

        _save_status(locked_order, update_fields)
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_CONFIRMED,
            actor=actor,
            actor_type=actor_type,
            previous_state=previous_state,
            new_state=OrderStatus.CONFIRMED,
            correlation_id=correlation_id,
        )
        _publish_after_commit(
            OrderConfirmed(
                order_id=locked_order.id,
                customer_id=locked_order.customer_id,
                correlation_id=correlation_id,
            )
        )
        return locked_order


def cancel_order(
    order: Order,
    cancellation_code: OrderCancellationCode | str,
    *,
    reason: str | None = None,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    if not cancellation_code:
        raise InvalidCancellation("Cancellation code is required.")

    with transaction.atomic():
        locked_order = _lock_order(order)
        ensure_transition_allowed(locked_order.status, OrderStatus.CANCELLED)

        previous_state = locked_order.status
        locked_order.status = OrderStatus.CANCELLED
        locked_order.cancelled_at = timezone.now()
        locked_order.cancellation_code = cancellation_code
        locked_order.cancellation_reason = reason
        _save_status(
            locked_order,
            [
                "status",
                "cancelled_at",
                "cancellation_code",
                "cancellation_reason",
            ],
        )
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_CANCELLED,
            actor=actor,
            actor_type=actor_type,
            previous_state=previous_state,
            new_state=OrderStatus.CANCELLED,
            metadata={
                "cancellation_code": cancellation_code,
                "reason": reason,
            },
            correlation_id=correlation_id,
        )
        _publish_after_commit(
            OrderCancelled(
                order_id=locked_order.id,
                customer_id=locked_order.customer_id,
                cancellation_code=str(cancellation_code),
                correlation_id=correlation_id,
            )
        )
        return locked_order


def fail_order(
    order: Order,
    failure_code: OrderFailureCode | str,
    *,
    reason: str | None = None,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    if not failure_code:
        raise InvalidOrderState("Failure code is required.")

    with transaction.atomic():
        locked_order = _lock_order(order)
        ensure_transition_allowed(locked_order.status, OrderStatus.FAILED)
        ensure_failure_code_allowed(failure_code, locked_order.status)

        source_status = locked_order.status
        locked_order.status = OrderStatus.FAILED
        locked_order.failed_from_status = source_status
        locked_order.failure_code = failure_code
        locked_order.failure_reason = reason
        locked_order.failed_at = timezone.now()
        _save_status(
            locked_order,
            [
                "status",
                "failed_from_status",
                "failure_code",
                "failure_reason",
                "failed_at",
            ],
        )
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_FAILED,
            actor=actor,
            actor_type=actor_type,
            previous_state=source_status,
            new_state=OrderStatus.FAILED,
            metadata={
                "failure_code": failure_code,
                "failure_reason": reason,
                "failed_from_status": source_status,
            },
            correlation_id=correlation_id,
        )
        _publish_after_commit(
            OrderFailed(
                order_id=locked_order.id,
                failure_code=str(failure_code),
                failed_from_status=str(source_status),
                correlation_id=correlation_id,
            )
        )
        return locked_order


def retry_failed_order(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        if locked_order.status != OrderStatus.FAILED:
            raise OrderNotRetryable("Only failed orders can be retried.")

        target_status = get_retry_target(
            locked_order.failure_code,
            locked_order.failed_from_status,
        )
        retry_metadata = {
            "failure_code": locked_order.failure_code,
            "failure_reason": locked_order.failure_reason,
            "failed_from_status": locked_order.failed_from_status,
            "failed_at": (
                locked_order.failed_at.isoformat() if locked_order.failed_at else None
            ),
        }
        previous_failure_code = locked_order.failure_code

        locked_order.status = target_status
        locked_order.failed_at = None
        locked_order.failure_code = None
        locked_order.failure_reason = None
        locked_order.failed_from_status = None
        _save_status(
            locked_order,
            [
                "status",
                "failed_at",
                "failure_code",
                "failure_reason",
                "failed_from_status",
            ],
        )
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_RETRY_REQUESTED,
            actor=actor,
            actor_type=actor_type,
            previous_state=OrderStatus.FAILED,
            new_state=target_status,
            metadata=retry_metadata,
            correlation_id=correlation_id,
        )
        _publish_after_commit(
            OrderRetryRequested(
                order_id=locked_order.id,
                failure_code=str(previous_failure_code),
                retry_target_status=str(target_status),
                correlation_id=correlation_id,
            )
        )
        return locked_order


def start_store_matching(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.MATCHING_STORES,
        idempotent_from={OrderStatus.MATCHING_STORES},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_store_offered(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.STORE_OFFERED,
        idempotent_from={OrderStatus.STORE_OFFERED},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_store_accepted(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.STORE_ACCEPTED,
        idempotent_from={OrderStatus.STORE_ACCEPTED},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_preparing(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.PREPARING,
        idempotent_from={OrderStatus.PREPARING},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_ready_for_pickup(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.READY_FOR_PICKUP,
        idempotent_from={OrderStatus.READY_FOR_PICKUP},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def start_driver_matching(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.MATCHING_DRIVERS,
        idempotent_from={OrderStatus.MATCHING_DRIVERS},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def assign_driver(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.DRIVER_ASSIGNED,
        idempotent_from={OrderStatus.DRIVER_ASSIGNED},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_picked_up(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.PICKED_UP,
        idempotent_from={OrderStatus.PICKED_UP},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_in_delivery(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    return _semantic_transition(
        order,
        OrderStatus.IN_DELIVERY,
        idempotent_from={OrderStatus.IN_DELIVERY},
        actor=actor,
        actor_type=actor_type,
        correlation_id=correlation_id,
    )


def mark_delivered(
    order: Order,
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        if locked_order.status == OrderStatus.DELIVERED:
            return locked_order

        ensure_transition_allowed(locked_order.status, OrderStatus.DELIVERED)
        previous_state = locked_order.status
        locked_order.status = OrderStatus.DELIVERED
        update_fields = ["status"]
        if locked_order.completed_at is None:
            locked_order.completed_at = timezone.now()
            update_fields.append("completed_at")

        _save_status(locked_order, update_fields)
        _record_order_audit(
            locked_order,
            event_type=AuditEventType.ORDER_STATUS_CHANGED,
            actor=actor,
            actor_type=actor_type,
            previous_state=previous_state,
            new_state=OrderStatus.DELIVERED,
            correlation_id=correlation_id,
        )
        return locked_order


def _semantic_transition(
    order: Order,
    target_status: OrderStatus,
    *,
    idempotent_from: set[OrderStatus],
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    correlation_id: str | None = None,
) -> Order:
    with transaction.atomic():
        locked_order = _lock_order(order)
        if locked_order.status in idempotent_from:
            return locked_order
        return _transition_order(
            locked_order,
            target_status,
            actor=actor,
            actor_type=actor_type,
            event_type=AuditEventType.ORDER_STATUS_CHANGED,
            correlation_id=correlation_id,
        )
