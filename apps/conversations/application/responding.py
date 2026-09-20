"""Render channel-neutral response plans at the messaging edge."""

import logging

from apps.messaging.domain.enums import MessagingChannel
from apps.messaging.domain.exceptions import MessagingError
from apps.telegram.exceptions import TelegramError
from apps.whatsapp.exceptions import WhatsAppError

logger = logging.getLogger(__name__)


def deliver_outcome_response(*, conversation, outcome, correlation_id=None) -> bool:
    """Deliver one non-empty response from a processed inbound turn.

    Conversation processing only produces an outcome; the channel edge performs
    delivery.  Sharing this decision keeps Telegram and WhatsApp aligned and
    limits every processed webhook message to one delivery attempt.

    Provider errors are deliberately acknowledged after logging: the inbound
    turn was already persisted, so retrying the webhook must not rerun domain
    processing.
    """
    response = getattr(outcome, "response", None)
    if response is None:
        return False

    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        return False

    try:
        send_response_plan(
            conversation=conversation,
            response=response,
            correlation_id=correlation_id,
        )
    except (MessagingError, TelegramError, WhatsAppError):
        logger.exception(
            "conversation_response_delivery_failed",
            extra={"channel": conversation.channel, "correlation_id": correlation_id},
        )
        return False
    return True


def send_response_plan(*, conversation, response, correlation_id=None):
    """Use provider adapters only after business routing has produced a plan."""
    if not response.actions:
        from apps.messaging.application.orchestration import send_conversation_text

        return send_conversation_text(
            conversation=conversation, text=response.text, correlation_id=correlation_id
        )
    actions = [
        {"id": action.action_id, "label": action.label} for action in response.actions
    ]
    if conversation.channel == MessagingChannel.WHATSAPP:
        from apps.whatsapp.application.outbound import (
            send_whatsapp_interactive_reply_buttons,
        )
        from apps.whatsapp.domain.dtos import WhatsAppReplyButton

        return send_whatsapp_interactive_reply_buttons(
            conversation=conversation,
            body=response.text,
            buttons=[
                WhatsAppReplyButton(action_id=item["id"], label=item["label"])
                for item in actions
            ],
            metadata={"response_actions": actions, **response.metadata},
        )
    from apps.messaging.application.orchestration import send
    from apps.messaging.domain.dtos import NormalizedOutboundMessage
    from apps.messaging.domain.enums import MessageType

    recipient = (
        conversation.external_chat_id or conversation.channel_identity.external_user_id
    )
    result = send(
        NormalizedOutboundMessage(
            channel=conversation.channel,
            recipient_external_id=recipient,
            message_type=MessageType.INTERACTIVE,
            text=response.text,
            metadata={
                "actions": actions,
                "response_actions": actions,
                **response.metadata,
            },
        )
    )
    from apps.conversations.application.services import record_outbound_message

    record_outbound_message(
        conversation=conversation,
        message=NormalizedOutboundMessage(
            channel=conversation.channel,
            recipient_external_id=recipient,
            message_type=MessageType.INTERACTIVE,
            text=response.text,
            metadata={"actions": actions, **response.metadata},
        ),
        message_event=result.message_event,
    )
    return result
