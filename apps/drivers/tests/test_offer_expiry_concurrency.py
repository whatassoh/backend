"""PostgreSQL race validation: acceptance and expiry contend on one offer."""

import threading
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.db import close_old_connections
from django.utils import timezone

from apps.drivers.application.dispatch import DriverMatchingOrchestrator
from apps.drivers.application.matching import (
    DriverMatchingResult,
    DriverMatchingStatus,
    RankedDriverCandidate,
)
from apps.drivers.application.offers import DriverOfferService
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    set_driver_available,
)
from apps.drivers.domain.enums import DriverOfferStatus
from apps.drivers.infrastructure.models import DriverAssignment, DriverOffer
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreOffer


class OneCandidate:
    strategy_version = "CONCURRENCY_V1"

    def __init__(self, driver):
        self.driver = driver

    def find_candidates(self, order):
        return DriverMatchingResult(
            order_id=str(order.id), pickup_store_id=None,
            status=DriverMatchingStatus.OK,
            candidates=(RankedDriverCandidate(
                driver_id=str(self.driver.id), rank=1, distance_meters=100,
                matchability="MATCHABLE", location_age_seconds=0,
            ),),
        )


def setup_offer():
    customer = User.objects.create_user(whatsapp_phone=f"+237688{User.objects.count():06d}")
    driver_user = User.objects.create_user(whatsapp_phone=f"+237687{User.objects.count():06d}")
    driver = activate_driver(create_driver_profile(driver_user))
    driver.last_location = Point(9.7, 4.05, srid=4326)
    driver.last_location_at = timezone.now()
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    set_driver_available(driver)
    create_channel_identity(user=driver_user, channel=MessagingChannel.TELEGRAM, external_user_id=f"race-{driver.id}")
    order = Order.objects.create(
        reference=f"RACE-{Order.objects.count():010d}"[-15:],
        customer=customer, status=OrderStatus.READY_FOR_PICKUP,
    )
    store = Store.objects.create(name=f"Race {Store.objects.count()}", city="Douala", location=Point(9.7, 4.05, srid=4326))
    StoreOffer.objects.create(order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1, strategy_version="TEST", expires_at=timezone.now() + timedelta(hours=1))
    return order, driver


@pytest.mark.django_db(transaction=True)
def test_accept_and_expire_race_has_one_valid_outcome(monkeypatch):
    monkeypatch.setattr("apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *args: None)
    monkeypatch.setattr("apps.drivers.tasks.expire_driver_offer.apply_async", lambda *args, **kwargs: None)

    # Repeat with independent rows: Barrier synchronizes competing DB requests,
    # while service-level SELECT FOR UPDATE chooses the sole valid transition.
    for _ in range(4):
        order, driver = setup_offer()
        DriverMatchingOrchestrator(matching_service=OneCandidate(driver)).start_matching(order.id)
        offer = DriverOffer.objects.get(order=order)
        offer.expires_at = timezone.now()
        offer.save(update_fields=["expires_at", "updated_at"])
        barrier, outcomes = threading.Barrier(2), []

        def accept():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                DriverOfferService().accept_offer(DriverOffer.objects.get(pk=offer.id), actor=driver.user)
                outcomes.append("ACCEPTED")
            except ValueError:
                outcomes.append("STALE")
            finally:
                close_old_connections()

        def expire():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                DriverOfferService().expire_offer(DriverOffer.objects.get(pk=offer.id))
                outcomes.append("EXPIRED")
            except ValueError:
                outcomes.append("STALE")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=accept), threading.Thread(target=expire)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        offer.refresh_from_db()
        assert offer.status in (DriverOfferStatus.ACCEPTED, DriverOfferStatus.EXPIRED)
        assert len(outcomes) == 2
        assert DriverAssignment.objects.filter(offer=offer).count() <= 1
        if offer.status == DriverOfferStatus.ACCEPTED:
            assert DriverAssignment.objects.filter(offer=offer, is_active=True).count() == 1
            order.refresh_from_db()
            assert order.status == OrderStatus.DRIVER_ASSIGNED
        else:
            assert not DriverAssignment.objects.filter(offer=offer).exists()
