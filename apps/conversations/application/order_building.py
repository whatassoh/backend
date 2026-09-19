"""Deterministic order-item targeting and mutations for conversational flows."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from django.db import transaction

from apps.catalog.domain.enums import ProductStatus, ProductVariantStatus
from apps.catalog.domain.grounding import CatalogResolution
from apps.catalog.infrastructure.models import Product, ProductVariant
from apps.orders.application.services import (
    add_order_item,
    remove_order_item,
    replace_order_item,
    update_order_item,
)
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.orders.infrastructure.models import Order, OrderItem


class ItemTargetStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, kw_only=True)
class ItemReference:
    """Backend-created reference; callers must never trust an LLM UUID."""

    order_item_id: str | None = None
    requested_name: str | None = None
    display_index: int | None = None


@dataclass(frozen=True, kw_only=True)
class ItemTargetDecision:
    status: ItemTargetStatus
    item: OrderItem | None = None
    candidates: tuple[OrderItem, ...] = ()


class OrderBuildingService:
    """Use this service instead of mutating OrderItem from an orchestrator."""

    @staticmethod
    def summary(order: Order) -> tuple[dict[str, str | None], ...]:
        items = OrderItem.objects.filter(order=order).order_by("created_at", "id")
        return tuple(
            {
                "order_item_id": str(item.id),
                "requested_name": item.requested_name,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "resolution_status": item.resolution_status,
                "product_id": str(item.product_id) if item.product_id else None,
                "variant_id": str(item.product_variant_id)
                if item.product_variant_id
                else None,
            }
            for item in items
        )

    @staticmethod
    def target(order: Order, reference: ItemReference) -> ItemTargetDecision:
        items = list(
            OrderItem.objects.filter(order=order)
            .select_related("product")
            .order_by("created_at", "id")
        )
        if reference.order_item_id:
            try:
                item_id = UUID(reference.order_item_id)
            except (TypeError, ValueError, AttributeError):
                return ItemTargetDecision(status=ItemTargetStatus.NOT_FOUND)
            item = next((item for item in items if item.id == item_id), None)
            return ItemTargetDecision(
                status=ItemTargetStatus.FOUND if item else ItemTargetStatus.NOT_FOUND,
                item=item,
            )
        if reference.display_index is not None:
            index = reference.display_index - 1
            item = items[index] if 0 <= index < len(items) else None
            return ItemTargetDecision(
                status=ItemTargetStatus.FOUND if item else ItemTargetStatus.NOT_FOUND,
                item=item,
            )
        needle = (reference.requested_name or "").strip().casefold()
        if not needle:
            if len(items) == 1:
                return ItemTargetDecision(status=ItemTargetStatus.FOUND, item=items[0])
            return ItemTargetDecision(
                status=ItemTargetStatus.AMBIGUOUS, candidates=tuple(items)
            )
        matches = [
            item
            for item in items
            if item.requested_name.casefold() == needle
            or (item.product and item.product.name.casefold() == needle)
        ]
        if len(matches) == 1:
            return ItemTargetDecision(status=ItemTargetStatus.FOUND, item=matches[0])
        return ItemTargetDecision(
            status=ItemTargetStatus.NOT_FOUND
            if not matches
            else ItemTargetStatus.AMBIGUOUS,
            candidates=tuple(matches),
        )

    @staticmethod
    def _catalog_values(resolution: CatalogResolution):
        if resolution.status == OrderItemResolutionStatus.UNMATCHED:
            return None, None, OrderItemResolutionStatus.UNMATCHED
        product = Product.objects.filter(
            pk=resolution.product_id, status=ProductStatus.ACTIVE
        ).first()
        variant = (
            ProductVariant.objects.filter(
                pk=resolution.variant_id,
                product=product,
                status=ProductVariantStatus.ACTIVE,
            ).first()
            if resolution.variant_id and product
            else None
        )
        if product is None or (resolution.variant_id and variant is None):
            return None, None, OrderItemResolutionStatus.UNMATCHED
        return product, variant, OrderItemResolutionStatus.MATCHED

    @transaction.atomic
    def add(self, order, extracted_item, resolution, *, actor=None, correlation_id=None):
        """One utterance creates one line; duplicates are intentionally retained."""
        product, variant, status = self._catalog_values(resolution)
        return add_order_item(
            order=order,
            requested_name=extracted_item.requested_name,
            quantity=extracted_item.quantity,
            unit=extracted_item.unit,
            notes=extracted_item.notes,
            product=product,
            product_variant=variant,
            resolution_status=status,
            actor=actor,
            correlation_id=correlation_id,
        )

    def update(self, item, *, quantity=None, unit=None, actor=None, correlation_id=None):
        fields = {}
        if quantity is not None:
            fields["quantity"] = quantity
        if unit is not None:
            fields["unit"] = unit
        if not fields:
            return item
        return update_order_item(
            item, actor=actor, correlation_id=correlation_id, **fields
        )

    def remove(self, item, *, actor=None, correlation_id=None):
        return remove_order_item(item, actor=actor, correlation_id=correlation_id)

    @transaction.atomic
    def replace(self, item, extracted_item, resolution, *, actor=None, correlation_id=None):
        product, variant, status = self._catalog_values(resolution)
        return replace_order_item(
            item,
            requested_name=extracted_item.requested_name,
            product=product,
            product_variant=variant,
            resolution_status=status,
            actor=actor,
            correlation_id=correlation_id,
        )
