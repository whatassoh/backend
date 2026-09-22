import pytest
from django.contrib.gis.geos import Point

from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.enums import ProductUnit
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.identity.infrastructure.models import User
from apps.orders.application.services import (
    add_order_item,
    confirm_order,
    create_order_draft,
    set_delivery_location,
)
from apps.stores.application.eligibility import StoreEligibilityService
from apps.stores.application.services import create_store
from apps.stores.domain.enums import StoreStatus


def confirmed_order():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    product = create_product(name="Riz", category=create_category(name="Food"))
    add_order_item(order=order, requested_name="Riz", quantity=1, unit=ProductUnit.KG, product=product, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order=order, point=Point(9.7357, 4.0907, srid=4326))
    return confirm_order(order)


@pytest.mark.django_db
def test_eligibility_rejects_non_confirmed_order_without_mutation():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)

    result = StoreEligibilityService().find_eligible_stores(order)

    assert result.eligible_stores == ()
    assert result.metadata["precondition"] == "ORDER_NOT_CONFIRMED"
    order.refresh_from_db()
    assert order.status == "DRAFT"


@pytest.mark.django_db
def test_postgis_eligibility_excludes_inactive_and_too_far_stores():
    order = confirmed_order()
    near = create_store(
        name="Near", city="Douala", location=Point(9.736, 4.091, srid=4326)
    )
    inactive = create_store(
        name="Inactive", city="Douala", location=Point(9.736, 4.091, srid=4326)
    )
    inactive.status = StoreStatus.INACTIVE
    inactive.save(update_fields=["status", "updated_at"])
    far = create_store(
        name="Far", city="Douala", location=Point(9.0, 4.0, srid=4326)
    )

    result = StoreEligibilityService().find_eligible_stores(order)

    assert [candidate.store_id for candidate in result.eligible_stores] == [str(near.id)]
    assert {excluded.store_id for excluded in result.excluded_stores} == {
        str(inactive.id), str(far.id)
    }
