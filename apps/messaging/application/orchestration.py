from dataclasses import asdict
from typing import Any

from apps.conversations.application.services import record_outbound_message
from apps.conversations.infrastructure.models import Conversation
from apps.messaging.application.registry import get_provider
from apps.messaging.domain.dtos import (
    MessageLocation,
    NormalizedOutboundMessage,
    ProviderSendResult,
)
from apps.messaging.domain.enums import MessageStatus, MessageType, MessagingChannel
from apps.messaging.domain.exceptions import (
    MessagingAuthenticationError,
    MessagingConfigurationError,
    MessagingError,
    MessagingPermanentError,
    MessagingRateLimitError,
    MessagingTemporaryError,
    UnsupportedMessagingCapability,
)


def send(message: NormalizedOutboundMessage) -> ProviderSendResult:
    provider = get_provider(message.channel)
    _ensure_capability(provider.capabilities, message.message_type)
    try:
        return provider.send(message)
    except Exception as exc:
        raise normalize_provider_error(exc) from exc


def send_text(
    *,
    channel: str,
    recipient_external_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> ProviderSendResult:
    return send(
        NormalizedOutboundMessage(
            channel=channel,
            recipient_external_id=recipient_external_id,
            message_type=MessageType.TEXT,
            text=text,
            metadata=metadata or {},
        )
    )


def send_location(
    *,
    channel: str,
    recipient_external_id: str,
    location: MessageLocation,
    metadata: dict[str, Any] | None = None,
) -> ProviderSendResult:
    return send(
        NormalizedOutboundMessage(
            channel=channel,
            recipient_external_id=recipient_external_id,
            message_type=MessageType.LOCATION,
            location=location,
            metadata=metadata or {},
        )
    )


def send_text_to_identity(
    *,
    channel_identity,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> ProviderSendResult:
    return send_text(
        channel=channel_identity.channel,
        recipient_external_id=channel_identity.external_user_id,
        text=text,
        metadata=metadata,
    )


def send_conversation_text(
    *,
    conversation: Conversation,
    text: str,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> ProviderSendResult:
    if conversation.channel == MessagingChannel.WHATSAPP:
        from apps.whatsapp.domain.enums import WhatsAppOutboundDecision
        from apps.whatsapp.domain.exceptions import WhatsAppTemplateRequired
        from apps.whatsapp.domain.outbound_policy import WhatsAppOutboundPolicy

        if (
            WhatsAppOutboundPolicy().decide(conversation)
            == WhatsAppOutboundDecision.TEMPLATE_REQUIRED
        ):
            raise WhatsAppTemplateRequired("WhatsApp service window is closed.")

    message = _conversation_outbound_message(
        conversation=conversation,
        message_type=MessageType.TEXT,
        text=text,
        metadata=metadata,
        correlation_id=correlation_id,
    )
    result = send(message)
    record_outbound_message(
        conversation=conversation,
        message=message,
        message_event=result.message_event,
    )
    return result


def send_conversation_interactive(
    *,
    conversation: Conversation,
    text: str,
    actions: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> ProviderSendResult:
    """Send channel-neutral interactive actions through the conversation boundary."""
    message_metadata = dict(metadata or {})
    message_metadata["actions"] = actions
    message = _conversation_outbound_message(
        conversation=conversation,
        message_type=MessageType.INTERACTIVE,
        text=text,
        metadata=message_metadata,
        correlation_id=correlation_id,
    )
    result = send(message)
    record_outbound_message(
        conversation=conversation,
        message=message,
        message_event=result.message_event,
    )
    return result


def send_conversation_location(
    *,
    conversation: Conversation,
    location: MessageLocation,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> ProviderSendResult:
    message = _conversation_outbound_message(
        conversation=conversation,
        message_type=MessageType.LOCATION,
        location=location,
        metadata=metadata,
        correlation_id=correlation_id,
    )
    result = send(message)
    record_outbound_message(
        conversation=conversation,
        message=message,
        message_event=result.message_event,
    )
    return result


def normalize_provider_error(exc: Exception) -> MessagingError:
    if isinstance(exc, MessagingError):
        return exc

    name = exc.__class__.__name__
    retry_after = getattr(exc, "retry_after", None)
    retryable = bool(getattr(exc, "retryable", False))

    if name.endswith("ConfigurationError"):
        return MessagingConfigurationError(str(exc))
    if name.endswith("AuthenticationError"):
        return MessagingAuthenticationError(str(exc))
    if name.endswith("RateLimitError"):
        return MessagingRateLimitError(str(exc), retry_after=retry_after)
    if retryable:
        return MessagingTemporaryError(str(exc))
    return MessagingPermanentError(str(exc))


def _conversation_outbound_message(
    *,
    conversation: Conversation,
    message_type: MessageType,
    text: str | None = None,
    location: MessageLocation | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> NormalizedOutboundMessage:
    message_metadata = dict(metadata or {})
    if correlation_id:
        message_metadata["correlation_id"] = correlation_id

    recipient_external_id = (
        conversation.external_chat_id or conversation.channel_identity.external_user_id
    )
    return NormalizedOutboundMessage(
        channel=conversation.channel,
        recipient_external_id=recipient_external_id,
        message_type=message_type,
        text=text,
        location=location,
        metadata=message_metadata,
    )


def _ensure_capability(capabilities, message_type: MessageType | str) -> None:
    capability = _capability_for_message_type(message_type)
    if not bool(getattr(capabilities, capability, False)):
        raise UnsupportedMessagingCapability(
            f"Provider does not support messaging capability: {capability}"
        )


def _capability_for_message_type(message_type: MessageType | str) -> str:
    if message_type == MessageType.TEXT:
        return "text"
    if message_type == MessageType.LOCATION:
        return "location"
    if message_type == MessageType.INTERACTIVE:
        return "interactive"
    if message_type in {
        MessageType.IMAGE,
        MessageType.DOCUMENT,
        MessageType.AUDIO,
        MessageType.VIDEO,
    }:
        return "media"
    return "unknown"


def provider_result_from_event(
    *,
    channel: str,
    message_event,
    provider_metadata: dict[str, Any] | None = None,
) -> ProviderSendResult:
    return ProviderSendResult(
        channel=channel,
        external_message_id=message_event.external_message_id,
        status=MessageStatus.SENT,
        provider_metadata=provider_metadata or {},
        message_event=message_event,
    )


def location_metadata(location: MessageLocation | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return asdict(location)
