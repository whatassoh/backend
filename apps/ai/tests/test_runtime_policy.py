import json
from io import StringIO

from django.core.management import call_command
from django.test import override_settings

from apps.ai.application.runtime import get_ai_runtime_policy


@override_settings(
    AI_ENABLED=True,
    AI_EMERGENCY_STOP=False,
    AI_AGENT_ENABLED=True,
    AI_AGENT_SHADOW_ENABLED=True,
    AI_AGENT_TOOLS_ENABLED=True,
    AI_AGENT_WRITE_TOOLS_ENABLED=True,
    AI_AGENT_MEMORY_ENABLED=True,
    AI_CONVERSATION_MEMORY_ENABLED=True,
    AI_MEMORY_SUMMARY_ENABLED=True,
    AI_NATURAL_COMPOSER_ENABLED=True,
    AI_NATURAL_COMPOSER_SHADOW_ENABLED=True,
    AI_STYLE_ADAPTATION_ENABLED=True,
    AI_RAG_ENABLED=True,
    AI_RAG_SHADOW_ENABLED=True,
    AI_RAG_SEMANTIC_ENABLED=True,
    AI_VOICE_ENABLED=True,
)
def test_runtime_policy_enables_explicit_capabilities():
    policy = get_ai_runtime_policy()

    assert policy.model_calls_enabled is True
    assert policy.agent_enabled is True
    assert policy.agent_shadow_enabled is True
    assert policy.tools_enabled is True
    assert policy.write_tools_enabled is True
    assert policy.memory_enabled is True
    assert policy.conversation_memory_enabled is True
    assert policy.memory_summary_enabled is True
    assert policy.natural_composer_enabled is True
    assert policy.natural_composer_shadow_enabled is True
    assert policy.style_adaptation_enabled is True
    assert policy.rag_enabled is True
    assert policy.rag_shadow_enabled is True
    assert policy.rag_semantic_enabled is True
    assert policy.voice_enabled is True


@override_settings(
    AI_ENABLED=True,
    AI_EMERGENCY_STOP=True,
    AI_AGENT_ENABLED=True,
    AI_AGENT_SHADOW_ENABLED=True,
    AI_AGENT_TOOLS_ENABLED=True,
    AI_AGENT_WRITE_TOOLS_ENABLED=True,
    AI_AGENT_MEMORY_ENABLED=True,
    AI_CONVERSATION_MEMORY_ENABLED=True,
    AI_MEMORY_SUMMARY_ENABLED=True,
    AI_NATURAL_COMPOSER_ENABLED=True,
    AI_NATURAL_COMPOSER_SHADOW_ENABLED=True,
    AI_RAG_ENABLED=True,
    AI_RAG_SHADOW_ENABLED=True,
    AI_RAG_SEMANTIC_ENABLED=True,
    AI_VOICE_ENABLED=True,
)
def test_emergency_stop_fails_closed_for_every_model_capability():
    policy = get_ai_runtime_policy()

    assert policy.model_calls_enabled is False
    assert policy.agent_enabled is False
    assert policy.agent_shadow_enabled is False
    assert policy.tools_enabled is False
    assert policy.write_tools_enabled is False
    assert policy.memory_enabled is False
    assert policy.memory_summary_enabled is False
    assert policy.natural_composer_enabled is False
    assert policy.natural_composer_shadow_enabled is False
    assert policy.rag_enabled is False
    assert policy.rag_shadow_enabled is False
    assert policy.rag_semantic_enabled is False
    assert policy.voice_enabled is False


@override_settings(
    AI_ENABLED=True,
    AI_EMERGENCY_STOP=False,
    AI_AGENT_ENABLED=False,
    AI_AGENT_TOOLS_ENABLED=True,
    AI_AGENT_WRITE_TOOLS_ENABLED=True,
    AI_AGENT_MEMORY_ENABLED=True,
)
def test_child_capabilities_cannot_bypass_disabled_agent():
    policy = get_ai_runtime_policy()

    assert policy.agent_enabled is False
    assert policy.tools_enabled is False
    assert policy.write_tools_enabled is False
    assert policy.memory_enabled is False


@override_settings(
    AI_ENABLED=True,
    AI_EMERGENCY_STOP=False,
    AI_AGENT_ENABLED=True,
    AI_AGENT_SHADOW_ENABLED=False,
    AI_AGENT_TOOLS_ENABLED=True,
    AI_AGENT_WRITE_TOOLS_ENABLED=False,
    AI_AGENT_MEMORY_ENABLED=True,
    AI_CONVERSATION_MEMORY_ENABLED=True,
    AI_MEMORY_SUMMARY_ENABLED=True,
    AI_NATURAL_COMPOSER_ENABLED=True,
    AI_NATURAL_COMPOSER_SHADOW_ENABLED=False,
    AI_STYLE_ADAPTATION_ENABLED=True,
    AI_CONTEXT_ENGINE_ENABLED=True,
    AI_RAG_ENABLED=True,
    AI_RAG_SHADOW_ENABLED=False,
    AI_RAG_SEMANTIC_ENABLED=True,
    AI_RAG_ROLLOUT_PERCENT=100,
    AI_VOICE_ENABLED=False,
)
def test_runtime_status_accepts_complete_lot6_profile():
    stdout = StringIO()

    call_command("ai_runtime_status", "--require-lot6-active", stdout=stdout)

    first_line = stdout.getvalue().splitlines()[0]
    status = json.loads(first_line)
    assert status["graph_version"] == "read-only-v2"
    assert status["write_tools_enabled"] is False
    assert status["rag_rollout_percent"] == 100
