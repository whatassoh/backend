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
        .exclude(status=InboxStatus.PROCESSED)
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
    logger.warning(
        "conversation_inbox_failed",
        extra={
            "inbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
            "attempt": item.attempts,
            "status": item.status,
            "error_category": item.last_error_category,
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
    delay = min(
        settings.CONVERSATION_RETRY_MAX_SECONDS,
        settings.CONVERSATION_RETRY_BASE_SECONDS * (2 ** max(0, item.attempts - 1)),
    )
    item.status = (
        OutboxStatus.FAILED_RETRYABLE if retryable else OutboxStatus.DEAD_LETTER
    )
    item.available_at = timezone.now() + timedelta(seconds=delay)
    item.lease_expires_at = None
    item.fencing_token = None
    item.last_error_category = decision.category[:80]
    item.last_error_message = str(exc)[:500]
    item.save()
    logger.warning(
        "conversation_outbox_failed",
        extra={
            "outbox_item_id": str(item.id),
            "conversation_id": str(item.conversation_id),
            "sequence": item.sequence,
            "attempt": item.attempts,
            "status": item.status,
            "error_category": item.last_error_category,
        },
    )
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
        "metadata": response.metadata,
        "actions": [
            {"action_id": action.action_id, "label": action.label}
            for action in response.actions
        ],
    }


def _deserialize_response(payload, idempotency_key):
    return ResponsePlan(
        text=payload["text"],
        request_location=bool(payload.get("request_location")),
        metadata={
            **(payload.get("metadata") or {}),
            "idempotency_key": idempotency_key,
        },
        actions=tuple(ResponseAction(**item) for item in payload.get("actions", ())),
    )
