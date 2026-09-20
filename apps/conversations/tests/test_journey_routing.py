from decimal import Decimal

import pytest

from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.conversations.application.journey import JourneyRoutingStatus
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ActiveJourney, ConversationContext
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
    assert [item.action_id for item in outcome.response.actions] == ["journey.select.buy", "journey.select.sell", "journey.select.deliver"]
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
