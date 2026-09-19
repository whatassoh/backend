import pytest
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.identity.infrastructure.models import User
from apps.messaging.application.selectors import (
    get_channel_identity,
    get_message_event_by_external_id,
    get_recent_message_events,
    get_user_channel_identities,
)
from apps.messaging.application.services import (
    create_channel_identity,
    record_inbound_message_event,
    record_outbound_message_event,
    resolve_user_from_channel_identity,
)
from apps.messaging.domain.dtos import (
    MessageLocation,
    NormalizedInboundMessage,
    NormalizedOutboundMessage,
)
from apps.messaging.domain.enums import (
    MessageDirection,
    MessageStatus,
    MessageType,
    MessagingChannel,
)
from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent


def create_user(phone: str) -> User:
    return User.objects.create_user(whatsapp_phone=phone)


@pytest.mark.django_db
def test_user_can_have_whatsapp_and_telegram_identities():
    user = create_user("+237699887766")

    whatsapp = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
        phone_number="+237699887766",
    )
    telegram = create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="123456",
        external_username="jacob_example",
    )

    assert whatsapp.user == user
    assert telegram.user == user
    assert set(get_user_channel_identities(user)) == {whatsapp, telegram}


@pytest.mark.django_db
def test_external_identity_is_unique_per_channel():
    user_a = create_user("+237699887766")
    user_b = create_user("+237699887767")
    create_channel_identity(
        user=user_a,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="123456",
    )

    with pytest.raises(IntegrityError):
        create_channel_identity(
            user=user_b,
            channel=MessagingChannel.TELEGRAM,
            external_user_id="123456",
        )


@pytest.mark.django_db
def test_same_external_id_is_allowed_on_different_channels():
    user_a = create_user("+237699887766")
    user_b = create_user("+237699887767")

    create_channel_identity(
        user=user_a,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="123",
    )
    create_channel_identity(
        user=user_b,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="123",
    )

    assert ChannelIdentity.objects.count() == 2


@pytest.mark.django_db
def test_resolves_channel_identity_and_user():
    user = create_user("+237699887766")
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )

    assert get_channel_identity(MessagingChannel.WHATSAPP, "237699887766") == identity
    assert (
        resolve_user_from_channel_identity(
            MessagingChannel.WHATSAPP,
            "237699887766",
        )
        == user
    )


def test_normalized_inbound_message_supports_text_location_and_unknown():
    location = MessageLocation(latitude=4.05, longitude=9.7, name="Douala")

    text_message = NormalizedInboundMessage(
        channel=MessagingChannel.TELEGRAM,
        external_message_id="501",
        external_user_id="123456",
        external_chat_id="123456",
        message_type=MessageType.TEXT,
        text="Je veux 5 kg de riz",
    )
    location_message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.location",
        external_user_id="237699887766",
        message_type=MessageType.LOCATION,
        location=location,
    )
    unknown_message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.unknown",
        external_user_id="237699887766",
        message_type=MessageType.UNKNOWN,
        raw_payload={"unsupported": True},
    )

    assert text_message.text == "Je veux 5 kg de riz"
    assert location_message.location == location
    assert unknown_message.raw_payload == {"unsupported": True}


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-90, -180),
        (90, 180),
        (4.05, 9.7),
    ],
)
def test_message_location_accepts_valid_coordinates(latitude, longitude):
    location = MessageLocation(latitude=latitude, longitude=longitude)

    assert location.latitude == latitude
    assert location.longitude == longitude


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-90.1, 0),
        (90.1, 0),
        (0, -180.1),
        (0, 180.1),
    ],
)
def test_message_location_rejects_invalid_coordinates(latitude, longitude):
    with pytest.raises(ValidationError):
        MessageLocation(latitude=latitude, longitude=longitude)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(float("nan"), 9.7), (4.05, float("inf"))],
)
def test_message_location_rejects_non_finite_coordinates(latitude, longitude):
    with pytest.raises(ValidationError):
        MessageLocation(latitude=latitude, longitude=longitude)


@pytest.mark.django_db
def test_records_inbound_and_outbound_message_events():
    inbound = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.123",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="Bonjour",
        raw_payload={"messages": [{"id": "wamid.123"}]},
        metadata={"adapter": "test"},
    )
    inbound_event = record_inbound_message_event(
        inbound,
        status=MessageStatus.PROCESSED,
        processed_at=timezone.now(),
    )
    outbound = NormalizedOutboundMessage(
        channel=MessagingChannel.TELEGRAM,
        recipient_external_id="123456",
        message_type=MessageType.LOCATION,
        location=MessageLocation(latitude=4.05, longitude=9.7),
    )
    outbound_event = record_outbound_message_event(
        outbound,
        external_message_id="tg.1",
    )

    assert inbound_event.direction == MessageDirection.INBOUND
    assert inbound_event.status == MessageStatus.PROCESSED
    assert inbound_event.raw_payload == {"messages": [{"id": "wamid.123"}]}
    assert inbound_event.received_at is not None
    assert inbound_event.processed_at is not None
    assert outbound_event.direction == MessageDirection.OUTBOUND
    assert outbound_event.status == MessageStatus.SENT
    assert outbound_event.sent_at is not None


@pytest.mark.django_db
def test_message_event_idempotence_is_scoped_by_channel():
    MessageEvent.objects.create(
        channel=MessagingChannel.WHATSAPP,
        direction=MessageDirection.INBOUND,
        external_message_id="wamid.123",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        status=MessageStatus.RECEIVED,
    )

    # PostgreSQL marks the surrounding test transaction as broken after a
    # constraint violation. Isolate the expected failure in a savepoint.
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MessageEvent.objects.create(
                channel=MessagingChannel.WHATSAPP,
                direction=MessageDirection.INBOUND,
                external_message_id="wamid.123",
                external_user_id="237699887766",
                message_type=MessageType.TEXT,
                status=MessageStatus.RECEIVED,
            )

    MessageEvent.objects.create(
        channel=MessagingChannel.TELEGRAM,
        direction=MessageDirection.INBOUND,
        external_message_id="wamid.123",
        external_user_id="123456",
        message_type=MessageType.TEXT,
        status=MessageStatus.RECEIVED,
    )

    assert MessageEvent.objects.count() == 2
    assert (
        get_message_event_by_external_id(
            MessagingChannel.WHATSAPP,
            "wamid.123",
        ).external_user_id
        == "237699887766"
    )
    assert len(get_recent_message_events()) == 2
