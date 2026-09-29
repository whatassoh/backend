"""Persistent sequential dispatch. Provider delivery remains an outer boundary."""

from django.db import transaction
from django.utils import timezone

from apps.drivers.application.matching import DriverMatchingService, DriverMatchingStatus
from apps.drivers.application.offers import DriverOfferService
from apps.drivers.application.selectors import get_driver_by_id
from apps.drivers.domain.enums import DriverMatchingAttemptStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverMatchingAttempt, DriverOffer
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.enums import OrderFailureCode
from apps.orders.application.services import fail_order, start_driver_matching
from apps.orders.infrastructure.models import Order


class DriverMatchingOrchestrator:
    """One active persisted SEQUENTIAL attempt per order; no network under locks."""
    def __init__(self, *, matching_service=None, offer_service=None):
        self.matching_service = matching_service or DriverMatchingService()
        self.offer_service = offer_service or DriverOfferService()

    def start_matching(self, order_id, *, correlation_id=None):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            attempt, created = DriverMatchingAttempt.objects.select_for_update().get_or_create(
                order=order, defaults={"strategy_version": self.matching_service.strategy_version}
            )
            resumable = (
                not created
                and attempt.status == DriverMatchingAttemptStatus.RUNNING
                and order.status == OrderStatus.MATCHING_DRIVERS
                and not attempt.candidate_snapshot
                and not DriverOffer.objects.filter(order=order).exists()
            )
            if not created and not resumable:
                return attempt
            if created and order.status != OrderStatus.READY_FOR_PICKUP:
                attempt.status = DriverMatchingAttemptStatus.CANCELLED
                attempt.completed_at = timezone.now(); attempt.save(update_fields=["status", "completed_at", "updated_at"])
                return attempt
            if created:
                start_driver_matching(order, correlation_id=correlation_id)
        result = self.matching_service.find_candidates(order)
        with transaction.atomic():
            attempt = DriverMatchingAttempt.objects.select_for_update().get(pk=attempt.pk)
            if result.status == DriverMatchingStatus.TECHNICAL_UNAVAILABLE:
                attempt.status = DriverMatchingAttemptStatus.FAILED_TECHNICAL
                attempt.metadata = {"reason": "geo_unavailable"}
                attempt.completed_at = timezone.now(); attempt.save(update_fields=["status", "metadata", "completed_at", "updated_at"])
                return attempt
            if result.status != DriverMatchingStatus.OK:
                attempt.status = DriverMatchingAttemptStatus.EXHAUSTED
                attempt.completed_at = timezone.now(); attempt.save(update_fields=["status", "completed_at", "updated_at"])
                return attempt
            attempt.candidate_snapshot = [{"driver_id": item.driver_id, "rank": item.rank, "distance_meters": item.distance_meters, "strategy_version": result.strategy_version} for item in result.candidates]
            attempt.save(update_fields=["candidate_snapshot", "updated_at"])
        offer = self.advance_matching(attempt.id, correlation_id=correlation_id)
        if offer is not None:
            from apps.drivers.application.offer_notifications import DriverOfferNotificationService
            if DriverOfferNotificationService().create_for_offer(offer, correlation_id=correlation_id) is None:
                self.offer_service.cancel_offer(offer)
                self.advance_matching(attempt.id, correlation_id=correlation_id)
        return DriverMatchingAttempt.objects.get(pk=attempt.pk)

    def advance_matching(self, attempt_id, *, correlation_id=None):
        with transaction.atomic():
            attempt = DriverMatchingAttempt.objects.select_for_update().select_related("order").get(pk=attempt_id)
            if attempt.status != DriverMatchingAttemptStatus.RUNNING:
                return None
            if attempt.order.status == OrderStatus.CANCELLED:
                attempt.status = DriverMatchingAttemptStatus.CANCELLED; attempt.completed_at = timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"]); return None
            pending = DriverOffer.objects.filter(order=attempt.order, status=DriverOfferStatus.PENDING).first()
            if pending:
                return pending
            while attempt.current_position < len(attempt.candidate_snapshot):
                candidate = attempt.candidate_snapshot[attempt.current_position]
                attempt.current_position += 1
                attempt.save(update_fields=["current_position", "updated_at"])
                driver = get_driver_by_id(candidate["driver_id"])
                if driver is None:
                    continue
                try:
                    return self.offer_service.create_offer(order=attempt.order, driver=driver, rank=candidate["rank"], strategy_version=candidate["strategy_version"], candidate_snapshot=candidate)
                except ValueError:
                    continue
            attempt.status = DriverMatchingAttemptStatus.EXHAUSTED; attempt.completed_at = timezone.now()
            attempt.save(update_fields=["status", "completed_at", "updated_at"])
            fail_order(attempt.order, OrderFailureCode.NO_DRIVER_AVAILABLE, correlation_id=correlation_id)
            return None

    def handle_reject(self, offer, *, actor, correlation_id=None):
        offer = self.offer_service.reject_offer(offer, actor=actor)
        attempt = DriverMatchingAttempt.objects.get(order=offer.order)
        next_offer = self.advance_matching(attempt.id, correlation_id=correlation_id)
        if next_offer is not None:
            from apps.drivers.application.offer_notifications import DriverOfferNotificationService
            if DriverOfferNotificationService().create_for_offer(next_offer, correlation_id=correlation_id) is None:
                self.offer_service.cancel_offer(next_offer)
                self.advance_matching(attempt.id, correlation_id=correlation_id)
        return next_offer

    def handle_accept(self, offer, *, actor, correlation_id=None):
        accepted = self.offer_service.accept_offer(offer, actor=actor, correlation_id=correlation_id)
        with transaction.atomic():
            attempt = DriverMatchingAttempt.objects.select_for_update().get(order=accepted.order)
            if attempt.status == DriverMatchingAttemptStatus.RUNNING:
                attempt.status = DriverMatchingAttemptStatus.SUCCEEDED
                attempt.completed_at = timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"])
        return accepted
