import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from apps.messaging.application.services import record_outbound_message_event
from apps.messaging.domain.dtos import (
    MessageLocation,
    NormalizedOutboundMessage,
    ProviderSendResult,
)
from apps.messaging.domain.enums import MessageStatus, MessageType, MessagingChannel
from apps.messaging.domain.providers import MessagingProvider, ProviderCapabilities
from apps.whatsapp.adapters.outbound import (
    build_interactive_reply_buttons_payload,
    build_template_payload,
    outbound_message_to_payload,
)
from apps.whatsapp.conf import get_whatsapp_settings
from apps.whatsapp.domain.dtos import (
    WhatsAppInteractiveMessage,
    WhatsAppTemplateMessage,
)
from apps.whatsapp.domain.templates import get_template_definition
from apps.whatsapp.exceptions import (
    WhatsAppAPIError,
    WhatsAppAuthenticationError,
    WhatsAppInvalidPayloadError,
    WhatsAppRateLimitError,
)

logger = logging.getLogger(__name__)


class WhatsAppProvider(MessagingProvider):
    capabilities = ProviderCapabilities(text=True, location=True)

    def __init__(self) -> None:
        self.config = get_whatsapp_settings(require_enabled=True)

    def send(self, message: NormalizedOutboundMessage) -> ProviderSendResult:
        response_data = self._post_message(outbound_message_to_payload(message))
        external_message_id = _extract_message_id(response_data)
        message_event = record_outbound_message_event(
            message,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            raw_payload=response_data,
        )
        logger.info(
            "whatsapp_outbound_sent",
            extra={
                "channel": MessagingChannel.WHATSAPP,
                "direction": "OUTBOUND",
                "external_message_id": external_message_id,
                "message_type": message.message_type,
                "result": "sent",
            },
        )
        return ProviderSendResult(
            channel=MessagingChannel.WHATSAPP,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            provider_metadata={"whatsapp": response_data},
            message_event=message_event,
        )

    def send_text(
        self,
        recipient_external_id: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderSendResult:
        return self.send(
            NormalizedOutboundMessage(
                channel=MessagingChannel.WHATSAPP,
                recipient_external_id=recipient_external_id,
                message_type=MessageType.TEXT,
                text=text,
                metadata=metadata or {},
            )
        )

    def send_location(
        self,
        recipient_external_id: str,
        location: MessageLocation,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderSendResult:
        return self.send(
            NormalizedOutboundMessage(
                channel=MessagingChannel.WHATSAPP,
                recipient_external_id=recipient_external_id,
                message_type=MessageType.LOCATION,
                location=location,
                metadata=metadata or {},
            )
        )

    def send_template(self, message: WhatsAppTemplateMessage) -> ProviderSendResult:
        response_data = self._post_message(build_template_payload(message))
        definition = get_template_definition(
            message.template_key,
            language_code=message.language_code,
        )
        external_message_id = _extract_message_id(response_data)
        outbound = NormalizedOutboundMessage(
            channel=MessagingChannel.WHATSAPP,
            recipient_external_id=message.recipient_external_id,
            message_type=MessageType.TEMPLATE,
            metadata={
                **message.metadata,
                "whatsapp": {
                    "template_key": str(definition.key),
                    "template_name": definition.meta_name,
                    "language_code": definition.language_code,
                },
            },
        )
        message_event = record_outbound_message_event(
            outbound,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            raw_payload=response_data,
        )
        logger.info(
            "whatsapp_template_sent",
            extra={
                "channel": MessagingChannel.WHATSAPP,
                "direction": "OUTBOUND",
                "external_message_id": external_message_id,
                "message_type": MessageType.TEMPLATE,
                "template_key": str(definition.key),
                "result": "sent",
            },
        )
        return ProviderSendResult(
            channel=MessagingChannel.WHATSAPP,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            provider_metadata={"whatsapp": response_data},
            message_event=message_event,
        )

    def send_interactive_reply_buttons(
        self,
        message: WhatsAppInteractiveMessage,
    ) -> ProviderSendResult:
        response_data = self._post_message(
            build_interactive_reply_buttons_payload(message)
        )
        external_message_id = _extract_message_id(response_data)
        outbound = NormalizedOutboundMessage(
            channel=MessagingChannel.WHATSAPP,
            recipient_external_id=message.recipient_external_id,
            message_type=MessageType.INTERACTIVE,
            text=message.body,
            metadata={
                **message.metadata,
                "whatsapp": {
                    "interactive_type": "button",
                    "actions": [
                        {"id": button.action_id, "label": button.label}
                        for button in message.buttons
                    ],
                },
            },
        )
        message_event = record_outbound_message_event(
            outbound,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            raw_payload=response_data,
        )
        logger.info(
            "whatsapp_interactive_sent",
            extra={
                "channel": MessagingChannel.WHATSAPP,
                "direction": "OUTBOUND",
                "external_message_id": external_message_id,
                "message_type": MessageType.INTERACTIVE,
                "result": "sent",
            },
        )
        return ProviderSendResult(
            channel=MessagingChannel.WHATSAPP,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            provider_metadata={"whatsapp": response_data},
            message_event=message_event,
        )

    def _post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = (
            "https://graph.facebook.com/"
            f"{self.config.graph_api_version}/{self.config.phone_number_id}/messages"
        )
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.config.request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise _api_error_from_http_error(exc) from exc
        except (TimeoutError, URLError) as exc:
            raise WhatsAppAPIError(
                "WhatsApp API request failed.", retryable=True
            ) from exc
        except json.JSONDecodeError as exc:
            raise WhatsAppInvalidPayloadError(
                "WhatsApp API response was not JSON."
            ) from exc


def _extract_message_id(response_data: dict[str, Any]) -> str | None:
    messages = response_data.get("messages") or []
    if not messages:
        return None
    return messages[0].get("id")


def _api_error_from_http_error(exc: HTTPError) -> WhatsAppAPIError:
    response_data = _read_error_response(exc)
    if exc.code in {401, 403}:
        return WhatsAppAuthenticationError(
            "WhatsApp API authentication failed.",
            status_code=exc.code,
            response_data=response_data,
        )
    if exc.code == 429:
        return WhatsAppRateLimitError(
            "WhatsApp API rate limit exceeded.",
            status_code=exc.code,
            response_data=response_data,
            retryable=True,
        )
    return WhatsAppAPIError(
        "WhatsApp API request failed.",
        status_code=exc.code,
        response_data=response_data,
        retryable=exc.code >= 500,
    )


def _read_error_response(exc: HTTPError) -> dict[str, Any]:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
