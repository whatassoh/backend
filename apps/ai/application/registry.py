from collections.abc import Callable

from django.conf import settings

from apps.ai.contracts.provider import AIProvider
from apps.ai.domain.exceptions import AIConfigurationError

ProviderFactory = Callable[[], AIProvider]


def get_ai_provider() -> AIProvider:
    if not getattr(settings, "AI_ENABLED", False):
        raise AIConfigurationError("AI is disabled (AI_ENABLED=false).")
    provider_name = getattr(settings, "AI_PROVIDER", "").lower()
    if provider_name == "mock":
        from apps.ai.providers.mock import MockAIProvider

        return MockAIProvider()
    if provider_name == "openai":
        from apps.ai.providers.openai import OpenAIProvider

        return OpenAIProvider()
    raise AIConfigurationError(f"Unsupported AI provider: {provider_name}")
