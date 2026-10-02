from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.ai.domain.understanding import (
    ExtractedItem,
    MessageIntent,
    OrderAction,
    UserMessageUnderstanding,
)
from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
)
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
)
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.application.services import (
    add_order_item,
    create_order_draft,
    set_delivery_location,
)
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.stores.application.services import create_store
from apps.whatsapp.domain.response_validation import validate_response_for_channel


class Queue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0)


def convo():
    user = User.objects.create_user(whatsapp_phone=f"+23767{User.objects.count():07d}")
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"p1e-{user.id}",
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def inbound(text=None, *, action_id=None, message_id="p1e-1"):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=message_id,
        external_user_id="p1e",
        message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT,
        text=text,
        metadata={"action_id": action_id} if action_id else {},
    )


def draft_context(conversation, *, location=True):
    order = create_order_draft(customer=conversation.user)
    product = create_product(name="Riz", category=create_category(name="Alimentation"))
    add_order_item(
        order=order,
        requested_name="Riz",
        quantity=4,
        unit=ProductUnit.KG,
        product=product,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    if location:
        set_delivery_location(order=order, point=Point(9.7, 4.05, srid=4326))
    conversation.context = ConversationContext(
        active_journey=ActiveJourney.BUY,
        active_order_id=str(order.id),
        flow=ConversationFlow.ORDER_BUILDING,
    ).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    return order


@pytest.mark.django_db
def test_real_finish_phrase_shows_review_before_confirmation():
    conversation = convo()
    order = draft_context(conversation)
    app = ConversationalOrchestrator(
        understanding_service=Queue(
            UserMessageUnderstanding(
                intent=MessageIntent.CONFIRM,
                confidence=Decimal(".99"),
                order_action=OrderAction.REVIEW,
            )
        )
    )

    review = app.process_message(
        conversation, inbound("C'est tout, je veux confirmer ma commande")
    )

    order.refresh_from_db()
    conversation.refresh_from_db()
    assert "Récapitulatif" in review.response.text
    assert "order.confirm" in {action.action_id for action in review.response.actions}
    assert ConversationContext.from_payload(conversation.context).flow == ConversationFlow.AWAITING_CONFIRMATION
    assert order.status == OrderStatus.DRAFT

    app.process_message(
        conversation,
        inbound(action_id="order.confirm", message_id="p1e-confirm"),
    )
    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED


@pytest.mark.django_db
def test_finish_missing_location_is_human_and_offers_location_action():
    conversation = convo()
    order = draft_context(conversation, location=False)
    app = ConversationalOrchestrator(
        understanding_service=Queue(
            UserMessageUnderstanding(
                intent=MessageIntent.CONFIRM,
                confidence=Decimal(".99"),
                order_action=OrderAction.REVIEW,
            )
        )
    )

    review = app.process_message(conversation, inbound("C'est tout"))

    order.refresh_from_db()
    assert "lieu de livraison" in review.response.text
    assert "delivery_location_missing" not in review.response.text
    assert "order.change_location" in {a.action_id for a in review.response.actions}
    assert order.status != OrderStatus.CONFIRMED


@pytest.mark.django_db
def test_modify_location_text_enters_location_flow():
    conversation = convo()
    draft_context(conversation, location=False)
    app = ConversationalOrchestrator(understanding_service=Queue())
    app.process_message(conversation, inbound(action_id="order.modify"))
    conversation.refresh_from_db()

    outcome = app.process_message(
        conversation, inbound("Ma localisation", message_id="p1e-location")
    )

    conversation.refresh_from_db()
    assert "Partagez" in outcome.response.text
    assert ConversationContext.from_payload(conversation.context).flow == ConversationFlow.AWAITING_LOCATION


@pytest.mark.django_db
def test_position_skip_produces_whatsapp_deliverable_recap():
    """Regression for position shared -> non, continue -> confirmation recap."""
    conversation = convo()
    draft_context(conversation, location=False)
    app = ConversationalOrchestrator(understanding_service=Queue())

    app.process_message(
        conversation,
        inbound(action_id="order.change_location", message_id="change-location"),
    )
    location_outcome = app.process_message(
        conversation,
        NormalizedInboundMessage(
            channel=MessagingChannel.WHATSAPP,
            external_message_id="shared-location",
            external_user_id="p1e",
            message_type=MessageType.LOCATION,
            location=MessageLocation(latitude=4.05, longitude=9.7),
        ),
    )
    assert "Position enregistrée" in location_outcome.response.text

    recap = app.process_message(
        conversation,
        inbound(
            action_id="order.skip_delivery_instructions",
            message_id="skip-instructions",
        ),
    )

    conversation.refresh_from_db()
    assert (
        ConversationContext.from_payload(conversation.context).flow
        == ConversationFlow.AWAITING_CONFIRMATION
    )
    assert len(recap.response.actions) <= 3
    assert validate_response_for_channel(recap.response, MessagingChannel.WHATSAPP)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("phrase", "category", "product_name"),
    [
        ("Je veux un truc à manger", "Alimentation", "Farine de blé"),
        ("Je veux un truc à boire", "Boissons", "Jus d'ananas"),
        ("Je cherche quelque chose pour bébé", "Bébé", "Couches bébé"),
        ("Je cherche quelque chose pour nettoyer", "Hygiène", "Détergent"),
    ],
)
def test_real_need_phrase_resolves_a_real_category(phrase, category, product_name):
    store = create_store(
        name="Marché", location=Point(9.7, 4.05, srid=4326), city="Douala"
    )
    product = create_product(name=product_name, category=create_category(name=category))
    add_product_to_store(
        store=store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )
    conversation = convo()
    app = ConversationalOrchestrator(
        understanding_service=Queue(
            UserMessageUnderstanding(
                intent=MessageIntent.CATALOG_BROWSE,
                confidence=Decimal(".99"),
                items=(ExtractedItem(requested_name=phrase),),
            )
        )
    )

    outcome = app.process_message(conversation, inbound(phrase))

    assert product_name in outcome.response.text


@pytest.mark.django_db
def test_order_details_prefers_active_draft_and_lists_db_items():
    conversation = convo()
    order = draft_context(conversation, location=False)
    app = ConversationalOrchestrator(
        understanding_service=Queue(
            UserMessageUnderstanding(
                intent=MessageIntent.ORDER_DETAILS, confidence=Decimal(".99")
            )
        )
    )

    outcome = app.process_message(
        conversation, inbound("Montre les éléments de ma commande actuelle")
    )

    assert order.reference in outcome.response.text
    assert "4.000 × Riz" in outcome.response.text
    assert "Lieu de livraison : à compléter" in outcome.response.text
