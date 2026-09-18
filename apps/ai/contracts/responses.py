from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from apps.ai.domain.value_objects import AIUsage


@dataclass(frozen=True, kw_only=True)
class AIStructuredResult[T]:
    data: T
    provider: str
    model: str | None = None
    usage: AIUsage | None = None
    latency_ms: int | None = None
    provider_request_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
