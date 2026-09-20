import pytest

from apps.drivers.application.delivery_execution import DeliveryExecutionService, DeliveryMissionAuthorizationError
from apps.drivers.application.availability import DriverAvailabilityService, DriverMatchability
from apps.drivers.tests.test_delivery_execution import assignment
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import OrderDelivered
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def events():
    clear_subscribers()
    yield
    clear_subscribers()


@pytest.mark.django_db(transaction=True)
def test_completion_is_terminal_auditable_evented_and_releases_assignment():
    item, _ = assignment()
    service = DeliveryExecutionService()
    service.confirm_pickup(item.driver.user, assignment_id=item.id)
    service.start_delivery(item.driver.user, assignment_id=item.id)
    received = []
    subscribe(OrderDelivered, received.append)
    result = service.confirm_delivered(item.driver.user, assignment_id=item.id)
    item.order.refresh_from_db()
    item.refresh_from_db()
    assert result.duplicate is False
    assert item.order.status == OrderStatus.DELIVERED
    assert item.order.delivered_at >= item.order.delivery_started_at >= item.order.picked_up_at
    assert item.is_active is False
    assert len(received) == 1
    assert service.confirm_delivered(item.driver.user, assignment_id=item.id).duplicate is True


@pytest.mark.django_db
def test_completion_rejects_wrong_driver_and_skipped_states():
    item, _ = assignment()
    other, _ = assignment()
    service = DeliveryExecutionService()
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.confirm_delivered(item.driver.user, assignment_id=item.id)
    service.confirm_pickup(item.driver.user, assignment_id=item.id)
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.confirm_delivered(other.driver.user, assignment_id=item.id)


@pytest.mark.django_db
def test_delivery_completion_leaves_matchability_derived():
    item, _ = assignment()
    service = DeliveryExecutionService()
    service.confirm_pickup(item.driver.user, assignment_id=item.id)
    service.start_delivery(item.driver.user, assignment_id=item.id)
    service.confirm_delivered(item.driver.user, assignment_id=item.id)
    assert DriverAvailabilityService().get_matchability(item.driver).status != DriverMatchability.ACTIVE_DELIVERY
