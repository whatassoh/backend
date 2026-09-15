from typing import Any

from django.db import transaction

from apps.catalog.domain.enums import (
    CategoryStatus,
    ProductAvailability,
    ProductStatus,
    ProductVariantStatus,
)
from apps.catalog.domain.rules import validate_price, validate_store_product_variant
from apps.catalog.domain.services import build_category_slug
from apps.catalog.infrastructure.models import (
    Category,
    Product,
    ProductAlias,
    ProductVariant,
    StoreProduct,
)
from apps.stores.domain.enums import StoreStatus
from apps.stores.infrastructure.models import Store


@transaction.atomic
def create_category(
    *,
    name: str,
    slug: str | None = None,
    parent: Category | None = None,
    status: CategoryStatus | str = CategoryStatus.ACTIVE,
) -> Category:
    return Category.objects.create(
        name=name,
        slug=build_category_slug(name, slug),
        parent=parent,
        status=status,
    )


def update_category(category: Category, **fields: Any) -> Category:
    for field, value in fields.items():
        setattr(category, field, value)

    if "name" in fields and "slug" not in fields:
        category.slug = build_category_slug(category.name)

    category.save()

    return category


def deactivate_category(category: Category) -> Category:
    category.status = CategoryStatus.INACTIVE
    category.save(update_fields=["status", "updated_at"])

    return category


@transaction.atomic
def create_product(
    *,
    name: str,
    category: Category,
    default_unit: str | None = None,
    status: ProductStatus | str = ProductStatus.ACTIVE,
) -> Product:
    return Product.objects.create(
        name=name,
        category=category,
        default_unit=default_unit,
        status=status,
    )


def update_product(product: Product, **fields: Any) -> Product:
    for field, value in fields.items():
        setattr(product, field, value)

    product.save()

    return product


def deactivate_product(product: Product) -> Product:
    product.status = ProductStatus.INACTIVE
    product.save(update_fields=["status", "updated_at"])

    return product


@transaction.atomic
def create_product_variant(
    *,
    product: Product,
    name: str,
    attributes: dict[str, Any] | None = None,
    status: ProductVariantStatus | str = ProductVariantStatus.ACTIVE,
) -> ProductVariant:
    return ProductVariant.objects.create(
        product=product,
        name=name,
        attributes=attributes,
        status=status,
    )


def deactivate_product_variant(variant: ProductVariant) -> ProductVariant:
    variant.status = ProductVariantStatus.INACTIVE
    variant.save(update_fields=["status", "updated_at"])

    return variant


@transaction.atomic
def add_product_alias(*, product: Product, alias: str) -> ProductAlias:
    return ProductAlias.objects.create(
        product=product,
        alias=alias,
    )


def remove_product_alias(alias: ProductAlias) -> int:
    deleted_count, _ = ProductAlias.objects.filter(id=alias.id).delete()

    return deleted_count


@transaction.atomic
def add_product_to_store(
    *,
    store: Store,
    product: Product,
    product_variant: ProductVariant | None = None,
    display_name: str | None = None,
    price: object | None = None,
    currency: str = "XAF",
    availability_status: ProductAvailability | str = ProductAvailability.UNKNOWN,
) -> StoreProduct:
    if store.status != StoreStatus.ACTIVE:
        raise ValueError("Only active stores can add catalog products.")
    if product.status != ProductStatus.ACTIVE:
        raise ValueError("Only active products can be added to a store catalog.")
    if (
        product_variant is not None
        and product_variant.status != ProductVariantStatus.ACTIVE
    ):
        raise ValueError("Only active product variants can be added.")

    validate_store_product_variant(product.id, product_variant)
    validate_price(price)

    return StoreProduct.objects.create(
        store=store,
        product=product,
        product_variant=product_variant,
        display_name=display_name,
        price=price,
        currency=currency,
        availability_status=availability_status,
    )


def update_store_product(store_product: StoreProduct, **fields: Any) -> StoreProduct:
    product = fields.get("product", store_product.product)
    product_variant = fields.get("product_variant", store_product.product_variant)
    price = fields.get("price", store_product.price)

    validate_store_product_variant(product.id, product_variant)
    validate_price(price)

    for field, value in fields.items():
        setattr(store_product, field, value)

    store_product.save()

    return store_product


def set_product_availability(
    store_product: StoreProduct,
    availability_status: ProductAvailability | str,
) -> StoreProduct:
    store_product.availability_status = availability_status
    store_product.save(update_fields=["availability_status", "updated_at"])

    return store_product


def set_store_product_price(
    store_product: StoreProduct,
    price: object | None,
    currency: str = "XAF",
) -> StoreProduct:
    validate_price(price)
    store_product.price = price
    store_product.currency = currency
    store_product.save(update_fields=["price", "currency", "updated_at"])

    return store_product


def remove_product_from_store(store_product: StoreProduct) -> StoreProduct:
    return set_product_availability(store_product, ProductAvailability.UNAVAILABLE)
