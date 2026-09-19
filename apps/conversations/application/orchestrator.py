import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from django.contrib.gis.geos import Point

from apps.ai.domain.exceptions import AIError
from apps.ai.domain.understanding import ExtractedItem, MessageIntent
from apps.catalog.application.grounding import CatalogGroundingService
from apps.conversations.application.active_orders import ActiveCustomerOrderService
from apps.conversations.application.clarification import ClarificationEngine
from apps.conversations.application.context import ConversationContextService
from apps.conversations.application.order_building import (
    ItemReference,
    ItemTargetStatus,
    OrderBuildingService,
)
from apps.conversations.application.recovery import (
    ConversationRecoveryService,
    RecoveryStatus,
)
from apps.conversations.domain.context import ConversationFlow
from apps.conversations.infrastructure.models import Conversation
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType
from apps.orders.application.services import (
    confirm_order,
    mark_awaiting_location,
    set_delivery_address_label,
    set_delivery_instructions,
    set_delivery_location,
)
from apps.orders.application.review import OrderReviewService


class ConversationalOutcomeStatus(StrEnum):
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
    RECOVERABLE_FAILURE = "RECOVERABLE_FAILURE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, kw_only=True)
class ResponseAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class ResponsePlan:
    text: str
    actions: tuple[ResponseAction, ...] = ()
    request_location: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ConversationalOutcome:
    status: ConversationalOutcomeStatus
    response: ResponsePlan
    actions_performed: tuple[str, ...] = ()
    active_order_id: str | None = None
    requires_followup: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationalOrchestrator:
    """Channel-neutral application composition; model output never writes ORM."""

    confidence_threshold = 0.60

    def __init__(
        self,
        *,
        understanding_service,
        grounding_service=None,
        knowledge_answer_service=None,
        context_service=None,
        clarification_engine=None,
        active_order_service=None,
        order_building_service=None,
        review_service=None,
        recovery_service=None,
    ) -> None:
        self.understanding_service = understanding_service
        self.grounding_service = grounding_service or CatalogGroundingService()
        self.knowledge_answer_service = knowledge_answer_service
        self.context_service = context_service or ConversationContextService()
        self.clarification_engine = clarification_engine or ClarificationEngine()
        self.active_order_service = active_order_service or ActiveCustomerOrderService()
        self.order_building_service = order_building_service or OrderBuildingService()
        self.review_service = review_service or OrderReviewService()
        self.recovery_service = recovery_service or ConversationRecoveryService(
            active_order_service=self.active_order_service,
            review_service=self.review_service,
        )

    def process_message(
        self,
        conversation: Conversation,
        message: NormalizedInboundMessage,
        *,
        correlation_id: str | None = None,
    ) -> ConversationalOutcome:
        context = self.context_service.get_context(conversation)
        if message.external_message_id in context.processed_message_ids:
            return self._outcome(
                ConversationalOutcomeStatus.DUPLICATE,
                "Ce message a déjà été traité.",
                context,
            )
        try:
            outcome = self._process(conversation, context, message, correlation_id)
        except AIError:
            return self._outcome(
                ConversationalOutcomeStatus.RECOVERABLE_FAILURE,
                "Je n'arrive pas à traiter votre demande pour le moment. Réessayez.",
                context,
            )
        # Mark only after a complete, successful decision; failures remain retryable.
        self.context_service.mark_message_processed(
            conversation, message.external_message_id
        )
        return outcome

    def _process(self, conversation, context, message, correlation_id):
        if message.message_type == MessageType.LOCATION:
            return self._handle_location(conversation, context, message, correlation_id)
        if context.metadata.get("awaiting_delivery_instructions") and message.text:
            return self._handle_delivery_instructions(
                conversation, context, message.text, correlation_id
            )
        action_id = message.metadata.get("action_id")
        if action_id:
            return self._handle_action(
                conversation, context, action_id, correlation_id, message.metadata
            )
        understanding = self.understanding_service.understand_normalized(
            message, correlation_id=correlation_id
        )
        if understanding is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette action n'est pas disponible.",
                context,
            )
        if float(understanding.confidence) < self.confidence_threshold:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Je n'ai pas bien compris. Pouvez-vous reformuler ?",
                context,
            )
        if understanding.intent == MessageIntent.GREETING:
            # Lightweight/unit callers can use an in-memory conversation stub;
            # recovery is persistence-backed and only applies to real rows.
            if not getattr(conversation, "pk", None):
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Bonjour ! Que souhaitez-vous commander ?",
                    context,
                )
            recovery = self.recovery_service.recover(
                conversation, context, correlation_id=correlation_id
            )
            if recovery.status != RecoveryStatus.NO_ACTIVE_FLOW:
                return self._recovery_outcome(conversation, context, recovery)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Bonjour ! Que souhaitez-vous commander ?",
                context,
            )
        if understanding.intent in {MessageIntent.HELP, MessageIntent.UNKNOWN}:
            return self._handle_knowledge_or_unknown(
                conversation,
                context,
                message.text or "",
                understanding.intent,
                correlation_id,
            )
        if understanding.intent == MessageIntent.CANCEL:
            return self._cancel_active_order(conversation, context, correlation_id)
        if understanding.intent == MessageIntent.CONFIRM:
            return self._confirm_active_order(conversation, context, correlation_id)
        if understanding.location_text and not understanding.items:
            return self._handle_text_address(
                conversation, context, understanding.location_text, correlation_id
            )
        if understanding.intent == MessageIntent.MODIFY:
            return self._handle_modify(
                conversation,
                context,
                understanding,
                message.text or "",
                correlation_id,
            )
        return self._handle_order_items(
            conversation, context, understanding, correlation_id
        )

    def _handle_order_items(self, conversation, context, understanding, correlation_id):
        if not understanding.items:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Quel article souhaitez-vous commander ?",
                context,
            )
        # A technical retrieval failure occurs before any Order mutation; it is
        # never recast as an unmatched product.
        resolutions = self.grounding_service.ground_items(understanding.items)
        decision = self.active_order_service.get_or_create_draft(
            conversation, correlation_id=correlation_id
        )
        order = decision.order
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette conversation est fermée; démarrez une nouvelle conversation pour commander.",
                context,
            )
        if decision.status.value == "CREATED":
            context = replace(
                context,
                active_order_id=str(order.id),
                flow=ConversationFlow.ORDER_BUILDING,
            )
            self.context_service.update_context(conversation, context)
        unresolved = []
        for item, resolution in zip(understanding.items, resolutions, strict=True):
            if item.quantity is None or item.unit is None:
                unresolved.append((item, resolution, "missing"))
            elif resolution.status == "AMBIGUOUS":
                unresolved.append((item, resolution, "ambiguous"))
            else:
                self.order_building_service.add(
                    order,
                    item,
                    resolution,
                    actor=conversation.user,
                    correlation_id=correlation_id,
                )
        if unresolved:
            item, resolution, reason = unresolved[0]
            clarification = self.clarification_engine.decide(
                context=context,
                understanding=understanding if reason == "missing" else None,
                resolutions=(resolution,) if reason == "ambiguous" else (),
            )
            self.context_service.set_pending_clarification(
                conversation,
                clarification.pending_clarification,
                flow=clarification.next_flow,
            )
            return self._from_clarification(
                clarification, context, actions=("order_draft_updated",)
            )
        mark_awaiting_location(
            order, actor=conversation.user, correlation_id=correlation_id
        )
        location_decision = self.clarification_engine.decide(
            context=context, requires_location=True
        )
        self.context_service.set_pending_clarification(
            conversation,
            location_decision.pending_clarification,
            flow=location_decision.next_flow,
        )
        return self._from_clarification(
            location_decision, context, actions=("order_draft_updated",)
        )

    def _handle_modify(self, conversation, context, understanding, text, correlation_id):
        """Only mutate when a backend target resolution is unique."""
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à modifier.",
                context,
            )
        lowered = text.casefold()
        target_name = None
        replacement = re.match(r"\s*remplace\s+(.+?)\s+par\s+(.+?)\s*$", text, re.I)
        if replacement:
            target = self.order_building_service.target(
                order, ItemReference(requested_name=replacement.group(1))
            )
            if target.status != ItemTargetStatus.FOUND:
                return self._ask_item_target(context, target)
            replacement_item = ExtractedItem(
                requested_name=replacement.group(2),
                quantity=target.item.quantity,
                unit=target.item.unit,
            )
            resolution = self.grounding_service.ground_item(replacement_item)
            if resolution.status == "AMBIGUOUS":
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Plusieurs produits correspondent. Lequel souhaitez-vous ?",
                    context,
                )
            self.order_building_service.replace(
                target.item,
                replacement_item,
                resolution,
                actor=conversation.user,
                correlation_id=correlation_id,
            )
            self.context_service.clear_pending_clarification(
                conversation, flow=ConversationFlow.ORDER_BUILDING
            )
            return self._modified_outcome(conversation, order, "order_item_replaced")
        for prefix in ("enlève ", "enleve ", "supprime "):
            if lowered.startswith(prefix):
                target_name = text[len(prefix) :].strip()
                target = self.order_building_service.target(
                    order, ItemReference(requested_name=target_name)
                )
                if target.status != ItemTargetStatus.FOUND:
                    return self._ask_item_target(context, target)
                self.order_building_service.remove(
                    target.item, actor=conversation.user, correlation_id=correlation_id
                )
                self.context_service.clear_pending_clarification(
                    conversation, flow=ConversationFlow.ORDER_BUILDING
                )
                return self._modified_outcome(conversation, order, "order_item_removed")
        if understanding.items:
            extracted = understanding.items[0]
            target = self.order_building_service.target(
                order, ItemReference(requested_name=extracted.requested_name)
            )
            if target.status == ItemTargetStatus.FOUND:
                self.order_building_service.update(
                    target.item,
                    quantity=extracted.quantity,
                    unit=extracted.unit,
                    actor=conversation.user,
                    correlation_id=correlation_id,
                )
                self.context_service.clear_pending_clarification(
                    conversation, flow=ConversationFlow.ORDER_BUILDING
                )
                return self._modified_outcome(conversation, order, "order_item_updated")
            return self._ask_item_target(context, target)
        target = self.order_building_service.target(order, ItemReference())
        return self._ask_item_target(context, target)

    def _ask_item_target(self, context, target):
        if target.status == ItemTargetStatus.AMBIGUOUS:
            labels = ", ".join(item.requested_name for item in target.candidates)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"Quel article souhaitez-vous modifier : {labels} ?",
                context,
            )
        return self._outcome(
            ConversationalOutcomeStatus.REJECTED,
            "Je ne retrouve pas cet article dans votre commande.",
            context,
        )

    def _modified_outcome(self, conversation, order, action):
        order.refresh_from_db()
        summary = self.order_building_service.summary(order)
        if not summary:
            self.context_service.clear_pending_clarification(
                conversation, flow=ConversationFlow.IDLE
            )
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=ResponsePlan(text="Votre commande est vide."),
                actions_performed=(action,),
                active_order_id=str(order.id),
            )
        self.context_service.clear_pending_clarification(
            conversation, flow=ConversationFlow.AWAITING_CONFIRMATION
        )
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=self._recap(order),
            actions_performed=(action,),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _handle_location(self, conversation, context, message, correlation_id):
        order = self._active_order(conversation, context)
        if order is None or message.location is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Je n'ai pas de commande active à localiser.",
                context,
            )
        point = Point(message.location.longitude, message.location.latitude, srid=4326)
        order = set_delivery_location(
            order=order,
            point=point,
            address_label=message.location.address or message.location.name,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        context = replace(
            context,
            flow=ConversationFlow.ORDER_BUILDING,
            pending_clarification=None,
            metadata={
                **context.metadata,
                "awaiting_delivery_instructions": True,
            },
        )
        self.context_service.update_context(conversation, context)
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text="Position enregistrée. Souhaitez-vous ajouter une indication pour le livreur ?",
                actions=(
                    ResponseAction(
                        "order.skip_delivery_instructions", "Non, continuer"
                    ),
                ),
            ),
            actions_performed=("delivery_location_set",),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _handle_action(self, conversation, context, action_id, correlation_id, metadata):
        if action_id == "order.confirm":
            return self._confirm_active_order(
                conversation,
                context,
                correlation_id,
                review_fingerprint=metadata.get("review_fingerprint"),
            )
        if action_id == "order.cancel":
            return self._cancel_active_order(conversation, context, correlation_id)
        if action_id == "order.modify":
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Que souhaitez-vous modifier dans votre commande ?",
                context,
            )
        if action_id == "order.change_location":
            context = replace(
                context,
                flow=ConversationFlow.AWAITING_LOCATION,
                pending_clarification=None,
                metadata={**context.metadata, "awaiting_delivery_instructions": False},
            )
            self.context_service.update_context(conversation, context)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Partagez votre nouvelle position pour mettre à jour la livraison.",
                context,
            )
        if action_id == "order.skip_delivery_instructions":
            context = replace(
                context,
                flow=ConversationFlow.AWAITING_CONFIRMATION,
                metadata={
                    **context.metadata,
                    "awaiting_delivery_instructions": False,
                },
            )
            self.context_service.update_context(conversation, context)
            order = self._active_order(conversation, context)
            if order is None:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Aucune commande active.",
                    context,
                )
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=self._recap(order),
                actions_performed=("delivery_instructions_skipped",),
                active_order_id=str(order.id),
                requires_followup=True,
            )
        if action_id == "order.resume":
            order_id = metadata.get("order_id")
            if not order_id:
                active = self.active_order_service.get_active_order(
                    conversation, correlation_id=correlation_id
                )
                order_id = active.order_id
            decision = self.active_order_service.resume_order(
                conversation,
                order_id or "",
                correlation_id=correlation_id,
            )
            if decision.order is None:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Cette commande ne peut pas être reprise.",
                    context,
                )
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=self._recap(decision.order),
                actions_performed=("order_resumed",),
                active_order_id=str(decision.order.id),
                requires_followup=True,
            )
        if action_id == "order.start_new":
            decision = self.active_order_service.start_new(
                conversation, correlation_id=correlation_id
            )
            if decision.requires_user_choice:
                return ConversationalOutcome(
                    status=ConversationalOutcomeStatus.PROCESSED,
                    response=ResponsePlan(
                        text="Vous avez déjà une commande en cours. Souhaitez-vous l'abandonner ?",
                        actions=(
                            ResponseAction(
                                "order.abandon_and_start_new", "Abandonner et recommencer"
                            ),
                        ),
                    ),
                    active_order_id=str(decision.order.id),
                    requires_followup=True,
                )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Indiquez les articles de votre nouvelle commande.",
                context,
            )
        if action_id == "order.abandon_and_start_new":
            decision = self.active_order_service.abandon_order(
                conversation, correlation_id=correlation_id
            )
            if decision.order is None:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Aucune commande active à abandonner.",
                    context,
                )
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=ResponsePlan(
                    text="Commande abandonnée. Indiquez les articles de votre nouvelle commande."
                ),
                actions_performed=("order_abandoned", "new_order_started"),
                active_order_id=str(decision.order.id),
            )
        return self._outcome(
            ConversationalOutcomeStatus.REJECTED,
            "Cette action n'est pas valide.",
            context,
        )

    def _confirm_active_order(
        self, conversation, context, correlation_id, review_fingerprint=None
    ):
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à confirmer.",
                context,
            )
        review = self.review_service.build_review(
            order, has_pending_clarification=context.pending_clarification is not None
        )
        if review_fingerprint and review_fingerprint != review.fingerprint:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette commande a été modifiée. Vérifiez le nouveau récapitulatif avant de confirmer.",
                context,
            )
        if not review.readiness.ready:
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.REJECTED,
                response=self._recap(order),
                active_order_id=str(order.id),
                metadata={"blocking_issues": review.readiness.blocking_issues},
            )
        order = confirm_order(
            order, actor=conversation.user, correlation_id=correlation_id
        )
        self.context_service.clear_context(conversation)
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(text="Votre commande est confirmée."),
            actions_performed=("order_confirmed",),
            active_order_id=str(order.id),
        )

    def _cancel_active_order(self, conversation, context, correlation_id):
        decision = self.active_order_service.abandon_order(
            conversation, correlation_id=correlation_id
        )
        order = decision.order
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à annuler.",
                context,
            )
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(text="Votre commande est annulée."),
            actions_performed=("order_cancelled",),
            active_order_id=str(order.id),
        )

    def _handle_knowledge_or_unknown(
        self, conversation, context, text, intent, correlation_id
    ):
        if intent == MessageIntent.HELP and self.knowledge_answer_service:
            answer = self.knowledge_answer_service.answer(
                text, correlation_id=correlation_id
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                answer.answer,
                context,
                metadata={"grounded": answer.grounded},
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "Je n'ai pas bien compris votre demande. Pouvez-vous la reformuler ?",
            context,
        )

    def _recovery_outcome(self, conversation, context, recovery):
        if recovery.status == RecoveryStatus.RESUME_CLARIFICATION:
            pending = context.pending_clarification
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"Votre commande est en cours. Pouvez-vous préciser : {pending.item_reference or pending.field} ?",
                context,
            )
        if recovery.status == RecoveryStatus.REQUEST_LOCATION:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Votre commande est en cours. Partagez votre position pour continuer.",
                context,
            )
        if recovery.status == RecoveryStatus.REVIEW_READY:
            order = self._active_order(conversation, context)
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=self._recap(order),
                active_order_id=str(order.id),
                requires_followup=True,
            )
        if recovery.status == RecoveryStatus.RESUME_DRAFT:
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=ResponsePlan(
                    text="Vous avez une commande en cours. Souhaitez-vous la reprendre ?",
                    actions=(
                        ResponseAction("order.resume", "Reprendre"),
                        ResponseAction("order.start_new", "Nouvelle commande"),
                    ),
                ),
                active_order_id=recovery.order_id,
                requires_followup=True,
            )
        if recovery.status == RecoveryStatus.STALE_CONTEXT_RECOVERED:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Votre ancienne commande n'est plus active. Que souhaitez-vous commander ?",
                context,
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "Bonjour ! Que souhaitez-vous commander ?",
            context,
        )

    def _handle_text_address(self, conversation, context, address, correlation_id):
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à localiser.",
                context,
            )
        set_delivery_address_label(
            order,
            address,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "Adresse enregistrée. Partagez aussi votre position pour une livraison précise.",
            context,
        )

    def _handle_delivery_instructions(self, conversation, context, text, correlation_id):
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active.",
                context,
            )
        order = set_delivery_instructions(
            order, text, actor=conversation.user, correlation_id=correlation_id
        )
        context = replace(
            context,
            flow=ConversationFlow.AWAITING_CONFIRMATION,
            metadata={**context.metadata, "awaiting_delivery_instructions": False},
        )
        self.context_service.update_context(conversation, context)
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=self._recap(order),
            actions_performed=("delivery_instructions_set",),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _active_order(self, conversation, context):
        return self.active_order_service.get_active_order(conversation).order

    def _recap(self, order):
        review = self.review_service.build_review(order)
        lines = [f"- {item.display_label}" for item in review.items]
        delivery = "Position partagée"
        if review.delivery_address_label:
            delivery += f" — {review.delivery_address_label}"
        if review.delivery_instructions:
            delivery += f"\nIndications : {review.delivery_instructions}"
        if not review.readiness.ready:
            blockers = ", ".join(review.readiness.blocking_issues)
            return ResponsePlan(
                text="Commande à compléter :\n" + "\n".join(lines) + f"\nÀ corriger : {blockers}",
                actions=(
                    ResponseAction("order.modify", "Modifier"),
                    ResponseAction("order.cancel", "Annuler"),
                ),
                metadata={"review_fingerprint": review.fingerprint},
            )
        return ResponsePlan(
            text=(
                "Récapitulatif :\n"
                + "\n".join(lines)
                + f"\nLivraison : {delivery}\nConfirmez-vous ?"
            ),
            actions=(
                ResponseAction("order.confirm", "Confirmer"),
                ResponseAction("order.modify", "Modifier"),
                ResponseAction("order.change_location", "Modifier l'adresse"),
                ResponseAction("order.cancel", "Annuler"),
            ),
            metadata={"review_fingerprint": review.fingerprint},
        )

    def _from_clarification(self, clarification, context, actions=()):
        item_reference = clarification.pending_clarification.item_reference
        text = {
            "ASK_QUANTITY": f"Quelle quantité de {item_reference} souhaitez-vous ?",
            "ASK_UNIT": "Quelle unité souhaitez-vous ?",
            "ASK_PRODUCT_CHOICE": "Lequel souhaitez-vous ?",
            "REQUEST_LOCATION": "Où souhaitez-vous être livré ?",
        }.get(
            str(clarification.response_instruction),
            "Pouvez-vous préciser votre demande ?",
        )
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text,
                request_location=str(clarification.response_instruction)
                == "REQUEST_LOCATION",
            ),
            actions_performed=actions,
            active_order_id=context.active_order_id,
            requires_followup=True,
        )

    def _outcome(self, status, text, context, metadata=None):
        return ConversationalOutcome(
            status=status,
            response=ResponsePlan(text=text),
            active_order_id=context.active_order_id,
            metadata=metadata or {},
        )
