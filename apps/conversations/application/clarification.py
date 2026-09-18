import re
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal

from apps.ai.domain.understanding import (
    MessageIntent,
    UserMessageUnderstanding,
)
from apps.catalog.domain.grounding import CatalogResolution
from apps.conversations.domain.context import (
    ClarificationKind,
    ClarificationResult,
    ClarificationStatus,
    ConversationContext,
    ConversationFlow,
    PendingClarification,
    ResponseInstruction,
)
from apps.messaging.domain.enums import MessageType
from apps.orders.domain.enums import OrderItemResolutionStatus

DEFAULT_CLARIFICATION_TEXT = {
    ResponseInstruction.ASK_QUANTITY: "Quelle quantité de {item} souhaitez-vous ?",
    ResponseInstruction.ASK_UNIT: "Quelle unité souhaitez-vous pour {item} ?",
    ResponseInstruction.ASK_PRODUCT_CHOICE: "Lequel souhaitez-vous ?",
    ResponseInstruction.REQUEST_LOCATION: "Où souhaitez-vous être livré ?",
    ResponseInstruction.REQUEST_CONFIRMATION: "Veuillez confirmer votre demande.",
}


class ClarificationEngine:
    def decide(
        self,
        *,
        context: ConversationContext,
        understanding: UserMessageUnderstanding | None = None,
        resolutions: Iterable[CatalogResolution] = (),
        message_type: MessageType | str = MessageType.TEXT,
        structured_data: dict | None = None,
        requires_location: bool = False,
    ) -> ClarificationResult:
        structured_data = structured_data or {}
        pending = context.pending_clarification
        message_type = MessageType(message_type)
        if pending:
            interrupted = self._interrupt_if_needed(context, understanding)
            if interrupted:
                return interrupted
            return self._resolve_pending(
                pending, message_type, structured_data, understanding
            )
        for item in understanding.items if understanding else ():
            if item.quantity is None:
                return self._ask(
                    ClarificationKind.MISSING_QUANTITY, "quantity", item.requested_name
                )
            if item.unit is None:
                return self._ask(
                    ClarificationKind.MISSING_UNIT, "unit", item.requested_name
                )
        for resolution in resolutions:
            if resolution.status == OrderItemResolutionStatus.AMBIGUOUS:
                return self._ask(
                    ClarificationKind.AMBIGUOUS_PRODUCT,
                    "product",
                    resolution.requested_name,
                    candidate_ids=tuple(
                        str(candidate.product_id) for candidate in resolution.candidates
                    ),
                )
        if requires_location:
            return self._ask(
                ClarificationKind.MISSING_LOCATION,
                "location",
                flow=ConversationFlow.AWAITING_LOCATION,
            )
        return ClarificationResult(
            status=ClarificationStatus.READY,
            next_flow=ConversationFlow.AWAITING_CONFIRMATION,
            response_instruction=ResponseInstruction.REQUEST_CONFIRMATION,
        )

    def _resolve_pending(self, pending, message_type, structured_data, understanding):
        if (
            pending.kind == ClarificationKind.MISSING_LOCATION
            and message_type == MessageType.LOCATION
        ):
            return ClarificationResult(
                status=ClarificationStatus.RESOLVED,
                resolved_values={"location_received": True},
                next_flow=ConversationFlow.AWAITING_CONFIRMATION,
                response_instruction=ResponseInstruction.REQUEST_CONFIRMATION,
                metadata={"deterministic": True},
            )
        if (
            pending.kind == ClarificationKind.CONFIRMATION
            and structured_data.get("action_id") == "order.confirm"
        ):
            return ClarificationResult(
                status=ClarificationStatus.RESOLVED,
                resolved_values={"confirmed": True},
                next_flow=ConversationFlow.AWAITING_CONFIRMATION,
                metadata={"deterministic": True},
            )
        if pending.kind == ClarificationKind.AMBIGUOUS_PRODUCT:
            chosen = structured_data.get("selected_candidate_id")
            if chosen in pending.candidate_ids:
                return ClarificationResult(
                    status=ClarificationStatus.RESOLVED,
                    resolved_values={"product_id": chosen},
                    next_flow=ConversationFlow.ORDER_BUILDING,
                    metadata={"deterministic": True},
                )
        if pending.kind == ClarificationKind.MISSING_QUANTITY:
            quantity = _parse_quantity(structured_data.get("text", ""))
            if quantity:
                return ClarificationResult(
                    status=ClarificationStatus.RESOLVED,
                    resolved_values=quantity,
                    next_flow=ConversationFlow.ORDER_BUILDING,
                    metadata={"deterministic": True},
                )
        return ClarificationResult(
            status=ClarificationStatus.UNRESOLVED,
            next_flow=ConversationFlow.AWAITING_CLARIFICATION,
            pending_clarification=replace(pending, attempts=pending.attempts + 1),
            response_instruction=_instruction_for(pending.kind),
        )

    def _interrupt_if_needed(self, context, understanding):
        if understanding is None:
            return None
        if understanding.intent == MessageIntent.CANCEL:
            return ClarificationResult(
                status=ClarificationStatus.RESOLVED,
                resolved_values={"cancel_requested": True},
                next_flow=ConversationFlow.IDLE,
            )
        if understanding.intent == MessageIntent.HELP:
            return ClarificationResult(
                status=ClarificationStatus.INTERRUPTED,
                next_flow=context.flow,
                pending_clarification=context.pending_clarification,
                metadata={"temporary_interruption": "knowledge"},
            )
        if (
            understanding.intent
            in {MessageIntent.CREATE_OR_UPDATE_ORDER, MessageIntent.MODIFY}
            and understanding.items
        ):
            return ClarificationResult(
                status=ClarificationStatus.INTERRUPTED,
                next_flow=ConversationFlow.ORDER_BUILDING,
                metadata={"pending_abandoned": True},
            )
        return None

    def _ask(
        self,
        kind,
        field,
        item_reference=None,
        candidate_ids=(),
        flow=ConversationFlow.AWAITING_CLARIFICATION,
    ):
        return ClarificationResult(
            status=ClarificationStatus.NEEDS_CLARIFICATION,
            next_flow=flow,
            pending_clarification=PendingClarification(
                kind=kind,
                field=field,
                item_reference=item_reference,
                candidate_ids=candidate_ids,
            ),
            response_instruction=_instruction_for(kind),
        )


def _instruction_for(kind):
    return {
        ClarificationKind.MISSING_QUANTITY: ResponseInstruction.ASK_QUANTITY,
        ClarificationKind.MISSING_UNIT: ResponseInstruction.ASK_UNIT,
        ClarificationKind.AMBIGUOUS_PRODUCT: ResponseInstruction.ASK_PRODUCT_CHOICE,
        ClarificationKind.MISSING_LOCATION: ResponseInstruction.REQUEST_LOCATION,
        ClarificationKind.CONFIRMATION: ResponseInstruction.REQUEST_CONFIRMATION,
    }.get(kind, ResponseInstruction.NONE)


def _parse_quantity(text: str):
    match = re.search(
        r"(?P<quantity>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|l|ml|bouteilles?)?",
        text.lower(),
    )
    if not match:
        return None
    unit = {"kg": "KG", "g": "G", "l": "L", "ml": "ML"}.get(match.group("unit"))
    return {
        "quantity": Decimal(match.group("quantity").replace(",", ".")),
        "unit": unit,
    }
