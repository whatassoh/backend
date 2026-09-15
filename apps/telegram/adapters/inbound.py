from datetime import UTC, datetime
from typing import Any

from django.core.exceptions import ValidationError

from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.telegram.exceptions import TelegramInvalidPayloadError


def parse_telegram_update(payload: dict[str, Any]) -> list[NormalizedInboundMessage]:
    if not isinstance(payload, dict):
        raise TelegramInvalidPayloadError("Telegram update payload must be an object.")

    update_id = payload.get("update_id")
    if "message" in payload:
        return [_parse_message(payload["message"], update_id, update_type="message")]
    if "edited_message" in payload:
        return [
            _parse_message(
                payload["edited_message"],
                update_id,
                update_type="edited_message",
            )
        ]
    if "callback_query" in payload:
        return [_parse_callback_query(payload["callback_query"], update_id)]
    return []


def build_telegram_message_external_id(chat_id: str, message_id: str) -> str:
    return f"telegram:{chat_id}:{message_id}"


def build_telegram_callback_external_id(update_id: str, callback_query_id: str) -> str:
    return f"telegram:update:{update_id}:callback:{callback_query_id}"


def _parse_message(
    message: dict[str, Any],
    update_id: int | None,
    *,
    update_type: str,
) -> NormalizedInboundMessage:
    telegram_user = message.get("from")
    chat = message.get("chat")
    message_id = message.get("message_id")
    if not telegram_user or not chat or message_id is None:
        raise TelegramInvalidPayloadError("Telegram message is missing from/chat/id.")

    if "id" not in telegram_user or "id" not in chat:
        raise TelegramInvalidPayloadError("Telegram message is missing user/chat id.")

    external_user_id = str(telegram_user["id"])
    external_chat_id = str(chat["id"])
    message_type = _message_type(message)
    text = message.get("text")
    location = None
    if "location" in message:
        location_payload = message["location"]
        try:
            location = MessageLocation(
                latitude=float(location_payload["latitude"]),
                longitude=float(location_payload["longitude"]),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise TelegramInvalidPayloadError(
                "Telegram location payload is invalid."
            ) from exc

    metadata = {
        "telegram": {
            "update_id": update_id,
            "update_type": update_type,
            "chat_type": chat.get("type"),
            "username": telegram_user.get("username"),
            "first_name": telegram_user.get("first_name"),
            "last_name": telegram_user.get("last_name"),
            "is_command": bool(text and text.startswith("/")),
        }
    }

    return NormalizedInboundMessage(
        channel=MessagingChannel.TELEGRAM,
        external_message_id=build_telegram_message_external_id(
            external_chat_id,
            str(message_id),
        ),
        external_user_id=external_user_id,
        external_chat_id=external_chat_id,
        message_type=message_type,
        text=text,
        location=location,
        occurred_at=_parse_timestamp(message.get("date")),
        raw_payload=message,
        metadata=metadata,
    )


def _parse_callback_query(
    callback_query: dict[str, Any],
    update_id: int | None,
) -> NormalizedInboundMessage:
    telegram_user = callback_query.get("from")
    callback_id = callback_query.get("id")
    if not telegram_user or not callback_id or update_id is None:
        raise TelegramInvalidPayloadError("Telegram callback query is incomplete.")
    if "id" not in telegram_user:
        raise TelegramInvalidPayloadError("Telegram callback query is missing user id.")

    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    external_chat_id = str(chat.get("id") or callback_query.get("chat_instance"))

    return NormalizedInboundMessage(
        channel=MessagingChannel.TELEGRAM,
        external_message_id=build_telegram_callback_external_id(
            str(update_id),
            str(callback_id),
        ),
        external_user_id=str(telegram_user["id"]),
        external_chat_id=external_chat_id,
        message_type=MessageType.INTERACTIVE,
        raw_payload=callback_query,
        metadata={
            "telegram": {
                "update_id": update_id,
                "update_type": "callback_query",
                "callback_query_id": callback_id,
                "callback_data": callback_query.get("data"),
            }
        },
    )


def _message_type(message: dict[str, Any]) -> MessageType:
    if "text" in message:
        return MessageType.TEXT
    if "location" in message:
        return MessageType.LOCATION
    if "photo" in message:
        return MessageType.IMAGE
    if "document" in message:
        return MessageType.DOCUMENT
    if "audio" in message or "voice" in message:
        return MessageType.AUDIO
    if "video" in message or "video_note" in message:
        return MessageType.VIDEO
    if "sticker" in message:
        return MessageType.UNKNOWN
    return MessageType.UNKNOWN


def _parse_timestamp(timestamp: int | None) -> datetime | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise TelegramInvalidPayloadError("Telegram timestamp is invalid.") from exc
