from apps.ai.domain.value_objects import PromptReference

MESSAGE_UNDERSTANDING_PROMPT = PromptReference(
    name="message_understanding", version="v1"
)

SYSTEM_INSTRUCTIONS = """Understand the user's message without performing any action.
Return only the requested JSON schema. Never invent product IDs, variant IDs, prices,
stock, stores, availability, coordinates, or quantities. Preserve product wording.
Use null for missing facts, report ambiguity explicitly, and use only allowed units.
A textual delivery place is location_text, never latitude or longitude. This is parsing,
not catalog grounding or order validation."""
