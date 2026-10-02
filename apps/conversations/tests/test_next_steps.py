import pytest

from apps.conversations.application.next_steps import (
    MissingNextStepError,
    NextStepKind,
    ensure_conversational_outcome,
    system_wait,
    validate_conversational_outcome,
)
from apps.conversations.application.orchestrator import (
    ConversationalOutcome,
    ConversationalOutcomeStatus,
    ResponseAction,
    ResponsePlan,
)
from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
)


def outcome(
    *,
    text="Réponse métier.",
    actions=(),
    next_step=None,
    status=ConversationalOutcomeStatus.PROCESSED,
):
    return ConversationalOutcome(
        status=status,
        response=ResponsePlan(text=text, actions=actions, next_step=next_step),
    )


def test_active_customer_flow_always_exposes_next_step():
    result = ensure_conversational_outcome(
        outcome(
            actions=(ResponseAction(action_id="order.confirm", label="Confirmer"),)
        ),
        ConversationContext(
            active_journey=ActiveJourney.BUY,
            flow=ConversationFlow.AWAITING_CONFIRMATION,
        ),
    )

    assert result.response.next_step.kind == NextStepKind.CONFIRMATION_REQUIRED


def test_active_store_flow_always_exposes_next_step():
    result = ensure_conversational_outcome(
        outcome(text="Catalogue mis à jour."),
        ConversationContext(active_journey=ActiveJourney.SELL),
    )

    assert result.response.next_step.kind == NextStepKind.USER_ACTION
    assert "Que souhaitez-vous faire maintenant ?" in result.response.text


def test_active_driver_flow_always_exposes_next_step():
    result = ensure_conversational_outcome(
        outcome(text="Votre profil livreur est prêt."),
        ConversationContext(active_journey=ActiveJourney.DELIVER),
    )

    assert result.response.next_step.kind == NextStepKind.USER_ACTION


def test_system_wait_state_explicitly_tells_user_to_wait():
    result = ensure_conversational_outcome(
        outcome(
            text=(
                "Recherche de boutique en cours. Vous n'avez rien à faire pour "
                "l'instant."
            ),
            next_step=system_wait("Vous n'avez rien à faire pour l'instant."),
        ),
        ConversationContext(active_journey=ActiveJourney.BUY),
    )

    assert result.response.next_step.kind == NextStepKind.SYSTEM_WAIT
    assert "Vous n'avez rien à faire" in result.response.text


def test_location_required_state_exposes_location_request():
    result = ensure_conversational_outcome(
        outcome(text="Position manquante.",),
        ConversationContext(
            active_journey=ActiveJourney.BUY,
            flow=ConversationFlow.AWAITING_LOCATION,
        ),
    )

    assert result.response.next_step.kind == NextStepKind.LOCATION_REQUIRED
    assert "Partagez votre position" in result.response.text


def test_confirmation_state_exposes_confirmation_actions():
    result = ensure_conversational_outcome(
        outcome(
            text="Récapitulatif.",
            actions=(
                ResponseAction(action_id="order.confirm", label="Confirmer"),
                ResponseAction(action_id="order.modify", label="Modifier"),
                ResponseAction(action_id="order.cancel", label="Annuler"),
            ),
        ),
        ConversationContext(
            active_journey=ActiveJourney.BUY,
            flow=ConversationFlow.AWAITING_CONFIRMATION,
        ),
    )

    assert result.response.next_step.kind == NextStepKind.CONFIRMATION_REQUIRED
    assert {action.label for action in result.response.actions} == {
        "Confirmer",
        "Modifier",
        "Annuler",
    }


def test_catalog_mutation_exposes_next_action():
    result = ensure_conversational_outcome(
        outcome(text="Catalogue mis à jour."),
        ConversationContext(active_journey=ActiveJourney.SELL),
    )

    assert result.response.next_step.kind == NextStepKind.USER_ACTION
    assert result.response.text.endswith("Que souhaitez-vous faire maintenant ?")


def test_driver_available_without_location_exposes_location_request():
    result = ensure_conversational_outcome(
        outcome(text="Vous êtes disponible.",),
        ConversationContext(
            active_journey=ActiveJourney.DELIVER,
            flow=ConversationFlow.AWAITING_LOCATION,
        ),
    )

    assert result.response.next_step.kind == NextStepKind.LOCATION_REQUIRED
    assert "Partagez votre position" in result.response.text


def test_driver_available_with_location_exposes_waiting_state():
    result = ensure_conversational_outcome(
        outcome(
            text="Vous êtes disponible. Vous n'avez rien à faire pour l'instant.",
            next_step=system_wait("Vous n'avez rien à faire pour l'instant."),
        ),
        ConversationContext(active_journey=ActiveJourney.DELIVER),
    )

    assert result.response.next_step.kind == NextStepKind.SYSTEM_WAIT


def test_store_order_ready_exposes_waiting_for_driver_state():
    result = ensure_conversational_outcome(
        outcome(
            text=(
                "Commande prête. Nous recherchons un livreur. Vous n'avez rien à "
                "faire pour l'instant."
            ),
            next_step=system_wait("Vous n'avez rien à faire pour l'instant."),
        ),
        ConversationContext(active_journey=ActiveJourney.SELL),
    )

    assert result.response.next_step.kind == NextStepKind.SYSTEM_WAIT


def test_non_terminal_outcome_without_next_step_is_rejected():
    active = ConversationContext(active_journey=ActiveJourney.BUY)

    with pytest.raises(MissingNextStepError):
        validate_conversational_outcome(outcome(text="Position enregistrée."), active)


def test_terminal_outcome_may_have_no_next_step():
    result = validate_conversational_outcome(
        outcome(
            text="Cette action n'est plus disponible.",
            status=ConversationalOutcomeStatus.REJECTED,
        ),
        ConversationContext(active_journey=ActiveJourney.BUY),
    )

    assert result.response.next_step is None
