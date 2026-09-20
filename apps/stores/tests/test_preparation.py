import pytest
from django.contrib.gis.geos import Point

from apps.identity.infrastructure.models import User
from apps.orders.application.services import create_order_draft
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import OrderReadyForPickup
from apps.stores.application.preparation import StorePreparationResult, StorePreparationService
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole, StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def clear_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


def accepted_order():
    actor = User.objects.create_user(whatsapp_phone=f"+237677{User.objects.count():06d}")
    customer = User.objects.create_user(whatsapp_phone=f"+237688{User.objects.count():06d}")
    store = create_store(name="Prep store", city="Douala", location=Point(9.7, 4.05, srid=4326))
    add_store_member(store, actor, StoreMembershipRole.OWNER)
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.STORE_ACCEPTED
    order.save(update_fields=["status", "updated_at"])
    StoreOffer.objects.create(order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1, strategy_version="test", selection_snapshot={}, expires_at=order.created_at)
    return actor, order


@pytest.mark.django_db(transaction=True)
def test_authorized_preparation_uses_existing_state_machine_and_emits_once():
    actor, order = accepted_order()
    received = []
    subscribe(OrderReadyForPickup, received.append)
    service = StorePreparationService()
    assert service.start_preparation(actor=actor, order_id=order.id) == StorePreparationResult.STARTED
    assert service.start_preparation(actor=actor, order_id=order.id) == StorePreparationResult.ALREADY_PREPARING
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.READY
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.ALREADY_READY
    order.refresh_from_db()
    assert order.status == OrderStatus.READY_FOR_PICKUP
    assert len(received) == 1


@pytest.mark.django_db
def test_preparation_rejects_non_member_and_skips_direct_ready():
    actor, order = accepted_order()
    outsider = User.objects.create_user(whatsapp_phone=f"+237699{User.objects.count():06d}")
    service = StorePreparationService()
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.NOT_AVAILABLE
    assert service.start_preparation(actor=outsider, order_id=order.id) == StorePreparationResult.NOT_AUTHORIZED
