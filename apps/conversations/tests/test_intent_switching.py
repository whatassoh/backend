from decimal import Decimal

import pytest

from apps.ai.domain.understanding import (
    MessageIntent,
    OrderAction,
    TaskTarget,
    UserMessageUnderstanding,
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
from apps.orders.infrastructure.models import Order


class UnderstandingQueue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0) if self.values else None


def understanding(intent, **kwargs):
    return UserMessageUnderstanding(
        intent=intent, confidence=Decimal(".99"), **kwargs
    )


def conversation():
    user = User.objects.create_user(
        whatsapp_phone=f"+23769{User.objects.count():07d}"
    )
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"switch-{user.id}",
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def inbound(text=None, *, action_id=None, message_id="switch-message"):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=message_id,
        external_user_id="switch-user",
        message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT,
        text=text,
        metadata={"action_id": action_id} if action_id else {},
    )


def persist_context(convo, context):
    convo.context = context.to_payload()
    convo.save(update_fields=["context", "updated_at"])


@pytest.mark.django_db
def test_rich_greeting_does_not_fill_pending_order_clarification():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    persist_context(
        convo,
        ConversationContext(
            active_journey=ActiveJourney.BUY,
            active_order_id=str(order.id),
            flow=ConversationFlow.AWAITING_CLARIFICATION,
            pending_clarification=PendingClarification(
                kind=ClarificationKind.MISSING_QUANTITY,
                field="quantity",
                item_reference="savon",
            ),
        ),
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(MessageIntent.GREETING)
        )
    )

    outcome = app.process_message(convo, inbound("Hello salut moi d'abord"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert outcome.metadata["arbitration_reason"] == "light_interruption"
    assert context.pending_clarification.item_reference == "savon"
    assert context.active_order_id == str(order.id)


@pytest.mark.django_db
def test_order_status_interrupt_reads_db_and_preserves_catalog_task():
    convo = conversation()
    order = Order.objects.create(
        reference="ORD-261001-A2B3",
        customer=convo.user,
        status=OrderStatus.IN_DELIVERY,
    )
    persist_context(
        convo,
        ConversationContext(
            active_journey=ActiveJourney.SELL,
            metadata={"seller_catalog_active": True},
        ),
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(MessageIntent.ORDER_STATUS)
        )
    )

    outcome = app.process_message(
        convo, inbound("Mon colis n'est jamais arrivé")
    )

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert order.reference in outcome.response.text
    assert outcome.metadata["authoritative"] is True
    assert outcome.metadata["arbitration_reason"] == "support_interruption"
    assert context.metadata["seller_catalog_active"] is True


@pytest.mark.django_db
def test_explicit_buy_suspends_seller_onboarding_and_resume_restores_it():
    convo = conversation()
    persist_context(
        convo,
        ConversationContext(
            active_journey=ActiveJourney.SELL,
            flow=ConversationFlow.SELLER_ONBOARDING,
            seller_onboarding=SellerOnboardingDraft(
                stage=SellerOnboardingStage.LOCATION,
                store_name="Chez Ada",
            ),
        ),
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(MessageIntent.CREATE_OR_UPDATE_ORDER),
        )
    )

    switch_prompt = app.process_message(convo, inbound("Je veux acheter du riz"))
    convo.refresh_from_db()
    prompt_context = ConversationContext.from_payload(convo.context)
    app.process_message(
        convo,
        inbound(
            action_id="journey.switch.confirm.buy",
            message_id="confirm-buy-switch",
        ),
    )
    convo.refresh_from_db()
    switched_context = ConversationContext.from_payload(convo.context)
    resume_prompt = app.process_message(
        convo, inbound("reprendre", message_id="resume-seller")
    )
    assert resume_prompt.metadata["journey_switch_confirmation"] is True
    resumed = app.process_message(
        convo,
        inbound(
            action_id="journey.switch.confirm.sell",
            message_id="confirm-seller-resume",
        ),
    )

    convo.refresh_from_db()
    resumed_context = ConversationContext.from_payload(convo.context)
    assert switch_prompt.metadata["journey_switch_confirmation"] is True
    assert prompt_context.active_journey == ActiveJourney.SELL
    assert switched_context.active_journey == ActiveJourney.BUY
    assert switched_context.suspended_tasks
    assert resumed_context.active_journey == ActiveJourney.SELL
    assert resumed_context.seller_onboarding.store_name == "Chez Ada"
    assert resumed.response.request_location is True


@pytest.mark.django_db
def test_cancel_persisted_customer_order_uses_real_state_machine():
    convo = conversation()
    order = Order.objects.create(
        reference="ORD-261001-C4D5",
        customer=convo.user,
        status=OrderStatus.CONFIRMED,
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(
                MessageIntent.CANCEL,
                task_target=TaskTarget.ORDER,
                order_action=OrderAction.CANCEL,
            )
        )
    )

    outcome = app.process_message(convo, inbound("Annule ma commande"))

    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED
    assert order.reference in outcome.response.text


@pytest.mark.django_db
def test_draft_and_persisted_order_require_explicit_cancel_choice():
    convo = conversation()
    draft = create_order_draft(customer=convo.user)
    persisted = Order.objects.create(
        reference="ORD-261001-E6F7",
        customer=convo.user,
        status=OrderStatus.PREPARING,
    )
    persist_context(
        convo,
        ConversationContext(
            active_journey=ActiveJourney.BUY,
            flow=ConversationFlow.ORDER_BUILDING,
            active_order_id=str(draft.id),
        ),
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(
                MessageIntent.CANCEL,
                task_target=TaskTarget.ORDER,
                order_action=OrderAction.CANCEL,
            )
        )
    )

    outcome = app.process_message(convo, inbound("Annule ma commande"))

    draft.refresh_from_db()
    persisted.refresh_from_db()
    assert outcome.metadata["clarification_required"] is True
    assert {action.action_id for action in outcome.response.actions} == {
        "order.cancel.draft",
        f"order.cancel.persisted:{persisted.id}",
    }
    assert draft.status == OrderStatus.DRAFT
    assert persisted.status == OrderStatus.PREPARING


@pytest.mark.django_db
def test_cancel_callback_rechecks_customer_ownership():
    convo = conversation()
    another = User.objects.create_user(whatsapp_phone="+237699999999")
    order = Order.objects.create(
        reference="ORD-261001-G8H9",
        customer=another,
        status=OrderStatus.CONFIRMED,
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    )

    outcome = app.process_message(
        convo,
        inbound(action_id=f"order.cancel.persisted:{order.id}"),
    )

    order.refresh_from_db()
    assert outcome.status.value == "REJECTED"
    assert outcome.metadata["authorization_denied"] is True
    assert order.status == OrderStatus.CONFIRMED


@pytest.mark.django_db
def test_non_cancellable_delivery_is_not_mutated():
    convo = conversation()
    order = Order.objects.create(
        reference="ORD-261001-J2K3",
        customer=convo.user,
        status=OrderStatus.IN_DELIVERY,
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(
                MessageIntent.CANCEL,
                task_target=TaskTarget.ORDER,
                order_action=OrderAction.CANCEL,
            )
        )
    )

    outcome = app.process_message(convo, inbound("Annule ma commande"))

    order.refresh_from_db()
    assert outcome.status.value == "REJECTED"
    assert "ne peut plus" in outcome.response.text
    assert order.status == OrderStatus.IN_DELIVERY
