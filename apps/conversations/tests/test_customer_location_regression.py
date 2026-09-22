from decimal import Decimal

import pytest

from apps.ai.domain.understanding import ExtractedItem, ExtractedUnit, MessageIntent, UserMessageUnderstanding
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.orders.infrastructure.models import OrderItem


class RiceUnderstanding:
    def understand_normalized(self, *_args, **_kwargs):
        return UserMessageUnderstanding(
            intent=MessageIntent.CREATE_OR_UPDATE_ORDER,
            confidence=Decimal(".99"),
            items=(ExtractedItem(requested_name="sac de riz", quantity=Decimal("1"), unit=ExtractedUnit.BAG),),
        )


def inbound(message_id, *, channel, text=None, location=None, action_id=None):
    return NormalizedInboundMessage(
        channel=channel,
        external_message_id=message_id,
        external_user_id="customer",
        message_type=MessageType.INTERACTIVE if action_id else (MessageType.LOCATION if location else MessageType.TEXT),
        text=text,
        location=location,
        metadata={"action_id": action_id} if action_id else {},
    )


@pytest.mark.django_db
@pytest.mark.parametrize("channel", [MessagingChannel.TELEGRAM, MessagingChannel.WHATSAPP])
def test_empty_catalog_location_builds_next_response_without_type_error(channel):
    user = User.objects.create_user(whatsapp_phone=f"+2376900{User.objects.count():06d}")
    identity = create_channel_identity(user=user, channel=channel, external_user_id=f"customer-{user.id}")
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    app = ConversationalOrchestrator(understanding_service=RiceUnderstanding())

    app.process_message(conversation, inbound("1", channel=channel, action_id="journey.select.buy"))
    initial = app.process_message(conversation, inbound("2", channel=channel, text="Je veux un sac de riz"))
    assert "Où souhaitez-vous être livré" in initial.response.text
    item = OrderItem.objects.get()
    assert item.resolution_status == OrderItemResolutionStatus.UNMATCHED
    assert item.product_id is None

    conversation.refresh_from_db()
    located = app.process_message(conversation, inbound("3", channel=channel, location=MessageLocation(latitude=4.05, longitude=9.70)))
    assert "Position enregistrée" in located.response.text
    assert [action.action_id for action in located.response.actions] == ["order.skip_delivery_instructions"]
    conversation.refresh_from_db()
    order = app.active_order_service.get_active_order(conversation).order
    assert (order.delivery_location.x, order.delivery_location.y) == (9.70, 4.05)

    conversation.refresh_from_db()
    review = app.process_message(conversation, inbound("4", channel=channel, action_id="order.skip_delivery_instructions"))
    assert "Commande à compléter" in review.response.text
    assert "order.confirm" not in [action.action_id for action in review.response.actions]
