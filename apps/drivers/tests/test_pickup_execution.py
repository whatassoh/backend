import pytest

from apps.audit.infrastructure.models import AuditEvent
from apps.drivers.application.delivery_execution import (
    DeliveryExecutionService,
    DeliveryMissionAuthorizationError,
)
from apps.drivers.tests.test_delivery_execution import assignment
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel, NotificationKind
from apps.messaging.infrastructure.models import OperationalNotification
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import OrderPickedUp
from apps.stores.domain.enums import StoreMembershipRole, StoreMembershipStatus
from apps.stores.infrastructure.models import StoreMembership
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def events():
    clear_subscribers()
    yield
    clear_subscribers()


@pytest.mark.django_db(transaction=True)
def test_pickup_is_persisted_audited_notified_and_emits_one_event(monkeypatch):
    item, store = assignment()
    create_channel_identity(user=item.order.customer, channel=MessagingChannel.WHATSAPP, external_user_id="customer")
    seller = User.objects.create_user(whatsapp_phone="+237699888777")
    create_channel_identity(user=seller, channel=MessagingChannel.TELEGRAM, external_user_id="seller")
    StoreMembership.objects.create(store=store, user=seller, role=StoreMembershipRole.OWNER, status=StoreMembershipStatus.ACTIVE)
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", lambda *args: None)
    received = []
    subscribe(OrderPickedUp, received.append)

    result = DeliveryExecutionService().confirm_pickup(item.driver.user, assignment_id=item.id)
    item.order.refresh_from_db()
    assert result.duplicate is False
    assert item.order.status == OrderStatus.PICKED_UP
    assert item.order.picked_up_at is not None
    assert len(received) == 1 and received[0].assignment_id == item.id
    assert AuditEvent.objects.filter(entity_id=item.order_id, new_state=OrderStatus.PICKED_UP).count() == 1
    assert OperationalNotification.objects.filter(kind=NotificationKind.ORDER_PICKED_UP).count() == 2


@pytest.mark.django_db
def test_pickup_is_idempotent_and_foreign_or_forged_assignment_is_rejected():
    item, _ = assignment()
    other, _ = assignment()
    service = DeliveryExecutionService()
    assert service.confirm_pickup(item.driver.user, assignment_id=item.id).duplicate is False
    assert service.confirm_pickup(item.driver.user, assignment_id=item.id).duplicate is True
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.confirm_pickup(item.driver.user, assignment_id=other.id)
    with pytest.raises(DeliveryMissionAuthorizationError):
        service.confirm_pickup(item.driver.user, assignment_id=other.id, order_id=item.order_id)


@pytest.mark.django_db
def test_linked_whatsapp_and_telegram_can_replay_same_pickup_without_llm():
    item, _ = assignment()
    wa = create_channel_identity(user=item.driver.user, channel=MessagingChannel.WHATSAPP, external_user_id="wa-pickup")
    tg = create_channel_identity(user=item.driver.user, channel=MessagingChannel.TELEGRAM, external_user_id="tg-pickup")
    service = DeliveryExecutionService()
    assert service.confirm_pickup(wa, assignment_id=item.id).duplicate is False
    assert service.confirm_pickup(tg, assignment_id=item.id).duplicate is True
