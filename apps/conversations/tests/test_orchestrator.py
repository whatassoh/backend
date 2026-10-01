from decimal import Decimal
from types import SimpleNamespace

import pytest

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
    assert outcome.response.text.startswith(("Bonjour", "Bonsoir"))
    assert context.processed == ["m-1"]


def test_ai_failure_is_retried_by_the_inbox_and_not_marked_processed():
    context = ContextStub()
    orchestrator = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(error=AITemporaryError("down")),
        context_service=context,
    )
    with pytest.raises(AITemporaryError, match="down"):
        orchestrator.process_message(SimpleNamespace(), message("Je veux du riz"))
    assert context.processed == []


def test_social_out_of_scope_unknown_and_low_confidence_are_non_mutating():
    cases = (
        (MessageIntent.GREETING, Decimal(".99"), "assistant WhatAssoh"),
        (MessageIntent.OUT_OF_SCOPE, Decimal(".99"), "Ce n'est pas un service"),
        (MessageIntent.UNKNOWN, Decimal(".99"), "reformuler"),
        (MessageIntent.CREATE_OR_UPDATE_ORDER, Decimal(".20"), "reformuler"),
    )
    for index, (intent, confidence, expected) in enumerate(cases):
        context = ContextStub()
        app = ConversationalOrchestrator(
            understanding_service=UnderstandingStub(
                UserMessageUnderstanding(intent=intent, confidence=confidence)
            ),
            context_service=context,
        )
        outcome = app.process_message(
            SimpleNamespace(), message("texte", f"cap-{index}")
        )
        assert expected in outcome.response.text
        assert context.processed == [f"cap-{index}"]


def test_order_status_has_authoritative_empty_state():
    context = ContextStub()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(
                intent=MessageIntent.ORDER_STATUS, confidence=Decimal(".99")
            )
        ),
        context_service=context,
    )
    outcome = app.process_message(SimpleNamespace(), message("Où en est ma commande ?"))
    assert "aucune commande" in outcome.response.text
    assert outcome.metadata["authoritative"] is True


def test_bare_confirmation_is_non_mutating_outside_review_context():
    context = ContextStub()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(
                intent=MessageIntent.CONFIRM, confidence=Decimal(".99")
            )
        ),
        context_service=context,
    )

    outcome = app.process_message(SimpleNamespace(), message("Oui"))

    assert outcome.status == ConversationalOutcomeStatus.PROCESSED
    assert "après son récapitulatif" in outcome.response.text


def test_help_fallback_distinguishes_platform_from_assistant():
    context = ContextStub()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(
                intent=MessageIntent.HELP, confidence=Decimal(".99")
            )
        ),
        context_service=context,
    )

    outcome = app.process_message(
        SimpleNamespace(), message("WhatAssoh, c'est toi ?")
    )

    assert "toute la plateforme" in outcome.response.text
    assert "assistant conversationnel" in outcome.response.text


def test_unexpected_orchestration_error_is_logged_and_reraised(caplog):
    caplog.set_level("INFO")
    context = ContextStub()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(), context_service=context
    )
    app._process = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("programming error")
    )

    with pytest.raises(RuntimeError, match="programming error"):
        app.process_message(
            SimpleNamespace(), message("secret message"), correlation_id="corr-6"
        )

    record = next(
        record
        for record in caplog.records
        if record.message == "conversation_orchestration_unexpected"
    )
    assert record.correlation_id == "corr-6"
    assert "secret message" not in record.getMessage()


def test_turn_completion_log_has_safe_correlation_fields(caplog):
    caplog.set_level("INFO")
    context = ContextStub()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingStub(
            UserMessageUnderstanding(
                intent=MessageIntent.GREETING, confidence=Decimal(".99")
            )
        ),
        context_service=context,
    )

    app.process_message(
        SimpleNamespace(), message("private greeting"), correlation_id="corr-7"
    )

    record = next(
        record
        for record in caplog.records
        if record.message == "conversation_turn_completed"
    )
    assert record.correlation_id == "corr-7"
    assert record.channel == MessagingChannel.WHATSAPP
    assert record.action_id_present is False
    assert "private greeting" not in record.getMessage()
