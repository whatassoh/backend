from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

from django.core.exceptions import ValidationError

from apps.messaging.domain.enums import MessageType, MessagingChannel


@dataclass(frozen=True, kw_only=True)
class MessageLocation:
    latitude: float
    longitude: float
    name: str | None = None
    address: str | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.latitude) or not isfinite(self.longitude):
            raise ValidationError("Latitude and longitude must be finite numbers.")
        if not -90 <= self.latitude <= 90:
            raise ValidationError("Latitude must be between -90 and 90.")
        if not -180 <= self.longitude <= 180:
            raise ValidationError("Longitude must be between -180 and 180.")


@dataclass(frozen=True, kw_only=True)
class NormalizedInboundMessage:
    channel: MessagingChannel | str
    external_message_id: str
    external_user_id: str
    message_type: MessageType | str
    text: str | None = None
    location: MessageLocation | None = None
    external_chat_id: str | None = None
    occurred_at: datetime | None = None
    raw_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class NormalizedOutboundMessage:
    channel: MessagingChannel | str
    recipient_external_id: str
    message_type: MessageType | str
    text: str | None = None
    location: MessageLocation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ProviderSendResult:
    channel: MessagingChannel | str
    external_message_id: str | None = None
    status: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    message_event: Any | None = None
