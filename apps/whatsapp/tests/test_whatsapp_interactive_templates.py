import json
from datetime import timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.conversations.application.services import ingest_inbound_message
from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.identity.infrastructure.models import User
from apps.messaging.application.orchestration import send_conversation_text
from apps.messaging.application.services import (
    create_channel_identity,
    record_inbound_message_event,
)
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import (
    MessageDirection,
    MessageStatus,
    MessageType,
    MessagingChannel,
)
from apps.messaging.infrastructure.models import MessageEvent
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook
from apps.whatsapp.adapters.outbound import (
    build_interactive_reply_buttons_payload,
    build_template_payload,
)
from apps.whatsapp.application.outbound import (
    send_whatsapp_interactive_reply_buttons,
    send_whatsapp_template_to_conversation,
    send_whatsapp_text_with_template_fallback,
)
from apps.whatsapp.domain.dtos import (
    WhatsAppInteractiveMessage,
    WhatsAppReplyButton,
    WhatsAppTemplateMessage,
)
from apps.whatsapp.domain.enums import WhatsAppTemplateKey
from apps.whatsapp.domain.exceptions import (
    WhatsAppTemplateConfigurationError,
    WhatsAppTemplateRequired,
    WhatsAppTemplateValidationError,
)
from apps.whatsapp.domain.service_window import WhatsAppServiceWindowPolicy
from apps.whatsapp.exceptions import WhatsAppAPIError, WhatsAppRateLimitError
from apps.whatsapp.providers.cloud_api import WhatsAppProvider


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def template_settings(**extra):
    values = {
        "WHATSAPP_ENABLED": True,
        "WHATSAPP_ACCESS_TOKEN": "token",
        "WHATSAPP_PHONE_NUMBER_ID": "phone-number-id",
        "WHATSAPP_VERIFY_TOKEN": "verify",
        "WHATSAPP_APP_SECRET": "secret",
        "META_GRAPH_API_VERSION": "v26.0",
        "WHATSAPP_TEMPLATE_LANGUAGE_CODE": "fr",
        "WHATSAPP_TEMPLATE_ORDER_UPDATE": "whatassoh_order_update",
        "WHATSAPP_TEMPLATE_ORDER_ACTION_REQUIRED": ("whatassoh_order_action_required"),
        "WHATSAPP_TEMPLATE_OPERATIONAL_ACTION_REQUIRED": (
            "whatassoh_operational_action_required"
        ),
    }
    values.update(extra)
    return override_settings(**values)


def inbound_message(occurred_at):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=f"wamid.{occurred_at.timestamp()}",
        external_user_id="237699887766",
        external_chat_id="237699887766",
        message_type=MessageType.TEXT,
        text="Bonjour",
        occurred_at=occurred_at,
    )


@template_settings()
def test_template_payload_uses_configured_meta_name_language_and_parameters():
    payload = build_template_payload(
        WhatsAppTemplateMessage(
            recipient_external_id="237699887766",
            template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
            body_parameters={
                "customer_name": "Jacob",
                "order_reference": "CMD-42",
            },
        )
    )

    assert payload["type"] == "template"
    assert payload["template"]["name"] == "whatassoh_order_action_required"
    assert payload["template"]["language"]["code"] == "fr"
    assert payload["template"]["components"][0]["type"] == "body"
    assert payload["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "Jacob"},
        {"type": "text", "text": "CMD-42"},
    ]


@template_settings()
@pytest.mark.parametrize(
    "body_parameters",
    [
        {"customer_name": "Jacob"},
        {
            "customer_name": "Jacob",
            "order_reference": "CMD-42",
            "extra": "nope",
        },
        {"customer_name": "", "order_reference": "CMD-42"},
    ],
)
def test_template_payload_validates_parameters_before_http(body_parameters):
    with pytest.raises(WhatsAppTemplateValidationError):
        build_template_payload(
            WhatsAppTemplateMessage(
                recipient_external_id="237699887766",
                template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                body_parameters=body_parameters,
            )
        )


@template_settings(WHATSAPP_TEMPLATE_ORDER_ACTION_REQUIRED="")
def test_template_payload_rejects_missing_configuration():
    with pytest.raises(WhatsAppTemplateConfigurationError):
        build_template_payload(
            WhatsAppTemplateMessage(
                recipient_external_id="237699887766",
                template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                body_parameters={
                    "customer_name": "Jacob",
                    "order_reference": "CMD-42",
                },
            )
        )


def test_interactive_reply_buttons_payload_uses_stable_action_ids():
    payload = build_interactive_reply_buttons_payload(
        WhatsAppInteractiveMessage(
            recipient_external_id="237699887766",
            body="Confirmer la commande ?",
            buttons=[
                WhatsAppReplyButton(action_id="order.confirm", label="Confirmer"),
                WhatsAppReplyButton(action_id="order.modify", label="Modifier"),
            ],
            footer_text="Whatassoh",
        )
    )

    assert payload["type"] == "interactive"
    assert payload["interactive"]["type"] == "button"
    assert payload["interactive"]["action"]["buttons"][0]["reply"] == {
        "id": "order.confirm",
        "title": "Confirmer",
    }


def test_parse_interactive_button_reply_normalizes_action_id():
    payload = {
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": "237699887766",
                                    "id": "wamid.button",
                                    "timestamp": "1700000000",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": "order.confirm",
                                            "title": "Confirmer",
                                        },
                                    },
                                }
                            ]
                        },
                    }
                ],
            }
        ]
    }

    message = parse_whatsapp_webhook(payload)[0]

    assert message.message_type == MessageType.INTERACTIVE
    assert message.metadata["action_id"] == "order.confirm"
    assert message.metadata["whatsapp"]["interaction_type"] == "button_reply"
    assert message.metadata["whatsapp"]["action_id"] == "order.confirm"


@pytest.mark.django_db
def test_service_window_uses_last_inbound_user_message_boundary():
    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )
    now = timezone.now()
    event = record_inbound_message_event(inbound_message(now))
    ingest_inbound_message(inbound_message(now), event)

    policy = WhatsAppServiceWindowPolicy()

    assert policy.is_open(conversation, now=now + timedelta(hours=23))
    assert not policy.is_open(conversation, now=now + timedelta(hours=24))


@pytest.mark.django_db
def test_recent_outbound_does_not_open_service_window():
    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )
    now = timezone.now()
    MessageEvent.objects.create(
        channel=MessagingChannel.WHATSAPP,
        direction=MessageDirection.OUTBOUND,
        external_message_id="wamid.out",
        external_user_id="237699887766",
        message_type=MessageType.TEXT,
        status=MessageStatus.SENT,
        sent_at=now,
    )

    assert not WhatsAppServiceWindowPolicy().is_open(conversation, now=now)


@pytest.mark.django_db
def test_closed_window_blocks_generic_conversation_text():
    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )

    with pytest.raises(WhatsAppTemplateRequired):
        send_conversation_text(conversation=conversation, text="Bonjour")


@pytest.mark.django_db
def test_inbound_reopens_window_for_free_form(monkeypatch):
    provider_calls = []

    class FakeProvider:
        capabilities = type(
            "Capabilities",
            (),
            {"text": True, "location": True, "interactive": True, "media": False},
        )()

        def send(self, message):
            provider_calls.append(message.message_type)
            event = MessageEvent.objects.create(
                channel=MessagingChannel.WHATSAPP,
                direction=MessageDirection.OUTBOUND,
                external_message_id="wamid.freeform",
                external_user_id=message.recipient_external_id,
                message_type=message.message_type,
                status=MessageStatus.SENT,
            )
            from apps.messaging.domain.dtos import ProviderSendResult

            return ProviderSendResult(
                channel=MessagingChannel.WHATSAPP,
                external_message_id="wamid.freeform",
                status=MessageStatus.SENT,
                message_event=event,
            )

    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )
    inbound = inbound_message(timezone.now())
    event = record_inbound_message_event(inbound)
    ingest_inbound_message(inbound, event)
    monkeypatch.setattr(
        "apps.messaging.application.orchestration.get_provider",
        lambda channel: FakeProvider(),
    )

    send_conversation_text(conversation=conversation, text="Merci")

    assert provider_calls == [MessageType.TEXT]


@pytest.mark.django_db
@template_settings()
def test_send_template_records_message_event_and_conversation_message(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"messages": [{"id": "wamid.template"}]}).encode()

    def fake_urlopen(request, timeout):
        return FakeResponse()

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)
    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )

    result = send_whatsapp_template_to_conversation(
        conversation=conversation,
        template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
        body_parameters={
            "customer_name": "Jacob",
            "order_reference": "CMD-42",
        },
    )

    assert result.external_message_id == "wamid.template"
    assert MessageEvent.objects.get().message_type == MessageType.TEMPLATE
    assert ConversationMessage.objects.get().message_type == MessageType.TEMPLATE


@pytest.mark.django_db
@template_settings()
def test_template_fallback_chooses_free_form_when_window_open(monkeypatch):
    calls = []

    def fake_send_text(*, conversation, text, metadata=None):
        calls.append(("text", text))
        return None

    def fake_send_template(**kwargs):
        calls.append(("template", kwargs["template_key"]))
        return None

    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )
    inbound = inbound_message(timezone.now())
    event = record_inbound_message_event(inbound)
    ingest_inbound_message(inbound, event)
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_conversation_text",
        fake_send_text,
    )
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_template_to_conversation",
        fake_send_template,
    )

    send_whatsapp_text_with_template_fallback(
        conversation=conversation,
        free_form_text="Une action est nécessaire.",
        template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
        template_body_parameters={
            "customer_name": "Jacob",
            "order_reference": "CMD-42",
        },
    )

    assert calls == [("text", "Une action est nécessaire.")]


@pytest.mark.django_db
@template_settings()
def test_template_fallback_chooses_template_when_window_closed(monkeypatch):
    calls = []

    def fake_send_template(**kwargs):
        calls.append(("template", kwargs["template_key"]))
        return None

    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_template_to_conversation",
        fake_send_template,
    )

    send_whatsapp_text_with_template_fallback(
        conversation=conversation,
        free_form_text="Une action est nécessaire.",
        template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
        template_body_parameters={
            "customer_name": "Jacob",
            "order_reference": "CMD-42",
        },
    )

    assert calls == [("template", WhatsAppTemplateKey.ORDER_ACTION_REQUIRED)]


@pytest.mark.django_db
@template_settings()
def test_interactive_send_requires_open_window(monkeypatch):
    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id="237699887766",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        channel_identity=identity,
        external_chat_id="237699887766",
    )

    with pytest.raises(WhatsAppTemplateRequired):
        send_whatsapp_interactive_reply_buttons(
            conversation=conversation,
            body="Confirmer ?",
            buttons=[WhatsAppReplyButton(action_id="order.confirm", label="Confirmer")],
        )


@pytest.mark.parametrize(
    ("status_code", "exception_class", "retryable"),
    [
        (400, WhatsAppAPIError, False),
        (429, WhatsAppRateLimitError, True),
        (500, WhatsAppAPIError, True),
    ],
)
@template_settings()
def test_template_provider_errors_are_categorized(
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
            BytesIO(b'{"error":{"message":"bad template"}}'),
        )

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)

    with pytest.raises(exception_class) as exc_info:
        WhatsAppProvider().send_template(
            WhatsAppTemplateMessage(
                recipient_external_id="237699887766",
                template_key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                body_parameters={
                    "customer_name": "Jacob",
                    "order_reference": "CMD-42",
                },
            )
        )

    assert exc_info.value.retryable is retryable
