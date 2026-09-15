from dataclasses import dataclass

from django.conf import settings

from apps.telegram.exceptions import TelegramConfigurationError


@dataclass(frozen=True, kw_only=True)
class TelegramSettings:
    enabled: bool
    bot_token: str
    webhook_secret: str
    webhook_url: str
    api_base_url: str
    request_timeout: float


def get_telegram_settings(*, require_enabled: bool = False) -> TelegramSettings:
    config = TelegramSettings(
        enabled=settings.TELEGRAM_ENABLED,
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        webhook_secret=settings.TELEGRAM_WEBHOOK_SECRET,
        webhook_url=settings.TELEGRAM_WEBHOOK_URL,
        api_base_url=settings.TELEGRAM_API_BASE_URL.rstrip("/"),
        request_timeout=settings.TELEGRAM_REQUEST_TIMEOUT,
    )

    if require_enabled and not config.enabled:
        raise TelegramConfigurationError("Telegram integration is disabled.")

    if config.enabled or require_enabled:
        missing = [
            name
            for name, value in {
                "TELEGRAM_BOT_TOKEN": config.bot_token,
                "TELEGRAM_WEBHOOK_SECRET": config.webhook_secret,
            }.items()
            if not value
        ]
        if missing:
            raise TelegramConfigurationError(
                "Telegram integration is missing required configuration: "
                + ", ".join(missing)
            )

    return config
