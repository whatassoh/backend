"""Database-driven recovery for sequential driver dispatch."""
from django.db import transaction
from django.utils import timezone

from apps.drivers.application.dispatch import DriverMatchingOrchestrator
from apps.drivers.application.offer_notifications import DriverOfferNotificationService
from apps.drivers.application.offers import DriverOfferService
from apps.drivers.domain.enums import DriverMatchingAttemptStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverMatchingAttempt, DriverOffer
from apps.orders.domain.enums import OrderStatus


class DriverMatchingReconciliationService:
    def reconcile_attempt(self, attempt_id, *, correlation_id=None):
        with transaction.atomic():
            attempt = DriverMatchingAttempt.objects.select_for_update().select_related("order").get(pk=attempt_id)
            order = attempt.order
            if attempt.status != DriverMatchingAttemptStatus.RUNNING:
                return "NOOP"
            pending = DriverOffer.objects.select_for_update().filter(order=order, status=DriverOfferStatus.PENDING).first()
            if order.status == OrderStatus.CANCELLED:
                if pending:
                    DriverOfferService().cancel_offer(pending)
                attempt.status, attempt.completed_at = DriverMatchingAttemptStatus.CANCELLED, timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"])
                return "CANCELLED"
            if order.status == OrderStatus.DRIVER_ASSIGNED:
                attempt.status, attempt.completed_at = DriverMatchingAttemptStatus.SUCCEEDED, timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"])
                return "SUCCEEDED"
            if pending and pending.expires_at > timezone.now():
                notification = DriverOfferNotificationService().create_for_offer(pending, correlation_id=correlation_id)
                return "REENQUEUED" if notification else "UNREACHABLE"
        if pending and pending.expires_at <= timezone.now():
            try:
                DriverOfferService().expire_offer(pending)
            except ValueError:
                return "NOOP"
        DriverMatchingOrchestrator().advance_matching(attempt_id, correlation_id=correlation_id)
        return "ADVANCED"

    def reconcile_running(self, *, limit=100, correlation_id=None):
        ids = DriverMatchingAttempt.objects.filter(status=DriverMatchingAttemptStatus.RUNNING).order_by("updated_at", "id").values_list("id", flat=True)[:limit]
        return tuple(self.reconcile_attempt(attempt_id, correlation_id=correlation_id) for attempt_id in ids)
