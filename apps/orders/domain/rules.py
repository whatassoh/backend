from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.orders.domain.enums import (
    OrderFailureCode,
    OrderItemResolutionStatus,
    OrderStatus,
)
from apps.orders.domain.exceptions import (
    InvalidFailureCode,
    InvalidOrderTransition,
    OrderNotConfirmable,
    OrderNotEditable,
    OrderNotRetryable,
)

DRAFT_MUTABLE_STATUSES = {
    OrderStatus.DRAFT,
    OrderStatus.AWAITING_LOCATION,
}

ALLOWED_TRANSITIONS = {
    OrderStatus.DRAFT: {
        OrderStatus.AWAITING_LOCATION,
        OrderStatus.CONFIRMED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.AWAITING_LOCATION: {
        OrderStatus.DRAFT,
        OrderStatus.CONFIRMED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.CONFIRMED: {
        OrderStatus.MATCHING_STORES,
        OrderStatus.CANCELLED,
    },
    OrderStatus.MATCHING_STORES: {
        OrderStatus.STORE_OFFERED,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.STORE_OFFERED: {
        OrderStatus.STORE_ACCEPTED,
        OrderStatus.MATCHING_STORES,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.STORE_ACCEPTED: {
        OrderStatus.PREPARING,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PREPARING: {
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.READY_FOR_PICKUP: {
        OrderStatus.MATCHING_DRIVERS,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.MATCHING_DRIVERS: {
        OrderStatus.DRIVER_ASSIGNED,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.DRIVER_ASSIGNED: {
        OrderStatus.PICKED_UP,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PICKED_UP: {
        OrderStatus.IN_DELIVERY,
        OrderStatus.FAILED,
    },
    OrderStatus.IN_DELIVERY: {
        OrderStatus.DELIVERED,
        OrderStatus.FAILED,
    },
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED: set(),
}

FAILURE_CODE_ALLOWED_SOURCES = {
    OrderFailureCode.NO_STORE_AVAILABLE: {
        OrderStatus.MATCHING_STORES,
        OrderStatus.STORE_OFFERED,
    },
    OrderFailureCode.NO_DRIVER_AVAILABLE: {
        OrderStatus.MATCHING_DRIVERS,
    },
    OrderFailureCode.STORE_FULFILLMENT_FAILED: {
        OrderStatus.STORE_ACCEPTED,
        OrderStatus.PREPARING,
        OrderStatus.READY_FOR_PICKUP,
    },
    OrderFailureCode.DELIVERY_FAILED: {
        OrderStatus.DRIVER_ASSIGNED,
        OrderStatus.PICKED_UP,
        OrderStatus.IN_DELIVERY,
    },
    OrderFailureCode.OTHER: {
        OrderStatus.MATCHING_STORES,
        OrderStatus.STORE_OFFERED,
        OrderStatus.STORE_ACCEPTED,
        OrderStatus.PREPARING,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.MATCHING_DRIVERS,
        OrderStatus.DRIVER_ASSIGNED,
        OrderStatus.PICKED_UP,
        OrderStatus.IN_DELIVERY,
    },
}

RETRY_TARGET_BY_FAILURE_CODE = {
    OrderFailureCode.NO_STORE_AVAILABLE: OrderStatus.MATCHING_STORES,
    OrderFailureCode.NO_DRIVER_AVAILABLE: OrderStatus.MATCHING_DRIVERS,
}


def validate_positive_quantity(quantity: Decimal | object) -> None:
    if quantity is None or quantity <= 0:
        raise ValidationError("Quantity must be strictly positive.")


def validate_order_item_product_variant(
    product_id: object | None,
    variant: object | None,
) -> None:
    if variant is None:
        return
    if product_id is None:
        raise ValidationError("Product is required when product variant is set.")
    if variant.product_id != product_id:
        raise ValidationError("Product variant must belong to the selected product.")


def validate_order_item_resolution(
    resolution_status: OrderItemResolutionStatus | str,
    product_id: object | None,
) -> None:
    if resolution_status == OrderItemResolutionStatus.MATCHED and product_id is None:
        raise ValidationError("Matched order items must reference a product.")
    if (
        resolution_status == OrderItemResolutionStatus.UNMATCHED
        and product_id is not None
    ):
        raise ValidationError("Unmatched order items cannot reference a product.")


def validate_order_is_draft_mutable(status: OrderStatus | str) -> None:
    if status not in DRAFT_MUTABLE_STATUSES:
        raise OrderNotEditable("Only draft orders can be modified by draft services.")


def ensure_transition_allowed(
    current_status: OrderStatus | str,
    target_status: OrderStatus | str,
) -> None:
    if target_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
        raise InvalidOrderTransition(
            f"Transition from {current_status} to {target_status} is not allowed."
        )


def ensure_order_confirmable(order: object) -> None:
    if order.status not in {OrderStatus.DRAFT, OrderStatus.AWAITING_LOCATION}:
        raise OrderNotConfirmable("Only draft orders can be confirmed.")
    if order.delivery_location is None:
        raise OrderNotConfirmable("Delivery location is required to confirm an order.")

    items = list(order.items.all())
    if not items:
        raise OrderNotConfirmable("At least one order item is required.")

    for item in items:
        item.full_clean()
        if item.resolution_status != OrderItemResolutionStatus.MATCHED or not item.product_id:
            raise OrderNotConfirmable("All order items must match a catalog product.")


def ensure_failure_code_allowed(
    failure_code: OrderFailureCode | str,
    source_status: OrderStatus | str,
) -> None:
    allowed_sources = FAILURE_CODE_ALLOWED_SOURCES.get(failure_code)
    if allowed_sources is None:
        raise InvalidFailureCode(f"Unknown failure code: {failure_code}.")
    if source_status not in allowed_sources:
        raise InvalidFailureCode(
            f"Failure code {failure_code} is not valid from {source_status}."
        )


def get_retry_target(
    failure_code: OrderFailureCode | str | None,
    failed_from_status: OrderStatus | str | None,
) -> OrderStatus:
    target = RETRY_TARGET_BY_FAILURE_CODE.get(failure_code)
    if target is None:
        raise OrderNotRetryable(f"Failure code {failure_code} is not retryable.")

    allowed_sources = FAILURE_CODE_ALLOWED_SOURCES[failure_code]
    if failed_from_status not in allowed_sources:
        raise OrderNotRetryable(
            f"Failure code {failure_code} cannot be retried from {failed_from_status}."
        )

    return target
