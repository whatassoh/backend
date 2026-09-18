from collections.abc import Mapping
from typing import Any

from apps.ai.contracts.provider import AIProvider
from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.contracts.responses import AIStructuredResult
from apps.ai.domain.exceptions import AIInvalidResponseError


class MockAIProvider(AIProvider):
    """In-memory deterministic provider for tests and local development."""

    name = "mock"

    def __init__(self) -> None:
        self._responses: dict[str, AIStructuredResult[dict] | Exception] = {}

    def register_response(
        self,
        *,
        task_name: str,
        result: Mapping[str, Any] | AIStructuredResult[dict] | Exception,
    ) -> None:
        if isinstance(result, Mapping):
            result = AIStructuredResult(
                data=dict(result), provider=self.name, model="mock"
            )
        self._responses[task_name] = result

    def generate_structured(
        self, request: AIStructuredRequest
    ) -> AIStructuredResult[dict]:
        response = self._responses.get(request.task_name)
        if response is None:
            raise AIInvalidResponseError(
                f"No mock AI response registered for task: {request.task_name}"
            )
        if isinstance(response, Exception):
            raise response
        return response
