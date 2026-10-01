"""Channel-neutral conversational proposal layer; only 8.3 mutates catalog rows."""

import logging
from dataclasses import dataclass, field, replace
from decimal import Decimal

from django.db import transaction

from apps.ai.application.seller_catalog_understanding import SellerCatalogIntent
from apps.ai.domain.understanding import ExtractedItem
from apps.catalog.application.grounding import CatalogGroundingService
from apps.catalog.application.seller_catalog import (
    SellerCatalogService,
    SellerCatalogStatus,
)
from apps.catalog.infrastructure.models import StoreProduct
from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    PendingSellerCatalogMutation,
    SellerCatalogAction,
    SellerOnboardingStage,
)
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.stores.domain.enums import StoreMembershipStatus
from apps.stores.infrastructure.models import StoreMembership
from apps.stores.infrastructure.models import Store

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class CatalogConversationAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class CatalogConversationOutcome:
    text: str
    actions: tuple[CatalogConversationAction, ...] = ()
    metadata: dict[str, object] = field(
        default_factory=lambda: {"seller_catalog": True}
    )


class SellerCatalogConversationService:
    confirm_action = "seller.catalog.confirm"
    modify_action = "seller.catalog.modify"
    cancel_action = "seller.catalog.cancel"
    confidence_threshold = Decimal("0.60")

    def __init__(
        self,
        *,
        understanding_service,
        grounding_service=None,
        catalog_service=None,
        context_service=None,
    ):
        from apps.conversations.application.context import ConversationContextService

        self.understanding_service = understanding_service
        self.grounding_service = grounding_service or CatalogGroundingService()
        self.catalog_service = catalog_service or SellerCatalogService()
        self.context_service = context_service or ConversationContextService()
        from apps.stores.application.dashboard_access import (
            SellerDashboardAccessService,
        )

        self.dashboard_access_service = SellerDashboardAccessService()

    def handles(self, context):
        return (
            context.active_journey == ActiveJourney.SELL
            and (
                context.seller_onboarding is None
                or context.seller_onboarding.stage == SellerOnboardingStage.COMPLETED
            )
            and (
                context.metadata.get("seller_catalog_active") is True
                or context.pending_seller_catalog_mutation is not None
            )
        )

    def process(
        self,
        conversation,
        context: ConversationContext,
        message,
        *,
        correlation_id=None,
    ):
        if not self.handles(context):
            return None
        action_id = message.metadata.get("action_id")
        if action_id:
            return self._action(conversation, context, action_id, correlation_id)
        if message.text and (
            "espace vendeur" in message.text.lower()
            or "dashboard" in message.text.lower()
        ):
            return self._dashboard_link(conversation, correlation_id)
        if not message.text:
            return CatalogConversationOutcome(
                text="Décrivez la modification souhaitée pour votre catalogue."
            )
        command = self.understanding_service.understand(
            message.text, correlation_id=correlation_id
        )
        logger.info(
            "seller_catalog_intent_detected",
            extra={"intent": command.intent, "correlation_id": correlation_id},
        )
        if command.intent == SellerCatalogIntent.LIST:
            return self._list(conversation, context)
        if (
            command.intent == SellerCatalogIntent.UNKNOWN
            or command.confidence < self.confidence_threshold
        ):
            return CatalogConversationOutcome(
                text="Je n'ai pas compris la modification du catalogue. Reformulez votre demande."
            )
        if not command.product_query:
            return CatalogConversationOutcome(
                text="Quel produit souhaitez-vous modifier ?"
            )
        store = self._store_for(conversation.user, context)
        if store is None:
            return CatalogConversationOutcome(
                text="Aucune boutique autorisée n'est disponible."
            )
        resolution = self.grounding_service.ground_item(
            ExtractedItem(requested_name=self._query(command))
        )
        if resolution.status == OrderItemResolutionStatus.UNMATCHED:
            logger.info(
                "seller_catalog_grounding_not_found",
                extra={"correlation_id": correlation_id},
            )
            return CatalogConversationOutcome(
                text="Je ne trouve pas encore ce produit dans le catalogue de référence."
            )
        action = self._action_for(command.intent)
        pending = PendingSellerCatalogMutation(
            action=action,
            store_id=str(store.id),
            price=str(command.price) if command.price is not None else None,
            currency=command.currency or "XAF",
            availability=command.availability,
        )
        if resolution.status == OrderItemResolutionStatus.AMBIGUOUS:
            candidates = tuple(
                {
                    "product_id": str(candidate.product_id),
                    "variant_id": str(candidate.variant_id)
                    if candidate.variant_id
                    else "",
                    "label": candidate.canonical_name,
                }
                for candidate in resolution.candidates
            )
            pending = replace(pending, candidates=candidates)
            self._save(conversation, context, pending)
            logger.info(
                "seller_catalog_grounding_ambiguous",
                extra={
                    "candidate_count": len(candidates),
                    "correlation_id": correlation_id,
                },
            )
            return CatalogConversationOutcome(
                text="Quel produit voulez-vous utiliser ?",
                actions=tuple(
                    CatalogConversationAction(
                        action_id=f"seller.catalog.select.{index}",
                        label=candidate["label"],
                    )
                    for index, candidate in enumerate(candidates)
                ),
            )
        pending = replace(
            pending,
            product_id=str(resolution.product_id),
            variant_id=str(resolution.variant_id) if resolution.variant_id else None,
        )
        return self._propose(conversation, context, pending, correlation_id)

    def _action(self, conversation, context, action_id, correlation_id):
        if action_id.startswith("store_order."):
            return None
        if action_id == "seller.dashboard.open":
            return self._dashboard_link(conversation, correlation_id)
        pending = context.pending_seller_catalog_mutation
        if action_id == self.cancel_action:
            self._clear(conversation, context)
            logger.info(
                "seller_catalog_mutation_cancelled",
                extra={"correlation_id": correlation_id},
            )
            return CatalogConversationOutcome(text="Modification du catalogue annulée.")
        if action_id == self.modify_action:
            self._clear(conversation, context)
            return CatalogConversationOutcome(
                text="D'accord. Décrivez la modification souhaitée."
            )
        if (
            action_id.startswith("seller.catalog.select.")
            and pending
            and pending.candidates
        ):
            try:
                candidate = pending.candidates[int(action_id.rsplit(".", 1)[1])]
            except (ValueError, IndexError):
                return CatalogConversationOutcome(
                    text="Ce choix n'est plus disponible."
                )
            updated = replace(
                pending,
                product_id=candidate["product_id"],
                variant_id=candidate["variant_id"] or None,
                candidates=(),
            )
            return self._propose(conversation, context, updated, correlation_id)
        if action_id == self.confirm_action and pending:
            return self._confirm(conversation, context, pending, correlation_id)
        return CatalogConversationOutcome(
            text="Cette action catalogue n'est pas disponible."
        )

    def _dashboard_link(self, conversation, correlation_id):
        result = self.dashboard_access_service.issue_access_link(
            actor=conversation.user,
            conversation=conversation,
            correlation_id=correlation_id,
        )
        if result.status != "ISSUED":
            return CatalogConversationOutcome(
                text="Vous n'êtes pas autorisé à ouvrir un espace vendeur."
            )
        return CatalogConversationOutcome(
            text=f"Ouvrez votre espace vendeur : {result.url}",
            metadata={"seller_catalog": True, "seller_dashboard": True},
        )

    def _propose(self, conversation, context, pending, correlation_id):
        failure = self._validate_pending(pending)
        if failure:
            return CatalogConversationOutcome(text=failure)
        if pending.action in {
            SellerCatalogAction.UPDATE_PRICE,
            SellerCatalogAction.SET_AVAILABILITY,
            SellerCatalogAction.REMOVE,
        }:
            row = StoreProduct.objects.filter(
                store_id=pending.store_id,
                product_id=pending.product_id,
                product_variant_id=pending.variant_id,
            ).first()
            if row is None:
                return CatalogConversationOutcome(
                    text="Ce produit n'est pas encore dans votre catalogue."
                )
            pending = replace(
                pending,
                store_product_id=str(row.id),
                expected_updated_at=row.updated_at.isoformat(),
            )
        self._save(conversation, context, pending)
        logger.info(
            "seller_catalog_mutation_proposed",
            extra={"action": pending.action, "correlation_id": correlation_id},
        )
        details = self._description(pending)
        return CatalogConversationOutcome(
            text=f"Opération proposée :\n{details}",
            actions=(
                CatalogConversationAction(
                    action_id=self.confirm_action, label="Confirmer"
                ),
                CatalogConversationAction(
                    action_id=self.modify_action, label="Modifier"
                ),
                CatalogConversationAction(
                    action_id=self.cancel_action, label="Annuler"
                ),
            ),
        )

    @transaction.atomic
    def _confirm(self, conversation, context, pending, correlation_id):
        from apps.conversations.infrastructure.models import Conversation

        locked = (
            Conversation.objects.select_for_update()
            .select_related("user")
            .get(pk=conversation.pk)
        )
        current = ConversationContext.from_payload(locked.context)
        pending = current.pending_seller_catalog_mutation
        if not pending or current.active_journey != ActiveJourney.SELL:
            return CatalogConversationOutcome(
                text="Cette proposition n'est plus disponible."
            )
        if not StoreMembership.objects.filter(
            store_id=pending.store_id,
            user=locked.user,
            status=StoreMembershipStatus.ACTIVE,
        ).exists():
            return CatalogConversationOutcome(
                text="Vous n'êtes plus autorisé à modifier cette boutique."
            )
        result = self._apply(locked.user, pending, correlation_id)
        if result.status != SellerCatalogStatus.SUCCESS:
            return CatalogConversationOutcome(
                text=f"La proposition doit être revue : {result.status.value}."
            )
        locked.context = replace(
            current, pending_seller_catalog_mutation=None
        ).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        logger.info(
            "seller_catalog_mutation_confirmed",
            extra={"action": pending.action, "correlation_id": correlation_id},
        )
        return CatalogConversationOutcome(text="Catalogue mis à jour.")

    def _apply(self, actor, pending, correlation_id):
        if pending.action == SellerCatalogAction.ADD:
            store = Store.objects.filter(pk=pending.store_id).first()
            if store is None:
                return type("Result", (), {"status": SellerCatalogStatus.FORBIDDEN})()
            return self.catalog_service.add_product(
                actor=actor,
                store=store,
                product_id=pending.product_id,
                variant_id=pending.variant_id,
                price=Decimal(pending.price) if pending.price else None,
                currency=pending.currency or "XAF",
                availability=pending.availability or "UNKNOWN",
                correlation_id=correlation_id,
            )
        row = StoreProduct.objects.filter(
            pk=pending.store_product_id, store_id=pending.store_id
        ).first()
        if row is None or (
            pending.expected_updated_at
            and row.updated_at.isoformat() != pending.expected_updated_at
        ):
            return type(
                "Result", (), {"status": SellerCatalogStatus.STORE_PRODUCT_NOT_FOUND}
            )()
        if pending.action == SellerCatalogAction.UPDATE_PRICE:
            return self.catalog_service.update_price(
                actor=actor,
                store_product_id=row.id,
                price=Decimal(pending.price),
                currency=pending.currency or "XAF",
                correlation_id=correlation_id,
            )
        availability = (
            "UNAVAILABLE"
            if pending.action == SellerCatalogAction.REMOVE
            else pending.availability
        )
        return self.catalog_service.set_availability(
            actor=actor,
            store_product_id=row.id,
            availability=availability,
            correlation_id=correlation_id,
        )

    def _list(self, conversation, context):
        store = self._store_for(conversation.user, context)
        if store is None:
            return CatalogConversationOutcome(
                text="Aucune boutique autorisée n'est disponible."
            )
        result = self.catalog_service.list_catalog(actor=conversation.user, store=store)
        if result.status != SellerCatalogStatus.SUCCESS:
            return CatalogConversationOutcome(
                text="Vous n'êtes pas autorisé à voir ce catalogue."
            )
        items = result.items[:10]
        if not items:
            return CatalogConversationOutcome(text="Votre catalogue est vide.")
        suffix = "\n…" if len(result.items) > len(items) else ""
        return CatalogConversationOutcome(
            text="Votre catalogue :\n"
            + "\n".join(
                f"- {item.product.name}: {item.price if item.price is not None else 'prix non déclaré'} ({item.availability_status})"
                for item in items
            )
            + suffix
        )

    @staticmethod
    def _store_for(user, context):
        membership = (
            StoreMembership.objects.filter(
                user=user, status=StoreMembershipStatus.ACTIVE
            )
            .select_related("store")
            .order_by("created_at")
        )
        selected_store_id = context.metadata.get("seller_store_id")
        if selected_store_id:
            membership = membership.filter(store_id=selected_store_id)
        membership = membership.first()
        return membership.store if membership else None

    @staticmethod
    def _action_for(intent):
        return {
            SellerCatalogIntent.ADD: SellerCatalogAction.ADD,
            SellerCatalogIntent.UPDATE_PRICE: SellerCatalogAction.UPDATE_PRICE,
            SellerCatalogIntent.SET_AVAILABILITY: SellerCatalogAction.SET_AVAILABILITY,
            SellerCatalogIntent.REMOVE: SellerCatalogAction.REMOVE,
        }[intent]

    @staticmethod
    def _query(command):
        return " ".join(
            part for part in (command.product_query, command.variant_query) if part
        )

    @staticmethod
    def _validate_pending(pending):
        if (
            pending.action
            in {SellerCatalogAction.ADD, SellerCatalogAction.UPDATE_PRICE}
            and pending.price is None
        ):
            return "Indiquez un prix avant de confirmer."
        if (
            pending.action == SellerCatalogAction.SET_AVAILABILITY
            and pending.availability not in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
        ):
            return "Indiquez la disponibilité souhaitée."
        return None

    def _save(self, conversation, context, pending):
        self.context_service.update_context(
            conversation, replace(context, pending_seller_catalog_mutation=pending)
        )

    def _clear(self, conversation, context):
        self.context_service.update_context(
            conversation, replace(context, pending_seller_catalog_mutation=None)
        )

    @staticmethod
    def _description(pending):
        return f"Action : {pending.action}\nProduit : {pending.product_id}\nPrix : {pending.price or 'non déclaré'}\nDisponibilité : {pending.availability or 'inchangée'}"
