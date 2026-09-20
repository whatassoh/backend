from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.drivers.application.delivery_execution import DeliveryExecutionService, DeliveryMissionAuthorizationError
from apps.drivers.application.tracking import DeliveryTrackingService, TrackingLocationStatus
from apps.drivers.tests.test_delivery_execution import assignment, user
from apps.orders.application.services import mark_delivered
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order


@pytest.mark.django_db
def test_customer_tracking_uses_durable_fresh_driver_location_only_in_delivery():
    item, _ = assignment()
    driver = item.driver
    driver.last_location, driver.last_location_at = Point(9.71, 4.06, srid=4326), timezone.now()
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    service = DeliveryExecutionService()
    service.confirm_pickup(driver.user, assignment_id=item.id)
    service.start_delivery(driver.user, assignment_id=item.id)
    snapshot = DeliveryTrackingService().get_customer_tracking(item.order.customer, order_id=item.order_id)
    assert snapshot.order_status == OrderStatus.IN_DELIVERY
    assert snapshot.driver.location_status == TrackingLocationStatus.FRESH
    assert snapshot.driver.latitude == pytest.approx(4.06)


@pytest.mark.django_db
def test_tracking_hides_stale_and_terminal_driver_position_and_rejects_other_customer():
    item, _ = assignment()
    driver = item.driver
    driver.last_location, driver.last_location_at = Point(9.71, 4.06, srid=4326), timezone.now() - timedelta(days=1)
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    service = DeliveryExecutionService()
    service.confirm_pickup(driver.user, assignment_id=item.id)
    service.start_delivery(driver.user, assignment_id=item.id)
    tracking = DeliveryTrackingService()
    stale = tracking.get_customer_tracking(item.order.customer, reference=item.order.reference)
    assert stale.driver.location_status == TrackingLocationStatus.STALE
    assert stale.driver.latitude is None
    with pytest.raises(DeliveryMissionAuthorizationError):
        tracking.get_customer_tracking(user("+237699123456"), order_id=item.order_id)
    mark_delivered(item.order)
    terminal = tracking.get_customer_tracking(item.order.customer, order_id=item.order_id)
    assert terminal.driver.latitude is None and terminal.driver.longitude is None


@pytest.mark.django_db
def test_tracking_before_assignment_is_status_truth_without_driver_location():
    customer = user("+237699123457")
    order = Order.objects.create(reference="ORD-260920-TRK1", customer=customer, status=OrderStatus.PREPARING)
    snapshot = DeliveryTrackingService().get_customer_tracking(customer, order_id=order.id)
    assert snapshot.order_status == OrderStatus.PREPARING
    assert snapshot.driver.assigned is False
    assert snapshot.driver.location_status == TrackingLocationStatus.UNAVAILABLE
