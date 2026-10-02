from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.ai.application.seller_catalog_understanding import (
    SellerCatalogCommand,
    SellerCatalogIntent,
)
from apps.ai.domain.understanding import (
    ExtractedItem,
    MessageIntent,
    UserMessageUnderstanding,
)
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.seller_catalog_conversation import (
    SellerCatalogConversationService,
)
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ActiveJourney, ConversationContext
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User, UserRole
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreMembershipRole, StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreMembership, StoreOffer
from apps.telegram.adapters.inbound import parse_telegram_update
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook


class UnderstandingQueue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0)


class CatalogUnderstanding:
    def __init__(self, command=None):
        self.command = command or SellerCatalogCommand(
            intent=SellerCatalogIntent.LIST, confidence=Decimal(".99")
        )

    def understand(self, *_args, **_kwargs):
        return self.command


def seller_conversation(channel=MessagingChannel.WHATSAPP, external_id=None):
    user = User.objects.create_user(whatsapp_phone=f"+23765{User.objects.count():07d}")
    external_id = external_id or f"seller-{user.id}"
    identity = create_channel_identity(
        user=user, channel=channel, external_user_id=str(external_id)
    )
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    store = Store.objects.create(
        name="Jacob's Store",
        city="Douala",
        location=Point(9.7, 4.05, srid=4326),
    )
    StoreMembership.objects.create(
        store=store, user=user, role=StoreMembershipRole.OWNER
    )
    return conversation, store


def inbound(message_id, *, text=None, action_id=None, channel=MessagingChannel.WHATSAPP):
    return NormalizedInboundMessage(
        channel=channel,
        external_message_id=message_id,
        external_user_id="seller",
        message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT,
        text=text,
        metadata={"action_id": action_id} if action_id else {},
    )


def orchestrator(*understandings, catalog_understanding=None):
    return ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(*understandings),
        seller_catalog_conversation_service=SellerCatalogConversationService(
            understanding_service=catalog_understanding or CatalogUnderstanding()
        ),
    )


@pytest.mark.django_db
def test_existing_seller_opens_workspace_without_restarting_onboarding():
    conversation, store = seller_conversation()
    app = orchestrator(
        UserMessageUnderstanding(intent=MessageIntent.SELL, confidence=Decimal(".99"))
    )

    outcome = app.process_message(
        conversation, inbound("open", text="Je veux gérer ma boutique")
    )

    assert store.name in outcome.response.text
    assert outcome.response.metadata["seller_workspace"] is True
    assert [action.label for action in outcome.response.actions] == [
        "Gérer mon catalogue",
        "Voir ma boutique",
    ]
    conversation.refresh_from_db()
    assert ConversationContext.from_payload(conversation.context).seller_onboarding is None


@pytest.mark.django_db
def test_workspace_catalog_action_hands_control_to_existing_catalog_flow():
    conversation, store = seller_conversation()
    app = orchestrator()
    workspace = app.process_message(
        conversation, inbound("open", text="Ma boutique")
    )
    catalog_action = workspace.response.actions[0].action_id

    handoff = app.process_message(
        conversation, inbound("catalog", action_id=catalog_action)
    )
    conversation.refresh_from_db()
    context = ConversationContext.from_payload(conversation.context)

    assert handoff.metadata["catalog_handoff"] is True
    assert context.metadata["seller_catalog_active"] is True
    listed = app.process_message(
        conversation, inbound("list", text="Voir mon catalogue")
    )
    assert listed.response.text.startswith("Votre catalogue est vide.")
    assert "Décrivez le produit" in listed.response.text
    assert context.metadata["seller_store_id"] == str(store.id)


@pytest.mark.django_db
def test_completed_onboarding_does_not_capture_client_purchase():
    conversation, _store = seller_conversation()
    UserRole.objects.create(user=conversation.user, role=UserRoleType.CLIENT)
    conversation.context = ConversationContext(active_journey=ActiveJourney.SELL).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    buy = UserMessageUnderstanding(
        intent=MessageIntent.CREATE_OR_UPDATE_ORDER,
        confidence=Decimal(".99"),
        items=(ExtractedItem(requested_name="riz", quantity=Decimal("1")),),
    )
    app = orchestrator(buy)

    outcome = app.process_message(
        conversation, inbound("buy", text="Je veux acheter du riz")
    )

    conversation.refresh_from_db()
    assert (
        ConversationContext.from_payload(conversation.context).active_journey
        == ActiveJourney.BUY
    )
    assert outcome.response.metadata.get("seller_workspace") is not True


@pytest.mark.django_db
def test_seller_next_step_question_returns_deterministic_live_actions():
    conversation, store = seller_conversation()

    outcome = orchestrator().process_message(
        conversation, inbound("next", text="C'est quoi la suite ?")
    )

    assert store.name in outcome.response.text
    assert outcome.response.metadata["store_id"] == str(store.id)
    assert outcome.response.actions


@pytest.mark.django_db
def test_workspace_prioritizes_active_store_preparation():
    conversation, store = seller_conversation()
    customer = User.objects.create_user(whatsapp_phone="+237699000099")
    order = Order.objects.create(
        reference="ORD-261001-ABCD",
        customer=customer,
        status=OrderStatus.PREPARING,
    )
    StoreOffer.objects.create(
        order=order,
        store=store,
        status=StoreOfferStatus.ACCEPTED,
        rank=1,
        strategy_version="test",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    outcome = orchestrator().process_message(
        conversation, inbound("next-operational", text="Que puis-je faire maintenant ?")
    )

    assert outcome.metadata["operational_priority"] == "STORE_PREPARATION"
    assert outcome.response.actions[0].label == "Commande prête"


@pytest.mark.django_db
def test_workspace_callback_cannot_access_another_users_store():
    conversation, _store = seller_conversation()
    _other_conversation, other_store = seller_conversation()

    outcome = orchestrator().process_message(
        conversation,
        inbound("forged", action_id=f"seller.workspace.store:{other_store.id}"),
    )

    assert outcome.metadata["authorization_denied"] is True
    assert outcome.status == "REJECTED"


@pytest.mark.django_db
@pytest.mark.parametrize("channel", [MessagingChannel.WHATSAPP, MessagingChannel.TELEGRAM])
def test_normalized_channel_payload_opens_workspace_and_catalog(channel):
    external_id = "237699887766" if channel == MessagingChannel.WHATSAPP else "123456"
    conversation, _store = seller_conversation(channel, external_id)
    if channel == MessagingChannel.WHATSAPP:
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "phone-id"},
                "messages": [{
                    "from": external_id,
                    "id": "wamid.workspace",
                    "timestamp": "1700000000",
                    "type": "text",
                    "text": {"body": "Ma boutique"},
                }],
            }}]}],
        }
        message = parse_whatsapp_webhook(payload)[0]
    else:
        message = parse_telegram_update({
            "update_id": 8001,
            "message": {
                "message_id": 91,
                "date": 1700000000,
                "from": {"id": 123456, "is_bot": False, "first_name": "Jacob"},
                "chat": {"id": 123456, "type": "private"},
                "text": "Ma boutique",
            },
        })[0]

    app = orchestrator()
    workspace = app.process_message(conversation, message)
    handoff = app.process_message(
        conversation,
        inbound(
            f"{channel}.catalog",
            action_id=workspace.response.actions[0].action_id,
            channel=channel,
        ),
    )

    assert workspace.response.metadata["seller_workspace"] is True
    assert handoff.response.metadata["catalog_handoff"] is True
