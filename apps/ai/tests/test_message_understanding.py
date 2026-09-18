import json
import logging
from pathlib import Path

import pytest

from apps.ai.application.message_understanding import MessageUnderstandingService
from apps.ai.application.service import AIService
from apps.ai.domain.exceptions import AIInvalidResponseError
from apps.ai.domain.understanding import MessageIntent, MessageUnderstandingInput
from apps.ai.providers.mock import MockAIProvider
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


@pytest.fixture
def examples():
    fixture_path = Path(__file__).parent / "fixtures" / "message_understanding_v1.json"
    return json.loads(fixture_path.read_text())


@pytest.fixture
def provider():
    return MockAIProvider()


def test_baseline_message_understanding_examples(provider, examples):
    service = MessageUnderstandingService(AIService(provider))
    for example in examples:
        provider.register_response(
            task_name="message_understanding", result=example["result"]
        )
        understanding = service.understand(
            MessageUnderstandingInput(current_message=example["input"])
        )
        assert understanding.intent == example["result"]["intent"]
        assert len(understanding.items) == len(example["result"]["items"])


def test_multiple_items_preserve_requested_names_and_normalized_units(provider):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "CREATE_OR_UPDATE_ORDER",
            "confidence": 0.92,
            "items": [
                {
                    "requested_name": "Mayor",
                    "quantity": 2,
                    "unit": "BOTTLE",
                    "notes": None,
                },
                {
                    "requested_name": "riz parfumé",
                    "quantity": 5,
                    "unit": "KG",
                    "notes": None,
                },
            ],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
        },
    )
    service = MessageUnderstandingService(AIService(provider))
    result = service.understand(
        MessageUnderstandingInput(
            current_message="Stp je veux deux Mayor et 5 kilo de riz parfumé"
        )
    )
    assert result.intent == MessageIntent.CREATE_OR_UPDATE_ORDER
    assert [
        (item.requested_name, item.quantity, item.unit) for item in result.items
    ] == [
        ("Mayor", 2, "BOTTLE"),
        ("riz parfumé", 5, "KG"),
    ]


@pytest.mark.parametrize(
    "invalid_item",
    [
        {"requested_name": "Mayor", "quantity": 0, "unit": "BOTTLE", "notes": None},
        {"requested_name": " ", "quantity": 1, "unit": "BOTTLE", "notes": None},
    ],
)
def test_semantically_invalid_items_are_rejected(provider, invalid_item):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "CREATE_OR_UPDATE_ORDER",
            "confidence": 0.8,
            "items": [invalid_item],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
        },
    )
    with pytest.raises(AIInvalidResponseError):
        MessageUnderstandingService(AIService(provider)).understand(
            MessageUnderstandingInput(current_message="x")
        )


def test_forbidden_business_facts_are_rejected_by_strict_schema(provider):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "CREATE_OR_UPDATE_ORDER",
            "confidence": 0.8,
            "items": [
                {
                    "requested_name": "Mayor",
                    "quantity": 2,
                    "unit": "BOTTLE",
                    "notes": None,
                    "product_id": "invented",
                    "price": 1500,
                }
            ],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
        },
    )
    with pytest.raises(AIInvalidResponseError):
        MessageUnderstandingService(AIService(provider)).understand(
            MessageUnderstandingInput(current_message="x")
        )


def test_location_and_interactive_actions_bypass_ai(provider):
    service = MessageUnderstandingService(AIService(provider))
    location = service.understand_normalized(
        NormalizedInboundMessage(
            channel=MessagingChannel.WHATSAPP,
            external_message_id="m1",
            external_user_id="u1",
            message_type=MessageType.LOCATION,
            location=MessageLocation(latitude=4.05, longitude=9.7),
        )
    )
    action = service.understand_normalized(
        NormalizedInboundMessage(
            channel=MessagingChannel.WHATSAPP,
            external_message_id="m2",
            external_user_id="u1",
            message_type=MessageType.INTERACTIVE,
            metadata={"action_id": "order.confirm"},
        )
    )
    assert location.intent == MessageIntent.PROVIDE_LOCATION
    assert action is None


def test_understanding_logs_aggregate_confidence_without_message(provider, caplog):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "GREETING",
            "confidence": 0.9,
            "items": [],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
        },
    )
    caplog.set_level(logging.INFO)
    MessageUnderstandingService(AIService(provider)).understand(
        MessageUnderstandingInput(
            current_message="secret customer message", correlation_id="corr-understand"
        )
    )
    record = next(
        r for r in caplog.records if r.message == "message_understanding_completed"
    )
    assert record.confidence == "0.9"
    assert record.correlation_id == "corr-understand"
    assert "secret customer message" not in caplog.text
