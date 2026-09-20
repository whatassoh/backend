from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.identity.infrastructure.models import User
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreOffer
from apps.stores.tasks import _expire_store_offer, sweep_expired_store_offers


def make_offer(*, expires_at):
    customer = User.objects.create_user(whatsapp_phone=f"+237677{User.objects.count():06d}")
    order = Order.objects.create(reference=f"EXP-{Order.objects.count():010d}"[-15:], customer=customer, status=OrderStatus.MATCHING_STORES)
    store = Store.objects.create(name=f"Store {Store.objects.count()}", city="Douala", location=Point(9.7, 4.05, srid=4326))
    return StoreOffer.objects.create(order=order, store=store, rank=1, strategy_version="TEST", expires_at=expires_at)


@pytest.mark.django_db
def test_store_expiry_uses_database_boundary_and_is_idempotent():
    offer = make_offer(expires_at=timezone.now())
    assert _expire_store_offer(offer.id)["status"] == "EXPIRED"
    assert _expire_store_offer(offer.id)["status"] == "NOOP"
    offer.refresh_from_db()
    assert offer.status == StoreOfferStatus.EXPIRED


@pytest.mark.django_db
def test_store_expiry_before_deadline_is_noop_and_sweep_recovers_overdue(settings):
    early = make_offer(expires_at=timezone.now() + timedelta(minutes=1))
    overdue = make_offer(expires_at=timezone.now() - timedelta(seconds=1))
    assert _expire_store_offer(early.id)["status"] == "NOOP"
    settings.OFFER_EXPIRY_SWEEP_BATCH_SIZE = 1
    result = sweep_expired_store_offers.run()
    assert result == {"found": 1, "expired": 1}
    overdue.refresh_from_db()
    assert overdue.status == StoreOfferStatus.EXPIRED
