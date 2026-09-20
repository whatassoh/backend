import pytest

from apps.drivers.application.delivery_execution import (
    DeliveryExecutionService,
    DeliveryMissionAuthorizationError,
)
from apps.drivers.application.availability import DriverAvailabilityService, DriverMatchability
from apps.drivers.tests.test_delivery_execution import assignment
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel, NotificationKind
from apps.messaging.infrastructure.models import OperationalNotification
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import DeliveryStarted
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def events():
    clear_subscribers()
    yield
    clear_subscribers()


@pytest.mark.django_db(transaction=True)
def test_explicit_start_persists_timestamp_event_notification_and_active_assignment(monkeypatch):
    item, _ = assignment()
    create_channel_identity(user=item.order.customer, channel=MessagingChannel.WHATSAPP, external_user_id="start-customer")
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", lambda *args: None)
    service = DeliveryExecutionService()
    service.confirm_pickup(item.driver.user, assignment_id=item.id)
    received = []
    subscribe(DeliveryStarted, received.append)

    result = service.start_delivery(item.driver.user, assignment_id=item.id)
    item.order.refresh_from_db()
    item.refresh_from_db()
    assert result.duplicate is False
    assert item.order.status == OrderStatus.IN_DELIVERY
    assert item.order.delivery_started_at >= item.order.picked_up_at
    assert item.is_active is True
    assert len(received) == 1
    assert OperationalNotification.objects.filter(kind=NotificationKind.ORDER_IN_DELIVERY).count() == 1


@pytest.mark.django_db
def test_start_is_idempotent_and_cannot_skip_pickup_or_cross_driver():
    item, _ = assignment()
    other, _ = assignment()
    service = DeliveryExecutionService()
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.start_delivery(item.driver.user, assignment_id=item.id)
    service.confirm_pickup(item.driver.user, assignment_id=item.id)
    assert service.start_delivery(item.driver.user, assignment_id=item.id).duplicate is False
    assert service.start_delivery(item.driver.user, assignment_id=item.id).duplicate is True
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.start_delivery(item.driver.user, assignment_id=other.id)


@pytest.mark.django_db
def test_channel_switch_and_offline_do_not_end_active_in_delivery_mission():
    item, _ = assignment()
    wa = create_channel_identity(user=item.driver.user, channel=MessagingChannel.WHATSAPP, external_user_id="start-wa")
    tg = create_channel_identity(user=item.driver.user, channel=MessagingChannel.TELEGRAM, external_user_id="start-tg")
    service = DeliveryExecutionService()
    service.confirm_pickup(wa, assignment_id=item.id)
    assert service.start_delivery(tg, assignment_id=item.id).duplicate is False
    DriverAvailabilityService().set_offline(item.driver, item.driver.user)
    item.refresh_from_db()
    assert item.is_active is True
    assert DriverAvailabilityService().get_matchability(item.driver).status == DriverMatchability.ACTIVE_DELIVERY
