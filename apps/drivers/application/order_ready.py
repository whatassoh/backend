"""Event bridge from a ready store order to asynchronous driver matching."""

from apps.orders.domain.events import OrderReadyForPickup
from common.async_tasks import enqueue_after_commit


def enqueue_driver_matching(event: OrderReadyForPickup):
    from apps.drivers.tasks import start_driver_matching_for_order

    enqueue_after_commit(
        lambda: start_driver_matching_for_order.delay(
            str(event.order_id), event.correlation_id
        ),
        task_name="drivers.start_driver_matching",
        entity_id=str(event.order_id),
        correlation_id=event.correlation_id,
    )
