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
from apps.telegram.adapters.inbound import build_telegram_message_external_id
from apps.telegram.adapters.outbound import outbound_message_to_method_and_payload
from apps.telegram.conf import get_telegram_settings
from apps.telegram.exceptions import (
    TelegramAPIError,
    TelegramAuthenticationError,
    TelegramConfigurationError,
    TelegramInvalidPayloadError,
    TelegramRateLimitError,
)

logger = logging.getLogger(__name__)


class TelegramProvider(MessagingProvider):
    capabilities = ProviderCapabilities(text=True, location=True, interactive=True)

    def __init__(self) -> None:
        self.config = get_telegram_settings(require_enabled=True)

    def send(self, message: NormalizedOutboundMessage) -> ProviderSendResult:
        method, payload = outbound_message_to_method_and_payload(message)
        response_data = self._call(method, payload)
        external_message_id = _extract_external_message_id(response_data)
        message_event = record_outbound_message_event(
            message,
            external_message_id=external_message_id,
            external_chat_id=message.recipient_external_id,
            status=MessageStatus.SENT,
            raw_payload=response_data,
        )
        logger.info(
            "telegram_outbound_sent",
            extra={
                "channel": MessagingChannel.TELEGRAM,
                "direction": "OUTBOUND",
                "external_message_id": external_message_id,
                "external_chat_id": message.recipient_external_id,
                "message_type": message.message_type,
                "result": "sent",
            },
        )
        return ProviderSendResult(
            channel=MessagingChannel.TELEGRAM,
            external_message_id=external_message_id,
            status=MessageStatus.SENT,
            provider_metadata={"telegram": response_data},
            message_event=message_event,
        )

    def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderSendResult:
        return self.send(
            NormalizedOutboundMessage(
                channel=MessagingChannel.TELEGRAM,
                recipient_external_id=chat_id,
                message_type=MessageType.TEXT,
                text=text,
                metadata=metadata or {},
            )
        )

    def send_location(
        self,
        chat_id: str,
        location: MessageLocation,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderSendResult:
        return self.send(
            NormalizedOutboundMessage(
                channel=MessagingChannel.TELEGRAM,
                recipient_external_id=chat_id,
                message_type=MessageType.LOCATION,
                location=location,
                metadata=metadata or {},
            )
        )

    def send_chat_action(self, chat_id: str, *, action: str = "typing") -> dict[str, Any]:
        """Send a Telegram chat action without persisting a business message."""
        return self._call(
            "sendChatAction",
            {"chat_id": chat_id, "action": action},
        )

    def set_webhook(self) -> dict[str, Any]:
        if not self.config.webhook_url:
            raise TelegramConfigurationError(
                "TELEGRAM_WEBHOOK_URL is required to set the Telegram webhook."
            )
        if not self.config.webhook_secret:
            raise TelegramConfigurationError(
                "TELEGRAM_WEBHOOK_SECRET is required to set the Telegram webhook."
            )
        return self._call(
            "setWebhook",
            {
                "url": self.config.webhook_url,
                "secret_token": self.config.webhook_secret,
                "allowed_updates": ["message", "edited_message", "callback_query"],
            },
        )

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return self._call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    def get_webhook_info(self) -> dict[str, Any]:
        return self._call("getWebhookInfo", {})

    def get_updates(self, *, timeout: int = 0) -> list[dict[str, Any]]:
        """Return pending updates through Telegram long polling.

        Telegram only permits this while no webhook is configured.
        """
        if not 0 <= timeout <= 50:
            raise ValueError("Telegram polling timeout must be between 0 and 50 seconds.")
        response = self._call(
            "getUpdates",
            {"timeout": timeout, "allowed_updates": ["message"]},
        )
        result = response.get("result", [])
        if not isinstance(result, list):
            raise TelegramInvalidPayloadError("Telegram getUpdates result was not a list.")
        return result

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._method_url(method)
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.config.request_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise _api_error_from_http_error(exc) from None
        except (TimeoutError, URLError):
            raise TelegramAPIError(
                "Telegram API request failed.", retryable=True
            ) from None
        except json.JSONDecodeError as exc:
            raise TelegramInvalidPayloadError(
                "Telegram API response was not JSON."
            ) from exc

        if not data.get("ok"):
            raise _api_error_from_response(data)
        return data

    def _method_url(self, method: str) -> str:
        return f"{self.config.api_base_url}/bot{self.config.bot_token}/{method}"


def sanitize_telegram_url(url: str, bot_token: str) -> str:
    return url.replace(bot_token, "<telegram-bot-token>")


def _extract_external_message_id(response_data: dict[str, Any]) -> str | None:
    result = response_data.get("result") or {}
    chat = result.get("chat") or {}
    if "message_id" not in result or "id" not in chat:
        return None
    return build_telegram_message_external_id(
        str(chat["id"]), str(result["message_id"])
    )


def _api_error_from_http_error(exc: HTTPError) -> TelegramAPIError:
    response_data = _read_error_response(exc)
    retry_after = _retry_after(response_data)
    if exc.code in {401, 403}:
        return TelegramAuthenticationError(
            "Telegram API authentication failed.",
            status_code=exc.code,
            response_data=response_data,
        )
    if exc.code == 429:
        return TelegramRateLimitError(
            "Telegram API rate limit exceeded.",
            status_code=exc.code,
            response_data=response_data,
            retryable=True,
            retry_after=retry_after,
        )
    return TelegramAPIError(
        "Telegram API request failed.",
        status_code=exc.code,
        response_data=response_data,
        retryable=exc.code >= 500,
        retry_after=retry_after,
    )


def _api_error_from_response(response_data: dict[str, Any]) -> TelegramAPIError:
    status_code = response_data.get("error_code")
    retry_after = _retry_after(response_data)
    if status_code in {401, 403}:
        return TelegramAuthenticationError(
            "Telegram API authentication failed.",
            status_code=status_code,
            response_data=response_data,
        )
    if status_code == 429:
        return TelegramRateLimitError(
            "Telegram API rate limit exceeded.",
            status_code=status_code,
            response_data=response_data,
            retryable=True,
            retry_after=retry_after,
        )
    return TelegramAPIError(
        "Telegram API request failed.",
        status_code=status_code,
        response_data=response_data,
        retryable=bool(status_code and status_code >= 500),
        retry_after=retry_after,
    )


def _retry_after(response_data: dict[str, Any]) -> int | None:
    parameters = response_data.get("parameters") or {}
    return parameters.get("retry_after")


def _read_error_response(exc: HTTPError) -> dict[str, Any]:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
