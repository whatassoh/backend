import pytest
from django.contrib.gis.geos import Point
from django.db import transaction

from apps.audit.application.selectors import (
    get_audit_events_by_correlation_id,
    get_order_audit_history,
)
from apps.audit.application.services import record_audit_event
from apps.audit.domain.enums import AuditActorType, AuditEntityType, AuditEventType
from apps.audit.domain.exceptions import AuditEventImmutableError
from apps.audit.infrastructure.models import AuditEvent
from apps.catalog.domain.enums import ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import (
    add_order_item,
    cancel_order,
    confirm_order,
    create_order_draft,
    fail_order,
    remove_order_item,
    retry_failed_order,
    set_delivery_location,
    start_store_matching,
    update_order_item,
)
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderFailureCode,
    OrderStatus,
)
from apps.orders.infrastructure.models import Order

BONAMOUSSADI = Point(9.7357, 4.0907, srid=4326)


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_confirmable_order(user: User) -> Order:
    order = create_order_draft(
        customer=user, actor=user, actor_type=AuditActorType.USER
    )
    add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG)
    set_delivery_location(order=order, point=BONAMOUSSADI)
    return order


@pytest.mark.django_db
def test_create_order_draft_records_audit_event():
    user = create_user()

    order = create_order_draft(
        customer=user,
        actor=user,
        actor_type=AuditActorType.USER,
        correlation_id="corr-create",
    )
    event = get_order_audit_history(order).get(event_type=AuditEventType.ORDER_CREATED)

    assert event.actor == user
    assert event.actor_type == AuditActorType.USER
    assert event.entity_type == AuditEntityType.ORDER
    assert event.entity_id == order.id
    assert event.new_state == OrderStatus.DRAFT
    assert event.metadata["reference"] == order.reference
    assert event.correlation_id == "corr-create"


@pytest.mark.django_db
def test_order_item_add_update_remove_are_audited():
    user = create_user()
    order = create_order_draft(customer=user)

    item = add_order_item(
        order=order,
        requested_name="Riz parfumé",
        quantity=5,
        unit=ProductUnit.KG,
    )
    update_order_item(item, notes="Bonne qualité")
    remove_order_item(item)

    events = list(get_order_audit_history(order))
    event_types = [event.event_type for event in events]

    assert AuditEventType.ORDER_ITEM_ADDED in event_types
    assert AuditEventType.ORDER_ITEM_UPDATED in event_types
    assert AuditEventType.ORDER_ITEM_REMOVED in event_types
    added = next(
        event for event in events if event.event_type == AuditEventType.ORDER_ITEM_ADDED
    )
    assert added.metadata["requested_name"] == "Riz parfumé"
    assert added.metadata["quantity"] == "5"
    assert added.metadata["unit"] == ProductUnit.KG


@pytest.mark.django_db
def test_confirm_order_records_state_change_audit_event():
    user = create_user()
    order = create_confirmable_order(user)

    confirmed = confirm_order(
        order,
        actor=user,
        actor_type=AuditActorType.USER,
        correlation_id="corr-confirm",
    )
    event = get_order_audit_history(confirmed).get(
        event_type=AuditEventType.ORDER_CONFIRMED
    )

    assert event.previous_state == OrderStatus.DRAFT
    assert event.new_state == OrderStatus.CONFIRMED
    assert event.actor == user
    assert event.correlation_id == "corr-confirm"


@pytest.mark.django_db
def test_cancel_order_records_metadata_and_actor():
    admin = create_user("+237699887767")
    user = create_user()
    order = confirm_order(create_confirmable_order(user))

    cancelled = cancel_order(
        order,
        OrderCancellationCode.ADMIN_REQUEST,
        reason="Fraud review",
        actor=admin,
        actor_type=AuditActorType.ADMIN,
    )
    event = get_order_audit_history(cancelled).get(
        event_type=AuditEventType.ORDER_CANCELLED
    )

    assert event.previous_state == OrderStatus.CONFIRMED
    assert event.new_state == OrderStatus.CANCELLED
    assert event.actor == admin
    assert event.actor_type == AuditActorType.ADMIN
    assert event.metadata["cancellation_code"] == OrderCancellationCode.ADMIN_REQUEST
    assert event.metadata["reason"] == "Fraud review"


@pytest.mark.django_db
def test_failed_and_retry_audit_preserves_failure_context():
    user = create_user()
    order = create_confirmable_order(user)
    order = confirm_order(order)
    order = start_store_matching(order)

    failed = fail_order(
        order,
        OrderFailureCode.NO_STORE_AVAILABLE,
        reason="No available store",
    )
    retried = retry_failed_order(failed)

    failed_event = get_order_audit_history(retried).get(
        event_type=AuditEventType.ORDER_FAILED
    )
    retry_event = get_order_audit_history(retried).get(
        event_type=AuditEventType.ORDER_RETRY_REQUESTED
    )

    assert failed_event.previous_state == OrderStatus.MATCHING_STORES
    assert failed_event.new_state == OrderStatus.FAILED
    assert failed_event.metadata["failure_code"] == OrderFailureCode.NO_STORE_AVAILABLE
    assert retry_event.previous_state == OrderStatus.FAILED
    assert retry_event.new_state == OrderStatus.MATCHING_STORES
    assert retry_event.metadata["failure_code"] == OrderFailureCode.NO_STORE_AVAILABLE
    assert retry_event.metadata["failed_from_status"] == OrderStatus.MATCHING_STORES
    assert retried.failure_code is None


@pytest.mark.django_db
def test_system_actor_and_correlation_id_selectors():
    user = create_user()
    order = create_order_draft(
        customer=user,
        actor_type=AuditActorType.SYSTEM,
        correlation_id="corr-system",
    )

    event = get_audit_events_by_correlation_id("corr-system").get()

    assert event.entity_id == order.id
    assert event.actor is None
    assert event.actor_type == AuditActorType.SYSTEM


@pytest.mark.django_db
def test_audit_event_is_append_only():
    user = create_user()
    event = record_audit_event(
        actor=user,
        actor_type=AuditActorType.USER,
        entity_type=AuditEntityType.ORDER,
        entity_id=user.id,
        event_type=AuditEventType.ORDER_CREATED,
    )

    event.metadata = {"changed": True}
    with pytest.raises(AuditEventImmutableError):
        event.save()
    with pytest.raises(AuditEventImmutableError):
        event.delete()
    with pytest.raises(AuditEventImmutableError):
        AuditEvent.objects.filter(id=event.id).delete()


@pytest.mark.django_db
def test_order_and_audit_rollback_together():
    user = create_user()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            create_order_draft(customer=user)
            raise RuntimeError("rollback")

    assert Order.objects.count() == 0
    assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
def test_audit_failure_rolls_back_business_change(monkeypatch):
    user = create_user()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "apps.orders.application.services.record_audit_event", fail_audit
    )

    with pytest.raises(RuntimeError):
        create_order_draft(customer=user)

    assert Order.objects.count() == 0
    assert AuditEvent.objects.count() == 0
