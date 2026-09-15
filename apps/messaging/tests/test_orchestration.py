import pytest
from django.test import override_settings

from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.identity.infrastructure.models import User
from apps.messaging.application.orchestration import (
    normalize_provider_error,
    send_conversation_location,
    send_conversation_text,
)
from apps.messaging.application.registry import (
    get_channel_configuration_status,
    get_enabled_channels,
    get_provider,
    is_channel_enabled,
    supports,
)
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import MessageLocation, ProviderSendResult
from apps.messaging.domain.enums import MessageStatus, MessagingChannel
from apps.messaging.domain.exceptions import (
    MessagingAuthenticationError,
    MessagingChannelDisabled,
    MessagingRateLimitError,
    MessagingTemporaryError,
    UnsupportedMessagingCapability,
)
from apps.messaging.domain.providers import ProviderCapabilities
from apps.messaging.infrastructure.models import MessageEvent
from apps.telegram.exceptions import TelegramRateLimitError
from apps.whatsapp.exceptions import WhatsAppAPIError, WhatsAppAuthenticationError


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


class FakeProvider:
    capabilities = ProviderCapabilities(text=True, location=True)

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        event = MessageEvent.objects.create(
            channel=message.channel,
            direction="OUTBOUND",
            external_message_id=f"{message.channel}:out:1",
            external_user_id=message.recipient_external_id,
            external_chat_id=message.recipient_external_id,
            message_type=message.message_type,
            status=MessageStatus.SENT,
            metadata=message.metadata,
        )
        return ProviderSendResult(
            channel=message.channel,
            external_message_id=event.external_message_id,
            status=MessageStatus.SENT,
            provider_metadata={"fake": True},
            message_event=event,
        )


class NoLocationProvider(FakeProvider):
    capabilities = ProviderCapabilities(text=True, location=False)


@pytest.mark.parametrize(
    (
        "whatsapp_enabled",
        "telegram_enabled",
        "expected",
    ),
    [
        (True, False, [MessagingChannel.WHATSAPP]),
        (False, True, [MessagingChannel.TELEGRAM]),
        (True, True, [MessagingChannel.WHATSAPP, MessagingChannel.TELEGRAM]),
        (False, False, []),
    ],
)
def test_enabled_channels_support_four_configurations(
    whatsapp_enabled,
    telegram_enabled,
    expected,
):
    with override_settings(
        WHATSAPP_ENABLED=whatsapp_enabled,
        TELEGRAM_ENABLED=telegram_enabled,
        WHATSAPP_ACCESS_TOKEN="wa-token",
        WHATSAPP_PHONE_NUMBER_ID="wa-phone",
        WHATSAPP_VERIFY_TOKEN="wa-verify",
        WHATSAPP_APP_SECRET="wa-secret",
        TELEGRAM_BOT_TOKEN="tg-token",
        TELEGRAM_WEBHOOK_SECRET="tg-secret",
    ):
        assert get_enabled_channels() == expected
        assert is_channel_enabled(MessagingChannel.WHATSAPP) is whatsapp_enabled
        assert is_channel_enabled(MessagingChannel.TELEGRAM) is telegram_enabled


def test_get_provider_rejects_disabled_channel():
    with override_settings(WHATSAPP_ENABLED=False):
        with pytest.raises(MessagingChannelDisabled):
            get_provider(MessagingChannel.WHATSAPP)


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="wa-token",
    WHATSAPP_PHONE_NUMBER_ID="wa-phone",
    WHATSAPP_VERIFY_TOKEN="wa-verify",
    WHATSAPP_APP_SECRET="wa-secret",
    TELEGRAM_ENABLED=True,
    TELEGRAM_BOT_TOKEN="tg-token",
    TELEGRAM_WEBHOOK_SECRET="tg-secret",
)
def test_registry_resolves_enabled_providers_and_capabilities():
    assert (
        get_provider(MessagingChannel.WHATSAPP).__class__.__name__ == "WhatsAppProvider"
    )
    assert (
        get_provider(MessagingChannel.TELEGRAM).__class__.__name__ == "TelegramProvider"
    )
    assert supports(MessagingChannel.WHATSAPP, "text") is True
    assert supports(MessagingChannel.TELEGRAM, "interactive") is True
    assert (
        get_channel_configuration_status()[MessagingChannel.WHATSAPP]["configured"]
        is True
    )
    assert (
        get_channel_configuration_status()[MessagingChannel.TELEGRAM]["configured"]
        is True
    )


def test_provider_errors_are_normalized():
    assert isinstance(
        normalize_provider_error(WhatsAppAuthenticationError("auth", status_code=401)),
        MessagingAuthenticationError,
    )
    rate_limit = normalize_provider_error(
        TelegramRateLimitError("rate", status_code=429, retryable=True, retry_after=12)
    )
    assert isinstance(rate_limit, MessagingRateLimitError)
    assert rate_limit.retry_after == 12
    assert isinstance(
        normalize_provider_error(WhatsAppAPIError("down", retryable=True)),
        MessagingTemporaryError,
    )


@pytest.mark.django_db
def test_send_conversation_text_routes_by_conversation_channel(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(
        "apps.messaging.application.orchestration.get_provider",
        lambda channel: provider,
    )
    user = create_user()
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="123456",
    )
    conversation = Conversation.objects.create(
        user=user,
        channel=MessagingChannel.TELEGRAM,
        channel_identity=identity,
        external_chat_id="987654",
    )

    result = send_conversation_text(
        conversation=conversation,
        text="Quelle quantite ?",
        correlation_id="corr-1",
    )

    assert result.external_message_id == "TELEGRAM:out:1"
    assert provider.messages[0].channel == MessagingChannel.TELEGRAM
    assert provider.messages[0].recipient_external_id == "987654"
    assert provider.messages[0].metadata["correlation_id"] == "corr-1"
    assert MessageEvent.objects.count() == 1
    assert ConversationMessage.objects.count() == 1
    assert ConversationMessage.objects.get().message_event == result.message_event


@pytest.mark.django_db
def test_send_conversation_location_checks_capability(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.application.orchestration.get_provider",
        lambda channel: NoLocationProvider(),
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
    )

    with pytest.raises(UnsupportedMessagingCapability):
        send_conversation_location(
            conversation=conversation,
            location=MessageLocation(latitude=4.05, longitude=9.7),
        )


@pytest.mark.django_db
def test_failed_send_does_not_record_conversation_message(monkeypatch):
    class FailingProvider(FakeProvider):
        def send(self, message):
            raise WhatsAppAPIError("down", retryable=True)

    monkeypatch.setattr(
        "apps.messaging.application.orchestration.get_provider",
        lambda channel: FailingProvider(),
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
    )

    with pytest.raises(MessagingTemporaryError):
        send_conversation_text(conversation=conversation, text="Bonjour")

    assert MessageEvent.objects.count() == 0
    assert ConversationMessage.objects.count() == 0
