"""Deterministic order-item targeting and mutations for conversational flows."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from django.db import transaction

from apps.catalog.domain.enums import ProductStatus, ProductVariantStatus
from apps.catalog.domain.grounding import CatalogResolution
from apps.catalog.domain.rules import normalize_catalog_text
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
            .select_related("product", "product_variant")
            .prefetch_related("product__aliases")
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
        needle = _normalize_item_reference(reference.requested_name or "")
        if not needle:
            if len(items) == 1:
                return ItemTargetDecision(status=ItemTargetStatus.FOUND, item=items[0])
            return ItemTargetDecision(
                status=ItemTargetStatus.AMBIGUOUS, candidates=tuple(items)
            )
        ranked = []
        for item in items:
            score = _item_match_score(item, needle)
            if score is not None:
                ranked.append((score, item))
        if not ranked:
            return ItemTargetDecision(status=ItemTargetStatus.NOT_FOUND)
        best = min(score for score, _ in ranked)
        matches = tuple(item for score, item in ranked if score == best)
        return ItemTargetDecision(
            status=ItemTargetStatus.FOUND
            if len(matches) == 1
            else ItemTargetStatus.AMBIGUOUS,
            item=matches[0] if len(matches) == 1 else None,
            candidates=matches if len(matches) > 1 else (),
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
    def add(
        self, order, extracted_item, resolution, *, actor=None, correlation_id=None
    ):
        """Add an item, folding an identical catalog line into the existing line.

        A basket is easier to review and edit when a repeated ``Mayor 1 L`` is
        one line with an accumulated quantity.  The lookup is performed while
        rows are locked, so two concurrent inbound deliveries cannot create
        duplicate equivalent lines.
        """
        product, variant, status = self._catalog_values(resolution)
        if status == OrderItemResolutionStatus.MATCHED:
            existing = (
                OrderItem.objects.select_for_update()
                .filter(
                    order=order,
                    product=product,
                    product_variant=variant,
                    unit=extracted_item.unit,
                    notes=extracted_item.notes,
                    resolution_status=OrderItemResolutionStatus.MATCHED,
                )
                .order_by("created_at", "id")
                .first()
            )
            if existing is not None:
                return update_order_item(
                    existing,
                    quantity=(
                        Decimal(existing.quantity) + Decimal(extracted_item.quantity)
                    ),
                    actor=actor,
                    correlation_id=correlation_id,
                )
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

    def update(
        self, item, *, quantity=None, unit=None, actor=None, correlation_id=None
    ):
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
    def replace(
        self, item, extracted_item, resolution, *, actor=None, correlation_id=None
    ):
        product, variant, status = self._catalog_values(resolution)
        quantity = extracted_item.quantity or item.quantity
        unit = extracted_item.unit or item.unit
        if status == OrderItemResolutionStatus.MATCHED:
            existing = (
                OrderItem.objects.select_for_update()
                .filter(
                    order=item.order,
                    product=product,
                    product_variant=variant,
                    unit=unit,
                    notes=extracted_item.notes,
                    resolution_status=OrderItemResolutionStatus.MATCHED,
                )
                .exclude(pk=item.pk)
                .order_by("created_at", "id")
                .first()
            )
            if existing is not None:
                merged = update_order_item(
                    existing,
                    quantity=Decimal(existing.quantity) + Decimal(quantity),
                    actor=actor,
                    correlation_id=correlation_id,
                )
                remove_order_item(
                    item, actor=actor, correlation_id=correlation_id
                )
                return merged
        replaced = replace_order_item(
            item,
            requested_name=extracted_item.requested_name,
            product=product,
            product_variant=variant,
            resolution_status=status,
            actor=actor,
            correlation_id=correlation_id,
        )
        return update_order_item(
            replaced,
            quantity=quantity,
            unit=unit,
            notes=extracted_item.notes,
            actor=actor,
            correlation_id=correlation_id,
        )


def _normalize_item_reference(value: str) -> str:
    """Catalog-consistent Unicode/punctuation/accent normalization."""
    return normalize_catalog_text(value)


def _singular_equivalent(value: str) -> str:
    """Intentionally small French plural equivalence, not linguistic stemming."""
    if len(value) > 3 and value.endswith("aux"):
        return f"{value[:-3]}al"
    if len(value) > 3 and value.endswith(("s", "x")):
        return value[:-1]
    return value


def _same_or_simple_plural(left: str, right: str) -> bool:
    return left == right or _singular_equivalent(left) == _singular_equivalent(right)


def _item_match_score(item: OrderItem, needle: str) -> int | None:
    """Stable precedence: requested name, product, alias, variant; then plural form."""
    names = [(0, item.requested_name)]
    if item.product:
        names.append((1, item.product.name))
        names.extend((2, alias.alias) for alias in item.product.aliases.all())
    if item.product_variant:
        names.append((3, item.product_variant.name))
    exact = [
        score for score, name in names if _normalize_item_reference(name) == needle
    ]
    if exact:
        return min(exact)
    plural = [
        score + 10
        for score, name in names
        if _same_or_simple_plural(_normalize_item_reference(name), needle)
    ]
    return min(plural) if plural else None
