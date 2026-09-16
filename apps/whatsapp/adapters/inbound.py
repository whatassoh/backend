from datetime import UTC, datetime
from typing import Any

from django.core.exceptions import ValidationError

from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.whatsapp.exceptions import WhatsAppInvalidPayloadError

SUPPORTED_TYPE_MAP = {
    "text": MessageType.TEXT,
    "location": MessageType.LOCATION,
    "image": MessageType.IMAGE,
    "document": MessageType.DOCUMENT,
    "audio": MessageType.AUDIO,
    "video": MessageType.VIDEO,
    "interactive": MessageType.INTERACTIVE,
}


def parse_whatsapp_webhook(payload: dict[str, Any]) -> list[NormalizedInboundMessage]:
    if not isinstance(payload, dict):
        raise WhatsAppInvalidPayloadError("WhatsApp webhook payload must be an object.")

    messages: list[NormalizedInboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = {
                "whatsapp": {
                    "entry_id": entry.get("id"),
                    "field": change.get("field"),
                    "phone_number_id": value.get("metadata", {}).get("phone_number_id"),
                    "display_phone_number": value.get("metadata", {}).get(
                        "display_phone_number"
                    ),
                }
            }
            for message in value.get("messages", []):
                messages.append(_parse_message(message, metadata))

    return messages


def _parse_message(
    message: dict[str, Any],
    metadata: dict[str, Any],
) -> NormalizedInboundMessage:
    external_message_id = message.get("id")
    external_user_id = message.get("from")
    if not external_message_id or not external_user_id:
        raise WhatsAppInvalidPayloadError("WhatsApp message is missing id or from.")

    raw_type = message.get("type")
    message_type = SUPPORTED_TYPE_MAP.get(raw_type, MessageType.UNKNOWN)
    text = None
    location = None

    if raw_type == "text":
        text = message.get("text", {}).get("body")
    elif raw_type == "location":
        location_payload = message.get("location", {})
        try:
            location = MessageLocation(
                latitude=float(location_payload["latitude"]),
                longitude=float(location_payload["longitude"]),
                name=location_payload.get("name"),
                address=location_payload.get("address"),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise WhatsAppInvalidPayloadError(
                "WhatsApp location payload is invalid."
            ) from exc
    elif raw_type == "interactive":
        metadata = _interactive_metadata(message, metadata)

    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=external_message_id,
        external_user_id=external_user_id,
        external_chat_id=external_user_id,
        message_type=message_type,
        text=text,
        location=location,
        occurred_at=_parse_timestamp(message.get("timestamp")),
        raw_payload=message,
        metadata=metadata,
    )


def _parse_timestamp(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise WhatsAppInvalidPayloadError("WhatsApp timestamp is invalid.") from exc


def _interactive_metadata(
    message: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    interactive = message.get("interactive") or {}
    interaction_type = interactive.get("type")
    enriched = {**metadata}
    whatsapp_metadata = {**enriched.get("whatsapp", {})}
    whatsapp_metadata["interaction_type"] = interaction_type

    if interaction_type == "button_reply":
        reply = interactive.get("button_reply") or {}
        whatsapp_metadata["action_id"] = reply.get("id")
        whatsapp_metadata["button_title"] = reply.get("title")
    elif interaction_type == "list_reply":
        reply = interactive.get("list_reply") or {}
        whatsapp_metadata["action_id"] = reply.get("id")
        whatsapp_metadata["list_title"] = reply.get("title")
        whatsapp_metadata["list_description"] = reply.get("description")

    if not whatsapp_metadata.get("action_id"):
        raise WhatsAppInvalidPayloadError("WhatsApp interactive reply is invalid.")

    enriched["whatsapp"] = whatsapp_metadata
    return enriched
