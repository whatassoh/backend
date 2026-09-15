from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.messaging.application.selectors import get_channel_identity
from apps.messaging.domain.dtos import (
    NormalizedInboundMessage,
    NormalizedOutboundMessage,
)
from apps.messaging.domain.enums import MessageDirection, MessageStatus
from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent


def resolve_user_from_channel_identity(
    channel: str,
    external_user_id: str,
) -> object | None:
    identity = get_channel_identity(channel, external_user_id)
    if identity is None:
        return None
    return identity.user


def create_channel_identity(
    *,
    user: object,
    channel: str,
    external_user_id: str,
    external_username: str | None = None,
    phone_number: str | None = None,
    metadata: dict[str, Any] | None = None,
    is_active: bool = True,
) -> ChannelIdentity:
    return ChannelIdentity.objects.create(
        user=user,
        channel=channel,
        external_user_id=external_user_id,
        external_username=external_username,
        phone_number=phone_number,
        metadata=metadata or {},
        is_active=is_active,
    )


@transaction.atomic
def get_or_create_channel_identity(
    *,
    user: object,
    channel: str,
    external_user_id: str,
    defaults: dict[str, Any] | None = None,
) -> tuple[ChannelIdentity, bool]:
    try:
        return ChannelIdentity.objects.get_or_create(
            channel=channel,
            external_user_id=external_user_id,
            defaults={"user": user, **(defaults or {})},
        )
    except IntegrityError:
        transaction.set_rollback(False)
        return (
            ChannelIdentity.objects.select_related("user").get(
                channel=channel,
                external_user_id=external_user_id,
            ),
            False,
        )


def record_inbound_message_event(
    message: NormalizedInboundMessage,
    *,
    status: MessageStatus | str = MessageStatus.RECEIVED,
    processed_at: datetime | None = None,
) -> MessageEvent:
    received_at = message.occurred_at or timezone.now()
    return MessageEvent.objects.create(
        channel=message.channel,
        direction=MessageDirection.INBOUND,
        external_message_id=message.external_message_id,
        external_user_id=message.external_user_id,
        external_chat_id=message.external_chat_id,
        message_type=message.message_type,
        status=status,
        raw_payload=message.raw_payload,
        metadata=message.metadata,
        received_at=received_at,
        processed_at=processed_at,
    )


def record_outbound_message_event(
    message: NormalizedOutboundMessage,
    *,
    external_message_id: str | None = None,
    external_chat_id: str | None = None,
    status: MessageStatus | str = MessageStatus.SENT,
    raw_payload: dict[str, Any] | None = None,
) -> MessageEvent:
    return MessageEvent.objects.create(
        channel=message.channel,
        direction=MessageDirection.OUTBOUND,
        external_message_id=external_message_id,
        external_user_id=message.recipient_external_id,
        external_chat_id=external_chat_id,
        message_type=message.message_type,
        status=status,
        raw_payload=raw_payload,
        metadata=message.metadata,
        sent_at=timezone.now(),
    )
