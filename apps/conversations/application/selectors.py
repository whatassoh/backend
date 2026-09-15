import uuid

from django.db.models import QuerySet

from apps.conversations.domain.enums import ConversationStatus
from apps.conversations.infrastructure.models import Conversation, ConversationMessage


def get_conversation(conversation_id: uuid.UUID | str) -> Conversation | None:
    return (
        Conversation.objects.select_related("user", "channel_identity")
        .filter(id=conversation_id)
        .first()
    )


def get_active_conversation(
    *,
    channel_identity: object,
    external_chat_id: str | None = None,
) -> Conversation | None:
    return (
        Conversation.objects.select_related("user", "channel_identity")
        .filter(
            channel_identity=channel_identity,
            external_chat_id=external_chat_id,
            status=ConversationStatus.ACTIVE,
        )
        .first()
    )


def get_user_conversations(user: object) -> QuerySet[Conversation]:
    return (
        Conversation.objects.filter(user=user)
        .select_related("user", "channel_identity")
        .prefetch_related("messages")
    )


def get_conversation_messages(
    conversation: Conversation,
) -> QuerySet[ConversationMessage]:
    return ConversationMessage.objects.filter(conversation=conversation).select_related(
        "conversation",
        "message_event",
    )


def get_conversation_with_messages(
    conversation_id: uuid.UUID | str,
) -> Conversation | None:
    return (
        Conversation.objects.select_related("user", "channel_identity")
        .prefetch_related("messages", "messages__message_event")
        .filter(id=conversation_id)
        .first()
    )
