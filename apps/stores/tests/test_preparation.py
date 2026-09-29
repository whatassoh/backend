import pytest
from uuid import UUID, uuid4
from django.contrib.gis.geos import Point
from django.urls import reverse
from rest_framework.test import APIClient

from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.orders.application.services import create_order_draft
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.events import OrderReadyForPickup
from apps.stores.application.preparation import (
    StorePreparationResult,
    StorePreparationService,
    decode_store_order_action,
    encode_store_order_action,
)
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole, StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer
from common.events import clear_subscribers, subscribe


@pytest.fixture(autouse=True)
def clear_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


def accepted_order():
    actor = User.objects.create_user(whatsapp_phone=f"+237677{User.objects.count():06d}")
    customer = User.objects.create_user(whatsapp_phone=f"+237688{User.objects.count():06d}")
    store = create_store(name="Prep store", city="Douala", location=Point(9.7, 4.05, srid=4326))
    add_store_member(store, actor, StoreMembershipRole.OWNER)
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.STORE_ACCEPTED
    order.save(update_fields=["status", "updated_at"])
    StoreOffer.objects.create(order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1, strategy_version="test", selection_snapshot={}, expires_at=order.created_at)
    return actor, order


def accepted_order_for(*, actor, store, phone):
    customer = User.objects.create_user(whatsapp_phone=phone)
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.STORE_ACCEPTED
    order.save(update_fields=["status", "updated_at"])
    StoreOffer.objects.create(
        order=order, store=store, status=StoreOfferStatus.ACCEPTED, rank=1,
        strategy_version="test", selection_snapshot={}, expires_at=order.created_at,
    )
    return order


class UnderstandingMustNotRun:
    def understand_normalized(self, *_args, **_kwargs):
        raise AssertionError("structured preparation callbacks must bypass AI")


def callback(action_id, message_id):
    return NormalizedInboundMessage(
        channel=MessagingChannel.TELEGRAM,
        external_message_id=message_id,
        external_user_id="seller",
        message_type=MessageType.INTERACTIVE,
        metadata={"action_id": action_id},
    )


@pytest.mark.django_db(transaction=True)
def test_authorized_preparation_uses_existing_state_machine_and_emits_once():
    actor, order = accepted_order()
    received = []
    subscribe(OrderReadyForPickup, received.append)
    service = StorePreparationService()
    assert service.start_preparation(actor=actor, order_id=order.id) == StorePreparationResult.STARTED
    assert service.start_preparation(actor=actor, order_id=order.id) == StorePreparationResult.ALREADY_PREPARING
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.READY
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.ALREADY_READY
    order.refresh_from_db()
    assert order.status == OrderStatus.READY_FOR_PICKUP
    assert len(received) == 1


@pytest.mark.django_db
def test_preparation_rejects_non_member_and_skips_direct_ready():
    actor, order = accepted_order()
    outsider = User.objects.create_user(whatsapp_phone=f"+237699{User.objects.count():06d}")
    service = StorePreparationService()
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.NOT_AVAILABLE
    assert service.start_preparation(actor=outsider, order_id=order.id) == StorePreparationResult.NOT_AUTHORIZED
    assert service.mark_ready_for_pickup(actor=outsider, order_id=order.id) == StorePreparationResult.NOT_AUTHORIZED


@pytest.mark.django_db
def test_preparation_rejects_unknown_cancelled_and_wrong_status_orders():
    actor, order = accepted_order()
    service = StorePreparationService()
    assert service.start_preparation(actor=actor, order_id=uuid4()) == StorePreparationResult.NOT_AUTHORIZED
    order.status = OrderStatus.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    assert service.start_preparation(actor=actor, order_id=order.id) == StorePreparationResult.NOT_AVAILABLE
    assert service.mark_ready_for_pickup(actor=actor, order_id=order.id) == StorePreparationResult.NOT_AVAILABLE


def test_store_order_callback_codec_is_exact_and_rejects_malformed_payloads():
    order_id = uuid4()
    start = encode_store_order_action("store_order.start_preparation", order_id)
    ready = encode_store_order_action("store_order.mark_ready", order_id)
    assert start == f"store_order.start:{order_id}"
    assert ready == f"store_order.ready:{order_id}"
    assert decode_store_order_action(start) == ("store_order.start_preparation", UUID(str(order_id)))
    assert decode_store_order_action("store_order.start_preparation:not-a-uuid") is None
    assert decode_store_order_action("store_order.start_preparation") is None
    assert decode_store_order_action(f"store_order.unknown:{order_id}") is None


@pytest.mark.django_db
def test_dashboard_transition_is_immediately_seen_by_chat_service():
    actor, order = accepted_order()
    client = APIClient()
    client.force_authenticate(actor)
    response = client.post(reverse(
        "seller-dashboard-order-preparation",
        kwargs={"order_id": order.id, "action": "start-preparation"},
    ))
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.PREPARING
    identity = create_channel_identity(
        user=actor, channel=MessagingChannel.TELEGRAM, external_user_id="dashboard-chat"
    )
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingMustNotRun()
    ).process_message(conversation, callback(
        encode_store_order_action("store_order.start_preparation", order.id),
        "dashboard-stale-1",
    ))
    assert outcome.metadata["store_preparation"] == StorePreparationResult.ALREADY_PREPARING


@pytest.mark.django_db(transaction=True)
def test_explicit_callbacks_disambiguate_multiple_orders_and_multiple_stores():
    actor = User.objects.create_user(whatsapp_phone="+237677900001")
    store_a = create_store(name="Prep A", city="Douala", location=Point(9.70, 4.05, srid=4326))
    store_b = create_store(name="Prep B", city="Douala", location=Point(9.71, 4.05, srid=4326))
    add_store_member(store_a, actor, StoreMembershipRole.OWNER)
    add_store_member(store_b, actor, StoreMembershipRole.MANAGER)
    order_x = accepted_order_for(actor=actor, store=store_a, phone="+237688900001")
    order_y = accepted_order_for(actor=actor, store=store_b, phone="+237688900002")
    identity = create_channel_identity(
        user=actor, channel=MessagingChannel.TELEGRAM, external_user_id="multi-store-seller"
    )
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    app = ConversationalOrchestrator(understanding_service=UnderstandingMustNotRun())

    clarification = app.process_message(
        conversation, callback("store_order.start_preparation", "multi-clarification")
    )
    assert clarification.metadata["store_order_clarification"] is True
    assert {action.label for action in clarification.response.actions} == {
        order_x.reference, order_y.reference
    }
    assert {action.action_id for action in clarification.response.actions} == {
        encode_store_order_action("store_order.start_preparation", order_x.id),
        encode_store_order_action("store_order.start_preparation", order_y.id),
    }

    app.process_message(conversation, callback(
        encode_store_order_action("store_order.start_preparation", order_x.id), "multi-1"
    ))
    order_x.refresh_from_db(); order_y.refresh_from_db()
    assert (order_x.status, order_y.status) == (OrderStatus.PREPARING, OrderStatus.STORE_ACCEPTED)

    app.process_message(conversation, callback(
        encode_store_order_action("store_order.start_preparation", order_y.id), "multi-2"
    ))
    app.process_message(conversation, callback(
        encode_store_order_action("store_order.mark_ready", order_x.id), "multi-3"
    ))
    order_x.refresh_from_db(); order_y.refresh_from_db()
    assert (order_x.status, order_y.status) == (OrderStatus.READY_FOR_PICKUP, OrderStatus.PREPARING)


@pytest.mark.django_db
def test_explicit_callback_rejects_actor_from_wrong_store_without_db_change():
    actor, order = accepted_order()
    outsider = User.objects.create_user(whatsapp_phone="+237677900011")
    identity = create_channel_identity(
        user=outsider, channel=MessagingChannel.TELEGRAM, external_user_id="wrong-store-seller"
    )
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingMustNotRun()
    ).process_message(conversation, callback(
        encode_store_order_action("store_order.start_preparation", order.id), "unauthorized-1"
    ))
    order.refresh_from_db()
    assert outcome.metadata["store_preparation"] == StorePreparationResult.NOT_AUTHORIZED
    assert order.status == OrderStatus.STORE_ACCEPTED
