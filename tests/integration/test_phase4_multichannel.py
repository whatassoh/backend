import hashlib
import hmac
import json

import pytest
from django.test import Client, override_settings

from apps.conversations.application.services import (
    close_conversation,
    set_context_value,
)
from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.identity.infrastructure.models import User
from apps.messaging.application.orchestration import send_conversation_text
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import ProviderSendResult
from apps.messaging.domain.enums import MessageDirection, MessageType, MessagingChannel
from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent

APP_SECRET = "phase4-app-secret"
VERIFY_TOKEN = "phase4-verify"
TELEGRAM_SECRET = "phase4-telegram-secret"


def create_user(phone: str) -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def whatsapp_payload(
    *,
    from_id: str = "WA123",
    message_id: str = "wamid.phase4.1",
    text: str = "Je veux 5kg de riz",
):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "display_phone_number": "237600000000",
                                "phone_number_id": "phone-number-id",
                            },
                            "messages": [
                                {
                                    "from": from_id,
                                    "id": message_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def whatsapp_location_payload():
    payload = whatsapp_payload(message_id="wamid.phase4.location")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "location"
    message.pop("text")
    message["location"] = {"latitude": 4.05, "longitude": 9.7}
    return payload


def signed_whatsapp(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, f"sha256={digest}"


def telegram_update(
    *,
    user_id: int = 456,
    chat_id: int = 987,
    message_id: int = 42,
    text: str = "Je veux 5kg de riz",
):
    return {
        "update_id": 7001,
        "message": {
            "message_id": message_id,
            "date": 1700000000,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Same",
                "username": "same_username",
            },
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


@pytest.fixture
def phase4_settings():
    return override_settings(
        WHATSAPP_ENABLED=True,
        WHATSAPP_ACCESS_TOKEN="wa-token",
        WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
        WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
        WHATSAPP_APP_SECRET=APP_SECRET,
        META_GRAPH_API_VERSION="v26.0",
        TELEGRAM_ENABLED=True,
        TELEGRAM_BOT_TOKEN="tg-token",
        TELEGRAM_WEBHOOK_SECRET=TELEGRAM_SECRET,
        TELEGRAM_API_BASE_URL="https://api.telegram.org",
    )


@pytest.mark.django_db
def test_whatsapp_webhook_e2e_converges_to_phase4_core(phase4_settings):
    user = create_user("+237600000001")
    create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
    )
    body, signature = signed_whatsapp(whatsapp_payload())

    with phase4_settings:
        response = Client().post(
            "/webhooks/whatsapp/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )

    assert response.status_code == 200
    event = MessageEvent.objects.get(channel=MessagingChannel.WHATSAPP)
    conversation = Conversation.objects.get(channel=MessagingChannel.WHATSAPP)
    message = ConversationMessage.objects.get(conversation=conversation)
    assert event.external_message_id == "wamid.phase4.1"
    assert event.external_user_id == "WA123"
    assert event.direction == MessageDirection.INBOUND
    assert message.message_type == MessageType.TEXT
    assert message.text == "Je veux 5kg de riz"
    assert conversation.user == user


@pytest.mark.django_db
def test_telegram_webhook_e2e_converges_to_phase4_core(phase4_settings):
    user = create_user("+237600000002")
    create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="456",
    )

    with phase4_settings:
        response = Client().post(
            "/webhooks/telegram/",
            data=json.dumps(telegram_update()).encode("utf-8"),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TELEGRAM_SECRET,
        )

    assert response.status_code == 200
    event = MessageEvent.objects.get(channel=MessagingChannel.TELEGRAM)
    conversation = Conversation.objects.get(channel=MessagingChannel.TELEGRAM)
    message = ConversationMessage.objects.get(conversation=conversation)
    assert event.external_message_id == "telegram:987:42"
    assert event.external_user_id == "456"
    assert event.external_chat_id == "987"
    assert message.message_type == MessageType.TEXT
    assert message.text == "Je veux 5kg de riz"
    assert conversation.user == user


@pytest.mark.django_db
def test_same_user_two_channels_keep_independent_conversations(phase4_settings):
    user = create_user("+237600000003")
    create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
    )
    create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="456",
    )
    wa_body, wa_signature = signed_whatsapp(whatsapp_payload(text="Bonjour"))

    with phase4_settings:
        Client().post(
            "/webhooks/whatsapp/",
            data=wa_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=wa_signature,
        )
        Client().post(
            "/webhooks/telegram/",
            data=json.dumps(telegram_update(text="Salut")).encode("utf-8"),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TELEGRAM_SECRET,
        )

    conversations = list(Conversation.objects.order_by("channel"))
    assert len(conversations) == 2
    assert {conversation.user for conversation in conversations} == {user}
    assert conversations[0] != conversations[1]
    assert {conversation.channel for conversation in conversations} == {
        MessagingChannel.WHATSAPP,
        MessagingChannel.TELEGRAM,
    }


@pytest.mark.django_db
def test_contexts_do_not_contaminate_across_channels(phase4_settings):
    test_same_user_two_channels_keep_independent_conversations(phase4_settings)
    whatsapp_conversation = Conversation.objects.get(channel=MessagingChannel.WHATSAPP)
    telegram_conversation = Conversation.objects.get(channel=MessagingChannel.TELEGRAM)

    set_context_value(whatsapp_conversation, "awaiting", "LOCATION")
    telegram_conversation.refresh_from_db()

    assert whatsapp_conversation.context == {"awaiting": "LOCATION"}
    assert telegram_conversation.context == {}


@pytest.mark.django_db
def test_similar_external_profile_does_not_merge_users(phase4_settings):
    wa_user = create_user("+237600000004")
    tg_user = create_user("+237600000005")
    create_channel_identity(
        user=wa_user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
        external_username="same_username",
    )
    create_channel_identity(
        user=tg_user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="456",
        external_username="same_username",
    )
    wa_body, wa_signature = signed_whatsapp(whatsapp_payload())

    with phase4_settings:
        Client().post(
            "/webhooks/whatsapp/",
            data=wa_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=wa_signature,
        )
        Client().post(
            "/webhooks/telegram/",
            data=json.dumps(telegram_update()).encode("utf-8"),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=TELEGRAM_SECRET,
        )

    assert Conversation.objects.get(channel=MessagingChannel.WHATSAPP).user == wa_user
    assert Conversation.objects.get(channel=MessagingChannel.TELEGRAM).user == tg_user
    assert ChannelIdentity.objects.count() == 2


@pytest.mark.django_db
def test_duplicate_webhooks_do_not_duplicate_conversation_messages(phase4_settings):
    user = create_user("+237600000006")
    create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
    )
    body, signature = signed_whatsapp(whatsapp_payload())

    with phase4_settings:
        for _ in range(3):
            Client().post(
                "/webhooks/whatsapp/",
                data=body,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=signature,
            )

    assert MessageEvent.objects.count() == 1
    assert Conversation.objects.count() == 1
    assert ConversationMessage.objects.count() == 1


@pytest.mark.django_db
def test_same_external_message_id_is_scoped_by_channel():
    MessageEvent.objects.create(
        channel=MessagingChannel.WHATSAPP,
        direction=MessageDirection.INBOUND,
        external_message_id="123",
        external_user_id="WA123",
        message_type=MessageType.TEXT,
        status="RECEIVED",
    )
    MessageEvent.objects.create(
        channel=MessagingChannel.TELEGRAM,
        direction=MessageDirection.INBOUND,
        external_message_id="123",
        external_user_id="456",
        message_type=MessageType.TEXT,
        status="RECEIVED",
    )

    assert MessageEvent.objects.count() == 2


@pytest.mark.django_db
def test_closed_conversation_is_not_reopened(phase4_settings):
    user = create_user("+237600000007")
    create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
    )
    first_body, first_signature = signed_whatsapp(
        whatsapp_payload(message_id="wamid.closed.1")
    )
    second_body, second_signature = signed_whatsapp(
        whatsapp_payload(message_id="wamid.closed.2")
    )

    with phase4_settings:
        Client().post(
            "/webhooks/whatsapp/",
            data=first_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=first_signature,
        )
        close_conversation(Conversation.objects.get())
        Client().post(
            "/webhooks/whatsapp/",
            data=second_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=second_signature,
        )

    assert Conversation.objects.count() == 2
    assert Conversation.objects.filter(status="ACTIVE").count() == 1


@pytest.mark.django_db
def test_location_payload_reaches_conversation_without_order_creation(phase4_settings):
    user = create_user("+237600000008")
    create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
    )
    body, signature = signed_whatsapp(whatsapp_location_payload())

    with phase4_settings:
        Client().post(
            "/webhooks/whatsapp/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )

    message = ConversationMessage.objects.get()
    assert message.message_type == MessageType.LOCATION
    assert message.location["latitude"] == 4.05
    assert message.location["longitude"] == 9.7


@pytest.mark.django_db
def test_outbound_conversation_routing_writes_once(monkeypatch):
    user = create_user("+237600000009")
    whatsapp_identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="WA123",
    )
    telegram_identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="456",
    )
    wa_conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=whatsapp_identity,
        external_chat_id="WA123",
    )
    tg_conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        channel_identity=telegram_identity,
        external_chat_id="987",
    )
    sent_channels = []

    class FakeProvider:
        capabilities = type(
            "Capabilities",
            (),
            {"text": True, "location": True, "interactive": False, "media": False},
        )()

        def send(self, message):
            sent_channels.append(message.channel)
            event = MessageEvent.objects.create(
                channel=message.channel,
                direction=MessageDirection.OUTBOUND,
                external_message_id=f"{message.channel}:out",
                external_user_id=message.recipient_external_id,
                external_chat_id=message.recipient_external_id,
                message_type=message.message_type,
                status="SENT",
            )
            return ProviderSendResult(
                channel=message.channel,
                external_message_id=event.external_message_id,
                status="SENT",
                message_event=event,
            )

    monkeypatch.setattr(
        "apps.messaging.application.orchestration.get_provider",
        lambda channel: FakeProvider(),
    )

    send_conversation_text(conversation=wa_conversation, text="Aide WA")
    send_conversation_text(conversation=tg_conversation, text="Aide TG")

    assert sent_channels == [MessagingChannel.WHATSAPP, MessagingChannel.TELEGRAM]
    assert MessageEvent.objects.filter(direction=MessageDirection.OUTBOUND).count() == 2
    assert (
        ConversationMessage.objects.filter(direction=MessageDirection.OUTBOUND).count()
        == 2
    )
