from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.test import override_settings
from django.utils import timezone

from apps.drivers.application.matching import DriverMatchingService, DriverMatchingStatus
from apps.drivers.application.services import activate_driver, create_driver_profile, set_driver_available
from apps.identity.infrastructure.models import User
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreOffer


class Geo:
    def __init__(self, rows=None, failure=False): self.rows, self.failure = rows or [], failure
    def nearby(self, **kwargs):
        if self.failure: raise RuntimeError("redis unavailable")
        return self.rows


def user(phone): return User.objects.create_user(whatsapp_phone=phone)


def ready_order():
    customer = user(f"+237690{User.objects.count():06d}")
    order = Order.objects.create(reference=f"ORD-260920-{Order.objects.count():04d}"[-15:], customer=customer, status=OrderStatus.READY_FOR_PICKUP)
    store = Store.objects.create(name="Pickup", city="Douala", location=Point(9.70, 4.05, srid=4326))
    StoreOffer.objects.create(order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1, strategy_version="TEST", expires_at=timezone.now() + timedelta(hours=1))
    return order


def available_driver(phone):
    driver = activate_driver(create_driver_profile(user(phone)))
    driver.last_location, driver.last_location_at = Point(9.70, 4.05, srid=4326), timezone.now()
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    set_driver_available(driver)
    return driver


@pytest.mark.django_db
def test_ready_order_ranks_revalidated_candidates_without_mutating_order():
    order = ready_order()
    near, far = available_driver("+237691000001"), available_driver("+237691000002")
    result = DriverMatchingService(geo_index=Geo([(str(far.id), 2000), (str(near.id), 1000)])).find_candidates(order)
    assert result.status == DriverMatchingStatus.OK
    assert [candidate.driver_id for candidate in result.candidates] == [str(near.id), str(far.id)]
    assert order.status == OrderStatus.READY_FOR_PICKUP


@pytest.mark.django_db
def test_stale_redis_member_is_excluded_and_technical_failure_is_not_no_candidate():
    order = ready_order(); driver = available_driver("+237691000003")
    driver.last_location_at = timezone.now() - timedelta(days=1); driver.save(update_fields=["last_location_at", "updated_at"])
    result = DriverMatchingService(geo_index=Geo([(str(driver.id), 10)])).find_candidates(order)
    assert result.status == DriverMatchingStatus.OK and result.candidate_count == 0
    assert DriverMatchingService(geo_index=Geo(failure=True)).find_candidates(order).status == DriverMatchingStatus.TECHNICAL_UNAVAILABLE


@pytest.mark.django_db
def test_wrong_order_state_is_controlled_without_geo_query():
    order = ready_order(); order.status = OrderStatus.PREPARING; order.save(update_fields=["status", "updated_at"])
    assert DriverMatchingService(geo_index=Geo()).find_candidates(order).status == DriverMatchingStatus.INVALID_ORDER_STATE
