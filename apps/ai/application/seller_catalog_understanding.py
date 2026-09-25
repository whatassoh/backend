"""Strict AI extraction for seller catalog language; it carries no database IDs."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from apps.ai.contracts.requests import AIStructuredRequest
from apps.ai.domain.exceptions import AIInvalidResponseError
from apps.ai.domain.value_objects import PromptReference


class SellerCatalogIntent(StrEnum):
    LIST = "SELLER_CATALOG_LIST"
    ADD = "SELLER_CATALOG_ADD"
    UPDATE_PRICE = "SELLER_CATALOG_UPDATE_PRICE"
    SET_AVAILABILITY = "SELLER_CATALOG_SET_AVAILABILITY"
    REMOVE = "SELLER_CATALOG_REMOVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, kw_only=True)
class SellerCatalogCommand:
    intent: SellerCatalogIntent
    confidence: Decimal
    product_query: str | None = None
    variant_query: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    availability: str | None = None
    missing_fields: tuple[str, ...] = ()


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "confidence",
        "product_query",
        "variant_query",
        "price",
        "currency",
        "availability",
        "missing_fields",
    ],
    "properties": {
        "intent": {
            "type": "string",
            "enum": [value.value for value in SellerCatalogIntent],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "product_query": {"type": ["string", "null"]},
        "variant_query": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"]},
        "availability": {
            "type": ["string", "null"],
            "enum": ["AVAILABLE", "UNAVAILABLE", "UNKNOWN", None],
        },
        "missing_fields": {"type": "array", "items": {"type": "string"}},
    },
}


class SellerCatalogUnderstandingService:
    def __init__(self, ai_service):
        self.ai_service = ai_service

    def understand(self, text, *, correlation_id=None):
        result = self.ai_service.generate_structured(
            AIStructuredRequest(
                task_name="seller_catalog_command",
                system_instructions=(
                    "Extract only a seller catalog command. Never return product, variant, store, user IDs or mutate data. "
                    "Use availability only for declared availability, never physical stock."
                ),
                user_input=text,
                response_schema=SCHEMA,
                correlation_id=correlation_id,
                prompt=PromptReference(name="seller_catalog_command", version="v1"),
            )
        )
        try:
            price = result.data["price"]
            return SellerCatalogCommand(
                intent=SellerCatalogIntent(result.data["intent"]),
                confidence=Decimal(str(result.data["confidence"])),
                product_query=result.data["product_query"],
                variant_query=result.data["variant_query"],
                price=Decimal(str(price)) if price is not None else None,
                currency=result.data["currency"],
                availability=result.data["availability"],
                missing_fields=tuple(result.data["missing_fields"]),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise AIInvalidResponseError("Invalid seller catalog command.") from exc
