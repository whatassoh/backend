from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.drivers.application.dispatch import DriverMatchingOrchestrator
from apps.drivers.application.offer_notifications import DriverOfferNotificationService
from apps.drivers.application.offers import decode_driver_offer_action, encode_driver_offer_action
from apps.drivers.application.order_ready import enqueue_driver_matching
from apps.drivers.application.services import activate_driver, create_driver_profile, set_driver_available
from apps.drivers.domain.enums import DriverMatchingAttemptStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverMatchingAttempt, DriverOffer, DriverOfferNotification
from apps.drivers.tasks import deliver_driver_offer_notification, start_driver_matching_for_order
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import ProviderSendResult
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import OrderReadyForPickup
from apps.orders.infrastructure.models import Order
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.stores.application.preparation import StorePreparationService
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole, StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


def setup_ready_path(*, channel=MessagingChannel.TELEGRAM):
    seller = User.objects.create_user(whatsapp_phone=f"+237650{User.objects.count():06d}")
    customer = User.objects.create_user(whatsapp_phone=f"+237651{User.objects.count():06d}")
    driver_user = User.objects.create_user(whatsapp_phone=f"+237652{User.objects.count():06d}")
    store = create_store(name=f"Driver pickup {customer.id}", city="Douala", location=Point(9.70, 4.05, srid=4326))
    add_store_member(store, seller, StoreMembershipRole.OWNER)
    order = Order.objects.create(
        reference=f"ORD-260929-{Order.objects.count():04d}"[-15:],
        customer=customer,
        status=OrderStatus.PREPARING,
    )
    StoreOffer.objects.create(
        order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1,
        strategy_version="TEST", expires_at=timezone.now() + timedelta(hours=1),
    )
    driver = activate_driver(create_driver_profile(driver_user))
    driver.last_location = Point(9.70, 4.05, srid=4326)
    driver.last_location_at = timezone.now()
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    set_driver_available(driver)
    create_channel_identity(
        user=driver_user, channel=channel,
        external_user_id=f"driver-{channel.lower()}-{driver.id}",
    )
    return seller, order, driver


@pytest.mark.django_db(transaction=True)
def test_mark_ready_event_starts_one_matching_attempt_and_driver_offer(monkeypatch):
    seller, order, driver = setup_ready_path()
    subscribe(OrderReadyForPickup, enqueue_driver_matching)
    monkeypatch.setattr(
        "apps.drivers.application.location.DriverGeoIndex.nearby",
        lambda *_args, **_kwargs: [(str(driver.id), 25.0)],
    )
    monkeypatch.setattr(
        "apps.drivers.tasks.start_driver_matching_for_order.delay",
        lambda order_id, correlation_id=None: start_driver_matching_for_order.run(order_id, correlation_id),
    )
    monkeypatch.setattr("apps.drivers.tasks.deliver_driver_offer_notification.delay", lambda *_args: None)

    assert StorePreparationService().mark_ready_for_pickup(
        actor=seller, order_id=order.id
    ).value == "READY"
    order.refresh_from_db()
    attempt = DriverMatchingAttempt.objects.get(order=order)
    offer = DriverOffer.objects.get(order=order)
    assert order.status == OrderStatus.MATCHING_DRIVERS
    assert attempt.status == DriverMatchingAttemptStatus.RUNNING
    assert offer.driver_id == driver.id
    assert offer.status == DriverOfferStatus.PENDING
    assert DriverOfferNotification.objects.filter(offer=offer).count() == 1

    event = OrderReadyForPickup(order_id=order.id, store_id=offer.order.store_offers.get(status="ACCEPTED").store_id, customer_id=order.customer_id)
    enqueue_driver_matching(event)
    assert DriverMatchingAttempt.objects.filter(order=order).count() == 1
    assert DriverOffer.objects.filter(order=order).count() == 1


@pytest.mark.django_db
def test_cancelled_order_before_task_is_noop():
    _, order, _ = setup_ready_path()
    order.status = OrderStatus.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    result = start_driver_matching_for_order.run(str(order.id))
    assert result == {"status": "NOOP", "reason": "ORDER_CANCELLED"}
    assert not DriverMatchingAttempt.objects.filter(order=order).exists()


@pytest.mark.django_db
def test_no_driver_exhausts_attempt_and_fails_order_coherently(monkeypatch):
    _, order, _ = setup_ready_path()
    order.status = OrderStatus.READY_FOR_PICKUP
    order.save(update_fields=["status", "updated_at"])
    monkeypatch.setattr(
        "apps.drivers.application.location.DriverGeoIndex.nearby",
        lambda *_args, **_kwargs: [],
    )
    result = start_driver_matching_for_order.run(str(order.id))
    order.refresh_from_db()
    attempt = DriverMatchingAttempt.objects.get(order=order)
    assert result["status"] == "STARTED"
    assert attempt.status == DriverMatchingAttemptStatus.EXHAUSTED
    assert order.status == OrderStatus.FAILED
    assert order.failure_code == "NO_DRIVER_AVAILABLE"
    assert not DriverOffer.objects.filter(order=order).exists()


class FlakyMatching:
    strategy_version = "PROXIMITY_V1"

    def __init__(self):
        self.calls = 0

    def find_candidates(self, order):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary")
        from apps.drivers.application.matching import DriverMatchingResult, DriverMatchingStatus
        return DriverMatchingResult(
            order_id=str(order.id), pickup_store_id=None,
            status=DriverMatchingStatus.OK, candidates=(),
        )


@pytest.mark.django_db
def test_retry_resumes_single_logical_attempt_after_temporary_failure():
    _, order, _ = setup_ready_path()
    order.status = OrderStatus.READY_FOR_PICKUP
    order.save(update_fields=["status", "updated_at"])
    matching = FlakyMatching()
    app = DriverMatchingOrchestrator(matching_service=matching)
    with pytest.raises(RuntimeError, match="temporary"):
        app.start_matching(order.id)
    app.start_matching(order.id)
    assert matching.calls == 2
    assert DriverMatchingAttempt.objects.filter(order=order).count() == 1
    assert DriverMatchingAttempt.objects.get(order=order).status == DriverMatchingAttemptStatus.EXHAUSTED


@pytest.mark.django_db
@pytest.mark.parametrize("channel", [MessagingChannel.TELEGRAM, MessagingChannel.WHATSAPP])
def test_driver_offer_delivery_is_interactive_and_targets_exact_offer(channel, monkeypatch):
    _, order, driver = setup_ready_path(channel=channel)
    order.status = OrderStatus.MATCHING_DRIVERS
    order.save(update_fields=["status", "updated_at"])
    offer = DriverOffer.objects.create(
        order=order, driver=driver, rank=1, strategy_version="TEST",
        expires_at=timezone.now() + timedelta(minutes=5),
        candidate_snapshot={"distance_meters": 25},
    )
    notification = DriverOfferNotificationService().create_for_offer(offer)
    captured = {}
    if channel == MessagingChannel.TELEGRAM:
        monkeypatch.setattr(
            "apps.drivers.tasks.send_conversation_interactive",
            lambda **kwargs: captured.update(kwargs) or ProviderSendResult(channel=channel),
        )
    else:
        monkeypatch.setattr(
            "apps.whatsapp.domain.outbound_policy.WhatsAppOutboundPolicy.decide",
            lambda *_args: "FREE_FORM_ALLOWED",
        )
        monkeypatch.setattr(
            "apps.whatsapp.application.outbound.send_whatsapp_interactive_reply_buttons",
            lambda **kwargs: captured.update(kwargs) or ProviderSendResult(channel=channel),
        )
    result = deliver_driver_offer_notification.run(str(notification.id))
    assert result["status"] == "SENT"
    buttons = captured.get("actions") or [
        {"id": button.action_id, "label": button.label} for button in captured["buttons"]
    ]
    assert buttons == [
        {"id": f"driver_offer.accept:{offer.id}", "label": "Accepter"},
        {"id": f"driver_offer.reject:{offer.id}", "label": "Refuser"},
    ]


def test_driver_offer_callback_codec_rejects_malformed_payloads():
    from uuid import uuid4

    offer_id = uuid4()
    encoded = encode_driver_offer_action("driver_offer.accept", offer_id)
    assert encoded == f"driver_offer.accept:{offer_id}"
    assert decode_driver_offer_action(encoded) == ("driver_offer.accept", offer_id)
    assert decode_driver_offer_action("driver_offer.accept:not-a-uuid") is None
    assert decode_driver_offer_action("driver_offer.accept") is None
    assert decode_driver_offer_action(f"driver_offer.unknown:{offer_id}") is None


@pytest.mark.django_db
def test_closed_whatsapp_driver_offer_uses_valid_operational_template(monkeypatch):
    _, order, driver = setup_ready_path(channel=MessagingChannel.WHATSAPP)
    order.status = OrderStatus.MATCHING_DRIVERS
    order.save(update_fields=["status", "updated_at"])
    offer = DriverOffer.objects.create(
        order=order, driver=driver, rank=1, strategy_version="TEST",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    notification = DriverOfferNotificationService().create_for_offer(offer)
    captured = {}
    monkeypatch.setattr(
        "apps.whatsapp.domain.outbound_policy.WhatsAppOutboundPolicy.decide",
        lambda *_args: "TEMPLATE_REQUIRED",
    )
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_template_to_conversation",
        lambda **kwargs: captured.update(kwargs) or ProviderSendResult(channel=MessagingChannel.WHATSAPP),
    )
    assert deliver_driver_offer_notification.run(str(notification.id))["status"] == "SENT"
    assert captured["body_parameters"] == {"recipient_name": "partenaire"}


@pytest.mark.django_db
def test_expired_historical_callback_cannot_act_on_new_pending_offer():
    _, old_order, driver = setup_ready_path()
    old_order.status = OrderStatus.MATCHING_DRIVERS
    old_order.save(update_fields=["status", "updated_at"])
    old_offer = DriverOffer.objects.create(
        order=old_order, driver=driver, rank=1, strategy_version="TEST",
        status=DriverOfferStatus.EXPIRED,
        expires_at=timezone.now() - timedelta(minutes=1), expired_at=timezone.now(),
    )
    customer = User.objects.create_user(whatsapp_phone="+237653999991")
    new_order = Order.objects.create(
        reference=f"ORD-260929-{Order.objects.count():04d}"[-15:], customer=customer,
        status=OrderStatus.MATCHING_DRIVERS,
    )
    new_offer = DriverOffer.objects.create(
        order=new_order, driver=driver, rank=1, strategy_version="TEST",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    identity = driver.user.channel_identities.get(channel=MessagingChannel.TELEGRAM)
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    conversation.context = {**conversation.context, "driver_offer_id": str(new_offer.id)}
    conversation.save(update_fields=["context", "updated_at"])

    class UnderstandingMustNotRun:
        def understand_normalized(self, *_args, **_kwargs):
            raise AssertionError

    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingMustNotRun()
    ).process_message(
        conversation,
        NormalizedInboundMessage(
            channel=MessagingChannel.TELEGRAM,
            external_message_id="old-driver-offer",
            external_user_id=identity.external_user_id,
            message_type=MessageType.INTERACTIVE,
            metadata={"action_id": encode_driver_offer_action("driver_offer.accept", old_offer.id)},
        ),
    )
    old_offer.refresh_from_db(); new_offer.refresh_from_db()
    assert outcome.status.value == "REJECTED"
    assert old_offer.status == DriverOfferStatus.EXPIRED
    assert new_offer.status == DriverOfferStatus.PENDING
