from dataclasses import dataclass

from django.conf import settings

from apps.whatsapp.domain.enums import WhatsAppTemplateKey
from apps.whatsapp.domain.templates import TemplateDefinition
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
    template_language_code: str
    templates: dict[WhatsAppTemplateKey, TemplateDefinition]


def get_whatsapp_settings(*, require_enabled: bool = False) -> WhatsAppSettings:
    template_language_code = settings.WHATSAPP_TEMPLATE_LANGUAGE_CODE
    config = WhatsAppSettings(
        enabled=settings.WHATSAPP_ENABLED,
        access_token=settings.WHATSAPP_ACCESS_TOKEN,
        phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
        business_account_id=settings.WHATSAPP_BUSINESS_ACCOUNT_ID,
        verify_token=settings.WHATSAPP_VERIFY_TOKEN,
        app_secret=settings.WHATSAPP_APP_SECRET,
        graph_api_version=settings.META_GRAPH_API_VERSION,
        request_timeout=settings.WHATSAPP_REQUEST_TIMEOUT,
        template_language_code=template_language_code,
        templates={
            WhatsAppTemplateKey.ORDER_UPDATE: TemplateDefinition(
                key=WhatsAppTemplateKey.ORDER_UPDATE,
                meta_name=settings.WHATSAPP_TEMPLATE_ORDER_UPDATE,
                language_code=template_language_code,
                body_parameters=("customer_name", "order_reference", "update_text"),
                category="UTILITY",
                use_case="order_update",
            ),
            WhatsAppTemplateKey.ORDER_ACTION_REQUIRED: TemplateDefinition(
                key=WhatsAppTemplateKey.ORDER_ACTION_REQUIRED,
                meta_name=settings.WHATSAPP_TEMPLATE_ORDER_ACTION_REQUIRED,
                language_code=template_language_code,
                body_parameters=("customer_name", "order_reference"),
                category="UTILITY",
                use_case="order_action_required",
            ),
            WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED: TemplateDefinition(
                key=WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
                meta_name=settings.WHATSAPP_TEMPLATE_OPERATIONAL_ACTION_REQUIRED,
                language_code=template_language_code,
                body_parameters=("recipient_name",),
                category="UTILITY",
                use_case="operational_action_required",
            ),
        },
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
