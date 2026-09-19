"""Catalog coverage evaluation; deliberately not stock reservation or ranking."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from apps.catalog.domain.enums import ProductAvailability
from apps.catalog.infrastructure.models import StoreProduct
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.orders.infrastructure.models import Order, OrderItem


class FulfillmentReason(StrEnum):
    PRODUCT_AVAILABLE = "PRODUCT_AVAILABLE"
    PRODUCT_NOT_CARRIED = "PRODUCT_NOT_CARRIED"
    VARIANT_NOT_CARRIED = "VARIANT_NOT_CARRIED"
    STORE_PRODUCT_UNAVAILABLE = "STORE_PRODUCT_UNAVAILABLE"
    ORDER_ITEM_UNMATCHED = "ORDER_ITEM_UNMATCHED"
    ORDER_ITEM_AMBIGUOUS = "ORDER_ITEM_AMBIGUOUS"
    INVALID_ITEM = "INVALID_ITEM"


@dataclass(frozen=True, kw_only=True)
class ItemFulfillmentResult:
    order_item_id: str
    resolution_status: str
    product_id: str | None
    variant_id: str | None
    covered: bool
    store_product_id: str | None
    availability_status: str | None
    reason: FulfillmentReason
    quantity_certainty: str = "UNKNOWN"
    price: Decimal | None = None


@dataclass(frozen=True, kw_only=True)
class StoreFulfillmentResult:
    store_id: str
    order_id: str
    item_results: tuple[ItemFulfillmentResult, ...]
    total_items: int
    covered_items: int
    unresolved_items: int
    coverage_ratio: Decimal
    fully_covered: bool
    blocking_reasons: tuple[FulfillmentReason, ...]
    metadata: dict[str, str]


class StoreFulfillmentService:
    """A deterministic catalog declaration evaluator with batched candidate support."""

    def evaluate_store(self, order: Order, store) -> StoreFulfillmentResult:
        items = list(OrderItem.objects.filter(order=order).order_by("created_at", "id"))
        products = self._products_for([store.id], items)
        return self._evaluate(order, store.id, items, products)

    def evaluate_candidates(self, order: Order, stores: Iterable) -> tuple[StoreFulfillmentResult, ...]:
        stores = tuple(stores)
        items = list(OrderItem.objects.filter(order=order).order_by("created_at", "id"))
        products = self._products_for([store.id for store in stores], items)
        return tuple(
            self._evaluate(order, store.id, items, products) for store in stores
        )

    @staticmethod
    def _products_for(store_ids, items):
        product_ids = {item.product_id for item in items if item.product_id}
        rows = StoreProduct.objects.filter(store_id__in=store_ids, product_id__in=product_ids).select_related("product_variant")
        return {
            (row.store_id, row.product_id, row.product_variant_id): row for row in rows
        }

    def _evaluate(self, order, store_id, items, products):
        results = tuple(self._evaluate_item(store_id, item, products) for item in items)
        covered = sum(result.covered for result in results)
        blockers = tuple(
            result.reason for result in results if not result.covered
        )
        total = len(results)
        return StoreFulfillmentResult(
            store_id=str(store_id),
            order_id=str(order.id),
            item_results=results,
            total_items=total,
            covered_items=covered,
            unresolved_items=sum(
                result.reason
                in {FulfillmentReason.ORDER_ITEM_UNMATCHED, FulfillmentReason.ORDER_ITEM_AMBIGUOUS}
                for result in results
            ),
            coverage_ratio=(Decimal(covered) / Decimal(total)) if total else Decimal("0"),
            fully_covered=bool(total) and covered == total,
            blocking_reasons=blockers,
            metadata={
                "coverage_semantics": "declared_catalog_availability_not_stock",
                "unit_conversion": "not_supported",
            },
        )

    @staticmethod
    def _evaluate_item(store_id, item, products):
        common = {
            "order_item_id": str(item.id),
            "resolution_status": item.resolution_status,
            "product_id": str(item.product_id) if item.product_id else None,
            "variant_id": str(item.product_variant_id) if item.product_variant_id else None,
        }
        if item.resolution_status == OrderItemResolutionStatus.UNMATCHED:
            return ItemFulfillmentResult(
                **common, covered=False, store_product_id=None,
                availability_status=None, reason=FulfillmentReason.ORDER_ITEM_UNMATCHED,
            )
        if item.resolution_status == OrderItemResolutionStatus.AMBIGUOUS:
            return ItemFulfillmentResult(
                **common, covered=False, store_product_id=None,
                availability_status=None, reason=FulfillmentReason.ORDER_ITEM_AMBIGUOUS,
            )
        if not item.product_id:
            return ItemFulfillmentResult(
                **common, covered=False, store_product_id=None,
                availability_status=None, reason=FulfillmentReason.INVALID_ITEM,
            )
        row = products.get((store_id, item.product_id, item.product_variant_id))
        if row is None:
            return ItemFulfillmentResult(
                **common, covered=False, store_product_id=None, availability_status=None,
                reason=(FulfillmentReason.VARIANT_NOT_CARRIED if item.product_variant_id else FulfillmentReason.PRODUCT_NOT_CARRIED),
            )
        if row.availability_status != ProductAvailability.AVAILABLE:
            return ItemFulfillmentResult(
                **common, covered=False, store_product_id=str(row.id),
                availability_status=row.availability_status,
                reason=FulfillmentReason.STORE_PRODUCT_UNAVAILABLE, price=row.price,
            )
        return ItemFulfillmentResult(
            **common, covered=True, store_product_id=str(row.id),
            availability_status=row.availability_status,
            reason=FulfillmentReason.PRODUCT_AVAILABLE,
            quantity_certainty="DECLARED_AVAILABLE", price=row.price,
        )
