from abc import ABC, abstractmethod
from dataclasses import dataclass

from apps.messaging.domain.dtos import NormalizedOutboundMessage, ProviderSendResult


@dataclass(frozen=True, kw_only=True)
class ProviderCapabilities:
    text: bool = True
    location: bool = False
    interactive: bool = False
    media: bool = False


class MessagingProvider(ABC):
    capabilities = ProviderCapabilities()

    @abstractmethod
    def send(self, message: NormalizedOutboundMessage) -> ProviderSendResult:
        """Send a normalized message and return a provider result."""
