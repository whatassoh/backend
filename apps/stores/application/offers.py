from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.orders.application.services import mark_store_accepted, mark_store_offered
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus, StoreStatus
from apps.stores.infrastructure.models import Store, StoreOffer


class StoreOfferService:
    def create_offer(self, *, order, store, rank, strategy_version, selection_snapshot, expires_at=None):
        if expires_at is None:
            expires_at = timezone.now() + timedelta(seconds=getattr(settings, "STORE_OFFER_TTL_SECONDS", 600))
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            locked_store = Store.objects.select_for_update().get(pk=store.pk)
            if locked_order.status not in {OrderStatus.MATCHING_STORES, OrderStatus.STORE_OFFERED}:
                raise ValueError("Order is not ready for a store offer.")
            if locked_store.status != StoreStatus.ACTIVE or not locked_store.is_open:
                raise ValueError("Store is not eligible for an offer.")
            offer, created = StoreOffer.objects.get_or_create(
                order=locked_order, store=locked_store,
                defaults={"rank": rank, "strategy_version": strategy_version, "selection_snapshot": selection_snapshot, "expires_at": expires_at},
            )
            if created and locked_order.status == OrderStatus.MATCHING_STORES:
                mark_store_offered(locked_order)
            return offer

    def accept_offer(self, offer):
        with transaction.atomic():
            locked = StoreOffer.objects.select_for_update().select_related("order").get(pk=offer.pk)
            order = Order.objects.select_for_update().get(pk=locked.order_id)
            list(StoreOffer.objects.select_for_update().filter(order=order))
            if locked.status == StoreOfferStatus.ACCEPTED:
                return locked
            if locked.status != StoreOfferStatus.PENDING or locked.expires_at <= timezone.now():
                raise ValueError("Offer is no longer available.")
            if StoreOffer.objects.filter(order=order, status=StoreOfferStatus.ACCEPTED).exists():
                raise ValueError("Order already assigned.")
            locked.status, locked.accepted_at = StoreOfferStatus.ACCEPTED, timezone.now()
            locked.save(update_fields=["status", "accepted_at", "updated_at"])
            mark_store_accepted(order)
            remaining = list(StoreOffer.objects.filter(order=order, status=StoreOfferStatus.PENDING).exclude(pk=locked.pk))
            StoreOffer.objects.filter(pk__in=[item.pk for item in remaining]).update(status=StoreOfferStatus.CANCELLED, cancelled_at=timezone.now())
            from apps.stores.application.offer_notifications import StoreOfferNotificationService
            notifier = StoreOfferNotificationService()
            for pending_offer in remaining:
                notifier.cancel_unsent_for_offer(pending_offer)
            return locked

    def reject_offer(self, offer, *, reason=None):
        return self._close(offer, StoreOfferStatus.REJECTED, "rejected_at", reason)

    def expire_offer(self, offer, *, now=None):
        now = now or timezone.now()
        if offer.expires_at > now:
            raise ValueError("Offer has not expired.")
        return self._close(offer, StoreOfferStatus.EXPIRED, "expired_at")

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
            fields = ["status", timestamp_field, "updated_at"] + (["rejection_reason"] if reason is not None else [])
            locked.save(update_fields=fields)
            from apps.stores.application.offer_notifications import StoreOfferNotificationService
            StoreOfferNotificationService().cancel_unsent_for_offer(locked)
            return locked
