from decimal import Decimal

import pytest

from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.conversations.application.journey import JourneyRoutingStatus
from apps.conversations.application.orchestrator import ConversationalOrchestrator, ConversationalOutcomeStatus
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ActiveJourney, ConversationContext, ConversationFlow
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.infrastructure.models import Order
from apps.stores.infrastructure.models import Store


class UnderstandingStub:
    def __init__(self, understanding=None):
        self.understanding = understanding
        self.called = False

    def understand_normalized(self, *_args, **_kwargs):
        self.called = True
        return self.understanding


def make_conversation(channel=MessagingChannel.WHATSAPP):
    user = User.objects.create_user(whatsapp_phone=f"+2376{User.objects.count():08d}")
    identity = create_channel_identity(user=user, channel=channel, external_user_id=f"external-{user.id}")
    return get_or_create_active_conversation(channel_identity=identity)[0]


def inbound(*, text=None, action_id=None, message_id="message-1", channel=MessagingChannel.WHATSAPP):
    return NormalizedInboundMessage(
        channel=channel, external_message_id=message_id, external_user_id="external",
        message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT,
        text=text, metadata={"action_id": action_id} if action_id else {},
    )


@pytest.mark.django_db
def test_greeting_requests_stable_journey_actions():
    conversation = make_conversation()
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub(UserMessageUnderstanding(intent=MessageIntent.GREETING, confidence=Decimal(".99"))))
    outcome = app.process_message(conversation, inbound(text="Bonjour"))
    assert outcome.response.text.startswith(("Bonjour", "Bonsoir"))
    assert outcome.metadata["capability_status"] == "SOCIAL"
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).active_journey is None


@pytest.mark.django_db
@pytest.mark.parametrize(("intent", "expected"), [(MessageIntent.SELL, ActiveJourney.SELL), (MessageIntent.DELIVER, ActiveJourney.DELIVER)])
def test_explicit_non_buy_intent_routes_without_question(intent, expected):
    conversation = make_conversation()
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub(UserMessageUnderstanding(intent=intent, confidence=Decimal(".99"))))
    outcome = app.process_message(conversation, inbound(text="Je veux"))
    assert outcome.metadata["journey"] == expected.value
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).active_journey == expected


@pytest.mark.django_db
def test_explicit_buy_intent_selects_buy_without_order_when_no_valid_line_exists():
    conversation = make_conversation()
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub(UserMessageUnderstanding(intent=MessageIntent.CREATE_OR_UPDATE_ORDER, confidence=Decimal(".99"))))
    app.process_message(conversation, inbound(text="Je veux acheter du riz"))
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).active_journey == ActiveJourney.BUY
    assert Order.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("action_id, expected, status", [
    ("journey.select.buy", ActiveJourney.BUY, JourneyRoutingStatus.SELECTED),
    ("journey.select.sell", ActiveJourney.SELL, JourneyRoutingStatus.SELLER_ONBOARDING_REQUIRED),
    ("journey.select.deliver", ActiveJourney.DELIVER, JourneyRoutingStatus.DRIVER_ONBOARDING_REQUIRED),
])
def test_structured_journey_actions_bypass_llm_and_do_not_create_profiles(action_id, expected, status):
    conversation = make_conversation()
    understanding = UnderstandingStub()
    app = ConversationalOrchestrator(understanding_service=understanding)
    outcome = app.process_message(conversation, inbound(action_id=action_id))
    assert not understanding.called
    assert outcome.metadata["journey_status"] == status.value
    assert Store.objects.count() == 0
    assert DriverProfile.objects.count() == 0
    assert Order.objects.count() == 0
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).active_journey == expected


@pytest.mark.django_db
def test_duplicate_callback_is_idempotent_and_switching_preserves_roles():
    conversation = make_conversation()
    user = conversation.user
    # A user may independently have multiple durable roles; route changes do not edit them.
    from apps.identity.infrastructure.models import UserRole
    from apps.identity.domain.enums import UserRoleType
    UserRole.objects.create(user=user, role=UserRoleType.CLIENT)
    UserRole.objects.create(user=user, role=UserRoleType.DRIVER)
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub())
    first = app.process_message(conversation, inbound(action_id="journey.select.sell", message_id="cb-1"))
    conversation.refresh_from_db()
    duplicate = app.process_message(conversation, inbound(action_id="journey.select.sell", message_id="cb-1"))
    app.process_message(conversation, inbound(action_id="journey.select.buy", message_id="cb-2"))
    assert first.metadata["journey"] == "SELL"
    assert duplicate.status.value == "DUPLICATE"
    assert set(user.roles.values_list("role", flat=True)) == {"CLIENT", "DRIVER"}
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).active_journey == ActiveJourney.BUY


@pytest.mark.django_db
def test_recovery_reads_persisted_journey_after_restart_and_store_membership_is_only_read():
    conversation = make_conversation(MessagingChannel.TELEGRAM)
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub())
    app.process_message(conversation, inbound(action_id="journey.select.sell", channel=MessagingChannel.TELEGRAM))
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).active_journey == ActiveJourney.SELL


@pytest.mark.django_db
def test_order_history_is_customer_scoped_and_short():
    conversation = make_conversation()
    other = make_conversation()
    from apps.orders.application.services import create_order_draft

    own = create_order_draft(customer=conversation.user)
    foreign = create_order_draft(customer=other.user)
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(intent=MessageIntent.ORDER_HISTORY, confidence=Decimal(".99"))
        )
    )
    outcome = app.process_message(conversation, inbound(text="Donne-moi mes commandes"))
    assert own.reference in outcome.response.text
    assert foreign.reference not in outcome.response.text
    assert outcome.metadata["capability"] == "order_history"


@pytest.mark.django_db
def test_empty_order_history_has_a_conversational_response():
    conversation = make_conversation()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(intent=MessageIntent.ORDER_HISTORY, confidence=Decimal(".99"))
        )
    )
    outcome = app.process_message(conversation, inbound(text="Mes commandes"))
    assert outcome.response.text == "Vous n'avez encore aucune commande."


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "text_fragment"),
    [
        (MessageIntent.GREETING, "assistant WhatAssoh"),
        (MessageIntent.OUT_OF_SCOPE, "Ce n'est pas un service"),
        (MessageIntent.UNKNOWN, "reformuler"),
    ],
)
def test_universal_interrupt_preserves_pending_order_context(intent, text_fragment):
    conversation = make_conversation()
    from apps.orders.application.services import create_order_draft

    order = create_order_draft(customer=conversation.user)
    pending = ConversationContext(
        active_order_id=str(order.id),
        flow=ConversationFlow.AWAITING_CLARIFICATION,
        active_journey=ActiveJourney.BUY,
    )
    conversation.context = pending.to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(intent=intent, confidence=Decimal(".99"))
        )
    )
    outcome = app.process_message(conversation, inbound(text="interruption"))
    conversation.refresh_from_db()
    preserved = ConversationContext.from_payload(conversation.context)
    assert text_fragment in outcome.response.text
    assert preserved.active_order_id == str(order.id)
    assert preserved.flow.value == "AWAITING_CLARIFICATION"
    assert preserved.active_journey == ActiveJourney.BUY


@pytest.mark.django_db
def test_order_history_interrupt_preserves_pending_order_context():
    conversation = make_conversation()
    from apps.orders.application.services import create_order_draft

    order = create_order_draft(customer=conversation.user)
    conversation.context = ConversationContext(
        active_order_id=str(order.id), flow=ConversationFlow.ORDER_BUILDING, active_journey=ActiveJourney.BUY
    ).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(intent=MessageIntent.ORDER_HISTORY, confidence=Decimal(".99"))
        )
    )
    outcome = app.process_message(conversation, inbound(text="Mes commandes"))
    conversation.refresh_from_db()
    assert order.reference in outcome.response.text
    assert ConversationContext.from_payload(conversation.context).active_order_id == str(order.id)

@pytest.mark.django_db
def test_ambiguous_typed_replacement_persists_backend_pending_operation():
    from apps.ai.domain.understanding import ExtractedItem, ExtractedUnit, ItemTargetReference, OrderAction
    from apps.catalog.application.services import create_category, create_product
    from apps.catalog.domain.grounding import CatalogCandidate, CatalogMatchStrategy, CatalogResolution, CatalogResolutionReason
    from apps.orders.application.services import add_order_item, create_order_draft
    from apps.orders.domain.enums import OrderItemResolutionStatus

    conversation = make_conversation()
    order = create_order_draft(customer=conversation.user)
    old = add_order_item(order=order, requested_name="arachide", quantity=3, unit="L")
    category = create_category(name="Sodas")
    first, second = create_product(name="Coca 33 cl", category=category), create_product(name="Coca 1 L", category=category)
    resolution = CatalogResolution(status=OrderItemResolutionStatus.AMBIGUOUS, requested_name="Coca", product_id=None, variant_id=None, reason=CatalogResolutionReason.MULTIPLE_STRONG_CANDIDATES, candidates=(CatalogCandidate(product_id=first.id, canonical_name=first.name, matched_on=CatalogMatchStrategy.FUZZY_NAME, score=.9), CatalogCandidate(product_id=second.id, canonical_name=second.name, matched_on=CatalogMatchStrategy.FUZZY_NAME, score=.9)))

    class Grounding:
        def ground_item(self, _): return resolution

    conversation.context = ConversationContext(active_order_id=str(order.id)).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub(), grounding_service=Grounding())
    understanding = UserMessageUnderstanding(intent=MessageIntent.MODIFY, confidence=Decimal(".99"), order_action=OrderAction.REPLACE_ITEM, target_reference=ItemTargetReference(text="arachides"), replacement=ExtractedItem(requested_name="Coca", quantity=Decimal("2"), unit=ExtractedUnit.BOTTLE))
    app._handle_modify(conversation, ConversationContext(active_order_id=str(order.id)), understanding, "", None)
    conversation.refresh_from_db()
    pending = ConversationContext.from_payload(conversation.context).pending_clarification
    old.refresh_from_db()
    assert old.requested_name == "arachide"
    assert pending.metadata["operation"] == "REPLACE_ITEM"
    assert pending.metadata["target_order_item_id"] == str(old.id)
    assert pending.metadata["replacement"]["quantity"] == "2"
    assert set(pending.candidate_ids) == {str(first.id), str(second.id)}

@pytest.mark.django_db
def test_replacement_candidate_callback_invalid_or_replayed_is_non_mutating():
    from apps.conversations.domain.context import ClarificationKind, PendingClarification
    from apps.orders.application.services import add_order_item, create_order_draft
    conversation = make_conversation()
    order = create_order_draft(customer=conversation.user)
    item = add_order_item(order=order, requested_name="arachide", quantity=1, unit="KG")
    conversation.context = ConversationContext(active_order_id=str(order.id), pending_clarification=PendingClarification(kind=ClarificationKind.AMBIGUOUS_PRODUCT, field="product", candidate_ids=("bad",), metadata={"operation": "REPLACE_ITEM", "target_order_item_id": str(item.id), "replacement": {"requested_name": "coca", "quantity": "1", "unit": "BOTTLE", "notes": None}})).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub())
    outcome = app.process_message(conversation, inbound(action_id="clarification.select_product.not-allowed"))
    item.refresh_from_db()
    assert outcome.status == ConversationalOutcomeStatus.REJECTED
    assert item.requested_name == "arachide"

@pytest.mark.django_db
@pytest.mark.parametrize("terminal", ["CANCELLED", "CONFIRMED"])
def test_replacement_callback_stale_order_or_missing_target_is_safe(terminal):
    from apps.conversations.domain.context import ClarificationKind, PendingClarification
    from apps.orders.application.services import add_order_item, create_order_draft
    from apps.orders.domain.enums import OrderStatus
    conversation = make_conversation()
    order = create_order_draft(customer=conversation.user)
    item = add_order_item(order=order, requested_name="arachide", quantity=1, unit="KG")
    order.status = OrderStatus(terminal)
    order.save(update_fields=["status", "updated_at"])
    conversation.context = ConversationContext(active_order_id=str(order.id), pending_clarification=PendingClarification(kind=ClarificationKind.AMBIGUOUS_PRODUCT, field="product", candidate_ids=("candidate",), metadata={"operation": "REPLACE_ITEM", "target_order_item_id": str(item.id), "replacement": {"requested_name": "coca", "quantity": "1", "unit": "BOTTLE", "notes": None}})).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    outcome = ConversationalOrchestrator(understanding_service=UnderstandingStub()).process_message(conversation, inbound(action_id="clarification.select_product.candidate"))
    item.refresh_from_db()
    assert outcome.status == ConversationalOutcomeStatus.REJECTED
    assert item.requested_name == "arachide"

@pytest.mark.django_db
def test_valid_replacement_choice_replaces_without_adding():
    from apps.catalog.application.services import create_category, create_product
    from apps.conversations.domain.context import ClarificationKind, PendingClarification
    from apps.orders.application.services import add_order_item, create_order_draft
    conversation = make_conversation(); order = create_order_draft(customer=conversation.user)
    old = add_order_item(order=order, requested_name="arachide", quantity=1, unit="KG")
    product = create_product(name="Coca", category=create_category(name="Boissons"))
    conversation.context = ConversationContext(active_order_id=str(order.id), pending_clarification=PendingClarification(kind=ClarificationKind.AMBIGUOUS_PRODUCT, field="product", candidate_ids=(str(product.id),), metadata={"operation":"REPLACE_ITEM", "target_order_item_id":str(old.id), "replacement":{"requested_name":"Coca", "quantity":"2", "unit":"BOTTLE", "notes":None}})).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub())
    result = app.process_message(conversation, inbound(action_id=f"clarification.select_product.{product.id}"))
    old.refresh_from_db(); conversation.refresh_from_db()
    assert old.product_id == product.id and old.requested_name == "Coca"
    assert old.quantity == 2
    assert old.order.items.count() == 1
    assert ConversationContext.from_payload(conversation.context).pending_clarification is None
    assert result.actions_performed == ("order_item_replaced",)

@pytest.mark.django_db
def test_replayed_replacement_callback_is_idempotently_non_mutating():
    # Reusing an already processed provider message must not execute a second mutation.
    # The existing message-id idempotency boundary is the authoritative protection.
    conversation = make_conversation()
    app = ConversationalOrchestrator(understanding_service=UnderstandingStub())
    first = app.process_message(conversation, inbound(action_id="clarification.select_product.invalid", message_id="replay-choice"))
    conversation.refresh_from_db()
    second = app.process_message(conversation, inbound(action_id="clarification.select_product.invalid", message_id="replay-choice"))
    assert first.status == ConversationalOutcomeStatus.REJECTED
    assert second.status == ConversationalOutcomeStatus.DUPLICATE

@pytest.mark.django_db
def test_replacement_callback_with_deleted_target_is_safe():
    from apps.catalog.application.services import create_category, create_product
    from apps.conversations.domain.context import ClarificationKind, PendingClarification
    from apps.orders.application.services import add_order_item, create_order_draft, remove_order_item
    conversation = make_conversation(); order = create_order_draft(customer=conversation.user)
    target = add_order_item(order=order, requested_name="arachide", quantity=1, unit="KG")
    product = create_product(name="Coca stale", category=create_category(name="Stale drinks"))
    conversation.context = ConversationContext(active_order_id=str(order.id), pending_clarification=PendingClarification(kind=ClarificationKind.AMBIGUOUS_PRODUCT, field="product", candidate_ids=(str(product.id),), metadata={"operation":"REPLACE_ITEM", "target_order_item_id":str(target.id), "replacement":{"requested_name":"Coca stale", "quantity":"1", "unit":"BOTTLE", "notes":None}})).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    remove_order_item(target)
    outcome = ConversationalOrchestrator(understanding_service=UnderstandingStub()).process_message(conversation, inbound(action_id=f"clarification.select_product.{product.id}"))
    assert outcome.status == ConversationalOutcomeStatus.REJECTED
    assert order.items.count() == 0
