import logging
import time

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from apps.ai.application.registry import get_ai_provider
from apps.ai.contracts.provider import AIProvider
from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.contracts.responses import AIStructuredResult
from apps.ai.domain.exceptions import AIInvalidResponseError

logger = logging.getLogger(__name__)


class AIService:
    """Thin application façade: resolution, validation and safe observability."""

    def __init__(self, provider: AIProvider | None = None) -> None:
        self.provider = provider

    def generate_structured(
        self, request: AIStructuredRequest
    ) -> AIStructuredResult[dict]:
        provider = self.provider or get_ai_provider()
        started = time.monotonic()
        try:
            Draft202012Validator.check_schema(dict(request.response_schema))
            result = provider.generate_structured(request)
            Draft202012Validator(dict(request.response_schema)).validate(result.data)
        except (SchemaError, ValidationError) as exc:
            self._log(request, provider.name, False, started, "invalid_response")
            raise AIInvalidResponseError(
                "AI response does not match the requested schema."
            ) from exc
        except AIInvalidResponseError:
            self._log(request, provider.name, False, started, "invalid_response")
            raise
        except Exception as exc:
            self._log(request, provider.name, False, started, exc.__class__.__name__)
            raise
        latency_ms = round((time.monotonic() - started) * 1000)
        result = AIStructuredResult(
            data=result.data,
            provider=result.provider,
            model=result.model,
            usage=result.usage,
            latency_ms=result.latency_ms
            if result.latency_ms is not None
            else latency_ms,
            provider_request_id=result.provider_request_id,
            metadata=result.metadata,
        )
        self._log(request, result.provider, True, started, None, result)
        return result

    @staticmethod
    def _log(request, provider, success, started, error_category, result=None) -> None:
        # Deliberately exclude user input, context, arbitrary metadata, and secrets.
        logger.info(
            "ai_structured_generation",
            extra={
                "task_name": request.task_name,
                "provider": provider,
                "model": getattr(result, "model", None),
                "prompt_version": request.prompt.identifier if request.prompt else None,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "success": success,
                "error_category": error_category,
                "correlation_id": request.correlation_id,
                "input_tokens": getattr(
                    getattr(result, "usage", None), "input_tokens", None
                ),
                "output_tokens": getattr(
                    getattr(result, "usage", None), "output_tokens", None
                ),
            },
        )
