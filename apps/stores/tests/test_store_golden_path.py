import threading
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.gis.geos import Point
from django.db import close_old_connections
from django.utils import timezone

from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
)
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.conversations.application.services import get_or_create_active_conversation
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import ProviderSendResult
from apps.messaging.domain.enums import MessagingChannel, NotificationKind
from apps.messaging.domain.exceptions import MessagingTemporaryError
from apps.messaging.infrastructure.models import OperationalNotification
from apps.orders.application.customer_notifications import notify_order_ready
from apps.orders.application.services import (
    add_order_item,
    create_order_draft,
    set_delivery_location,
)
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.domain.events import OrderReadyForPickup
from apps.orders.infrastructure.models import Order
from apps.stores.application.offer_actions import (
    StoreOfferActionResult,
    StoreOfferActionService,
    encode_store_offer_action,
)
from apps.stores.application.offer_notifications import StoreOfferNotificationService
from apps.stores.application.offers import StoreOfferService
from apps.stores.application.preparation import (
    StorePreparationResult,
    StorePreparationService,
)
from apps.stores.application.services import create_store
from apps.stores.domain.enums import (
    StoreMatchingAttemptStatus,
    StoreOfferNotificationAttemptStatus,
    StoreOfferNotificationStatus,
    StoreOfferStatus,
)
from apps.stores.infrastructure.models import (
    StoreMatchingAttempt,
    StoreOffer,
    StoreOfferNotification,
    StoreOfferNotificationAttempt,
)
from apps.stores.tasks import (
    _expire_store_offer,
    deliver_store_offer_notification,
    recover_store_offer_notification,
)
from apps.whatsapp.domain.enums import WhatsAppOutboundDecision
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def isolated_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


def _phone(prefix):
    return f"+237{prefix}{User.objects.count():06d}"


def notification_fixture(*, nullable_conversation=False):
    customer = User.objects.create_user(whatsapp_phone=_phone("670"))
    seller = User.objects.create_user(whatsapp_phone=_phone("671"))
    identity = create_channel_identity(
        user=seller,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"store-golden-{seller.id}",
    )
    conversation = None
    if not nullable_conversation:
        conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    order = Order.objects.create(
        reference=f"S7-{uuid4().hex[:12]}",
        customer=customer,
        status=OrderStatus.STORE_OFFERED,
    )
    store = create_store(
        name=f"Store notification {store_count()}",
        city="Douala",
        location=Point(9.70, 4.05, srid=4326),
        created_by=seller,
    )
    offer = StoreOffer.objects.create(
        order=order,
        store=store,
        rank=1,
        strategy_version="deterministic_v1",
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    notification = StoreOfferNotification.objects.create(
        offer=offer,
        recipient=seller,
        channel_identity=identity,
        conversation=conversation,
        channel=MessagingChannel.WHATSAPP,
        presentation_snapshot={
            "order_reference": order.reference,
            "items": ["2 × Riz"],
            "distance_meters": 2500,
            "actions": [],
        },
        idempotency_key=f"lot7:{offer.id}:{identity.id}",
    )
    return seller, order, store, offer, notification


def store_count():
    from apps.stores.infrastructure.models import Store

    return Store.objects.count()


def sequential_matching_fixture(*, candidates=2, expired=False):
    customer = User.objects.create_user(whatsapp_phone=_phone("672"))
    order = create_order_draft(customer=customer)
    category = create_category(name=f"Lot 7 {uuid4().hex[:8]}")
    product = create_product(name=f"Riz {uuid4().hex[:6]}", category=category)
    add_order_item(
        order=order,
        requested_name="Riz",
        quantity=2,
        unit=ProductUnit.KG,
        product=product,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    set_delivery_location(order, Point(9.70, 4.05, srid=4326))
    order.status = OrderStatus.STORE_OFFERED
    order.save(update_fields=["status", "updated_at"])

    sellers = []
    stores = []
    snapshot = []
    for rank in range(1, candidates + 1):
        seller = User.objects.create_user(whatsapp_phone=_phone(f"{672 + rank}"))
        create_channel_identity(
            user=seller,
            channel=MessagingChannel.WHATSAPP,
            external_user_id=f"lot7-seller-{seller.id}",
        )
        store = create_store(
            name=f"Lot 7 rank {rank} {uuid4().hex[:6]}",
            city="Douala",
            location=Point(9.70 + rank / 1000, 4.05, srid=4326),
            created_by=seller,
        )
        add_product_to_store(
            store=store,
            product=product,
            availability_status=ProductAvailability.AVAILABLE,
        )
        sellers.append(seller)
        stores.append(store)
        snapshot.append(
            {
                "rank": rank,
                "store_id": str(store.id),
                "fully_covered": True,
                "coverage_ratio": "1",
                "distance_meters": rank * 100,
            }
        )

    attempt = StoreMatchingAttempt.objects.create(
        order=order,
        status=StoreMatchingAttemptStatus.RUNNING,
        strategy_version="deterministic_v1",
        candidate_snapshot=snapshot,
        current_position=1,
    )
    first_offer = StoreOffer.objects.create(
        order=order,
        store=stores[0],
        rank=1,
        strategy_version="deterministic_v1",
        selection_snapshot=snapshot[0],
        expires_at=timezone.now()
        + (-timedelta(seconds=1) if expired else timedelta(minutes=10)),
    )
    return sellers, stores, order, attempt, first_offer


@pytest.mark.django_db(transaction=True)
def test_store_offer_notification_claim_does_not_outer_join_for_update():
    _, _, _, _, notification = notification_fixture(nullable_conversation=True)

    attempt = StoreOfferNotificationService().claim_attempt(notification.id)

    notification.refresh_from_db()
    assert attempt is not None
    assert attempt.notification_id == notification.id
    assert notification.conversation_id is not None


@pytest.mark.django_db
def test_store_offer_notification_claim_creates_attempt():
    _, _, _, _, notification = notification_fixture()

    attempt = StoreOfferNotificationService().claim_attempt(
        notification.id, correlation_id="lot7-claim"
    )

    notification.refresh_from_db()
    assert attempt.attempt_number == 1
    assert attempt.status == StoreOfferNotificationAttemptStatus.STARTED
    assert attempt.provider == MessagingChannel.WHATSAPP
    assert attempt.correlation_id == "lot7-claim"
    assert notification.status == StoreOfferNotificationStatus.SENDING


@pytest.mark.django_db
def test_store_offer_notification_delivery_marks_sent(monkeypatch):
    _, _, _, _, notification = notification_fixture()
    monkeypatch.setattr(
        "apps.stores.tasks._dispatch",
        lambda *_args: ProviderSendResult(
            channel=MessagingChannel.WHATSAPP,
            external_message_id="wamid.lot7",
        ),
    )

    result = deliver_store_offer_notification.run(notification.id, "lot7-sent")

    notification.refresh_from_db()
    attempt = notification.attempts.get()
    assert result == {"status": "SENT"}
    assert notification.status == StoreOfferNotificationStatus.SENT
    assert notification.sent_at is not None
    assert attempt.status == StoreOfferNotificationAttemptStatus.SUCCEEDED
    assert attempt.provider_message_id == "wamid.lot7"


@pytest.mark.django_db
def test_store_offer_whatsapp_delivery_payload_is_actionable(monkeypatch):
    _, order, _, offer, notification = notification_fixture()
    notification.presentation_snapshot["actions"] = [
        {
            "action": "store_offer.accept",
            "id": encode_store_offer_action("store_offer.accept", offer.id),
        },
        {
            "action": "store_offer.reject",
            "id": encode_store_offer_action("store_offer.reject", offer.id),
        },
    ]
    notification.save(update_fields=["presentation_snapshot", "updated_at"])
    captured = {}
    monkeypatch.setattr(
        "apps.whatsapp.domain.outbound_policy.WhatsAppOutboundPolicy.decide",
        lambda *_args: WhatsAppOutboundDecision.FREE_FORM_ALLOWED,
    )
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_interactive_reply_buttons",
        lambda **kwargs: captured.update(kwargs)
        or ProviderSendResult(channel=MessagingChannel.WHATSAPP),
    )

    result = deliver_store_offer_notification.run(notification.id)

    assert result == {"status": "SENT"}
    assert order.reference in captured["body"]
    assert "2 × Riz" in captured["body"]
    assert "2,5 km" in captured["body"]
    assert "Souhaitez-vous accepter" in captured["body"]
    assert [(button.action_id, button.label) for button in captured["buttons"]] == [
        (f"store_offer.accept:{offer.id}", "Accepter"),
        (f"store_offer.reject:{offer.id}", "Refuser"),
    ]


@pytest.mark.django_db
def test_store_offer_notification_failure_is_classified(monkeypatch):
    _, _, _, _, notification = notification_fixture()
    monkeypatch.setattr(
        "apps.stores.tasks._dispatch",
        lambda *_args: (_ for _ in ()).throw(MessagingTemporaryError("timeout")),
    )
    scheduled = []
    monkeypatch.setattr(
        deliver_store_offer_notification,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    result = deliver_store_offer_notification.run(notification.id, "lot7-failure")

    notification.refresh_from_db()
    attempt = notification.attempts.get()
    assert result["status"] == "RETRY"
    assert notification.status == StoreOfferNotificationStatus.FAILED_RETRYABLE
    assert notification.last_error_category == "TRANSIENT_PROVIDER"
    assert attempt.error_category == "TRANSIENT_PROVIDER"
    assert attempt.retryable is True
    assert len(scheduled) == 1


@pytest.mark.django_db
def test_reconciler_does_not_requeue_terminal_notification():
    _, _, _, offer, notification = notification_fixture()
    offer.status = StoreOfferStatus.REJECTED
    offer.rejected_at = timezone.now()
    offer.save(update_fields=["status", "rejected_at", "updated_at"])

    candidates = StoreOfferNotificationService.recovery_candidates()

    assert notification.id not in set(candidates.values_list("id", flat=True))


@pytest.mark.django_db
def test_recovery_closes_stale_sending_without_blind_resend():
    _, _, _, _, notification = notification_fixture()
    attempt = StoreOfferNotificationService().claim_attempt(notification.id)
    StoreOfferNotificationAttempt.objects.filter(pk=attempt.id).update(
        started_at=timezone.now() - timedelta(hours=1)
    )

    result = recover_store_offer_notification.run(notification.id)

    notification.refresh_from_db()
    attempt.refresh_from_db()
    assert result == {"status": "AMBIGUOUS_DELIVERY"}
    assert notification.status == StoreOfferNotificationStatus.FAILED_PERMANENT
    assert notification.last_error_category == "AMBIGUOUS_DELIVERY"
    assert attempt.status == StoreOfferNotificationAttemptStatus.FAILED
    assert attempt.retryable is False


@pytest.mark.django_db
def test_replayed_notification_task_does_not_duplicate_attempt_unsafely(monkeypatch):
    _, _, _, _, notification = notification_fixture()
    monkeypatch.setattr(
        "apps.stores.tasks._dispatch",
        lambda *_args: ProviderSendResult(channel=MessagingChannel.WHATSAPP),
    )

    first = deliver_store_offer_notification.run(notification.id)
    replay = deliver_store_offer_notification.run(notification.id)

    assert first == {"status": "SENT"}
    assert replay == {"status": "NOOP"}
    assert StoreOfferNotificationAttempt.objects.filter(
        notification=notification
    ).count() == 1


@pytest.mark.django_db
def test_retry_notification_is_not_claimed_before_due_time():
    _, _, _, _, notification = notification_fixture()
    notification.status = StoreOfferNotificationStatus.FAILED_RETRYABLE
    notification.next_retry_at = timezone.now() + timedelta(minutes=5)
    notification.save(update_fields=["status", "next_retry_at", "updated_at"])

    attempt = StoreOfferNotificationService().claim_attempt(notification.id)

    assert attempt is None
    assert not notification.attempts.exists()


@pytest.mark.django_db
def test_store_offer_accept_is_idempotent(monkeypatch):
    seller, order, _, offer, _ = notification_fixture()
    attempt = StoreMatchingAttempt.objects.create(
        order=order,
        strategy_version="deterministic_v1",
        candidate_snapshot=[],
        current_position=1,
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_operational_notification.delay",
        lambda *_args: None,
    )
    service = StoreOfferActionService()

    first = service.handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )
    replay = service.handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )

    order.refresh_from_db()
    attempt.refresh_from_db()
    assert first == replay == StoreOfferActionResult.ACCEPTED
    assert order.status == OrderStatus.STORE_ACCEPTED
    assert attempt.status == StoreMatchingAttemptStatus.SUCCEEDED
    assert StoreOffer.objects.filter(
        order=order, status=StoreOfferStatus.ACCEPTED
    ).count() == 1


@pytest.mark.django_db
def test_store_offer_reject_advances_to_next_candidate():
    sellers, stores, order, attempt, first_offer = sequential_matching_fixture()

    result = StoreOfferActionService().handle(
        actor=sellers[0],
        offer_id=first_offer.id,
        action_id="store_offer.reject",
    )

    first_offer.refresh_from_db()
    attempt.refresh_from_db()
    assert result == StoreOfferActionResult.REJECTED
    assert first_offer.status == StoreOfferStatus.REJECTED
    assert attempt.current_position == 2
    assert StoreOffer.objects.filter(
        order=order, store=stores[1], status=StoreOfferStatus.PENDING
    ).count() == 1


@pytest.mark.django_db
def test_store_offer_expire_advances_to_next_candidate():
    _, stores, order, attempt, first_offer = sequential_matching_fixture(expired=True)

    result = _expire_store_offer(first_offer.id, "lot7-expire")

    first_offer.refresh_from_db()
    attempt.refresh_from_db()
    assert result == {"status": "EXPIRED"}
    assert first_offer.status == StoreOfferStatus.EXPIRED
    assert attempt.current_position == 2
    assert StoreOffer.objects.filter(
        order=order, store=stores[1], status=StoreOfferStatus.PENDING
    ).exists()
    assert not StoreOfferNotificationService.recovery_candidates().filter(
        offer=first_offer
    ).exists()


@pytest.mark.django_db
def test_store_matching_exhausts_cleanly():
    sellers, _, order, attempt, first_offer = sequential_matching_fixture(candidates=1)

    StoreOfferActionService().handle(
        actor=sellers[0],
        offer_id=first_offer.id,
        action_id="store_offer.reject",
    )

    order.refresh_from_db()
    attempt.refresh_from_db()
    assert attempt.status == StoreMatchingAttemptStatus.EXHAUSTED
    assert attempt.failure_reason == "NO_CANDIDATES_REMAINING"
    assert order.status == OrderStatus.FAILED
    assert not StoreOffer.objects.filter(
        order=order, status=StoreOfferStatus.PENDING
    ).exists()


@pytest.mark.django_db
def test_only_one_store_can_accept_order():
    sellers, stores, order, _, first_offer = sequential_matching_fixture()
    second = StoreOffer.objects.create(
        order=order,
        store=stores[1],
        rank=2,
        strategy_version="deterministic_v1",
        expires_at=timezone.now() + timedelta(minutes=10),
    )

    first = StoreOfferActionService().handle(
        actor=sellers[0], offer_id=first_offer.id, action_id="store_offer.accept"
    )
    second_result = StoreOfferActionService().handle(
        actor=sellers[1], offer_id=second.id, action_id="store_offer.accept"
    )

    second.refresh_from_db()
    assert first == StoreOfferActionResult.ACCEPTED
    assert second_result == StoreOfferActionResult.NOT_AVAILABLE
    assert second.status == StoreOfferStatus.CANCELLED
    assert StoreOffer.objects.filter(
        order=order, status=StoreOfferStatus.ACCEPTED
    ).count() == 1


@pytest.mark.django_db
def test_store_accept_updates_order_state():
    seller, order, _, offer, _ = notification_fixture()

    StoreOfferActionService().handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )

    order.refresh_from_db()
    offer.refresh_from_db()
    assert offer.status == StoreOfferStatus.ACCEPTED
    assert offer.accepted_at is not None
    assert order.status == OrderStatus.STORE_ACCEPTED


@pytest.mark.django_db
def test_store_order_can_start_preparing():
    seller, order, _, offer, _ = notification_fixture()
    StoreOfferActionService().handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )

    result = StorePreparationService().start_preparation(
        actor=seller, order_id=order.id
    )

    order.refresh_from_db()
    assert result == StorePreparationResult.STARTED
    assert order.status == OrderStatus.PREPARING


@pytest.mark.django_db(transaction=True)
def test_store_order_ready_starts_driver_matching_once(monkeypatch):
    seller, order, _, offer, _ = notification_fixture()
    StoreOfferActionService().handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )
    StorePreparationService().start_preparation(actor=seller, order_id=order.id)
    events = []
    subscribe(OrderReadyForPickup, events.append)

    first = StorePreparationService().mark_ready_for_pickup(
        actor=seller, order_id=order.id
    )
    replay = StorePreparationService().mark_ready_for_pickup(
        actor=seller, order_id=order.id
    )

    assert first == StorePreparationResult.READY
    assert replay == StorePreparationResult.ALREADY_READY
    assert len(events) == 1


@pytest.mark.django_db
def test_store_ready_is_idempotent():
    seller, order, _, offer, _ = notification_fixture()
    StoreOfferActionService().handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )
    service = StorePreparationService()
    service.start_preparation(actor=seller, order_id=order.id)

    assert service.mark_ready_for_pickup(
        actor=seller, order_id=order.id
    ) == StorePreparationResult.READY
    assert service.mark_ready_for_pickup(
        actor=seller, order_id=order.id
    ) == StorePreparationResult.ALREADY_READY


@pytest.mark.django_db
def test_obsolete_store_offer_action_is_rejected():
    seller, _, _, offer, notification = notification_fixture()
    offer.status = StoreOfferStatus.EXPIRED
    offer.expired_at = timezone.now()
    offer.save(update_fields=["status", "expired_at", "updated_at"])

    result = StoreOfferActionService().handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )

    assert result == StoreOfferActionResult.NOT_AVAILABLE
    assert notification.status == StoreOfferNotificationStatus.PENDING


@pytest.mark.django_db(transaction=True)
def test_store_e2e_reject_accept_prepare_ready(monkeypatch):
    monkeypatch.setattr(
        "apps.stores.tasks.deliver_store_offer_notification.delay",
        lambda *_args: None,
    )
    sellers, stores, order, attempt, first_offer = sequential_matching_fixture()

    rejected = StoreOfferActionService().handle(
        actor=sellers[0],
        offer_id=first_offer.id,
        action_id="store_offer.reject",
        correlation_id="lot7-e2e",
    )
    second_offer = StoreOffer.objects.get(order=order, store=stores[1])
    accepted = StoreOfferActionService().handle(
        actor=sellers[1],
        offer_id=second_offer.id,
        action_id="store_offer.accept",
        correlation_id="lot7-e2e",
    )
    started = StorePreparationService().start_preparation(
        actor=sellers[1], order_id=order.id, correlation_id="lot7-e2e"
    )
    ready_events = []
    subscribe(OrderReadyForPickup, ready_events.append)
    ready = StorePreparationService().mark_ready_for_pickup(
        actor=sellers[1], order_id=order.id, correlation_id="lot7-e2e"
    )

    first_offer.refresh_from_db()
    second_offer.refresh_from_db()
    attempt.refresh_from_db()
    order.refresh_from_db()
    assert rejected == StoreOfferActionResult.REJECTED
    assert first_offer.status == StoreOfferStatus.REJECTED
    assert accepted == StoreOfferActionResult.ACCEPTED
    assert second_offer.status == StoreOfferStatus.ACCEPTED
    assert attempt.status == StoreMatchingAttemptStatus.SUCCEEDED
    assert started == StorePreparationResult.STARTED
    assert ready == StorePreparationResult.READY
    assert order.status == OrderStatus.READY_FOR_PICKUP
    assert len(ready_events) == 1


@pytest.mark.django_db(transaction=True)
def test_customer_receives_store_preparing_and_ready_milestones(monkeypatch):
    seller, order, _, offer, _ = notification_fixture()
    create_channel_identity(
        user=order.customer,
        channel=MessagingChannel.TELEGRAM,
        external_user_id=f"lot7-customer-{order.customer_id}",
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_operational_notification.delay",
        lambda *_args: None,
    )
    subscribe(OrderReadyForPickup, notify_order_ready)

    StoreOfferActionService().handle(
        actor=seller, offer_id=offer.id, action_id="store_offer.accept"
    )
    service = StorePreparationService()
    service.start_preparation(actor=seller, order_id=order.id)
    service.start_preparation(actor=seller, order_id=order.id)
    service.mark_ready_for_pickup(actor=seller, order_id=order.id)
    service.mark_ready_for_pickup(actor=seller, order_id=order.id)

    kinds = list(
        OperationalNotification.objects.filter(recipient=order.customer)
        .order_by("created_at")
        .values_list("kind", flat=True)
    )
    assert kinds == [
        NotificationKind.ORDER_STORE_ACCEPTED,
        NotificationKind.STORE_PREPARATION_STARTED,
        NotificationKind.ORDER_READY_FOR_PICKUP,
    ]


@pytest.mark.django_db(transaction=True)
def test_concurrent_store_accepts_have_exactly_one_winner():
    _, stores, order, _, first_offer = sequential_matching_fixture()
    second_offer = StoreOffer.objects.create(
        order=order,
        store=stores[1],
        rank=2,
        strategy_version="deterministic_v1",
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def accept(offer_id):
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            StoreOfferService().accept_offer(StoreOffer.objects.get(pk=offer_id))
            outcomes.append("ACCEPTED")
        except ValueError:
            outcomes.append("STALE")
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=accept, args=(first_offer.id,)),
        threading.Thread(target=accept, args=(second_offer.id,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["ACCEPTED", "STALE"]
    assert StoreOffer.objects.filter(
        order=order, status=StoreOfferStatus.ACCEPTED
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_accept_and_expire_race_has_one_terminal_store_offer():
    _, _, _, _, offer = sequential_matching_fixture(candidates=1)
    offer.expires_at = timezone.now()
    offer.save(update_fields=["expires_at", "updated_at"])
    barrier = threading.Barrier(2)
    outcomes = []

    def accept():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            StoreOfferService().accept_offer(StoreOffer.objects.get(pk=offer.id))
            outcomes.append("ACCEPTED")
        except ValueError:
            outcomes.append("STALE")
        finally:
            close_old_connections()

    def expire():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            StoreOfferService().expire_offer(StoreOffer.objects.get(pk=offer.id))
            outcomes.append("EXPIRED")
        except ValueError:
            outcomes.append("STALE")
        finally:
            close_old_connections()

    threads = [threading.Thread(target=accept), threading.Thread(target=expire)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    offer.refresh_from_db()
    assert not any(thread.is_alive() for thread in threads)
    assert offer.status in {StoreOfferStatus.ACCEPTED, StoreOfferStatus.EXPIRED}
    assert len(outcomes) == 2
    assert outcomes.count("STALE") == 1
