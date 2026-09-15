from typing import Any

from apps.messaging.domain.dtos import MessageLocation, NormalizedOutboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


def build_text_payload(
    *,
    chat_id: str,
    text: str,
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "text": text,
    }


def build_location_payload(
    *,
    chat_id: str,
    location: MessageLocation,
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "latitude": location.latitude,
        "longitude": location.longitude,
    }


def outbound_message_to_method_and_payload(
    message: NormalizedOutboundMessage,
) -> tuple[str, dict[str, Any]]:
    if message.channel != MessagingChannel.TELEGRAM:
        raise ValueError("Telegram payloads require TELEGRAM channel.")
    if message.message_type == MessageType.TEXT and message.text:
        return (
            "sendMessage",
            build_text_payload(
                chat_id=message.recipient_external_id,
                text=message.text,
            ),
        )
    if message.message_type == MessageType.LOCATION and message.location:
        return (
            "sendLocation",
            build_location_payload(
                chat_id=message.recipient_external_id,
                location=message.location,
            ),
        )
    raise ValueError("Unsupported Telegram outbound message.")
