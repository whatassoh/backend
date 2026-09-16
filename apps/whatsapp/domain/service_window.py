from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.conversations.infrastructure.models import Conversation
from apps.messaging.domain.enums import MessageDirection

SERVICE_WINDOW_DURATION = timedelta(hours=24)


@dataclass(frozen=True, kw_only=True)
class ServiceWindowState:
    is_open: bool
    last_inbound_at: object | None
    expires_at: object | None


class WhatsAppServiceWindowPolicy:
    duration = SERVICE_WINDOW_DURATION

    def expires_at(self, conversation: Conversation):
        last_inbound_at = self.last_inbound_at(conversation)
        if last_inbound_at is None:
            return None
        return last_inbound_at + self.duration

    def is_open(self, conversation: Conversation, *, now=None) -> bool:
        expires_at = self.expires_at(conversation)
        if expires_at is None:
            return False
        return (now or timezone.now()) < expires_at

    def state(self, conversation: Conversation, *, now=None) -> ServiceWindowState:
        last_inbound_at = self.last_inbound_at(conversation)
        expires_at = (
            None if last_inbound_at is None else last_inbound_at + self.duration
        )
        return ServiceWindowState(
            is_open=bool(expires_at and (now or timezone.now()) < expires_at),
            last_inbound_at=last_inbound_at,
            expires_at=expires_at,
        )

    def last_inbound_at(self, conversation: Conversation):
        message = (
            conversation.messages.filter(direction=MessageDirection.INBOUND)
            .order_by("-occurred_at", "-created_at", "-id")
            .first()
        )
        if message is None:
            return None
        return message.occurred_at
