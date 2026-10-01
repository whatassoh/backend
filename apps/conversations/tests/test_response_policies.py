from types import SimpleNamespace

import pytest

from apps.ai.domain.response_policy import ResponseUseCase
from apps.conversations.application.response_policies import select_response_policy


def outcome(
    text="Réponse.",
    *,
    status="PROCESSED",
    metadata=None,
    response_metadata=None,
    actions=(),
    actions_performed=(),
    request_location=False,
    requires_followup=False,
):
    return SimpleNamespace(
        status=status,
        metadata=metadata or {},
        actions_performed=actions_performed,
        requires_followup=requires_followup,
        response=SimpleNamespace(
            text=text,
            metadata=response_metadata or {},
            actions=tuple(SimpleNamespace(action_id=value) for value in actions),
            request_location=request_location,
        ),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (outcome(status="RECOVERABLE_FAILURE"), ResponseUseCase.ERROR_RECOVERY),
        (outcome(request_location=True), ResponseUseCase.LOCATION_REQUEST),
        (
            outcome(metadata={"capability": "memory_show"}),
            ResponseUseCase.MEMORY_CONTROL,
        ),
        (
            outcome(metadata={"clarification_required": True}),
            ResponseUseCase.CLARIFICATION,
        ),
        (outcome(actions=("order.confirm",)), ResponseUseCase.ORDER_REVIEW),
        (
            outcome(metadata={"capability": "order_history"}),
            ResponseUseCase.ORDER_STATUS,
        ),
        (outcome(metadata={"driver_workspace": True}), ResponseUseCase.ORDER_STATUS),
        (outcome(metadata={"capability": "pause"}), ResponseUseCase.NAVIGATION),
        (outcome(metadata={"seller_onboarding": True}), ResponseUseCase.ONBOARDING),
        (outcome(metadata={"seller_catalog": True}), ResponseUseCase.CATALOG),
        (
            outcome(metadata={"capability": "product_discovery"}),
            ResponseUseCase.CATALOG,
        ),
        (
            outcome(actions_performed=("order_item_added",)),
            ResponseUseCase.ACTION_RESULT,
        ),
        (outcome(metadata={"capability": "help"}), ResponseUseCase.HELP),
        (outcome(metadata={"capability_status": "SOCIAL"}), ResponseUseCase.SOCIAL),
        (outcome("Aucun produit trouvé."), ResponseUseCase.EMPTY_RESULT),
        (outcome(), ResponseUseCase.GENERIC),
    ],
)
def test_every_supported_response_family_has_an_explicit_policy(value, expected):
    policy = select_response_policy(value, SimpleNamespace())

    assert policy.use_case == expected
    assert policy.max_characters > 0
