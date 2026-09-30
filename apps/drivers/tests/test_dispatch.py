from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.drivers.application.dispatch import DriverMatchingOrchestrator
from apps.drivers.application.matching import (
    DriverMatchingResult,
    DriverMatchingStatus,
    RankedDriverCandidate,
)
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    set_driver_available,
)
from apps.drivers.domain.enums import DriverMatchingAttemptStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import (
    DriverAssignment,
    DriverOffer,
    DriverOfferNotification,
)
from apps.drivers.tasks import _expire_driver_offer
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreOffer


class Matching:
    strategy_version = "PROXIMITY_V1"
    def __init__(self, candidates): self.candidates = candidates
    def find_candidates(self, order):
        return DriverMatchingResult(order_id=str(order.id), pickup_store_id=None, status=DriverMatchingStatus.OK, candidates=tuple(self.candidates))


def make_user(phone): return User.objects.create_user(whatsapp_phone=phone)


def make_driver(phone, *, identity=True):
    user = make_user(phone)
    driver = activate_driver(create_driver_profile(user))
    driver.last_location, driver.last_location_at = Point(9.7, 4.05, srid=4326), timezone.now()
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    set_driver_available(driver)
    if identity:
        create_channel_identity(user=user, channel=MessagingChannel.TELEGRAM, external_user_id=f"tg-{user.id}")
    return driver


def make_order():
    customer = make_user(f"+237690{User.objects.count():06d}")
    order = Order.objects.create(reference=f"ORD-260920-{Order.objects.count():04d}"[-15:], customer=customer, status=OrderStatus.READY_FOR_PICKUP)
    store = Store.objects.create(name="Pickup", city="Douala", location=Point(9.7, 4.05, srid=4326))
    StoreOffer.objects.create(order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1, strategy_version="TEST", expires_at=timezone.now() + timedelta(hours=1))
    return order


def candidate(driver, rank):
    return RankedDriverCandidate(driver_id=str(driver.id), rank=rank, distance_meters=rank * 1000, matchability="MATCHABLE", location_age_seconds=0)


@pytest.mark.django_db
def test_offer_is_persisted_with_notification_before_any_provider_delivery(monkeypatch):
    order, driver = make_order(), make_driver("+237691000001")
    monkeypatch.setattr("apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *args: None)
    attempt = DriverMatchingOrchestrator(matching_service=Matching([candidate(driver, 1)])).start_matching(order.id)
    offer = DriverOffer.objects.get(order=order)
    notification = DriverOfferNotification.objects.get(offer=offer)
    assert offer.status == DriverOfferStatus.PENDING
    assert notification.status == "PENDING"
    assert attempt.status == DriverMatchingAttemptStatus.RUNNING


@pytest.mark.django_db
def test_reject_advances_once_to_next_candidate(monkeypatch):
    first, second, order = make_driver("+237691000002"), make_driver("+237691000003"), make_order()
    monkeypatch.setattr(
        "apps.drivers.tasks.deliver_driver_offer_notification.delay",
        lambda *_args: None,
    )
    app = DriverMatchingOrchestrator(matching_service=Matching([candidate(first, 1), candidate(second, 2)]))
    app.start_matching(order.id)
    offer_a = DriverOffer.objects.get(order=order, driver=first)
    app.handle_reject(offer_a, actor=first.user)
    offer_a.refresh_from_db()
    offer_b = DriverOffer.objects.get(order=order, driver=second)
    assert offer_a.status == DriverOfferStatus.REJECTED
    assert offer_b.status == DriverOfferStatus.PENDING
    assert DriverOfferNotification.objects.filter(offer=offer_b).count() == 1
    assert app.handle_reject(offer_a, actor=first.user).id == offer_b.id
    assert DriverOffer.objects.filter(order=order).count() == 2
    assert DriverOfferNotification.objects.filter(offer=offer_b).count() == 1


@pytest.mark.django_db
def test_accept_assigns_exactly_once_and_driver_becomes_busy(monkeypatch):
    order, driver = make_order(), make_driver("+237691000004")
    monkeypatch.setattr("apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *args: None)
    app = DriverMatchingOrchestrator(matching_service=Matching([candidate(driver, 1)]))
    app.start_matching(order.id)
    offer = DriverOffer.objects.get(order=order)
    accepted = app.handle_accept(offer, actor=driver.user)
    order.refresh_from_db()
    assert accepted.status == DriverOfferStatus.ACCEPTED
    assert order.status == OrderStatus.DRIVER_ASSIGNED
    assert DriverAssignment.objects.filter(order=order, driver=driver, is_active=True).count() == 1
    assert app.handle_accept(offer, actor=driver.user).id == offer.id


@pytest.mark.django_db
def test_accept_rejects_foreign_expired_cancelled_and_now_busy_driver(monkeypatch):
    order = make_order()
    offered = make_driver("+237691000104")
    foreign = make_driver("+237691000105")
    monkeypatch.setattr(
        "apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *_args: None
    )
    app = DriverMatchingOrchestrator(
        matching_service=Matching([candidate(offered, 1)])
    )
    app.start_matching(order.id)
    offer = DriverOffer.objects.get(order=order)

    with pytest.raises(PermissionError):
        app.handle_accept(offer, actor=foreign.user)
    assert not DriverAssignment.objects.filter(order=order).exists()

    offer.expires_at = timezone.now() - timedelta(seconds=1)
    offer.save(update_fields=["expires_at", "updated_at"])
    with pytest.raises(ValueError, match="OFFER_EXPIRED"):
        app.handle_accept(offer, actor=offered.user)
    assert not DriverAssignment.objects.filter(order=order).exists()

    offer.expires_at = timezone.now() + timedelta(minutes=5)
    offer.save(update_fields=["expires_at", "updated_at"])
    other_order = make_order()
    other_order.status = OrderStatus.MATCHING_DRIVERS
    other_order.save(update_fields=["status", "updated_at"])
    other_offer = DriverOffer.objects.create(
        order=other_order,
        driver=offered,
        status=DriverOfferStatus.ACCEPTED,
        rank=1,
        strategy_version="TEST",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    DriverAssignment.objects.create(
        order=other_order, driver=offered, offer=other_offer
    )
    with pytest.raises(ValueError, match="no longer matchable"):
        app.handle_accept(offer, actor=offered.user)
    assert not DriverAssignment.objects.filter(order=order).exists()

    DriverAssignment.objects.filter(order=other_order).update(is_active=False)
    order.status = OrderStatus.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    with pytest.raises(ValueError, match="no longer assignable"):
        app.handle_accept(offer, actor=offered.user)
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED
    assert not DriverAssignment.objects.filter(order=order).exists()


@pytest.mark.django_db
def test_no_recipient_cancels_offer_without_calling_provider(monkeypatch):
    order, driver = make_order(), make_driver("+237691000005", identity=False)
    app = DriverMatchingOrchestrator(matching_service=Matching([candidate(driver, 1)]))
    app.start_matching(order.id)
    assert DriverOffer.objects.get(order=order).status == DriverOfferStatus.CANCELLED


@pytest.mark.django_db
def test_driver_expiry_uses_deadline_and_duplicate_is_noop(monkeypatch):
    order, driver = make_order(), make_driver("+237691000006")
    monkeypatch.setattr("apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *args: None)
    DriverMatchingOrchestrator(matching_service=Matching([candidate(driver, 1)])).start_matching(order.id)
    offer = DriverOffer.objects.get(order=order)
    offer.expires_at = timezone.now()
    offer.save(update_fields=["expires_at", "updated_at"])
    assert _expire_driver_offer(offer.id)["status"] == "EXPIRED"
    assert _expire_driver_offer(offer.id)["status"] == "NOOP"
    offer.refresh_from_db()
    assert offer.status == DriverOfferStatus.EXPIRED


@pytest.mark.django_db
def test_timeout_advances_once_then_exhaustion_fails_order(monkeypatch):
    first = make_driver("+237691000206")
    second = make_driver("+237691000207")
    order = make_order()
    monkeypatch.setattr(
        "apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *_args: None
    )
    app = DriverMatchingOrchestrator(
        matching_service=Matching([candidate(first, 1), candidate(second, 2)])
    )
    app.start_matching(order.id)
    first_offer = DriverOffer.objects.get(order=order, driver=first)
    first_offer.expires_at = timezone.now()
    first_offer.save(update_fields=["expires_at", "updated_at"])

    assert _expire_driver_offer(first_offer.id)["status"] == "EXPIRED"
    second_offer = DriverOffer.objects.get(order=order, driver=second)
    assert second_offer.status == DriverOfferStatus.PENDING
    assert _expire_driver_offer(first_offer.id)["status"] == "NOOP"
    assert DriverOffer.objects.filter(order=order, driver=second).count() == 1

    second_offer.expires_at = timezone.now()
    second_offer.save(update_fields=["expires_at", "updated_at"])
    assert _expire_driver_offer(second_offer.id)["status"] == "EXPIRED"
    order.refresh_from_db()
    assert order.driver_matching_attempt.status == DriverMatchingAttemptStatus.EXHAUSTED
    assert order.status == OrderStatus.FAILED
    assert order.failure_code == "NO_DRIVER_AVAILABLE"
