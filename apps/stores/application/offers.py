import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.orders.application.services import mark_store_accepted, mark_store_offered
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import (
    StoreMatchingAttemptStatus,
    StoreOfferStatus,
    StoreStatus,
)
from apps.stores.infrastructure.models import Store, StoreMatchingAttempt, StoreOffer

logger = logging.getLogger(__name__)


class StoreOfferService:
    def create_offer(
        self,
        *,
        order,
        store,
        rank,
        strategy_version,
        selection_snapshot,
        expires_at=None,
        correlation_id=None,
    ):
        if expires_at is None:
            expires_at = timezone.now() + timedelta(
                seconds=getattr(settings, "STORE_OFFER_TTL_SECONDS", 600)
            )
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            locked_store = Store.objects.select_for_update().get(pk=store.pk)
            if locked_order.status not in {
                OrderStatus.MATCHING_STORES,
                OrderStatus.STORE_OFFERED,
            }:
                raise ValueError("Order is not ready for a store offer.")
            if locked_store.status != StoreStatus.ACTIVE or not locked_store.is_open:
                raise ValueError("Store is not eligible for an offer.")
            offer, created = StoreOffer.objects.get_or_create(
                order=locked_order,
                store=locked_store,
                defaults={
                    "rank": rank,
                    "strategy_version": strategy_version,
                    "selection_snapshot": selection_snapshot,
                    "expires_at": expires_at,
                },
            )
            if created and locked_order.status == OrderStatus.MATCHING_STORES:
                mark_store_offered(locked_order)
        if created:
            from apps.stores.tasks import expire_store_offer
            from common.deadlines import schedule_deadline

            schedule_deadline(
                task=expire_store_offer,
                entity_id=offer.id,
                expires_at=offer.expires_at,
                task_name="stores.expire_store_offer",
            )
            logger.info(
                "store_offer_created",
                extra={
                    "order_id": str(offer.order_id),
                    "offer_id": str(offer.id),
                    "store_id": str(offer.store_id),
                    "rank": offer.rank,
                    "correlation_id": correlation_id,
                },
            )
        return offer

    def accept_offer(self, offer, *, correlation_id=None):
        with transaction.atomic():
            # Serialize competing stores on the Order first, then lock all its
            # offers in a stable order.  Locking each selected offer first can
            # deadlock when two stores accept different offers concurrently.
            reference = StoreOffer.objects.only("order_id").get(pk=offer.pk)
            order = Order.objects.select_for_update().get(pk=reference.order_id)
            locked_offers = list(
                StoreOffer.objects.select_for_update()
                .filter(order=order)
                .order_by("id")
            )
            locked = next(item for item in locked_offers if item.pk == offer.pk)
            if locked.status == StoreOfferStatus.ACCEPTED:
                return locked
            if (
                locked.status != StoreOfferStatus.PENDING
                or locked.expires_at <= timezone.now()
            ):
                raise ValueError("Offer is no longer available.")
            if any(item.status == StoreOfferStatus.ACCEPTED for item in locked_offers):
                raise ValueError("Order already assigned.")
            locked.status, locked.accepted_at = (
                StoreOfferStatus.ACCEPTED,
                timezone.now(),
            )
            locked.save(update_fields=["status", "accepted_at", "updated_at"])
            mark_store_accepted(order)
            attempt = (
                StoreMatchingAttempt.objects.select_for_update()
                .filter(order=order)
                .first()
            )
            if (
                attempt is not None
                and attempt.status == StoreMatchingAttemptStatus.RUNNING
            ):
                attempt.status = StoreMatchingAttemptStatus.SUCCEEDED
                attempt.completed_at = timezone.now()
                attempt.save(update_fields=["status", "completed_at", "updated_at"])
                logger.info(
                    "store_matching_completed",
                    extra={
                        "order_id": str(order.id),
                        "matching_attempt_id": str(attempt.id),
                        "offer_id": str(locked.id),
                        "store_id": str(locked.store_id),
                        "rank": locked.rank,
                        "correlation_id": correlation_id,
                    },
                )
            from apps.orders.application.customer_notifications import (
                notify_store_accepted,
            )

            transaction.on_commit(
                lambda: notify_store_accepted(
                    order_id=order.id, correlation_id=correlation_id
                )
            )
            remaining = [
                item
                for item in locked_offers
                if item.pk != locked.pk and item.status == StoreOfferStatus.PENDING
            ]
            StoreOffer.objects.filter(pk__in=[item.pk for item in remaining]).update(
                status=StoreOfferStatus.CANCELLED, cancelled_at=timezone.now()
            )
            from apps.stores.application.offer_notifications import (
                StoreOfferNotificationService,
            )

            notifier = StoreOfferNotificationService()
            for pending_offer in remaining:
                notifier.cancel_unsent_for_offer(pending_offer)
            logger.info(
                "store_offer_accepted",
                extra={
                    "order_id": str(order.id),
                    "matching_attempt_id": str(attempt.id) if attempt else None,
                    "offer_id": str(locked.id),
                    "store_id": str(locked.store_id),
                    "rank": locked.rank,
                    "correlation_id": correlation_id,
                },
            )
            return locked

    def reject_offer(self, offer, *, reason=None, correlation_id=None):
        closed = self._close(offer, StoreOfferStatus.REJECTED, "rejected_at", reason)
        logger.info(
            "store_offer_rejected",
            extra={
                "order_id": str(closed.order_id),
                "offer_id": str(closed.id),
                "store_id": str(closed.store_id),
                "rank": closed.rank,
                "correlation_id": correlation_id,
            },
        )
        return closed

    def expire_offer(self, offer, *, now=None, correlation_id=None):
        now = now or timezone.now()
        if offer.expires_at > now:
            raise ValueError("Offer has not expired.")
        closed = self._close(offer, StoreOfferStatus.EXPIRED, "expired_at")
        logger.info(
            "store_offer_expired",
            extra={
                "order_id": str(closed.order_id),
                "offer_id": str(closed.id),
                "store_id": str(closed.store_id),
                "rank": closed.rank,
                "correlation_id": correlation_id,
            },
        )
        return closed

    def cancel_offer(self, offer):
        return self._close(offer, StoreOfferStatus.CANCELLED, "cancelled_at")

    @staticmethod
    def _close(offer, status, timestamp_field, reason=None):
        with transaction.atomic():
            locked = StoreOffer.objects.select_for_update().get(pk=offer.pk)
            if locked.status == status:
                return locked
            if locked.status != StoreOfferStatus.PENDING:
                raise ValueError("Offer cannot transition from its current status.")
            locked.status = status
            setattr(locked, timestamp_field, timezone.now())
            if reason is not None:
                locked.rejection_reason = reason
            fields = ["status", timestamp_field, "updated_at"] + (
                ["rejection_reason"] if reason is not None else []
            )
            locked.save(update_fields=fields)
            from apps.stores.application.offer_notifications import (
                StoreOfferNotificationService,
            )

            StoreOfferNotificationService().cancel_unsent_for_offer(locked)
            return locked
