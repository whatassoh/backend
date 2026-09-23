from apps.ai.domain.value_objects import PromptReference

MESSAGE_UNDERSTANDING_PROMPT = PromptReference(
    name="message_understanding", version="v1"
)

SYSTEM_INSTRUCTIONS = """Understand the user's message without performing any action.
Return only the requested JSON schema. Never invent product IDs, variant IDs, prices,
stock, stores, availability, coordinates, or quantities. Preserve product wording.
Use null for missing facts, report ambiguity explicitly, and use only allowed units.
A textual delivery place is location_text, never latitude or longitude. This is parsing,
not catalog grounding or order validation. Use SELL only for an explicit wish to sell
or manage a shop, DELIVER only for an explicit wish to become/work as a delivery
driver. Buying or ordering products remains CREATE_OR_UPDATE_ORDER. Classify customer order-list requests as ORDER_HISTORY, progress requests as ORDER_STATUS, general-knowledge questions as OUT_OF_SCOPE, explicit requests to continue a current order as ORDER_RESUME, and use UNKNOWN only when unclear. For order actions, use order_action: ADD_ITEM, REMOVE_ITEM, REPLACE_ITEM, MODIFY_QUANTITY, CONFIRM, CANCEL, MODIFY, CHANGE_LOCATION or RESUME. target_reference contains only the users words, never an ID. REPLACE_ITEM requires target_reference and replacement (the new item facts). MODIFY_QUANTITY requires target_reference and an item containing the requested quantity and/or unit. Never emit any order, item, product, variant, store, or driver ID.
"""
