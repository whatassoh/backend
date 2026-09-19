"""Controlled, non-provider-specific messages for recoverable technical errors."""

from apps.messaging.domain.exceptions import MessagingError


def technical_retry_message(error: Exception) -> str:
    """Never expose provider, model, secret or transport details to customers."""
    if isinstance(error, MessagingError):
        return "Je ne peux pas envoyer la réponse pour le moment. Réessayez bientôt."
    return "Je n'ai pas pu traiter votre demande pour le moment. Vous pouvez réessayer."
