import pytest
from django.contrib.gis.geos import Point

from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.enums import ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.review import OrderReviewService
from apps.orders.application.services import (
    add_order_item,
    create_order_draft,
    set_delivery_location,
    update_order_item,
)
from apps.orders.domain.enums import OrderItemResolutionStatus


@pytest.mark.django_db
def test_review_is_built_from_current_database_truth_and_is_ready():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    product = create_product(name="Riz", category=create_category(name="Food"))
    item = add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG, product=product, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order=order, point=Point(9.7, 4.05, srid=4326))
    service = OrderReviewService()
    first = service.build_review(order)
    update_order_item(item, quantity=10)
    second = service.build_review(order)

    assert first.readiness.ready is True
    assert second.items[0].quantity == "10.000"
    assert first.fingerprint != second.fingerprint
    assert "order.confirm" in second.available_actions


@pytest.mark.django_db
def test_review_exposes_missing_location_and_unmatched_as_blocker():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    add_order_item(
        order=order,
        requested_name="Produit inconnu",
        quantity=1,
        unit=ProductUnit.UNIT,
        resolution_status=OrderItemResolutionStatus.UNMATCHED,
    )

    review = OrderReviewService().build_review(order)

    assert review.readiness.ready is False
    assert "delivery_location_missing" in review.readiness.blocking_issues
    assert "unmatched_items" in review.readiness.blocking_issues
    assert "order.confirm" not in review.available_actions
