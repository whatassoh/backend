import json
from typing import Any

from django.conf import settings

from apps.ai.contracts.provider import AIProvider
from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.contracts.responses import AIStructuredResult
from apps.ai.domain.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIInvalidResponseError,
    AIProviderError,
    AIRateLimitError,
    AITemporaryError,
    AITimeoutError,
)
from apps.ai.domain.value_objects import AIUsage


class OpenAIProvider(AIProvider):
    """OpenAI Responses API adapter, isolated from the rest of the project."""

    name = "openai"

    def __init__(self, client: Any | None = None) -> None:
        if not settings.OPENAI_API_KEY:
            raise AIConfigurationError(
                "OPENAI_API_KEY is required for the OpenAI provider."
            )
        if not settings.AI_MODEL:
            raise AIConfigurationError("AI_MODEL is required for the OpenAI provider.")
        if settings.AI_TIMEOUT_SECONDS <= 0:
            raise AIConfigurationError("AI_TIMEOUT_SECONDS must be greater than zero.")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise AIConfigurationError(
                    "The openai package is not installed."
                ) from exc
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.client = client

    def generate_structured(
        self, request: AIStructuredRequest
    ) -> AIStructuredResult[dict]:
        try:
            response = self.client.responses.create(
                model=settings.AI_MODEL,
                instructions=request.system_instructions,
                input=_build_input(request),
                text={"format": _response_format(request)},
                timeout=settings.AI_TIMEOUT_SECONDS,
                store=False,
            )
        except Exception as exc:
            raise _map_openai_error(exc) from exc

        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise AIInvalidResponseError(
                "OpenAI returned an empty structured response."
            )
        try:
            data = json.loads(output_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIInvalidResponseError("OpenAI returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise AIInvalidResponseError(
                "OpenAI structured response must be an object."
            )
        return AIStructuredResult(
            data=data,
            provider=self.name,
            model=getattr(response, "model", settings.AI_MODEL),
            usage=_usage_from_response(getattr(response, "usage", None)),
            provider_request_id=getattr(response, "_request_id", None)
            or getattr(response, "id", None),
        )


def _build_input(request: AIStructuredRequest) -> str:
    if not request.conversation_context:
        return request.user_input
    # Callers supply only minimal, explicitly selected context.
    context = json.dumps(dict(request.conversation_context))
    return f"Context:\n{context}\n\nInput:\n{request.user_input}"


def _response_format(request: AIStructuredRequest) -> dict[str, Any]:
    schema = dict(request.response_schema)
    name = schema.get("title") or request.task_name.replace("-", "_")[:64]
    return {"type": "json_schema", "name": name, "schema": schema, "strict": True}


def _usage_from_response(usage: Any) -> AIUsage | None:
    if usage is None:
        return None
    details = getattr(usage, "input_tokens_details", None)
    return AIUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
        cached_input_tokens=getattr(details, "cached_tokens", None),
    )


def _map_openai_error(exc: Exception) -> AIProviderError:
    name = exc.__class__.__name__
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return AIAuthenticationError("OpenAI authentication failed.")
    if name == "RateLimitError":
        return AIRateLimitError("OpenAI rate limit exceeded.")
    if name in {"APITimeoutError", "TimeoutError"}:
        return AITimeoutError("OpenAI request timed out.")
    if name in {"APIConnectionError", "InternalServerError", "APIStatusError"}:
        return AITemporaryError("OpenAI temporarily unavailable.")
    return AIProviderError("OpenAI request failed.")
