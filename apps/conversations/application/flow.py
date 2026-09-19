"""Composition root for inbound customer flow, deliberately called after ingest."""

from apps.ai.application.message_understanding import MessageUnderstandingService
from apps.ai.application.registry import get_ai_provider
from apps.ai.application.service import AIService
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from django.conf import settings


class _UnavailableUnderstandingService:
    def understand_normalized(self, *_args, **_kwargs):
        return None


def process_inbound_conversation(conversation, message, *, correlation_id=None):
    """External/AI work occurs after the inbound persistence transaction commits."""
    understanding_service = (
        MessageUnderstandingService(AIService(get_ai_provider()))
        if getattr(settings, "AI_ENABLED", False)
        else _UnavailableUnderstandingService()
    )
    orchestrator = ConversationalOrchestrator(
        understanding_service=understanding_service
    )
    return orchestrator.process_message(
        conversation, message, correlation_id=correlation_id
    )
