"""Active LangGraph boundary restricted to allowlisted R1 business reads."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.ai.application.agent_graph import GRAPH_VERSION, build_compiled_graph
from apps.ai.application.tools import (
    ToolContext,
    ToolResult,
    build_business_tool_registry,
)
from apps.ai.domain.agent_state import ToolRisk
from apps.ai.infrastructure.checkpoints import postgres_saver
from apps.ai.infrastructure.models import AgentExecutionRun
from apps.conversations.application.interruptions import active_task_kind
from apps.conversations.domain.context import ConversationContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActiveAgentDecision:
    """Authorized results; raw tool output is not checkpointed or audited."""

    route: str
    policy_decision: str
    risk_level: str
    tool_results: dict[str, ToolResult] = field(default_factory=dict)


class ActiveReadAgentService:
    """Plan with LangGraph, enforce policy, then execute R1 tools outside the graph."""

    def __init__(self, *, tool_registry=None):
        self.tool_registry = tool_registry or build_business_tool_registry()

    def run(self, *, conversation, message, understanding, correlation_id=None):
        started = time.monotonic()
        audit_correlation_id = _audit_correlation_id(correlation_id, message)
        context = ConversationContext.from_payload(conversation.context)
        task = active_task_kind(context)
        initial_state = {
            "graph_version": GRAPH_VERSION,
            "thread_id": str(conversation.id),
            "correlation_id": audit_correlation_id,
            "message_reference": message.external_message_id,
            "active_task": task.value if task else None,
            "active_journey": (
                context.active_journey.value if context.active_journey else None
            ),
            "intent": understanding.intent.value,
            "journey_goal": understanding.journey_goal.value,
            "confidence": float(understanding.confidence),
            "ambiguities": list(understanding.ambiguities[:10]),
            "entities": [
                {
                    "requested_name": item.requested_name[:120],
                    "quantity": str(item.quantity) if item.quantity else None,
                    "unit": item.unit.value if item.unit else None,
                }
                for item in understanding.items[:10]
            ],
            "shadow_only": False,
        }
        with postgres_saver() as saver:
            graph = build_compiled_graph(checkpointer=saver)
            state = graph.invoke(
                initial_state,
                config={
                    "configurable": {
                        "thread_id": str(conversation.id),
                        "checkpoint_ns": "active-read-v2",
                    }
                },
            )
        plan = self._validated_plan(state)
        results: dict[str, ToolResult] = {}
        executed = []
        if state.get("policy_decision") == "ACTIVE_READ_ONLY":
            tool_context = ToolContext(
                user=conversation.user,
                conversation=conversation,
                shadow_mode=False,
                confirmed=False,
            )
            for step in plan:
                result = self.tool_registry.execute(
                    step["tool"], step.get("arguments", {}), tool_context
                )
                results[step["tool"]] = result
                executed.append(step["tool"])
        status = (
            "COMPLETED"
            if state.get("policy_decision") == "ACTIVE_READ_ONLY"
            else "POLICY_DENIED"
        )
        self._persist(
            conversation=conversation,
            correlation_id=audit_correlation_id,
            state=state,
            status=status,
            executed=executed,
            results=results,
            started=started,
        )
        return ActiveAgentDecision(
            route=state.get("route", ""),
            policy_decision=state.get("policy_decision", ""),
            risk_level=state.get("risk_level", ""),
            tool_results=results,
        )

    def _validated_plan(self, state) -> list[dict[str, Any]]:
        plan = state.get("plan", [])
        if len(plan) > settings.AI_AGENT_MAX_PLAN_STEPS:
            raise ValueError("Agent plan exceeds configured step budget.")
        for step in plan:
            name = step.get("tool")
            spec = self.tool_registry.specs.get(name)
            if spec is None:
                raise ValueError("Agent plan contains a non-allowlisted tool.")
            if state.get("policy_decision") == "ACTIVE_READ_ONLY" and (
                spec.risk != ToolRisk.R1
                or spec.side_effects not in {"none", "read_only"}
            ):
                raise ValueError("Active agent policy admitted a mutating tool.")
        return plan

    @staticmethod
    def _persist(
        *,
        conversation,
        correlation_id,
        state,
        status,
        executed,
        results,
        started,
    ):
        run, _ = AgentExecutionRun.objects.update_or_create(
            conversation=conversation,
            correlation_id=correlation_id,
            defaults={
                "graph_version": GRAPH_VERSION,
                "status": status,
                "route": state.get("route", ""),
                "planned_tools": [item["tool"] for item in state.get("plan", [])],
                "executed_tools": executed,
                "result_statuses": {
                    name: result.status for name, result in results.items()
                },
                "risk_level": state.get("risk_level", ""),
                "policy_decision": state.get("policy_decision", ""),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error_category": None,
            },
        )
        logger.info(
            "active_read_agent_completed",
            extra={
                "run_id": str(run.id),
                "conversation_id": str(conversation.id),
                "status": run.status,
                "route": run.route,
                "planned_tool_count": len(run.planned_tools),
                "executed_tool_count": len(run.executed_tools),
                "latency_ms": run.latency_ms,
            },
        )


def run_active_agent_safely(
    *, service, conversation, message, understanding, correlation_id=None
):
    """Fail open to the established deterministic orchestration path."""

    started = time.monotonic()
    try:
        return service.run(
            conversation=conversation,
            message=message,
            understanding=understanding,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        audit_correlation_id = _audit_correlation_id(correlation_id, message)
        logger.exception(
            "active_read_agent_failed",
            extra={
                "conversation_id": str(conversation.id),
                "correlation_id": correlation_id,
                "error_category": exc.__class__.__name__,
            },
        )
        try:
            AgentExecutionRun.objects.update_or_create(
                conversation=conversation,
                correlation_id=audit_correlation_id,
                defaults={
                    "graph_version": GRAPH_VERSION,
                    "status": "FAILED",
                    "route": "",
                    "planned_tools": [],
                    "executed_tools": [],
                    "result_statuses": {},
                    "risk_level": "",
                    "policy_decision": "",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error_category": exc.__class__.__name__[:80],
                },
            )
        except Exception:
            logger.exception(
                "active_read_agent_failure_audit_failed",
                extra={"conversation_id": str(conversation.id)},
            )
        return None


def _audit_correlation_id(correlation_id, message) -> str:
    """Ensure every audit upsert has a stable non-null idempotency key."""

    value = correlation_id or f"message:{message.external_message_id}"
    return str(value)[:100]
