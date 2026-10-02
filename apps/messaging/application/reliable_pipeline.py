"""Durable ordered inbox/outbox pipeline for conversational turns.

PostgreSQL is the coordinator. Celery only nudges work: every task reloads and
locks durable state, and fencing tokens prevent an expired worker from writing
after a newer worker has acquired the item.
"""

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.conversations.application.flow import process_inbound_conversation
from apps.conversations.application.orchestrator import ResponseAction, ResponsePlan
from apps.conversations.application.responding import send_response_plan
from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.messaging.application.failures import FailureClassifier
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import (
    InboxStatus,
    MessageDirection,
    MessageStatus,
    OutboxStatus,
)
from apps.messaging.infrastructure.models import (
    ConversationInboxItem,
    ConversationOutboxItem,
    MessageEvent,
)
from apps.whatsapp.domain.response_validation import validate_response_for_channel
from common.async_tasks import enqueue_after_commit

logger = logging.getLogger(__name__)

INBOX_TERMINAL = (InboxStatus.PROCESSED, InboxStatus.DEAD_LETTER)
OUTBOX_TERMINAL = (OutboxStatus.DELIVERED, OutboxStatus.DEAD_LETTER)


def channel_pipeline_enabled(channel: str) -> bool:
    enabled = getattr(settings, "CONVERSATION_INBOX_CHANNELS", ())
    return channel in {str(value).upper() for value in enabled}


@transaction.atomic
def enqueue_inbound_turn(*, inbound, message, message_event):
    """Assign a monotonic sequence while holding the conversation row lock."""

    existing = ConversationInboxItem.objects.filter(message_event=message_event).first()
    if existing is not None:
        return existing, False
    conversation = Conversation.objects.select_for_update().get(
        pk=inbound.conversation.pk
    )
    last = (
        ConversationInboxItem.objects.filter(conversation=conversation).aggregate(
            value=Max("sequence")
        )["value"]
        or 0
    )
    item = ConversationInboxItem.objects.create(
        conversation=conversation,
        message_event=message_event,
        sequence=last + 1,
        payload=_serialize_inbound(message),
    )
    logger.info(
        "conversation_inbox_enqueued",
        extra={
            "inbox_item_id": str(item.id),
            "conversation_id": str(conversation.id),
            "sequence": item.sequence,
            "channel": conversation.channel,
        },
    )
    _enqueue_inbox_after_commit(item)
    return item, True


def _enqueue_inbox_after_commit(item):
    from apps.messaging.tasks import drain_conversation_inbox

    enqueue_after_commit(
        lambda: drain_conversation_inbox.delay(str(item.id), str(item.id)),
        task_name="messaging.drain_conversation_inbox",
        entity_id=str(item.id),
        correlation_id=str(item.id),
    )


def _enqueue_outbox_after_commit(item):
    from apps.messaging.tasks import deliver_conversation_outbox

    enqueue_after_commit(
        lambda: deliver_conversation_outbox.delay(
            str(item.id), str(item.inbox_item_id)
        ),
        task_name="messaging.deliver_conversation_outbox",
        entity_id=str(item.id),
        correlation_id=str(item.inbox_item_id),
    )


@transaction.atomic
def claim_inbox(item_id):
    now = timezone.now()
    item = (
        ConversationInboxItem.objects.select_for_update()
        .select_related("conversation", "message_event")
        .get(pk=item_id)
    )
    if item.status in INBOX_TERMINAL or item.available_at > now:
        return None
    if (
        item.status == InboxStatus.PROCESSING
        and item.lease_expires_at
        and item.lease_expires_at > now
    ):
        return None
    blocked = (
        ConversationInboxItem.objects.filter(
            conversation=item.conversation,
            sequence__lt=item.sequence,
        )
        .exclude(status__in=INBOX_TERMINAL)
        .exists()
    )
    if blocked:
        return None
    token = uuid.uuid4()
    item.status = InboxStatus.PROCESSING
    item.attempts += 1
    item.fencing_token = token
    item.lease_expires_at = now + timedelta(
        seconds=settings.CONVERSATION_INBOX_LEASE_SECONDS
    )
    item.save(
        update_fields=[
            "status",
            "attempts",
            "fencing_token",
            "lease_expires_at",
            "updated_at",
        ]
    )
    logger.info(
        "conversation_inbox_claimed",
        extra={
            "inbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
            "attempt": item.attempts,
        },
    )
    return token


@transaction.atomic
def process_claimed_inbox(item_id, token):
    """Commit business mutations and the matching response intent together."""

    item = (
        ConversationInboxItem.objects.select_for_update()
        .select_related("conversation", "message_event")
        .get(pk=item_id)
    )
    if item.status != InboxStatus.PROCESSING or item.fencing_token != token:
        return "STALE"
    message = _deserialize_inbound(item.payload)
    outcome = process_inbound_conversation(
        item.conversation, message, correlation_id=str(item.id)
    )
    response = getattr(outcome, "response", None)
    outbox = None
    if (
        response is not None
        and isinstance(response.text, str)
        and response.text.strip()
    ):
        # Fail in the same transaction as the business turn.  An invalid
        # channel plan can therefore never advance business state then become
        # an impossible durable response.
        validate_response_for_channel(response, item.conversation.channel)
        outbox, _ = ConversationOutboxItem.objects.get_or_create(
            inbox_item=item,
            defaults={
                "conversation": item.conversation,
                "sequence": item.sequence,
                "payload": _serialize_response(response),
                "idempotency_key": f"conversation-response:{item.id}",
            },
        )
    now = timezone.now()
    item.status = InboxStatus.PROCESSED
    item.processed_at = now
    item.lease_expires_at = None
    item.fencing_token = None
    item.last_error_category = None
    item.last_error_message = None
    item.save(
        update_fields=[
            "status",
            "processed_at",
            "lease_expires_at",
            "fencing_token",
            "last_error_category",
            "last_error_message",
            "updated_at",
        ]
    )
    MessageEvent.objects.filter(pk=item.message_event_id).update(
        status=MessageStatus.PROCESSED, processed_at=now
    )
    if outbox is not None:
        _enqueue_outbox_after_commit(outbox)
    _enqueue_next_inbox_after_commit(item)
    logger.info(
        "conversation_inbox_processed",
        extra={
            "inbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
            "outbox_created": outbox is not None,
        },
    )
    return "PROCESSED"


def _enqueue_next_inbox_after_commit(item):
    next_item = (
        ConversationInboxItem.objects.filter(
            conversation=item.conversation, sequence__gt=item.sequence
        )
        .exclude(status__in=INBOX_TERMINAL)
        .order_by("sequence")
        .first()
    )
    if next_item is not None:
        _enqueue_inbox_after_commit(next_item)


@transaction.atomic
def record_inbox_failure(item_id, token, exc):
    item = ConversationInboxItem.objects.select_for_update().get(pk=item_id)
    if item.status != InboxStatus.PROCESSING or item.fencing_token != token:
        return "STALE"
    retryable = item.attempts < settings.CONVERSATION_INBOX_MAX_ATTEMPTS
    delay = min(
        settings.CONVERSATION_RETRY_MAX_SECONDS,
        settings.CONVERSATION_RETRY_BASE_SECONDS * (2 ** max(0, item.attempts - 1)),
    )
    item.status = InboxStatus.FAILED_RETRYABLE if retryable else InboxStatus.DEAD_LETTER
    item.available_at = timezone.now() + timedelta(seconds=delay)
    item.lease_expires_at = None
    item.fencing_token = None
    item.last_error_category = exc.__class__.__name__[:80]
    item.last_error_message = str(exc)[:500]
    item.save()
    MessageEvent.objects.filter(pk=item.message_event_id).update(
        status=MessageStatus.FAILED
    )
    outbox = None
    if not retryable:
        from apps.conversations.application.customer_responses import (
            terminal_processing_message,
        )

        response = ResponsePlan(
            text=terminal_processing_message(
                item.conversation,
                attempts=item.attempts,
            ),
            metadata={
                "processing_failure": True,
                "retry_exhausted": True,
            },
        )
        outbox, _ = ConversationOutboxItem.objects.get_or_create(
            inbox_item=item,
            defaults={
                "conversation": item.conversation,
                "sequence": item.sequence,
                "payload": _serialize_response(response),
                "idempotency_key": f"conversation-response:{item.id}",
            },
        )
        _enqueue_outbox_after_commit(outbox)
        _enqueue_next_inbox_after_commit(item)
    logger.warning(
        "conversation_inbox_failed",
        extra={
            "inbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
            "attempt": item.attempts,
            "status": item.status,
            "error_category": item.last_error_category,
            "terminal_response_created": outbox is not None,
        },
    )
    return item.status


@transaction.atomic
def claim_outbox(item_id):
    now = timezone.now()
    item = (
        ConversationOutboxItem.objects.select_for_update()
        .select_related("conversation", "inbox_item")
        .get(pk=item_id)
    )
    if item.status in OUTBOX_TERMINAL or item.available_at > now:
        return None
    if _outbound_already_recorded(item):
        _mark_outbox_delivered(item, now)
        logger.info(
            "conversation_outbox_already_recorded",
            extra={"outbox_item_id": str(item.id), "sequence": item.sequence},
        )
        return None
    if item.status == OutboxStatus.SENDING:
        if item.lease_expires_at and item.lease_expires_at > now:
            return None
        # The provider may have accepted a request that crashed before local
        # recording. Automatic replay would risk a duplicate user response.
        item.status = OutboxStatus.DEAD_LETTER
        item.last_error_category = "UNCERTAIN_DELIVERY"
        item.last_error_message = (
            "Expired sending lease requires manual reconciliation."
        )
        item.save()
        logger.error(
            "conversation_outbox_uncertain_delivery",
            extra={
                "outbox_item_id": str(item.id),
                "conversation_id": str(item.conversation_id),
                "sequence": item.sequence,
            },
        )
        return None
    blocked = (
        ConversationOutboxItem.objects.filter(
            conversation=item.conversation, sequence__lt=item.sequence
        )
        .exclude(status__in=OUTBOX_TERMINAL)
        .exists()
    )
    if blocked:
        return None
    token = uuid.uuid4()
    item.status = OutboxStatus.SENDING
    item.attempts += 1
    item.fencing_token = token
    item.lease_expires_at = now + timedelta(
        seconds=settings.CONVERSATION_OUTBOX_LEASE_SECONDS
    )
    item.save()
    logger.info(
        "conversation_outbox_claimed",
        extra={
            "outbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
            "attempt": item.attempts,
        },
    )
    return token


def deliver_claimed_outbox(item_id, token):
    item = ConversationOutboxItem.objects.select_related(
        "conversation__channel_identity", "inbox_item"
    ).get(pk=item_id)
    if item.status != OutboxStatus.SENDING or item.fencing_token != token:
        return "STALE"
    response = _deserialize_response(item.payload, item.idempotency_key)
    send_response_plan(
        conversation=item.conversation,
        response=response,
        correlation_id=str(item.inbox_item_id),
    )
    with transaction.atomic():
        locked = ConversationOutboxItem.objects.select_for_update().get(pk=item_id)
        if locked.status != OutboxStatus.SENDING or locked.fencing_token != token:
            return "STALE"
        _mark_outbox_delivered(locked, timezone.now())
        _enqueue_next_outbox_after_commit(locked)
    fallback = _fallback_metadata(item.payload)
    if fallback:
        logger.info(
            "conversation_outbox_fallback_delivered",
            extra={
                "outbox_item_id": str(item.id),
                "conversation_id": str(item.conversation_id),
                "sequence": item.sequence,
                "fallback_for_outbox_id": fallback["fallback_for_outbox_id"],
                "correlation_id": str(item.inbox_item_id),
            },
        )
    logger.info(
        "conversation_outbox_delivered",
        extra={
            "outbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
        },
    )
    return "DELIVERED"


@transaction.atomic
def record_outbox_failure(item_id, token, exc):
    item = ConversationOutboxItem.objects.select_for_update().get(pk=item_id)
    if item.status != OutboxStatus.SENDING or item.fencing_token != token:
        return "STALE"
    decision = FailureClassifier.classify(exc)
    retryable = (
        decision.retryable and item.attempts < settings.CONVERSATION_OUTBOX_MAX_ATTEMPTS
    )
    now = timezone.now()
    fallback = _fallback_metadata(item.payload)
    fallback_created = should_create_fallback(item, decision)
    if retryable:
        delay = conversation_outbox_retry_delay(decision, item.attempts)
        item.status = OutboxStatus.FAILED_RETRYABLE
        item.available_at = now + timedelta(seconds=delay)
    elif fallback_created:
        item.payload = build_text_fallback(item, decision, exc)
        item.status = OutboxStatus.PENDING
        item.available_at = now
        delay = None
    else:
        item.status = OutboxStatus.DEAD_LETTER
        item.available_at = now
        delay = None
    item.lease_expires_at = None
    item.fencing_token = None
    item.last_error_category = decision.category[:80]
    item.last_error_message = str(exc)[:500]
    item.save()
    extra = _outbox_failure_log_fields(item, exc)
    logger.warning("conversation_outbox_failed", extra=extra)
    if retryable:
        if fallback:
            logger.warning("conversation_outbox_fallback_failed", extra=extra)
        logger.info(
            "conversation_outbox_retry_scheduled",
            extra={**extra, "retry_delay_seconds": delay},
        )
    elif fallback:
        logger.error("conversation_outbox_fallback_failed", extra=extra)
        logger.error("conversation_outbox_dead_letter", extra=extra)
    else:
        logger.error("conversation_outbox_permanent_failure", extra=extra)
        logger.error("conversation_outbox_dead_letter", extra=extra)
    if fallback_created:
        fallback_metadata = _fallback_metadata(item.payload)
        logger.warning(
            "conversation_outbox_fallback_created",
            extra={
                **extra,
                "fallback_for_outbox_id": fallback_metadata["fallback_for_outbox_id"],
                "fallback_reason": fallback_metadata["fallback_reason"],
            },
        )
        _enqueue_outbox_after_commit(item)
    return item.status


@transaction.atomic
def replay_inbox_dead_letter(item_id):
    """Operator-controlled replay; prior business work was rolled back atomically."""

    item = ConversationInboxItem.objects.select_for_update().get(pk=item_id)
    if item.status != InboxStatus.DEAD_LETTER:
        return False
    item.status = InboxStatus.PENDING
    item.attempts = 0
    item.available_at = timezone.now()
    item.last_error_category = None
    item.last_error_message = None
    item.save()
    _enqueue_inbox_after_commit(item)
    return True


@transaction.atomic
def replay_outbox_dead_letter(item_id, *, accept_duplicate_risk=False):
    """Replay a delivery DLQ item, requiring opt-in for uncertain deliveries."""

    item = ConversationOutboxItem.objects.select_for_update().get(pk=item_id)
    if item.status != OutboxStatus.DEAD_LETTER:
        return False
    if item.last_error_category == "UNCERTAIN_DELIVERY" and not accept_duplicate_risk:
        return False
    item.status = OutboxStatus.PENDING
    item.attempts = 0
    item.available_at = timezone.now()
    item.last_error_category = None
    item.last_error_message = None
    item.save()
    _enqueue_outbox_after_commit(item)
    return True


def inbox_reconciliation_candidates(limit=None):
    now = timezone.now()
    return ConversationInboxItem.objects.filter(
        Q(status=InboxStatus.PENDING, available_at__lte=now)
        | Q(status=InboxStatus.FAILED_RETRYABLE, available_at__lte=now)
        | Q(status=InboxStatus.PROCESSING, lease_expires_at__lte=now)
    ).order_by("created_at", "id")[
        : limit or settings.CONVERSATION_RECONCILIATION_BATCH_SIZE
    ]


def outbox_reconciliation_candidates(limit=None):
    now = timezone.now()
    return ConversationOutboxItem.objects.filter(
        Q(status=OutboxStatus.PENDING, available_at__lte=now)
        | Q(status=OutboxStatus.FAILED_RETRYABLE, available_at__lte=now)
        | Q(status=OutboxStatus.SENDING, lease_expires_at__lte=now)
    ).order_by("created_at", "id")[
        : limit or settings.CONVERSATION_RECONCILIATION_BATCH_SIZE
    ]


def _mark_outbox_delivered(item, now):
    item.status = OutboxStatus.DELIVERED
    item.delivered_at = now
    item.lease_expires_at = None
    item.fencing_token = None
    item.last_error_category = None
    item.last_error_message = None
    item.save()


def _enqueue_next_outbox_after_commit(item):
    next_item = (
        ConversationOutboxItem.objects.filter(
            conversation=item.conversation, sequence__gt=item.sequence
        )
        .exclude(status__in=OUTBOX_TERMINAL)
        .order_by("sequence")
        .first()
    )
    if next_item is not None:
        _enqueue_outbox_after_commit(next_item)


def _outbound_already_recorded(item):
    return ConversationMessage.objects.filter(
        conversation=item.conversation,
        direction=MessageDirection.OUTBOUND,
        metadata__idempotency_key=item.idempotency_key,
    ).exists()


def _serialize_inbound(message):
    return {
        "channel": str(message.channel),
        "external_message_id": message.external_message_id,
        "external_user_id": message.external_user_id,
        "external_chat_id": message.external_chat_id,
        "message_type": str(message.message_type),
        "text": message.text,
        "location": asdict(message.location) if message.location else None,
        "occurred_at": message.occurred_at.isoformat() if message.occurred_at else None,
        "raw_payload": message.raw_payload,
        "metadata": message.metadata,
    }


def _deserialize_inbound(payload):
    location = (
        MessageLocation(**payload["location"]) if payload.get("location") else None
    )
    occurred_at = (
        datetime.fromisoformat(payload["occurred_at"])
        if payload.get("occurred_at")
        else None
    )
    return NormalizedInboundMessage(
        channel=payload["channel"],
        external_message_id=payload["external_message_id"],
        external_user_id=payload["external_user_id"],
        external_chat_id=payload.get("external_chat_id"),
        message_type=payload["message_type"],
        text=payload.get("text"),
        location=location,
        occurred_at=occurred_at,
        raw_payload=payload.get("raw_payload"),
        metadata=payload.get("metadata") or {},
    )


def _serialize_response(response):
    return {
        "text": response.text,
        "request_location": bool(response.request_location),
        "next_step": (
            {
                "kind": response.next_step.kind.value,
                "instruction": response.next_step.instruction,
            }
            if response.next_step
            else None
        ),
        "metadata": response.metadata,
        "actions": [
            {"action_id": action.action_id, "label": action.label}
            for action in response.actions
        ],
    }


def _deserialize_response(payload, idempotency_key):
    from apps.conversations.application.next_steps import NextStep, NextStepKind

    next_step = payload.get("next_step")
    return ResponsePlan(
        text=payload["text"],
        request_location=bool(payload.get("request_location")),
        next_step=(
            NextStep(
                kind=NextStepKind(next_step["kind"]),
                instruction=next_step["instruction"],
            )
            if next_step
            else None
        ),
        metadata={
            **(payload.get("metadata") or {}),
            "idempotency_key": idempotency_key,
        },
        actions=tuple(ResponseAction(**item) for item in payload.get("actions", ())),
    )


def _meta_error_detail(exc, key):
    response_data = getattr(exc, "response_data", None) or {}
    error = response_data.get("error") if isinstance(response_data, dict) else {}
    return error.get(key) if isinstance(error, dict) else None


def should_create_fallback(item, decision):
    """Only replace a permanently rejected WhatsApp interactive message once."""
    return bool(
        item.conversation.channel == "WHATSAPP"
        and decision.category == "PERMANENT_PAYLOAD"
        and item.payload.get("actions")
        and not _fallback_metadata(item.payload)
        and isinstance(item.payload.get("text"), str)
        and item.payload["text"].strip()
    )


def build_text_fallback(item, decision, exc):
    """Create a text-only replacement at the same ordered Outbox position."""
    original = item.payload
    action_labels = [
        action.get("label", "").strip()
        for action in original.get("actions", ())
        if isinstance(action, dict) and isinstance(action.get("label"), str)
    ]
    next_step = ""
    if action_labels:
        choices = ", ".join(f"« {label} »" for label in action_labels)
        next_step = f"\n\nJe n'ai pas pu afficher les boutons. Répondez {choices}."
    if original.get("request_location"):
        next_step += "\n\nVous pouvez aussi partager votre position WhatsApp."
    return {
        "text": f"{original['text']}{next_step}",
        "actions": [],
        "request_location": False,
        "next_step": original.get("next_step"),
        "metadata": {
            **(original.get("metadata") or {}),
            "fallback": True,
            "fallback_for_outbox_id": str(item.id),
            "fallback_reason": decision.category,
            "fallback_original_response": {
                "text": original["text"],
                "actions": original.get("actions", []),
                "request_location": bool(original.get("request_location")),
                "next_step": original.get("next_step"),
            },
            "fallback_original_failure": _failure_details(decision, exc),
        },
    }


def _fallback_metadata(payload):
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict) or not metadata.get("fallback"):
        return None
    return metadata


def _failure_details(decision, exc):
    return {
        "category": decision.category,
        "exception_class": type(exc).__name__,
        "http_status": getattr(exc, "status_code", None),
        "meta_error_code": _meta_error_detail(exc, "code"),
        "meta_error_subcode": _meta_error_detail(exc, "error_subcode"),
    }


def conversation_outbox_retry_delay(decision, attempts):
    """Return the bounded delay persisted for a retryable outbox delivery."""
    base_delay = settings.CONVERSATION_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1))
    if decision.category == "RATE_LIMIT":
        base_delay *= 2
    if decision.retry_after is not None:
        base_delay = max(base_delay, decision.retry_after)
    return min(settings.CONVERSATION_RETRY_MAX_SECONDS, base_delay)


def _outbox_failure_log_fields(item, exc):
    actions = item.payload.get("actions") or ()
    action_count = len(actions) if isinstance(actions, (list, tuple)) else 0
    return {
        "outbox_item_id": str(item.id),
        "conversation_id": str(item.conversation_id),
        "sequence": item.sequence,
        "attempt": item.attempts,
        "status": item.status,
        "error_category": item.last_error_category,
        "exception_class": type(exc).__name__,
        "http_status": getattr(exc, "status_code", None),
        "meta_error_code": _meta_error_detail(exc, "code"),
        "meta_error_subcode": _meta_error_detail(exc, "error_subcode"),
        "meta_error_type": _meta_error_detail(exc, "type"),
        "meta_error_message": _meta_error_detail(exc, "message"),
        "operation": "messages.create",
        "message_type": "interactive" if action_count else "text",
        "interactive_type": (
            "button" if 1 <= action_count <= 3 else "list" if action_count else None
        ),
        "correlation_id": str(item.inbox_item_id),
    }
