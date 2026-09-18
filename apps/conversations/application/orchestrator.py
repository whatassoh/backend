from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from django.contrib.gis.geos import Point

from apps.ai.domain.exceptions import AIError
from apps.ai.domain.understanding import MessageIntent
from apps.catalog.application.grounding import CatalogGroundingService
from apps.catalog.infrastructure.models import Product, ProductVariant
from apps.conversations.application.clarification import ClarificationEngine
from apps.conversations.application.context import ConversationContextService
from apps.conversations.domain.context import ConversationFlow
from apps.conversations.infrastructure.models import Conversation
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType
from apps.orders.application.services import (
    add_order_item,
    cancel_order,
    confirm_order,
    create_order_draft,
    mark_awaiting_location,
    set_delivery_location,
)
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderStatus,
)
from apps.orders.infrastructure.models import Order


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
    ) -> None:
        self.understanding_service = understanding_service
        self.grounding_service = grounding_service or CatalogGroundingService()
        self.knowledge_answer_service = knowledge_answer_service
        self.context_service = context_service or ConversationContextService()
        self.clarification_engine = clarification_engine or ClarificationEngine()

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
        action_id = message.metadata.get("action_id")
        if action_id:
            return self._handle_action(conversation, context, action_id, correlation_id)
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
        order = self._active_order(conversation, context)
        if order is None:
            order = create_order_draft(
                customer=conversation.user,
                actor=conversation.user,
                correlation_id=correlation_id,
            )
            context = replace(
                context,
                active_order_id=str(order.id),
                flow=ConversationFlow.ORDER_BUILDING,
            )
            self.context_service.update_context(conversation, context)
        clarification = self.clarification_engine.decide(
            context=context,
            understanding=understanding,
            resolutions=resolutions,
            requires_location=False,
        )
        if clarification.pending_clarification:
            self.context_service.set_pending_clarification(
                conversation,
                clarification.pending_clarification,
                flow=clarification.next_flow,
            )
            return self._from_clarification(clarification, context)
        for item, resolution in zip(understanding.items, resolutions, strict=True):
            product = (
                Product.objects.filter(
                    pk=resolution.product_id, status="ACTIVE"
                ).first()
                if resolution.product_id
                else None
            )
            variant = (
                ProductVariant.objects.filter(
                    pk=resolution.variant_id, product=product, status="ACTIVE"
                ).first()
                if resolution.variant_id
                else None
            )
            add_order_item(
                order=order,
                requested_name=item.requested_name,
                quantity=item.quantity,
                unit=item.unit,
                notes=item.notes,
                product=product,
                product_variant=variant,
                resolution_status=resolution.status,
                actor=conversation.user,
                correlation_id=correlation_id,
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
            address_label=message.location.address,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        context = replace(
            context,
            flow=ConversationFlow.AWAITING_CONFIRMATION,
            pending_clarification=None,
        )
        self.context_service.update_context(conversation, context)
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=self._recap(order),
            actions_performed=("delivery_location_set",),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _handle_action(self, conversation, context, action_id, correlation_id):
        if action_id == "order.confirm":
            return self._confirm_active_order(conversation, context, correlation_id)
        if action_id == "order.cancel":
            return self._cancel_active_order(conversation, context, correlation_id)
        return self._outcome(
            ConversationalOutcomeStatus.REJECTED,
            "Cette action n'est pas valide.",
            context,
        )

    def _confirm_active_order(self, conversation, context, correlation_id):
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à confirmer.",
                context,
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
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à annuler.",
                context,
            )
        order = cancel_order(
            order,
            OrderCancellationCode.CUSTOMER_REQUEST,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        self.context_service.clear_context(conversation)
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

    def _active_order(self, conversation, context):
        if not context.active_order_id:
            return None
        return Order.objects.filter(
            pk=context.active_order_id,
            customer=conversation.user,
            status__in=[OrderStatus.DRAFT, OrderStatus.AWAITING_LOCATION],
        ).first()

    def _recap(self, order):
        items = order.items.all()
        lines = [
            f"- {item.quantity} {item.unit} {item.requested_name}" for item in items
        ]
        return ResponsePlan(
            text="Récapitulatif :\n" + "\n".join(lines) + "\nConfirmez-vous ?",
            actions=(
                ResponseAction("order.confirm", "Confirmer"),
                ResponseAction("order.cancel", "Annuler"),
            ),
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
