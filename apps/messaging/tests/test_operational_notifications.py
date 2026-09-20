from unittest.mock import Mock

import pytest
from django.db import transaction

from apps.identity.infrastructure.models import User
from apps.messaging.application.failures import FailureDecision
from apps.messaging.application.notifications import NotificationService
from apps.messaging.domain.enums import (
    MessagingChannel,
    NotificationKind,
    NotificationStatus,
)
from apps.messaging.infrastructure.models import (
    ChannelIdentity,
    OperationalNotification,
)


def make_user(number):
    return User.objects.create_user(whatsapp_phone=f"+23761122{number:04d}")


@pytest.mark.django_db(transaction=True)
def test_intent_is_idempotent_and_enqueued_only_after_commit(monkeypatch):
    recipient = make_user(1)
    ChannelIdentity.objects.create(user=recipient, channel=MessagingChannel.TELEGRAM, external_user_id="1001")
    delay = Mock()
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", delay)

    with transaction.atomic():
        first = NotificationService().create_intent(
            kind=NotificationKind.DRIVER_OFFER, recipient=recipient,
            business_entity_type="drivers.DriverOffer", business_entity_id="opaque-offer",
            idempotency_key="driver-offer:opaque-offer:initial", correlation_id="corr-1",
        )
        second = NotificationService().create_intent(
            kind=NotificationKind.DRIVER_OFFER, recipient=recipient,
            business_entity_type="drivers.DriverOffer", business_entity_id="opaque-offer",
            idempotency_key="driver-offer:opaque-offer:initial", correlation_id="corr-1",
        )
        assert first.id == second.id
        delay.assert_not_called()

    delay.assert_called_once_with(str(first.id), "corr-1")
    assert OperationalNotification.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_rollback_does_not_enqueue(monkeypatch):
    recipient = make_user(2)
    ChannelIdentity.objects.create(user=recipient, channel=MessagingChannel.TELEGRAM, external_user_id="1002")
    delay = Mock()
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", delay)

    with pytest.raises(RuntimeError), transaction.atomic():
        NotificationService().create_intent(
            kind=NotificationKind.ORDER_CONFIRMED, recipient=recipient,
            business_entity_type="orders.Order", business_entity_id="opaque-order",
            idempotency_key="order:opaque-order:confirmed",
        )
        raise RuntimeError("rollback")

    delay.assert_not_called()
    assert OperationalNotification.objects.count() == 0


@pytest.mark.django_db
def test_missing_recipient_creates_no_intent():
    recipient = make_user(3)
    assert NotificationService().create_intent(
        kind=NotificationKind.ORDER_CONFIRMED, recipient=recipient,
        business_entity_type="orders.Order", business_entity_id="opaque-order",
        idempotency_key="order:opaque-order:confirmed",
    ) is None


@pytest.mark.django_db
def test_cancelled_intent_is_not_claimed_for_delivery():
    recipient = make_user(4)
    identity = ChannelIdentity.objects.create(user=recipient, channel=MessagingChannel.TELEGRAM, external_user_id="1004")
    notification = OperationalNotification.objects.create(
        kind=NotificationKind.ORDER_CONFIRMED, recipient=recipient, channel_identity=identity,
        channel=MessagingChannel.TELEGRAM, business_entity_type="orders.Order",
        business_entity_id="opaque-order", idempotency_key="order:opaque-order:confirmed",
        status=NotificationStatus.CANCELLED,
    )
    assert NotificationService().deliver(notification.id) is None


@pytest.mark.django_db(transaction=True)
def test_failure_is_terminal_after_policy_and_replay_keeps_same_intent(monkeypatch):
    recipient = make_user(5)
    identity = ChannelIdentity.objects.create(user=recipient, channel=MessagingChannel.TELEGRAM, external_user_id="1005")
    notification = OperationalNotification.objects.create(
        kind=NotificationKind.ORDER_CONFIRMED, recipient=recipient, channel_identity=identity,
        channel=MessagingChannel.TELEGRAM, business_entity_type="orders.Order",
        business_entity_id="opaque-order", idempotency_key="order:opaque-order:confirmed",
    )
    attempt = NotificationService().deliver(notification.id)
    NotificationService().record_failure(
        attempt.id, FailureDecision("CONFIGURATION", False)
    )
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED_PERMANENT
    delay = Mock()
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", delay)
    replayed = NotificationService().replay(notification.id)
    assert replayed.id == notification.id
    assert OperationalNotification.objects.count() == 1
    delay.assert_called_once()
