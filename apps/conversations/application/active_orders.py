"""The conversation-scoped, safe entry point for customer order drafts.

An ``Order`` remains the business source of truth.  The JSON context is only a
pointer and is consequently validated on every read.
"""

import logging
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from django.db import transaction

from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
)
from apps.conversations.domain.enums import ConversationStatus
from apps.conversations.infrastructure.models import Conversation
from apps.orders.application.services import cancel_order, create_order_draft
from apps.orders.domain.enums import OrderCancellationCode
from apps.orders.domain.rules import BUILDING_ORDER_STATUSES
from apps.orders.infrastructure.models import Order

logger = logging.getLogger(__name__)


class ActiveOrderDecisionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CREATED = "CREATED"
    NO_ACTIVE_ORDER = "NO_ACTIVE_ORDER"
    STALE_CONTEXT = "STALE_CONTEXT"
    CLOSED_CONVERSATION = "CLOSED_CONVERSATION"
    REQUIRES_USER_CHOICE = "REQUIRES_USER_CHOICE"
    RESUMED = "RESUMED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, kw_only=True)
class ActiveOrderDecision:
    status: ActiveOrderDecisionStatus
    order: Order | None = None
    reason: str | None = None
    stale_context_detected: bool = False
    requires_user_choice: bool = False

    @property
    def order_id(self) -> str | None:
        return str(self.order.id) if self.order else None


class ActiveCustomerOrderService:
    """Resolves the one building order per user; context is only a cache."""

    active_statuses = frozenset(BUILDING_ORDER_STATUSES)

    def _trace(self, event: str, conversation, order=None, correlation_id=None, **data):
        logger.info(
            event,
            extra={
                "conversation_id": str(conversation.id),
                "order_id": str(order.id) if order else None,
                "correlation_id": correlation_id,
                **data,
            },
        )

    @staticmethod
    def _context(conversation: Conversation) -> ConversationContext:
        try:
            return ConversationContext.from_payload(conversation.context)
        except (KeyError, TypeError, ValueError):
            return ConversationContext()

    @staticmethod
    def _valid_uuid(value: object) -> UUID | None:
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return None

    def _detach_locked(self, conversation: Conversation, context: ConversationContext):
        conversation.context = replace(context, active_order_id=None).to_payload()
        conversation.save(update_fields=["context", "updated_at"])

    def _attach_locked(
        self, conversation: Conversation, context: ConversationContext, order: Order
    ):
        conversation.context = replace(
            context,
            flow=ConversationFlow.ORDER_BUILDING,
            active_journey=ActiveJourney.BUY,
            active_order_id=str(order.id),
        ).to_payload()
        conversation.save(update_fields=["context", "updated_at"])

    def _validate_locked(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        context = self._context(conversation)
        raw_context = (
            conversation.context if isinstance(conversation.context, dict) else {}
        )
        active_order_id = raw_context.get("active_order_id", context.active_order_id)
        if context.active_journey not in {None, ActiveJourney.BUY}:
            return ActiveOrderDecision(status=ActiveOrderDecisionStatus.NO_ACTIVE_ORDER)
        order_id = self._valid_uuid(active_order_id)
        if order_id is None:
            if active_order_id:
                self._detach_locked(conversation, context)
                self._trace(
                    "stale_active_order",
                    conversation,
                    correlation_id=correlation_id,
                    reason="invalid_uuid",
                )
                return ActiveOrderDecision(
                    status=ActiveOrderDecisionStatus.STALE_CONTEXT,
                    reason="invalid_uuid",
                    stale_context_detected=True,
                )
            return ActiveOrderDecision(status=ActiveOrderDecisionStatus.NO_ACTIVE_ORDER)
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if (
            order is None
            or order.customer_id != conversation.user_id
            or order.status not in self.active_statuses
        ):
            reason = (
                "missing"
                if order is None
                else "wrong_owner"
                if order.customer_id != conversation.user_id
                else "inactive_status"
            )
            self._detach_locked(conversation, context)
            self._trace(
                "stale_active_order", conversation, order, correlation_id, reason=reason
            )
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.STALE_CONTEXT,
                reason=reason,
                stale_context_detected=True,
            )
        if context.active_journey is None:
            self._attach_locked(conversation, context, order)
        return ActiveOrderDecision(status=ActiveOrderDecisionStatus.ACTIVE, order=order)

    def _resolve_building_locked(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        """Validate the pointer, then fall back to the user-owned DB state."""
        pointer = self._validate_locked(conversation, correlation_id=correlation_id)
        if pointer.order:
            return pointer
        context = self._context(conversation)
        # A building order can be deliberately suspended while a seller or
        # driver journey is foregrounded.  Database fallback must not silently
        # reattach it and let its pending state consume the new journey.
        if context.active_journey not in {None, ActiveJourney.BUY} or any(
            task.journey == ActiveJourney.BUY for task in context.suspended_tasks
        ):
            return pointer
        order = (
            Order.objects.select_for_update()
            .filter(customer_id=conversation.user_id, status__in=self.active_statuses)
            .order_by("created_at")
            .first()
        )
        if order is None:
            return pointer
        self._attach_locked(conversation, context, order)
        return ActiveOrderDecision(
            status=ActiveOrderDecisionStatus.ACTIVE,
            order=order,
            stale_context_detected=pointer.stale_context_detected,
        )

    @transaction.atomic
    def get_active_order(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        locked = (
            Conversation.objects.select_for_update()
            .select_related("user")
            .get(pk=conversation.pk)
        )
        if locked.status == ConversationStatus.CLOSED:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.CLOSED_CONVERSATION,
                reason="conversation_closed",
            )
        return self._resolve_building_locked(locked, correlation_id=correlation_id)

    def validate_active_order(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        """Named validation entry point for callers that need the decision."""
        return self.get_active_order(conversation, correlation_id=correlation_id)

    @transaction.atomic
    def detach_order(self, conversation: Conversation) -> ActiveOrderDecision:
        locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if locked.status == ConversationStatus.CLOSED:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.CLOSED_CONVERSATION,
                reason="conversation_closed",
            )
        self._detach_locked(locked, self._context(locked))
        return ActiveOrderDecision(status=ActiveOrderDecisionStatus.NO_ACTIVE_ORDER)

    @transaction.atomic
    def attach_order(
        self,
        conversation: Conversation,
        order_id: str,
        *,
        correlation_id: str | None = None,
    ) -> ActiveOrderDecision:
        """Explicitly attaches only a customer-owned mutable order."""
        locked = (
            Conversation.objects.select_for_update()
            .select_related("user")
            .get(pk=conversation.pk)
        )
        if locked.status == ConversationStatus.CLOSED:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.CLOSED_CONVERSATION,
                reason="conversation_closed",
            )
        context = self._context(locked)
        if context.active_journey not in {None, ActiveJourney.BUY}:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.REQUIRES_USER_CHOICE,
                reason="another_journey_active",
                requires_user_choice=True,
            )
        parsed_id = self._valid_uuid(order_id)
        order = (
            Order.objects.select_for_update()
            .filter(
                pk=parsed_id,
                customer=locked.user,
                status__in=self.active_statuses,
            )
            .first()
            if parsed_id
            else None
        )
        if order is None:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.NO_ACTIVE_ORDER,
                reason="not_resumable",
            )
        # Attaching an existing domain order is a narrow context mutation.  Do
        # not rebuild the dataclass: suspended tasks, onboarding drafts and
        # future context fields must survive this operation.
        locked.context = replace(
            context,
            flow=ConversationFlow.ORDER_BUILDING,
            active_journey=ActiveJourney.BUY,
            active_order_id=str(order.id),
        ).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        self._trace("draft_resumed", locked, order, correlation_id)
        return ActiveOrderDecision(
            status=ActiveOrderDecisionStatus.RESUMED, order=order
        )

    @transaction.atomic
    def get_or_create_draft(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        locked = (
            Conversation.objects.select_for_update()
            .select_related("user")
            .get(pk=conversation.pk)
        )
        if locked.status == ConversationStatus.CLOSED:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.CLOSED_CONVERSATION,
                reason="conversation_closed",
            )
        context = self._context(locked)
        if context.active_journey not in {None, ActiveJourney.BUY}:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.REQUIRES_USER_CHOICE,
                reason="another_journey_active",
                requires_user_choice=True,
            )
        decision = self._resolve_building_locked(locked, correlation_id=correlation_id)
        if decision.order:
            self._trace("draft_resumed", locked, decision.order, correlation_id)
            return decision
        order = create_order_draft(
            customer=locked.user, actor=locked.user, correlation_id=correlation_id
        )
        context = self._context(locked)
        locked.context = replace(
            context,
            flow=ConversationFlow.ORDER_BUILDING,
            active_journey=ActiveJourney.BUY,
            active_order_id=str(order.id),
        ).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        self._trace("draft_created", locked, order, correlation_id)
        return ActiveOrderDecision(
            status=ActiveOrderDecisionStatus.CREATED,
            order=order,
            stale_context_detected=decision.stale_context_detected,
        )

    @transaction.atomic
    def resume_order(
        self,
        conversation: Conversation,
        order_id: str,
        *,
        correlation_id: str | None = None,
    ) -> ActiveOrderDecision:
        return self.attach_order(conversation, order_id, correlation_id=correlation_id)

    @transaction.atomic
    def abandon_order(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        locked = (
            Conversation.objects.select_for_update()
            .select_related("user")
            .get(pk=conversation.pk)
        )
        if locked.status == ConversationStatus.CLOSED:
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.CLOSED_CONVERSATION,
                reason="conversation_closed",
            )
        decision = self._resolve_building_locked(locked, correlation_id=correlation_id)
        if not decision.order:
            return decision
        order = cancel_order(
            decision.order,
            OrderCancellationCode.CUSTOMER_REQUEST,
            actor=locked.user,
            correlation_id=correlation_id,
        )
        # Only clear order-scoped state.  A customer can also be preparing a
        # seller catalogue or onboarding task, which must remain resumable.
        context = self._context(locked)
        metadata = dict(context.metadata)
        metadata.pop("awaiting_delivery_instructions", None)
        metadata.pop("delivery_instructions_decided", None)
        metadata.pop("last_confirmed_order_id", None)
        locked.context = replace(
            context,
            flow=ConversationFlow.IDLE,
            active_order_id=None,
            pending_clarification=None,
            metadata=metadata,
        ).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        self._trace("draft_abandoned", locked, order, correlation_id)
        return ActiveOrderDecision(
            status=ActiveOrderDecisionStatus.ABANDONED, order=order
        )

    def start_new(
        self, conversation: Conversation, *, correlation_id: str | None = None
    ) -> ActiveOrderDecision:
        """Never creates an empty draft; an existing one needs explicit abandonment."""
        decision = self.get_active_order(conversation, correlation_id=correlation_id)
        if decision.order:
            self._trace(
                "active_order_conflict", conversation, decision.order, correlation_id
            )
            return ActiveOrderDecision(
                status=ActiveOrderDecisionStatus.REQUIRES_USER_CHOICE,
                order=decision.order,
                requires_user_choice=True,
            )
        return decision
