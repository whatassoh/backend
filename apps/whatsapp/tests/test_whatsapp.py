import hashlib
import hmac
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from django.test import Client, override_settings

from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.messaging.domain.dtos import MessageLocation
from apps.messaging.domain.enums import MessageStatus, MessageType, MessagingChannel
from apps.messaging.infrastructure.models import ChannelIdentity, MessageEvent
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook
from apps.whatsapp.application.services import process_whatsapp_webhook
from apps.whatsapp.conf import get_whatsapp_settings
from apps.whatsapp.exceptions import (
    WhatsAppAPIError,
    WhatsAppAuthenticationError,
    WhatsAppConfigurationError,
    WhatsAppInvalidPayloadError,
    WhatsAppRateLimitError,
)
from apps.whatsapp.providers.cloud_api import WhatsAppProvider
from apps.whatsapp.security import verify_meta_signature

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "verify-token"


def sign_payload(payload: dict) -> tuple[bytes, str]:
    raw_body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return raw_body, f"sha256={digest}"


def text_webhook(message_id: str = "wamid.1", text: str = "Je veux 5kg de riz"):
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
                                    "from": "237699887766",
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


def location_webhook():
    payload = text_webhook(message_id="wamid.location")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "location"
    message.pop("text")
    message["location"] = {
        "latitude": 4.05,
        "longitude": 9.7,
        "name": "Douala",
        "address": "Akwa",
    }
    return payload


@pytest.mark.parametrize(
    ("enabled", "token", "raises"),
    [
        (False, "", False),
        (True, "", True),
        (True, "token", False),
    ],
)
@override_settings(
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_whatsapp_configuration_validation(enabled, token, raises):
    with override_settings(WHATSAPP_ENABLED=enabled, WHATSAPP_ACCESS_TOKEN=token):
        if raises:
            with pytest.raises(WhatsAppConfigurationError):
                get_whatsapp_settings()
        else:
            assert get_whatsapp_settings().enabled is enabled


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_webhook_verification_get_success():
    response = Client().get(
        "/webhooks/whatsapp/",
        {
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "challenge-value",
        },
    )

    assert response.status_code == 200
    assert response.content == b"challenge-value"


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_webhook_verification_get_rejects_bad_or_missing_params():
    client = Client()

    assert client.get("/webhooks/whatsapp/").status_code == 400
    assert (
        client.get(
            "/webhooks/whatsapp/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-value",
            },
        ).status_code
        == 403
    )


def test_signature_validation():
    raw_body, signature = sign_payload(text_webhook())

    assert verify_meta_signature(
        raw_body=raw_body,
        signature_header=signature,
        app_secret=APP_SECRET,
    )
    assert not verify_meta_signature(
        raw_body=raw_body,
        signature_header="sha256=bad",
        app_secret=APP_SECRET,
    )


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_webhook_post_rejects_invalid_signature_and_payload():
    payload = text_webhook()
    raw_body, _ = sign_payload(payload)
    client = Client()

    invalid_signature = client.post(
        "/webhooks/whatsapp/",
        data=raw_body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=bad"},
    )
    assert invalid_signature.status_code == 403

    digest = hmac.new(APP_SECRET.encode(), b"not-json", hashlib.sha256).hexdigest()
    invalid_payload = client.post(
        "/webhooks/whatsapp/",
        data=b"not-json",
        content_type="application/json",
        headers={"X-Hub-Signature-256": f"sha256={digest}"},
    )
    assert invalid_payload.status_code == 400


@override_settings(
    WHATSAPP_ENABLED=False,
    WHATSAPP_ACCESS_TOKEN="",
    WHATSAPP_PHONE_NUMBER_ID="",
    WHATSAPP_VERIFY_TOKEN="",
    WHATSAPP_APP_SECRET="",
    META_GRAPH_API_VERSION="v26.0",
)
def test_webhook_returns_503_when_whatsapp_is_disabled():
    response = Client().post(
        "/webhooks/whatsapp/",
        data=b"{}",
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=bad"},
    )

    assert response.status_code == 503


def test_parse_text_location_unsupported_and_multiple_events():
    payload = text_webhook()
    second = location_webhook()["entry"][0]["changes"][0]["value"]["messages"][0]
    unsupported = {
        "from": "237699887766",
        "id": "wamid.unsupported",
        "timestamp": "1700000002",
        "type": "sticker",
        "sticker": {"id": "media-id"},
    }
    payload["entry"][0]["changes"][0]["value"]["messages"].extend([second, unsupported])

    messages = parse_whatsapp_webhook(payload)

    assert [message.message_type for message in messages] == [
        MessageType.TEXT,
        MessageType.LOCATION,
        MessageType.UNKNOWN,
    ]
    assert messages[0].text == "Je veux 5kg de riz"
    assert messages[1].location.latitude == 4.05
    assert messages[1].location.address == "Akwa"


@pytest.mark.parametrize(
    "payload",
    [
        {"entry": [{"changes": [{"value": {"messages": [{"type": "text"}]}}]}]},
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "237699887766",
                                        "id": "wamid.bad.location",
                                        "timestamp": "1700000000",
                                        "type": "location",
                                        "location": {"latitude": 200, "longitude": 9.7},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        },
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "237699887766",
                                        "id": "wamid.bad.timestamp",
                                        "timestamp": "not-a-timestamp",
                                        "type": "text",
                                        "text": {"body": "Bonjour"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        },
    ],
)
def test_parse_webhook_rejects_invalid_message_payloads(payload):
    with pytest.raises(WhatsAppInvalidPayloadError):
        parse_whatsapp_webhook(payload)


@pytest.mark.django_db
def test_status_updates_do_not_create_conversation_messages():
    MessageEvent.objects.create(
        channel=MessagingChannel.WHATSAPP,
        direction="OUTBOUND",
        external_message_id="wamid.out",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        status=MessageStatus.SENT,
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.out",
                                    "status": "failed",
                                    "timestamp": "1700000003",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    result = process_whatsapp_webhook(payload)

    assert result["statuses_updated"] == 1
    assert ConversationMessage.objects.count() == 0
    assert MessageEvent.objects.get(external_message_id="wamid.out").status == "FAILED"


@pytest.mark.django_db
def test_webhook_integration_creates_message_event_identity_conversation_and_message():
    result = process_whatsapp_webhook(text_webhook())

    assert result == {"processed": 1, "duplicates": 0, "statuses_updated": 0}
    assert MessageEvent.objects.count() == 1
    assert ChannelIdentity.objects.count() == 1
    assert Conversation.objects.count() == 1
    assert ConversationMessage.objects.get().text == "Je veux 5kg de riz"


@pytest.mark.django_db
def test_duplicate_webhook_is_idempotent():
    payload = text_webhook()

    first = process_whatsapp_webhook(payload)
    second = process_whatsapp_webhook(payload)

    assert first["processed"] == 1
    assert second["duplicates"] == 1
    assert MessageEvent.objects.count() == 1
    assert Conversation.objects.count() == 1
    assert ConversationMessage.objects.count() == 1


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="secret-token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_whatsapp_provider_send_typing_indicator_calls_meta(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"messages": [{"id": "wamid.typing"}]})

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)

    result = WhatsAppProvider().send_typing_indicator("237699887766")

    assert result["messages"][0]["id"] == "wamid.typing"
    assert captured["url"] == (
        "https://graph.facebook.com/v26.0/phone-number-id/messages"
    )
    assert captured["payload"]["type"] == "typing"
    assert captured["payload"]["typing"]["type"] == "text"
    assert captured["timeout"] == 10


@pytest.mark.django_db
@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="secret-token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_whatsapp_processing_indicator_failure_does_not_block_response(monkeypatch):
    def raise_on_indicator(*args, **kwargs):
        raise RuntimeError("whatsapp indicator failed")

    monkeypatch.setattr(
        "apps.whatsapp.providers.cloud_api.WhatsAppProvider.send_typing_indicator",
        raise_on_indicator,
    )

    result = process_whatsapp_webhook(text_webhook())

    assert result == {"processed": 1, "duplicates": 0, "statuses_updated": 0}
    assert ConversationMessage.objects.count() == 1


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
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="secret-token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
    WHATSAPP_REQUEST_TIMEOUT=7,
)
def test_provider_send_text_calls_meta_and_records_message_event(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"messages": [{"id": "wamid.out"}]})

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)

    result = WhatsAppProvider().send_text("237699887766", "Quelle quantite ?")

    assert result.external_message_id == "wamid.out"
    assert result.message_event is not None
    assert captured["url"] == (
        "https://graph.facebook.com/v26.0/phone-number-id/messages"
    )
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["payload"]["type"] == "text"
    assert captured["payload"]["text"]["body"] == "Quelle quantite ?"
    assert captured["timeout"] == 7
    assert MessageEvent.objects.get().external_message_id == "wamid.out"


@pytest.mark.django_db
@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="secret-token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_provider_send_location_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"messages": [{"id": "wamid.location"}]})

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)

    WhatsAppProvider().send_location(
        "237699887766",
        MessageLocation(latitude=4.05, longitude=9.7, name="Douala"),
    )

    assert captured["payload"]["type"] == "location"
    assert captured["payload"]["location"]["latitude"] == 4.05


@override_settings(
    WHATSAPP_ENABLED=False,
    WHATSAPP_ACCESS_TOKEN="",
    WHATSAPP_PHONE_NUMBER_ID="",
    WHATSAPP_VERIFY_TOKEN="",
    WHATSAPP_APP_SECRET="",
    META_GRAPH_API_VERSION="v26.0",
)
def test_provider_is_unavailable_when_disabled():
    with pytest.raises(WhatsAppConfigurationError):
        WhatsAppProvider()


@pytest.mark.parametrize(
    ("status_code", "exception_class", "retryable"),
    [
        (401, WhatsAppAuthenticationError, False),
        (403, WhatsAppAuthenticationError, False),
        (429, WhatsAppRateLimitError, True),
        (500, WhatsAppAPIError, True),
        (400, WhatsAppAPIError, False),
    ],
)
@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="secret-token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
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
            BytesIO(b'{"error":{"message":"bad"}}'),
        )

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)

    with pytest.raises(exception_class) as exc_info:
        WhatsAppProvider().send_text("237699887766", "Bonjour")

    assert exc_info.value.retryable is retryable


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="secret-token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN,
    WHATSAPP_APP_SECRET=APP_SECRET,
    META_GRAPH_API_VERSION="v26.0",
)
def test_provider_timeout_is_retryable(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError()

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)

    with pytest.raises(WhatsAppAPIError) as exc_info:
        WhatsAppProvider().send_text("237699887766", "Bonjour")

    assert exc_info.value.retryable is True
