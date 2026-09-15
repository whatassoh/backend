from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.catalog.application.selectors import (
    get_available_store_products,
    get_candidate_store_products,
    get_store_catalog,
    get_store_product,
    get_store_products,
    get_stores_for_product,
)
from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
    create_product_variant,
    remove_product_from_store,
    set_product_availability,
    set_store_product_price,
)
from apps.catalog.domain.enums import ProductAvailability, ProductStatus
from apps.catalog.infrastructure.models import StoreProduct
from apps.identity.infrastructure.models import User
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole, StoreStatus


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def create_user(phone: str) -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_test_store(name: str = "Boutique A"):
    return create_store(
        name=name,
        location=Point(9.7357, 4.0907, srid=4326),
        city="Douala",
    )


def create_test_product(name: str = "Riz"):
    category = create_category(name=f"Catégorie {name}")

    return create_product(name=name, category=category)


@pytest.mark.django_db
def test_store_product_without_variant_is_valid():
    store = create_test_store()
    product = create_test_product("Riz")

    store_product = add_product_to_store(
        store=store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )

    assert store_product.product_variant is None
    assert store_product.availability_status == ProductAvailability.AVAILABLE


@pytest.mark.django_db
def test_store_product_with_variant_is_valid():
    store = create_test_store()
    product = create_test_product("Huile Mayor")
    variant = create_product_variant(product=product, name="1 L")

    store_product = add_product_to_store(
        store=store,
        product=product,
        product_variant=variant,
        price=Decimal("1800.00"),
    )

    assert store_product.product_variant == variant
    assert store_product.price == Decimal("1800.00")
    assert store_product.currency == "XAF"


@pytest.mark.django_db
def test_available_store_product_with_null_price_is_valid():
    store_product = add_product_to_store(
        store=create_test_store(),
        product=create_test_product("Riz"),
        availability_status=ProductAvailability.AVAILABLE,
        price=None,
    )

    assert store_product.price is None


@pytest.mark.django_db
def test_unknown_store_product_is_valid():
    store_product = add_product_to_store(
        store=create_test_store(),
        product=create_test_product("Savon"),
        availability_status=ProductAvailability.UNKNOWN,
    )

    assert store_product.availability_status == ProductAvailability.UNKNOWN


@pytest.mark.django_db
def test_variant_must_belong_to_product():
    mayor = create_test_product("Huile Mayor")
    rice = create_test_product("Riz")
    variant = create_product_variant(product=mayor, name="1 L")

    with pytest.raises(ValidationError):
        add_product_to_store(
            store=create_test_store(),
            product=rice,
            product_variant=variant,
        )


@pytest.mark.django_db
def test_duplicate_store_product_without_variant_is_rejected():
    store = create_test_store()
    product = create_test_product("Riz")
    add_product_to_store(store=store, product=product)

    with pytest.raises(IntegrityError):
        add_product_to_store(store=store, product=product)


@pytest.mark.django_db
def test_duplicate_store_product_with_variant_is_rejected():
    store = create_test_store()
    product = create_test_product("Huile Mayor")
    variant = create_product_variant(product=product, name="1 L")
    add_product_to_store(store=store, product=product, product_variant=variant)

    with pytest.raises(IntegrityError):
        add_product_to_store(store=store, product=product, product_variant=variant)


@pytest.mark.django_db
def test_distinct_variants_for_same_store_product_are_allowed():
    store = create_test_store()
    product = create_test_product("Huile Mayor")
    small = create_product_variant(product=product, name="500 ml")
    large = create_product_variant(product=product, name="1 L")

    add_product_to_store(store=store, product=product, product_variant=small)
    add_product_to_store(store=store, product=product, product_variant=large)

    assert StoreProduct.objects.count() == 2


@pytest.mark.django_db
def test_same_product_in_different_stores_is_allowed():
    product = create_test_product("Riz")

    add_product_to_store(store=create_test_store("Boutique A"), product=product)
    add_product_to_store(store=create_test_store("Boutique B"), product=product)

    assert StoreProduct.objects.count() == 2


@pytest.mark.django_db
def test_availability_transitions_do_not_touch_stock_fields():
    store_product = add_product_to_store(
        store=create_test_store(),
        product=create_test_product("Riz"),
        availability_status=ProductAvailability.UNKNOWN,
    )

    set_product_availability(store_product, ProductAvailability.AVAILABLE)
    set_product_availability(store_product, ProductAvailability.UNAVAILABLE)
    set_product_availability(store_product, ProductAvailability.AVAILABLE)
    store_product.refresh_from_db()

    assert store_product.availability_status == ProductAvailability.AVAILABLE
    assert not hasattr(store_product, "stock_quantity")
    assert not hasattr(store_product, "reserved_quantity")


@pytest.mark.django_db
def test_price_null_valid_decimal_valid_invalid_rejected_and_default_currency():
    store_product = add_product_to_store(
        store=create_test_store(),
        product=create_test_product("Riz"),
        price=None,
    )

    assert store_product.price is None
    assert store_product.currency == "XAF"

    set_store_product_price(store_product, Decimal("1500.00"), "XAF")
    store_product.refresh_from_db()

    assert store_product.price == Decimal("1500.00")

    with pytest.raises(ValidationError):
        set_store_product_price(store_product, Decimal("-1.00"))


@pytest.mark.django_db
def test_remove_product_from_store_marks_unavailable():
    store_product = add_product_to_store(
        store=create_test_store(),
        product=create_test_product("Riz"),
        availability_status=ProductAvailability.AVAILABLE,
    )

    remove_product_from_store(store_product)
    store_product.refresh_from_db()

    assert store_product.availability_status == ProductAvailability.UNAVAILABLE


@pytest.mark.django_db
def test_store_catalog_selectors():
    store = create_test_store()
    product = create_test_product("Riz")
    store_product = add_product_to_store(
        store=store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )

    assert list(get_store_catalog(store)) == [store_product]
    assert list(get_store_products(store)) == [store_product]
    assert list(get_available_store_products(store)) == [store_product]
    assert list(get_stores_for_product(product)) == [store]
    assert get_store_product(store, product) == store_product


@pytest.mark.django_db
def test_candidate_store_products_excludes_non_operational_records():
    product = create_test_product("Riz")
    available_store = create_test_store("Available")
    inactive_store = create_test_store("Inactive")
    inactive_store.status = StoreStatus.INACTIVE
    inactive_store.save(update_fields=["status", "updated_at"])

    available = add_product_to_store(
        store=available_store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )
    StoreProduct.objects.create(
        store=inactive_store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )
    unavailable = add_product_to_store(
        store=create_test_store("Unavailable"),
        product=product,
        availability_status=ProductAvailability.UNAVAILABLE,
    )

    assert list(get_candidate_store_products(product)) == [available]
    assert unavailable not in get_candidate_store_products(product)


@pytest.mark.django_db
def test_candidate_store_products_excludes_inactive_product():
    product = create_test_product("Riz")
    product.status = ProductStatus.INACTIVE
    product.save(update_fields=["status", "updated_at"])
    StoreProduct.objects.create(
        store=create_test_store(),
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )

    assert list(get_candidate_store_products(product)) == []


@pytest.mark.django_db
def test_authorized_store_member_can_modify_store_catalog(api_client):
    store = create_test_store()
    product = create_test_product("Riz")
    user = create_user("+237699887766")
    add_store_member(store, user, StoreMembershipRole.OWNER)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f"/api/v1/stores/{store.id}/products/",
        {
            "product": str(product.id),
            "availability_status": ProductAvailability.AVAILABLE,
        },
        format="json",
    )

    assert response.status_code == 201


@pytest.mark.django_db
def test_user_without_permission_cannot_modify_store_catalog(api_client):
    store = create_test_store()
    product = create_test_product("Riz")
    user = create_user("+237699887766")
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f"/api/v1/stores/{store.id}/products/",
        {
            "product": str(product.id),
            "availability_status": ProductAvailability.AVAILABLE,
        },
        format="json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_store_b_member_cannot_modify_store_a_catalog(api_client):
    store_a = create_test_store("Boutique A")
    store_b = create_test_store("Boutique B")
    product = create_test_product("Riz")
    user = create_user("+237699887766")
    add_store_member(store_b, user, StoreMembershipRole.OWNER)
    api_client.force_authenticate(user=user)

    response = api_client.post(
        f"/api/v1/stores/{store_a.id}/products/",
        {
            "store": str(store_b.id),
            "product": str(product.id),
            "availability_status": ProductAvailability.AVAILABLE,
        },
        format="json",
    )

    assert response.status_code == 403
