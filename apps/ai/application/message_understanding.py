import logging
from typing import Any

from apps.ai.application.service import AIService
from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.domain.understanding import (
    MessageIntent,
    MessageUnderstandingInput,
    OrderAction,
    UserMessageUnderstanding,
    parse_understanding,
)
from apps.ai.prompts.message_understanding import (
    MESSAGE_UNDERSTANDING_PROMPT,
    SYSTEM_INSTRUCTIONS,
)
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType

logger = logging.getLogger(__name__)

MESSAGE_UNDERSTANDING_SCHEMA: dict[str, Any] = {
    "title": "message_understanding",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "confidence",
        "items",
        "location_text",
        "confirmation",
        "missing_fields",
        "ambiguities",
        "language",
        "order_action",
        "target_reference",
        "replacement",
    ],
    "properties": {
        "intent": {
            "type": "string",
            "enum": [intent.value for intent in MessageIntent],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requested_name", "quantity", "unit", "notes"],
                "properties": {
                    "requested_name": {"type": "string", "minLength": 1},
                    "quantity": {"type": ["number", "null"], "exclusiveMinimum": 0},
                    "unit": {
                        "type": ["string", "null"],
                        "enum": [
                            "KG",
                            "G",
                            "L",
                            "ML",
                            "UNIT",
                            "PACK",
                            "BOTTLE",
                            "BAG",
                            "OTHER",
                            None,
                        ],
                    },
                    "notes": {"type": ["string", "null"]},
                },
            },
        },
        "location_text": {"type": ["string", "null"]},
        "confirmation": {"type": ["boolean", "null"]},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "language": {"type": ["string", "null"]},
        "order_action": {
            "type": ["string", "null"],
            "enum": [*(action.value for action in OrderAction), None],
        },
        "target_reference": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["text"],
            "properties": {"text": {"type": "string", "minLength": 1}},
        },
        "replacement": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["requested_name", "quantity", "unit", "notes"],
            "properties": {
                "requested_name": {"type": "string", "minLength": 1},
                "quantity": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "unit": {
                    "type": ["string", "null"],
                    "enum": [
                        "KG",
                        "G",
                        "L",
                        "ML",
                        "UNIT",
                        "PACK",
                        "BOTTLE",
                        "BAG",
                        "OTHER",
                        None,
                    ],
                },
                "notes": {"type": ["string", "null"]},
            },
        },
    },
}


class MessageUnderstandingService:
    """Structured language interpretation only; no ORM or business mutation."""

    def __init__(self, ai_service: AIService) -> None:
        self.ai_service = ai_service

    def understand(self, input: MessageUnderstandingInput) -> UserMessageUnderstanding:
        result = self.ai_service.generate_structured(
            AIStructuredRequest(
                task_name="message_understanding",
                system_instructions=SYSTEM_INSTRUCTIONS,
                user_input=input.current_message,
                conversation_context=input.limited_context,
                response_schema=MESSAGE_UNDERSTANDING_SCHEMA,
                correlation_id=input.correlation_id,
                prompt=MESSAGE_UNDERSTANDING_PROMPT,
            )
        )
        understanding = parse_understanding(result.data)
        logger.info(
            "message_understanding_completed",
            extra={
                "task_name": "message_understanding",
                "intent": understanding.intent,
                "confidence": str(understanding.confidence),
                "item_count": len(understanding.items),
                "ambiguity_count": len(understanding.ambiguities),
                "correlation_id": input.correlation_id,
            },
        )
        return understanding

    def understand_normalized(
        self,
        message: NormalizedInboundMessage,
        *,
        limited_context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> UserMessageUnderstanding | None:
        """Use trusted message structure; interactive actions are routed later."""
        if MessageType(message.message_type) == MessageType.LOCATION:
            # Coordinates remain on the normalized message for a later deterministic
            # orchestrator; the LLM never sees or recreates them.
            return UserMessageUnderstanding(
                intent=MessageIntent.PROVIDE_LOCATION,
                confidence=1,
            )
        if MessageType(message.message_type) == MessageType.INTERACTIVE and (
            message.metadata.get("action_id")
        ):
            return None
        return self.understand(
            MessageUnderstandingInput(
                current_message=message.text or "",
                limited_context=limited_context,
                structured_message_data={"message_type": str(message.message_type)},
                correlation_id=correlation_id,
            )
        )
