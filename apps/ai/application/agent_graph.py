"""Bounded LangGraph router and policy gate for shadow and read-only execution."""

from functools import lru_cache

from django.conf import settings

from apps.ai.domain.agent_state import AgentGraphState, AgentRoute, ToolRisk
from apps.ai.domain.understanding import MessageIntent

GRAPH_VERSION = "read-only-v2"

INTERRUPT_INTENTS = {
    MessageIntent.CANCEL,
    MessageIntent.PAUSE,
    MessageIntent.BACK,
    MessageIntent.ORDER_RESUME,
}


def route_node(state: AgentGraphState):
    intent = MessageIntent(state["intent"])
    route = AgentRoute.UNKNOWN
    if intent == MessageIntent.GREETING:
        route = AgentRoute.SOCIAL
    elif intent in INTERRUPT_INTENTS:
        route = AgentRoute.INTERRUPT
    elif intent in {MessageIntent.MEMORY_SHOW, MessageIntent.MEMORY_FORGET}:
        route = AgentRoute.MEMORY
    elif intent == MessageIntent.SELL:
        route = AgentRoute.SELL
    elif intent == MessageIntent.DELIVER:
        route = AgentRoute.DELIVER
    elif intent in {
        MessageIntent.CREATE_OR_UPDATE_ORDER,
        MessageIntent.CONFIRM,
        MessageIntent.MODIFY,
        MessageIntent.PROVIDE_LOCATION,
        MessageIntent.ORDER_HISTORY,
        MessageIntent.ORDER_STATUS,
        MessageIntent.CATALOG_BROWSE,
    }:
        route = AgentRoute.BUY
    elif intent in {MessageIntent.HELP, MessageIntent.OUT_OF_SCOPE}:
        route = AgentRoute.SUPPORT
    return {"route": route.value}


def plan_node(state: AgentGraphState):
    route = AgentRoute(state["route"])
    intent = MessageIntent(state["intent"])
    names: list[str] = []
    if route == AgentRoute.INTERRUPT:
        names = [
            {
                MessageIntent.CANCEL: "cancel_task",
                MessageIntent.PAUSE: "suspend_task",
                MessageIntent.BACK: "get_current_task",
                MessageIntent.ORDER_RESUME: "resume_task",
            }[intent]
        ]
    elif intent == MessageIntent.ORDER_HISTORY:
        names = ["list_orders"]
    elif intent == MessageIntent.ORDER_STATUS:
        names = ["get_active_order"]
    elif intent == MessageIntent.CATALOG_BROWSE:
        names = ["list_marketplace_products"]
    elif route == AgentRoute.BUY:
        names = ["search_products"] if state.get("entities") else []
    elif route == AgentRoute.SELL:
        names = ["get_store_state"]
    elif route == AgentRoute.DELIVER:
        names = ["get_driver_state", "get_active_delivery_mission"]
    elif route == AgentRoute.MEMORY and intent == MessageIntent.MEMORY_SHOW:
        names = ["get_memory_preferences", "get_response_style"]
    maximum = settings.AI_AGENT_MAX_PLAN_STEPS
    plan = []
    for name in names[:maximum]:
        arguments = {}
        if name == "search_products":
            arguments = {"query": state["entities"][0]["requested_name"]}
        plan.append({"tool": name, "arguments": arguments})
    return {"plan": plan, "requested_capabilities": names[:maximum]}


def policy_node(state: AgentGraphState):
    tool_risks = _registered_tool_risks()
    risks = [tool_risks.get(step["tool"], ToolRisk.R4) for step in state["plan"]]
    risk = max(risks, default=ToolRisk.R0, key=lambda item: int(item.value[1]))
    requires_confirmation = risk in {ToolRisk.R3, ToolRisk.R4}
    if state.get("shadow_only", True):
        decision = "SHADOW_ONLY"
    elif any(item != ToolRisk.R1 for item in risks):
        decision = "DENIED_NON_READ_ONLY"
    else:
        decision = "ACTIVE_READ_ONLY"
    return {
        "risk_level": risk.value,
        "requires_confirmation": requires_confirmation,
        "policy_decision": decision,
    }


@lru_cache(maxsize=1)
def _registered_tool_risks():
    """Keep graph policy aligned with the registry's single risk declaration."""

    from apps.conversations.application.ai_tools import build_business_tool_registry

    return {
        name: specification.risk
        for name, specification in build_business_tool_registry().specs.items()
    }


def build_compiled_graph(*, checkpointer=None):
    """Import LangGraph lazily so disabled agent modes keep startup independent."""

    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(AgentGraphState)
    graph.add_node("route", route_node)
    graph.add_node("plan", plan_node)
    graph.add_node("policy", policy_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "plan")
    graph.add_edge("plan", "policy")
    graph.add_edge("policy", END)
    return graph.compile(checkpointer=checkpointer)
