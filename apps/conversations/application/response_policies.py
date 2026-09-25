"""Select a presentation policy from trusted outcome and context state."""

from apps.ai.domain.response_policy import POLICIES, ResponseUseCase

NAVIGATION_CAPABILITIES = {"pause", "resume", "back", "cancel", "paused_task"}
MEMORY_CAPABILITIES = {"memory_show", "memory_forget"}
STATUS_CAPABILITIES = {"order_history", "order_status", "delivery_tracking"}


def select_response_policy(outcome, context):
    metadata = {**outcome.metadata, **outcome.response.metadata}
    capability = str(metadata.get("capability") or "")
    action_ids = {action.action_id for action in outcome.response.actions}
    text = outcome.response.text.casefold()

    if str(outcome.status) == "RECOVERABLE_FAILURE":
        use_case = ResponseUseCase.ERROR_RECOVERY
    elif outcome.response.request_location:
        use_case = ResponseUseCase.LOCATION_REQUEST
    elif capability in MEMORY_CAPABILITIES:
        use_case = ResponseUseCase.MEMORY_CONTROL
    elif metadata.get("clarification_required"):
        use_case = ResponseUseCase.CLARIFICATION
    elif any(action.endswith("confirm") for action in action_ids):
        use_case = ResponseUseCase.ORDER_REVIEW
    elif capability in STATUS_CAPABILITIES or metadata.get("order_status"):
        use_case = ResponseUseCase.ORDER_STATUS
    elif capability in NAVIGATION_CAPABILITIES or any(
        action.startswith("conversation.") for action in action_ids
    ):
        use_case = ResponseUseCase.NAVIGATION
    elif metadata.get("seller_onboarding") or metadata.get("driver_onboarding"):
        use_case = ResponseUseCase.ONBOARDING
    elif metadata.get("seller_catalog") or metadata.get("seller_workspace"):
        use_case = ResponseUseCase.CATALOG
    elif outcome.actions_performed:
        use_case = ResponseUseCase.ACTION_RESULT
    elif capability == "help":
        use_case = ResponseUseCase.HELP
    elif metadata.get("capability_status") == "SOCIAL" or capability == "social":
        use_case = ResponseUseCase.SOCIAL
    elif _is_empty_result(text):
        use_case = ResponseUseCase.EMPTY_RESULT
    elif outcome.requires_followup or text.rstrip().endswith("?"):
        use_case = ResponseUseCase.CLARIFICATION
    else:
        use_case = ResponseUseCase.GENERIC
    return POLICIES[use_case]


def _is_empty_result(text):
    return any(
        phrase in text
        for phrase in (
            "aucune commande",
            "aucune boutique",
            "aucun produit",
            "ne trouve pas",
            "not found",
            "no order",
            "no store",
            "no product",
        )
    )
