"""Safe terminal responses used only after the durable retry budget is exhausted."""

from apps.conversations.application.interruptions import active_task_kind
from apps.conversations.domain.context import ConversationContext, ConversationTaskKind


def terminal_processing_message(conversation, *, attempts: int) -> str:
    """Explain a terminal technical failure without hiding the preserved task."""

    context = ConversationContext.from_payload(conversation.context)
    task = active_task_kind(context)
    preserved = {
        ConversationTaskKind.ORDER: "Votre commande en cours reste inchangée.",
        ConversationTaskKind.SELLER_ONBOARDING: (
            "La création de votre boutique reste enregistrée à l'étape en cours."
        ),
        ConversationTaskKind.SELLER_CATALOG: (
            "Les informations de votre catalogue restent inchangées."
        ),
        ConversationTaskKind.DRIVER_ONBOARDING: (
            "Votre inscription comme livreur reste enregistrée à l'étape en cours."
        ),
        ConversationTaskKind.DELIVERY_INSTRUCTIONS: (
            "Vos informations de livraison déjà validées restent inchangées."
        ),
        ConversationTaskKind.JOURNEY: "Votre parcours en cours reste inchangé.",
    }.get(task, "Aucune action n'a été effectuée à partir de ce message.")
    return (
        f"Je n'ai pas réussi à comprendre ce dernier message après {attempts} "
        f"tentative(s). {preserved} Reformulez-le avec vos propres mots; "
        "je reprendrai ensuite depuis le même contexte."
    )
