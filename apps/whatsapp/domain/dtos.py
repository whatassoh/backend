from dataclasses import dataclass, field
from typing import Any

from apps.whatsapp.domain.enums import WhatsAppTemplateKey


@dataclass(frozen=True, kw_only=True)
class WhatsAppTemplateMessage:
    recipient_external_id: str
    template_key: WhatsAppTemplateKey | str
    body_parameters: dict[str, str]
    language_code: str | None = None
    header_parameters: dict[str, str] = field(default_factory=dict)
    button_parameters: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class WhatsAppReplyButton:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class WhatsAppInteractiveMessage:
    recipient_external_id: str
    body: str
    buttons: list[WhatsAppReplyButton]
    header_text: str | None = None
    footer_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class WhatsAppInteractiveListMessage:
    recipient_external_id: str
    body: str
    rows: list[WhatsAppReplyButton]
    button_label: str = "Choisir"
    header_text: str | None = None
    footer_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
