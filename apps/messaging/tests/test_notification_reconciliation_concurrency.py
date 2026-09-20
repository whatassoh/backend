"""PostgreSQL race validation for abandoned notification reconciliation."""

import threading
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.identity.infrastructure.models import User
from apps.messaging.application.notifications import NotificationService
from apps.messaging.domain.enums import (
    MessagingChannel,
    NotificationKind,
    NotificationStatus,
)
from apps.messaging.infrastructure.models import ChannelIdentity, OperationalNotification


@pytest.mark.django_db(transaction=True)
def test_two_notification_reconcilers_converge_on_one_intent(monkeypatch):
    recipient = User.objects.create_user(whatsapp_phone="+237686000001")
    identity = ChannelIdentity.objects.create(
        user=recipient, channel=MessagingChannel.TELEGRAM, external_user_id="reconcile-race"
    )
    notification = OperationalNotification.objects.create(
        kind=NotificationKind.ORDER_CONFIRMED,
        recipient=recipient,
        channel_identity=identity,
        channel=MessagingChannel.TELEGRAM,
        business_entity_type="orders.Order",
        business_entity_id="opaque-order",
        idempotency_key="order:opaque-order:reconcile-race",
        status=NotificationStatus.PROCESSING,
        updated_at=timezone.now() - timedelta(hours=1),
    )
    published = []
    publish_lock = threading.Lock()

    def fake_delay(*args, **kwargs):
        with publish_lock:
            published.append(args)

    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", fake_delay)
    barrier, results = threading.Barrier(2), []

    def reconcile():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            results.append(NotificationService().recover(notification.id))
        finally:
            close_old_connections()

    threads = [threading.Thread(target=reconcile) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED_RETRYABLE
    assert OperationalNotification.objects.filter(idempotency_key=notification.idempotency_key).count() == 1
    assert results.count("REQUEUED") == 1
    assert len(published) == 1
