"""Composition root for inbound customer flow, deliberately called after ingest."""

from decimal import Decimal

from apps.ai.application.message_understanding import MessageUnderstandingService
from apps.ai.application.registry import get_ai_provider
from apps.ai.application.runtime import get_ai_runtime_policy
from apps.ai.application.seller_catalog_understanding import (
    SellerCatalogCommand,
    SellerCatalogIntent,
    SellerCatalogUnderstandingService,
)
from apps.ai.application.service import AIService
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.seller_catalog_conversation import (
    SellerCatalogConversationService,
)


class _UnavailableUnderstandingService:
    def understand_normalized(self, *_args, **_kwargs):
        return None


class _UnavailableSellerCatalogUnderstandingService:
    """Keeps deterministic seller actions available while the AI provider is off."""

    def understand(self, *_args, **_kwargs):
        return SellerCatalogCommand(
            intent=SellerCatalogIntent.UNKNOWN,
            confidence=Decimal("0"),
        )


def process_inbound_conversation(conversation, message, *, correlation_id=None):
    """External/AI work occurs after the inbound persistence transaction commits."""
    runtime_policy = get_ai_runtime_policy()
    understanding_service = (
        MessageUnderstandingService(AIService(get_ai_provider()))
        if runtime_policy.model_calls_enabled
        else _UnavailableUnderstandingService()
    )
    seller_catalog_service = SellerCatalogConversationService(
        understanding_service=(
            SellerCatalogUnderstandingService(AIService(get_ai_provider()))
            if runtime_policy.model_calls_enabled
            else _UnavailableSellerCatalogUnderstandingService()
        )
    )
    orchestrator = ConversationalOrchestrator(
        understanding_service=understanding_service,
        seller_catalog_conversation_service=seller_catalog_service,
    )
    return orchestrator.process_message(
        conversation, message, correlation_id=correlation_id
    )
