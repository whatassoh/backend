import pytest

from apps.catalog.application.services import add_product_to_store, create_category, create_product
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import add_order_item, create_order_draft
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.stores.application.fulfillment import (
    FulfillmentReason,
    StoreFulfillmentService,
)
from apps.stores.application.services import create_store
from django.contrib.gis.geos import Point


def setup_order_and_store():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    store = create_store(name="Store", city="Douala", location=Point(9.7, 4.05, srid=4326))
    category = create_category(name="Food")
    rice = create_product(name="Riz", category=category)
    return order, store, rice


@pytest.mark.django_db
def test_declared_available_store_product_covers_item_without_stock_guarantee():
    order, store, rice = setup_order_and_store()
    add_order_item(
        order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG,
        product=rice, resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    add_product_to_store(store=store, product=rice, availability_status=ProductAvailability.AVAILABLE)

    result = StoreFulfillmentService().evaluate_store(order, store)

    assert result.fully_covered is True
    assert result.item_results[0].quantity_certainty == "DECLARED_AVAILABLE"
    assert result.item_results[0].price is None


@pytest.mark.django_db
def test_unmatched_and_unavailable_items_are_explained_not_invented():
    order, store, rice = setup_order_and_store()
    add_order_item(order=order, requested_name="Inconnu", quantity=1, unit=ProductUnit.UNIT)
    add_order_item(
        order=order, requested_name="Riz", quantity=1, unit=ProductUnit.KG,
        product=rice, resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    add_product_to_store(store=store, product=rice, availability_status=ProductAvailability.UNAVAILABLE)

    result = StoreFulfillmentService().evaluate_store(order, store)

    assert result.covered_items == 0
    assert [item.reason for item in result.item_results] == [
        FulfillmentReason.ORDER_ITEM_UNMATCHED,
        FulfillmentReason.STORE_PRODUCT_UNAVAILABLE,
    ]
