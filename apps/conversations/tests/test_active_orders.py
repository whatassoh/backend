import threading
from django.db import close_old_connections
import pytest

from apps.conversations.application.active_orders import (
    ActiveCustomerOrderService,
    ActiveOrderDecisionStatus,
)
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ConversationContext
from apps.conversations.domain.enums import ConversationStatus
from apps.conversations.infrastructure.models import Conversation
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.application.services import create_order_draft
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order


def conversation(phone="+237699887766", channel=MessagingChannel.WHATSAPP):
    user = User.objects.create_user(whatsapp_phone=phone)
    identity = create_channel_identity(
        user=user, channel=channel, external_user_id=f"{channel}-{phone}"
    )
    return get_or_create_active_conversation(channel_identity=identity)[0], user


@pytest.mark.django_db
def test_creates_then_reuses_a_single_conversation_draft():
    convo, _ = conversation()
    service = ActiveCustomerOrderService()

    first = service.get_or_create_draft(convo)
    second = service.get_or_create_draft(convo)

    assert first.status == ActiveOrderDecisionStatus.CREATED
    assert second.status == ActiveOrderDecisionStatus.ACTIVE
    assert second.order_id == first.order_id


@pytest.mark.django_db
@pytest.mark.parametrize("status", [OrderStatus.CANCELLED, OrderStatus.CONFIRMED])
def test_terminal_or_stale_pointer_is_detached(status):
    convo, user = conversation()
    order = create_order_draft(customer=user)
    order.status = status
    order.save(update_fields=["status", "updated_at"])
    convo.context = ConversationContext(active_order_id=str(order.id)).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    decision = ActiveCustomerOrderService().get_active_order(convo)

    convo.refresh_from_db()
    assert decision.status == ActiveOrderDecisionStatus.STALE_CONTEXT
    assert ConversationContext.from_payload(convo.context).active_order_id is None


@pytest.mark.django_db
def test_wrong_owner_and_corrupt_uuid_are_never_reused():
    convo, _ = conversation()
    _, other = conversation("+237699887767")
    order = create_order_draft(customer=other)
    service = ActiveCustomerOrderService()

    convo.context = ConversationContext(active_order_id=str(order.id)).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    assert service.get_active_order(convo).status == ActiveOrderDecisionStatus.STALE_CONTEXT

    convo.context = {"active_order_id": "not-a-uuid"}
    convo.save(update_fields=["context", "updated_at"])
    assert service.get_active_order(convo).status == ActiveOrderDecisionStatus.STALE_CONTEXT


@pytest.mark.django_db
def test_closed_conversation_neither_resumes_nor_creates_draft():
    convo, _ = conversation()
    convo.status = ConversationStatus.CLOSED
    convo.save(update_fields=["status", "updated_at"])

    decision = ActiveCustomerOrderService().get_or_create_draft(convo)

    assert decision.status == ActiveOrderDecisionStatus.CLOSED_CONVERSATION


@pytest.mark.django_db
def test_cross_channel_conversations_share_user_building_order_but_keep_contexts_separate():
    convo, user = conversation()
    identity = create_channel_identity(
        user=user, channel=MessagingChannel.TELEGRAM, external_user_id="telegram-1"
    )
    telegram, _ = get_or_create_active_conversation(
        channel_identity=identity, external_chat_id="chat-1"
    )
    service = ActiveCustomerOrderService()
    order = service.get_or_create_draft(convo).order

    resolved = service.get_or_create_draft(telegram)
    telegram.refresh_from_db()
    convo.refresh_from_db()
    assert resolved.order_id == str(order.id)
    assert ConversationContext.from_payload(telegram.context).active_order_id == str(order.id)
    assert telegram.id != convo.id


@pytest.mark.django_db
def test_resume_is_explicit_and_abandon_uses_cancellation_transition():
    convo, user = conversation()
    order = create_order_draft(customer=user)
    service = ActiveCustomerOrderService()

    resumed = service.resume_order(convo, str(order.id))
    abandoned = service.abandon_order(convo)

    order.refresh_from_db()
    assert resumed.status == ActiveOrderDecisionStatus.RESUMED
    assert abandoned.status == ActiveOrderDecisionStatus.ABANDONED
    assert order.status == OrderStatus.CANCELLED

@pytest.mark.django_db(transaction=True)
def test_two_cross_channel_workers_converge_on_one_building_order():
    whatsapp, user = conversation("+237699887799")
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="telegram-concurrent-1",
    )
    telegram, _ = get_or_create_active_conversation(
        channel_identity=identity, external_chat_id="chat-concurrent-1"
    )
    barrier = threading.Barrier(2)
    results, errors = [], []
    result_lock = threading.Lock()

    def create_from(conversation_id):
        close_old_connections()
        try:
            parallel_conversation = Conversation.objects.get(pk=conversation_id)
            barrier.wait(timeout=5)
            decision = ActiveCustomerOrderService().get_or_create_draft(
                parallel_conversation
            )
            with result_lock:
                results.append(decision.order_id)
        except Exception as exc:  # surface worker errors in the main test thread
            with result_lock:
                errors.append(exc)
        finally:
            close_old_connections()

    workers = [
        threading.Thread(target=create_from, args=(whatsapp.id,)),
        threading.Thread(target=create_from, args=(telegram.id,)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert not errors
    assert len(results) == 2
    assert results[0] == results[1]
    assert Order.objects.filter(
        customer=user, status__in=ActiveCustomerOrderService.active_statuses
    ).count() == 1
