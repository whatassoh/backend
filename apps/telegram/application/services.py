import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.conversations.application.services import ingest_inbound_message
from apps.conversations.application.flow import process_inbound_conversation
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

    for message in messages:
        with transaction.atomic():
            message_event, created = _get_or_create_inbound_message_event(message)
            if not created and _has_conversation_message(message_event):
                duplicates += 1
                continue
            inbound = ingest_inbound_message(message, message_event)
            message_event.status = MessageStatus.PROCESSED
            message_event.processed_at = timezone.now()
            message_event.save(update_fields=["status", "processed_at", "updated_at"])
        outcome = process_inbound_conversation(
            inbound.conversation,
            message,
            correlation_id=str(message_event.id),
        )
        if (
            outcome.response.metadata.get("journey_selection")
            or outcome.response.metadata.get("seller_onboarding")
            or outcome.response.metadata.get("seller_workspace")
            or outcome.response.metadata.get("seller_catalog")
            or outcome.response.metadata.get("driver_onboarding")
            or outcome.response.metadata.get("driver_profile_ready")
            or outcome.response.metadata.get("driver_availability")
            or outcome.response.metadata.get("driver_location")
        ):
            from apps.conversations.application.responding import send_response_plan
            send_response_plan(conversation=inbound.conversation, response=outcome.response, correlation_id=str(message_event.id))
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

    return {
        "processed": processed,
        "duplicates": duplicates,
        "ignored": ignored,
    }


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
