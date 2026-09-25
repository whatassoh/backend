"""Small deterministic bridge between language understanding and routing."""

from dataclasses import dataclass
from enum import StrEnum

from apps.ai.domain.understanding import MessageIntent


class CapabilityStatus(StrEnum):
    SOCIAL = "SOCIAL"
    SUPPORTED = "SUPPORTED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, kw_only=True)
class CapabilityDecision:
    status: CapabilityStatus
    name: str | None = None


def classify_capability(intent: MessageIntent) -> CapabilityDecision:
    """Decide supportability; this never authorizes a business mutation."""
    if intent == MessageIntent.GREETING:
        return CapabilityDecision(status=CapabilityStatus.SOCIAL, name="greeting")
    if intent == MessageIntent.OUT_OF_SCOPE:
        return CapabilityDecision(status=CapabilityStatus.OUT_OF_SCOPE)
    if intent == MessageIntent.UNKNOWN:
        return CapabilityDecision(status=CapabilityStatus.UNKNOWN)
    if intent == MessageIntent.ORDER_HISTORY:
        return CapabilityDecision(
            status=CapabilityStatus.SUPPORTED, name="order_history"
        )
    if intent == MessageIntent.ORDER_RESUME:
        return CapabilityDecision(
            status=CapabilityStatus.SUPPORTED, name="order_resume"
        )
    if intent == MessageIntent.ORDER_STATUS:
        return CapabilityDecision(
            status=CapabilityStatus.NOT_IMPLEMENTED, name="order_status"
        )
    return CapabilityDecision(
        status=CapabilityStatus.SUPPORTED, name=intent.value.lower()
    )


SOCIAL_RESPONSE = "Bonjour 👋 Je vais bien, merci ! Comment puis-je vous aider ?"
OUT_OF_SCOPE_RESPONSE = (
    "Ce n'est pas un service proposé par Whatassoh. Je peux vous aider à acheter "
    "des produits, gérer une boutique ou effectuer des livraisons."
)
UNKNOWN_RESPONSE = "Je n'ai pas bien compris votre demande. Pouvez-vous la reformuler ?"
NOT_IMPLEMENTED_RESPONSES = {
    "order_status": (
        "J'ai compris votre demande de suivi de commande, mais cette fonctionnalité "
        "n'est pas encore disponible."
    ),
}
