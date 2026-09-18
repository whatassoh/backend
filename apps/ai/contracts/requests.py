from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from apps.ai.domain.value_objects import PromptReference


@dataclass(frozen=True, kw_only=True)
class AIStructuredRequest:
    """Explicit, serializable input at the AI boundary; never ORM objects."""

    task_name: str
    system_instructions: str
    user_input: str
    response_schema: Mapping[str, Any]
    conversation_context: Mapping[str, Any] | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    prompt: PromptReference | None = None
