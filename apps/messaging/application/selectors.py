import uuid

from django.db.models import QuerySet

from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent


def get_channel_identity(
    channel: str,
    external_user_id: str,
) -> ChannelIdentity | None:
    return (
        ChannelIdentity.objects.select_related("user")
        .filter(channel=channel, external_user_id=external_user_id)
        .first()
    )


def get_user_channel_identities(user: object) -> QuerySet[ChannelIdentity]:
    return ChannelIdentity.objects.filter(user=user).select_related("user")


def get_message_event(message_event_id: uuid.UUID | str) -> MessageEvent | None:
    return MessageEvent.objects.filter(id=message_event_id).first()


def get_message_event_by_external_id(
    channel: str,
    external_message_id: str,
) -> MessageEvent | None:
    return MessageEvent.objects.filter(
        channel=channel,
        external_message_id=external_message_id,
    ).first()


def get_recent_message_events(limit: int = 50) -> QuerySet[MessageEvent]:
    return MessageEvent.objects.all()[:limit]
