"""The sole seller-facing catalog mutation facade for chat and dashboard."""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from django.db import IntegrityError, transaction

from apps.catalog.application.permissions import can_manage_store_catalog
from apps.catalog.application.selectors import get_store_products
from apps.catalog.application.services import (
    add_product_to_store,
    set_product_availability,
    set_store_product_price,
)
from apps.catalog.domain.enums import ProductAvailability, ProductStatus, ProductVariantStatus
from apps.catalog.domain.events import (
    StoreProductAdded,
    StoreProductAvailabilityChanged,
    StoreProductPriceChanged,
)
from apps.catalog.infrastructure.models import Product, ProductVariant, StoreProduct
from apps.stores.application.permissions import user_has_store_role
from apps.stores.domain.enums import StoreMembershipRole
from common.events.bus import publish

logger = logging.getLogger(__name__)


class SellerCatalogStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    FORBIDDEN = "FORBIDDEN"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    PRODUCT_INACTIVE = "PRODUCT_INACTIVE"
    VARIANT_NOT_FOUND = "VARIANT_NOT_FOUND"
    VARIANT_INVALID = "VARIANT_INVALID"
    VARIANT_INACTIVE = "VARIANT_INACTIVE"
    STORE_PRODUCT_NOT_FOUND = "STORE_PRODUCT_NOT_FOUND"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_AVAILABILITY = "INVALID_AVAILABILITY"
    INVALID_CURRENCY = "INVALID_CURRENCY"


@dataclass(frozen=True, kw_only=True)
class SellerCatalogMutationResult:
    status: SellerCatalogStatus
    store_product: StoreProduct | None = None
    warnings: tuple[str, ...] = ()

    @property
    def store_product_id(self) -> str | None:
        return str(self.store_product.id) if self.store_product else None


@dataclass(frozen=True, kw_only=True)
class SellerCatalogListResult:
    status: SellerCatalogStatus
    items: tuple[StoreProduct, ...] = ()


class SellerCatalogService:
    """Authorization, validation and audit boundary; no text/LLM inputs."""

    view_roles = tuple(StoreMembershipRole.values)

    def list_catalog(self, *, actor, store) -> SellerCatalogListResult:
        if not user_has_store_role(actor, store, list(self.view_roles)):
            return SellerCatalogListResult(status=SellerCatalogStatus.FORBIDDEN)
        return SellerCatalogListResult(
            status=SellerCatalogStatus.SUCCESS,
            items=tuple(get_store_products(store)),
        )

    @transaction.atomic
    def add_product(
        self, *, actor, store, product_id, variant_id=None, price=None,
        availability=ProductAvailability.UNKNOWN, currency="XAF", display_name=None,
        correlation_id=None,
    ) -> SellerCatalogMutationResult:
        if not can_manage_store_catalog(actor, store):
            return SellerCatalogMutationResult(status=SellerCatalogStatus.FORBIDDEN)
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            return SellerCatalogMutationResult(status=SellerCatalogStatus.PRODUCT_NOT_FOUND)
        if product.status != ProductStatus.ACTIVE:
            return SellerCatalogMutationResult(status=SellerCatalogStatus.PRODUCT_INACTIVE)
        variant, failure = self._variant(product, variant_id)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        normalized_price, failure = self._price(price)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        availability, failure = self._availability(availability)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        currency, failure = self._currency(currency)
        if failure:
            return SellerCatalogMutationResult(status=failure)

        existing = StoreProduct.objects.select_for_update().filter(
            store=store, product=product, product_variant=variant
        ).first()
        if existing:
            return SellerCatalogMutationResult(
                status=SellerCatalogStatus.ALREADY_EXISTS, store_product=existing
            )
        try:
            with transaction.atomic():
                store_product = add_product_to_store(
                    store=store, product=product, product_variant=variant,
                    display_name=display_name,
                    price=normalized_price, currency=currency,
                    availability_status=availability,
                )
        except IntegrityError:
            existing = StoreProduct.objects.get(
                store=store, product=product, product_variant=variant
            )
            return SellerCatalogMutationResult(
                status=SellerCatalogStatus.ALREADY_EXISTS, store_product=existing
            )
        self._audit("store_product_added", actor, store_product, correlation_id)
        transaction.on_commit(lambda: publish(StoreProductAdded(
            actor_id=str(actor.id), store_id=str(store.id),
            store_product_id=str(store_product.id), correlation_id=correlation_id,
        )))
        return SellerCatalogMutationResult(status=SellerCatalogStatus.SUCCESS, store_product=store_product)

    @transaction.atomic
    def update_price(self, *, actor, store_product_id, price, currency="XAF", correlation_id=None):
        store_product, failure = self._mutable(actor, store_product_id)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        normalized_price, failure = self._price(price)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        currency, failure = self._currency(currency)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        old = {"price": str(store_product.price) if store_product.price is not None else None, "currency": store_product.currency}
        set_store_product_price(store_product, normalized_price, currency)
        self._audit("store_product_price_changed", actor, store_product, correlation_id, old=old, new={"price": str(normalized_price) if normalized_price is not None else None, "currency": currency})
        transaction.on_commit(lambda: publish(StoreProductPriceChanged(actor_id=str(actor.id), store_id=str(store_product.store_id), store_product_id=str(store_product.id), correlation_id=correlation_id)))
        return SellerCatalogMutationResult(status=SellerCatalogStatus.SUCCESS, store_product=store_product)

    @transaction.atomic
    def set_availability(self, *, actor, store_product_id, availability, correlation_id=None):
        store_product, failure = self._mutable(actor, store_product_id)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        availability, failure = self._availability(availability)
        if failure:
            return SellerCatalogMutationResult(status=failure)
        old = store_product.availability_status
        set_product_availability(store_product, availability)
        action = "removed" if availability == ProductAvailability.UNAVAILABLE else "availability_changed"
        self._audit("store_product_" + action, actor, store_product, correlation_id, old={"availability": old}, new={"availability": availability.value})
        transaction.on_commit(lambda: publish(StoreProductAvailabilityChanged(actor_id=str(actor.id), store_id=str(store_product.store_id), store_product_id=str(store_product.id), action=action, correlation_id=correlation_id)))
        return SellerCatalogMutationResult(status=SellerCatalogStatus.SUCCESS, store_product=store_product)

    def remove_product(self, *, actor, store_product_id, correlation_id=None):
        return self.set_availability(actor=actor, store_product_id=store_product_id, availability=ProductAvailability.UNAVAILABLE, correlation_id=correlation_id)

    def restore_product(self, *, actor, store_product_id, correlation_id=None):
        return self.set_availability(actor=actor, store_product_id=store_product_id, availability=ProductAvailability.AVAILABLE, correlation_id=correlation_id)

    @staticmethod
    def _variant(product, variant_id):
        if variant_id is None:
            return None, None
        variant = ProductVariant.objects.filter(pk=variant_id).first()
        if variant is None:
            return None, SellerCatalogStatus.VARIANT_NOT_FOUND
        if variant.product_id != product.id:
            return None, SellerCatalogStatus.VARIANT_INVALID
        if variant.status != ProductVariantStatus.ACTIVE:
            return None, SellerCatalogStatus.VARIANT_INACTIVE
        return variant, None

    @staticmethod
    def _price(value):
        if value is None:
            return None, None
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            return None, SellerCatalogStatus.INVALID_PRICE
        try:
            normalized = value.quantize(Decimal("0.01"))
        except InvalidOperation:
            return None, SellerCatalogStatus.INVALID_PRICE
        if normalized.adjusted() > 9:
            return None, SellerCatalogStatus.INVALID_PRICE
        return normalized, None

    @staticmethod
    def _availability(value):
        try:
            return ProductAvailability(value), None
        except (TypeError, ValueError):
            return None, SellerCatalogStatus.INVALID_AVAILABILITY

    @staticmethod
    def _currency(value):
        value = value.upper() if isinstance(value, str) else ""
        if len(value) != 3 or not value.isalpha():
            return None, SellerCatalogStatus.INVALID_CURRENCY
        return value, None

    @staticmethod
    def _mutable(actor, store_product_id):
        store_product = StoreProduct.objects.select_for_update().select_related("store").filter(pk=store_product_id).first()
        if store_product is None:
            return None, SellerCatalogStatus.STORE_PRODUCT_NOT_FOUND
        if not can_manage_store_catalog(actor, store_product.store):
            return None, SellerCatalogStatus.FORBIDDEN
        return store_product, None

    @staticmethod
    def _audit(action, actor, store_product, correlation_id, *, old=None, new=None):
        logger.info(action, extra={"actor_id": str(actor.id), "store_id": str(store_product.store_id), "store_product_id": str(store_product.id), "old": old or {}, "new": new or {}, "correlation_id": correlation_id})
