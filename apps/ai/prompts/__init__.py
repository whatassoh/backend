"""Versioned prompt references live here; prompt content remains caller-owned."""

from apps.ai.domain.value_objects import PromptReference

from .message_understanding import MESSAGE_UNDERSTANDING_PROMPT

__all__ = ["MESSAGE_UNDERSTANDING_PROMPT", "PromptReference"]
