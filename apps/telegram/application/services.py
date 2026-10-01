import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.conversations.application.flow import process_inbound_conversation
from apps.conversations.application.services import ingest_inbound_message
from apps.messaging.application.activities import show_processing_indicator
from apps.messaging.application.reliable_pipeline import (
    channel_pipeline_enabled,
    enqueue_inbound_turn,
)
from apps.messaging.domain.enums import (
    MessageDirection,
    MessageStatus,
    MessagingChannel,
)
from apps.messaging.infrastructure.models import MessageEvent
from apps.telegram.adapters.inbound import parse_telegram_update

logger = logging.getLogger(__name__)


def process_telegram_webhook(payload: dict[str, Any]) -> dict[str, int]:
    messages = parse_telegram_update(payload)
    processed = 0
    duplicates = 0
    ignored = 0
    queued = 0

    for message in messages:
        logger.info(
            "inbound_normalized",
            extra={
                "channel": MessagingChannel.TELEGRAM,
                "message_type": message.message_type,
                "action_id": message.metadata.get("action_id"),
            },
        )
        with transaction.atomic():
            message_event, created = _get_or_create_inbound_message_event(message)
            if not created and _has_conversation_message(message_event):
                duplicates += 1
                continue
            inbound = ingest_inbound_message(message, message_event)
            show_processing_indicator(message)
            if channel_pipeline_enabled(message.channel):
                _, created_item = enqueue_inbound_turn(
                    inbound=inbound, message=message, message_event=message_event
                )
                queued += int(created_item)
                continue
            message_event.status = MessageStatus.PROCESSED
            message_event.processed_at = timezone.now()
            message_event.save(update_fields=["status", "processed_at", "updated_at"])
        try:
            outcome = process_inbound_conversation(
                inbound.conversation,
                message,
                correlation_id=str(message_event.id),
            )
        except Exception as exc:
            logger.exception(
                "inbound_processing_failed",
                extra={
                    "correlation_id": str(message_event.id),
                    "channel": MessagingChannel.TELEGRAM,
                    "message_type": message.message_type,
                    "exception_class": type(exc).__name__,
                },
            )
            raise
        from apps.conversations.application.responding import deliver_outcome_response

        deliver_outcome_response(
            conversation=inbound.conversation,
            outcome=outcome,
            correlation_id=str(message_event.id),
        )
        processed += 1

        logger.info(
            "telegram_inbound_processed",
            extra={
                "channel": MessagingChannel.TELEGRAM,
                "direction": MessageDirection.INBOUND,
                "external_message_id": message.external_message_id,
                "external_user_id": message.external_user_id,
                "external_chat_id": message.external_chat_id,
                "message_type": message.message_type,
                "result": "processed",
            },
        )

    if not messages:
        ignored = 1

    result = {
        "processed": processed,
        "duplicates": duplicates,
        "ignored": ignored,
    }
    if channel_pipeline_enabled(MessagingChannel.TELEGRAM):
        result["queued"] = queued
    return result


def _get_or_create_inbound_message_event(message):
    try:
        return MessageEvent.objects.get_or_create(
            channel=MessagingChannel.TELEGRAM,
            external_message_id=message.external_message_id,
            defaults={
                "direction": MessageDirection.INBOUND,
                "external_user_id": message.external_user_id,
                "external_chat_id": message.external_chat_id,
                "message_type": message.message_type,
                "status": MessageStatus.RECEIVED,
                "raw_payload": message.raw_payload,
                "metadata": message.metadata,
                "received_at": message.occurred_at or timezone.now(),
            },
        )
    except IntegrityError:
        transaction.set_rollback(False)
        return (
            MessageEvent.objects.get(
                channel=MessagingChannel.TELEGRAM,
                external_message_id=message.external_message_id,
            ),
            False,
        )


def _has_conversation_message(message_event: MessageEvent) -> bool:
    return hasattr(message_event, "conversation_message")
