from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ConversationFlow(StrEnum):
    IDLE = "IDLE"
    ORDER_BUILDING = "ORDER_BUILDING"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    AWAITING_LOCATION = "AWAITING_LOCATION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"


class ClarificationKind(StrEnum):
    MISSING_QUANTITY = "MISSING_QUANTITY"
    MISSING_UNIT = "MISSING_UNIT"
    AMBIGUOUS_PRODUCT = "AMBIGUOUS_PRODUCT"
    MISSING_LOCATION = "MISSING_LOCATION"
    CONFIRMATION = "CONFIRMATION"
    OTHER = "OTHER"


class ClarificationStatus(StrEnum):
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    INTERRUPTED = "INTERRUPTED"


class ResponseInstruction(StrEnum):
    ASK_QUANTITY = "ASK_QUANTITY"
    ASK_UNIT = "ASK_UNIT"
    ASK_PRODUCT_CHOICE = "ASK_PRODUCT_CHOICE"
    REQUEST_LOCATION = "REQUEST_LOCATION"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    NONE = "NONE"


@dataclass(frozen=True, kw_only=True)
class PendingClarification:
    kind: ClarificationKind
    field: str
    item_reference: str | None = None
    candidate_ids: tuple[str, ...] = ()
    created_at: str | None = None
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ConversationContext:
    flow: ConversationFlow = ConversationFlow.IDLE
    active_order_id: str | None = None
    pending_clarification: PendingClarification | None = None
    last_understanding: dict[str, Any] | None = None
    processed_message_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["flow"] = self.flow.value
        if self.pending_clarification:
            payload["pending_clarification"]["kind"] = (
                self.pending_clarification.kind.value
            )
            payload["pending_clarification"]["candidate_ids"] = list(
                self.pending_clarification.candidate_ids
            )
        payload["processed_message_ids"] = list(self.processed_message_ids)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ConversationContext":
        payload = payload or {}
        pending_data = payload.get("pending_clarification")
        pending = None
        if pending_data:
            pending = PendingClarification(
                kind=ClarificationKind(pending_data["kind"]),
                field=pending_data["field"],
                item_reference=pending_data.get("item_reference"),
                candidate_ids=tuple(pending_data.get("candidate_ids", ())),
                created_at=pending_data.get("created_at"),
                attempts=int(pending_data.get("attempts", 0)),
                metadata=dict(pending_data.get("metadata", {})),
            )
        return cls(
            flow=ConversationFlow(payload.get("flow", ConversationFlow.IDLE)),
            active_order_id=payload.get("active_order_id"),
            pending_clarification=pending,
            last_understanding=payload.get("last_understanding"),
            processed_message_ids=tuple(payload.get("processed_message_ids", ())),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True, kw_only=True)
class ClarificationResult:
    status: ClarificationStatus
    resolved_values: dict[str, Any] = field(default_factory=dict)
    next_flow: ConversationFlow = ConversationFlow.IDLE
    pending_clarification: PendingClarification | None = None
    response_instruction: ResponseInstruction = ResponseInstruction.NONE
    metadata: dict[str, Any] = field(default_factory=dict)
