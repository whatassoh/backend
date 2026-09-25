from enum import StrEnum
from typing import Any, TypedDict


class AgentRoute(StrEnum):
    SOCIAL = "SOCIAL"
    INTERRUPT = "INTERRUPT"
    BUY = "BUY"
    SELL = "SELL"
    DELIVER = "DELIVER"
    SUPPORT = "SUPPORT"
    MEMORY = "MEMORY"
    UNKNOWN = "UNKNOWN"


class ToolRisk(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class AgentGraphState(TypedDict, total=False):
    """Bounded, JSON-safe state passed between graph nodes."""

    graph_version: str
    thread_id: str
    correlation_id: str | None
    message_reference: str
    active_task: str | None
    active_journey: str | None
    intent: str
    confidence: float
    ambiguities: list[str]
    entities: list[dict[str, Any]]
    route: str
    requested_capabilities: list[str]
    plan: list[dict[str, Any]]
    risk_level: str
    requires_confirmation: bool
    policy_decision: str
    shadow_only: bool
    comparison: dict[str, Any]
