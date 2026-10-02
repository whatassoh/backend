"""Structural next-step contract for active conversational journeys."""

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from apps.conversations.domain.context import ConversationFlow


class NextStepKind(StrEnum):
    USER_ACTION = "USER_ACTION"
    USER_CHOICE = "USER_CHOICE"
    USER_TEXT_INPUT = "USER_TEXT_INPUT"
    LOCATION_REQUIRED = "LOCATION_REQUIRED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    SYSTEM_WAIT = "SYSTEM_WAIT"
    WORKFLOW_COMPLETE = "WORKFLOW_COMPLETE"
    ERROR_RECOVERY = "ERROR_RECOVERY"


@dataclass(frozen=True, kw_only=True)
class NextStep:
    """The authoritative owner of the next turn, independent of response prose."""

    kind: NextStepKind
    instruction: str


class MissingNextStepError(ValueError):
    """Raised before an active journey can send an ambiguous response."""


def system_wait(
    instruction=(
        "Vous n'avez rien à faire pour l'instant ; je vous préviendrai dès qu'il "
        "y aura une évolution."
    ),
):
    return NextStep(kind=NextStepKind.SYSTEM_WAIT, instruction=instruction)


def workflow_complete(instruction="Ce parcours est terminé."):
    return NextStep(kind=NextStepKind.WORKFLOW_COMPLETE, instruction=instruction)


def ensure_conversational_outcome(outcome, context):
    """Derive safe next-step structure from authoritative context, then validate it.

    This is intentionally state-led: punctuation and generated prose never decide
    whether a user must act.  Text is only supplemented for next steps that have
    no visible interactive control.
    """

    response = outcome.response
    next_step = response.next_step or _derive_next_step(response, context)
    if next_step is not None:
        response = replace(response, next_step=next_step)
        if _must_render_instruction(response):
            response = replace(
                response,
                text=_append_instruction(response.text, next_step.instruction),
            )
        outcome = replace(outcome, response=response)
    validate_conversational_outcome(outcome, context)
    return outcome


def validate_conversational_outcome(outcome, context):
    """Reject a non-terminal active result that has no structural next step."""

    if _is_terminal(outcome) or not _has_active_journey(outcome, context):
        return outcome
    next_step = outcome.response.next_step
    if next_step is None:
        raise MissingNextStepError(
            "An active non-terminal conversational outcome requires next_step."
        )
    if not next_step.instruction.strip():
        raise MissingNextStepError("next_step.instruction cannot be empty.")
    if next_step.kind == NextStepKind.SYSTEM_WAIT and not _contains_instruction(
        outcome.response.text, next_step.instruction
    ):
        raise MissingNextStepError(
            "A system-wait response must explicitly tell the user to wait."
        )
    if (
        next_step.kind in {NextStepKind.LOCATION_REQUIRED, NextStepKind.USER_TEXT_INPUT}
        and not outcome.response.actions
        and not _contains_instruction(outcome.response.text, next_step.instruction)
    ):
        raise MissingNextStepError(
            "A text-input next step without controls must be rendered in the response."
        )
    return outcome


def _derive_next_step(response, context):
    if response.request_location or context.flow == ConversationFlow.AWAITING_LOCATION:
        return NextStep(
            kind=NextStepKind.LOCATION_REQUIRED,
            instruction="Partagez votre position pour continuer.",
        )
    action_ids = {action.action_id for action in response.actions}
    if any(action_id.endswith("confirm") for action_id in action_ids):
        return NextStep(
            kind=NextStepKind.CONFIRMATION_REQUIRED,
            instruction="Choisissez de confirmer, modifier ou annuler.",
        )
    if response.actions:
        return NextStep(
            kind=NextStepKind.USER_CHOICE,
            instruction="Choisissez une option pour continuer.",
        )
    if context.metadata.get("awaiting_delivery_instructions"):
        return NextStep(
            kind=NextStepKind.USER_TEXT_INPUT,
            instruction="Indiquez une instruction de livraison pour continuer.",
        )
    if context.pending_clarification or context.pending_seller_catalog_mutation:
        return NextStep(
            kind=NextStepKind.USER_TEXT_INPUT,
            instruction="Répondez à la demande en cours pour continuer.",
        )
    if context.flow in {
        ConversationFlow.ORDER_BUILDING,
        ConversationFlow.AWAITING_CLARIFICATION,
        ConversationFlow.AWAITING_CONFIRMATION,
        ConversationFlow.SELLER_ONBOARDING,
        ConversationFlow.DRIVER_ONBOARDING,
        ConversationFlow.AWAITING_JOURNEY_SELECTION,
    }:
        return NextStep(
            kind=NextStepKind.USER_TEXT_INPUT,
            instruction="Répondez à la demande en cours pour continuer.",
        )
    if context.active_journey is not None:
        return NextStep(
            kind=NextStepKind.USER_ACTION,
            instruction="Que souhaitez-vous faire maintenant ?",
        )
    return None


def _has_active_journey(outcome, context):
    return bool(
        context.active_journey
        or context.active_order_id
        or context.pending_clarification
        or context.pending_seller_catalog_mutation
        or context.seller_onboarding
        or context.driver_onboarding
        or context.metadata.get("awaiting_delivery_instructions")
        or context.flow != ConversationFlow.IDLE
        or outcome.active_order_id
    )


def _is_terminal(outcome):
    if str(outcome.status) != "PROCESSED":
        return True
    next_step = outcome.response.next_step
    return bool(next_step and next_step.kind == NextStepKind.WORKFLOW_COMPLETE)


def _must_render_instruction(response):
    # Waiting is user-facing information, not an implicit consequence of an
    # optional workspace button.  Always render it, even when controls exist.
    if response.next_step.kind == NextStepKind.SYSTEM_WAIT:
        return True
    if response.actions:
        return False
    return response.next_step.kind in {
        NextStepKind.USER_ACTION,
        NextStepKind.USER_TEXT_INPUT,
        NextStepKind.LOCATION_REQUIRED,
        NextStepKind.SYSTEM_WAIT,
        NextStepKind.ERROR_RECOVERY,
    }


def _append_instruction(text, instruction):
    if _contains_instruction(text, instruction):
        return text
    return f"{text}\n\n{instruction}"


def _contains_instruction(text, instruction):
    # Visible guidance can legitimately end with a comma or semicolon before
    # a second sentence.  Compare normalised words, not typography.
    def normalise(value):
        return " ".join(re.sub(r"[^\w\s]", " ", value.casefold()).split())

    return normalise(instruction) in normalise(text)
