from dataclasses import dataclass

from django.conf import settings

from apps.whatsapp.exceptions import WhatsAppConfigurationError


@dataclass(frozen=True, kw_only=True)
class WhatsAppSettings:
    enabled: bool
    access_token: str
    phone_number_id: str
    business_account_id: str
    verify_token: str
    app_secret: str
    graph_api_version: str
    request_timeout: float


def get_whatsapp_settings(*, require_enabled: bool = False) -> WhatsAppSettings:
    config = WhatsAppSettings(
        enabled=settings.WHATSAPP_ENABLED,
        access_token=settings.WHATSAPP_ACCESS_TOKEN,
        phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
        business_account_id=settings.WHATSAPP_BUSINESS_ACCOUNT_ID,
        verify_token=settings.WHATSAPP_VERIFY_TOKEN,
        app_secret=settings.WHATSAPP_APP_SECRET,
        graph_api_version=settings.META_GRAPH_API_VERSION,
        request_timeout=settings.WHATSAPP_REQUEST_TIMEOUT,
    )

    if require_enabled and not config.enabled:
        raise WhatsAppConfigurationError("WhatsApp integration is disabled.")

    if config.enabled or require_enabled:
        missing = [
            name
            for name, value in {
                "WHATSAPP_ACCESS_TOKEN": config.access_token,
                "WHATSAPP_PHONE_NUMBER_ID": config.phone_number_id,
                "WHATSAPP_VERIFY_TOKEN": config.verify_token,
                "WHATSAPP_APP_SECRET": config.app_secret,
                "META_GRAPH_API_VERSION": config.graph_api_version,
            }.items()
            if not value
        ]
        if missing:
            raise WhatsAppConfigurationError(
                "WhatsApp integration is missing required configuration: "
                + ", ".join(missing)
            )

    return config
