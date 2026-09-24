from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from apps.ai.domain.exceptions import AIInvalidResponseError


class MessageIntent(StrEnum):
    CREATE_OR_UPDATE_ORDER = "CREATE_OR_UPDATE_ORDER"
    PROVIDE_LOCATION = "PROVIDE_LOCATION"
    CONFIRM = "CONFIRM"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    GREETING = "GREETING"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"
    SELL = "SELL"
    DELIVER = "DELIVER"
    ORDER_HISTORY = "ORDER_HISTORY"
    ORDER_STATUS = "ORDER_STATUS"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ORDER_RESUME = "ORDER_RESUME"
    PAUSE = "PAUSE"
    BACK = "BACK"


class OrderAction(StrEnum):
    ADD_ITEM = "ADD_ITEM"
    REMOVE_ITEM = "REMOVE_ITEM"
    REPLACE_ITEM = "REPLACE_ITEM"
    MODIFY_QUANTITY = "MODIFY_QUANTITY"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    MODIFY = "MODIFY"
    CHANGE_LOCATION = "CHANGE_LOCATION"
    RESUME = "RESUME"


class ExtractedUnit(StrEnum):
    """Transport enum deliberately aligned with catalog ProductUnit values."""
    KG = "KG"
    G = "G"
    L = "L"
    ML = "ML"
    UNIT = "UNIT"
    PACK = "PACK"
    BOTTLE = "BOTTLE"
    BAG = "BAG"
    OTHER = "OTHER"


@dataclass(frozen=True, kw_only=True)
class ExtractedItem:
    requested_name: str
    quantity: Decimal | None = None
    unit: ExtractedUnit | None = None
    notes: str | None = None


@dataclass(frozen=True, kw_only=True)
class ItemTargetReference:
    """User wording only; deliberately not a business identifier."""
    text: str


@dataclass(frozen=True, kw_only=True)
class UserMessageUnderstanding:
    intent: MessageIntent
    confidence: Decimal
    items: tuple[ExtractedItem, ...] = ()
    location_text: str | None = None
    confirmation: bool | None = None
    missing_fields: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    language: str | None = None
    order_action: OrderAction | None = None
    target_reference: ItemTargetReference | None = None
    replacement: ExtractedItem | None = None


@dataclass(frozen=True, kw_only=True)
class MessageUnderstandingInput:
    current_message: str
    limited_context: dict[str, Any] | None = None
    structured_message_data: dict[str, Any] | None = None
    correlation_id: str | None = None


def parse_understanding(data: dict[str, Any]) -> UserMessageUnderstanding:
    """Convert provider data into strongly typed, non-business facts."""
    try:
        items = tuple(_parse_item(item) for item in data["items"])
        confidence = Decimal(str(data["confidence"]))
        action = OrderAction(data["order_action"]) if data.get("order_action") else None
        target = _parse_target(data.get("target_reference"))
        replacement_data = data.get("replacement")
        replacement = _parse_item(replacement_data) if replacement_data else None
        result = UserMessageUnderstanding(
            intent=MessageIntent(data["intent"]), confidence=confidence, items=items,
            location_text=data.get("location_text"), confirmation=data.get("confirmation"),
            missing_fields=tuple(data["missing_fields"]), ambiguities=tuple(data["ambiguities"]),
            language=data.get("language"), order_action=action,
            target_reference=target, replacement=replacement,
        )
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise AIInvalidResponseError("Invalid message-understanding response.") from exc
    _validate_understanding(result)
    return result


def _parse_target(data: dict[str, Any] | None) -> ItemTargetReference | None:
    if data is None:
        return None
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Invalid target reference.")
    return ItemTargetReference(text=text.strip())


def _parse_item(data: dict[str, Any]) -> ExtractedItem:
    quantity = data.get("quantity")
    try:
        parsed_quantity = Decimal(str(quantity)) if quantity is not None else None
        unit = ExtractedUnit(data["unit"]) if data.get("unit") is not None else None
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AIInvalidResponseError("Invalid extracted item.") from exc
    return ExtractedItem(requested_name=data["requested_name"].strip(), quantity=parsed_quantity, unit=unit, notes=data.get("notes"))


def _validate_understanding(result: UserMessageUnderstanding) -> None:
    if not Decimal("0") <= result.confidence <= Decimal("1"):
        raise AIInvalidResponseError("Confidence must be between zero and one.")
    if result.confirmation is not None and not isinstance(result.confirmation, bool):
        raise AIInvalidResponseError("Confirmation must be a boolean or null.")
    for item in (*result.items, *(() if result.replacement is None else (result.replacement,))):
        if not item.requested_name:
            raise AIInvalidResponseError("Extracted item name cannot be empty.")
        if item.quantity is not None and item.quantity <= 0:
            raise AIInvalidResponseError("Extracted item quantity must be positive.")
    requires_target = {OrderAction.REMOVE_ITEM, OrderAction.REPLACE_ITEM, OrderAction.MODIFY_QUANTITY}
    if result.order_action in requires_target and result.target_reference is None:
        raise AIInvalidResponseError("This order action requires a target reference.")
    if result.order_action == OrderAction.REPLACE_ITEM and result.replacement is None:
        raise AIInvalidResponseError("Replacement action requires a replacement item.")
