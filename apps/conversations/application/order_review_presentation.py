"""Human presentation policy for authoritative order reviews."""

from dataclasses import dataclass

from apps.orders.application.review import OrderReview


@dataclass(frozen=True, kw_only=True)
class ReviewPresentation:
    text: str
    actions: tuple[tuple[str, str], ...]


class OrderReviewPresenter:
    blocker_labels = {
        "delivery_location_missing": "Il manque votre lieu de livraison.",
        "unmatched_items": "Un article indisponible doit être remplacé ou retiré.",
        "ambiguous_item": "Un article ou son format doit être précisé.",
        "no_items": "Votre commande ne contient encore aucun article.",
        "clarification_pending": "Une précision demandée doit encore être fournie.",
        "order_not_editable": "Cette commande ne peut plus être modifiée.",
    }

    def present(self, review: OrderReview) -> ReviewPresentation:
        lines = [f"- {item.display_label}" for item in review.items]
        if review.readiness.ready:
            delivery = "Position partagée"
            if review.delivery_address_label:
                delivery += f" — {review.delivery_address_label}"
            if review.delivery_instructions:
                delivery += f"\nIndications : {review.delivery_instructions}"
            return ReviewPresentation(
                text="Récapitulatif :\n"
                + "\n".join(lines)
                + f"\nLivraison : {delivery}\nConfirmez-vous ?",
                actions=(
                    ("order.confirm", "Confirmer"),
                    ("order.modify", "Modifier"),
                    ("order.change_location", "Modifier l'adresse"),
                    ("order.cancel", "Annuler"),
                ),
            )
        issues = [
            self.blocker_labels.get(code, "Une information doit être corrigée.")
            for code in review.readiness.blocking_issues
        ]
        actions = []
        if "delivery_location_missing" in review.readiness.blocking_issues:
            actions.append(("order.change_location", "Ajouter ma localisation"))
        if any(
            code in review.readiness.blocking_issues
            for code in ("unmatched_items", "ambiguous_item", "clarification_pending")
        ):
            actions.append(("order.modify", "Résoudre les articles"))
        if "no_items" in review.readiness.blocking_issues:
            actions.append(("order.modify", "Ajouter un article"))
        actions.append(("order.cancel", "Annuler"))
        return ReviewPresentation(
            text="Commande à compléter :\n"
            + ("\n".join(lines) if lines else "- Aucun article")
            + "\n\n"
            + "\n".join(issues),
            actions=tuple(dict.fromkeys(actions)),
        )
