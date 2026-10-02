from typing import Any

from apps.messaging.domain.dtos import MessageLocation, NormalizedOutboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.whatsapp.domain.dtos import (
    WhatsAppInteractiveListMessage,
    WhatsAppInteractiveMessage,
    WhatsAppTemplateMessage,
)
from apps.whatsapp.domain.templates import (
    get_template_definition,
    validate_template_parameters,
)


def build_text_payload(
    *,
    recipient_external_id: str,
    text: str,
    preview_url: bool = False,
) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_external_id,
        "type": "text",
        "text": {
            "preview_url": preview_url,
            "body": text,
        },
    }


def build_location_payload(
    *,
    recipient_external_id: str,
    location: MessageLocation,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_external_id,
        "type": "location",
        "location": {
            "latitude": location.latitude,
            "longitude": location.longitude,
        },
    }
    if location.name:
        payload["location"]["name"] = location.name
    if location.address:
        payload["location"]["address"] = location.address
    return payload


def build_interactive_reply_buttons_payload(
    message: WhatsAppInteractiveMessage,
) -> dict[str, Any]:
    if not 1 <= len(message.buttons) <= 3:
        raise ValueError("WhatsApp reply buttons require between 1 and 3 buttons.")

    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": message.body},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {
                        "id": button.action_id,
                        "title": button.label,
                    },
                }
                for button in message.buttons
            ]
        },
    }
    if message.header_text:
        interactive["header"] = {"type": "text", "text": message.header_text}
    if message.footer_text:
        interactive["footer"] = {"text": message.footer_text}

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": message.recipient_external_id,
        "type": "interactive",
        "interactive": interactive,
    }


def build_interactive_list_payload(
    message: WhatsAppInteractiveListMessage,
) -> dict[str, Any]:
    if not 1 <= len(message.rows) <= 10:
        raise ValueError("WhatsApp interactive lists require between 1 and 10 rows.")

    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": message.body},
        "action": {
            "button": message.button_label,
            "sections": [
                {
                    "rows": [
                        {"id": row.action_id, "title": row.label}
                        for row in message.rows
                    ]
                }
            ],
        },
    }
    if message.header_text:
        interactive["header"] = {"type": "text", "text": message.header_text}
    if message.footer_text:
        interactive["footer"] = {"text": message.footer_text}

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": message.recipient_external_id,
        "type": "interactive",
        "interactive": interactive,
    }


def build_template_payload(message: WhatsAppTemplateMessage) -> dict[str, Any]:
    definition = get_template_definition(
        message.template_key,
        language_code=message.language_code,
    )
    validate_template_parameters(
        definition,
        body_parameters=message.body_parameters,
        header_parameters=message.header_parameters,
        button_parameters=message.button_parameters,
    )
    components = []
    if definition.header_parameters:
        components.append(
            {
                "type": "header",
                "parameters": [
                    {"type": "text", "text": message.header_parameters[name]}
                    for name in definition.header_parameters
                ],
            }
        )
    if definition.body_parameters:
        components.append(
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": message.body_parameters[name]}
                    for name in definition.body_parameters
                ],
            }
        )
    if definition.button_parameters:
        components.append(
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": "0",
                "parameters": [
                    {"type": "payload", "payload": message.button_parameters[name]}
                    for name in definition.button_parameters
                ],
            }
        )

    template: dict[str, Any] = {
        "name": definition.meta_name,
        "language": {"code": definition.language_code},
    }
    if components:
        template["components"] = components
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": message.recipient_external_id,
        "type": "template",
        "template": template,
    }


def outbound_message_to_payload(message: NormalizedOutboundMessage) -> dict[str, Any]:
    if message.channel != MessagingChannel.WHATSAPP:
        raise ValueError("WhatsApp payloads require WHATSAPP channel.")
    if message.message_type == MessageType.TEXT and message.text:
        return build_text_payload(
            recipient_external_id=message.recipient_external_id,
            text=message.text,
        )
    if message.message_type == MessageType.LOCATION and message.location:
        return build_location_payload(
            recipient_external_id=message.recipient_external_id,
            location=message.location,
        )
    raise ValueError("Unsupported WhatsApp outbound message.")
