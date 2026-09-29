"""Channel-neutral review and confirmation readiness for draft orders."""

import hashlib
import json
from dataclasses import dataclass

from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.infrastructure.models import Order, OrderItem


@dataclass(frozen=True, kw_only=True)
class OrderReviewItem:
    order_item_id: str
    requested_name: str
    product_name: str | None
    variant_name: str | None
    quantity: str
    unit: str
    resolution_status: str
    display_label: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OrderConfirmationReadiness:
    ready: bool
    blocking_issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OrderReview:
    order_id: str
    reference: str
    items: tuple[OrderReviewItem, ...]
    delivery_address_label: str | None
    has_delivery_location: bool
    delivery_instructions: str | None
    readiness: OrderConfirmationReadiness
    available_actions: tuple[str, ...]
    fingerprint: str


class OrderReviewService:
    """Always rebuilds a review from persisted Order and OrderItem rows."""

    def build_review(
        self, order: Order, *, has_pending_clarification: bool = False
    ) -> OrderReview:
        current = Order.objects.select_related("customer").get(pk=order.pk)
        rows = list(
            OrderItem.objects.filter(order=current)
            .select_related("product", "product_variant")
            .order_by("created_at", "id")
        )
        items = tuple(self._item(row) for row in rows)
        blockers = []
        warnings = []
        if current.status not in {OrderStatus.DRAFT, OrderStatus.AWAITING_LOCATION}:
            blockers.append("order_not_editable")
        if not items:
            blockers.append("no_items")
        if current.delivery_location is None:
            blockers.append("delivery_location_missing")
        if has_pending_clarification:
            blockers.append("clarification_pending")
        if any(
            item.resolution_status == OrderItemResolutionStatus.AMBIGUOUS
            for item in items
        ):
            blockers.append("ambiguous_item")
        if any(
            item.resolution_status == OrderItemResolutionStatus.UNMATCHED
            for item in items
        ):
            blockers.append("unmatched_items")
        readiness = OrderConfirmationReadiness(
            ready=not blockers,
            blocking_issues=tuple(blockers),
            warnings=tuple(warnings),
        )
        fingerprint = self._fingerprint(current, items)
        return OrderReview(
            order_id=str(current.id),
            reference=current.reference,
            items=items,
            delivery_address_label=current.delivery_address_label,
            has_delivery_location=current.delivery_location is not None,
            delivery_instructions=current.delivery_instructions,
            readiness=readiness,
            available_actions=("order.modify", "order.cancel")
            + (("order.confirm",) if readiness.ready else ()),
            fingerprint=fingerprint,
        )

    @staticmethod
    def _item(item: OrderItem) -> OrderReviewItem:
        warnings = (
            ("product_not_resolved",)
            if item.resolution_status == OrderItemResolutionStatus.UNMATCHED
            else ()
        )
        identity = item.product.name if item.product else item.requested_name
        if item.product_variant:
            identity = f"{identity} — {item.product_variant.name}"
        return OrderReviewItem(
            order_item_id=str(item.id),
            requested_name=item.requested_name,
            product_name=item.product.name if item.product else None,
            variant_name=item.product_variant.name if item.product_variant else None,
            quantity=str(item.quantity),
            unit=item.unit,
            resolution_status=item.resolution_status,
            display_label=f"{item.quantity} × {identity}",
            warnings=warnings,
        )

    @staticmethod
    def _fingerprint(order: Order, items: tuple[OrderReviewItem, ...]) -> str:
        payload = {
            "order_id": str(order.id),
            "status": order.status,
            "location": order.delivery_location.ewkt
            if order.delivery_location
            else None,
            "address": order.delivery_address_label,
            "instructions": order.delivery_instructions,
            "items": [
                (
                    item.order_item_id,
                    item.quantity,
                    item.unit,
                    item.resolution_status,
                    item.product_name,
                    item.variant_name,
                    item.requested_name,
                )
                for item in items
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
