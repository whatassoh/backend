from decimal import Decimal
from types import SimpleNamespace

from apps.ai.domain.exceptions import AITemporaryError
from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.conversations.application.orchestrator import (
    ConversationalOrchestrator,
    ConversationalOutcomeStatus,
)
from apps.conversations.domain.context import ConversationContext
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


class ContextStub:
    def __init__(self):
        self.context = ConversationContext()
        self.processed = []

    def get_context(self, conversation):
        return self.context

    def mark_message_processed(self, conversation, message_id):
        self.processed.append(message_id)
        return True


class UnderstandingStub:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def understand_normalized(self, message, **kwargs):
        if self.error:
            raise self.error
        return self.result


def message(text, message_id="m-1"):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=message_id,
        external_user_id="u-1",
        message_type=MessageType.TEXT,
        text=text,
    )


def test_greeting_does_not_create_order_or_call_catalog():
    context = ContextStub()
    orchestrator = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(
                intent=MessageIntent.GREETING, confidence=Decimal("0.99")
            )
        ),
        context_service=context,
    )
    outcome = orchestrator.process_message(SimpleNamespace(), message("Bonjour"))
    assert outcome.status == ConversationalOutcomeStatus.PROCESSED
    assert "Bonjour" in outcome.response.text
    assert context.processed == ["m-1"]


def test_ai_failure_is_recoverable_and_not_marked_processed():
    context = ContextStub()
    orchestrator = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(error=AITemporaryError("down")),
        context_service=context,
    )
    outcome = orchestrator.process_message(SimpleNamespace(), message("Je veux du riz"))
    assert outcome.status == ConversationalOutcomeStatus.RECOVERABLE_FAILURE
    assert context.processed == []
