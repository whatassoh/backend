from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.ai.domain.understanding import MessageIntent, TaskTarget, UserMessageUnderstanding
from apps.conversations.application.active_orders import ActiveCustomerOrderService
from apps.conversations.application.interruptions import (
    active_task_kind,
    resume_last_task,
    suspend_active_task,
)
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import (
    ActiveJourney,
    ClarificationKind,
    ConversationContext,
    ConversationFlow,
    PendingClarification,
    SellerOnboardingDraft,
    SellerOnboardingStage,
)
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.application.services import create_order_draft
from apps.orders.domain.enums import OrderStatus


class UnderstandingQueue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0) if self.values else None


def understanding(intent, **kwargs):
    return UserMessageUnderstanding(
        intent=intent,
        confidence=Decimal(".99"),
        **kwargs,
    )


def conversation():
    user = User.objects.create_user(
        whatsapp_phone=f"+23768{User.objects.count():07d}"
    )
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"lot5-{user.id}",
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def inbound(text=None, *, action_id=None, message_id="lot5-message"):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=message_id,
        external_user_id="lot5-user",
        message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT,
        text=text,
        metadata={"action_id": action_id} if action_id else {},
    )


def persist(conversation, context):
    conversation.context = context.to_payload()
    conversation.save(update_fields=["context", "updated_at"])


def buy_context(order_id="order-id", flow=ConversationFlow.AWAITING_LOCATION):
    return ConversationContext(
        active_journey=ActiveJourney.BUY,
        active_order_id=order_id,
        flow=flow,
        pending_clarification=PendingClarification(
            kind=ClarificationKind.OTHER,
            field="replacement",
            item_reference="huile",
            metadata={"operation": "UNMATCHED_ITEM_REPLACE"},
        ),
        metadata={"awaiting_delivery_instructions": False, "marker": "buy"},
        processed_message_ids=("already-processed",),
    )


@pytest.mark.django_db
def test_attach_order_preserves_existing_conversation_context():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    original = ConversationContext(
        active_journey=ActiveJourney.SELL,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION,
            store_name="Chez Ada",
        ),
        metadata={"seller_catalog_active": True},
        processed_message_ids=("kept",),
    )
    persist(convo, original)

    ActiveCustomerOrderService().attach_order(convo, str(order.id))

    convo.refresh_from_db()
    restored = ConversationContext.from_payload(convo.context)
    assert restored.active_order_id == str(order.id)
    assert restored.active_journey == ActiveJourney.SELL
    assert restored.seller_onboarding == original.seller_onboarding
    assert restored.processed_message_ids == ("kept",)


@pytest.mark.django_db
def test_get_or_create_draft_preserves_existing_context():
    convo = conversation()
    original = ConversationContext(
        active_journey=ActiveJourney.SELL,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.NAME,
        ),
        metadata={"seller_catalog_active": True},
        processed_message_ids=("kept",),
    )
    persist(convo, original)

    decision = ActiveCustomerOrderService().get_or_create_draft(convo)

    convo.refresh_from_db()
    restored = ConversationContext.from_payload(convo.context)
    assert decision.order is not None
    assert restored.active_order_id == str(decision.order.id)
    assert restored.seller_onboarding == original.seller_onboarding
    assert restored.processed_message_ids == ("kept",)


def test_only_one_journey_is_active_at_a_time():
    suspended, task = suspend_active_task(buy_context(), reason="journey_switch")

    assert task.journey == ActiveJourney.BUY
    assert suspended.active_journey is None
    assert suspended.active_order_id is None
    assert suspended.pending_clarification is None
    assert len(suspended.suspended_tasks) == 1


@pytest.mark.django_db
def test_switch_from_buy_to_deliver_requires_confirmation():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    persist(convo, buy_context(str(order.id)))
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(understanding(MessageIntent.DELIVER))
    )

    outcome = app.process_message(convo, inbound("Je veux livrer"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert outcome.metadata["journey_switch_confirmation"] is True
    assert context.active_journey == ActiveJourney.BUY
    assert context.pending_journey_switch.target == ActiveJourney.DELIVER
    assert not context.suspended_tasks


@pytest.mark.django_db
def test_confirmed_switch_suspends_buy_and_activates_deliver():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    persist(convo, buy_context(str(order.id)))
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(understanding(MessageIntent.DELIVER))
    )
    app.process_message(convo, inbound("Je veux livrer"))

    app.process_message(
        convo,
        inbound(
            action_id="journey.switch.confirm.deliver",
            message_id="confirm-switch",
        ),
    )

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    snapshot = context.suspended_tasks[-1]
    assert context.active_journey == ActiveJourney.DELIVER
    assert context.active_order_id is None
    assert snapshot.journey == ActiveJourney.BUY
    assert snapshot.active_order_id == str(order.id)
    assert snapshot.flow == ConversationFlow.AWAITING_LOCATION


@pytest.mark.django_db
def test_declined_switch_keeps_buy_active():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    persist(convo, buy_context(str(order.id)))
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(understanding(MessageIntent.DELIVER))
    )
    app.process_message(convo, inbound("Je veux livrer"))

    app.process_message(
        convo,
        inbound(action_id="journey.switch.stay", message_id="decline-switch"),
    )

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert context.active_journey == ActiveJourney.BUY
    assert context.active_order_id == str(order.id)
    assert context.pending_journey_switch is None


def test_suspended_buy_does_not_consume_driver_messages():
    paused, _ = suspend_active_task(buy_context(), reason="journey_switch")
    driver = replace(
        ConversationContext.from_payload(paused.to_payload()),
        active_journey=ActiveJourney.DELIVER,
        metadata={},
    )

    assert active_task_kind(driver).value == "JOURNEY"
    assert driver.pending_clarification is None
    assert driver.suspended_tasks[-1].pending_clarification.item_reference == "huile"


def test_resume_buy_restores_exact_previous_flow():
    paused, _ = suspend_active_task(buy_context(flow=ConversationFlow.ORDER_BUILDING), reason="switch")
    restored = resume_last_task(paused)

    assert restored.context.active_journey == ActiveJourney.BUY
    assert restored.context.flow == ConversationFlow.ORDER_BUILDING
    assert restored.context.active_order_id == "order-id"


def test_resume_buy_restores_pending_replacement():
    paused, _ = suspend_active_task(buy_context(), reason="switch")
    restored = resume_last_task(paused)

    assert restored.context.pending_clarification.item_reference == "huile"
    assert restored.context.pending_clarification.metadata["operation"] == "UNMATCHED_ITEM_REPLACE"


def test_resume_buy_restores_awaiting_location():
    paused, _ = suspend_active_task(
        buy_context(flow=ConversationFlow.AWAITING_LOCATION), reason="switch"
    )

    assert resume_last_task(paused).context.flow == ConversationFlow.AWAITING_LOCATION


def test_resume_buy_restores_awaiting_confirmation():
    paused, _ = suspend_active_task(
        buy_context(flow=ConversationFlow.AWAITING_CONFIRMATION), reason="switch"
    )

    assert resume_last_task(paused).context.flow == ConversationFlow.AWAITING_CONFIRMATION


def test_completed_journey_is_not_kept_as_suspended():
    paused, _ = suspend_active_task(buy_context(), reason="switch")
    restored = resume_last_task(paused)

    assert not restored.context.suspended_tasks


@pytest.mark.django_db
def test_cancelled_suspended_order_is_removed_from_resume_state():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    paused, _ = suspend_active_task(buy_context(str(order.id)), reason="switch")
    persist(convo, paused)
    order.status = OrderStatus.CANCELLED
    order.save(update_fields=["status", "updated_at"])

    restored = resume_last_task(ConversationContext.from_payload(convo.context))
    persist(convo, restored.context)
    ActiveCustomerOrderService().get_active_order(convo)
    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)

    assert not context.suspended_tasks
    assert context.active_order_id is None


@pytest.mark.django_db
def test_non_suspendable_driver_delivery_blocks_journey_switch(monkeypatch):
    convo = conversation()
    persist(convo, ConversationContext(active_journey=ActiveJourney.DELIVER))
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())
    monkeypatch.setattr(app, "_active_delivery_mission", lambda _conversation: SimpleNamespace())

    outcome = app._request_journey_switch(convo, ConversationContext(active_journey=ActiveJourney.DELIVER), ActiveJourney.BUY)

    assert outcome.metadata["journey_switch_blocked"] == "ACTIVE_DELIVERY"


def test_explicit_journey_switch_preempts_pending_step():
    context = buy_context()
    deliver = understanding(MessageIntent.DELIVER)

    assert ConversationalOrchestrator._is_hard_pending_interrupt(deliver, context)


def test_normal_pending_reply_does_not_switch_journey():
    context = buy_context()
    reply = understanding(MessageIntent.CREATE_OR_UPDATE_ORDER)

    assert not ConversationalOrchestrator._is_hard_pending_interrupt(reply, context)


def test_switch_preserves_processed_message_ids_and_other_context_fields():
    paused, _ = suspend_active_task(buy_context(), reason="switch")
    restored = resume_last_task(paused)

    assert restored.context.processed_message_ids == ("already-processed",)
    assert restored.context.metadata["marker"] == "buy"


def test_resume_exposes_next_step():
    from apps.conversations.application.next_steps import ensure_conversational_outcome
    from apps.conversations.application.orchestrator import (
        ConversationalOutcome,
        ConversationalOutcomeStatus,
        ResponsePlan,
    )

    restored = resume_last_task(suspend_active_task(buy_context(), reason="switch")[0])
    outcome = ensure_conversational_outcome(
        ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(text="Commande reprise."),
            active_order_id=restored.context.active_order_id,
        ),
        restored.context,
    )

    assert outcome.response.next_step is not None
