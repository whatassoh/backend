from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.catalog.application.discovery import ProductDiscoveryService
from apps.catalog.application.services import (
    add_product_alias,
    add_product_to_store,
    create_category,
    create_product,
    create_product_variant,
)
from apps.catalog.domain.discovery import ProductSearchQuery
from apps.catalog.domain.enums import ProductAvailability
from apps.stores.application.services import create_store


@pytest.fixture
def store():
    return create_store(
        name="Marketplace discovery",
        location=Point(9.7, 4.05, srid=4326),
        city="Douala",
    )


def offer(store, name, *, category="Alimentation", price=None, variant=None):
    category_obj = create_category(name=category)
    product = create_product(name=name, category=category_obj)
    product_variant = (
        create_product_variant(product=product, name=variant) if variant else None
    )
    add_product_to_store(
        store=store,
        product=product,
        product_variant=product_variant,
        price=price,
        availability_status=ProductAvailability.AVAILABLE,
    )
    return product, product_variant


@pytest.mark.django_db
def test_exact_search_returns_only_real_available_offer(store):
    oil, _ = offer(store, "Huile Mayor", price=Decimal("1800"))
    offer(store, "Farine de maïs", price=Decimal("900"), category="Epicerie")

    result = ProductDiscoveryService().search(
        ProductSearchQuery(product_hint="huile", text="huile")
    )

    assert result.total == 1
    assert result.products[0].product_id == oil.id
    assert result.products[0].minimum_price == Decimal("1800")


@pytest.mark.django_db
def test_absent_product_does_not_fall_back_to_global_catalog(store):
    offer(store, "Couches bébé")

    result = ProductDiscoveryService().search(
        ProductSearchQuery(product_hint="gaz", text="gaz")
    )

    assert result.total == 0
    assert result.products == ()
    assert result.known_but_unavailable is False


@pytest.mark.django_db
def test_known_but_unavailable_product_is_distinguished(store):
    category = create_category(name="Energie")
    create_product(name="Gaz domestique", category=category)

    result = ProductDiscoveryService().search(
        ProductSearchQuery(product_hint="gaz domestique", text="gaz domestique")
    )

    assert result.total == 0
    assert result.known_but_unavailable is True


@pytest.mark.django_db
def test_budget_filters_on_real_store_prices(store):
    offer(store, "Téléphone Alpha", category="Téléphones", price=Decimal("450000"))
    offer(store, "Téléphone Premium", category="Mobiles", price=Decimal("650000"))

    result = ProductDiscoveryService().search(
        ProductSearchQuery(
            product_hint="téléphone", text="téléphone", budget_max=Decimal("500000")
        )
    )

    assert [item.product_name for item in result.products] == ["Téléphone Alpha"]


@pytest.mark.django_db
def test_category_need_is_resolved_from_real_category(store):
    meal, _ = offer(store, "Riz parfumé", category="Alimentation")
    offer(store, "Savon vaisselle", category="Nettoyage")

    result = ProductDiscoveryService().search(
        ProductSearchQuery(product_hint="alimentation", text="alimentation")
    )

    assert [item.product_id for item in result.products] == [meal.id]


@pytest.mark.django_db
def test_alias_search_is_grounded_in_product_alias(store):
    oil, _ = offer(store, "Huile Mayor")
    add_product_alias(product=oil, alias="huille mayor")

    result = ProductDiscoveryService().search(
        ProductSearchQuery(product_hint="huille mayor", text="huille mayor")
    )

    assert [item.product_id for item in result.products] == [oil.id]


@pytest.mark.django_db
def test_variant_filter_keeps_quantity_format_separate(store):
    category = create_category(name="Huiles")
    product = create_product(name="Huile Mayor", category=category)
    small = create_product_variant(product=product, name="1 L")
    large = create_product_variant(product=product, name="5 L")
    for variant, price in ((small, "1800"), (large, "7800")):
        add_product_to_store(
            store=store,
            product=product,
            product_variant=variant,
            price=Decimal(price),
            availability_status=ProductAvailability.AVAILABLE,
        )

    result = ProductDiscoveryService().search(
        ProductSearchQuery(
            product_hint="Huile Mayor", text="Huile Mayor", variant_hint="1L"
        )
    )

    assert result.total == 1
    assert result.products[0].variant_id == small.id


@pytest.mark.django_db
def test_missing_variant_returns_only_real_variant_suggestions(store):
    category = create_category(name="Huiles")
    product = create_product(name="Huile Mayor", category=category)
    for label in ("1 L", "5 L"):
        variant = create_product_variant(product=product, name=label)
        add_product_to_store(
            store=store,
            product=product,
            product_variant=variant,
            availability_status=ProductAvailability.AVAILABLE,
        )

    result = ProductDiscoveryService().search(
        ProductSearchQuery(
            product_hint="Huile Mayor", text="Huile Mayor", variant_hint="20 L"
        )
    )

    assert result.products == ()
    assert {item.variant_name for item in result.variant_suggestions} == {"1 L", "5 L"}


@pytest.mark.django_db
def test_price_range_reflects_multiple_real_store_offers(store):
    product, _ = offer(store, "Riz", price=Decimal("1000"))
    second = create_store(
        name="Second store",
        location=Point(9.71, 4.06, srid=4326),
        city="Douala",
    )
    add_product_to_store(
        store=second,
        product=product,
        price=Decimal("1400"),
        availability_status=ProductAvailability.AVAILABLE,
    )

    hit = (
        ProductDiscoveryService()
        .search(ProductSearchQuery(product_hint="riz", text="riz"))
        .products[0]
    )

    assert (hit.minimum_price, hit.maximum_price) == (
        Decimal("1000"),
        Decimal("1400"),
    )
