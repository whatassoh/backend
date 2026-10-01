"""Low-cardinality telemetry for the current conversational orchestrator.

Observability is deliberately best-effort: metrics and diagnostics must never
change a user-facing decision. No message text, external identity, location,
order identifier, or model payload is exported by this module.
"""

import logging
from dataclasses import dataclass
from time import monotonic

from prometheus_client import Counter, Histogram

from apps.ai.application.runtime import get_ai_runtime_policy
from apps.conversations.infrastructure.models import ConversationMessage
from apps.messaging.domain.enums import MessageDirection

logger = logging.getLogger(__name__)

DECISIONS = Counter(
    "whatassoh_conversation_decisions_total",
    "Completed conversational decisions.",
    ("channel", "status", "route"),
)
AI_FAILURES = Counter(
    "whatassoh_conversation_ai_failures_total",
    "AI failures converted to a recoverable conversational outcome.",
    ("channel", "category", "retryable"),
)
REPEATED_RESPONSES = Counter(
    "whatassoh_conversation_repeated_responses_total",
    "Responses equal to the immediately preceding outbound response.",
    ("channel", "flow"),
)
DECISION_DURATION = Histogram(
    "whatassoh_conversation_decision_duration_seconds",
    "Wall-clock duration of conversational orchestration.",
    ("channel", "status"),
)

_ROUTE_METADATA_KEYS = (
    "capability",
    "driver_offer",
    "driver_workspace",
    "journey_selection",
    "operation",
    "seller_workspace",
    "universal_interrupt",
)


@dataclass(frozen=True, slots=True)
class DecisionTimer:
    """Monotonic timer passed across the orchestration boundary."""

    started_at: float

    @classmethod
    def start(cls) -> "DecisionTimer":
        return cls(started_at=monotonic())

    def elapsed_seconds(self) -> float:
        return max(0.0, monotonic() - self.started_at)


def observe_ai_failure(*, channel: str, error: Exception) -> None:
    """Count a classified AI failure without exposing its message."""

    if not _enabled():
        return
    try:
        AI_FAILURES.labels(
            channel=_bounded(channel),
            category=error.__class__.__name__,
            retryable=str(bool(getattr(error, "retryable", False))).lower(),
        ).inc()
    except Exception:  # pragma: no cover - telemetry must be non-blocking
        logger.exception("conversation_ai_failure_observation_failed")


def observe_decision(
    *, conversation, message, outcome, flow_before: str, timer: DecisionTimer
) -> None:
    """Record one completed turn and detect a repeated outbound answer.

    The repetition check performs a single indexed query for the latest
    outbound message. It compares in process and never emits either text.
    """

    if not _enabled():
        return
    try:
        channel = _bounded(message.channel)
        status = _bounded(outcome.status)
        route = _route_for(outcome)
        DECISIONS.labels(channel=channel, status=status, route=route).inc()
        DECISION_DURATION.labels(channel=channel, status=status).observe(
            timer.elapsed_seconds()
        )
        if _equals_previous_response(conversation, outcome.response.text):
            REPEATED_RESPONSES.labels(channel=channel, flow=_bounded(flow_before)).inc()
            logger.warning(
                "conversation_repeated_response_detected",
                extra={
                    "channel": channel,
                    "flow": _bounded(flow_before),
                    "outcome_status": status,
                    "route_selected": route,
                },
            )
    except Exception:  # pragma: no cover - telemetry must be non-blocking
        logger.exception("conversation_decision_observation_failed")


def _enabled() -> bool:
    return get_ai_runtime_policy().decision_observability_enabled


def _equals_previous_response(conversation, response_text: str) -> bool:
    if not getattr(conversation, "pk", None):
        return False
    previous = (
        ConversationMessage.objects.filter(
            conversation=conversation,
            direction=MessageDirection.OUTBOUND,
        )
        .order_by("-occurred_at", "-created_at", "-id")
        .values_list("text", flat=True)
        .first()
    )
    return bool(previous and response_text and previous == response_text)


def _route_for(outcome) -> str:
    if str(outcome.status) == "DUPLICATE":
        return "duplicate"
    metadata = outcome.metadata or {}
    for key in _ROUTE_METADATA_KEYS:
        if key in metadata:
            return key
    return "workflow"


def _bounded(value: object, *, maximum: int = 64) -> str:
    """Bound labels defensively; callers should still pass finite enums."""

    normalized = str(getattr(value, "value", value) or "unknown").lower()
    return normalized[:maximum]
