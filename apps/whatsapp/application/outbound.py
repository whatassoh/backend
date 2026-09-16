from apps.conversations.application.services import record_outbound_message
from apps.conversations.infrastructure.models import Conversation
from apps.messaging.domain.dtos import NormalizedOutboundMessage, ProviderSendResult
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.messaging.domain.exceptions import MessagingTemporaryError
from apps.whatsapp.domain.dtos import (
    WhatsAppInteractiveMessage,
    WhatsAppReplyButton,
    WhatsAppTemplateMessage,
)
from apps.whatsapp.domain.enums import WhatsAppOutboundDecision, WhatsAppTemplateKey
from apps.whatsapp.domain.exceptions import WhatsAppTemplateRequired
from apps.whatsapp.domain.outbound_policy import WhatsAppOutboundPolicy
from apps.whatsapp.providers.cloud_api import WhatsAppProvider


def send_whatsapp_conversation_text(
    *,
    conversation: Conversation,
    text: str,
    metadata: dict | None = None,
) -> ProviderSendResult:
    if (
        WhatsAppOutboundPolicy().decide(conversation)
        == WhatsAppOutboundDecision.TEMPLATE_REQUIRED
    ):
        raise WhatsAppTemplateRequired("WhatsApp service window is closed.")

    from apps.messaging.application.orchestration import send_conversation_text

    return send_conversation_text(
        conversation=conversation,
        text=text,
        metadata=metadata,
    )


def send_whatsapp_interactive_reply_buttons(
    *,
    conversation: Conversation,
    body: str,
    buttons: list[WhatsAppReplyButton],
    header_text: str | None = None,
    footer_text: str | None = None,
    metadata: dict | None = None,
) -> ProviderSendResult:
    if conversation.channel != MessagingChannel.WHATSAPP:
        raise MessagingTemporaryError("Conversation is not a WhatsApp conversation.")
    if (
        WhatsAppOutboundPolicy().decide(conversation)
        == WhatsAppOutboundDecision.TEMPLATE_REQUIRED
    ):
        raise WhatsAppTemplateRequired("WhatsApp service window is closed.")

    recipient_external_id = (
        conversation.external_chat_id or conversation.channel_identity.external_user_id
    )
    message = WhatsAppInteractiveMessage(
        recipient_external_id=recipient_external_id,
        body=body,
        buttons=buttons,
        header_text=header_text,
        footer_text=footer_text,
        metadata=metadata or {},
    )
    result = WhatsAppProvider().send_interactive_reply_buttons(message)
    record_outbound_message(
        conversation=conversation,
        message=NormalizedOutboundMessage(
            channel=MessagingChannel.WHATSAPP,
            recipient_external_id=recipient_external_id,
            message_type=MessageType.INTERACTIVE,
            text=body,
            metadata={
                **(metadata or {}),
                "whatsapp": {
                    "interactive_type": "button",
                    "actions": [
                        {"id": button.action_id, "label": button.label}
                        for button in buttons
                    ],
                },
            },
        ),
        message_event=result.message_event,
    )
    return result


def send_whatsapp_template_to_conversation(
    *,
    conversation: Conversation,
    template_key: WhatsAppTemplateKey | str,
    body_parameters: dict[str, str],
    language_code: str | None = None,
    metadata: dict | None = None,
) -> ProviderSendResult:
    if conversation.channel != MessagingChannel.WHATSAPP:
        raise MessagingTemporaryError("Conversation is not a WhatsApp conversation.")

    recipient_external_id = (
        conversation.external_chat_id or conversation.channel_identity.external_user_id
    )
    template_message = WhatsAppTemplateMessage(
        recipient_external_id=recipient_external_id,
        template_key=template_key,
        language_code=language_code,
        body_parameters=body_parameters,
        metadata=metadata or {},
    )
    result = WhatsAppProvider().send_template(template_message)
    record_outbound_message(
        conversation=conversation,
        message=NormalizedOutboundMessage(
            channel=MessagingChannel.WHATSAPP,
            recipient_external_id=recipient_external_id,
            message_type=MessageType.TEMPLATE,
            text=f"[template:{template_key}]",
            metadata={
                **(metadata or {}),
                "whatsapp": {
                    "template_key": str(template_key),
                    "language_code": language_code,
                },
            },
        ),
        message_event=result.message_event,
    )
    return result


def send_whatsapp_text_with_template_fallback(
    *,
    conversation: Conversation,
    free_form_text: str,
    template_key: WhatsAppTemplateKey | str,
    template_body_parameters: dict[str, str],
    metadata: dict | None = None,
) -> ProviderSendResult:
    if (
        WhatsAppOutboundPolicy().decide(conversation)
        == WhatsAppOutboundDecision.FREE_FORM_ALLOWED
    ):
        return send_whatsapp_conversation_text(
            conversation=conversation,
            text=free_form_text,
            metadata=metadata,
        )
    return send_whatsapp_template_to_conversation(
        conversation=conversation,
        template_key=template_key,
        body_parameters=template_body_parameters,
        metadata=metadata,
    )
