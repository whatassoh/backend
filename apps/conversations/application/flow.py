"""Composition root for inbound customer flow, deliberately called after ingest."""

import logging
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

logger = logging.getLogger(__name__)


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
    if runtime_policy.style_adaptation_enabled and message.text:
        from apps.conversations.application.style import observe_writing_style

        try:
            observe_writing_style(conversation, message.text)
        except Exception:
            logger.exception("conversation_style_observation_failed")
    if runtime_policy.conversation_memory_enabled and message.text:
        from apps.conversations.application.memory import ConversationMemoryService

        try:
            source = (
                conversation.messages.filter(
                    message_event__external_message_id=message.external_message_id
                )
                .values_list("id", flat=True)
                .first()
            )
            ConversationMemoryService().remember_explicit_preferences(
                conversation,
                message.text,
                source_message_id=str(source) if source else None,
            )
        except Exception:
            logger.exception("conversation_explicit_memory_failed")
    ai_service = (
        AIService(get_ai_provider()) if runtime_policy.model_calls_enabled else None
    )
    understanding_service = (
        MessageUnderstandingService(ai_service)
        if ai_service is not None
        else _UnavailableUnderstandingService()
    )
    seller_catalog_service = SellerCatalogConversationService(
        understanding_service=(
            SellerCatalogUnderstandingService(ai_service)
            if ai_service is not None
            else _UnavailableSellerCatalogUnderstandingService()
        )
    )
    from apps.knowledge.application.rollout import rag_enabled_for

    rag_active = runtime_policy.rag_enabled and rag_enabled_for(conversation.user_id)
    knowledge_service = _knowledge_service(runtime_policy, ai_service, rag_active)
    active_agent_service = None
    if runtime_policy.agent_enabled and runtime_policy.tools_enabled:
        from apps.ai.application.active_agent import ActiveReadAgentService

        active_agent_service = ActiveReadAgentService()
    orchestrator = ConversationalOrchestrator(
        understanding_service=understanding_service,
        seller_catalog_conversation_service=seller_catalog_service,
        knowledge_answer_service=knowledge_service,
        knowledge_answer_enabled=rag_active,
        knowledge_answer_shadow=runtime_policy.rag_shadow_enabled,
        active_agent_service=active_agent_service,
    )
    outcome = orchestrator.process_message(
        conversation, message, correlation_id=correlation_id
    )
    if runtime_policy.agent_shadow_enabled:
        from apps.ai.application.shadow import run_agent_shadow_safely

        run_agent_shadow_safely(
            conversation=conversation,
            message=message,
            legacy_outcome=outcome,
            understanding_service=understanding_service,
            correlation_id=correlation_id,
        )
    if (
        runtime_policy.natural_composer_enabled
        or runtime_policy.natural_composer_shadow_enabled
    ):
        from apps.conversations.application.composition import compose_outcome_safely

        try:
            outcome = compose_outcome_safely(
                conversation=conversation,
                outcome=outcome,
                enabled=runtime_policy.natural_composer_enabled,
                shadow=runtime_policy.natural_composer_shadow_enabled,
                correlation_id=correlation_id,
            )
        except Exception:
            logger.exception("natural_response_composition_boundary_failed")
    if runtime_policy.memory_summary_enabled:
        from apps.conversations.tasks import refresh_conversation_memory

        try:
            refresh_conversation_memory.delay(str(conversation.id), correlation_id)
        except Exception:
            logger.exception("conversation_memory_enqueue_failed")
    return outcome


def _knowledge_service(runtime_policy, ai_service, rag_active):
    if ai_service is None or not (rag_active or runtime_policy.rag_shadow_enabled):
        return None
    try:
        from apps.knowledge.application.answers import KnowledgeAnswerService
        from apps.knowledge.application.embeddings import get_embedding_provider
        from apps.knowledge.application.retrieval import HybridKnowledgeRetriever

        embedding_provider = (
            get_embedding_provider() if runtime_policy.rag_semantic_enabled else None
        )
        return KnowledgeAnswerService(
            HybridKnowledgeRetriever(embedding_provider=embedding_provider), ai_service
        )
    except Exception:
        logger.exception("knowledge_service_initialization_failed")
        return None
