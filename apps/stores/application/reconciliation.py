"""Database-driven convergence for interrupted sequential store matching."""
import logging
from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.orders.domain.enums import OrderStatus
from apps.stores.application.matching import StoreMatchingOrchestrator
from apps.stores.application.offer_notifications import StoreOfferNotificationService
from apps.stores.application.offers import StoreOfferService
from apps.stores.domain.enums import StoreMatchingAttemptStatus, StoreOfferStatus
from apps.stores.infrastructure.models import StoreMatchingAttempt, StoreOffer

logger = logging.getLogger(__name__)


class ReconciliationResult(StrEnum):
    NOOP = "NOOP"
    RECOVERED = "RECOVERED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"


@dataclass(frozen=True, kw_only=True)
class ReconciliationDecision:
    result: ReconciliationResult
    detail: str


class StoreMatchingReconciliationService:
    """Repairs only states whose next action is unambiguous from PostgreSQL."""

    def __init__(self, *, matching=None, offers=None, notifications=None):
        self.matching = matching or StoreMatchingOrchestrator()
        self.offers = offers or StoreOfferService()
        self.notifications = notifications or StoreOfferNotificationService()

    def reconcile_attempt(self, attempt_id, *, correlation_id=None) -> ReconciliationDecision:
        with transaction.atomic():
            attempt = StoreMatchingAttempt.objects.select_for_update().select_related("order").get(pk=attempt_id)
            order = attempt.order
            offers = list(StoreOffer.objects.select_for_update().filter(order=order).order_by("rank", "id"))
            accepted = [offer for offer in offers if offer.status == StoreOfferStatus.ACCEPTED]
            if len(accepted) > 1:
                logger.critical("matching_invariant_violation", extra={"attempt_id": str(attempt.id), "kind": "multiple_accepted"})
                return ReconciliationDecision(result=ReconciliationResult.INVARIANT_VIOLATION, detail="multiple_accepted_offers")
            if attempt.status == StoreMatchingAttemptStatus.SUCCEEDED:
                if order.status != OrderStatus.STORE_ACCEPTED or len(accepted) != 1:
                    logger.critical("matching_invariant_violation", extra={"attempt_id": str(attempt.id), "kind": "succeeded_mismatch"})
                    return ReconciliationDecision(result=ReconciliationResult.INVARIANT_VIOLATION, detail="succeeded_mismatch")
                return ReconciliationDecision(result=ReconciliationResult.NOOP, detail="terminal_success")
            if order.status == OrderStatus.STORE_ACCEPTED:
                if len(accepted) != 1:
                    logger.critical("matching_invariant_violation", extra={"attempt_id": str(attempt.id), "kind": "accepted_order_without_winner"})
                    return ReconciliationDecision(result=ReconciliationResult.INVARIANT_VIOLATION, detail="accepted_order_without_winner")
                attempt.status = StoreMatchingAttemptStatus.SUCCEEDED
                attempt.completed_at = timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"])
                return ReconciliationDecision(result=ReconciliationResult.RECOVERED, detail="marked_succeeded")
            if order.status == OrderStatus.CANCELLED:
                for offer in offers:
                    if offer.status == StoreOfferStatus.PENDING:
                        self.offers.cancel_offer(offer)
                # No matching CANCELLED enum exists; EXHAUSTED is the existing
                # terminal storage state, with an explicit cancellation reason.
                attempt.status = StoreMatchingAttemptStatus.EXHAUSTED
                attempt.failure_reason = "ORDER_CANCELLED"
                attempt.completed_at = timezone.now()
                attempt.save(update_fields=["status", "failure_reason", "completed_at", "updated_at"])
                return ReconciliationDecision(result=ReconciliationResult.RECOVERED, detail="cancelled_order_closed")
            if attempt.status != StoreMatchingAttemptStatus.RUNNING:
                return ReconciliationDecision(result=ReconciliationResult.NOOP, detail="terminal_attempt")
            pending = [offer for offer in offers if offer.status == StoreOfferStatus.PENDING]
            if len(pending) > 1:
                logger.critical("matching_invariant_violation", extra={"attempt_id": str(attempt.id), "kind": "multiple_pending_sequential"})
                return ReconciliationDecision(result=ReconciliationResult.INVARIANT_VIOLATION, detail="multiple_pending_offers")
            if pending:
                offer = pending[0]
                if offer.expires_at <= timezone.now():
                    # Lifecycle service serializes accept-vs-expire.  The next
                    # step is outside this transaction to avoid nested fallback.
                    pass
                else:
                    notifications = self.notifications.create_for_offer(offer, correlation_id=correlation_id)
                    if notifications:
                        return ReconciliationDecision(result=ReconciliationResult.RECOVERED, detail="notification_reenqueued")
                    return ReconciliationDecision(result=ReconciliationResult.NOOP, detail="pending_offer_no_recipient")
            elif order.status == OrderStatus.MATCHING_STORES and attempt.candidate_snapshot:
                # No offer was persisted; advance uses current_position and the
                # snapshot to select the first/next valid candidate.
                pass
            else:
                return ReconciliationDecision(result=ReconciliationResult.NOOP, detail="nothing_actionable")

        # Do state transitions after releasing reconciliation locks.
        if pending and pending[0].expires_at <= timezone.now():
            try:
                self.offers.expire_offer(pending[0])
            except ValueError:
                return ReconciliationDecision(result=ReconciliationResult.NOOP, detail="offer_resolved_concurrently")
            self.matching.advance_matching(attempt_id, "EXPIRED", correlation_id=correlation_id, offer_id=pending[0].id)
            return ReconciliationDecision(result=ReconciliationResult.RECOVERED, detail="expired_offer_advanced")
        self.matching.advance_matching(attempt_id, "SKIPPED_STALE_CANDIDATE", correlation_id=correlation_id)
        return ReconciliationDecision(result=ReconciliationResult.RECOVERED, detail="stuck_matching_advanced")

    def reconcile_stuck_attempts(self, *, limit=None, correlation_id=None):
        limit = limit or getattr(settings, "STORE_MATCHING_RECONCILIATION_BATCH_SIZE", 100)
        ids = list(StoreMatchingAttempt.objects.filter(status=StoreMatchingAttemptStatus.RUNNING).order_by("updated_at", "id").values_list("id", flat=True)[:limit])
        return tuple(self.reconcile_attempt(attempt_id, correlation_id=correlation_id) for attempt_id in ids)
