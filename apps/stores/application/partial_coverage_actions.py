"""Stable, provider-safe actions for customer partial-coverage proposals."""

from uuid import UUID


PARTIAL_COVERAGE_ACTIONS = (
    "partial_coverage.accept",
    "partial_coverage.modify",
    "partial_coverage.cancel",
)


def encode_partial_coverage_action(action_id: str, proposal_id) -> str:
    """Encode the proposal identity in the callback itself.

    The longest supported value is 60 ASCII bytes, below Telegram's 64-byte
    ``callback_data`` limit and comfortably within WhatsApp reply-button IDs.
    """
    if action_id not in PARTIAL_COVERAGE_ACTIONS:
        raise ValueError("Unsupported partial coverage action.")
    return f"{action_id}:{UUID(str(proposal_id))}"


def decode_partial_coverage_action(callback_id: str) -> tuple[str, UUID] | None:
    """Return the action and proposal UUID, or reject malformed callbacks."""
    action_id, separator, raw_proposal_id = (callback_id or "").partition(":")
    if not separator or action_id not in PARTIAL_COVERAGE_ACTIONS:
        return None
    try:
        return action_id, UUID(raw_proposal_id)
    except (ValueError, AttributeError):
        return None


def partial_coverage_actions(proposal) -> list[dict[str, str]]:
    """Presentation actions shared by asynchronous and conversational paths."""
    return [
        {
            "id": encode_partial_coverage_action(
                "partial_coverage.accept", proposal.id
            ),
            "label": "Continuer sans",
        },
        {
            "id": encode_partial_coverage_action(
                "partial_coverage.modify", proposal.id
            ),
            "label": "Modifier",
        },
        {
            "id": encode_partial_coverage_action(
                "partial_coverage.cancel", proposal.id
            ),
            "label": "Annuler",
        },
    ]


def partial_coverage_text(proposal) -> str:
    """Render the persisted snapshot, never a recomputed catalogue view."""
    snapshot = proposal.proposal_snapshot
    covered = "\n".join(
        "✓ " + row["requested_name"] for row in snapshot.get("covered_items", [])
    )
    uncovered = "\n".join(
        "✗ " + row["requested_name"] for row in snapshot.get("uncovered_items", [])
    )
    return (
        "Aucune boutique ne peut fournir toute votre commande.\n\n"
        f"{proposal.store.name} peut fournir :\n{covered}\n\n"
        f"Indisponibles :\n{uncovered}\n\n"
        "Voulez-vous continuer uniquement avec les produits disponibles ?"
    )
