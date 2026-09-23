from decimal import Decimal

import pytest

from apps.ai.domain.understanding import ExtractedItem, ExtractedUnit, MessageIntent, UserMessageUnderstanding
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ConversationContext
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
    assert "n’est pas disponible" in initial.response.text
    assert not initial.response.request_location
    assert [action.action_id for action in initial.response.actions] == [
        "order.unmatched.remove", "order.unmatched.replace"
    ]
    item = OrderItem.objects.get()
    assert item.resolution_status == OrderItemResolutionStatus.UNMATCHED
    assert item.product_id is None

@pytest.mark.django_db
def test_unmatched_remove_is_safe_and_stale_replace_does_not_mutate():
    user = User.objects.create_user(whatsapp_phone="+237690099999")
    identity = create_channel_identity(
        user=user, channel=MessagingChannel.WHATSAPP, external_user_id="lot5-stale"
    )
    conversation = get_or_create_active_conversation(channel_identity=identity)[0]
    app = ConversationalOrchestrator(understanding_service=RiceUnderstanding())

    app.process_message(conversation, inbound("journey", channel=MessagingChannel.WHATSAPP, action_id="journey.select.buy"))
    app.process_message(conversation, inbound("unmatched", channel=MessagingChannel.WHATSAPP, text="Je veux un sac de riz"))
    removed = app.process_message(conversation, inbound("remove", channel=MessagingChannel.WHATSAPP, action_id="order.unmatched.remove"))

    assert "vide" in removed.response.text
    assert OrderItem.objects.count() == 0
    stale = app.process_message(conversation, inbound("stale", channel=MessagingChannel.WHATSAPP, action_id="order.unmatched.replace"))
    assert stale.status.value == "REJECTED"
    assert OrderItem.objects.count() == 0
