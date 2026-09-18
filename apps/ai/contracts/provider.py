from abc import ABC, abstractmethod

from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.contracts.responses import AIStructuredResult


class AIProvider(ABC):
    """Provider boundary. Implementations must not access Django ORM."""

    name: str

    @abstractmethod
    def generate_structured(
        self, request: AIStructuredRequest
    ) -> AIStructuredResult[dict]:
        """Return parsed structured data, never provider-native JSON."""
