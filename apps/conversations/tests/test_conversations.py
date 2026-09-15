import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.conversations.application.selectors import (
    get_active_conversation,
    get_conversation_messages,
    get_conversation_with_messages,
    get_user_conversations,
)
from apps.conversations.application.services import (
    clear_conversation_context,
    close_conversation,
    get_conversation_context,
    get_or_create_active_conversation,
    ingest_inbound_message,
    record_outbound_message,
    remove_context_value,
    set_context_value,
    update_conversation_context,
)
from apps.conversations.domain.enums import ConversationStatus
from apps.conversations.domain.exceptions import UnknownChannelIdentityError
from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import (
    create_channel_identity,
    record_inbound_message_event,
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
from apps.messaging.infrastructure.models import MessageEvent


def create_user(phone: str) -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_identity(
    user: User,
    channel: MessagingChannel,
    external_user_id: str,
):
    return create_channel_identity(
        user=user,
        channel=channel,
        external_user_id=external_user_id,
    )


def create_inbound_event(message: NormalizedInboundMessage) -> MessageEvent:
    return record_inbound_message_event(message, status=MessageStatus.RECEIVED)


@pytest.mark.django_db
def test_first_message_creates_active_conversation():
    user = create_user("+237699887766")
    create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.1",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="Je veux du riz",
    )

    result = ingest_inbound_message(message, create_inbound_event(message))

    assert result.user == user
    assert result.created_conversation is True
    assert result.created_message is True
    assert result.conversation.status == ConversationStatus.ACTIVE
    assert result.conversation_message.text == "Je veux du riz"


@pytest.mark.django_db
def test_second_message_same_identity_and_chat_reuses_conversation():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    first, _ = get_or_create_active_conversation(channel_identity=identity)
    message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.2",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="5 kg",
    )

    result = ingest_inbound_message(message, create_inbound_event(message))

    assert result.conversation == first
    assert result.created_conversation is False


@pytest.mark.django_db
def test_closed_conversation_is_not_reused():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    conversation, _ = get_or_create_active_conversation(channel_identity=identity)
    close_conversation(conversation)

    new_conversation, created = get_or_create_active_conversation(
        channel_identity=identity
    )

    assert created is True
    assert new_conversation.id != conversation.id
    assert new_conversation.status == ConversationStatus.ACTIVE


@pytest.mark.django_db
def test_history_keeps_inbound_outbound_inbound_order():
    user = create_user("+237699887766")
    create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    first = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.1",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="Je veux du riz",
    )
    first_context = ingest_inbound_message(first, create_inbound_event(first))
    outbound = NormalizedOutboundMessage(
        channel=MessagingChannel.WHATSAPP,
        recipient_external_id="237699887766",
        message_type=MessageType.TEXT,
        text="Quelle quantite ?",
    )
    record_outbound_message(
        conversation=first_context.conversation,
        message=outbound,
    )
    second = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.2",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="5 kg",
    )
    ingest_inbound_message(second, create_inbound_event(second))

    messages = list(get_conversation_messages(first_context.conversation))

    assert [message.direction for message in messages] == [
        MessageDirection.INBOUND,
        MessageDirection.OUTBOUND,
        MessageDirection.INBOUND,
    ]
    assert [message.text for message in messages] == [
        "Je veux du riz",
        "Quelle quantite ?",
        "5 kg",
    ]


@pytest.mark.django_db
def test_same_user_on_two_channels_has_independent_conversations():
    user = create_user("+237699887766")
    whatsapp = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    telegram = create_identity(user, MessagingChannel.TELEGRAM, "123456")

    whatsapp_conversation, _ = get_or_create_active_conversation(
        channel_identity=whatsapp
    )
    telegram_conversation, _ = get_or_create_active_conversation(
        channel_identity=telegram,
        external_chat_id="chat-1",
    )

    assert whatsapp_conversation.user == telegram_conversation.user
    assert whatsapp_conversation.id != telegram_conversation.id


@pytest.mark.django_db
def test_same_identity_can_have_different_external_chat_contexts():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.TELEGRAM, "123456")

    direct, _ = get_or_create_active_conversation(
        channel_identity=identity,
        external_chat_id="123456",
    )
    group, _ = get_or_create_active_conversation(
        channel_identity=identity,
        external_chat_id="group-789",
    )

    assert direct.id != group.id
    assert (
        get_active_conversation(
            channel_identity=identity,
            external_chat_id="group-789",
        )
        == group
    )


@pytest.mark.django_db
def test_same_message_event_is_idempotent_for_conversation_message():
    user = create_user("+237699887766")
    create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.1",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="Je veux du riz",
    )
    event = create_inbound_event(message)
    first = ingest_inbound_message(message, event)
    last_activity_at = first.conversation.last_activity_at

    duplicate = ingest_inbound_message(message, event)

    assert duplicate.conversation_message == first.conversation_message
    assert duplicate.created_message is False
    duplicate.conversation.refresh_from_db()
    assert duplicate.conversation.last_activity_at == last_activity_at
    assert ConversationMessage.objects.count() == 1


@pytest.mark.django_db
def test_conversation_message_db_rejects_same_message_event_twice():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    conversation, _ = get_or_create_active_conversation(channel_identity=identity)
    event = MessageEvent.objects.create(
        channel=MessagingChannel.WHATSAPP,
        direction=MessageDirection.INBOUND,
        external_message_id="wamid.1",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        status=MessageStatus.RECEIVED,
    )
    ConversationMessage.objects.create(
        conversation=conversation,
        message_event=event,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        text="Je veux du riz",
    )

    with pytest.raises(IntegrityError):
        ConversationMessage.objects.create(
            conversation=conversation,
            message_event=event,
            direction=MessageDirection.INBOUND,
            message_type=MessageType.TEXT,
            text="duplicate",
        )


@pytest.mark.django_db
def test_last_activity_updates_only_on_effective_message():
    user = create_user("+237699887766")
    create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    occurred_at = timezone.now()
    message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.1",
        external_user_id="237699887766",
        message_type=MessageType.LOCATION,
        location=MessageLocation(latitude=4.05, longitude=9.7),
        occurred_at=occurred_at,
    )
    event = create_inbound_event(message)

    result = ingest_inbound_message(message, event)
    duplicate = ingest_inbound_message(message, event)

    assert result.conversation.last_activity_at == occurred_at
    duplicate.conversation.refresh_from_db()
    assert duplicate.conversation.last_activity_at == occurred_at


@pytest.mark.django_db
def test_context_helpers_set_update_remove_and_clear_values():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    conversation, _ = get_or_create_active_conversation(channel_identity=identity)

    set_context_value(conversation, "current_intent", "CREATE_ORDER")
    update_conversation_context(conversation, {"awaiting": "QUANTITY"})
    remove_context_value(conversation, "current_intent")
    conversation.refresh_from_db()

    assert get_conversation_context(conversation) == {"awaiting": "QUANTITY"}

    clear_conversation_context(conversation)
    conversation.refresh_from_db()
    assert get_conversation_context(conversation) == {}


@pytest.mark.django_db
def test_unknown_whatsapp_identity_creates_user_and_identity():
    message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="wamid.1",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        text="Bonjour",
    )

    result = ingest_inbound_message(message, create_inbound_event(message))

    assert result.user.whatsapp_phone == "+237699887766"
    assert result.channel_identity.external_user_id == "237699887766"


@pytest.mark.django_db
def test_unknown_telegram_identity_is_explicitly_rejected():
    message = NormalizedInboundMessage(
        channel=MessagingChannel.TELEGRAM,
        external_message_id="tg.1",
        external_user_id="123456",
        external_chat_id="123456",
        message_type=MessageType.TEXT,
        text="Bonjour",
    )

    with pytest.raises(UnknownChannelIdentityError):
        ingest_inbound_message(message, create_inbound_event(message))


@pytest.mark.django_db
def test_active_conversation_uniqueness_protects_concurrent_creation():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        status=ConversationStatus.ACTIVE,
    )

    with pytest.raises(IntegrityError):
        Conversation.objects.create(
            user=user,
            channel=MessagingChannel.WHATSAPP,
            channel_identity=identity,
            status=ConversationStatus.ACTIVE,
        )


@pytest.mark.django_db
def test_user_conversation_selectors_prefetch_messages():
    user = create_user("+237699887766")
    identity = create_identity(user, MessagingChannel.WHATSAPP, "237699887766")
    conversation, _ = get_or_create_active_conversation(channel_identity=identity)

    assert list(get_user_conversations(user)) == [conversation]
    assert get_conversation_with_messages(conversation.id) == conversation
