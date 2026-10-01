"""Seller workspace resolved from live memberships and operational state."""

import re
import unicodedata
from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID

from django.utils import timezone

from apps.conversations.domain.context import ActiveJourney, ConversationFlow
from apps.orders.domain.enums import OrderStatus
from apps.stores.application.offer_actions import encode_store_offer_action
from apps.stores.application.preparation import encode_store_order_action
from apps.stores.domain.enums import StoreMembershipStatus, StoreOfferStatus
from apps.stores.infrastructure.models import StoreMembership, StoreOffer


class SellerWorkspaceRequest(StrEnum):
    OPEN = "OPEN"
    CATALOG = "CATALOG"


@dataclass(frozen=True, kw_only=True)
class SellerWorkspaceAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class SellerWorkspaceOutcome:
    text: str
    actions: tuple[SellerWorkspaceAction, ...] = ()
    metadata: dict[str, object] = field(
        default_factory=lambda: {"seller_workspace": True}
    )


class SellerWorkspaceService:
    """Authorization and presentation boundary for the conversational workspace."""

    catalog_prefix = "seller.workspace.catalog:"
    store_prefix = "seller.workspace.store:"
    legacy_actions = {
        "seller_onboarding.confirm",
        "seller.catalog.chat",
        "seller.catalog.dashboard",
    }

    def __init__(self, *, context_service=None):
        from apps.conversations.application.context import ConversationContextService

        self.context_service = context_service or ConversationContextService()

    def classify(self, *, user, context, text: str | None) -> SellerWorkspaceRequest | None:
        if not text or not self.resolve_membership(user=user):
            return None
        normalized = self._normalize(text)
        if self._is_catalog_request(normalized):
            return SellerWorkspaceRequest.CATALOG
        if self._is_workspace_request(normalized):
            return SellerWorkspaceRequest.OPEN
        if (
            self._is_next_step_question(normalized)
            and context.active_order_id is None
            and context.pending_clarification is None
            and context.active_journey in {None, ActiveJourney.SELL}
        ):
            return SellerWorkspaceRequest.OPEN
        return None

    def handles_action(self, action_id: str | None) -> bool:
        return bool(
            action_id
            and (
                action_id.startswith(self.catalog_prefix)
                or action_id.startswith(self.store_prefix)
                or action_id in self.legacy_actions
            )
        )

    def present(self, *, conversation, store_id=None) -> SellerWorkspaceOutcome:
        membership = self.resolve_membership(user=conversation.user, store_id=store_id)
        if membership is None:
            return self._forbidden()
        self._set_workspace_context(conversation, membership.store_id)
        operational = self._operational_outcome(membership.store)
        if operational is not None:
            return operational
        store = membership.store
        return SellerWorkspaceOutcome(
            text=(
                f"Votre boutique « {store.name} » est prête.\n\n"
                "Que souhaitez-vous faire maintenant ?"
            ),
            actions=(
                SellerWorkspaceAction(
                    action_id=f"{self.catalog_prefix}{store.id}",
                    label="Gérer mon catalogue",
                ),
                SellerWorkspaceAction(
                    action_id=f"{self.store_prefix}{store.id}",
                    label="Voir ma boutique",
                ),
            ),
            metadata={
                "seller_workspace": True,
                "store_id": str(store.id),
                "store_status": store.status,
            },
        )

    def handle_action(self, *, conversation, action_id: str) -> SellerWorkspaceOutcome:
        if action_id in self.legacy_actions:
            membership = self.resolve_membership(user=conversation.user)
            if membership is None:
                return self._forbidden()
            if action_id == "seller.catalog.chat":
                return self.activate_catalog(
                    conversation=conversation, store_id=membership.store_id
                )
            return self.present(conversation=conversation, store_id=membership.store_id)

        prefix = (
            self.catalog_prefix
            if action_id.startswith(self.catalog_prefix)
            else self.store_prefix
        )
        try:
            store_id = UUID(action_id.removeprefix(prefix))
        except (ValueError, AttributeError):
            return self._forbidden()
        membership = self.resolve_membership(user=conversation.user, store_id=store_id)
        if membership is None:
            return self._forbidden()
        if prefix == self.catalog_prefix:
            return self.activate_catalog(
                conversation=conversation, store_id=membership.store_id
            )
        store = membership.store
        product_count = store.catalog_products.count()
        opening = "ouverte" if store.is_open else "fermée"
        return SellerWorkspaceOutcome(
            text=(
                f"Boutique : {store.name}\n"
                f"Ville : {store.city}\n"
                f"Statut : {store.get_status_display()}\n"
                f"Ouverture : {opening}\n"
                f"Produits au catalogue : {product_count}"
            ),
            actions=(
                SellerWorkspaceAction(
                    action_id=f"{self.catalog_prefix}{store.id}",
                    label="Gérer mon catalogue",
                ),
            ),
            metadata={"seller_workspace": True, "store_id": str(store.id)},
        )

    def activate_catalog(self, *, conversation, store_id=None) -> SellerWorkspaceOutcome:
        membership = self.resolve_membership(user=conversation.user, store_id=store_id)
        if membership is None:
            return self._forbidden()
        context = self.context_service.get_context(conversation)
        metadata = {
            **context.metadata,
            "seller_workspace_active": True,
            "seller_catalog_active": True,
            "seller_store_id": str(membership.store_id),
        }
        metadata.pop("task_paused", None)
        self.context_service.update_context(
            conversation,
            replace(
                context,
                active_journey=ActiveJourney.SELL,
                flow=ConversationFlow.IDLE,
                seller_onboarding=None,
                metadata=metadata,
            ),
        )
        return SellerWorkspaceOutcome(
            text=(
                f"Gestion du catalogue de « {membership.store.name} ».\n\n"
                "Vous pouvez voir le catalogue, ajouter un produit, modifier son prix "
                "ou sa disponibilité, ou retirer un produit. Que souhaitez-vous faire ?"
            ),
            metadata={
                "seller_workspace": True,
                "seller_catalog": True,
                "catalog_handoff": True,
                "store_id": str(membership.store_id),
            },
        )

    def resolve_membership(self, *, user, store_id=None):
        queryset = StoreMembership.objects.filter(
            user=user, status=StoreMembershipStatus.ACTIVE
        ).select_related("store")
        if store_id is not None:
            queryset = queryset.filter(store_id=store_id)
        return queryset.order_by("created_at", "id").first()

    def _set_workspace_context(self, conversation, store_id) -> None:
        context = self.context_service.get_context(conversation)
        metadata = {
            **context.metadata,
            "seller_workspace_active": True,
            "seller_catalog_active": False,
            "seller_store_id": str(store_id),
        }
        metadata.pop("task_paused", None)
        self.context_service.update_context(
            conversation,
            replace(
                context,
                active_journey=ActiveJourney.SELL,
                flow=ConversationFlow.IDLE,
                seller_onboarding=None,
                pending_seller_catalog_mutation=None,
                metadata=metadata,
            ),
        )

    @staticmethod
    def _operational_outcome(store):
        pending = (
            StoreOffer.objects.filter(
                store=store,
                status=StoreOfferStatus.PENDING,
                expires_at__gt=timezone.now(),
            )
            .select_related("order")
            .order_by("expires_at", "created_at")
            .first()
        )
        if pending is not None:
            return SellerWorkspaceOutcome(
                text=(
                    f"Une demande pour la commande {pending.order.reference} attend "
                    "votre réponse."
                ),
                actions=(
                    SellerWorkspaceAction(
                        action_id=encode_store_offer_action(
                            "store_offer.accept", pending.id
                        ),
                        label="Accepter",
                    ),
                    SellerWorkspaceAction(
                        action_id=encode_store_offer_action(
                            "store_offer.reject", pending.id
                        ),
                        label="Refuser",
                    ),
                ),
                metadata={
                    "seller_workspace": True,
                    "operational_priority": "STORE_OFFER",
                    "store_id": str(store.id),
                },
            )
        preparation = (
            StoreOffer.objects.filter(
                store=store,
                status=StoreOfferStatus.ACCEPTED,
                order__status__in=[OrderStatus.STORE_ACCEPTED, OrderStatus.PREPARING],
            )
            .select_related("order")
            .order_by("order__created_at")
            .first()
        )
        if preparation is None:
            return None
        if preparation.order.status == OrderStatus.STORE_ACCEPTED:
            action_id = "store_order.start_preparation"
            label = "Démarrer la préparation"
            text = f"La commande {preparation.order.reference} attend sa préparation."
        else:
            action_id = "store_order.mark_ready"
            label = "Commande prête"
            text = f"La commande {preparation.order.reference} est en préparation."
        return SellerWorkspaceOutcome(
            text=text,
            actions=(
                SellerWorkspaceAction(
                    action_id=encode_store_order_action(action_id, preparation.order_id),
                    label=label,
                ),
            ),
            metadata={
                "seller_workspace": True,
                "operational_priority": "STORE_PREPARATION",
                "store_id": str(store.id),
            },
        )

    @staticmethod
    def _forbidden():
        return SellerWorkspaceOutcome(
            text="Vous n'êtes pas autorisé à accéder à cette boutique.",
            metadata={"seller_workspace": True, "authorization_denied": True},
        )

    @staticmethod
    def _normalize(text: str) -> str:
        value = unicodedata.normalize("NFKD", text.casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    @staticmethod
    def _is_catalog_request(text: str) -> bool:
        return bool(
            re.search(
                r"\b(gerer|voir|consulter|afficher) (mon |le |mes )?catalogue\b|"
                r"\b(ajouter|modifier|supprimer|retirer|rendre) (un |le |mon )?produit\b|"
                r"\bmodifier (le )?prix\b|\bquels produits (ai je|j ai)\b",
                text,
            )
        )

    @staticmethod
    def _is_workspace_request(text: str) -> bool:
        return bool(
            re.fullmatch(r"(ma boutique|gerer ma boutique|voir ma boutique)", text)
            or re.search(r"\b(gerer|ouvrir|acceder a) (mon espace vendeur|ma boutique)\b", text)
        )

    @staticmethod
    def _is_next_step_question(text: str) -> bool:
        return bool(
            re.fullmatch(
                r"(que puis je faire( maintenant)?|"
                r"qu est ce que je peux faire( avec ma boutique)?|"
                r"c est quoi la suite|que dois je faire maintenant)",
                text,
            )
        )
