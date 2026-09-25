"""Runtime policy for AI capabilities.

This module is the single read boundary for AI feature flags. Keeping the
dependency rules here prevents a deployment from accidentally enabling a
write-capable agent while its parent capabilities are disabled.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True, slots=True)
class AIRuntimePolicy:
    """Resolved, internally consistent AI switches for one process."""

    ai_enabled: bool
    emergency_stop: bool
    agent_enabled: bool
    agent_shadow_enabled: bool
    tools_enabled: bool
    write_tools_enabled: bool
    memory_enabled: bool
    conversation_memory_enabled: bool
    memory_summary_enabled: bool
    natural_composer_enabled: bool
    natural_composer_shadow_enabled: bool
    style_adaptation_enabled: bool
    rag_enabled: bool
    rag_shadow_enabled: bool
    rag_semantic_enabled: bool
    voice_enabled: bool
    decision_observability_enabled: bool

    @property
    def model_calls_enabled(self) -> bool:
        """Return whether any production model call is currently allowed."""

        return self.ai_enabled and not self.emergency_stop


def get_ai_runtime_policy() -> AIRuntimePolicy:
    """Resolve flags conservatively and enforce parent-child dependencies.

    A child capability cannot bypass its parent. In particular, write tools
    require the agent and tool registry, while all model-backed capabilities
    are disabled by the emergency stop.
    """

    ai_enabled = bool(getattr(settings, "AI_ENABLED", False))
    emergency_stop = bool(getattr(settings, "AI_EMERGENCY_STOP", False))
    model_calls_enabled = ai_enabled and not emergency_stop
    agent_enabled = model_calls_enabled and bool(
        getattr(settings, "AI_AGENT_ENABLED", False)
    )
    tools_enabled = agent_enabled and bool(
        getattr(settings, "AI_AGENT_TOOLS_ENABLED", False)
    )

    return AIRuntimePolicy(
        ai_enabled=ai_enabled,
        emergency_stop=emergency_stop,
        agent_enabled=agent_enabled,
        agent_shadow_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_AGENT_SHADOW_ENABLED", False)),
        tools_enabled=tools_enabled,
        write_tools_enabled=tools_enabled
        and bool(getattr(settings, "AI_AGENT_WRITE_TOOLS_ENABLED", False)),
        memory_enabled=agent_enabled
        and bool(getattr(settings, "AI_AGENT_MEMORY_ENABLED", False)),
        conversation_memory_enabled=bool(
            getattr(settings, "AI_CONVERSATION_MEMORY_ENABLED", False)
        ),
        memory_summary_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_CONVERSATION_MEMORY_ENABLED", False))
        and bool(getattr(settings, "AI_MEMORY_SUMMARY_ENABLED", False)),
        natural_composer_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_NATURAL_COMPOSER_ENABLED", False)),
        natural_composer_shadow_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_NATURAL_COMPOSER_SHADOW_ENABLED", False)),
        style_adaptation_enabled=bool(
            getattr(settings, "AI_STYLE_ADAPTATION_ENABLED", False)
        ),
        rag_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_RAG_ENABLED", False)),
        rag_shadow_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_RAG_SHADOW_ENABLED", False)),
        rag_semantic_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_RAG_SEMANTIC_ENABLED", False)),
        voice_enabled=model_calls_enabled
        and bool(getattr(settings, "AI_VOICE_ENABLED", False)),
        decision_observability_enabled=bool(
            getattr(settings, "AI_DECISION_OBSERVABILITY_ENABLED", True)
        ),
    )
