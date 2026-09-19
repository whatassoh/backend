"""Rebuild a customer-visible next step from persistent conversation state."""

import logging
from dataclasses import dataclass
from enum import StrEnum

from apps.conversations.application.active_orders import (
    ActiveCustomerOrderService,
    ActiveOrderDecisionStatus,
)
from apps.conversations.domain.context import ConversationFlow
from apps.orders.application.review import OrderReviewService

logger = logging.getLogger(__name__)


class RecoveryStatus(StrEnum):
    NO_ACTIVE_FLOW = "NO_ACTIVE_FLOW"
    RESUME_DRAFT = "RESUME_DRAFT"
    RESUME_CLARIFICATION = "RESUME_CLARIFICATION"
    REQUEST_LOCATION = "REQUEST_LOCATION"
    REVIEW_READY = "REVIEW_READY"
    STALE_CONTEXT_RECOVERED = "STALE_CONTEXT_RECOVERED"
    CLOSED_CONVERSATION = "CLOSED_CONVERSATION"


@dataclass(frozen=True, kw_only=True)
class RecoveryDecision:
    status: RecoveryStatus
    order_id: str | None = None
    next_flow: ConversationFlow = ConversationFlow.IDLE
    resumable: bool = False
    requires_user_choice: bool = False
    reason: str | None = None


class ConversationRecoveryService:
    """No in-memory state and no provider calls: safe after restart/failure."""

    def __init__(self, *, active_order_service=None, review_service=None):
        self.active_order_service = active_order_service or ActiveCustomerOrderService()
        self.review_service = review_service or OrderReviewService()

    def recover(self, conversation, context, *, correlation_id=None) -> RecoveryDecision:
        active = self.active_order_service.get_active_order(
            conversation, correlation_id=correlation_id
        )
        if active.status == ActiveOrderDecisionStatus.CLOSED_CONVERSATION:
            return RecoveryDecision(
                status=RecoveryStatus.CLOSED_CONVERSATION, reason="conversation_closed"
            )
        if active.stale_context_detected:
            self._log("recovery_stale_context", conversation, correlation_id)
            return RecoveryDecision(
                status=RecoveryStatus.STALE_CONTEXT_RECOVERED,
                reason=active.reason,
            )
        if active.order is None:
            return RecoveryDecision(status=RecoveryStatus.NO_ACTIVE_FLOW)
        order = active.order
        if context.pending_clarification:
            return RecoveryDecision(
                status=RecoveryStatus.RESUME_CLARIFICATION,
                order_id=str(order.id),
                next_flow=ConversationFlow.AWAITING_CLARIFICATION,
                resumable=True,
            )
        if order.delivery_location is None:
            return RecoveryDecision(
                status=RecoveryStatus.REQUEST_LOCATION,
                order_id=str(order.id),
                next_flow=ConversationFlow.AWAITING_LOCATION,
                resumable=True,
            )
        review = self.review_service.build_review(order)
        if review.readiness.ready:
            return RecoveryDecision(
                status=RecoveryStatus.REVIEW_READY,
                order_id=str(order.id),
                next_flow=ConversationFlow.AWAITING_CONFIRMATION,
                resumable=True,
            )
        return RecoveryDecision(
            status=RecoveryStatus.RESUME_DRAFT,
            order_id=str(order.id),
            next_flow=ConversationFlow.ORDER_BUILDING,
            resumable=True,
            requires_user_choice=True,
        )

    @staticmethod
    def _log(event, conversation, correlation_id):
        logger.info(
            event,
            extra={
                "conversation_id": str(conversation.id),
                "correlation_id": correlation_id,
            },
        )
