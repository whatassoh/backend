import logging

from apps.messaging.domain.enums import MessagingChannel

logger = logging.getLogger(__name__)


def show_processing_indicator(message) -> None:
    """Best-effort channel-native processing indicator.

    This is intentionally isolated to the messaging edge and never records a
    conversation-level message or mutates application state.
    """
    channel = getattr(message, "channel", None)
    if channel == MessagingChannel.TELEGRAM:
        _show_telegram_processing_indicator(message)
        return
    if channel == MessagingChannel.WHATSAPP:
        _show_whatsapp_processing_indicator(message)


def _show_telegram_processing_indicator(message) -> None:
    from apps.telegram.providers.bot_api import TelegramProvider

    chat_id = getattr(message, "external_chat_id", None) or getattr(
        message, "external_user_id", None
    )
    if not chat_id:
        return
    try:
        TelegramProvider().send_chat_action(str(chat_id), action="typing")
    except Exception:
        logger.warning(
            "telegram_processing_indicator_failed",
            extra={
                "channel": MessagingChannel.TELEGRAM,
                "external_chat_id": str(chat_id),
            },
            exc_info=True,
        )


def _show_whatsapp_processing_indicator(message) -> None:
    from apps.whatsapp.providers.cloud_api import WhatsAppProvider

    recipient_id = getattr(message, "external_user_id", None)
    if not recipient_id:
        return
    try:
        WhatsAppProvider().send_typing_indicator(str(recipient_id))
    except Exception:
        logger.warning(
            "whatsapp_processing_indicator_failed",
            extra={
                "channel": MessagingChannel.WHATSAPP,
                "external_user_id": str(recipient_id),
            },
            exc_info=True,
        )
