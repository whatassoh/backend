import re
import logging
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
from apps.conversations.application.seller_onboarding import SellerOnboardingService
from apps.conversations.application.driver_onboarding import DriverOnboardingService
from apps.conversations.application.seller_catalog_conversation import SellerCatalogConversationService
from apps.conversations.domain.context import ConversationFlow
from apps.conversations.domain.context import ActiveJourney
from apps.conversations.application.journey import JourneyRoutingService, JourneyRoutingStatus
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
from apps.stores.application.preparation import StorePreparationResult, StorePreparationService

logger = logging.getLogger(__name__)


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
        journey_routing_service=None,
        seller_onboarding_service=None,
        driver_onboarding_service=None,
        seller_catalog_conversation_service=None,
        store_preparation_service=None,
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
        self.journey_routing_service = journey_routing_service or JourneyRoutingService(
            context_service=self.context_service
        )
        self.seller_onboarding_service = seller_onboarding_service or SellerOnboardingService(
            context_service=self.context_service
        )
        self.driver_onboarding_service = driver_onboarding_service or DriverOnboardingService(
            context_service=self.context_service
        )
        self.seller_catalog_conversation_service = seller_catalog_conversation_service
        self.store_preparation_service = store_preparation_service or StorePreparationService()

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
        onboarding = self.seller_onboarding_service.handle(conversation, context, message)
        if onboarding is not None:
            return self._from_seller_onboarding(onboarding, context)
        driver_onboarding = self.driver_onboarding_service.handle(conversation, context, message)
        if driver_onboarding is not None:
            return self._from_driver_onboarding(driver_onboarding, context)
        if self.seller_catalog_conversation_service:
            catalog = self.seller_catalog_conversation_service.process(
                conversation, context, message, correlation_id=correlation_id
            )
            if catalog is not None:
                return self._from_seller_catalog(catalog, context)
        if message.message_type == MessageType.LOCATION:
            if context.active_journey == ActiveJourney.DELIVER and message.location is not None:
                return self._handle_driver_location(conversation, context, message)
            return self._handle_location(conversation, context, message, correlation_id)
        if context.metadata.get("awaiting_delivery_instructions") and message.text:
            return self._handle_delivery_instructions(
                conversation, context, message.text, correlation_id
            )
        action_id = message.metadata.get("action_id")
        if action_id:
            logger.info("structured_action_resolved", extra={"correlation_id": correlation_id, "channel": message.channel, "action_id": action_id, "journey": context.active_journey, "flow": context.flow})
            driver_offer = self._driver_offer_action(conversation, context, action_id, correlation_id)
            if driver_offer is not None:
                return driver_offer
            delivery = self._delivery_action(conversation, context, action_id, correlation_id)
            if delivery is not None:
                return delivery
            journey = self.journey_routing_service.journey_for_action(action_id)
            if journey:
                return self._select_journey(conversation, context, journey)
            availability = self._driver_availability_action(conversation, context, action_id)
            if availability is not None:
                return availability
            return self._handle_action(
                conversation, context, action_id, correlation_id, message.metadata
            )
        availability = self._natural_driver_availability(conversation, context, message.text)
        if availability is not None:
            return availability
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
            return self._ask_journey(conversation, context)
        if understanding.intent == MessageIntent.SELL:
            return self._select_journey(conversation, context, ActiveJourney.SELL)
        if understanding.intent == MessageIntent.DELIVER:
            return self._select_journey(conversation, context, ActiveJourney.DELIVER)
        if understanding.intent == MessageIntent.CREATE_OR_UPDATE_ORDER:
            if context.active_journey != ActiveJourney.BUY:
                self.journey_routing_service.select(conversation, ActiveJourney.BUY)
                context = replace(context, active_journey=ActiveJourney.BUY)
        if understanding.intent == MessageIntent.GREETING:
            # Lightweight/unit callers can use an in-memory conversation stub;
            # recovery is persistence-backed and only applies to real rows.
            if not getattr(conversation, "pk", None):
                return self._ask_journey(conversation, context)
            recovery = self.recovery_service.recover(
                conversation, context, correlation_id=correlation_id
            )
            if recovery.status != RecoveryStatus.NO_ACTIVE_FLOW:
                return self._recovery_outcome(conversation, context, recovery)
            if context.active_journey is None:
                return self._ask_journey(conversation, context)
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Bonjour ! Que souhaitez-vous faire ?", context)
        if understanding.intent in {MessageIntent.HELP, MessageIntent.UNKNOWN}:
            if context.active_journey is None:
                return self._ask_journey(conversation, context)
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

    def _ask_journey(self, conversation, context):
        if getattr(conversation, "pk", None):
            self.context_service.update_context(
                conversation,
                replace(context, flow=ConversationFlow.AWAITING_JOURNEY_SELECTION),
            )
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text="Bonjour ! Que souhaitez-vous faire ?",
                actions=(
                    ResponseAction(action_id="journey.select.buy", label="Acheter"),
                    ResponseAction(action_id="journey.select.sell", label="Vendre"),
                    ResponseAction(action_id="journey.select.deliver", label="Livrer"),
                ),
                metadata={"journey_selection": True},
            ),
            active_order_id=context.active_order_id,
            metadata={"journey_selection": True},
        )

    def _select_journey(self, conversation, context, journey):
        decision = self.journey_routing_service.select(conversation, journey)
        context = replace(context, active_journey=journey)
        if decision.status == JourneyRoutingStatus.SELLER_ONBOARDING_REQUIRED:
            return self._with_journey_metadata(self._from_seller_onboarding(
                self.seller_onboarding_service.start(conversation), context
            ), journey, decision)
        if decision.status == JourneyRoutingStatus.DRIVER_ONBOARDING_REQUIRED:
            return self._with_journey_metadata(self._from_driver_onboarding(self.driver_onboarding_service.start(conversation), context), journey, decision)
        if journey == ActiveJourney.BUY:
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Parfait. Que souhaitez-vous commander ?", context, metadata={"journey": journey.value, "journey_status": decision.status.value})
        text = (
            "Votre parcours vendeur est prêt." if decision.status == JourneyRoutingStatus.SELLER_JOURNEY_READY
            else "Pour vendre, l'onboarding vendeur est requis."
        ) if journey == ActiveJourney.SELL else (
            "Votre parcours livreur est prêt." if decision.status == JourneyRoutingStatus.DRIVER_JOURNEY_READY
            else "Pour livrer, l'onboarding livreur est requis."
        )
        if journey == ActiveJourney.SELL and decision.status == JourneyRoutingStatus.SELLER_JOURNEY_READY:
            return self._with_journey_metadata(ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=ResponsePlan(
                    text=text,
                    actions=(ResponseAction(action_id="seller.dashboard.open", label="Ouvrir mon espace vendeur"),),
                    metadata={"seller_workspace": True},
                ),
                active_order_id=context.active_order_id,
                metadata={"seller_workspace": True},
            ), journey, decision)
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            text,
            context,
            metadata={
                "journey": journey.value,
                "journey_status": decision.status.value,
                "seller_workspace": journey == ActiveJourney.SELL,
            },
        )

    @staticmethod
    def _with_journey_metadata(outcome, journey, decision):
        metadata = {**outcome.metadata, "journey": journey.value, "journey_status": decision.status.value}
        response = replace(outcome.response, metadata={**outcome.response.metadata, "journey": journey.value, "journey_status": decision.status.value})
        return replace(outcome, response=response, metadata=metadata)

    def _from_seller_onboarding(self, onboarding, context):
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text=onboarding.text,
                actions=tuple(
                    ResponseAction(action_id=action.action_id, label=action.label)
                    for action in onboarding.actions
                ),
                request_location=onboarding.request_location,
                metadata=dict(onboarding.metadata),
            ),
            active_order_id=context.active_order_id,
            metadata=dict(onboarding.metadata),
        )

    def _from_driver_onboarding(self, onboarding, context):
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(text=onboarding.text, actions=tuple(ResponseAction(action_id=action.action_id, label=action.label) for action in onboarding.actions), metadata=dict(onboarding.metadata)),
            active_order_id=context.active_order_id,
            metadata=dict(onboarding.metadata),
        )

    def _driver_availability_action(self, conversation, context, action_id):
        if action_id not in {"driver.availability.available", "driver.availability.offline"}:
            return None
        if context.active_journey != ActiveJourney.DELIVER:
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette action est réservée au parcours livreur.", context)
        from apps.drivers.application.availability import DriverAvailabilityService
        from apps.drivers.application.selectors import get_driver_by_user
        driver = get_driver_by_user(conversation.user)
        if driver is None:
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Créez d'abord votre profil livreur.", context)
        service = DriverAvailabilityService()
        driver = service.set_available(driver, conversation.user) if action_id.endswith("available") else service.set_offline(driver, conversation.user)
        matchability = service.get_matchability(driver)
        text = "Vous êtes disponible." if action_id.endswith("available") else "Vous êtes hors ligne pour les nouvelles livraisons."
        if matchability.status.value == "LOCATION_REQUIRED":
            text += " Partagez votre position pour recevoir des livraisons."
        elif matchability.status.value == "LOCATION_REFRESH_REQUIRED":
            text += " Actualisez votre position pour recevoir des livraisons."
        return self._outcome(ConversationalOutcomeStatus.PROCESSED, text, context, metadata={"driver_availability": True, "matchability": matchability.status.value})

    def _driver_offer_action(self, conversation, context, action_id, correlation_id):
        if action_id not in {"driver_offer.accept", "driver_offer.reject"}:
            return None
        offer_id = context.metadata.get("driver_offer_id") if hasattr(context, "metadata") else None
        if not offer_id:
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette proposition n'est plus disponible.", context)
        from apps.drivers.application.dispatch import DriverMatchingOrchestrator
        from apps.drivers.infrastructure.models import DriverOffer
        offer = DriverOffer.objects.filter(pk=offer_id).first()
        if offer is None or offer.driver.user_id != conversation.user_id:
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette proposition n'est pas autorisée.", context)
        try:
            if action_id == "driver_offer.accept":
                DriverMatchingOrchestrator().handle_accept(offer, actor=conversation.user, correlation_id=correlation_id)
                from apps.drivers.application.delivery_execution import DeliveryExecutionService
                mission = DeliveryExecutionService().get_active_mission(conversation.user)
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    f"Livraison acceptée. Retrait : {mission.pickup.display_name}.", context,
                    metadata={"driver_offer": "ACCEPTED", "assignment_id": mission.assignment_id},
                    actions=(ResponseAction(action_id="delivery.confirm_pickup", label="Commande récupérée"),),
                )
            DriverMatchingOrchestrator().handle_reject(offer, actor=conversation.user, correlation_id=correlation_id)
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Proposition refusée.", context, metadata={"driver_offer": "REJECTED"})
        except (ValueError, PermissionError):
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette proposition n'est plus disponible.", context)

    def _delivery_action(self, conversation, context, action_id, correlation_id):
        issue_actions = {
            "delivery.issue.customer_unreachable": "CUSTOMER_UNREACHABLE",
            "delivery.issue.address_problem": "ADDRESS_PROBLEM",
            "delivery.issue.vehicle_problem": "VEHICLE_PROBLEM",
            "delivery.issue.other": "OTHER",
        }
        if action_id == "delivery.report_issue":
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Quel problème rencontrez-vous ?", context, actions=tuple(
                ResponseAction(action_id=key, label=label) for key, label in (
                    ("delivery.issue.customer_unreachable", "Client injoignable"),
                    ("delivery.issue.address_problem", "Problème d'adresse"),
                    ("delivery.issue.vehicle_problem", "Problème de véhicule"),
                    ("delivery.issue.other", "Autre problème"),
                )
            ))
        if action_id in issue_actions:
            from apps.drivers.application.delivery_execution import DeliveryExecutionService, DeliveryMissionAuthorizationError
            try:
                mission = DeliveryExecutionService().get_active_mission(conversation.channel_identity)
                issue, _ = DeliveryExecutionService().report_issue(conversation.channel_identity, assignment_id=mission.assignment_id, issue_type=issue_actions[action_id], correlation_id=correlation_id)
            except (DeliveryMissionAuthorizationError, ValueError):
                return self._outcome(ConversationalOutcomeStatus.REJECTED, "Ce signalement n'est plus disponible.", context)
            self.context_service.update_context(conversation, replace(context, metadata={**context.metadata, "delivery_issue_id": str(issue.id)}))
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Incident enregistré.", context, actions=(ResponseAction(action_id="delivery.confirm_failure", label="Confirmer l'impossibilité"),))
        if action_id == "delivery.confirm_failure":
            issue_id = context.metadata.get("delivery_issue_id")
            if not issue_id:
                return self._outcome(ConversationalOutcomeStatus.REJECTED, "Aucun incident à confirmer.", context)
            from apps.drivers.application.delivery_execution import DeliveryExecutionService, DeliveryMissionAuthorizationError
            try:
                mission = DeliveryExecutionService().get_active_mission(conversation.channel_identity)
                _, duplicate = DeliveryExecutionService().confirm_failure(conversation.channel_identity, assignment_id=mission.assignment_id, issue_id=issue_id, correlation_id=correlation_id)
            except (DeliveryMissionAuthorizationError, ValueError):
                return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette confirmation n'est plus disponible.", context)
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Échec de livraison déjà confirmé." if duplicate else "Échec de livraison confirmé.", context)
        if action_id not in {"delivery.confirm_pickup", "delivery.start_delivery", "delivery.confirm_delivered"}:
            return None
        from apps.drivers.application.delivery_execution import (
            DeliveryExecutionService,
            DeliveryMissionAuthorizationError,
            DeliveryMissionNotFound,
        )
        try:
            service = DeliveryExecutionService()
            result = (
                service.confirm_pickup(conversation.channel_identity, correlation_id=correlation_id)
                if action_id == "delivery.confirm_pickup"
                else service.start_delivery(conversation.channel_identity, correlation_id=correlation_id)
                if action_id == "delivery.start_delivery"
                else service.confirm_delivered(conversation.channel_identity, correlation_id=correlation_id)
            )
        except (DeliveryMissionAuthorizationError, DeliveryMissionNotFound, ValueError):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette confirmation de retrait n'est plus disponible.", context,
            )
        if action_id == "delivery.start_delivery":
            text = "Livraison déjà en cours." if result.duplicate else "Livraison démarrée."
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED, text, context,
                metadata={"delivery_start": "DUPLICATE" if result.duplicate else "CONFIRMED"},
                actions=(
                    ResponseAction(action_id="delivery.confirm_delivered", label="Commande livrée"),
                    ResponseAction(action_id="delivery.report_issue", label="Signaler un problème"),
                ),
            )
        if action_id == "delivery.confirm_delivered":
            text = "Livraison déjà terminée." if result.duplicate else "Livraison terminée."
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED, text, context,
                metadata={"delivery_completion": "DUPLICATE" if result.duplicate else "CONFIRMED"},
            )
        destination = result.mission.destination.address_label or "Destination enregistrée"
        prefix = "Retrait déjà confirmé." if result.duplicate else "Commande récupérée confirmée."
        text = f"{prefix}\nDestination : {destination}"
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED, text, context,
            metadata={"delivery_pickup": "DUPLICATE" if result.duplicate else "CONFIRMED"},
            actions=(ResponseAction(action_id="delivery.start_delivery", label="Démarrer la livraison"),),
        )

    def _natural_driver_availability(self, conversation, context, text):
        if context.active_journey != ActiveJourney.DELIVER or not text:
            return None
        normalized = " ".join(text.lower().split())
        action = None
        if normalized in {"je suis disponible", "disponible", "je suis en ligne"}:
            action = "driver.availability.available"
        elif normalized in {"je suis hors ligne", "hors ligne", "indisponible"}:
            action = "driver.availability.offline"
        return self._driver_availability_action(conversation, context, action) if action else None

    def _from_seller_catalog(self, catalog, context):
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text=catalog.text,
                actions=tuple(
                    ResponseAction(action_id=action.action_id, label=action.label)
                    for action in catalog.actions
                ),
                metadata=dict(catalog.metadata),
            ),
            active_order_id=context.active_order_id,
            metadata=dict(catalog.metadata),
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
        logger.info("location_received", extra={"correlation_id": correlation_id, "channel": message.channel, "journey": context.active_journey, "flow": context.flow})
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
        logger.info("location_persisted", extra={"correlation_id": correlation_id, "order_id": str(order.id), "order_status": order.status})
        context = replace(
            context,
            flow=ConversationFlow.ORDER_BUILDING,
            active_order_id=str(order.id),
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
                        action_id="order.skip_delivery_instructions", label="Non, continuer"
                    ),
                ),
            ),
            actions_performed=("delivery_location_set",),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _handle_driver_location(self, conversation, context, message):
        from django.utils import timezone
        from apps.drivers.application.availability import DriverAvailabilityService
        from apps.drivers.application.location import DriverLocationService, DriverLocationUpdate
        from apps.drivers.application.selectors import get_driver_by_user

        driver = get_driver_by_user(conversation.user)
        if driver is None:
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Créez d'abord votre profil livreur.", context)
        received_at = timezone.now()
        result = DriverLocationService().update_location(
            driver,
            DriverLocationUpdate(
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                occurred_at=message.occurred_at or received_at,
                received_at=received_at,
                source=str(message.channel),
                accuracy_meters=message.location.accuracy_meters,
            ),
            actor=conversation.user,
        )
        driver.refresh_from_db()
        matchability = DriverAvailabilityService().get_matchability(driver)
        text = "Position enregistrée." if result.status.value == "UPDATED" else "Cette position était déjà enregistrée ou plus ancienne."
        return self._outcome(ConversationalOutcomeStatus.PROCESSED, text, context, metadata={"driver_location": True, "matchability": matchability.status.value})

    def _handle_action(self, conversation, context, action_id, correlation_id, metadata):
        if action_id == "order.accept_partial_store_coverage":
            from apps.stores.application.partial_coverage import PartialCoverageConsentService
            proposal_id = metadata.get("partial_coverage_proposal_id")
            try:
                from apps.stores.infrastructure.models import PartialStoreCoverageProposal
                proposal = PartialStoreCoverageProposal.objects.filter(pk=proposal_id).first()
                if proposal is None:
                    raise ValueError("stale")
                order, replay = PartialCoverageConsentService().accept(order_id=proposal.order_id, customer=conversation.user, correlation_id=correlation_id)
            except (ValueError, PermissionError):
                return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette proposition n'est plus disponible.", context)
            return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Votre commande a été ajustée et nous recherchons à nouveau une boutique.", context)
        if action_id == "order.modify":
            # A partial proposal has no active draft context after confirmation;
            # restore the existing editing flow, never a second editor.
            from apps.stores.infrastructure.models import PartialStoreCoverageProposal
            proposal = PartialStoreCoverageProposal.objects.filter(order__customer=conversation.user, status="PENDING").select_related("order").first()
            if proposal and proposal.order.status == "AWAITING_PARTIAL_COVERAGE":
                from apps.stores.application.partial_coverage import PartialCoverageConsentService
                try:
                    order = PartialCoverageConsentService().return_to_editing(order_id=proposal.order_id, customer=conversation.user, correlation_id=correlation_id)
                    self.active_order_service.attach_order(conversation, str(order.id), correlation_id=correlation_id)
                except ValueError:
                    return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette proposition n'est plus disponible.", context)
                return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Que souhaitez-vous modifier dans votre commande ?", context)
        if action_id == "order.cancel":
            from apps.stores.infrastructure.models import PartialStoreCoverageProposal
            proposal = PartialStoreCoverageProposal.objects.filter(order__customer=conversation.user, status="PENDING").select_related("order").first()
            if proposal and proposal.order.status == "AWAITING_PARTIAL_COVERAGE":
                from apps.stores.application.partial_coverage import PartialCoverageConsentService
                try:
                    PartialCoverageConsentService().cancel(order_id=proposal.order_id, customer=conversation.user, correlation_id=correlation_id)
                except ValueError:
                    return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette proposition n'est plus disponible.", context)
                return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Votre commande est annulée.", context)
        if action_id in {"store_order.start_preparation", "store_order.mark_ready"}:
            target = "PREPARING" if action_id == "store_order.start_preparation" else "READY_FOR_PICKUP"
            order_id = self.store_preparation_service.available_order_for(
                actor=conversation.user, target=target
            )
            if order_id is None:
                return self._outcome(ConversationalOutcomeStatus.REJECTED, "Aucune commande de boutique unique ne correspond à cette action.", context)
            operation = self.store_preparation_service.start_preparation if target == "PREPARING" else self.store_preparation_service.mark_ready_for_pickup
            result = operation(actor=conversation.user, order_id=order_id, correlation_id=correlation_id)
            if result in {StorePreparationResult.STARTED, StorePreparationResult.ALREADY_PREPARING}:
                return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Préparation démarrée.", context)
            if result in {StorePreparationResult.READY, StorePreparationResult.ALREADY_READY}:
                return self._outcome(ConversationalOutcomeStatus.PROCESSED, "Commande marquée comme prête.", context)
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette commande ne peut plus être préparée.", context)
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
                                action_id="order.abandon_and_start_new", label="Abandonner et recommencer"
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
                        ResponseAction(action_id="order.resume", label="Reprendre"),
                        ResponseAction(action_id="order.start_new", label="Nouvelle commande"),
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
                    ResponseAction(action_id="order.modify", label="Modifier"),
                    ResponseAction(action_id="order.cancel", label="Annuler"),
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
                ResponseAction(action_id="order.confirm", label="Confirmer"),
                ResponseAction(action_id="order.modify", label="Modifier"),
                ResponseAction(action_id="order.change_location", label="Modifier l'adresse"),
                ResponseAction(action_id="order.cancel", label="Annuler"),
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
                text=text,
                request_location=str(clarification.response_instruction)
                == "REQUEST_LOCATION",
            ),
            actions_performed=actions,
            active_order_id=context.active_order_id,
            requires_followup=True,
        )

    def _outcome(self, status, text, context, metadata=None, actions=()):
        return ConversationalOutcome(
            status=status,
            response=ResponsePlan(text=text, actions=actions),
            active_order_id=context.active_order_id,
            metadata=metadata or {},
        )
