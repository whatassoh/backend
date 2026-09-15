import pytest
from django.contrib.gis.geos import Point
from django.db import transaction

from apps.audit.domain.enums import AuditEventType
from apps.audit.infrastructure.models import AuditEvent
from apps.catalog.domain.enums import ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import (
    add_order_item,
    cancel_order,
    confirm_order,
    create_order_draft,
    fail_order,
    retry_failed_order,
    set_delivery_location,
    start_store_matching,
)
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderFailureCode,
    OrderStatus,
)
from apps.orders.domain.events import (
    OrderCancelled,
    OrderConfirmed,
    OrderCreated,
    OrderFailed,
    OrderRetryRequested,
)
from apps.orders.infrastructure.models import Order
from common.events import clear_subscribers, subscribe

BONAMOUSSADI = Point(9.7357, 4.0907, srid=4326)


@pytest.fixture(autouse=True)
def clear_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_confirmable_order(user: User) -> Order:
    order = create_order_draft(customer=user)
    add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG)
    set_delivery_location(order=order, point=BONAMOUSSADI)
    return order


@pytest.mark.django_db(transaction=True)
def test_order_created_is_published_after_commit_not_before():
    user = create_user()
    received = []
    subscribe(OrderCreated, received.append)

    with transaction.atomic():
        order = create_order_draft(
            customer=user,
            correlation_id="corr-created",
        )
        assert received == []

    assert len(received) == 1
    event = received[0]
    assert event.order_id == order.id
    assert event.customer_id == user.id
    assert event.correlation_id == "corr-created"


@pytest.mark.django_db(transaction=True)
def test_order_confirmed_publishes_after_audit_commit_with_payload():
    user = create_user()
    order = create_confirmable_order(user)
    received = []
    subscribe(OrderConfirmed, received.append)

    confirmed = confirm_order(order, correlation_id="corr-confirmed")

    assert confirmed.status == OrderStatus.CONFIRMED
    assert AuditEvent.objects.filter(
        entity_id=order.id,
        event_type=AuditEventType.ORDER_CONFIRMED,
    ).exists()
    assert len(received) == 1
    assert received[0].order_id == order.id
    assert received[0].customer_id == user.id
    assert received[0].correlation_id == "corr-confirmed"


@pytest.mark.django_db(transaction=True)
def test_rollback_prevents_order_audit_and_domain_event():
    user = create_user()
    received = []
    subscribe(OrderCreated, received.append)

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            create_order_draft(customer=user)
            raise RuntimeError("rollback")

    assert Order.objects.count() == 0
    assert AuditEvent.objects.count() == 0
    assert received == []


@pytest.mark.django_db(transaction=True)
def test_repeated_confirm_noop_does_not_publish_twice():
    user = create_user()
    order = create_confirmable_order(user)
    received = []
    subscribe(OrderConfirmed, received.append)

    confirmed = confirm_order(order)
    confirm_order(confirmed)

    assert len(received) == 1


@pytest.mark.django_db(transaction=True)
def test_order_cancelled_event_only_for_effective_transition():
    user = create_user()
    order = confirm_order(create_confirmable_order(user))
    received = []
    subscribe(OrderCancelled, received.append)

    cancelled = cancel_order(
        order,
        OrderCancellationCode.CUSTOMER_REQUEST,
        correlation_id="corr-cancelled",
    )

    assert cancelled.status == OrderStatus.CANCELLED
    assert len(received) == 1
    assert received[0].order_id == order.id
    assert received[0].customer_id == user.id
    assert received[0].cancellation_code == OrderCancellationCode.CUSTOMER_REQUEST
    assert received[0].correlation_id == "corr-cancelled"


@pytest.mark.django_db(transaction=True)
def test_order_failed_event_payload():
    user = create_user()
    order = start_store_matching(confirm_order(create_confirmable_order(user)))
    received = []
    subscribe(OrderFailed, received.append)

    failed = fail_order(
        order,
        OrderFailureCode.NO_STORE_AVAILABLE,
        correlation_id="corr-failed",
    )

    assert failed.status == OrderStatus.FAILED
    assert len(received) == 1
    assert received[0].order_id == order.id
    assert received[0].failure_code == OrderFailureCode.NO_STORE_AVAILABLE
    assert received[0].failed_from_status == OrderStatus.MATCHING_STORES
    assert received[0].correlation_id == "corr-failed"


@pytest.mark.django_db(transaction=True)
def test_order_retry_requested_preserves_previous_failure_code():
    user = create_user()
    order = start_store_matching(confirm_order(create_confirmable_order(user)))
    failed = fail_order(order, OrderFailureCode.NO_STORE_AVAILABLE)
    received = []
    subscribe(OrderRetryRequested, received.append)

    retried = retry_failed_order(failed, correlation_id="corr-retry")

    assert retried.status == OrderStatus.MATCHING_STORES
    assert retried.failure_code is None
    assert len(received) == 1
    assert received[0].order_id == order.id
    assert received[0].failure_code == OrderFailureCode.NO_STORE_AVAILABLE
    assert received[0].retry_target_status == OrderStatus.MATCHING_STORES
    assert received[0].correlation_id == "corr-retry"


@pytest.mark.django_db(transaction=True)
def test_handler_failure_does_not_turn_order_failed():
    user = create_user()
    order = create_confirmable_order(user)

    def failing_handler(event):
        raise RuntimeError("handler failed")

    subscribe(OrderConfirmed, failing_handler)

    with pytest.raises(RuntimeError):
        confirm_order(order)

    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert order.failure_code is None
