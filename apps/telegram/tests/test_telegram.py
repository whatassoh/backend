import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from django.test import Client, override_settings

from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import MessageLocation
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.messaging.infrastructure.models import MessageEvent
from apps.telegram.adapters.inbound import (
    build_telegram_message_external_id,
    parse_telegram_update,
)
from apps.telegram.application.services import process_telegram_webhook
from apps.telegram.conf import get_telegram_settings
from apps.telegram.exceptions import (
    TelegramAPIError,
    TelegramAuthenticationError,
    TelegramConfigurationError,
    TelegramInvalidPayloadError,
    TelegramRateLimitError,
)
from apps.telegram.providers.bot_api import TelegramProvider, sanitize_telegram_url
from apps.telegram.security import verify_telegram_secret

WEBHOOK_SECRET = "telegram-secret"


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_telegram_identity(user: User, external_user_id: str = "123456"):
    return create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id=external_user_id,
        external_username="jacob_example",
        metadata={"telegram": {"first_name": "Jacob"}},
    )


def text_update(
    *,
    update_id: int = 1001,
    user_id: int = 123456,
    chat_id: int = 987654,
    message_id: int = 42,
    text: str = "Je veux 5kg de riz",
):
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1700000000,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Jacob",
                "username": "jacob_example",
            },
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def location_update():
    payload = text_update(update_id=1002, message_id=43)
    message = payload["message"]
    message.pop("text")
    message["location"] = {"latitude": 4.05, "longitude": 9.7}
    return payload


@pytest.mark.parametrize(
    ("enabled", "token", "secret", "raises"),
    [
        (False, "", "", False),
        (True, "", WEBHOOK_SECRET, True),
        (True, "token", "", False),
        (True, "token", WEBHOOK_SECRET, False),
    ],
)
def test_telegram_configuration_validation(enabled, token, secret, raises):
    with override_settings(
        TELEGRAM_ENABLED=enabled,
        TELEGRAM_BOT_TOKEN=token,
        TELEGRAM_WEBHOOK_SECRET=secret,
        TELEGRAM_WEBHOOK_URL="https://example.com/webhooks/telegram/",
        TELEGRAM_API_BASE_URL="https://api.telegram.org",
    ):
        if raises:
            with pytest.raises(TelegramConfigurationError):
                get_telegram_settings()
        else:
            assert get_telegram_settings().enabled is enabled


def test_telegram_secret_validation():
    assert verify_telegram_secret(
        received_secret=WEBHOOK_SECRET,
        expected_secret=WEBHOOK_SECRET,
    )
    assert not verify_telegram_secret(
        received_secret="wrong",
        expected_secret=WEBHOOK_SECRET,
    )


@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
)
def test_webhook_rejects_invalid_secret_and_payload():
    client = Client()

    assert (
        client.post(
            "/webhooks/telegram/",
            data=json.dumps(text_update()),
            content_type="application/json",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/webhooks/telegram/",
            data=b"not-json",
            content_type="application/json",
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        ).status_code
        == 400
    )


@override_settings(
    TELEGRAM_ENABLED=False,
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_WEBHOOK_SECRET="",
)
def test_webhook_returns_503_when_telegram_is_disabled():
    response = Client().post(
        "/webhooks/telegram/",
        data=b"{}",
        content_type="application/json",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 503


def test_parse_text_location_start_unsupported_callback_and_empty_update():
    text_message = parse_telegram_update(text_update())[0]
    start_message = parse_telegram_update(text_update(text="/start"))[0]
    location_message = parse_telegram_update(location_update())[0]
    unsupported = text_update(update_id=1003, message_id=44)
    unsupported["message"].pop("text")
    unsupported["message"]["sticker"] = {"file_id": "sticker-file-id"}
    unsupported_message = parse_telegram_update(unsupported)[0]
    callback_message = parse_telegram_update(
        {
            "update_id": 1004,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 123456, "first_name": "Jacob"},
                "message": {"message_id": 10, "chat": {"id": 987654}},
                "data": "confirm",
            },
        }
    )[0]

    assert text_message.message_type == MessageType.TEXT
    assert text_message.text == "Je veux 5kg de riz"
    assert text_message.external_user_id == "123456"
    assert text_message.external_chat_id == "987654"
    assert text_message.external_message_id == "telegram:987654:42"
    assert start_message.metadata["telegram"]["is_command"] is True
    assert location_message.message_type == MessageType.LOCATION
    assert location_message.location.latitude == 4.05
    assert unsupported_message.message_type == MessageType.UNKNOWN
    assert callback_message.message_type == MessageType.INTERACTIVE
    assert parse_telegram_update({"update_id": 9999, "poll": {}}) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 1, "message": {"message_id": 42, "chat": {"id": 1}}},
        {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "date": 1700000000,
                "from": {"id": 123, "is_bot": False},
                "chat": {"id": 1, "type": "private"},
                "location": {"latitude": 200, "longitude": 9.7},
            },
        },
        {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "date": "not-a-timestamp",
                "from": {"id": 123, "is_bot": False},
                "chat": {"id": 1, "type": "private"},
                "text": "Bonjour",
            },
        },
        {"update_id": 1, "callback_query": {"id": "cb-1", "from": {}}},
    ],
)
def test_parse_update_rejects_invalid_message_payloads(payload):
    with pytest.raises(TelegramInvalidPayloadError):
        parse_telegram_update(payload)


def test_message_id_strategy_avoids_cross_chat_collisions():
    first = parse_telegram_update(text_update(chat_id=1, message_id=42))[0]
    second = parse_telegram_update(text_update(chat_id=2, message_id=42))[0]

    assert first.external_message_id != second.external_message_id
    assert first.external_message_id == build_telegram_message_external_id("1", "42")
    assert second.external_message_id == build_telegram_message_external_id("2", "42")


@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
    TELEGRAM_API_BASE_URL="https://api.telegram.org",
)
def test_telegram_provider_send_chat_action_calls_bot_api(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": True})

    monkeypatch.setattr("apps.telegram.providers.bot_api.urlopen", fake_urlopen)

    response = TelegramProvider().send_chat_action("987654", action="typing")

    assert response["ok"] is True
    assert captured["url"] == "https://api.telegram.org/bottoken/sendChatAction"
    assert captured["payload"] == {"chat_id": "987654", "action": "typing"}
    assert captured["timeout"] == 10


@pytest.mark.django_db
@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
)
def test_telegram_processing_indicator_failure_does_not_block_response(monkeypatch):
    def raise_on_indicator(*args, **kwargs):
        raise RuntimeError("telegram indicator failed")

    monkeypatch.setattr(
        "apps.telegram.providers.bot_api.TelegramProvider.send_chat_action",
        raise_on_indicator,
    )

    result = process_telegram_webhook(text_update())

    assert result == {"processed": 1, "duplicates": 0, "ignored": 0}
    assert ConversationMessage.objects.count() == 1


@pytest.mark.django_db
def test_webhook_integration_with_existing_telegram_identity():
    user = create_user()
    create_telegram_identity(user)

    result = process_telegram_webhook(text_update())

    assert result == {"processed": 1, "duplicates": 0, "ignored": 0}
    assert MessageEvent.objects.count() == 1
    assert Conversation.objects.count() == 1
    assert ConversationMessage.objects.get().text == "Je veux 5kg de riz"
    event = MessageEvent.objects.get()
    assert event.external_user_id == "123456"
    assert event.external_chat_id == "987654"


@pytest.mark.django_db
def test_duplicate_telegram_webhook_is_idempotent():
    user = create_user()
    create_telegram_identity(user)
    payload = text_update()

    first = process_telegram_webhook(payload)
    second = process_telegram_webhook(payload)

    assert first["processed"] == 1
    assert second["duplicates"] == 1
    assert MessageEvent.objects.count() == 1
    assert Conversation.objects.count() == 1
    assert ConversationMessage.objects.count() == 1


@pytest.mark.django_db
def test_webhook_view_accepts_valid_secret(monkeypatch):
    user = create_user()
    create_telegram_identity(user)

    with override_settings(
        TELEGRAM_ENABLED=True,
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
    ):
        response = Client().post(
            "/webhooks/telegram/",
            data=json.dumps(text_update()),
            content_type="application/json",
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        )

    assert response.status_code == 200
    assert response.json()["processed"] == 1


class FakeResponse:
    def __init__(self, data: dict):
        self.data = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.data


@pytest.mark.django_db
@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="secret-token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
    TELEGRAM_API_BASE_URL="https://api.telegram.org",
    TELEGRAM_REQUEST_TIMEOUT=7,
)
def test_provider_send_text_calls_bot_api_and_records_message_event(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "ok": True,
                "result": {
                    "message_id": 77,
                    "chat": {"id": 987654, "type": "private"},
                    "text": "Quelle quantite ?",
                },
            }
        )

    monkeypatch.setattr("apps.telegram.providers.bot_api.urlopen", fake_urlopen)

    result = TelegramProvider().send_text("987654", "Quelle quantite ?")

    assert result.external_message_id == "telegram:987654:77"
    assert result.message_event is not None
    assert captured["url"] == "https://api.telegram.org/botsecret-token/sendMessage"
    assert captured["payload"] == {
        "chat_id": "987654",
        "text": "Quelle quantite ?",
    }
    assert captured["timeout"] == 7
    event = MessageEvent.objects.get()
    assert event.external_message_id == "telegram:987654:77"
    assert event.external_chat_id == "987654"


@pytest.mark.django_db
@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="secret-token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
    TELEGRAM_API_BASE_URL="https://api.telegram.org",
)
def test_provider_send_location_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "ok": True,
                "result": {
                    "message_id": 78,
                    "chat": {"id": 987654, "type": "private"},
                },
            }
        )

    monkeypatch.setattr("apps.telegram.providers.bot_api.urlopen", fake_urlopen)

    TelegramProvider().send_location(
        "987654",
        MessageLocation(latitude=4.05, longitude=9.7),
    )

    assert captured["url"].endswith("/sendLocation")
    assert captured["payload"] == {
        "chat_id": "987654",
        "latitude": 4.05,
        "longitude": 9.7,
    }


@override_settings(
    TELEGRAM_ENABLED=False,
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_WEBHOOK_SECRET="",
)
def test_provider_is_unavailable_when_disabled():
    with pytest.raises(TelegramConfigurationError):
        TelegramProvider()


@pytest.mark.parametrize(
    ("status_code", "exception_class", "retryable"),
    [
        (401, TelegramAuthenticationError, False),
        (403, TelegramAuthenticationError, False),
        (429, TelegramRateLimitError, True),
        (500, TelegramAPIError, True),
        (400, TelegramAPIError, False),
    ],
)
@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="secret-token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
    TELEGRAM_API_BASE_URL="https://api.telegram.org",
)
def test_provider_http_errors_are_categorized(
    monkeypatch,
    status_code,
    exception_class,
    retryable,
):
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            status_code,
            "error",
            {},
            BytesIO(b'{"ok":false,"parameters":{"retry_after":12}}'),
        )

    monkeypatch.setattr("apps.telegram.providers.bot_api.urlopen", fake_urlopen)

    with pytest.raises(exception_class) as exc_info:
        TelegramProvider().send_text("987654", "Bonjour")

    assert exc_info.value.retryable is retryable
    if status_code == 429:
        assert exc_info.value.retry_after == 12


@override_settings(
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="secret-token",
    TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET,
    TELEGRAM_API_BASE_URL="https://api.telegram.org",
)
def test_provider_ok_false_and_timeout(monkeypatch):
    def fake_ok_false(request, timeout):
        return FakeResponse(
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 9},
            }
        )

    monkeypatch.setattr("apps.telegram.providers.bot_api.urlopen", fake_ok_false)
    with pytest.raises(TelegramRateLimitError) as rate_limit:
        TelegramProvider().send_text("987654", "Bonjour")
    assert rate_limit.value.retry_after == 9

    def fake_timeout(request, timeout):
        raise TimeoutError()

    monkeypatch.setattr("apps.telegram.providers.bot_api.urlopen", fake_timeout)
    with pytest.raises(TelegramAPIError) as timeout_error:
        TelegramProvider().send_text("987654", "Bonjour")
    assert timeout_error.value.retryable is True


def test_sanitize_telegram_url_hides_token():
    url = "https://api.telegram.org/botsecret-token/sendMessage"

    assert sanitize_telegram_url(url, "secret-token") == (
        "https://api.telegram.org/bot<telegram-bot-token>/sendMessage"
    )
