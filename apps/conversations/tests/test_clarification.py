from decimal import Decimal

from apps.ai.domain.understanding import (
    ExtractedItem,
    ExtractedUnit,
    MessageIntent,
    UserMessageUnderstanding,
)
from apps.conversations.application.clarification import ClarificationEngine
from apps.conversations.domain.context import (
    ClarificationKind,
    ClarificationStatus,
    ConversationContext,
    ConversationFlow,
    PendingClarification,
    ResponseInstruction,
)
from apps.messaging.domain.enums import MessageType


def understanding(*, intent=MessageIntent.CREATE_OR_UPDATE_ORDER, items=()):
    return UserMessageUnderstanding(
        intent=intent, confidence=Decimal("0.9"), items=items
    )


def test_missing_quantity_then_short_contextual_reply():
    engine = ClarificationEngine()
    first = engine.decide(
        context=ConversationContext(),
        understanding=understanding(items=(ExtractedItem(requested_name="riz"),)),
    )
    assert first.pending_clarification.kind == ClarificationKind.MISSING_QUANTITY
    assert first.response_instruction == ResponseInstruction.ASK_QUANTITY

    second = engine.decide(
        context=ConversationContext(
            flow=first.next_flow, pending_clarification=first.pending_clarification
        ),
        structured_data={"text": "5 kg"},
    )
    assert second.status == ClarificationStatus.RESOLVED
    assert second.resolved_values == {"quantity": Decimal("5"), "unit": "KG"}


def test_location_and_confirmation_structures_bypass_language_parsing():
    engine = ClarificationEngine()
    location = engine.decide(
        context=ConversationContext(
            pending_clarification=PendingClarification(
                kind=ClarificationKind.MISSING_LOCATION, field="location"
            )
        ),
        message_type=MessageType.LOCATION,
    )
    confirmation = engine.decide(
        context=ConversationContext(
            pending_clarification=PendingClarification(
                kind=ClarificationKind.CONFIRMATION, field="confirmation"
            )
        ),
        message_type=MessageType.INTERACTIVE,
        structured_data={"action_id": "order.confirm"},
    )
    assert location.resolved_values["location_received"] is True
    assert confirmation.resolved_values["confirmed"] is True


def test_cancel_and_help_do_not_force_old_pending_context():
    pending = PendingClarification(
        kind=ClarificationKind.MISSING_QUANTITY, field="quantity"
    )
    engine = ClarificationEngine()
    cancelled = engine.decide(
        context=ConversationContext(
            flow=ConversationFlow.AWAITING_CLARIFICATION, pending_clarification=pending
        ),
        understanding=understanding(intent=MessageIntent.CANCEL),
    )
    help_result = engine.decide(
        context=ConversationContext(
            flow=ConversationFlow.AWAITING_CLARIFICATION, pending_clarification=pending
        ),
        understanding=understanding(intent=MessageIntent.HELP),
    )
    assert cancelled.resolved_values["cancel_requested"] is True
    assert help_result.status == ClarificationStatus.INTERRUPTED
    assert help_result.pending_clarification == pending


def test_new_order_request_interrupts_old_pending_clarification():
    pending = PendingClarification(
        kind=ClarificationKind.MISSING_QUANTITY, field="quantity"
    )
    result = ClarificationEngine().decide(
        context=ConversationContext(pending_clarification=pending),
        understanding=understanding(
            items=(
                ExtractedItem(
                    requested_name="huile",
                    quantity=Decimal("1"),
                    unit=ExtractedUnit.BOTTLE,
                ),
            )
        ),
    )
    assert result.status == ClarificationStatus.INTERRUPTED
    assert result.metadata["pending_abandoned"] is True
