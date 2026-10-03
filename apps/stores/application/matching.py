import logging

from django.db import transaction
from django.utils import timezone

from apps.orders.application.services import (
    fail_order,
    mark_awaiting_partial_coverage,
    start_store_matching,
)
from apps.orders.domain.enums import OrderFailureCode, OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.application.eligibility import StoreEligibilityService
from apps.stores.application.fulfillment import StoreFulfillmentService
from apps.stores.application.offer_notifications import StoreOfferNotificationService
from apps.stores.application.offers import StoreOfferService
from apps.stores.application.ranking import StoreRankingService
from apps.stores.domain.enums import (
    PartialStoreCoverageProposalStatus,
    StoreMatchingAttemptStatus,
)
from apps.stores.infrastructure.models import (
    PartialStoreCoverageProposal,
    Store,
    StoreMatchingAttempt,
    StoreOffer,
)

logger = logging.getLogger(__name__)


class StoreMatchingOrchestrator:
    def __init__(
        self,
        *,
        eligibility=None,
        fulfillment=None,
        ranking=None,
        offers=None,
        notifications=None,
    ):
        self.eligibility = eligibility or StoreEligibilityService()
        self.fulfillment = fulfillment or StoreFulfillmentService()
        self.ranking = ranking or StoreRankingService()
        self.offers = offers or StoreOfferService()
        self.notifications = notifications or StoreOfferNotificationService()

    def start_matching(self, order_id, correlation_id=None):
        order = Order.objects.get(pk=order_id)
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            attempt = (
                StoreMatchingAttempt.objects.select_for_update()
                .filter(order=locked)
                .first()
            )
            if attempt:
                if attempt.status == StoreMatchingAttemptStatus.RUNNING:
                    logger.info(
                        "matching_start_duplicate",
                        extra={
                            "order_id": str(locked.id),
                            "correlation_id": correlation_id,
                        },
                    )
                    return attempt
                # A customer accepted a persisted partial proposal. Its old
                # diagnostic attempt is no longer authoritative.
                attempt.delete()
            if locked.status != OrderStatus.CONFIRMED:
                raise ValueError("Order must be CONFIRMED to start store matching.")
            start_store_matching(locked, correlation_id=correlation_id)
            attempt = StoreMatchingAttempt.objects.create(
                order=locked, strategy_version="pending"
            )
            logger.info(
                "store_matching_started",
                extra={
                    "order_id": str(locked.id),
                    "matching_attempt_id": str(attempt.id),
                    "correlation_id": correlation_id,
                },
            )
        # Eligibility is explicitly defined for a confirmed order.  Keep the
        # pre-transition snapshot for this pure evaluation: reloading here
        # would see MATCHING_STORES and incorrectly produce no candidates.
        eligibility = self.eligibility.find_eligible_stores(order)
        stores = list(
            Store.objects.filter(
                id__in=[item.store_id for item in eligibility.eligible_stores]
            )
        )
        fulfillment = self.fulfillment.evaluate_candidates(order, stores)
        ranked = self.ranking.rank_candidates(
            order, eligibility.eligible_stores, fulfillment
        )
        snapshot = [candidate.snapshot() for candidate in ranked.ranked_candidates]
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            attempt = StoreMatchingAttempt.objects.select_for_update().get(order=locked)
            attempt.strategy_version = str(ranked.strategy_version)
            attempt.candidate_snapshot = snapshot
            if not snapshot:
                # A partial store is a customer proposal only.  It must never
                # enter the StoreOffer path before consent.
                partials = [
                    c
                    for c in ranked.diagnostics
                    if c.coverage_ratio > 0 and not c.fully_covered
                ]
                if partials:
                    selected = partials[
                        0
                    ]  # diagnostics already uses ratio, distance, stable id.
                    store = Store.objects.get(pk=selected.store_id)
                    coverage = self.fulfillment.evaluate_store(locked, store)
                    items = {str(item.id): item for item in locked.items.all()}
                    covered = []
                    uncovered = []
                    for result in coverage.item_results:
                        payload = {
                            "order_item_id": result.order_item_id,
                            "requested_name": items[
                                result.order_item_id
                            ].requested_name,
                        }
                        (covered if result.covered else uncovered).append(payload)
                    proposal_snapshot = {
                        "store_id": str(store.id),
                        "coverage_ratio": str(coverage.coverage_ratio),
                        "covered_items": covered,
                        "uncovered_items": uncovered,
                        "ranking": selected.snapshot(),
                    }
                    PartialStoreCoverageProposal.objects.update_or_create(
                        order=locked,
                        defaults={
                            "matching_attempt": attempt,
                            "store": store,
                            "status": PartialStoreCoverageProposalStatus.PENDING,
                            "proposal_snapshot": proposal_snapshot,
                            "consumed_at": None,
                            "cancelled_at": None,
                        },
                    )
                    mark_awaiting_partial_coverage(
                        locked, correlation_id=correlation_id
                    )
                    attempt.candidate_snapshot = [selected.snapshot()]
                    attempt.status = StoreMatchingAttemptStatus.EXHAUSTED
                    attempt.completed_at = timezone.now()
                    attempt.failure_reason = "AWAITING_PARTIAL_COVERAGE"
                    attempt.save(
                        update_fields=[
                            "strategy_version",
                            "candidate_snapshot",
                            "status",
                            "completed_at",
                            "failure_reason",
                            "updated_at",
                        ]
                    )
                    transaction.on_commit(
                        lambda: self._notify_partial_proposal(locked.id, correlation_id)
                    )
                    return attempt
                attempt.status = StoreMatchingAttemptStatus.EXHAUSTED
                attempt.completed_at = timezone.now()
                attempt.failure_reason = "NO_CANDIDATES"
                attempt.save(
                    update_fields=[
                        "strategy_version",
                        "candidate_snapshot",
                        "status",
                        "completed_at",
                        "failure_reason",
                        "updated_at",
                    ]
                )
                # Empty eligibility/coverage/ranking is a business exhaustion,
                # not a technical matching state left indefinitely RUNNING.
                fail_order(
                    locked,
                    OrderFailureCode.NO_STORE_AVAILABLE,
                    reason="No offerable store candidates",
                    correlation_id=correlation_id,
                )
                return attempt
            candidate = snapshot[0]
            store = Store.objects.get(pk=candidate["store_id"])
            offer = self.offers.create_offer(
                order=locked,
                store=store,
                rank=candidate["rank"],
                strategy_version=attempt.strategy_version,
                selection_snapshot=candidate,
                correlation_id=correlation_id,
            )
            notifications = self.notifications.create_for_offer(
                offer, correlation_id=correlation_id
            )
            if not notifications:
                transaction.on_commit(
                    lambda: self._advance_unreachable(
                        attempt.id, offer.id, correlation_id
                    )
                )
            attempt.current_position = 1
            attempt.save(
                update_fields=[
                    "strategy_version",
                    "candidate_snapshot",
                    "current_position",
                    "updated_at",
                ]
            )
            logger.info(
                "store_candidate_selected",
                extra={
                    "order_id": str(locked.id),
                    "matching_attempt_id": str(attempt.id),
                    "offer_id": str(offer.id),
                    "store_id": str(store.id),
                    "rank": candidate["rank"],
                    "correlation_id": correlation_id,
                },
            )
            return attempt

    @staticmethod
    def _notify_partial_proposal(order_id, correlation_id):
        from apps.stores.tasks import deliver_partial_coverage_proposal

        deliver_partial_coverage_proposal.delay(str(order_id), correlation_id)

    def advance_matching(self, attempt_id, outcome, correlation_id=None, offer_id=None):
        """Advance only after a terminal business outcome; provider failures retry elsewhere."""
        with transaction.atomic():
            attempt = (
                StoreMatchingAttempt.objects.select_for_update()
                .select_related("order")
                .get(pk=attempt_id)
            )
            order = Order.objects.select_for_update().get(pk=attempt.order_id)
            if attempt.status != StoreMatchingAttemptStatus.RUNNING:
                return attempt
            if order.status == OrderStatus.STORE_ACCEPTED:
                attempt.status = StoreMatchingAttemptStatus.SUCCEEDED
                attempt.completed_at = timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"])
                logger.info(
                    "store_matching_completed",
                    extra={
                        "order_id": str(order.id),
                        "matching_attempt_id": str(attempt.id),
                        "correlation_id": correlation_id,
                    },
                )
                return attempt
            if order.status == OrderStatus.CANCELLED:
                StoreOffer.objects.filter(order=order, status="PENDING").update(
                    status="CANCELLED", cancelled_at=timezone.now()
                )
                attempt.status = StoreMatchingAttemptStatus.EXHAUSTED
                attempt.failure_reason = "ORDER_CANCELLED"
                attempt.completed_at = timezone.now()
                attempt.save(
                    update_fields=[
                        "status",
                        "failure_reason",
                        "completed_at",
                        "updated_at",
                    ]
                )
                return attempt
            if outcome not in {
                "REJECTED",
                "EXPIRED",
                "UNREACHABLE",
                "SKIPPED_STALE_CANDIDATE",
            }:
                return attempt
            # An old callback/outcome must never advance the current candidate.
            if offer_id is not None:
                outcome_offer = StoreOffer.objects.filter(
                    pk=offer_id, order=order
                ).first()
                if (
                    outcome_offer is None
                    or outcome_offer.rank != attempt.current_position
                ):
                    return attempt
            next_position = attempt.current_position
            snapshot = attempt.candidate_snapshot or []
            while next_position < len(snapshot):
                candidate = snapshot[next_position]
                next_position += 1
                store = Store.objects.filter(
                    pk=candidate["store_id"], status="ACTIVE", is_open=True
                ).first()
                if store is None:
                    continue
                coverage = self.fulfillment.evaluate_store(order, store)
                if not coverage.fully_covered:
                    continue
                if StoreOffer.objects.filter(order=order, store=store).exists():
                    continue
                offer = self.offers.create_offer(
                    order=order,
                    store=store,
                    rank=candidate["rank"],
                    strategy_version=attempt.strategy_version,
                    selection_snapshot=candidate,
                    correlation_id=correlation_id,
                )
                notifications = self.notifications.create_for_offer(
                    offer, correlation_id=correlation_id
                )
                if not notifications:
                    transaction.on_commit(
                        lambda attempt_id=attempt.id, offer_id=offer.id: (
                            self._advance_unreachable(
                                attempt_id, offer_id, correlation_id
                            )
                        )
                    )
                attempt.current_position = next_position
                attempt.save(update_fields=["current_position", "updated_at"])
                logger.info(
                    "store_matching_advanced",
                    extra={
                        "order_id": str(order.id),
                        "matching_attempt_id": str(attempt.id),
                        "offer_id": str(offer.id),
                        "store_id": str(store.id),
                        "rank": candidate["rank"],
                        "outcome": outcome,
                        "correlation_id": correlation_id,
                    },
                )
                return attempt
            attempt.status = StoreMatchingAttemptStatus.EXHAUSTED
            attempt.completed_at = timezone.now()
            attempt.failure_reason = "NO_CANDIDATES_REMAINING"
            attempt.save(
                update_fields=["status", "completed_at", "failure_reason", "updated_at"]
            )
            logger.info(
                "store_matching_exhausted",
                extra={
                    "order_id": str(order.id),
                    "matching_attempt_id": str(attempt.id),
                    "correlation_id": correlation_id,
                },
            )
            if order.status in {OrderStatus.MATCHING_STORES, OrderStatus.STORE_OFFERED}:
                fail_order(
                    order,
                    OrderFailureCode.NO_STORE_AVAILABLE,
                    reason="All store candidates exhausted",
                    correlation_id=correlation_id,
                )
            return attempt

    def _advance_unreachable(self, attempt_id, offer_id, correlation_id):
        """Recipient absence is an operational outcome, not a provider failure."""
        offer = StoreOffer.objects.filter(pk=offer_id, status="PENDING").first()
        if offer is not None:
            self.offers.cancel_offer(offer)
        self.advance_matching(
            attempt_id, "UNREACHABLE", correlation_id=correlation_id, offer_id=offer_id
        )
