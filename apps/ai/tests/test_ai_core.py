import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import override_settings

from apps.ai.application.registry import get_ai_provider
from apps.ai.application.service import AIService
from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.domain.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIInvalidResponseError,
    AIRateLimitError,
    AITemporaryError,
    AITimeoutError,
)
from apps.ai.domain.value_objects import PromptReference
from apps.ai.providers.mock import MockAIProvider
from apps.ai.providers.openai import OpenAIProvider

SCHEMA = {
    "title": "test_result",
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def request(**kwargs):
    values = {
        "task_name": "test_task",
        "system_instructions": "Return an object.",
        "user_input": "hello",
        "response_schema": SCHEMA,
    }
    values.update(kwargs)
    return AIStructuredRequest(**values)


def test_mock_provider_is_deterministic_and_validated():
    provider = MockAIProvider()
    provider.register_response(task_name="test_task", result={"answer": "ok"})

    result = AIService(provider).generate_structured(request())

    assert result.data == {"answer": "ok"}
    assert result.provider == "mock"
    assert result.latency_ms is not None


def test_invalid_provider_result_is_controlled_error():
    provider = MockAIProvider()
    provider.register_response(task_name="test_task", result={"unexpected": True})

    with pytest.raises(AIInvalidResponseError):
        AIService(provider).generate_structured(request())


@override_settings(AI_ENABLED=False)
def test_disabled_ai_cannot_resolve_default_provider():
    with pytest.raises(AIConfigurationError, match="disabled"):
        get_ai_provider()


@override_settings(AI_ENABLED=True, AI_PROVIDER="unknown")
def test_unknown_provider_is_explicit_configuration_error():
    with pytest.raises(AIConfigurationError, match="Unsupported"):
        get_ai_provider()


@override_settings(AI_ENABLED=True, AI_PROVIDER="mock")
def test_registry_returns_mock_provider():
    assert isinstance(get_ai_provider(), MockAIProvider)


@override_settings(OPENAI_API_KEY="", AI_MODEL="configured")
def test_openai_requires_api_key():
    with pytest.raises(AIConfigurationError, match="OPENAI_API_KEY"):
        OpenAIProvider()


@pytest.mark.parametrize(
    ("exception_name", "expected"),
    [
        ("AuthenticationError", AIAuthenticationError),
        ("RateLimitError", AIRateLimitError),
        ("APITimeoutError", AITimeoutError),
        ("APIConnectionError", AITemporaryError),
    ],
)
@override_settings(OPENAI_API_KEY="test-key", AI_MODEL="configured")
def test_openai_maps_provider_errors(exception_name, expected):
    error_type = type(exception_name, (Exception,), {})
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(error_type())
        )
    )

    with pytest.raises(expected):
        OpenAIProvider(client=client).generate_structured(request())


@override_settings(
    OPENAI_API_KEY="test-key", AI_MODEL="configured", AI_TIMEOUT_SECONDS=7
)
def test_openai_uses_structured_outputs_and_extracts_usage():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_text=json.dumps({"answer": "ok"}),
            model="configured",
            id="resp-1",
            usage=SimpleNamespace(
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
                input_tokens_details=SimpleNamespace(cached_tokens=1),
            ),
        )

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    result = AIService(OpenAIProvider(client=client)).generate_structured(request())

    assert captured["timeout"] == 7
    assert captured["store"] is False
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["strict"] is True
    assert result.usage.total_tokens == 5
    assert result.usage.cached_input_tokens == 1
    assert result.provider_request_id == "resp-1"


def test_logs_are_correlated_but_do_not_include_user_content(caplog):
    provider = MockAIProvider()
    provider.register_response(task_name="test_task", result={"answer": "ok"})
    secret_like_input = "phone +237 699000000 and secret-token"
    caplog.set_level(logging.INFO)

    AIService(provider).generate_structured(
        request(
            user_input=secret_like_input,
            correlation_id="corr-ai-1",
            prompt=PromptReference(name="intent_extraction", version="v1"),
        )
    )

    record = next(r for r in caplog.records if r.message == "ai_structured_generation")
    assert record.correlation_id == "corr-ai-1"
    assert record.prompt_version == "intent_extraction:v1"
    assert secret_like_input not in caplog.text


def test_ai_core_has_no_business_or_channel_dependencies():
    ai_root = Path(__file__).resolve().parents[1]
    forbidden = ("apps.orders", "apps.catalog", "apps.whatsapp", "apps.telegram")
    source = "\n".join(
        path.read_text() for path in ai_root.rglob("*.py") if "tests" not in path.parts
    )

    assert not any(dependency in source for dependency in forbidden)
