from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.drivers.application.delivery_execution import (
    DeliveryExecutionService,
    DeliveryMissionAuthorizationError,
    DeliveryMissionNotFound,
)
from apps.drivers.application.services import activate_driver, create_driver_profile
from apps.drivers.infrastructure.models import DriverAssignment, DriverOffer
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.application.services import mark_delivered, mark_in_delivery, mark_picked_up
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreOffer


def user(phone):
    return User.objects.create_user(whatsapp_phone=phone)


def assignment(*, driver=None, status=OrderStatus.DRIVER_ASSIGNED):
    driver = driver or activate_driver(create_driver_profile(user(f"+237691{User.objects.count():06d}")))
    customer = user(f"+237690{User.objects.count():06d}")
    order = Order.objects.create(
        reference=f"ORD-260920-{Order.objects.count():04d}"[-15:], customer=customer,
        status=status, delivery_location=Point(9.7357, 4.0907, srid=4326),
        delivery_address_label="Bonamoussadi, près du rond-point",
        delivery_instructions="Appeler en arrivant",
    )
    store = Store.objects.create(
        name="Boutique gagnante", city="Douala", address_label="Akwa, Rue Joss",
        location=Point(9.70, 4.05, srid=4326),
    )
    StoreOffer.objects.create(order=order, store=store, status=StoreOfferStatus.ACCEPTED,
        rank=1, strategy_version="TEST", expires_at=timezone.now() + timedelta(hours=1))
    offer = DriverOffer.objects.create(order=order, driver=driver, status="ACCEPTED", rank=1,
        strategy_version="TEST", expires_at=timezone.now() + timedelta(hours=1))
    return DriverAssignment.objects.create(order=order, driver=driver, offer=offer), store


@pytest.mark.django_db
def test_resumes_db_backed_assigned_mission_with_winning_store_and_destination():
    item, store = assignment()
    mission = DeliveryExecutionService().get_active_mission(item.driver.user)
    assert mission.assignment_id == str(item.id)
    assert mission.order_id == str(item.order_id)
    assert mission.order_status == OrderStatus.DRIVER_ASSIGNED
    assert mission.pickup_store_id == str(store.id)
    assert mission.pickup.display_name == "Boutique gagnante"
    assert mission.pickup.address_label == "Akwa, Rue Joss"
    assert mission.destination.address_label == "Bonamoussadi, près du rond-point"
    assert mission.coordinates_available is True
    assert mission.delivery_instructions == "Appeler en arrivant"
    assert mission.allowed_actions == ("delivery.confirm_pickup",)


@pytest.mark.django_db
def test_linked_whatsapp_and_telegram_identities_resolve_same_user_mission():
    item, _ = assignment()
    wa = create_channel_identity(user=item.driver.user, channel=MessagingChannel.WHATSAPP, external_user_id="wa-driver")
    tg = create_channel_identity(user=item.driver.user, channel=MessagingChannel.TELEGRAM, external_user_id="tg-driver")
    service = DeliveryExecutionService()
    assert service.get_active_mission(wa).assignment_id == service.get_active_mission(tg).assignment_id == str(item.id)


@pytest.mark.django_db
def test_driver_cannot_access_another_drivers_assignment_or_order_id():
    first, _ = assignment()
    second, _ = assignment()
    service = DeliveryExecutionService()
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.get_mission(first.driver.user, assignment_id=second.id)
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.get_mission(first.driver.user, order_id=second.order_id)


@pytest.mark.django_db
def test_inactive_or_terminal_assignment_is_not_an_active_mission_and_releases_constraint():
    item, _ = assignment()
    mark_picked_up(item.order)
    mark_in_delivery(item.order)
    mark_delivered(item.order)
    item.refresh_from_db()
    assert item.is_active is False
    with pytest.raises(DeliveryMissionNotFound):
        DeliveryExecutionService().get_active_mission(item.driver.user)
    # The partial PostgreSQL uniqueness constraint can now permit a later mission.
    another, _ = assignment(driver=item.driver)
    assert another.is_active is True


@pytest.mark.django_db
def test_allowed_actions_follow_order_state_without_any_llm_or_conversation_context():
    item, _ = assignment(status=OrderStatus.PICKED_UP)
    mission = DeliveryExecutionService().get_active_mission(item.driver.user)
    assert mission.allowed_actions == ("delivery.start_delivery",)
    mark_in_delivery(item.order)
    mission = DeliveryExecutionService().get_active_mission(item.driver.user)
    assert mission.allowed_actions == ("delivery.confirm_delivered", "delivery.report_issue")
