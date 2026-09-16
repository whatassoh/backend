from dataclasses import dataclass

from apps.whatsapp.domain.enums import WhatsAppTemplateKey
from apps.whatsapp.domain.exceptions import (
    WhatsAppTemplateConfigurationError,
    WhatsAppTemplateValidationError,
)


@dataclass(frozen=True, kw_only=True)
class TemplateDefinition:
    key: WhatsAppTemplateKey
    meta_name: str
    language_code: str
    body_parameters: tuple[str, ...]
    category: str
    use_case: str
    button_parameters: tuple[str, ...] = ()
    header_parameters: tuple[str, ...] = ()


def get_template_definition(
    key: WhatsAppTemplateKey | str,
    *,
    language_code: str | None = None,
) -> TemplateDefinition:
    from apps.whatsapp.conf import get_whatsapp_settings

    key = WhatsAppTemplateKey(key)
    config = get_whatsapp_settings()
    configured = config.templates.get(key)
    if configured is None or not configured.meta_name:
        raise WhatsAppTemplateConfigurationError(
            f"WhatsApp template is not configured: {key}"
        )
    if language_code and language_code != configured.language_code:
        raise WhatsAppTemplateConfigurationError(
            f"WhatsApp template language is not configured: {key}/{language_code}"
        )
    return configured


def validate_template_parameters(
    definition: TemplateDefinition,
    *,
    body_parameters: dict[str, str],
    header_parameters: dict[str, str] | None = None,
    button_parameters: dict[str, str] | None = None,
) -> None:
    _validate_parameter_set("body", definition.body_parameters, body_parameters)
    _validate_parameter_set(
        "header",
        definition.header_parameters,
        header_parameters or {},
    )
    _validate_parameter_set(
        "button",
        definition.button_parameters,
        button_parameters or {},
    )


def _validate_parameter_set(
    component: str,
    expected: tuple[str, ...],
    provided: dict[str, str],
) -> None:
    expected_set = set(expected)
    provided_set = set(provided)
    missing = expected_set - provided_set
    extra = provided_set - expected_set
    if missing or extra:
        raise WhatsAppTemplateValidationError(
            f"Invalid WhatsApp template {component} parameters."
        )
    for value in provided.values():
        if not isinstance(value, str) or not value:
            raise WhatsAppTemplateValidationError(
                f"WhatsApp template {component} parameters must be non-empty strings."
            )
