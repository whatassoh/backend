from dataclasses import asdict
from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.conversations.application.selectors import get_active_conversation
from apps.conversations.domain.dtos import InboundMessageContext
from apps.conversations.domain.enums import ConversationStatus
from apps.conversations.domain.exceptions import (
    ConversationAlreadyClosedError,
    ConversationChannelMismatchError,
)
from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.identity.application.services import (
    create_user_for_telegram,
    get_or_create_user_by_whatsapp,
)
from apps.messaging.application.selectors import get_channel_identity
from apps.messaging.application.services import get_or_create_channel_identity
from apps.messaging.domain.dtos import (
    MessageLocation,
    NormalizedInboundMessage,
    NormalizedOutboundMessage,
)
from apps.messaging.domain.enums import (
    MessageDirection,
    MessagingChannel,
)
from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent


def _location_to_payload(location: MessageLocation | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return asdict(location)


def _ensure_channel_match(
    channel_identity: ChannelIdentity,
    channel: str,
) -> None:
    if channel_identity.channel != channel:
        raise ConversationChannelMismatchError(
            "Conversation channel must match channel identity channel.",
            details={
                "identity_channel": channel_identity.channel,
                "message_channel": channel,
            },
        )


@transaction.atomic
def resolve_channel_identity_for_inbound(
    message: NormalizedInboundMessage,
) -> tuple[ChannelIdentity, bool]:
    identity = get_channel_identity(message.channel, message.external_user_id)
    if identity is not None:
        return identity, False

    if message.channel == MessagingChannel.TELEGRAM:
        telegram_metadata = message.metadata.get("telegram", {})
        if not isinstance(telegram_metadata, dict):
            telegram_metadata = {}
        user = create_user_for_telegram(
            first_name=telegram_metadata.get("first_name"),
            last_name=telegram_metadata.get("last_name"),
        )
        identity, created = get_or_create_channel_identity(
            user=user,
            channel=MessagingChannel.TELEGRAM,
            external_user_id=message.external_user_id,
            defaults={
                "external_username": telegram_metadata.get("username"),
                "metadata": {"telegram": telegram_metadata},
            },
        )
        if not created:
            user.delete()
        return identity, created

    if message.channel != MessagingChannel.WHATSAPP:
        raise ValueError(f"Unsupported messaging channel: {message.channel}")

    user, _ = get_or_create_user_by_whatsapp(message.external_user_id)
    identity, created = get_or_create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=message.external_user_id,
        defaults={
            "phone_number": user.whatsapp_phone,
            "metadata": {"source": "conversation.inbound"},
        },
    )
    return identity, created


@transaction.atomic
def get_or_create_active_conversation(
    *,
    channel_identity: ChannelIdentity,
    external_chat_id: str | None = None,
) -> tuple[Conversation, bool]:
    existing = (
        Conversation.objects.select_for_update()
        .select_related("user", "channel_identity")
        .filter(
            channel_identity=channel_identity,
            external_chat_id=external_chat_id,
            status=ConversationStatus.ACTIVE,
        )
        .first()
    )
    if existing is not None:
        return existing, False

    try:
        return (
            Conversation.objects.create(
                user=channel_identity.user,
                channel=channel_identity.channel,
                channel_identity=channel_identity,
                external_chat_id=external_chat_id,
            ),
            True,
        )
    except IntegrityError:
        transaction.set_rollback(False)
        conversation = get_active_conversation(
            channel_identity=channel_identity,
            external_chat_id=external_chat_id,
        )
        if conversation is None:
            raise
        return conversation, False


def get_conversation_context(conversation: Conversation) -> dict[str, Any]:
    return dict(conversation.context or {})


def update_conversation_context(
    conversation: Conversation,
    values: dict[str, Any],
) -> Conversation:
    conversation.context = {**get_conversation_context(conversation), **values}
    conversation.save(update_fields=["context", "updated_at"])
    return conversation


def set_context_value(
    conversation: Conversation,
    key: str,
    value: Any,
) -> Conversation:
    return update_conversation_context(conversation, {key: value})


def remove_context_value(conversation: Conversation, key: str) -> Conversation:
    context = get_conversation_context(conversation)
    context.pop(key, None)
    conversation.context = context
    conversation.save(update_fields=["context", "updated_at"])
    return conversation


def clear_conversation_context(conversation: Conversation) -> Conversation:
    conversation.context = {}
    conversation.save(update_fields=["context", "updated_at"])
    return conversation


@transaction.atomic
def ingest_inbound_message(
    normalized_message: NormalizedInboundMessage,
    message_event: MessageEvent,
) -> InboundMessageContext:
    channel_identity, _ = resolve_channel_identity_for_inbound(normalized_message)
    _ensure_channel_match(channel_identity, normalized_message.channel)

    conversation, created_conversation = get_or_create_active_conversation(
        channel_identity=channel_identity,
        external_chat_id=normalized_message.external_chat_id,
    )

    existing_message = (
        ConversationMessage.objects.select_related("conversation")
        .filter(message_event=message_event)
        .first()
    )
    if existing_message is not None:
        return InboundMessageContext(
            user=channel_identity.user,
            channel_identity=channel_identity,
            conversation=existing_message.conversation,
            conversation_message=existing_message,
            created_conversation=False,
            created_message=False,
        )

    occurred_at = (
        normalized_message.occurred_at or message_event.received_at or timezone.now()
    )
    conversation_message = ConversationMessage.objects.create(
        conversation=conversation,
        message_event=message_event,
        direction=MessageDirection.INBOUND,
        message_type=normalized_message.message_type,
        text=normalized_message.text,
        location=_location_to_payload(normalized_message.location),
        metadata=normalized_message.metadata,
        occurred_at=occurred_at,
    )
    conversation.last_activity_at = occurred_at
    conversation.save(update_fields=["last_activity_at", "updated_at"])

    return InboundMessageContext(
        user=channel_identity.user,
        channel_identity=channel_identity,
        conversation=conversation,
        conversation_message=conversation_message,
        created_conversation=created_conversation,
        created_message=True,
    )


@transaction.atomic
def record_outbound_message(
    *,
    conversation: Conversation,
    message: NormalizedOutboundMessage,
    message_event: MessageEvent | None = None,
    occurred_at: datetime | None = None,
) -> ConversationMessage:
    if conversation.status != ConversationStatus.ACTIVE:
        raise ConversationAlreadyClosedError(
            "Cannot add a message to a closed conversation."
        )
    if conversation.channel != message.channel:
        raise ConversationChannelMismatchError(
            "Outbound message channel must match conversation channel."
        )

    timestamp = occurred_at or timezone.now()
    conversation_message = ConversationMessage.objects.create(
        conversation=conversation,
        message_event=message_event,
        direction=MessageDirection.OUTBOUND,
        message_type=message.message_type,
        text=message.text,
        location=_location_to_payload(message.location),
        metadata=message.metadata,
        occurred_at=timestamp,
    )
    conversation.last_activity_at = timestamp
    conversation.save(update_fields=["last_activity_at", "updated_at"])
    return conversation_message


@transaction.atomic
def close_conversation(conversation: Conversation) -> Conversation:
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if locked.status == ConversationStatus.CLOSED:
        raise ConversationAlreadyClosedError("Conversation is already closed.")

    now = timezone.now()
    locked.status = ConversationStatus.CLOSED
    locked.closed_at = now
    # Temporary dialogue state must never leak into a later conversation.
    locked.context = {}
    locked.save(update_fields=["status", "closed_at", "context", "updated_at"])
    return locked
