"""Transactional customer consent for a persisted partial-coverage proposal."""
from django.db import transaction
from django.utils import timezone

from apps.audit.application.services import record_audit_event
from apps.audit.domain.enums import AuditActorType, AuditEntityType, AuditEventType
from apps.orders.domain.enums import OrderStatus
from apps.orders.domain.rules import ensure_transition_allowed, ensure_order_items_confirmable
from apps.orders.infrastructure.models import Order, OrderItem
from apps.stores.domain.enums import PartialStoreCoverageProposalStatus
from apps.stores.infrastructure.models import PartialStoreCoverageProposal, StoreMatchingAttempt


class PartialCoverageConsentService:
    def cancel(self, *, order_id, customer, correlation_id=None):
        from apps.orders.application.services import cancel_order
        from apps.orders.domain.enums import OrderCancellationCode
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id, customer=customer)
            proposal = PartialStoreCoverageProposal.objects.select_for_update().filter(order=order).first()
            if not proposal or proposal.status != PartialStoreCoverageProposalStatus.PENDING:
                raise ValueError("This partial coverage proposal is stale.")
            cancel_order(order, OrderCancellationCode.CUSTOMER_REQUEST, actor=customer, correlation_id=correlation_id)
            proposal.status = PartialStoreCoverageProposalStatus.CANCELLED
            proposal.cancelled_at = timezone.now()
            proposal.save(update_fields=["status", "cancelled_at", "updated_at"])
            return order

    def return_to_editing(self, *, order_id, customer, correlation_id=None):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id, customer=customer)
            proposal = PartialStoreCoverageProposal.objects.select_for_update().filter(order=order).first()
            if not proposal or proposal.status != PartialStoreCoverageProposalStatus.PENDING or order.status != OrderStatus.AWAITING_PARTIAL_COVERAGE:
                raise ValueError("This partial coverage proposal is stale.")
            ensure_transition_allowed(order.status, OrderStatus.DRAFT)
            order.status = OrderStatus.DRAFT
            order.save(update_fields=["status", "updated_at"])
            proposal.status = PartialStoreCoverageProposalStatus.CANCELLED
            proposal.cancelled_at = timezone.now()
            proposal.save(update_fields=["status", "cancelled_at", "updated_at"])
            return order

    def accept(self, *, order_id, customer, correlation_id=None):
        with transaction.atomic():
            order = Order.objects.select_for_update().prefetch_related("items").get(pk=order_id)
            if order.customer_id != customer.id:
                raise PermissionError("Only the order customer may accept this proposal.")
            proposal = PartialStoreCoverageProposal.objects.select_for_update().filter(order=order).first()
            if proposal is None or proposal.status != PartialStoreCoverageProposalStatus.PENDING:
                # Replayed callback after a successful accept is harmless.
                if proposal and proposal.status == PartialStoreCoverageProposalStatus.ACCEPTED:
                    return order, True
                raise ValueError("This partial coverage proposal is stale.")
            if order.status != OrderStatus.AWAITING_PARTIAL_COVERAGE:
                raise ValueError("Order is not awaiting a partial coverage decision.")
            uncovered_ids = {row["order_item_id"] for row in proposal.proposal_snapshot.get("uncovered_items", [])}
            rows = list(OrderItem.objects.select_for_update().filter(order=order, id__in=uncovered_ids))
            if len(rows) != len(uncovered_ids):
                raise ValueError("Partial coverage proposal no longer matches this order.")
            removed = [{"order_item_id": str(row.id), "requested_name": row.requested_name} for row in rows]
            OrderItem.objects.filter(id__in=[row.id for row in rows]).delete()
            # Remaining rows are catalog-grounded already; guard the invariant before rematching.
            ensure_order_items_confirmable(order)
            ensure_transition_allowed(order.status, OrderStatus.CONFIRMED)
            previous = order.status
            order.status = OrderStatus.CONFIRMED
            order.save(update_fields=["status", "updated_at"])
            proposal.status = PartialStoreCoverageProposalStatus.ACCEPTED
            proposal.consumed_at = timezone.now()
            proposal.save(update_fields=["status", "consumed_at", "updated_at"])
            StoreMatchingAttempt.objects.filter(order=order).delete()
            record_audit_event(actor=customer, actor_type=AuditActorType.USER, entity_type=AuditEntityType.ORDER, entity_id=order.id,
                event_type=AuditEventType.ORDER_ITEM_REMOVED, previous_state=previous, new_state=OrderStatus.CONFIRMED,
                metadata={"partial_coverage_proposal_id": str(proposal.id), "removed_items": removed}, correlation_id=correlation_id)
            transaction.on_commit(lambda: self._enqueue(order.id, correlation_id))
            return order, False

    @staticmethod
    def _enqueue(order_id, correlation_id):
        from apps.stores.tasks import start_store_matching_for_order
        start_store_matching_for_order.delay(str(order_id), correlation_id)
