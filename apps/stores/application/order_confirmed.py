"""Business-event bridge from an effective order confirmation to async matching."""

import logging

from apps.orders.domain.events import OrderConfirmed

logger = logging.getLogger(__name__)


def enqueue_store_matching(event: OrderConfirmed) -> None:
    """Runs after the Order transaction commits (the event bus publishes on_commit)."""
    from apps.stores.tasks import start_store_matching_for_order

    logger.info(
        "store_matching_requested",
        extra={
            "event_type": "OrderConfirmed",
            "order_id": str(event.order_id),
            "correlation_id": event.correlation_id,
        },
    )
    from common.async_tasks import enqueue_after_commit

    enqueue_after_commit(
        lambda: start_store_matching_for_order.delay(
            str(event.order_id), event.correlation_id
        ),
        task_name="stores.start_store_matching",
        entity_id=str(event.order_id),
        correlation_id=event.correlation_id,
    )
