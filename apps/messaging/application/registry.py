from collections.abc import Callable

from django.conf import settings

from apps.messaging.domain.enums import MessagingChannel
from apps.messaging.domain.exceptions import (
    MessagingChannelDisabled,
    MessagingConfigurationError,
)
from apps.messaging.domain.providers import MessagingProvider, ProviderCapabilities

ProviderFactory = Callable[[], MessagingProvider]


def is_channel_enabled(channel: MessagingChannel | str) -> bool:
    channel = MessagingChannel(channel)
    if channel == MessagingChannel.WHATSAPP:
        return bool(getattr(settings, "WHATSAPP_ENABLED", False))
    if channel == MessagingChannel.TELEGRAM:
        return bool(getattr(settings, "TELEGRAM_ENABLED", False))
    return False


def get_enabled_channels() -> list[MessagingChannel]:
    return [channel for channel in MessagingChannel if is_channel_enabled(channel)]


def get_provider_capabilities(
    channel: MessagingChannel | str,
) -> ProviderCapabilities:
    channel = MessagingChannel(channel)
    if channel == MessagingChannel.WHATSAPP:
        from apps.whatsapp.providers.cloud_api import WhatsAppProvider

        return WhatsAppProvider.capabilities
    if channel == MessagingChannel.TELEGRAM:
        from apps.telegram.providers.bot_api import TelegramProvider

        return TelegramProvider.capabilities
    raise MessagingConfigurationError(f"Unsupported messaging channel: {channel}")


def get_provider(channel: MessagingChannel | str) -> MessagingProvider:
    channel = MessagingChannel(channel)
    if not is_channel_enabled(channel):
        raise MessagingChannelDisabled(f"Messaging channel is disabled: {channel}")

    if channel == MessagingChannel.WHATSAPP:
        from apps.whatsapp.providers.cloud_api import WhatsAppProvider

        return WhatsAppProvider()
    if channel == MessagingChannel.TELEGRAM:
        from apps.telegram.providers.bot_api import TelegramProvider

        return TelegramProvider()
    raise MessagingConfigurationError(f"Unsupported messaging channel: {channel}")


def supports(channel: MessagingChannel | str, capability: str) -> bool:
    capabilities = get_provider_capabilities(channel)
    return bool(getattr(capabilities, capability, False))


def get_channel_configuration_status() -> dict[str, dict[str, bool]]:
    return {
        MessagingChannel.WHATSAPP: {
            "enabled": is_channel_enabled(MessagingChannel.WHATSAPP),
            "configured": _is_whatsapp_configured(),
        },
        MessagingChannel.TELEGRAM: {
            "enabled": is_channel_enabled(MessagingChannel.TELEGRAM),
            "configured": _is_telegram_configured(),
        },
    }


def _is_whatsapp_configured() -> bool:
    if not is_channel_enabled(MessagingChannel.WHATSAPP):
        return False
    required = [
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET",
    ]
    return all(bool(getattr(settings, name, "")) for name in required)


def _is_telegram_configured() -> bool:
    if not is_channel_enabled(MessagingChannel.TELEGRAM):
        return False
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"]
    return all(bool(getattr(settings, name, "")) for name in required)
