import uuid

from django.db.models import Q, QuerySet

from apps.catalog.domain.enums import (
    CategoryStatus,
    ProductAvailability,
    ProductStatus,
    ProductVariantStatus,
)
from apps.catalog.domain.services import normalize_product_name
from apps.catalog.infrastructure.models import (
    Category,
    Product,
    ProductAlias,
    ProductVariant,
    StoreProduct,
)
from apps.stores.domain.enums import StoreStatus
from apps.stores.infrastructure.models import Store


def get_category_by_id(category_id: uuid.UUID | str) -> Category | None:
    return Category.objects.select_related("parent").filter(id=category_id).first()


def get_active_categories() -> QuerySet[Category]:
    return Category.objects.filter(status=CategoryStatus.ACTIVE).select_related(
        "parent"
    )


def get_category_children(category: Category) -> QuerySet[Category]:
    return category.children.filter(status=CategoryStatus.ACTIVE)


def get_product_by_id(product_id: uuid.UUID | str) -> Product | None:
    return (
        Product.objects.select_related("category")
        .prefetch_related("variants", "aliases")
        .filter(id=product_id)
        .first()
    )


def get_active_products() -> QuerySet[Product]:
    return (
        Product.objects.filter(status=ProductStatus.ACTIVE)
        .select_related("category")
        .prefetch_related("variants", "aliases")
    )


def get_products_by_category(category: Category) -> QuerySet[Product]:
    return get_active_products().filter(category=category)


def get_product_variants(product: Product) -> QuerySet[ProductVariant]:
    return product.variants.all()


def get_product_aliases(product: Product) -> QuerySet[ProductAlias]:
    return product.aliases.all()


def search_products(query: str) -> QuerySet[Product]:
    normalized_query = normalize_product_name(query)

    if not normalized_query:
        return Product.objects.none()

    return (
        get_active_products()
        .filter(
            Q(name__icontains=query.strip())
            | Q(normalized_name__icontains=normalized_query)
            | Q(aliases__normalized_alias__icontains=normalized_query)
        )
        .distinct()
    )


def get_store_catalog(store: Store) -> QuerySet[StoreProduct]:
    return get_store_products(store)


def get_store_products(store: Store) -> QuerySet[StoreProduct]:
    return (
        StoreProduct.objects.filter(store=store)
        .select_related("store", "product", "product__category", "product_variant")
        .order_by("product__name", "product_variant__name")
    )


def get_available_store_products(store: Store) -> QuerySet[StoreProduct]:
    return get_store_products(store).filter(
        availability_status=ProductAvailability.AVAILABLE
    )


def get_stores_for_product(product: Product) -> QuerySet[Store]:
    return Store.objects.filter(catalog_products__product=product).distinct()


def get_store_product(
    store: Store,
    product: Product,
    variant: ProductVariant | None = None,
) -> StoreProduct | None:
    return (
        StoreProduct.objects.select_related("store", "product", "product_variant")
        .filter(store=store, product=product, product_variant=variant)
        .first()
    )


def get_candidate_store_products(
    product: Product,
    variant: ProductVariant | None = None,
) -> QuerySet[StoreProduct]:
    queryset = StoreProduct.objects.filter(
        store__status=StoreStatus.ACTIVE,
        product__status=ProductStatus.ACTIVE,
        availability_status=ProductAvailability.AVAILABLE,
    )

    if variant is None:
        queryset = queryset.filter(product=product, product_variant__isnull=True)
    else:
        queryset = queryset.filter(
            product=product,
            product_variant=variant,
            product_variant__status=ProductVariantStatus.ACTIVE,
        )

    return queryset.select_related("store", "product", "product_variant")
