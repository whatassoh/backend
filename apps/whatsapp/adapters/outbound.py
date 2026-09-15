from typing import Any

from apps.messaging.domain.dtos import MessageLocation, NormalizedOutboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


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
