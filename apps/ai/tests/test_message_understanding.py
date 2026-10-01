import json
import logging
from pathlib import Path

import pytest

from apps.ai.application.message_understanding import MessageUnderstandingService
from apps.ai.application.service import AIService
from apps.ai.domain.exceptions import AIInvalidResponseError
from apps.ai.domain.understanding import (
    InteractionMode,
    JourneyGoal,
    MessageIntent,
    MessageUnderstandingInput,
    TaskTarget,
)
from apps.ai.prompts.message_understanding import SYSTEM_INSTRUCTIONS
from apps.ai.providers.mock import MockAIProvider
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


@pytest.fixture
def examples():
    fixture_path = Path(__file__).parent / "fixtures" / "message_understanding_v1.json"
    examples = json.loads(fixture_path.read_text())
    for example in examples:
        for item in example["result"].get("items", []):
            item.setdefault("variant_hint", None)
        example["result"].update(
            secondary_intents=[],
            interaction_mode="ANSWER",
            action_requested=False,
            journey_goal="NONE",
            standalone_question=None,
            task_target="NONE",
            order_action=None,
            target_reference=None,
            replacement=None,
        )
    return examples


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


def test_prompt_routes_platform_catalog_and_social_questions_explicitly():
    assert "Questions about WhatAssoh" in SYSTEM_INSTRUCTIONS
    assert "CATALOG_BROWSE" in SYSTEM_INSTRUCTIONS
    assert "comment tu vas" in SYSTEM_INSTRUCTIONS
    assert "ANSWER_AND_ACT" in SYSTEM_INSTRUCTIONS
    assert "RECEIVE_DELIVERY" in SYSTEM_INSTRUCTIONS
    assert "never DELIVER" in SYSTEM_INSTRUCTIONS
    assert "leave items empty" in SYSTEM_INSTRUCTIONS
    assert "category/concept" in SYSTEM_INSTRUCTIONS
    assert "positive quantity" in SYSTEM_INSTRUCTIONS


def test_compound_turn_contract_preserves_information_and_action(provider):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "HELP",
            "secondary_intents": ["CANCEL"],
            "interaction_mode": "ANSWER_AND_ACT",
            "action_requested": True,
            "journey_goal": "NONE",
            "standalone_question": "L'assistant est-il toute la plateforme ?",
            "task_target": "CURRENT_TASK",
            "confidence": 0.97,
            "items": [],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
            "order_action": None,
            "target_reference": None,
            "replacement": None,
        },
    )

    result = MessageUnderstandingService(AIService(provider)).understand(
        MessageUnderstandingInput(
            current_message="Laisse tomber, WhatAssoh c'est toi ?",
            limited_context={"active_task": "DRIVER_ONBOARDING"},
        )
    )

    assert result.intent == MessageIntent.HELP
    assert result.secondary_intents == (MessageIntent.CANCEL,)
    assert result.interaction_mode == InteractionMode.ANSWER_AND_ACT
    assert result.journey_goal == JourneyGoal.NONE
    assert result.task_target == TaskTarget.CURRENT_TASK


def test_multiple_items_preserve_requested_names_and_normalized_units(provider):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "CREATE_OR_UPDATE_ORDER",
            "secondary_intents": [],
            "interaction_mode": "ACT",
            "action_requested": True,
            "journey_goal": "BUY_PRODUCTS",
            "standalone_question": None,
            "task_target": "ORDER",
            "confidence": 0.92,
            "items": [
                {
                    "requested_name": "Mayor",
                    "quantity": 2,
                        "unit": "BOTTLE",
                        "variant_hint": None,
                        "notes": None,
                },
                {
                    "requested_name": "riz parfumé",
                    "quantity": 5,
                        "unit": "KG",
                        "variant_hint": None,
                        "notes": None,
                },
            ],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
            "order_action": None,
            "target_reference": None,
            "replacement": None,
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


def test_item_preserves_variant_hint_separately_from_ordered_quantity(provider):
    provider.register_response(
        task_name="message_understanding",
        result={
            "intent": "CREATE_OR_UPDATE_ORDER", "secondary_intents": [],
            "interaction_mode": "ACT", "action_requested": True,
            "journey_goal": "BUY_PRODUCTS", "standalone_question": None,
            "task_target": "ORDER", "confidence": 0.95,
            "items": [{
                "requested_name": "Huile Mayor", "quantity": 2,
                "unit": "BOTTLE", "variant_hint": "1 L", "notes": None,
            }],
            "location_text": None, "confirmation": None, "missing_fields": [],
            "ambiguities": [], "language": "fr", "order_action": "ADD_ITEM",
            "target_reference": None, "replacement": None,
        },
    )

    item = MessageUnderstandingService(AIService(provider)).understand(
        MessageUnderstandingInput(current_message="Je veux 2 huiles Mayor de 1L")
    ).items[0]

    assert (item.requested_name, item.quantity, item.unit, item.variant_hint) == (
        "Huile Mayor", 2, "BOTTLE", "1 L"
    )


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
            "secondary_intents": [],
            "interaction_mode": "ACT",
            "action_requested": True,
            "journey_goal": "BUY_PRODUCTS",
            "standalone_question": None,
            "task_target": "ORDER",
            "confidence": 0.8,
            "items": [invalid_item],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
            "order_action": None,
            "target_reference": None,
            "replacement": None,
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
            "secondary_intents": [],
            "interaction_mode": "ACT",
            "action_requested": True,
            "journey_goal": "BUY_PRODUCTS",
            "standalone_question": None,
            "task_target": "ORDER",
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
            "order_action": None,
            "target_reference": None,
            "replacement": None,
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
            "secondary_intents": [],
            "interaction_mode": "ANSWER",
            "action_requested": False,
            "journey_goal": "NONE",
            "standalone_question": None,
            "task_target": "NONE",
            "confidence": 0.9,
            "items": [],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
            "order_action": None,
            "target_reference": None,
            "replacement": None,
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


@pytest.mark.parametrize(
    ("action", "target", "replacement"),
    [
        ("ADD_ITEM", None, None),
        ("REMOVE_ITEM", {"text": "arachides"}, None),
        ("MODIFY_QUANTITY", {"text": "riz"}, None),
        (
            "REPLACE_ITEM",
            {"text": "arachides"},
            {"requested_name": "maïs", "quantity": 1, "unit": "L", "notes": None},
        ),
    ],
)
def test_typed_order_action_contract_parses_without_business_ids(
    action, target, replacement
):
    from apps.ai.domain.understanding import OrderAction, parse_understanding

    parsed = parse_understanding(
        {
            "intent": "MODIFY",
            "confidence": 0.9,
            "items": [],
            "location_text": None,
            "confirmation": None,
            "missing_fields": [],
            "ambiguities": [],
            "language": "fr",
            "order_action": action,
            "target_reference": target,
            "replacement": replacement,
        }
    )
    assert parsed.order_action == OrderAction(action)
    assert (
        parsed.target_reference.text == target["text"]
        if target
        else parsed.target_reference is None
    )
    assert (
        parsed.replacement.requested_name == "maïs"
        if replacement
        else parsed.replacement is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"order_action": "REMOVE_ITEM", "target_reference": None, "replacement": None},
        {
            "order_action": "REPLACE_ITEM",
            "target_reference": {"text": "riz"},
            "replacement": None,
        },
        {
            "order_action": "REPLACE_ITEM",
            "target_reference": {"id": "invented"},
            "replacement": {
                "requested_name": "maïs",
                "quantity": 1,
                "unit": "L",
                "notes": None,
            },
        },
    ],
)
def test_invalid_typed_order_action_contract_is_rejected(payload):
    from apps.ai.domain.understanding import parse_understanding

    data = {
        "intent": "MODIFY",
        "confidence": 0.9,
        "items": [],
        "location_text": None,
        "confirmation": None,
        "missing_fields": [],
        "ambiguities": [],
        "language": "fr",
        **payload,
    }
    with pytest.raises(AIInvalidResponseError):
        parse_understanding(data)
