from decimal import Decimal

import pytest
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.catalog.application.selectors import get_candidate_store_products
from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
    create_product_variant,
)
from apps.catalog.domain.enums import ProductAvailability
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    set_driver_available,
)
from apps.drivers.domain.enums import DriverAvailability, DriverStatus, VehicleType
from apps.identity.application.services import assign_role, has_role
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User
from apps.stores.application.selectors import get_stores_within_radius
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def create_user(phone: str) -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_demo_store(name: str, point: Point):
    return create_store(name=name, location=point, city="Douala")


@pytest.mark.django_db
def test_phase2_store_owner_catalog_flow():
    user = create_user("+237699100001")
    assign_role(user, UserRoleType.STORE_OWNER)
    store = create_demo_store("Boutique A", Point(9.7357, 4.0907, srid=4326))
    add_store_member(store, user, StoreMembershipRole.OWNER)
    category = create_category(name="Alimentation")
    product = create_product(name="Riz", category=category)

    store_product = add_product_to_store(
        store=store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
        price=None,
    )

    assert has_role(user, UserRoleType.STORE_OWNER)
    assert store_product.store == store
    assert store_product.product == product
    assert store_product.price is None


@pytest.mark.django_db
def test_phase2_driver_flow():
    user = create_user("+237699100002")

    driver = create_driver_profile(user, vehicle_type=VehicleType.MOTORBIKE)
    activate_driver(driver)
    set_driver_available(driver)
    driver.refresh_from_db()

    assert has_role(user, UserRoleType.DRIVER)
    assert driver.status == DriverStatus.ACTIVE
    assert driver.availability_status == DriverAvailability.AVAILABLE


@pytest.mark.django_db
def test_phase2_multi_role_user_remains_single_identity():
    user = create_user("+237699100003")
    assign_role(user, UserRoleType.CLIENT)
    driver = create_driver_profile(user)
    store = create_demo_store("Boutique A", Point(9.7357, 4.0907, srid=4326))
    add_store_member(store, user, StoreMembershipRole.MANAGER)

    assert User.objects.count() == 1
    assert driver.user == user
    assert has_role(user, UserRoleType.CLIENT)
    assert has_role(user, UserRoleType.DRIVER)


@pytest.mark.django_db
def test_phase2_soft_catalog_product_without_variant_or_price():
    category = create_category(name="Alimentation")
    product = create_product(name="Riz", category=category)
    store = create_demo_store("Boutique A", Point(9.7357, 4.0907, srid=4326))

    store_product = add_product_to_store(
        store=store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )

    assert store_product.product_variant is None
    assert store_product.price is None
    assert list(get_candidate_store_products(product)) == [store_product]


@pytest.mark.django_db
def test_phase2_variant_coherence_flow():
    category = create_category(name="Alimentation")
    mayor = create_product(name="Huile Mayor", category=category)
    rice = create_product(name="Riz", category=category)
    mayor_1l = create_product_variant(product=mayor, name="1 L")
    store = create_demo_store("Boutique A", Point(9.7357, 4.0907, srid=4326))

    valid_store_product = add_product_to_store(
        store=store,
        product=mayor,
        product_variant=mayor_1l,
        availability_status=ProductAvailability.AVAILABLE,
        price=Decimal("1800.00"),
    )

    assert valid_store_product.product_variant == mayor_1l

    with pytest.raises(ValidationError):
        add_product_to_store(
            store=store,
            product=rice,
            product_variant=mayor_1l,
        )


@pytest.mark.django_db
def test_phase2_store_catalog_permissions(api_client):
    store_a = create_demo_store("Boutique A", Point(9.7357, 4.0907, srid=4326))
    store_b = create_demo_store("Boutique B", Point(9.7043, 4.0511, srid=4326))
    owner_a = create_user("+237699100004")
    manager_a = create_user("+237699100005")
    outsider = create_user("+237699100006")
    owner_b = create_user("+237699100007")
    category = create_category(name="Alimentation")
    product = create_product(name="Riz", category=category)
    add_store_member(store_a, owner_a, StoreMembershipRole.OWNER)
    add_store_member(store_a, manager_a, StoreMembershipRole.MANAGER)
    add_store_member(store_b, owner_b, StoreMembershipRole.OWNER)

    api_client.force_authenticate(user=owner_a)
    owner_response = api_client.post(
        f"/api/v1/stores/{store_a.id}/products/",
        {"product": str(product.id)},
        format="json",
    )
    assert owner_response.status_code == 201

    store_product_id = owner_response.json()["id"]
    api_client.force_authenticate(user=manager_a)
    manager_response = api_client.patch(
        f"/api/v1/stores/{store_a.id}/products/{store_product_id}/",
        {"availability_status": ProductAvailability.AVAILABLE},
        format="json",
    )
    assert manager_response.status_code == 200

    api_client.force_authenticate(user=outsider)
    outsider_response = api_client.patch(
        f"/api/v1/stores/{store_a.id}/products/{store_product_id}/",
        {"availability_status": ProductAvailability.UNAVAILABLE},
        format="json",
    )
    assert outsider_response.status_code == 403

    api_client.force_authenticate(user=owner_b)
    other_store_response = api_client.patch(
        f"/api/v1/stores/{store_a.id}/products/{store_product_id}/",
        {"availability_status": ProductAvailability.UNAVAILABLE},
        format="json",
    )
    assert other_store_response.status_code == 403


@pytest.mark.django_db
def test_phase2_postgis_distance_and_radius_search():
    client_point = Point(9.7357, 4.0907, srid=4326)
    near_store = create_demo_store("Near", Point(9.7360, 4.0910, srid=4326))
    medium_store = create_demo_store("Medium", Point(9.7043, 4.0511, srid=4326))
    far_store = create_demo_store("Far", Point(11.5021, 3.8480, srid=4326))

    distance = (
        type(near_store)
        .objects.annotate(distance=Distance("location", client_point))
        .get(id=near_store.id)
        .distance
    )
    stores_within_5km = list(get_stores_within_radius(client_point, 5000))
    stores_within_500m = list(
        type(near_store).objects.filter(location__distance_lte=(client_point, D(m=500)))
    )

    assert distance.m > 0
    assert near_store in stores_within_5km
    assert medium_store not in stores_within_500m
    assert far_store not in stores_within_5km
