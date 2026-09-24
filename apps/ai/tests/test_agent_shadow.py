from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.ai.application.agent_graph import plan_node, policy_node, route_node
from apps.ai.application.shadow import run_agent_shadow_safely
from apps.ai.application.tools import (
    ToolContext,
    ToolPolicyError,
    build_business_tool_registry,
)
from apps.ai.domain.agent_state import AgentRoute
from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.ai.infrastructure.models import AgentShadowRun
from apps.conversations.application.orchestrator import ResponsePlan
from apps.conversations.application.services import get_or_create_active_conversation
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


def test_bounded_route_plan_and_policy_for_cancellation(settings):
    settings.AI_AGENT_MAX_PLAN_STEPS = 4
    state = {
        "intent": MessageIntent.CANCEL.value,
        "plan": [],
    }
    state.update(route_node(state))
    state.update(plan_node(state))
    state.update(policy_node(state))

    assert state["route"] == AgentRoute.INTERRUPT
    assert state["plan"] == [{"tool": "cancel_task", "arguments": {}}]
    assert state["risk_level"] == "R3"
    assert state["requires_confirmation"] is True
    assert state["policy_decision"] == "SHADOW_ONLY"


def test_shadow_registry_rejects_every_mutating_tool():
    registry = build_business_tool_registry()
    context = ToolContext(user=object(), conversation=object(), shadow_mode=True)

    with pytest.raises(ToolPolicyError, match="forbids mutating"):
        registry.execute("cancel_task", {}, context)


def test_registry_manifest_contains_policy_metadata(monkeypatch, settings):
    settings.AI_TOOL_MANIFEST_CACHE_SECONDS = 300
    monkeypatch.setattr(
        "apps.ai.application.tools.EphemeralCache.get", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.ai.application.tools.EphemeralCache.set", lambda *_args, **_kwargs: None
    )
    manifest = build_business_tool_registry().manifest()
    cancel = next(item for item in manifest if item["name"] == "cancel_order")

    assert cancel["risk"] == "R3"
    assert cancel["confirmation_required"] is True
    assert cancel["permission"] == "orders.cancel_own"
    assert len(manifest) >= 25
    assert next(item for item in manifest if item["name"] == "get_response_style")[
        "executable"
    ] is True
    assert cancel["executable"] is False


def test_registry_executes_authorized_read_and_rejects_cross_user(settings):
    settings.AI_TOOL_MAX_RESULT_BYTES = 65536
    registry = build_business_tool_registry()
    user = SimpleNamespace(id="user-1")
    conversation = SimpleNamespace(user_id="user-1", context={})

    result = registry.execute(
        "get_current_task",
        {},
        ToolContext(user=user, conversation=conversation, shadow_mode=True),
    )
    assert result.status == "OK"

    with pytest.raises(ToolPolicyError, match="not authorized"):
        registry.execute(
            "get_current_task",
            {},
            ToolContext(
                user=SimpleNamespace(id="user-2"),
                conversation=conversation,
                shadow_mode=True,
            ),
        )


class UnderstandingStub:
    def understand_normalized(self, *_args, **_kwargs):
        return UserMessageUnderstanding(
            intent=MessageIntent.GREETING,
            confidence=Decimal(".99"),
        )


def conversation():
    user = User.objects.create_user(whatsapp_phone="+237699000111")
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=user.whatsapp_phone,
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


@pytest.mark.django_db
def test_shadow_failure_is_persisted_without_changing_legacy_outcome(monkeypatch):
    convo = conversation()
    message = NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id="shadow-1",
        external_user_id=convo.channel_identity.external_user_id,
        message_type=MessageType.TEXT,
        text="Bonjour",
    )
    legacy = SimpleNamespace(response=ResponsePlan(text="Bonjour"), status="PROCESSED")
    monkeypatch.setattr(
        "apps.ai.application.shadow.postgres_saver",
        lambda: (_ for _ in ()).throw(RuntimeError("checkpoint unavailable")),
    )

    run = run_agent_shadow_safely(
        conversation=convo,
        message=message,
        legacy_outcome=legacy,
        understanding_service=UnderstandingStub(),
        correlation_id="shadow-correlation",
    )

    assert legacy.response.text == "Bonjour"
    assert run.status == "FAILED"
    assert run.error_category == "RuntimeError"
    assert AgentShadowRun.objects.count() == 1
