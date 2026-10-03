"""Celery delivery for persistent StoreOffer notifications."""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import InterfaceError, OperationalError
from django.utils import timezone

from apps.messaging.application.failures import FailureClassifier
from apps.messaging.application.orchestration import (
    send_conversation_interactive,
)
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.application.offer_notifications import (
    StoreOfferNotificationRenderer,
    StoreOfferNotificationService,
    retry_delay_seconds,
)
from apps.stores.application.partial_coverage_actions import (
    partial_coverage_actions,
    partial_coverage_text,
)
from apps.stores.domain.enums import StoreOfferNotificationStatus, StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer, StoreOfferNotification
from common.async_tasks import WhatassohTask


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="stores.start_store_matching",
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(OperationalError, InterfaceError),
    retry_backoff=True,
    retry_jitter=True,
)
def start_store_matching_for_order(self, order_id, correlation_id=None):
    """At-least-once-safe entry point; business exhaustion stays in the orchestrator."""
    order = Order.objects.filter(pk=order_id).only("id", "status").first()
    if order is None:
        return {"status": "NOOP", "reason": "ORDER_NOT_FOUND"}
    if order.status != OrderStatus.CONFIRMED:
        return {"status": "NOOP", "reason": f"ORDER_{order.status}"}
    from apps.stores.application.matching import StoreMatchingOrchestrator

    attempt = StoreMatchingOrchestrator().start_matching(
        order.id, correlation_id=correlation_id
    )
    order.refresh_from_db(fields=["status"])
    return {
        "status": "STARTED",
        "attempt_id": str(attempt.id),
        "resulting_status": order.status,
    }


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="stores.deliver_partial_coverage_proposal",
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_partial_coverage_proposal(self, order_id, correlation_id=None):
    """Channel-neutral interactive rendering; no seller is contacted here."""
    from apps.conversations.domain.enums import ConversationStatus
    from apps.conversations.infrastructure.models import Conversation
    from apps.stores.domain.enums import PartialStoreCoverageProposalStatus
    from apps.stores.infrastructure.models import PartialStoreCoverageProposal

    proposal = (
        PartialStoreCoverageProposal.objects.select_related("order", "store")
        .filter(order_id=order_id, status=PartialStoreCoverageProposalStatus.PENDING)
        .first()
    )
    if (
        proposal is None
        or proposal.order.status != OrderStatus.AWAITING_PARTIAL_COVERAGE
    ):
        return {"status": "NOOP"}
    conversation = (
        Conversation.objects.filter(
            user=proposal.order.customer, status=ConversationStatus.ACTIVE
        )
        .order_by("-last_activity_at")
        .first()
    )
    if conversation is None:
        return {"status": "NOOP", "reason": "NO_ACTIVE_CONVERSATION"}
    text = partial_coverage_text(proposal)
    metadata = {
        "partial_coverage_proposal_id": str(proposal.id),
        "actions": partial_coverage_actions(proposal),
    }
    if conversation.channel == "WHATSAPP":
        from apps.whatsapp.application.outbound import (
            send_whatsapp_interactive_reply_buttons,
        )
        from apps.whatsapp.domain.dtos import WhatsAppReplyButton

        result = send_whatsapp_interactive_reply_buttons(
            conversation=conversation,
            body=text,
            buttons=[
                WhatsAppReplyButton(action_id=x["id"], label=x["label"])
                for x in metadata["actions"]
            ],
            metadata=metadata,
        )
    else:
        # Telegram builds its inline keyboard only for INTERACTIVE messages.
        result = send_conversation_interactive(
            conversation=conversation,
            text=text,
            actions=metadata["actions"],
            metadata=metadata,
            correlation_id=correlation_id,
        )
    # Celery uses JSON serialization; ProviderSendResult contains a MessageEvent.
    return {"status": "SENT", "external_message_id": result.external_message_id}


def _dispatch(notification, instruction, correlation_id):
    metadata = {
        "store_offer_id": str(notification.offer_id),
        "actions": list(instruction.actions),
        "delivery_kind": instruction.kind,
        "template_key": str(instruction.template_key or ""),
    }
    if notification.channel == "WHATSAPP":
        from apps.whatsapp.application.outbound import (
            send_whatsapp_interactive_reply_buttons,
            send_whatsapp_template_to_conversation,
        )
        from apps.whatsapp.domain.dtos import WhatsAppReplyButton

        if instruction.kind == "template":
            return send_whatsapp_template_to_conversation(
                conversation=notification.conversation,
                template_key=instruction.template_key,
                body_parameters={
                    "recipient_name": notification.recipient.first_name or "partenaire"
                },
                metadata=metadata,
            )
        buttons = [
            WhatsAppReplyButton(
                action_id=item["id"],
                label="Accepter" if item["action"].endswith("accept") else "Refuser",
            )
            for item in instruction.actions
        ]
        return send_whatsapp_interactive_reply_buttons(
            conversation=notification.conversation,
            body=instruction.text,
            buttons=buttons,
            metadata=metadata,
        )
    actions = [
        {
            "id": item["id"],
            "label": "Accepter" if item["action"].endswith("accept") else "Refuser",
        }
        for item in instruction.actions
    ]
    return send_conversation_interactive(
        conversation=notification.conversation,
        text=instruction.text,
        actions=actions,
        metadata=metadata,
        correlation_id=correlation_id,
    )


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="stores.deliver_store_offer_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_store_offer_notification(self, notification_id, correlation_id=None):
    service = StoreOfferNotificationService()
    attempt = service.claim_attempt(notification_id, correlation_id=correlation_id)
    if attempt is None:
        return {"status": "NOOP"}
    notification = StoreOfferNotification.objects.select_related("conversation").get(
        pk=attempt.notification_id
    )
    try:
        instruction = StoreOfferNotificationRenderer().render(notification)
        result = _dispatch(notification, instruction, correlation_id)
    except Exception as exc:
        classification = FailureClassifier.classify(exc)
        max_attempts = getattr(settings, "STORE_OFFER_NOTIFICATION_MAX_ATTEMPTS", 3)
        retryable = classification.retryable and attempt.attempt_number < max_attempts
        if retryable:
            delay = retry_delay_seconds(
                attempt.attempt_number, classification.retry_after
            )
            service.record_failure(attempt.id, classification, delay_seconds=delay)
            deliver_store_offer_notification.apply_async(
                args=[str(notification_id), correlation_id], countdown=delay
            )
            return {"status": "RETRY", "delay_seconds": delay}
        service.record_failure(attempt.id, classification, delay_seconds=None)
        return {"status": "FAILED", "category": classification.category}
    service.record_success(attempt.id, result)
    return {"status": "SENT"}


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="stores.recover_store_offer_notification",
    acks_late=True,
    reject_on_worker_lost=True,
)
def recover_store_offer_notification(self, notification_id, correlation_id=None):
    """Safe re-enqueue entry point for the future reconciliation sweep."""
    notification = (
        StoreOfferNotification.objects.select_related("offer")
        .filter(pk=notification_id)
        .first()
    )
    if notification is None:
        return {"status": "NOOP"}
    if notification.status == StoreOfferNotificationStatus.SENDING:
        stale_before = timezone.now() - timedelta(
            seconds=getattr(
                settings, "STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS", 900
            )
        )
        active_attempt = (
            notification.attempts.filter(status="STARTED")
            .order_by("-attempt_number")
            .first()
        )
        if active_attempt is None or active_attempt.started_at >= stale_before:
            return {"status": "IN_PROGRESS"}
        # Provider may have accepted the request just before a worker crash.
        # Record the ambiguous terminal outcome instead of blindly resending.
        StoreOfferNotificationService().close_stale_sending(notification.id)
        return {"status": "AMBIGUOUS_DELIVERY"}
    if (
        notification.offer.status != StoreOfferStatus.PENDING
        or notification.offer.expires_at <= timezone.now()
        or StoreOffer.objects.filter(
            order_id=notification.offer.order_id,
            status=StoreOfferStatus.ACCEPTED,
        ).exists()
    ):
        StoreOfferNotificationService().cancel_unsent_for_offer(notification.offer)
        return {"status": "NOOP"}
    if notification.status not in (
        StoreOfferNotificationStatus.PENDING,
        StoreOfferNotificationStatus.FAILED_RETRYABLE,
    ):
        return {"status": "NOOP"}
    deliver_store_offer_notification.delay(str(notification_id), correlation_id)
    return {"status": "ENQUEUED"}


def _expire_store_offer(offer_id, correlation_id=None):
    from django.utils import timezone

    from apps.stores.application.matching import StoreMatchingOrchestrator
    from apps.stores.application.offers import StoreOfferService

    offer = StoreOffer.objects.select_related("order").filter(pk=offer_id).first()
    if (
        offer is None
        or offer.status != StoreOfferStatus.PENDING
        or offer.expires_at > timezone.now()
    ):
        return {"status": "NOOP"}
    try:
        StoreOfferService().expire_offer(offer, correlation_id=correlation_id)
    except ValueError:
        return {"status": "NOOP"}
    attempt = getattr(offer.order, "store_matching_attempt", None)
    if attempt:
        StoreMatchingOrchestrator().advance_matching(
            attempt.id, "EXPIRED", correlation_id=correlation_id, offer_id=offer.id
        )
    return {"status": "EXPIRED"}


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="stores.expire_store_offer",
    acks_late=True,
    reject_on_worker_lost=True,
)
def expire_store_offer(self, offer_id, correlation_id=None):
    return _expire_store_offer(offer_id, correlation_id)


@shared_task(
    bind=True,
    base=WhatassohTask,
    name="stores.sweep_expired_store_offers",
    acks_late=True,
    reject_on_worker_lost=True,
)
def sweep_expired_store_offers(self, correlation_id=None):
    from django.utils import timezone

    limit = settings.OFFER_EXPIRY_SWEEP_BATCH_SIZE
    ids = list(
        StoreOffer.objects.filter(
            status=StoreOfferStatus.PENDING, expires_at__lte=timezone.now()
        )
        .order_by("expires_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    results = [_expire_store_offer(offer_id, correlation_id) for offer_id in ids]
    return {
        "found": len(ids),
        "expired": sum(item["status"] == "EXPIRED" for item in results),
    }
