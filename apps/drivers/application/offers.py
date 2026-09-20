"""Transactional DriverOffer lifecycle; no provider/network work belongs here."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.application.services import record_audit_event
from apps.audit.domain.enums import AuditActorType, AuditEntityType, AuditEventType
from apps.drivers.application.availability import (
    DriverAvailabilityService,
    DriverMatchability,
)
from apps.drivers.domain.enums import DriverOfferStatus
from apps.drivers.domain.events import DriverAssigned
from apps.drivers.infrastructure.models import (
    DriverAssignment,
    DriverOffer,
    DriverProfile,
)
from apps.orders.application.services import assign_driver
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from common.events import publish

logger = logging.getLogger(__name__)


class DriverOfferService:
    """Lock order → offer(s) → driver.  All callers use that order."""

    def __init__(self, *, availability_service=None):
        self.availability_service = availability_service or DriverAvailabilityService()

    def create_offer(self, *, order, driver, rank, strategy_version, candidate_snapshot, expires_at=None):
        expires_at = expires_at or timezone.now() + timedelta(seconds=settings.DRIVER_OFFER_TTL_SECONDS)
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            locked_driver = DriverProfile.objects.select_for_update().get(pk=driver.pk)
            if locked_order.status != OrderStatus.MATCHING_DRIVERS:
                raise ValueError("Order is not ready for a driver offer.")
            if expires_at <= timezone.now() or self.availability_service.get_matchability(locked_driver).status != DriverMatchability.MATCHABLE:
                raise ValueError("Driver is not eligible for an offer.")
            existing = DriverOffer.objects.filter(order=locked_order, driver=locked_driver).first()
            if existing:
                return existing
            try:
                snapshot = {
                    key: candidate_snapshot[key]
                    for key in ("distance_meters", "matchability", "strategy_version", "metadata")
                    if key in candidate_snapshot
                }
                created = DriverOffer.objects.create(order=locked_order, driver=locked_driver, rank=rank, strategy_version=strategy_version, candidate_snapshot=snapshot, expires_at=expires_at)
                record_audit_event(entity_type=AuditEntityType.DRIVER_OFFER, entity_id=created.id, event_type=AuditEventType.DRIVER_OFFER_CREATED, metadata={"order_id": str(order.id), "driver_id": str(driver.id), "rank": rank, "strategy_version": strategy_version})
                from apps.drivers.tasks import expire_driver_offer
                from common.deadlines import schedule_deadline
                schedule_deadline(task=expire_driver_offer, entity_id=created.id, expires_at=created.expires_at, task_name="drivers.expire_driver_offer")
                return created
            except IntegrityError as exc:
                raise ValueError("An actionable driver offer already exists.") from exc

    def accept_offer(self, offer, *, actor, correlation_id=None, now=None):
        now = now or timezone.now()
        with transaction.atomic():
            # Lock order first even when offer lookup starts from a callback.
            offer_row = DriverOffer.objects.select_related("order").get(pk=offer.pk)
            order = Order.objects.select_for_update().get(pk=offer_row.order_id)
            locked = DriverOffer.objects.select_for_update().select_related("driver").get(pk=offer.pk)
            list(DriverOffer.objects.select_for_update().filter(order=order))
            driver = DriverProfile.objects.select_for_update().get(pk=locked.driver_id)
            if actor is None or actor.id != driver.user_id:
                raise PermissionError("Only the offered driver may accept.")
            if locked.status == DriverOfferStatus.ACCEPTED:
                return locked
            if locked.status != DriverOfferStatus.PENDING:
                raise ValueError("Offer is no longer actionable.")
            if now >= locked.expires_at:
                locked.status, locked.expired_at = DriverOfferStatus.EXPIRED, now
                locked.save(update_fields=["status", "expired_at", "updated_at"])
                raise ValueError("OFFER_EXPIRED")
            if order.status != OrderStatus.MATCHING_DRIVERS:
                self._cancel_locked(locked, now)
                raise ValueError("Order is no longer assignable.")
            if self.availability_service.get_matchability(driver, now=now).status != DriverMatchability.MATCHABLE:
                self._cancel_locked(locked, now)
                raise ValueError("Driver is no longer matchable.")
            if DriverAssignment.objects.filter(driver=driver, is_active=True).exists():
                self._cancel_locked(locked, now)
                raise ValueError("Driver already has an active delivery.")
            try:
                assignment = DriverAssignment.objects.create(order=order, driver=driver, offer=locked)
            except IntegrityError as exc:
                raise ValueError("Order or driver is already assigned.") from exc
            locked.status, locked.accepted_at = DriverOfferStatus.ACCEPTED, now
            locked.save(update_fields=["status", "accepted_at", "updated_at"])
            DriverOffer.objects.filter(order=order, status=DriverOfferStatus.PENDING).exclude(pk=locked.pk).update(status=DriverOfferStatus.CANCELLED, cancelled_at=now)
            assign_driver(order, actor=actor, correlation_id=correlation_id)
            transaction.on_commit(
                lambda: publish(
                    DriverAssigned(
                        assignment_id=assignment.id,
                        order_id=order.id,
                        driver_id=driver.id,
                        correlation_id=correlation_id,
                    )
                )
            )
            record_audit_event(actor=actor, actor_type=AuditActorType.USER, entity_type=AuditEntityType.DRIVER_OFFER, entity_id=locked.id, event_type=AuditEventType.DRIVER_OFFER_ACCEPTED, previous_state=DriverOfferStatus.PENDING, new_state=DriverOfferStatus.ACCEPTED, metadata={"order_id": str(order.id), "driver_id": str(driver.id), "rank": locked.rank, "strategy_version": locked.strategy_version}, correlation_id=correlation_id)
            logger.info("driver_offer_accepted", extra={"offer_id": str(locked.id), "order_id": str(order.id), "driver_id": str(driver.id), "correlation_id": correlation_id})
            return locked

    def reject_offer(self, offer, *, actor, reason=None, now=None):
        return self._close(offer, DriverOfferStatus.REJECTED, "rejected_at", actor=actor, reason=reason, now=now)

    def expire_offer(self, offer, *, now=None):
        now = now or timezone.now()
        if now < offer.expires_at:
            raise ValueError("Offer has not expired.")
        return self._close(offer, DriverOfferStatus.EXPIRED, "expired_at", now=now)

    def cancel_offer(self, offer, *, now=None):
        return self._close(offer, DriverOfferStatus.CANCELLED, "cancelled_at", now=now)

    def _close(self, offer, target, field, *, actor=None, reason=None, now=None):
        now = now or timezone.now()
        with transaction.atomic():
            locked = DriverOffer.objects.select_for_update().get(pk=offer.pk)
            if locked.status == target:
                return locked
            if locked.status != DriverOfferStatus.PENDING:
                raise ValueError("Offer is no longer actionable.")
            if actor is not None and actor.id != locked.driver.user_id:
                raise PermissionError("Only the offered driver may reject.")
            locked.status = target; setattr(locked, field, now)
            if reason is not None: locked.rejection_reason = reason
            fields = ["status", field, "updated_at"] + (["rejection_reason"] if reason is not None else [])
            locked.save(update_fields=fields)
            event_type = {DriverOfferStatus.REJECTED: AuditEventType.DRIVER_OFFER_REJECTED, DriverOfferStatus.EXPIRED: AuditEventType.DRIVER_OFFER_EXPIRED, DriverOfferStatus.CANCELLED: AuditEventType.DRIVER_OFFER_CANCELLED}[target]
            record_audit_event(actor=actor, actor_type=AuditActorType.USER if actor else AuditActorType.SYSTEM, entity_type=AuditEntityType.DRIVER_OFFER, entity_id=locked.id, event_type=event_type, previous_state=DriverOfferStatus.PENDING, new_state=target, metadata={"order_id": str(locked.order_id), "driver_id": str(locked.driver_id), "rank": locked.rank, "strategy_version": locked.strategy_version})
            logger.info("driver_offer_transition", extra={"offer_id": str(locked.id), "status": target})
            return locked

    @staticmethod
    def _cancel_locked(offer, now):
        offer.status, offer.cancelled_at = DriverOfferStatus.CANCELLED, now
        offer.save(update_fields=["status", "cancelled_at", "updated_at"])
