from datetime import UTC, datetime
from typing import Any

from django.utils import timezone

from apps.messaging.domain.enums import MessageStatus, MessagingChannel
from apps.messaging.infrastructure.models import MessageEvent

STATUS_MAP = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.PROCESSED,
    "read": MessageStatus.PROCESSED,
    "failed": MessageStatus.FAILED,
}


def parse_whatsapp_statuses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for status in value.get("statuses", []):
                statuses.append(status)
    return statuses


def apply_whatsapp_status_updates(payload: dict[str, Any]) -> int:
    updated = 0
    for status in parse_whatsapp_statuses(payload):
        external_message_id = status.get("id")
        if not external_message_id:
            continue
        message_status = STATUS_MAP.get(status.get("status"))
        if message_status is None:
            continue
        event = MessageEvent.objects.filter(
            channel=MessagingChannel.WHATSAPP,
            external_message_id=external_message_id,
        ).first()
        if event is None:
            continue
        event.status = message_status
        event.processed_at = _parse_timestamp(status.get("timestamp")) or timezone.now()
        event.metadata = {
            **(event.metadata or {}),
            "whatsapp_status": status.get("status"),
        }
        event.save(update_fields=["status", "processed_at", "metadata", "updated_at"])
        updated += 1
    return updated


def _parse_timestamp(timestamp: str | None):
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=UTC)
