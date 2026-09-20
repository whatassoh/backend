import threading
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.db import close_old_connections

from apps.catalog.application.seller_catalog import SellerCatalogService, SellerCatalogStatus
from apps.catalog.application.services import create_category, create_product, create_product_variant
from apps.catalog.domain.enums import ProductAvailability, ProductStatus
from apps.catalog.domain.events import StoreProductAdded
from apps.catalog.infrastructure.models import StoreProduct
from apps.identity.infrastructure.models import User
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole
from common.events.bus import subscribed


def user(number):
    return User.objects.create_user(whatsapp_phone=f"+23769988{number:04d}")


def store(name="Seller Store"):
    return create_store(name=name, city="Douala", location=Point(9.7, 4.05, srid=4326))


def product(name="Riz"):
    category = create_category(name=f"Category {name}")
    return create_product(name=name, category=category)


def owner_for(target_store):
    # ``count`` is the number of existing users, while the test helper embeds
    # the supplied number directly in a unique phone value.  Use the next slot;
    # otherwise a pre-created ``user(1)`` collides when count is also one.
    actor = user(User.objects.count() + 1)
    add_store_member(target_store, actor, StoreMembershipRole.OWNER)
    return actor


@pytest.mark.django_db(transaction=True)
def test_authorized_add_list_price_availability_remove_restore_and_event():
    target_store = store()
    actor = owner_for(target_store)
    rice = product()
    events = []
    service = SellerCatalogService()
    with subscribed(StoreProductAdded, events.append):
        added = service.add_product(
            actor=actor, store=target_store, product_id=rice.id,
            price=Decimal("1500.00"), availability=ProductAvailability.AVAILABLE,
            correlation_id="catalog-1",
        )
    assert added.status == SellerCatalogStatus.SUCCESS
    assert added.store_product.price == Decimal("1500.00")
    assert len(events) == 1
    assert service.list_catalog(actor=actor, store=target_store).items == (added.store_product,)
    assert service.update_price(actor=actor, store_product_id=added.store_product.id, price=None).store_product.price is None
    assert service.remove_product(actor=actor, store_product_id=added.store_product.id).store_product.availability_status == ProductAvailability.UNAVAILABLE
    assert service.restore_product(actor=actor, store_product_id=added.store_product.id).store_product.availability_status == ProductAvailability.AVAILABLE


@pytest.mark.django_db
def test_authorization_product_variant_and_price_validation():
    target_store = store()
    stranger = user(1)
    actor = owner_for(target_store)
    rice, oil, sugar = product("Riz"), product("Huile"), product("Sucre")
    oil_variant = create_product_variant(product=oil, name="1 L")
    sugar_variant = create_product_variant(product=sugar, name="1 KG")
    service = SellerCatalogService()
    assert service.add_product(actor=stranger, store=target_store, product_id=rice.id).status == SellerCatalogStatus.FORBIDDEN
    assert service.add_product(actor=actor, store=target_store, product_id="00000000-0000-0000-0000-000000000000").status == SellerCatalogStatus.PRODUCT_NOT_FOUND
    rice.status = ProductStatus.INACTIVE
    rice.save(update_fields=["status", "updated_at"])
    assert service.add_product(actor=actor, store=target_store, product_id=rice.id).status == SellerCatalogStatus.PRODUCT_INACTIVE
    assert service.add_product(actor=actor, store=target_store, product_id=oil.id, variant_id=oil_variant.id).status == SellerCatalogStatus.SUCCESS
    assert service.add_product(actor=actor, store=target_store, product_id=oil.id, variant_id=sugar_variant.id).status == SellerCatalogStatus.VARIANT_INVALID
    assert service.add_product(actor=actor, store=target_store, product_id=oil.id, variant_id="00000000-0000-0000-0000-000000000000").status == SellerCatalogStatus.VARIANT_NOT_FOUND
    assert service.add_product(actor=actor, store=target_store, product_id=oil.id, price=Decimal("-1")).status == SellerCatalogStatus.INVALID_PRICE
    assert service.add_product(actor=actor, store=target_store, product_id=oil.id, price=float("nan")).status == SellerCatalogStatus.INVALID_PRICE


@pytest.mark.django_db
def test_duplicate_is_controlled_and_store_membership_is_scoped():
    store_a, store_b = store("A"), store("B")
    actor = owner_for(store_a)
    rice = product()
    service = SellerCatalogService()
    first = service.add_product(actor=actor, store=store_a, product_id=rice.id)
    duplicate = service.add_product(actor=actor, store=store_a, product_id=rice.id)
    assert duplicate.status == SellerCatalogStatus.ALREADY_EXISTS
    assert StoreProduct.objects.count() == 1
    assert service.add_product(actor=actor, store=store_b, product_id=rice.id).status == SellerCatalogStatus.FORBIDDEN
    assert service.list_catalog(actor=actor, store=store_b).status == SellerCatalogStatus.FORBIDDEN
    assert first.store_product_id == duplicate.store_product_id


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_add_creates_one_row():
    target_store = store()
    actor = owner_for(target_store)
    rice = product()
    barrier, results = threading.Barrier(2), []

    def add():
        try:
            close_old_connections()
            barrier.wait()
            results.append(SellerCatalogService().add_product(actor=actor, store=target_store, product_id=rice.id).status)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=add) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert set(results) == {SellerCatalogStatus.SUCCESS, SellerCatalogStatus.ALREADY_EXISTS}
    assert StoreProduct.objects.count() == 1
