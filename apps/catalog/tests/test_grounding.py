from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.ai.domain.understanding import ExtractedItem, ExtractedUnit
from apps.catalog.application.grounding import CatalogGroundingService
from apps.catalog.application.services import (
    add_product_alias,
    add_product_to_store,
    create_category,
    create_product,
    create_product_variant,
)
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.stores.application.services import create_store


def make_product(name: str, *, unit=ProductUnit.UNIT):
    category = create_category(name=f"Category {name}")
    return create_product(name=name, category=category, default_unit=unit)


@pytest.mark.django_db
def test_exact_canonical_and_accent_insensitive_grounding():
    product = make_product("Riz Parfumé", unit=ProductUnit.KG)

    resolution = CatalogGroundingService().ground_item(
        ExtractedItem(
            requested_name="  riz   parfume ",
            quantity=Decimal("5"),
            unit=ExtractedUnit.KG,
        )
    )

    assert resolution.status == OrderItemResolutionStatus.MATCHED
    assert resolution.product_id == product.id
    assert resolution.product_id is not None


@pytest.mark.django_db
def test_alias_is_real_catalog_evidence_not_llm_fact():
    product = make_product("Huile Mayor", unit=ProductUnit.BOTTLE)
    add_product_alias(product=product, alias="Mayor")

    resolution = CatalogGroundingService().ground_item(
        ExtractedItem(
            requested_name="MAYOR", quantity=Decimal("2"), unit=ExtractedUnit.BOTTLE
        )
    )

    assert resolution.status == OrderItemResolutionStatus.MATCHED
    assert resolution.product_id == product.id
    assert resolution.reason == "ALIAS_MATCH"


@pytest.mark.django_db
def test_unknown_product_is_unmatched_without_invented_id():
    resolution = CatalogGroundingService().ground_item(
        ExtractedItem(requested_name="Produit totalement inexistant")
    )

    assert resolution.status == OrderItemResolutionStatus.UNMATCHED
    assert resolution.product_id is None
    assert resolution.variant_id is None


@pytest.mark.django_db
def test_variant_is_only_selected_when_real_variant_is_explicit():
    product = make_product("Coca")
    variant = create_product_variant(product=product, name="1.5 L")
    service = CatalogGroundingService()

    ambiguous = service.ground_item(ExtractedItem(requested_name="Coca"))
    selected = service.ground_item(ExtractedItem(requested_name="Coca 1.5 L"))

    assert ambiguous.status == OrderItemResolutionStatus.AMBIGUOUS
    assert selected.status == OrderItemResolutionStatus.MATCHED
    assert selected.product_id == product.id
    assert selected.variant_id == variant.id


@pytest.mark.django_db
def test_display_name_is_only_used_for_available_real_offer():
    product = make_product("Huile de palme")
    store = create_store(name="Store Display", location=Point(9.7, 4.0), city="Douala")
    add_product_to_store(
        store=store,
        product=product,
        display_name="Palm oil",
        availability_status=ProductAvailability.AVAILABLE,
    )

    resolution = CatalogGroundingService().ground_item(
        ExtractedItem(requested_name="palm oil")
    )

    assert resolution.status == OrderItemResolutionStatus.MATCHED
    assert resolution.product_id == product.id


@pytest.mark.django_db
def test_multi_item_is_independent_and_deterministic():
    mayor = make_product("Huile Mayor")
    add_product_alias(product=mayor, alias="Mayor")
    results = CatalogGroundingService().ground_items(
        (ExtractedItem(requested_name="Mayor"), ExtractedItem(requested_name="unknown"))
    )

    assert [result.status for result in results] == [
        OrderItemResolutionStatus.MATCHED,
        OrderItemResolutionStatus.UNMATCHED,
    ]
