"""Non-blocking shadow agent runner and legacy parity recorder."""

import logging
import time

from django.conf import settings
from django.db import IntegrityError

from apps.ai.application.agent_graph import GRAPH_VERSION, build_compiled_graph
from apps.ai.application.cache import EphemeralCache
from apps.ai.domain.agent_state import AgentRoute
from apps.ai.infrastructure.checkpoints import postgres_saver
from apps.ai.infrastructure.models import AgentShadowRun
from apps.conversations.application.ai_tools import build_business_tool_registry
from apps.conversations.application.interruptions import active_task_kind
from apps.conversations.domain.context import ConversationContext

logger = logging.getLogger(__name__)


class AgentShadowService:
    """Compare a bounded plan without executing tools or changing the response."""

    def __init__(self, *, understanding_service, tool_registry=None):
        self.understanding_service = understanding_service
        self.tool_registry = tool_registry or build_business_tool_registry()

    def run(self, *, conversation, message, legacy_outcome, correlation_id=None):
        if correlation_id:
            existing = AgentShadowRun.objects.filter(
                conversation=conversation, correlation_id=correlation_id
            ).first()
            if existing is not None:
                return existing
        started = time.monotonic()
        context = ConversationContext.from_payload(conversation.context)
        task = active_task_kind(context)
        understanding = self.understanding_service.understand_normalized(
            message,
            limited_context={
                "active_task": task.value if task else None,
                "active_journey": context.active_journey.value
                if context.active_journey
                else None,
                "flow": context.flow.value,
                "shadow_mode": True,
            },
            correlation_id=correlation_id,
        )
        if understanding is None:
            return self._persist(
                conversation=conversation,
                correlation_id=correlation_id,
                status="SKIPPED_STRUCTURED",
                started=started,
            )
        initial_state = {
            "graph_version": GRAPH_VERSION,
            "thread_id": str(conversation.id),
            "correlation_id": correlation_id,
            "message_reference": message.external_message_id,
            "active_task": task.value if task else None,
            "active_journey": context.active_journey.value
            if context.active_journey
            else None,
            "intent": understanding.intent.value,
            "confidence": float(understanding.confidence),
            "ambiguities": list(understanding.ambiguities),
            "entities": [
                {
                    "requested_name": item.requested_name,
                    "quantity": str(item.quantity) if item.quantity else None,
                    "unit": item.unit.value if item.unit else None,
                }
                for item in understanding.items[:10]
            ],
            "shadow_only": True,
        }
        cache_key = EphemeralCache.fingerprint(
            GRAPH_VERSION,
            conversation.id,
            message.external_message_id,
            initial_state["active_task"],
            initial_state["active_journey"],
        )
        state = EphemeralCache.get("shadow-decision", cache_key)
        if state is None:
            with postgres_saver() as saver:
                graph = build_compiled_graph(checkpointer=saver)
                state = graph.invoke(
                    initial_state,
                    config={
                        "configurable": {
                            "thread_id": str(conversation.id),
                            "checkpoint_ns": "shadow-v1",
                        }
                    },
                )
            EphemeralCache.set(
                "shadow-decision",
                cache_key,
                _safe_state(state),
                timeout=settings.AI_AGENT_SHADOW_CACHE_SECONDS,
            )
        self._validate_shadow_plan(state)
        comparison = compare_with_legacy(state, conversation, legacy_outcome)
        return self._persist(
            conversation=conversation,
            correlation_id=correlation_id,
            status="COMPLETED",
            state=state,
            comparison=comparison,
            started=started,
        )

    def _validate_shadow_plan(self, state):
        plan = state.get("plan", [])
        if len(plan) > settings.AI_AGENT_MAX_PLAN_STEPS:
            raise ValueError("Shadow plan exceeds the configured step budget.")
        unknown = [
            step.get("tool")
            for step in plan
            if step.get("tool") not in self.tool_registry.specs
        ]
        if unknown:
            raise ValueError("Shadow plan contains a non-allowlisted tool.")
        if state.get("policy_decision") != "SHADOW_ONLY":
            raise ValueError("Shadow graph attempted to leave shadow-only policy.")

    def _persist(
        self,
        *,
        conversation,
        correlation_id,
        status,
        started,
        state=None,
        comparison=None,
        error_category=None,
    ):
        state = state or {}
        comparison = comparison or {}
        values = {
            "graph_version": GRAPH_VERSION,
            "status": status,
            "route": state.get("route", ""),
            "planned_tools": [step["tool"] for step in state.get("plan", [])],
            "risk_level": state.get("risk_level", ""),
            "parity": comparison.get("parity"),
            "comparison": comparison,
            "state_snapshot": _safe_state(state),
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error_category": error_category,
        }
        try:
            run, _ = AgentShadowRun.objects.update_or_create(
                conversation=conversation,
                correlation_id=correlation_id,
                defaults=values,
            )
        except IntegrityError:
            run = AgentShadowRun.objects.get(
                conversation=conversation, correlation_id=correlation_id
            )
        logger.info(
            "agent_shadow_completed",
            extra={
                "shadow_run_id": str(run.id),
                "conversation_id": str(conversation.id),
                "status": run.status,
                "route": run.route,
                "parity": run.parity,
                "latency_ms": run.latency_ms,
            },
        )
        return run


def compare_with_legacy(state, conversation, outcome):
    context = ConversationContext.from_payload(conversation.context)
    route = state.get("route")
    journey_route = {
        "BUY": AgentRoute.BUY.value,
        "SELL": AgentRoute.SELL.value,
        "DELIVER": AgentRoute.DELIVER.value,
    }.get(context.active_journey.value if context.active_journey else None)
    response = getattr(outcome, "response", None)
    response_metadata = getattr(response, "metadata", {}) or {}
    capability = response_metadata.get("capability") or getattr(
        outcome, "metadata", {}
    ).get("capability")
    if capability == "greeting":
        expected = AgentRoute.SOCIAL.value
    elif capability in {"pause", "back", "cancel", "resume"}:
        expected = AgentRoute.INTERRUPT.value
    else:
        expected = journey_route
    return {
        "parity": expected is None or expected == route,
        "expected_route": expected,
        "shadow_route": route,
        "legacy_status": str(getattr(outcome, "status", "")),
        "tool_count": len(state.get("plan", [])),
    }


def _safe_state(state):
    allowed = {
        "graph_version",
        "thread_id",
        "correlation_id",
        "message_reference",
        "active_task",
        "active_journey",
        "intent",
        "confidence",
        "ambiguities",
        "entities",
        "route",
        "requested_capabilities",
        "plan",
        "risk_level",
        "requires_confirmation",
        "policy_decision",
        "shadow_only",
    }
    return {key: value for key, value in state.items() if key in allowed}


def run_agent_shadow_safely(
    *, conversation, message, legacy_outcome, understanding_service, correlation_id=None
):
    """Shadow failures are recorded and can never fail the customer turn."""

    started = time.monotonic()
    try:
        conversation.refresh_from_db(fields=["context", "updated_at"])
        return AgentShadowService(understanding_service=understanding_service).run(
            conversation=conversation,
            message=message,
            legacy_outcome=legacy_outcome,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.exception(
            "agent_shadow_failed",
            extra={
                "conversation_id": str(conversation.id),
                "correlation_id": correlation_id,
                "error_category": exc.__class__.__name__,
            },
        )
        values = {
            "graph_version": GRAPH_VERSION,
            "status": "FAILED",
            "route": "",
            "planned_tools": [],
            "risk_level": "",
            "parity": None,
            "comparison": {},
            "state_snapshot": {},
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error_category": exc.__class__.__name__[:80],
        }
        run, _ = AgentShadowRun.objects.update_or_create(
            conversation=conversation,
            correlation_id=correlation_id,
            defaults=values,
        )
        return run
