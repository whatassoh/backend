import logging
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.ai.domain.exceptions import AIError
from apps.ai.domain.understanding import (
    ExtractedItem,
    ExtractedUnit,
    InteractionMode,
    JourneyGoal,
    MessageIntent,
    OrderAction,
    TaskTarget,
)
from apps.catalog.application.grounding import CatalogGroundingService
from apps.catalog.domain.enums import ProductStatus, ProductVariantStatus
from apps.catalog.infrastructure.models import Product, ProductVariant
from apps.conversations.application.active_orders import ActiveCustomerOrderService
from apps.conversations.application.capabilities import (
    NOT_IMPLEMENTED_RESPONSES,
    OUT_OF_SCOPE_RESPONSE,
    SOCIAL_RESPONSE,
    UNKNOWN_RESPONSE,
    CapabilityStatus,
    classify_capability,
)
from apps.conversations.application.clarification import ClarificationEngine
from apps.conversations.application.context import ConversationContextService
from apps.conversations.application.context_window import (
    ConversationContextWindowBuilder,
)
from apps.conversations.application.driver_onboarding import DriverOnboardingService
from apps.conversations.application.driver_workspace import (
    DriverWorkspaceRequest,
    DriverWorkspaceService,
)
from apps.conversations.application.interruptions import (
    UniversalCommand,
    active_task_kind,
    detect_universal_command,
    discard_suspended_kind,
    has_resumable_work,
    resume_last_task,
    suspend_active_task,
)
from apps.conversations.application.journey import (
    JourneyRoutingService,
    JourneyRoutingStatus,
)
from apps.conversations.application.observability import (
    DecisionTimer,
    observe_ai_failure,
    observe_decision,
)
from apps.conversations.application.order_building import (
    ItemReference,
    ItemTargetStatus,
    OrderBuildingService,
)
from apps.conversations.application.product_discovery import (
    ProductDiscoveryConversationService,
)
from apps.conversations.application.recovery import (
    ConversationRecoveryService,
    RecoveryStatus,
)
from apps.conversations.application.seller_onboarding import SellerOnboardingService
from apps.conversations.application.seller_workspace import (
    SellerWorkspaceRequest,
    SellerWorkspaceService,
)
from apps.conversations.domain.context import (
    ActiveJourney,
    ClarificationKind,
    ConversationFlow,
    ConversationSocialState,
    ConversationTaskKind,
    DriverOnboardingStage,
    PendingClarification,
    SellerOnboardingStage,
)
from apps.conversations.infrastructure.models import Conversation
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType
from apps.orders.application.review import OrderReviewService
from apps.orders.application.selectors import get_customer_orders
from apps.orders.application.services import (
    confirm_order,
    mark_awaiting_location,
    set_delivery_address_label,
    set_delivery_instructions,
    set_delivery_location,
)
from apps.orders.domain.enums import OrderItemResolutionStatus
from apps.stores.application.preparation import (
    StorePreparationResult,
    StorePreparationService,
)

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
        knowledge_answer_enabled=False,
        knowledge_answer_shadow=False,
        context_service=None,
        clarification_engine=None,
        active_order_service=None,
        order_building_service=None,
        review_service=None,
        recovery_service=None,
        journey_routing_service=None,
        seller_onboarding_service=None,
        seller_workspace_service=None,
        driver_onboarding_service=None,
        driver_workspace_service=None,
        product_discovery_service=None,
        seller_catalog_conversation_service=None,
        store_preparation_service=None,
        store_offer_action_service=None,
        context_window_builder=None,
        active_agent_service=None,
    ) -> None:
        self.understanding_service = understanding_service
        self.grounding_service = grounding_service or CatalogGroundingService()
        self.knowledge_answer_service = knowledge_answer_service
        self.knowledge_answer_enabled = knowledge_answer_enabled
        self.knowledge_answer_shadow = knowledge_answer_shadow
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
        self.seller_onboarding_service = (
            seller_onboarding_service
            or SellerOnboardingService(context_service=self.context_service)
        )
        self.seller_workspace_service = seller_workspace_service or SellerWorkspaceService(
            context_service=self.context_service
        )
        self.driver_onboarding_service = (
            driver_onboarding_service
            or DriverOnboardingService(context_service=self.context_service)
        )
        self.driver_workspace_service = driver_workspace_service or DriverWorkspaceService(
            context_service=self.context_service
        )
        self.product_discovery_service = (
            product_discovery_service
            or ProductDiscoveryConversationService(context_service=self.context_service)
        )
        self.seller_catalog_conversation_service = seller_catalog_conversation_service
        self.store_preparation_service = (
            store_preparation_service or StorePreparationService()
        )
        self.store_offer_action_service = store_offer_action_service
        self.context_window_builder = (
            context_window_builder or ConversationContextWindowBuilder()
        )
        self.active_agent_service = active_agent_service

    def process_message(
        self,
        conversation: Conversation,
        message: NormalizedInboundMessage,
        *,
        correlation_id: str | None = None,
    ) -> ConversationalOutcome:
        timer = DecisionTimer.start()
        if getattr(conversation, "pk", None):
            conversation.refresh_from_db()
        context = self.context_service.get_context(conversation)
        flow_before = context.flow
        if message.external_message_id in context.processed_message_ids:
            outcome = self._outcome(
                ConversationalOutcomeStatus.DUPLICATE,
                "Ce message a déjà été traité.",
                context,
            )
            observe_decision(
                conversation=conversation,
                message=message,
                outcome=outcome,
                flow_before=flow_before,
                timer=timer,
            )
            return outcome
        try:
            outcome = self._process(conversation, context, message, correlation_id)
        except AIError as exc:
            observe_ai_failure(channel=message.channel, error=exc)
            logger.warning(
                "conversation_ai_processing_failed",
                extra={
                    "correlation_id": correlation_id,
                    "channel": message.channel,
                    "error_category": exc.__class__.__name__,
                    "retryable": bool(getattr(exc, "retryable", False)),
                },
            )
            raise
        except Exception:
            logger.exception(
                "conversation_orchestration_unexpected",
                extra={
                    "correlation_id": correlation_id,
                    "channel": message.channel,
                    "normalized_message_type": message.message_type,
                    "action_id_present": bool(message.metadata.get("action_id")),
                    "flow_before": context.flow,
                },
            )
            raise
        # Mark only after a complete, successful decision; failures remain retryable.
        self.context_service.mark_message_processed(
            conversation, message.external_message_id
        )
        observe_decision(
            conversation=conversation,
            message=message,
            outcome=outcome,
            flow_before=flow_before,
            timer=timer,
        )
        logger.info(
            "conversation_turn_completed",
            extra={
                "correlation_id": correlation_id,
                "channel": message.channel,
                "normalized_message_type": message.message_type,
                "action_id_present": bool(message.metadata.get("action_id")),
                "flow_before": context.flow,
                "flow_after": self.context_service.get_context(conversation).flow,
                "outcome_type": outcome.status,
                "outbound_type": "response_plan",
            },
        )
        return outcome

    def _process(self, conversation, context, message, correlation_id):
        conversation_context = self.context_window_builder.build(
            conversation,
            context,
            current_message_id=message.external_message_id,
        )
        if not message.metadata.get("action_id"):
            resumed_store_offer = self._resume_whatsapp_store_offer(
                conversation, context, message
            )
            if resumed_store_offer is not None:
                return resumed_store_offer
            resumed_driver_offer = self._resume_whatsapp_driver_offer(
                conversation, context, message
            )
            if resumed_driver_offer is not None:
                return resumed_driver_offer
        action_id = message.metadata.get("action_id")
        workspace_callback = self.seller_workspace_service.handles_action(action_id)
        legacy_without_store = (
            action_id in self.seller_workspace_service.legacy_actions
            and self.seller_workspace_service.resolve_membership(
                user=conversation.user
            )
            is None
        )
        if workspace_callback and not legacy_without_store:
            # A live membership is resolved for every callback. Legacy onboarding
            # callbacks therefore become idempotent workspace navigation.
            workspace = self.seller_workspace_service.handle_action(
                conversation=conversation, action_id=action_id
            )
            return self._from_seller_workspace(workspace, context)
        workspace_request = self.seller_workspace_service.classify(
            user=conversation.user, context=context, text=message.text
        )
        if workspace_request == SellerWorkspaceRequest.OPEN:
            return self._select_journey(conversation, context, ActiveJourney.SELL)
        if workspace_request == SellerWorkspaceRequest.CATALOG:
            membership = self.seller_workspace_service.resolve_membership(
                user=conversation.user
            )
            workspace = self.seller_workspace_service.activate_catalog(
                conversation=conversation,
                store_id=membership.store_id if membership else None,
            )
            # A direct command such as "voir mon catalogue" is handed to the
            # existing catalog flow in the same turn; no catalog logic is copied.
            if self.seller_catalog_conversation_service and not workspace.metadata.get(
                "authorization_denied"
            ):
                context = self.context_service.get_context(conversation)
                catalog = self.seller_catalog_conversation_service.process(
                    conversation, context, message, correlation_id=correlation_id
                )
                if catalog is not None:
                    return self._from_seller_catalog(catalog, context)
            return self._from_seller_workspace(workspace, context)
        driver_workspace_callback = self.driver_workspace_service.handles_action(
            action_id
        )
        legacy_driver_without_profile = (
            action_id in self.driver_workspace_service.legacy_actions
            and self.driver_workspace_service.resolve_driver(conversation.user) is None
        )
        if driver_workspace_callback and not legacy_driver_without_profile:
            workspace = self.driver_workspace_service.present(
                conversation=conversation
            )
            return self._from_driver_workspace(workspace, context)
        if self.product_discovery_service.handles_action(action_id):
            discovery = self.product_discovery_service.continue_from_callback(
                conversation=conversation,
                context=context,
            )
            return self._from_product_discovery(discovery, context)
        driver_workspace_request = self.driver_workspace_service.classify(
            user=conversation.user, context=context, text=message.text
        )
        if driver_workspace_request == DriverWorkspaceRequest.OPEN:
            return self._select_journey(conversation, context, ActiveJourney.DELIVER)
        universal = self._try_universal_interrupt(
            conversation,
            context,
            message,
            correlation_id,
            conversation_context,
        )
        if universal is not None:
            return universal
        action_id = message.metadata.get("action_id")
        if action_id == "conversation.resume":
            return self._resume_workflow(conversation, context, correlation_id)
        if action_id == "conversation.pause":
            return self._pause_current_task(conversation, context)
        if action_id == "conversation.back":
            return self._back_current_task(conversation, context)
        if action_id == "conversation.cancel":
            return self._cancel_current_task(
                conversation, context, correlation_id=correlation_id
            )
        onboarding = self.seller_onboarding_service.handle(
            conversation, context, message
        )
        if onboarding is not None:
            if onboarding.metadata.get("open_seller_workspace"):
                workspace = self.seller_workspace_service.present(
                    conversation=conversation,
                    store_id=onboarding.metadata.get("store_id"),
                )
                return self._from_seller_workspace(workspace, context)
            return self._from_seller_onboarding(onboarding, context)
        driver_onboarding = self.driver_onboarding_service.handle(
            conversation, context, message
        )
        if driver_onboarding is not None:
            if driver_onboarding.metadata.get("open_driver_workspace"):
                workspace = self.driver_workspace_service.present(
                    conversation=conversation
                )
                return self._from_driver_workspace(workspace, context)
            return self._from_driver_onboarding(driver_onboarding, context)
        if self.seller_catalog_conversation_service:
            catalog = self.seller_catalog_conversation_service.process(
                conversation, context, message, correlation_id=correlation_id
            )
            if catalog is not None:
                return self._from_seller_catalog(catalog, context)
        if message.message_type == MessageType.LOCATION:
            if (
                context.active_journey == ActiveJourney.DELIVER
                and message.location is not None
            ):
                return self._handle_driver_location(conversation, context, message)
            return self._handle_location(conversation, context, message, correlation_id)
        if (
            context.metadata.get("awaiting_delivery_instructions")
            and message.text
            and not context.metadata.get("task_paused")
        ):
            return self._handle_delivery_instructions(
                conversation, context, message.text, correlation_id
            )
        action_id = message.metadata.get("action_id")
        if action_id:
            logger.info(
                "structured_action_resolved",
                extra={
                    "correlation_id": correlation_id,
                    "channel": message.channel,
                    "action_id": action_id,
                    "journey": context.active_journey,
                    "flow": context.flow,
                },
            )
            choice = self._clarification_choice_action(
                conversation, context, action_id, correlation_id
            )
            if choice is not None:
                return choice
            store_offer = self._store_offer_action(
                conversation, context, action_id, correlation_id
            )
            if store_offer is not None:
                return store_offer
            store_order = self._store_order_action(
                conversation, context, action_id, correlation_id
            )
            if store_order is not None:
                return store_order
            driver_offer = self._driver_offer_action(
                conversation, context, action_id, correlation_id
            )
            if driver_offer is not None:
                return driver_offer
            delivery = self._delivery_action(
                conversation, context, action_id, correlation_id
            )
            if delivery is not None:
                return delivery
            journey = self.journey_routing_service.journey_for_action(action_id)
            if journey:
                return self._select_journey(conversation, context, journey)
            availability = self._driver_availability_action(
                conversation, context, action_id
            )
            if availability is not None:
                return availability
            return self._handle_action(
                conversation, context, action_id, correlation_id, message.metadata
            )
        availability = self._natural_driver_availability(
            conversation, context, message.text
        )
        if availability is not None:
            return availability
        understanding = self.understanding_service.understand_normalized(
            message,
            limited_context=conversation_context,
            correlation_id=correlation_id,
        )
        if understanding is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette action n'est pas disponible.",
                context,
            )
        understanding = self._normalize_understanding(understanding)
        if understanding.intent == MessageIntent.CREATE_OR_UPDATE_ORDER:
            understanding = self.product_discovery_service.enrich_order_understanding(
                understanding, message.text or "", context
            )
        compound = self._handle_compound_turn(
            conversation,
            context,
            understanding,
            message,
            correlation_id,
            conversation_context,
        )
        if compound is not None:
            return compound
        agent_decision = self._run_active_agent(
            conversation, message, understanding, correlation_id
        )
        variant_text_choice = self._complete_variant_text_selection(
            conversation, context, understanding, correlation_id
        )
        if variant_text_choice is not None:
            return variant_text_choice
        replacement = self._complete_unmatched_replacement(
            conversation, context, understanding, correlation_id
        )
        if replacement is not None:
            return replacement
        pending_item = self._complete_pending_item_slots(
            conversation, context, understanding, correlation_id
        )
        if pending_item is not None:
            return pending_item
        if understanding.intent == MessageIntent.MEMORY_SHOW:
            return self._memory_show_outcome(conversation, context)
        if understanding.intent == MessageIntent.MEMORY_FORGET:
            return self._memory_forget_outcome(conversation, context)
        capability = classify_capability(understanding.intent)
        logger.info(
            "conversation_capability_classified",
            extra={
                "correlation_id": correlation_id,
                "channel": message.channel,
                "intent": understanding.intent,
                "confidence": str(understanding.confidence),
                "capability": capability.name,
                "capability_status": capability.status,
            },
        )
        if float(understanding.confidence) < self.confidence_threshold:
            return self._capability_outcome(context, None, conversation=conversation)
        if understanding.intent == MessageIntent.ORDER_STATUS:
            status_outcome = self._agent_order_status_outcome(
                context, agent_decision
            )
            if status_outcome is not None:
                return status_outcome
        if capability.status != CapabilityStatus.SUPPORTED:
            return self._capability_outcome(
                context, capability, conversation=conversation
            )
        if understanding.intent == MessageIntent.SELL:
            return self._select_journey(conversation, context, ActiveJourney.SELL)
        if understanding.intent == MessageIntent.DELIVER:
            return self._select_journey(conversation, context, ActiveJourney.DELIVER)
        if understanding.intent == MessageIntent.CREATE_OR_UPDATE_ORDER:
            if context.active_journey != ActiveJourney.BUY:
                self.journey_routing_service.select(conversation, ActiveJourney.BUY)
                context = replace(context, active_journey=ActiveJourney.BUY)
        if understanding.intent == MessageIntent.ORDER_RESUME:
            return self._resume_workflow(
                conversation,
                context,
                correlation_id,
                target=understanding.task_target,
            )
        if understanding.intent == MessageIntent.PAUSE:
            return self._pause_current_task(conversation, context)
        if understanding.intent == MessageIntent.BACK:
            return self._back_current_task(conversation, context)
        if understanding.intent == MessageIntent.ORDER_HISTORY:
            return self._order_history_outcome(
                conversation, context, agent_decision=agent_decision
            )
        if understanding.intent == MessageIntent.CATALOG_BROWSE:
            discovery = self.product_discovery_service.discover(
                conversation=conversation,
                context=context,
                understanding=understanding,
                text=message.text or "",
            )
            return self._from_product_discovery(discovery, context)
        if understanding.intent == MessageIntent.HELP:
            return self._handle_knowledge_or_unknown(
                conversation,
                context,
                message.text or "",
                understanding.intent,
                correlation_id,
                retrieval_query=understanding.standalone_question,
                conversation_context=conversation_context,
            )
        if (
            understanding.intent == MessageIntent.CANCEL
            or understanding.order_action == OrderAction.CANCEL
        ):
            if not self._task_target_matches_context(understanding, context):
                return self._task_target_clarification(context, understanding)
            return self._cancel_current_task(
                conversation, context, correlation_id=correlation_id
            )
        if (
            understanding.intent == MessageIntent.CONFIRM
            or understanding.order_action == OrderAction.CONFIRM
        ):
            # A textual yes is meaningful only after this conversation presented a review.
            if context.flow != ConversationFlow.AWAITING_CONFIRMATION:
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Je peux confirmer une commande après son récapitulatif. Que souhaitez-vous commander ?",
                    context,
                )
            return self._confirm_active_order(conversation, context, correlation_id)
        if understanding.order_action == OrderAction.RESUME:
            return self._resume_workflow(
                conversation,
                context,
                correlation_id,
                target=understanding.task_target,
            )
        if understanding.order_action == OrderAction.CHANGE_LOCATION:
            return self._start_location_change(conversation, context)
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

    def _resume_whatsapp_store_offer(self, conversation, context, message):
        """After a template reply, present one still-live offer with exact callbacks."""
        if (
            message.channel != "WHATSAPP"
            or not message.text
            or not getattr(conversation, "pk", None)
            or not getattr(conversation, "user_id", None)
        ):
            return None
        from apps.stores.domain.enums import StoreOfferStatus
        from apps.stores.infrastructure.models import StoreOfferNotification

        notification = (
            StoreOfferNotification.objects.filter(
                conversation=conversation,
                recipient_id=conversation.user_id,
                offer__status=StoreOfferStatus.PENDING,
                offer__expires_at__gt=timezone.now(),
            )
            .select_related("offer", "offer__order")
            .order_by("offer__expires_at", "created_at")
            .first()
        )
        if notification is None:
            return None
        snapshot = notification.presentation_snapshot
        items = "\n".join(f"- {item}" for item in snapshot.get("items", ()))
        actions = tuple(
            ResponseAction(
                action_id=item["id"],
                label="Accepter" if item.get("action", "").endswith("accept") else "Refuser",
            )
            for item in snapshot.get("actions", ())
        )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"Nouvelle commande {snapshot.get('order_reference', '')}\n{items}\nRépondez avant {snapshot.get('expires_at', '')}.",
            context,
            actions=actions,
            metadata={"store_offer_resumed": True, "offer_id": str(notification.offer_id)},
        )

    def _resume_whatsapp_driver_offer(self, conversation, context, message):
        if (
            message.channel != "WHATSAPP"
            or not message.text
            or not getattr(conversation, "pk", None)
            or not getattr(conversation, "user_id", None)
        ):
            return None
        from apps.drivers.domain.enums import DriverOfferStatus
        from apps.drivers.infrastructure.models import DriverOfferNotification

        notification = (
            DriverOfferNotification.objects.filter(
                conversation=conversation,
                recipient_id=conversation.user_id,
                offer__status=DriverOfferStatus.PENDING,
                offer__expires_at__gt=timezone.now(),
            )
            .order_by("offer__expires_at", "created_at")
            .first()
        )
        if notification is None:
            return None
        snapshot = notification.presentation_snapshot
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"Nouvelle livraison disponible\n\nCommande : {snapshot.get('order_reference', '')}\nRetrait : {snapshot.get('pickup_store_name', '')}\nRépondez avant {snapshot.get('expires_at', '')}.",
            context,
            actions=tuple(
                ResponseAction(
                    action_id=item["id"],
                    label="Accepter" if item.get("action", "").endswith("accept") else "Refuser",
                )
                for item in snapshot.get("actions", ())
            ),
            metadata={"driver_offer_resumed": True, "offer_id": str(notification.offer_id)},
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
        if context.active_journey not in {None, journey}:
            context, _ = suspend_active_task(context, reason="topic_change")
            updated = self.context_service.update_context(conversation, context)
            # `JourneyRoutingService.select()` immediately reads the passed
            # instance. Keep it aligned with the locked write so it cannot
            # overwrite the freshly suspended task with stale JSON.
            conversation.context = updated.context
        decision = self.journey_routing_service.select(conversation, journey)
        metadata = {**context.metadata}
        metadata.pop("task_paused", None)
        context = replace(context, active_journey=journey, metadata=metadata)
        if decision.status == JourneyRoutingStatus.SELLER_ONBOARDING_REQUIRED:
            return self._with_journey_metadata(
                self._from_seller_onboarding(
                    self.seller_onboarding_service.start(conversation), context
                ),
                journey,
                decision,
            )
        if decision.status == JourneyRoutingStatus.DRIVER_ONBOARDING_REQUIRED:
            return self._with_journey_metadata(
                self._from_driver_onboarding(
                    self.driver_onboarding_service.start(conversation), context
                ),
                journey,
                decision,
            )
        if (
            journey == ActiveJourney.DELIVER
            and decision.status == JourneyRoutingStatus.DRIVER_JOURNEY_READY
        ):
            workspace = self.driver_workspace_service.present(
                conversation=conversation
            )
            return self._with_journey_metadata(
                self._from_driver_workspace(workspace, context), journey, decision
            )
        if journey == ActiveJourney.BUY:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Parfait. Que souhaitez-vous commander ?",
                context,
                metadata={
                    "journey": journey.value,
                    "journey_status": decision.status.value,
                },
            )
        if (
            journey == ActiveJourney.SELL
            and decision.status == JourneyRoutingStatus.SELLER_JOURNEY_READY
        ):
            workspace = self.seller_workspace_service.present(
                conversation=conversation
            )
            return self._with_journey_metadata(
                self._from_seller_workspace(workspace, context), journey, decision
            )
        text = (
            "Votre parcours livreur est prêt."
            if decision.status == JourneyRoutingStatus.DRIVER_JOURNEY_READY
            else "Pour livrer, l'onboarding livreur est requis."
        )
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
        metadata = {
            **outcome.metadata,
            "journey": journey.value,
            "journey_status": decision.status.value,
        }
        response = replace(
            outcome.response,
            metadata={
                **outcome.response.metadata,
                "journey": journey.value,
                "journey_status": decision.status.value,
            },
        )
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

    def _from_seller_workspace(self, workspace, context):
        return ConversationalOutcome(
            status=(
                ConversationalOutcomeStatus.REJECTED
                if workspace.metadata.get("authorization_denied")
                else ConversationalOutcomeStatus.PROCESSED
            ),
            response=ResponsePlan(
                text=workspace.text,
                actions=tuple(
                    ResponseAction(action_id=action.action_id, label=action.label)
                    for action in workspace.actions
                ),
                metadata=dict(workspace.metadata),
            ),
            active_order_id=context.active_order_id,
            metadata=dict(workspace.metadata),
        )

    def _from_driver_onboarding(self, onboarding, context):
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text=onboarding.text,
                actions=tuple(
                    ResponseAction(action_id=action.action_id, label=action.label)
                    for action in onboarding.actions
                ),
                metadata=dict(onboarding.metadata),
            ),
            active_order_id=context.active_order_id,
            metadata=dict(onboarding.metadata),
        )

    def _from_driver_workspace(self, workspace, context):
        return ConversationalOutcome(
            status=(
                ConversationalOutcomeStatus.REJECTED
                if workspace.metadata.get("driver_profile_missing")
                else ConversationalOutcomeStatus.PROCESSED
            ),
            response=ResponsePlan(
                text=workspace.text,
                actions=tuple(
                    ResponseAction(action_id=action.action_id, label=action.label)
                    for action in workspace.actions
                ),
                request_location=workspace.request_location,
                metadata=dict(workspace.metadata),
            ),
            active_order_id=context.active_order_id,
            metadata=dict(workspace.metadata),
        )

    def _from_product_discovery(self, discovery, context):
        metadata = dict(discovery.metadata or {})
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text=discovery.text,
                actions=tuple(
                    ResponseAction(action_id=action.action_id, label=action.label)
                    for action in discovery.actions
                ),
                metadata=metadata,
            ),
            active_order_id=context.active_order_id,
            metadata=metadata,
        )

    def _driver_availability_action(self, conversation, context, action_id):
        if action_id not in {
            "driver.availability.available",
            "driver.availability.offline",
        }:
            return None
        workspace = (
            self.driver_workspace_service.set_available(conversation=conversation)
            if action_id.endswith("available")
            else self.driver_workspace_service.set_offline(conversation=conversation)
        )
        workspace = replace(
            workspace,
            metadata={**workspace.metadata, "driver_availability": True},
        )
        return self._from_driver_workspace(workspace, context)

    def _driver_offer_action(self, conversation, context, action_id, correlation_id):
        from apps.drivers.application.offers import decode_driver_offer_action

        decoded = decode_driver_offer_action(action_id)
        if decoded is not None:
            action_id, offer_id = decoded
        elif action_id in {"driver_offer.accept", "driver_offer.reject"}:
            # Backward compatibility for already-sent legacy buttons only.
            offer_id = (
                context.metadata.get("driver_offer_id")
                if hasattr(context, "metadata")
                else None
            )
        elif action_id.startswith("driver_offer."):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette proposition n'est plus disponible.",
                context,
            )
        else:
            return None
        if not offer_id:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette proposition n'est plus disponible.",
                context,
            )

        from apps.drivers.application.dispatch import DriverMatchingOrchestrator
        from apps.drivers.infrastructure.models import DriverOffer

        offer = DriverOffer.objects.filter(pk=offer_id).first()
        if offer is None or offer.driver.user_id != conversation.user_id:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette proposition n'est pas autorisée.",
                context,
            )
        try:
            if action_id == "driver_offer.accept":
                DriverMatchingOrchestrator().handle_accept(
                    offer, actor=conversation.user, correlation_id=correlation_id
                )
                from apps.drivers.application.delivery_execution import (
                    DeliveryExecutionService,
                    encode_delivery_action,
                )

                mission = DeliveryExecutionService().get_active_mission(
                    conversation.user
                )
                pickup = mission.pickup.display_name
                if mission.pickup.address_label:
                    pickup += f" — {mission.pickup.address_label}"
                destination = (
                    mission.destination.address_label
                    or "position de livraison enregistrée"
                )
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    f"Course {mission.order_reference} acceptée."
                    f"\nRetrait : {pickup}."
                    f"\nDestination : {destination}.",
                    context,
                    metadata={
                        "driver_offer": "ACCEPTED",
                        "assignment_id": mission.assignment_id,
                    },
                    actions=(
                        ResponseAction(
                            action_id=encode_delivery_action(
                                "delivery.confirm_pickup", mission.assignment_id
                            ),
                            label="Commande récupérée",
                        ),
                    ),
                )
            DriverMatchingOrchestrator().handle_reject(
                offer, actor=conversation.user, correlation_id=correlation_id
            )
            workspace = self.driver_workspace_service.present(
                conversation=conversation, prefix="Proposition refusée."
            )
            workspace = replace(
                workspace,
                metadata={**workspace.metadata, "driver_offer": "REJECTED"},
            )
            return self._from_driver_workspace(workspace, context)
        except (ValueError, PermissionError):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette proposition n'est plus disponible.",
                context,
            )

    def _store_offer_action(self, conversation, context, callback_id, correlation_id):
        from apps.stores.application.offer_actions import (
            StoreOfferActionResult,
            StoreOfferActionService,
            decode_store_offer_action,
        )

        decoded = decode_store_offer_action(callback_id)
        if decoded is None:
            if callback_id.startswith("store_offer."):
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Cette offre n'est plus disponible.",
                    context,
                )
            return None
        action_id, offer_id = decoded
        service = self.store_offer_action_service or StoreOfferActionService()
        result = service.handle(
            actor=conversation.user,
            offer_id=offer_id,
            action_id=action_id,
        )
        if result == StoreOfferActionResult.ACCEPTED:
            from apps.stores.application.offer_actions import present_offer
            from apps.stores.application.preparation import encode_store_order_action
            from apps.stores.infrastructure.models import StoreOffer

            offer = StoreOffer.objects.select_related("order").get(pk=offer_id)
            presentation = present_offer(offer)
            items = "\n".join(presentation.items)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"Commande {presentation.order_reference} acceptée.\n\n{items}\n\nVous pouvez maintenant commencer sa préparation.",
                context,
                metadata={"store_offer": result.value, "offer_id": str(offer_id)},
                actions=(ResponseAction(
                    action_id=encode_store_order_action(
                        "store_order.start_preparation", offer.order_id
                    ),
                    label="Démarrer la préparation",
                ),),
            )
        if result == StoreOfferActionResult.REJECTED:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Cette commande a été refusée.",
                context,
                metadata={"store_offer": result.value, "offer_id": str(offer_id)},
            )
        text = (
            "Vous n'êtes pas autorisé à traiter cette offre."
            if result == StoreOfferActionResult.NOT_AUTHORIZED
            else "Cette offre n'est plus disponible."
        )
        return self._outcome(
            ConversationalOutcomeStatus.REJECTED,
            text,
            context,
            metadata={"store_offer": result.value, "offer_id": str(offer_id)},
        )

    def _store_order_action(self, conversation, context, callback_id, correlation_id):
        from apps.orders.infrastructure.models import Order
        from apps.stores.application.preparation import (
            StorePreparationResult,
            decode_store_order_action,
            encode_store_order_action,
        )

        decoded = decode_store_order_action(callback_id)
        if decoded is None:
            if callback_id.startswith("store_order.") and ":" in callback_id:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Cette action n'est plus disponible.",
                    context,
                )
            return None
        action_id, order_id = decoded
        operation = (
            self.store_preparation_service.start_preparation
            if action_id == "store_order.start_preparation"
            else self.store_preparation_service.mark_ready_for_pickup
        )
        result = operation(
            actor=conversation.user,
            order_id=order_id,
            correlation_id=correlation_id,
        )
        order = Order.objects.filter(pk=order_id).only("reference").first()
        reference = order.reference if order else ""
        if result in {
            StorePreparationResult.STARTED,
            StorePreparationResult.ALREADY_PREPARING,
        }:
            text = (
                f"Préparation de la commande {reference} démarrée."
                if result == StorePreparationResult.STARTED
                else f"La commande {reference} est déjà en préparation."
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                text + "\n\nLorsque la commande est prête à être récupérée, indiquez-le ici.",
                context,
                actions=(ResponseAction(
                    action_id=encode_store_order_action(
                        "store_order.mark_ready", order_id
                    ),
                    label="Commande prête",
                ),),
                metadata={"store_preparation": result.value, "order_id": str(order_id)},
            )
        if result in {
            StorePreparationResult.READY,
            StorePreparationResult.ALREADY_READY,
        }:
            text = (
                f"Commande {reference} marquée comme prête pour la récupération."
                if result == StorePreparationResult.READY
                else f"La commande {reference} est déjà prête pour la récupération."
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                text,
                context,
                metadata={"store_preparation": result.value, "order_id": str(order_id)},
            )
        text = (
            "Vous n'êtes pas autorisé à traiter cette commande."
            if result == StorePreparationResult.NOT_AUTHORIZED
            else "Cette action n'est plus disponible pour cette commande."
        )
        return self._outcome(
            ConversationalOutcomeStatus.REJECTED,
            text,
            context,
            metadata={"store_preparation": result.value, "order_id": str(order_id)},
        )

    def _delivery_action(self, conversation, context, action_id, correlation_id):
        from apps.drivers.application.delivery_execution import (
            DELIVERY_ASSIGNMENT_ACTIONS,
            decode_delivery_action,
            encode_delivery_action,
        )

        decoded = decode_delivery_action(action_id)
        assignment_id = None
        if decoded is not None:
            action_id, assignment_id = decoded
        elif action_id.startswith("delivery.") and ":" in action_id:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette action de livraison n'est plus disponible.",
                context,
            )
        issue_actions = {
            "delivery.issue.customer_unreachable": "CUSTOMER_UNREACHABLE",
            "delivery.issue.address_problem": "ADDRESS_PROBLEM",
            "delivery.issue.vehicle_problem": "VEHICLE_PROBLEM",
            "delivery.issue.other": "OTHER",
        }
        if action_id == "delivery.report_issue":
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Quel problème rencontrez-vous ?",
                context,
                actions=tuple(
                    ResponseAction(action_id=key, label=label)
                    for key, label in (
                        ("delivery.issue.customer_unreachable", "Client injoignable"),
                        ("delivery.issue.address_problem", "Problème d'adresse"),
                        ("delivery.issue.vehicle_problem", "Problème de véhicule"),
                        ("delivery.issue.other", "Autre problème"),
                    )
                ),
            )
        if action_id in issue_actions:
            from apps.drivers.application.delivery_execution import (
                DeliveryExecutionService,
                DeliveryMissionAuthorizationError,
            )

            try:
                mission = DeliveryExecutionService().get_active_mission(
                    conversation.channel_identity
                )
                issue, _ = DeliveryExecutionService().report_issue(
                    conversation.channel_identity,
                    assignment_id=mission.assignment_id,
                    issue_type=issue_actions[action_id],
                    correlation_id=correlation_id,
                )
            except (DeliveryMissionAuthorizationError, ValueError):
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Ce signalement n'est plus disponible.",
                    context,
                )
            self.context_service.update_context(
                conversation,
                replace(
                    context,
                    metadata={**context.metadata, "delivery_issue_id": str(issue.id)},
                ),
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Incident enregistré.",
                context,
                actions=(
                    ResponseAction(
                        action_id="delivery.confirm_failure",
                        label="Confirmer l'impossibilité",
                    ),
                ),
            )
        if action_id == "delivery.confirm_failure":
            issue_id = context.metadata.get("delivery_issue_id")
            if not issue_id:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Aucun incident à confirmer.",
                    context,
                )
            from apps.drivers.application.delivery_execution import (
                DeliveryExecutionService,
                DeliveryMissionAuthorizationError,
            )

            try:
                mission = DeliveryExecutionService().get_active_mission(
                    conversation.channel_identity
                )
                _, duplicate = DeliveryExecutionService().confirm_failure(
                    conversation.channel_identity,
                    assignment_id=mission.assignment_id,
                    issue_id=issue_id,
                    correlation_id=correlation_id,
                )
            except (DeliveryMissionAuthorizationError, ValueError):
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Cette confirmation n'est plus disponible.",
                    context,
                )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Échec de livraison déjà confirmé."
                if duplicate
                else "Échec de livraison confirmé.",
                context,
            )
        if action_id not in DELIVERY_ASSIGNMENT_ACTIONS:
            return None
        from apps.drivers.application.delivery_execution import (
            DeliveryExecutionService,
            DeliveryMissionAuthorizationError,
            DeliveryMissionNotFound,
        )

        try:
            service = DeliveryExecutionService()
            result = (
                service.confirm_pickup(
                    conversation.channel_identity,
                    assignment_id=assignment_id,
                    correlation_id=correlation_id,
                )
                if action_id == "delivery.confirm_pickup"
                else service.start_delivery(
                    conversation.channel_identity,
                    assignment_id=assignment_id,
                    correlation_id=correlation_id,
                )
                if action_id == "delivery.start_delivery"
                else service.confirm_delivered(
                    conversation.channel_identity,
                    assignment_id=assignment_id,
                    correlation_id=correlation_id,
                )
            )
        except (DeliveryMissionAuthorizationError, DeliveryMissionNotFound, ValueError):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette confirmation de retrait n'est plus disponible.",
                context,
            )
        if action_id == "delivery.start_delivery":
            text = (
                "Livraison déjà en cours."
                if result.duplicate
                else "Livraison démarrée."
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                text,
                context,
                metadata={
                    "delivery_start": "DUPLICATE" if result.duplicate else "CONFIRMED"
                },
                actions=(
                    ResponseAction(
                        action_id=encode_delivery_action(
                            "delivery.confirm_delivered", result.mission.assignment_id
                        ),
                        label="Commande livrée",
                    ),
                    ResponseAction(
                        action_id="delivery.report_issue", label="Signaler un problème"
                    ),
                ),
            )
        if action_id == "delivery.confirm_delivered":
            text = (
                "Livraison déjà terminée."
                if result.duplicate
                else "Livraison terminée."
            )
            workspace = self.driver_workspace_service.present(
                conversation=conversation, prefix=text
            )
            workspace = replace(
                workspace,
                metadata={
                    **workspace.metadata,
                    "delivery_completion": "DUPLICATE"
                    if result.duplicate
                    else "CONFIRMED"
                },
            )
            return self._from_driver_workspace(workspace, context)
        destination = (
            result.mission.destination.address_label or "Destination enregistrée"
        )
        prefix = (
            "Retrait déjà confirmé."
            if result.duplicate
            else "Commande récupérée confirmée."
        )
        text = f"{prefix}\nDestination : {destination}"
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            text,
            context,
            metadata={
                "delivery_pickup": "DUPLICATE" if result.duplicate else "CONFIRMED"
            },
            actions=(
                ResponseAction(
                    action_id=encode_delivery_action(
                        "delivery.start_delivery", result.mission.assignment_id
                    ),
                    label="Démarrer la livraison",
                ),
            ),
        )

    def _natural_driver_availability(self, conversation, context, text):
        if context.active_journey != ActiveJourney.DELIVER or not text:
            return None
        normalized = " ".join(text.casefold().strip(" .!?").split())
        action = None
        if normalized in {
            "je suis disponible",
            "disponible",
            "je suis en ligne",
            "je veux commencer à livrer",
            "je veux commencer a livrer",
            "je suis prêt",
            "je suis pret",
        }:
            action = "driver.availability.available"
        elif normalized in {
            "je suis hors ligne",
            "hors ligne",
            "indisponible",
            "je ne suis plus disponible",
            "je veux arrêter les livraisons",
            "je veux arreter les livraisons",
            "je fais une pause",
        }:
            action = "driver.availability.offline"
        return (
            self._driver_availability_action(conversation, context, action)
            if action
            else None
        )

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
            if reason == "ambiguous":
                selecting_variant = (
                    resolution.reason.value == "VARIANT_SELECTION_REQUIRED"
                )
                pending = PendingClarification(
                    kind=ClarificationKind.AMBIGUOUS_PRODUCT,
                    field="product",
                    item_reference=item.requested_name,
                    candidate_ids=tuple(
                        str(
                            candidate.variant_id
                            if selecting_variant else candidate.product_id
                        )
                        for candidate in resolution.candidates
                    ),
                    metadata={
                        "operation": OrderAction.ADD_ITEM.value,
                        "order_id": str(order.id),
                        "item": {
                            "requested_name": item.requested_name,
                            "quantity": str(item.quantity),
                            "unit": item.unit.value,
                            "variant_hint": item.variant_hint,
                            "notes": item.notes,
                        },
                        "selection": "variant" if selecting_variant else "product",
                    },
                )
                self.context_service.set_pending_clarification(
                    conversation, pending, flow=ConversationFlow.AWAITING_CLARIFICATION
                )
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    (
                        f"Quel format de {item.requested_name} souhaitez-vous ?"
                        if selecting_variant
                        else "Plusieurs produits correspondent. Lequel souhaitez-vous ?"
                    ),
                    context,
                    actions=tuple(
                        ResponseAction(
                            action_id=(
                                f"clarification.select_variant.{candidate.variant_id}"
                                if selecting_variant
                                else f"clarification.select_product.{candidate.product_id}"
                            ),
                            label=(
                                candidate.metadata.get("variant_label", candidate.canonical_name)
                                if selecting_variant else candidate.canonical_name
                            ),
                        )
                        for candidate in resolution.candidates
                    ),
                    metadata={"pending_operation": "ADD_ITEM"},
                )
            pending = PendingClarification(
                kind=(
                    ClarificationKind.MISSING_QUANTITY
                    if item.quantity is None
                    else ClarificationKind.MISSING_UNIT
                ),
                field="quantity" if item.quantity is None else "unit",
                item_reference=item.requested_name,
                metadata={
                    "operation": OrderAction.ADD_ITEM.value,
                    "order_id": str(order.id),
                    "item": self._item_draft(item),
                },
            )
            self.context_service.set_pending_clarification(
                conversation,
                pending,
                flow=ConversationFlow.AWAITING_CLARIFICATION,
            )
            return self._ask_for_item_slots(
                context, pending, actions=("order_draft_updated",)
            )
        unmatched = self._unmatched_outcome(conversation, context, order)
        if unmatched is not None:
            return unmatched
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

    def _handle_modify(
        self, conversation, context, understanding, text, correlation_id
    ):
        """Only mutate when a backend target resolution is unique."""
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à modifier.",
                context,
            )
        if understanding.order_action == OrderAction.ADD_ITEM:
            return self._handle_order_items(
                conversation, context, understanding, correlation_id
            )
        if understanding.order_action in {
            OrderAction.REMOVE_ITEM,
            OrderAction.REPLACE_ITEM,
        }:
            return self._handle_typed_item_action(
                conversation, context, order, understanding, correlation_id
            )
        if understanding.order_action == OrderAction.MODIFY_QUANTITY:
            return self._handle_quantity_change(
                conversation, context, order, understanding, correlation_id
            )
        if understanding.order_action == OrderAction.CHANGE_LOCATION:
            return self._start_location_change(conversation, context)
        if understanding.order_action == OrderAction.MODIFY and not understanding.items:
            pending = PendingClarification(
                kind=ClarificationKind.OTHER,
                field="order_item",
                metadata={
                    "operation": OrderAction.MODIFY.value,
                    "order_id": str(order.id),
                },
            )
            self.context_service.set_pending_clarification(
                conversation, pending, flow=ConversationFlow.AWAITING_CLARIFICATION
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Que souhaitez-vous modifier dans votre commande ?",
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

    def _handle_quantity_change(
        self, conversation, context, order, understanding, correlation_id
    ):
        """Resolve the spoken line inside this order, then use the domain service."""
        target = self.order_building_service.target(
            order, ItemReference(requested_name=understanding.target_reference.text)
        )
        if target.status != ItemTargetStatus.FOUND:
            return self._ask_item_target(context, target)
        requested = understanding.items[0] if understanding.items else None
        if requested is None or (requested.quantity is None and requested.unit is None):
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"Quelle quantité ou unité souhaitez-vous pour {target.item.requested_name} ?",
                context,
            )
        self.order_building_service.update(
            target.item,
            quantity=requested.quantity,
            unit=requested.unit,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        self.context_service.clear_pending_clarification(
            conversation, flow=ConversationFlow.ORDER_BUILDING
        )
        return self._modified_outcome(conversation, order, "order_item_updated")

    def _start_location_change(self, conversation, context):
        if self._active_order(conversation, context) is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Aucune commande active à localiser.",
                context,
            )
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

    def _unmatched_outcome(
        self, conversation, context, order, action="order_item_unmatched"
    ):
        item = (
            self.order_building_service.target(order, ItemReference()).item
            if False
            else None
        )
        from apps.orders.infrastructure.models import OrderItem

        item = (
            OrderItem.objects.filter(
                order=order, resolution_status=OrderItemResolutionStatus.UNMATCHED
            )
            .order_by("created_at", "id")
            .first()
        )
        if item is None:
            return None
        pending = PendingClarification(
            kind=ClarificationKind.OTHER,
            field="unmatched_item",
            item_reference=item.requested_name,
            metadata={
                "operation": "UNMATCHED_ITEM",
                "order_id": str(order.id),
                "target_order_item_id": str(item.id),
            },
        )
        self.context_service.set_pending_clarification(
            conversation, pending, flow=ConversationFlow.AWAITING_CLARIFICATION
        )
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text=f"{item.requested_name} n’est pas disponible dans le catalogue Whatassoh. Voulez-vous le remplacer ou continuer sans cet article ?",
                actions=(
                    ResponseAction(
                        action_id="order.unmatched.remove", label="Continuer sans"
                    ),
                    ResponseAction(
                        action_id="order.unmatched.replace", label="Remplacer"
                    ),
                ),
            ),
            actions_performed=(action,),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _handle_unmatched_action(
        self, conversation, context, action_id, correlation_id
    ):
        if action_id not in {"order.unmatched.remove", "order.unmatched.replace"}:
            return None
        pending = context.pending_clarification
        if not pending or pending.metadata.get("operation") not in {
            "UNMATCHED_ITEM",
            "UNMATCHED_ITEM_REPLACE",
        }:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette action n’est plus disponible.",
                context,
            )
        order = self._active_order(conversation, context)
        target = (
            self.order_building_service.target(
                order,
                ItemReference(
                    order_item_id=pending.metadata.get("target_order_item_id")
                ),
            )
            if order
            else None
        )
        if (
            not target
            or target.status != ItemTargetStatus.FOUND
            or target.item.resolution_status != OrderItemResolutionStatus.UNMATCHED
        ):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette action n’est plus disponible.",
                context,
            )
        if action_id == "order.unmatched.remove":
            self.order_building_service.remove(
                target.item, actor=conversation.user, correlation_id=correlation_id
            )
            return self._modified_outcome(conversation, order, "unmatched_item_removed")
        if pending.metadata.get("operation") == "UNMATCHED_ITEM_REPLACE":
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"Par quel autre produit souhaitez-vous remplacer "
                f"{target.item.requested_name} ?",
                context,
            )
        self.context_service.set_pending_clarification(
            conversation,
            replace(
                pending,
                metadata={
                    **pending.metadata,
                    "operation": "UNMATCHED_ITEM_REPLACE",
                    "replacement": {
                        "requested_name": None,
                        "quantity": None,
                        "unit": None,
                        "notes": None,
                    },
                },
            ),
            flow=ConversationFlow.AWAITING_CLARIFICATION,
        )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"Par quel produit souhaitez-vous remplacer {target.item.requested_name} ?",
            context,
        )

    def _modified_outcome(self, conversation, order, action):
        order.refresh_from_db()
        unmatched = self._unmatched_outcome(
            conversation, self.context_service.get_context(conversation), order
        )
        if unmatched is not None:
            return unmatched
        summary = self.order_building_service.summary(order)
        if not summary:
            self.context_service.clear_pending_clarification(
                conversation, flow=ConversationFlow.ORDER_BUILDING
            )
            return ConversationalOutcome(
                status=ConversationalOutcomeStatus.PROCESSED,
                response=ResponsePlan(
                    text=(
                        "Votre commande est maintenant vide. "
                        "Que souhaitez-vous ajouter ?"
                    )
                ),
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
        logger.info(
            "location_received",
            extra={
                "correlation_id": correlation_id,
                "channel": message.channel,
                "journey": context.active_journey,
                "flow": context.flow,
            },
        )
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
        logger.info(
            "location_persisted",
            extra={
                "correlation_id": correlation_id,
                "order_id": str(order.id),
                "order_status": order.status,
            },
        )
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
                        action_id="order.skip_delivery_instructions",
                        label="Non, continuer",
                    ),
                ),
            ),
            actions_performed=("delivery_location_set",),
            active_order_id=str(order.id),
            requires_followup=True,
        )

    def _handle_driver_location(self, conversation, context, message):
        from django.utils import timezone

        from apps.drivers.application.location import (
            DriverLocationService,
            DriverLocationUpdate,
        )
        from apps.drivers.application.selectors import get_driver_by_user

        driver = get_driver_by_user(conversation.user)
        if driver is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Créez d'abord votre profil livreur.",
                context,
            )
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
        prefix = (
            "Votre position a été mise à jour."
            if result.status.value == "UPDATED"
            else "Cette position était déjà enregistrée ou plus ancienne."
        )
        workspace = self.driver_workspace_service.present(
            conversation=conversation, prefix=prefix
        )
        workspace = replace(
            workspace,
            metadata={**workspace.metadata, "driver_location": True},
        )
        return self._from_driver_workspace(workspace, context)

    def _handle_action(
        self, conversation, context, action_id, correlation_id, metadata
    ):
        unmatched = self._handle_unmatched_action(
            conversation, context, action_id, correlation_id
        )
        if unmatched is not None:
            return unmatched
        if action_id == "order.accept_partial_store_coverage":
            from apps.stores.application.partial_coverage import (
                PartialCoverageConsentService,
            )

            proposal_id = metadata.get("partial_coverage_proposal_id")
            try:
                from apps.stores.infrastructure.models import (
                    PartialStoreCoverageProposal,
                )

                proposal = PartialStoreCoverageProposal.objects.filter(
                    pk=proposal_id
                ).first()
                if proposal is None:
                    raise ValueError("stale")
                order, replay = PartialCoverageConsentService().accept(
                    order_id=proposal.order_id,
                    customer=conversation.user,
                    correlation_id=correlation_id,
                )
            except (ValueError, PermissionError):
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Cette proposition n'est plus disponible.",
                    context,
                )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Votre commande a été ajustée et nous recherchons à nouveau une boutique.",
                context,
            )
        if action_id == "order.status":
            from apps.orders.domain.enums import OrderStatus
            from apps.orders.infrastructure.models import Order

            order = (
                Order.objects.filter(customer=conversation.user)
                .exclude(status__in=[OrderStatus.DRAFT, OrderStatus.AWAITING_LOCATION])
                .order_by("-created_at")
                .first()
            )
            messages = {
                OrderStatus.CONFIRMED: "Votre demande est confirmée. Nous lançons la recherche d'une boutique.",
                OrderStatus.MATCHING_STORES: "Nous recherchons une boutique pour votre commande.",
                OrderStatus.AWAITING_PARTIAL_COVERAGE: "Nous attendons votre décision sur les produits disponibles.",
                OrderStatus.STORE_OFFERED: "Une boutique est en cours de consultation.",
                OrderStatus.STORE_ACCEPTED: "Une boutique a accepté votre commande. Elle va être préparée.",
                OrderStatus.PREPARING: "Votre commande est en préparation.",
                OrderStatus.READY_FOR_PICKUP: "Votre commande est prête. Nous recherchons un livreur.",
                OrderStatus.MATCHING_DRIVERS: "Nous recherchons un livreur.",
                OrderStatus.DRIVER_ASSIGNED: "Un livreur a été assigné à votre commande.",
                OrderStatus.PICKED_UP: "Votre commande a été récupérée par le livreur.",
                OrderStatus.IN_DELIVERY: "Votre commande est en route.",
                OrderStatus.DELIVERED: "Votre commande a été livrée.",
                OrderStatus.CANCELLED: "Votre commande est annulée.",
                OrderStatus.FAILED: "Votre commande ne peut pas être traitée pour le moment.",
            }
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                messages.get(order.status, "Aucune commande en cours.")
                if order
                else "Aucune commande en cours.",
                context,
            )
        if action_id == "order.modify":
            # A partial proposal has no active draft context after confirmation;
            # restore the existing editing flow, never a second editor.
            from apps.stores.infrastructure.models import PartialStoreCoverageProposal

            proposal = (
                PartialStoreCoverageProposal.objects.filter(
                    order__customer=conversation.user, status="PENDING"
                )
                .select_related("order")
                .first()
            )
            if proposal and proposal.order.status == "AWAITING_PARTIAL_COVERAGE":
                from apps.stores.application.partial_coverage import (
                    PartialCoverageConsentService,
                )

                try:
                    order = PartialCoverageConsentService().return_to_editing(
                        order_id=proposal.order_id,
                        customer=conversation.user,
                        correlation_id=correlation_id,
                    )
                    self.active_order_service.attach_order(
                        conversation, str(order.id), correlation_id=correlation_id
                    )
                except ValueError:
                    return self._outcome(
                        ConversationalOutcomeStatus.REJECTED,
                        "Cette proposition n'est plus disponible.",
                        context,
                    )
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Que souhaitez-vous modifier dans votre commande ?",
                    context,
                )
        if action_id == "order.cancel":
            from apps.stores.infrastructure.models import PartialStoreCoverageProposal

            proposal = (
                PartialStoreCoverageProposal.objects.filter(
                    order__customer=conversation.user, status="PENDING"
                )
                .select_related("order")
                .first()
            )
            if proposal and proposal.order.status == "AWAITING_PARTIAL_COVERAGE":
                from apps.stores.application.partial_coverage import (
                    PartialCoverageConsentService,
                )

                try:
                    PartialCoverageConsentService().cancel(
                        order_id=proposal.order_id,
                        customer=conversation.user,
                        correlation_id=correlation_id,
                    )
                except ValueError:
                    return self._outcome(
                        ConversationalOutcomeStatus.REJECTED,
                        "Cette proposition n'est plus disponible.",
                        context,
                    )
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Votre commande est annulée.",
                    context,
                )
        if action_id in {"store_order.start_preparation", "store_order.mark_ready"}:
            target = (
                "PREPARING"
                if action_id == "store_order.start_preparation"
                else "READY_FOR_PICKUP"
            )
            order_id = self.store_preparation_service.available_order_for(
                actor=conversation.user, target=target
            )
            if order_id is None:
                orders = self.store_preparation_service.available_orders_for(
                    actor=conversation.user, target=target
                )
                if len(orders) > 1:
                    from apps.stores.application.preparation import encode_store_order_action

                    return self._outcome(
                        ConversationalOutcomeStatus.PROCESSED,
                        "Quelle commande souhaitez-vous préparer ?",
                        context,
                        actions=tuple(
                            ResponseAction(
                                action_id=encode_store_order_action(action_id, order.id),
                                label=order.reference,
                            )
                            for order in orders
                        ),
                        metadata={"store_order_clarification": True},
                    )
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Aucune commande de boutique unique ne correspond à cette action.",
                    context,
                )
            operation = (
                self.store_preparation_service.start_preparation
                if target == "PREPARING"
                else self.store_preparation_service.mark_ready_for_pickup
            )
            result = operation(
                actor=conversation.user,
                order_id=order_id,
                correlation_id=correlation_id,
            )
            if result in {
                StorePreparationResult.STARTED,
                StorePreparationResult.ALREADY_PREPARING,
            }:
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Préparation démarrée.",
                    context,
                )
            if result in {
                StorePreparationResult.READY,
                StorePreparationResult.ALREADY_READY,
            }:
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Commande marquée comme prête.",
                    context,
                )
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette commande ne peut plus être préparée.",
                context,
            )
        if action_id == "order.confirm":
            if context.flow != ConversationFlow.AWAITING_CONFIRMATION:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    (
                        "Cette confirmation n'est plus disponible. "
                        "Vérifiez d'abord votre commande."
                    ),
                    context,
                )
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
                                action_id="order.abandon_and_start_new",
                                label="Abandonner et recommencer",
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
        # Do not erase unrelated suspended work (for example a seller catalog).
        # The confirmed order is no longer an editable active basket.
        latest_context = self.context_service.get_context(conversation)
        metadata = dict(latest_context.metadata)
        metadata.pop("awaiting_delivery_instructions", None)
        self.context_service.update_context(
            conversation,
            replace(
                latest_context,
                flow=ConversationFlow.IDLE,
                active_order_id=None,
                pending_clarification=None,
                metadata=metadata,
            ),
        )
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(
                text="Demande confirmée ✅\nNous recherchons maintenant une boutique capable de fournir votre commande. Je vous tiendrai informé ici.",
                actions=(
                    ResponseAction(
                        action_id="order.status", label="Suivre ma commande"
                    ),
                ),
            ),
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

    @staticmethod
    def _normalize_understanding(understanding):
        """Correct a dangerous delivery ambiguity before journey routing.

        The model may describe customer delivery with the legacy DELIVER intent. The
        explicit goal is the finer signal: RECEIVE_DELIVERY is informational/customer
        work and must never start driver onboarding.
        """

        if (
            understanding.intent == MessageIntent.DELIVER
            and understanding.journey_goal == JourneyGoal.RECEIVE_DELIVERY
        ):
            secondary = tuple(
                item
                for item in understanding.secondary_intents
                if item != MessageIntent.HELP
            )
            return replace(
                understanding,
                intent=MessageIntent.HELP,
                secondary_intents=secondary,
                interaction_mode=InteractionMode.ANSWER,
                action_requested=False,
            )
        return understanding

    @staticmethod
    def _task_target_matches_context(understanding, context):
        if understanding.task_target in {TaskTarget.NONE, TaskTarget.CURRENT_TASK}:
            return True
        expected = {
            TaskTarget.ORDER: ConversationTaskKind.ORDER,
            TaskTarget.STORE_ONBOARDING: ConversationTaskKind.SELLER_ONBOARDING,
            TaskTarget.DRIVER_ONBOARDING: ConversationTaskKind.DRIVER_ONBOARDING,
            TaskTarget.SELLER_CATALOG: ConversationTaskKind.SELLER_CATALOG,
        }.get(understanding.task_target)
        return expected is not None and expected == active_task_kind(context)

    def _task_target_clarification(self, context, understanding):
        target = {
            TaskTarget.ORDER: "la commande",
            TaskTarget.STORE_ONBOARDING: "la création de boutique",
            TaskTarget.DRIVER_ONBOARDING: "la création du profil livreur",
            TaskTarget.SELLER_CATALOG: "la gestion du catalogue",
        }.get(understanding.task_target, "la tâche mentionnée")
        current = active_task_kind(context)
        current_text = self._task_label(current) if current else "aucune tâche"
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"Vous avez demandé d'annuler {target}, mais la tâche active est "
            f"{current_text}. Je n'ai rien modifié. Précisez la tâche à annuler.",
            context,
            metadata={
                "capability": "cancel",
                "clarification_required": True,
                "target_mismatch": True,
            },
        )

    def _handle_compound_turn(
        self,
        conversation,
        context,
        understanding,
        message,
        correlation_id,
        conversation_context,
    ):
        """Execute one explicit navigation action and answer the same turn.

        This path is intentionally limited to deterministic conversation navigation.
        Orders, stores, catalog mutations and delivery state never execute here.
        """

        if (
            understanding.interaction_mode != InteractionMode.ANSWER_AND_ACT
            or not understanding.action_requested
        ):
            return None
        intents = (understanding.intent, *understanding.secondary_intents)
        if MessageIntent.HELP not in intents:
            return None
        navigation = next(
            (
                item
                for item in intents
                if item
                in {
                    MessageIntent.CANCEL,
                    MessageIntent.PAUSE,
                    MessageIntent.BACK,
                    MessageIntent.ORDER_RESUME,
                }
            ),
            None,
        )
        if navigation is None:
            return None
        if navigation == MessageIntent.CANCEL:
            action = (
                self._cancel_current_task(
                    conversation, context, correlation_id=correlation_id
                )
                if self._task_target_matches_context(understanding, context)
                else self._task_target_clarification(context, understanding)
            )
        elif navigation == MessageIntent.PAUSE:
            action = self._pause_current_task(conversation, context)
        elif navigation == MessageIntent.BACK:
            action = self._back_current_task(conversation, context)
        else:
            action = self._resume_workflow(
                conversation,
                context,
                correlation_id,
                target=understanding.task_target,
            )
        if getattr(conversation, "pk", None):
            conversation.refresh_from_db(fields=["context", "updated_at"])
            context = self.context_service.get_context(conversation)
            conversation_context = self.context_window_builder.build(
                conversation,
                context,
                current_message_id=message.external_message_id,
            )
        answer = self._handle_knowledge_or_unknown(
            conversation,
            context,
            message.text or "",
            MessageIntent.HELP,
            correlation_id,
            retrieval_query=understanding.standalone_question,
            conversation_context=conversation_context,
        )
        response = replace(
            answer.response,
            text=f"{action.response.text}\n\n{answer.response.text}",
            actions=tuple(
                dict.fromkeys((*action.response.actions, *answer.response.actions))
            ),
            metadata={
                **action.response.metadata,
                **answer.response.metadata,
                "compound_turn": True,
            },
        )
        return replace(
            answer,
            response=response,
            actions_performed=tuple(
                dict.fromkeys((*action.actions_performed, *answer.actions_performed))
            ),
            metadata={
                **action.metadata,
                **answer.metadata,
                "compound_turn": True,
                "navigation_intent": navigation.value,
            },
        )

    def _handle_knowledge_or_unknown(
        self,
        conversation,
        context,
        text,
        intent,
        correlation_id,
        *,
        retrieval_query=None,
        conversation_context=None,
    ):
        if intent == MessageIntent.HELP and self.knowledge_answer_service:
            try:
                from apps.knowledge.application.access import principal_for_user

                answer = self.knowledge_answer_service.answer(
                    text,
                    principal=principal_for_user(conversation.user),
                    language=conversation.user.preferred_language,
                    retrieval_query=retrieval_query,
                    conversation_context=conversation_context,
                    correlation_id=correlation_id,
                )
                if self.knowledge_answer_enabled and answer.grounded:
                    return self._outcome(
                        ConversationalOutcomeStatus.PROCESSED,
                        answer.answer,
                        context,
                        metadata={
                            "grounded": True,
                            "citation_count": len(answer.citations),
                        },
                    )
                if self.knowledge_answer_shadow:
                    logger.info(
                        "knowledge_answer_shadow_completed",
                        extra={
                            "grounded": answer.grounded,
                            "citation_count": len(answer.citations),
                            "no_answer_reason": answer.no_answer_reason,
                            "correlation_id": correlation_id,
                        },
                    )
            except Exception as exc:
                logger.exception(
                    "knowledge_answer_failed",
                    extra={
                        "error_category": exc.__class__.__name__,
                        "correlation_id": correlation_id,
                    },
                )
        if intent == MessageIntent.HELP:
            return self._help_outcome(context, question=text)
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
                        ResponseAction(
                            action_id="order.start_new", label="Nouvelle commande"
                        ),
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

    def _handle_delivery_instructions(
        self, conversation, context, text, correlation_id
    ):
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
                text="Commande à compléter :\n"
                + "\n".join(lines)
                + f"\nÀ corriger : {blockers}",
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
                ResponseAction(
                    action_id="order.change_location", label="Modifier l'adresse"
                ),
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

    def _capability_outcome(self, context, capability, *, conversation=None):
        status = capability.status if capability else CapabilityStatus.UNKNOWN
        if status == CapabilityStatus.SOCIAL:
            if conversation is not None:
                return self._social_greeting(conversation, context)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                SOCIAL_RESPONSE,
                context,
                metadata={"capability": "greeting", "capability_status": status.value},
            )
        if status == CapabilityStatus.OUT_OF_SCOPE:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                OUT_OF_SCOPE_RESPONSE,
                context,
                metadata={"capability_status": status.value},
            )
        if status == CapabilityStatus.NOT_IMPLEMENTED:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                NOT_IMPLEMENTED_RESPONSES.get(
                    capability.name,
                    "J'ai compris votre demande, mais cette fonctionnalité n'est pas encore disponible.",
                ),
                context,
                metadata={
                    "capability": capability.name,
                    "capability_status": status.value,
                },
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            UNKNOWN_RESPONSE,
            context,
            metadata={"capability_status": CapabilityStatus.UNKNOWN.value},
        )

    def _social_greeting(self, conversation, context):
        """Greet naturally once, then acknowledge without repeating identity."""

        now = timezone.localtime()
        social = context.social_state
        first_greeting = social.greeting_count == 0
        salutation = "Bonsoir" if now.hour >= 18 or now.hour < 5 else "Bonjour"
        user = getattr(conversation, "user", None)
        name = (getattr(user, "first_name", None) or "").strip()
        addressee = f" {name}" if name else ""
        if first_greeting:
            text = (
                f"{salutation}{addressee}. Je suis l'assistant WhatAssoh. "
                "Je peux vous aider à acheter, vendre ou livrer."
            )
        else:
            text = f"Rebonjour{addressee}. Je vous écoute."
        task = active_task_kind(context)
        if task is not None:
            text += f" Nous étions sur {self._task_label(task)}."
        elif context.suspended_tasks:
            text += (
                f" {self._task_label(context.suspended_tasks[-1].kind).capitalize()} "
                "est toujours en pause."
            )
        updated_social = ConversationSocialState(
            greeting_count=social.greeting_count + 1,
            last_greeting_at=now.isoformat(),
            introduced_at=social.introduced_at or now.isoformat(),
        )
        updated = replace(context, social_state=updated_social)
        if getattr(conversation, "pk", None):
            self.context_service.update_context(conversation, updated)
        actions = ()
        if task is not None or context.suspended_tasks:
            actions = (
                ResponseAction(action_id="conversation.resume", label="Continuer"),
                ResponseAction(action_id="conversation.pause", label="Plus tard"),
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            text,
            updated,
            metadata={
                "capability": "greeting",
                "capability_status": CapabilityStatus.SOCIAL.value,
                "repeated_greeting": not first_greeting,
            },
            actions=actions,
        )

    def _paused_task_outcome(self, context):
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "Cette tâche est en pause. Dites « reprendre » pour continuer, ou indiquez ce que vous souhaitez faire maintenant.",
            context,
            metadata={"capability": "paused_task", "task_paused": True},
            actions=(
                ResponseAction(action_id="conversation.resume", label="Reprendre"),
            ),
        )

    def _order_history_outcome(
        self, conversation, context, *, agent_decision=None
    ):
        result = (
            agent_decision.tool_results.get("list_orders")
            if agent_decision is not None
            else None
        )
        orders = (
            result.data.get("orders", ())[:5]
            if result is not None
            else list(
                get_customer_orders(conversation.user).order_by("-created_at")[:5]
            )
        )
        if not orders:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Vous n'avez encore aucune commande.",
                context,
                metadata={
                    "capability": "order_history",
                    "capability_status": CapabilityStatus.SUPPORTED.value,
                    "agent_read": result is not None,
                },
            )
        lines = ["Vos commandes récentes :"]
        if result is not None:
            lines.extend(
                f"{order['reference']} — "
                f"{order.get('status_label', order['status'])}"
                for order in orders
            )
        else:
            lines.extend(
                f"{order.reference} — {order.get_status_display()}" for order in orders
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "\n".join(lines),
            context,
            metadata={
                "capability": "order_history",
                "capability_status": CapabilityStatus.SUPPORTED.value,
                "agent_read": result is not None,
            },
        )

    def _run_active_agent(
        self, conversation, message, understanding, correlation_id
    ):
        if (
            self.active_agent_service is None
            or not getattr(conversation, "pk", None)
            or understanding.intent
            not in {
                MessageIntent.CATALOG_BROWSE,
                MessageIntent.ORDER_HISTORY,
                MessageIntent.ORDER_STATUS,
            }
        ):
            return None
        from apps.ai.application.active_agent import run_active_agent_safely

        return run_active_agent_safely(
            service=self.active_agent_service,
            conversation=conversation,
            message=message,
            understanding=understanding,
            correlation_id=correlation_id,
        )

    def _agent_order_status_outcome(self, context, agent_decision):
        if agent_decision is None:
            return None
        result = agent_decision.tool_results.get("get_active_order")
        if result is None:
            return None
        if result.status != "OK":
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Vous n'avez aucune commande active à suivre pour le moment.",
                context,
                metadata={
                    "capability": "order_status",
                    "authoritative": True,
                    "agent_read": True,
                },
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"La commande {result.data['reference']} est actuellement au statut "
            f"{result.data.get('status_label', result.data['status'])}.",
            context,
            metadata={
                "capability": "order_status",
                "authoritative": True,
                "agent_read": True,
                "order_status": result.data["status"],
            },
        )

    @staticmethod
    def _has_pending_task(context):
        return has_resumable_work(context)

    def _try_universal_interrupt(
        self,
        conversation,
        context,
        message,
        correlation_id,
        conversation_context,
    ):
        """Handle safe conversational detours without consuming the pending task.

        Structured callbacks and native locations remain deterministic and never enter
        this pre-read. A non-universal text is intentionally left for its current
        workflow handler, preserving the existing state-machine routing.
        """
        if (
            not message.text
            or message.metadata.get("action_id")
            or message.message_type == MessageType.LOCATION
        ):
            return None
        command = detect_universal_command(message.text)
        if command is not None:
            return self._handle_deterministic_interrupt(
                conversation,
                context,
                command,
                message.text,
                correlation_id,
            )
        # Seller and driver onboarding own their deterministic free-text slots.
        # Do not make an LLM call before those handlers get a chance to consume
        # their input; explicit universal commands above remain available.
        if active_task_kind(context) in {
            ConversationTaskKind.SELLER_ONBOARDING,
            ConversationTaskKind.DRIVER_ONBOARDING,
        } and not re.search(
            r"\b(annul\w*|laissons tomber|ne veux plus|continu\w*|repren\w*|"
            r"revenir|retour\w*|catalog\w*|produit\w*|est\W*ce|aide|help|"
            r"bonjour|bonsoir|salut)\b",
            message.text.casefold(),
        ):
            return None
        if not self._has_pending_task(context):
            return None
        understanding = self.understanding_service.understand_normalized(
            message,
            limited_context=conversation_context,
            correlation_id=correlation_id,
        )
        if understanding is None:
            return None
        understanding = self._normalize_understanding(understanding)
        if understanding.intent == MessageIntent.CREATE_OR_UPDATE_ORDER:
            understanding = self.product_discovery_service.enrich_order_understanding(
                understanding, message.text or "", context
            )
        compound = self._handle_compound_turn(
            conversation,
            context,
            understanding,
            message,
            correlation_id,
            conversation_context,
        )
        if compound is not None:
            return compound
        agent_decision = self._run_active_agent(
            conversation, message, understanding, correlation_id
        )
        replacement = self._complete_unmatched_replacement(
            conversation, context, understanding, correlation_id
        )
        if replacement is not None:
            return replacement
        if understanding.intent == MessageIntent.MEMORY_SHOW:
            return self._memory_show_outcome(conversation, context)
        if understanding.intent == MessageIntent.MEMORY_FORGET:
            return self._memory_forget_outcome(conversation, context)
        capability = classify_capability(understanding.intent)
        is_universal = (
            float(understanding.confidence) < self.confidence_threshold
            or capability.status
            in {
                CapabilityStatus.SOCIAL,
                CapabilityStatus.OUT_OF_SCOPE,
                CapabilityStatus.UNKNOWN,
                CapabilityStatus.NOT_IMPLEMENTED,
            }
            or understanding.intent
            in {
                MessageIntent.HELP,
                MessageIntent.ORDER_HISTORY,
                MessageIntent.CANCEL,
                MessageIntent.ORDER_RESUME,
                MessageIntent.PAUSE,
                MessageIntent.BACK,
                MessageIntent.SELL,
                MessageIntent.DELIVER,
                MessageIntent.CREATE_OR_UPDATE_ORDER,
                MessageIntent.MEMORY_SHOW,
                MessageIntent.MEMORY_FORGET,
                MessageIntent.CATALOG_BROWSE,
            }
        )
        if not is_universal:
            return None
        logger.info(
            "conversation_universal_interrupt",
            extra={
                "correlation_id": correlation_id,
                "channel": message.channel,
                "intent": understanding.intent,
                "confidence": str(understanding.confidence),
                "capability": capability.name,
                "capability_status": capability.status,
                "route_selected": "universal_interrupt",
            },
        )
        if float(understanding.confidence) < self.confidence_threshold:
            return self._capability_outcome(context, None, conversation=conversation)
        if understanding.intent == MessageIntent.ORDER_STATUS:
            status_outcome = self._agent_order_status_outcome(
                context, agent_decision
            )
            if status_outcome is not None:
                return status_outcome
        if capability.status != CapabilityStatus.SUPPORTED:
            return self._capability_outcome(
                context, capability, conversation=conversation
            )
        if understanding.intent == MessageIntent.ORDER_HISTORY:
            return self._order_history_outcome(
                conversation, context, agent_decision=agent_decision
            )
        if understanding.intent == MessageIntent.CATALOG_BROWSE:
            discovery = self.product_discovery_service.discover(
                conversation=conversation,
                context=context,
                understanding=understanding,
                text=message.text or "",
            )
            return self._from_product_discovery(discovery, context)
        if understanding.intent == MessageIntent.MEMORY_SHOW:
            return self._memory_show_outcome(conversation, context)
        if understanding.intent == MessageIntent.MEMORY_FORGET:
            return self._memory_forget_outcome(conversation, context)
        if understanding.intent == MessageIntent.CANCEL:
            if understanding.ambiguities and not (
                understanding.task_target == TaskTarget.CURRENT_TASK
                and active_task_kind(context) is not None
            ):
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Que souhaitez-vous annuler exactement ? "
                    "La tâche en cours reste inchangée.",
                    context,
                    metadata={
                        "capability": "cancel",
                        "clarification_required": True,
                        "ambiguities": understanding.ambiguities,
                    },
                )
            if not self._task_target_matches_context(understanding, context):
                return self._task_target_clarification(context, understanding)
            return self._cancel_current_task(
                conversation, context, correlation_id=correlation_id
            )
        if understanding.intent == MessageIntent.ORDER_RESUME:
            return self._resume_workflow(
                conversation,
                context,
                correlation_id,
                target=understanding.task_target,
            )
        if understanding.intent == MessageIntent.PAUSE:
            return self._pause_current_task(conversation, context)
        if understanding.intent == MessageIntent.BACK:
            return self._back_current_task(conversation, context)
        if understanding.intent in {
            MessageIntent.SELL,
            MessageIntent.DELIVER,
            MessageIntent.CREATE_OR_UPDATE_ORDER,
        }:
            return self._change_topic(
                conversation, context, understanding, correlation_id
            )
        if understanding.intent == MessageIntent.HELP:
            return self._handle_knowledge_or_unknown(
                conversation,
                context,
                message.text,
                understanding.intent,
                correlation_id,
                retrieval_query=understanding.standalone_question,
                conversation_context=conversation_context,
            )
        return None

    def _handle_deterministic_interrupt(
        self, conversation, context, command, text, correlation_id
    ):
        """Execute high-confidence navigation without requiring a model call."""

        logger.info(
            "conversation_deterministic_interrupt",
            extra={
                "channel": getattr(conversation, "channel", None),
                "command": command.value,
                "active_task": active_task_kind(context),
                "correlation_id": correlation_id,
                "route_selected": "universal_interrupt",
            },
        )
        if command == UniversalCommand.CANCEL:
            return self._cancel_current_task(
                conversation, context, correlation_id=correlation_id
            )
        if command == UniversalCommand.PAUSE:
            return self._pause_current_task(conversation, context)
        if command == UniversalCommand.RESUME:
            return self._resume_workflow(conversation, context, correlation_id)
        if command == UniversalCommand.BACK:
            return self._back_current_task(conversation, context)
        if command == UniversalCommand.GREETING:
            return self._social_greeting(conversation, context)
        if command == UniversalCommand.MEMORY_SHOW:
            return self._memory_show_outcome(conversation, context)
        if command == UniversalCommand.MEMORY_FORGET:
            return self._memory_forget_outcome(conversation, context)
        return self._help_outcome(context, question=text)

    def _memory_show_outcome(self, conversation, context):
        from apps.conversations.application.memory import ConversationMemoryService

        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            ConversationMemoryService.describe(conversation.user),
            context,
            metadata={
                "capability": "memory_show",
                "memory_scope": "social_preferences",
            },
        )

    def _memory_forget_outcome(self, conversation, context):
        from apps.conversations.application.memory import ConversationMemoryService

        count = ConversationMemoryService().forget(conversation.user)
        text = (
            "C’est fait. J’ai supprimé vos préférences de conversation mémorisées."
            if count
            else "Je n’avais aucune préférence de conversation durable à supprimer."
        )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            text,
            context,
            metadata={"capability": "memory_forget", "forgotten_fact_count": count},
        )

    @staticmethod
    def _limited_interrupt_context(context):
        """Expose navigation facts only; business records stay behind tools."""

        task = active_task_kind(context)
        return {
            "active_task": task.value if task else None,
            "active_journey": (
                context.active_journey.value if context.active_journey else None
            ),
            "flow": context.flow.value,
            "has_suspended_task": bool(context.suspended_tasks),
        }

    def _pause_current_task(self, conversation, context):
        paused, task = suspend_active_task(context, reason="user_pause")
        if task is None:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Il n'y a aucune tâche en cours à mettre en pause.",
                context,
                metadata={"capability": "pause", "task_paused": False},
            )
        self.context_service.update_context(conversation, paused)
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"D'accord, je mets {self._task_label(task.kind)} en pause. "
            "Dites « reprendre » quand vous souhaitez continuer.",
            paused,
            metadata={
                "capability": "pause",
                "task_paused": True,
                "task_kind": task.kind.value,
            },
            actions=(
                ResponseAction(action_id="conversation.resume", label="Reprendre"),
            ),
        )

    def _cancel_current_task(self, conversation, context, *, correlation_id):
        kind = active_task_kind(context)
        if kind is None and context.suspended_tasks:
            decision = resume_last_task(context)
            context = decision.context
            kind = decision.task.kind
            # Order cancellation reloads and locks the persisted conversation.
            # Persist the selected suspended task so it cancels that exact order.
            if kind == ConversationTaskKind.ORDER:
                self.context_service.update_context(conversation, context)
        if kind == ConversationTaskKind.SELLER_ONBOARDING:
            draft = context.seller_onboarding
            if draft and draft.stage != SellerOnboardingStage.COMPLETED:
                return self._from_seller_onboarding(
                    self.seller_onboarding_service.cancel(conversation, context),
                    context,
                )
        if kind == ConversationTaskKind.DRIVER_ONBOARDING:
            draft = context.driver_onboarding
            if draft and draft.stage != DriverOnboardingStage.COMPLETED:
                return self._from_driver_onboarding(
                    self.driver_onboarding_service.cancel(conversation, context),
                    context,
                )
        if kind == ConversationTaskKind.ORDER:
            return self._cancel_active_order(conversation, context, correlation_id)

        updated = context
        if kind == ConversationTaskKind.SELLER_CATALOG:
            updated = replace(updated, pending_seller_catalog_mutation=None)
        if kind == ConversationTaskKind.DELIVERY_INSTRUCTIONS:
            metadata = {**updated.metadata, "awaiting_delivery_instructions": False}
            metadata.pop("task_paused", None)
            updated = replace(updated, metadata=metadata)
        if kind is not None:
            updated = discard_suspended_kind(updated, kind)
            updated = replace(updated, flow=ConversationFlow.IDLE, active_journey=None)
            self.context_service.update_context(conversation, updated)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"{self._task_label(kind).capitalize()} annulée.",
                updated,
                metadata={"capability": "cancel", "task_kind": kind.value},
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "Il n'y a aucune tâche en cours à annuler.",
            context,
            metadata={"capability": "cancel", "task_cancelled": False},
        )

    def _back_current_task(self, conversation, context):
        kind = active_task_kind(context)
        if kind is None and context.suspended_tasks:
            resumed = resume_last_task(context)
            context = resumed.context
            kind = resumed.task.kind
            self.context_service.update_context(conversation, context)
        if kind == ConversationTaskKind.SELLER_ONBOARDING:
            return self._from_seller_onboarding(
                self.seller_onboarding_service.back(conversation, context), context
            )
        if kind == ConversationTaskKind.DRIVER_ONBOARDING:
            return self._from_driver_onboarding(
                self.driver_onboarding_service.back(conversation, context), context
            )
        if kind == ConversationTaskKind.SELLER_CATALOG:
            updated = replace(context, pending_seller_catalog_mutation=None)
            self.context_service.update_context(conversation, updated)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "La proposition en cours est retirée. Décrivez une autre modification.",
                updated,
                metadata={"capability": "back", "task_kind": kind.value},
            )
        if kind == ConversationTaskKind.ORDER:
            updated = replace(
                context,
                flow=ConversationFlow.ORDER_BUILDING,
                pending_clarification=None,
            )
            self.context_service.update_context(conversation, updated)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Vous êtes revenu à la modification de la commande.",
                updated,
                metadata={"capability": "back", "task_kind": kind.value},
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            "Aucune étape précédente n'est disponible.",
            context,
            metadata={"capability": "back", "at_first_step": True},
        )

    def _change_topic(self, conversation, context, understanding, correlation_id):
        target = {
            MessageIntent.SELL: ActiveJourney.SELL,
            MessageIntent.DELIVER: ActiveJourney.DELIVER,
            MessageIntent.CREATE_OR_UPDATE_ORDER: ActiveJourney.BUY,
        }[understanding.intent]
        if context.active_journey != target:
            suspended, _ = suspend_active_task(context, reason="topic_change")
            self.context_service.update_context(conversation, suspended)
            context = suspended
        if target == ActiveJourney.BUY and understanding.items:
            self.journey_routing_service.select(conversation, target)
            context = replace(
                context,
                active_journey=target,
                metadata={
                    key: value
                    for key, value in context.metadata.items()
                    if key != "task_paused"
                },
            )
            return self._handle_order_items(
                conversation, context, understanding, correlation_id
            )
        return self._select_journey(conversation, context, target)

    def _resume_workflow(
        self, conversation, context, correlation_id, *, target=TaskTarget.NONE
    ):
        target_kind = {
            TaskTarget.ORDER: ConversationTaskKind.ORDER,
            TaskTarget.STORE_ONBOARDING: ConversationTaskKind.SELLER_ONBOARDING,
            TaskTarget.DRIVER_ONBOARDING: ConversationTaskKind.DRIVER_ONBOARDING,
            TaskTarget.SELLER_CATALOG: ConversationTaskKind.SELLER_CATALOG,
        }.get(target)
        resumed = resume_last_task(context, target=target_kind)
        if resumed.task is not None:
            context = resumed.context
            self.context_service.update_context(conversation, context)
            kind = resumed.task.kind
            if kind == ConversationTaskKind.SELLER_ONBOARDING:
                return self._from_seller_onboarding(
                    self.seller_onboarding_service.start(conversation), context
                )
            if kind == ConversationTaskKind.DRIVER_ONBOARDING:
                return self._from_driver_onboarding(
                    self.driver_onboarding_service.start(conversation), context
                )
            if kind == ConversationTaskKind.SELLER_CATALOG:
                return self._outcome(
                    ConversationalOutcomeStatus.PROCESSED,
                    "Reprenons la gestion de votre catalogue. Que souhaitez-vous modifier ?",
                    context,
                    metadata={"capability": "resume", "task_kind": kind.value},
                )
        current_kind = active_task_kind(context)
        if current_kind == ConversationTaskKind.SELLER_ONBOARDING:
            return self._from_seller_onboarding(
                self.seller_onboarding_service.start(conversation), context
            )
        if current_kind == ConversationTaskKind.DRIVER_ONBOARDING:
            return self._from_driver_onboarding(
                self.driver_onboarding_service.start(conversation), context
            )
        if current_kind == ConversationTaskKind.SELLER_CATALOG:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "La gestion de votre catalogue est déjà active. Que souhaitez-vous modifier ?",
                context,
                metadata={
                    "capability": "resume",
                    "task_kind": current_kind.value,
                },
            )
        if context.metadata.get("awaiting_delivery_instructions"):
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Souhaitez-vous ajouter une indication pour le livreur ?",
                context,
                metadata={
                    "capability": "order_resume",
                    "pending_task": "delivery_instructions",
                },
            )
        if not getattr(conversation, "pk", None):
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Aucune commande en cours à reprendre.",
                context,
            )
        recovery = self.recovery_service.recover(
            conversation, context, correlation_id=correlation_id
        )
        if recovery.status == RecoveryStatus.NO_ACTIVE_FLOW:
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Aucune commande en cours à reprendre.",
                context,
            )
        return self._recovery_outcome(conversation, context, recovery)

    @staticmethod
    def _item_draft(item):
        return {
            "requested_name": item.requested_name,
            "quantity": str(item.quantity) if item.quantity is not None else None,
            "unit": item.unit.value if item.unit else None,
            "variant_hint": item.variant_hint,
            "notes": item.notes,
        }

    @staticmethod
    def _draft_item(values):
        return ExtractedItem(
            requested_name=values.get("requested_name") or "",
            quantity=(Decimal(values["quantity"]) if values.get("quantity") else None),
            unit=ExtractedUnit(values["unit"]) if values.get("unit") else None,
            variant_hint=values.get("variant_hint"),
            notes=values.get("notes"),
        )

    @staticmethod
    def _merge_item_draft(values, candidate):
        merged = dict(values or {})
        if candidate is None:
            return merged
        # The active PendingClarification owns the product identity.  A short
        # answer such as "2 paquets" must only fill slots, not overwrite it.
        if candidate.quantity is None and candidate.requested_name:
            merged["requested_name"] = candidate.requested_name
        if candidate.quantity is not None:
            merged["quantity"] = str(candidate.quantity)
        if candidate.unit is not None:
            merged["unit"] = candidate.unit.value
        if candidate.variant_hint is not None:
            merged["variant_hint"] = candidate.variant_hint
        if candidate.notes is not None:
            merged["notes"] = candidate.notes
        return merged

    def _ask_for_item_slots(self, context, pending, *, actions=()):
        values = pending.metadata.get("replacement") or pending.metadata.get("item", {})
        if not values.get("requested_name"):
            text = "Quel produit souhaitez-vous ajouter ?"
        elif not values.get("quantity"):
            text = f"Quelle quantité de {values['requested_name']} souhaitez-vous ?"
        else:
            text = f"Quelle unité souhaitez-vous pour {values['requested_name']} ?"
        return ConversationalOutcome(
            status=ConversationalOutcomeStatus.PROCESSED,
            response=ResponsePlan(text=text),
            actions_performed=actions,
            active_order_id=context.active_order_id,
            requires_followup=True,
        )

    def _complete_pending_item_slots(self, conversation, context, understanding, correlation_id):
        pending = context.pending_clarification
        if not pending or pending.metadata.get("operation") != OrderAction.ADD_ITEM.value:
            return None
        values = self._merge_item_draft(
            pending.metadata.get("item", {}),
            understanding.items[0] if understanding.items else None,
        )
        updated = replace(
            pending,
            item_reference=values.get("requested_name") or pending.item_reference,
            kind=(ClarificationKind.MISSING_QUANTITY if not values.get("quantity") else ClarificationKind.MISSING_UNIT),
            field="quantity" if not values.get("quantity") else "unit",
            metadata={**pending.metadata, "item": values},
        )
        if not values.get("requested_name") or not values.get("quantity") or not values.get("unit"):
            self.context_service.set_pending_clarification(conversation, updated)
            return self._ask_for_item_slots(context, updated)
        order = self._active_order(conversation, context)
        if order is None:
            return self._outcome(ConversationalOutcomeStatus.REJECTED, "Cette commande n'est plus disponible.", context)
        item = self._draft_item(values)
        resolution = self.grounding_service.ground_item(item)
        self.order_building_service.add(order, item, resolution, actor=conversation.user, correlation_id=correlation_id)
        self.context_service.clear_pending_clarification(conversation, flow=ConversationFlow.ORDER_BUILDING)
        unmatched = self._unmatched_outcome(conversation, context, order)
        if unmatched is not None:
            return unmatched
        return self._modified_outcome(conversation, order, "order_item_added")

    def _complete_variant_text_selection(self, conversation, context, understanding, correlation_id):
        """Treat a textual format answer as the same selection as a variant button."""
        pending = context.pending_clarification
        if not pending or pending.metadata.get("selection") != "variant":
            return None
        candidate = understanding.items[0] if understanding.items else None
        hint = candidate.variant_hint if candidate else None
        if not hint and candidate:
            hint = candidate.requested_name
        if not hint:
            return None
        facts = pending.metadata.get("item") or pending.metadata.get("replacement") or {}
        resolution = self.grounding_service.ground_item(
            ExtractedItem(
                requested_name=facts.get("requested_name", pending.item_reference or ""),
                quantity=Decimal(facts["quantity"]) if facts.get("quantity") else None,
                unit=ExtractedUnit(facts["unit"]) if facts.get("unit") else None,
                variant_hint=hint,
            )
        )
        if (
            resolution.status == OrderItemResolutionStatus.MATCHED
            and str(resolution.variant_id) in pending.candidate_ids
        ):
            return self._clarification_choice_action(
                conversation,
                context,
                f"clarification.select_variant.{resolution.variant_id}",
                correlation_id,
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"Je ne trouve pas ce format pour {pending.item_reference}. Choisissez un format proposé.",
            context,
        )

    @staticmethod
    def _task_label(kind):
        return {
            ConversationTaskKind.ORDER: "la commande",
            ConversationTaskKind.SELLER_ONBOARDING: "la création de votre boutique",
            ConversationTaskKind.SELLER_CATALOG: "la modification du catalogue",
            ConversationTaskKind.DRIVER_ONBOARDING: "l'inscription livreur",
            ConversationTaskKind.DELIVERY_INSTRUCTIONS: "les instructions de livraison",
            ConversationTaskKind.JOURNEY: "le parcours en cours",
        }[kind]

    def _help_outcome(self, context, *, question=""):
        """Useful deterministic fallback when grounded generation is unavailable."""

        task = active_task_kind(context)
        suffix = (
            f" {self._task_label(task).capitalize()} reste en cours."
            if task is not None
            else ""
        )
        normalized = re.sub(r"[^a-z0-9\s]", " ", question.casefold())
        if "whatasso" in normalized and any(
            phrase in normalized for phrase in ("c est toi", "es tu", "qui es tu")
        ):
            text = (
                "WhatAssoh est toute la plateforme de commerce local. "
                "Je suis son assistant conversationnel : je vous aide à l'utiliser, "
                "mais WhatAssoh comprend aussi les boutiques, le catalogue, les "
                "commandes et la livraison."
            )
        elif "whatasso" in normalized and any(
            word in normalized.split() for word in ("sert", "but", "raison")
        ):
            text = (
                "WhatAssoh relie clients, boutiques et livreurs pour rechercher des "
                "produits, commander, vendre et organiser une livraison depuis une "
                "conversation."
            )
        elif "livr" in normalized:
            text = (
                "Pour recevoir une livraison, préparez votre commande comme client; "
                "cela ne crée pas de profil livreur. Le parcours livreur sert aux "
                "personnes qui veulent effectuer des livraisons."
            )
        elif "annul" in normalized and "comment" in normalized:
            text = (
                "Vous pouvez demander l'annulation en nommant la tâche concernée. "
                "Demander comment annuler donne seulement une explication; aucune "
                "action n'est exécutée sans demande explicite."
            )
        else:
            text = (
                "Je peux vous aider à acheter des produits, créer ou gérer une "
                "boutique, et utiliser le parcours livreur. Vous pouvez aussi dire "
                "« retour », « plus tard », « reprendre » ou « annuler »."
            )
        return self._outcome(
            ConversationalOutcomeStatus.PROCESSED,
            f"{text}{suffix}",
            context,
            metadata={
                "capability": "help",
                "task_preserved": task is not None,
                "grounded": False,
                "support_fallback": True,
            },
        )

    def _complete_unmatched_replacement(
        self, conversation, context, understanding, correlation_id
    ):
        pending = context.pending_clarification
        if not pending or pending.metadata.get("operation") != "UNMATCHED_ITEM_REPLACE":
            return None
        order = self._active_order(conversation, context)
        target = (
            self.order_building_service.target(
                order,
                ItemReference(
                    order_item_id=pending.metadata.get("target_order_item_id")
                ),
            )
            if order
            else None
        )
        if (
            not target
            or target.status != ItemTargetStatus.FOUND
            or target.item.resolution_status != OrderItemResolutionStatus.UNMATCHED
        ):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette modification n’est plus disponible.",
                context,
            )
        replacement = understanding.replacement or (
            understanding.items[0] if understanding.items else None
        )
        values = self._merge_item_draft(
            pending.metadata.get("replacement", {}), replacement
        )
        updated = replace(
            pending,
            item_reference=values.get("requested_name") or pending.item_reference,
            kind=(
                ClarificationKind.MISSING_QUANTITY
                if not values.get("quantity")
                else ClarificationKind.MISSING_UNIT
            ),
            field="quantity" if not values.get("quantity") else "unit",
            metadata={**pending.metadata, "replacement": values},
        )
        if not values.get("requested_name") or not values.get("quantity") or not values.get("unit"):
            self.context_service.set_pending_clarification(conversation, updated)
            return self._ask_for_item_slots(context, updated)
        replacement = self._draft_item(values)
        resolution = self.grounding_service.ground_item(replacement)
        if resolution.status == OrderItemResolutionStatus.UNMATCHED:
            # The original unmatched line stays targeted, but a rejected
            # replacement must not leak its quantity/unit into the next choice.
            retry_pending = replace(
                updated,
                item_reference=target.item.requested_name,
                metadata={
                    **updated.metadata,
                    "replacement": {
                        "requested_name": None,
                        "quantity": None,
                        "unit": None,
                        "notes": None,
                    },
                },
            )
            self.context_service.set_pending_clarification(conversation, retry_pending)
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"{replacement.requested_name} n’est pas disponible non plus. "
                f"Quel autre produit souhaitez-vous utiliser pour remplacer "
                f"{target.item.requested_name} ?",
                context,
                actions=(
                    ResponseAction(
                        action_id="order.unmatched.remove", label="Continuer sans"
                    ),
                ),
            )
        if resolution.status == OrderItemResolutionStatus.AMBIGUOUS:
            selecting_variant = resolution.reason.value == "VARIANT_SELECTION_REQUIRED"
            pending = PendingClarification(
                kind=ClarificationKind.AMBIGUOUS_PRODUCT,
                field="product",
                item_reference=replacement.requested_name,
                candidate_ids=tuple(str(candidate.variant_id if selecting_variant else candidate.product_id) for candidate in resolution.candidates),
                metadata={
                    "operation": "UNMATCHED_ITEM_REPLACE",
                    "order_id": str(order.id),
                    "target_order_item_id": str(target.item.id),
                    "replacement": {
                        "requested_name": replacement.requested_name,
                        "quantity": str(replacement.quantity),
                        "unit": replacement.unit.value,
                        "variant_hint": replacement.variant_hint,
                        "notes": replacement.notes,
                    },
                    "selection": "variant" if selecting_variant else "product",
                },
            )
            self.context_service.set_pending_clarification(
                conversation, pending, flow=ConversationFlow.AWAITING_CLARIFICATION
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                f"Quel format de {replacement.requested_name} souhaitez-vous ?" if selecting_variant else "Plusieurs produits correspondent. Lequel souhaitez-vous ?",
                context,
                actions=tuple(
                    ResponseAction(
                        action_id=(f"clarification.select_variant.{candidate.variant_id}" if selecting_variant else f"clarification.select_product.{candidate.product_id}"),
                        label=candidate.metadata.get("variant_label", candidate.canonical_name) if selecting_variant else candidate.canonical_name,
                    )
                    for candidate in resolution.candidates
                ),
            )
        if resolution.status != OrderItemResolutionStatus.MATCHED:
            self.context_service.set_pending_clarification(conversation, updated)
            return self._ask_for_item_slots(context, updated)
        self.order_building_service.replace(
            target.item,
            replacement,
            resolution,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        # Replacement identity and customer-requested slots are separate in the
        # existing order service: persist the collected quantity/unit explicitly.
        self.order_building_service.update(
            target.item,
            quantity=replacement.quantity,
            unit=replacement.unit,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        return self._modified_outcome(conversation, order, "unmatched_item_replaced")

    def _handle_typed_item_action(
        self, conversation, context, order, understanding, correlation_id
    ):
        """Typed actions are resolved and grounded before any domain mutation."""
        target = self.order_building_service.target(
            order, ItemReference(requested_name=understanding.target_reference.text)
        )
        if target.status != ItemTargetStatus.FOUND:
            return self._ask_item_target(context, target)
        if understanding.order_action == OrderAction.REMOVE_ITEM:
            self.order_building_service.remove(
                target.item, actor=conversation.user, correlation_id=correlation_id
            )
            self.context_service.clear_pending_clarification(
                conversation, flow=ConversationFlow.ORDER_BUILDING
            )
            return self._modified_outcome(conversation, order, "order_item_removed")
        replacement = understanding.replacement
        resolution = self.grounding_service.ground_item(replacement)
        # Strict-catalog policy: an unsuccessful grounding leaves the old line intact.
        if resolution.status == OrderItemResolutionStatus.UNMATCHED:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                f"{replacement.requested_name} n'est pas disponible dans le catalogue Whatassoh. {target.item.requested_name} a été conservé dans votre commande.",
                context,
                metadata={
                    "target_resolution_status": target.status,
                    "grounding_status": resolution.status,
                },
            )
        if resolution.status == OrderItemResolutionStatus.AMBIGUOUS:
            pending = PendingClarification(
                kind=ClarificationKind.AMBIGUOUS_PRODUCT,
                field="product",
                item_reference=replacement.requested_name,
                candidate_ids=tuple(
                    str(candidate.product_id) for candidate in resolution.candidates
                ),
                metadata={
                    "operation": OrderAction.REPLACE_ITEM.value,
                    "order_id": str(order.id),
                    "target_order_item_id": str(target.item.id),
                    "candidates": [
                        {
                            "id": str(candidate.product_id),
                            "label": candidate.canonical_name,
                        }
                        for candidate in resolution.candidates
                    ],
                    "replacement": {
                        "requested_name": replacement.requested_name,
                        "quantity": str(replacement.quantity)
                        if replacement.quantity is not None
                        else None,
                        "unit": replacement.unit.value if replacement.unit else None,
                        "notes": replacement.notes,
                    },
                },
            )
            self.context_service.set_pending_clarification(
                conversation, pending, flow=ConversationFlow.AWAITING_CLARIFICATION
            )
            logger.info(
                "natural_replace_ambiguity_persisted",
                extra={
                    "correlation_id": correlation_id,
                    "operation": "REPLACE_ITEM",
                    "target_resolution_status": target.status,
                    "grounding_status": resolution.status,
                    "pending_operation": "REPLACE_ITEM",
                },
            )
            return self._outcome(
                ConversationalOutcomeStatus.PROCESSED,
                "Plusieurs produits correspondent. Lequel souhaitez-vous ?",
                context,
                actions=tuple(
                    ResponseAction(
                        action_id=f"clarification.select_product.{candidate.product_id}",
                        label=candidate.canonical_name,
                    )
                    for candidate in resolution.candidates
                ),
                metadata={
                    "target_resolution_status": target.status,
                    "grounding_status": resolution.status,
                    "pending_operation": "REPLACE_ITEM",
                },
            )
        self.order_building_service.replace(
            target.item,
            replacement,
            resolution,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        self.context_service.clear_pending_clarification(
            conversation, flow=ConversationFlow.ORDER_BUILDING
        )
        return self._modified_outcome(conversation, order, "order_item_replaced")

    def _clarification_choice_action(
        self, conversation, context, action_id, correlation_id
    ):
        product_prefix = "clarification.select_product."
        variant_prefix = "clarification.select_variant."
        if action_id.startswith(product_prefix):
            prefix, selecting_variant = product_prefix, False
        elif action_id.startswith(variant_prefix):
            prefix, selecting_variant = variant_prefix, True
        else:
            return None
        pending = context.pending_clarification
        candidate_id = action_id.removeprefix(prefix)
        if (
            not pending
            or pending.kind != ClarificationKind.AMBIGUOUS_PRODUCT
            or candidate_id not in pending.candidate_ids
            or (pending.metadata.get("selection") == "variant") != selecting_variant
        ):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Ce choix n'est plus disponible.",
                context,
            )
        operation = pending.metadata.get("operation")
        if operation not in {
            OrderAction.REPLACE_ITEM.value,
            OrderAction.ADD_ITEM.value,
            "UNMATCHED_ITEM_REPLACE",
        }:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette clarification n'est pas encore disponible.",
                context,
            )
        order = self._active_order(conversation, context)
        if operation == OrderAction.ADD_ITEM.value and order is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette commande n'est plus disponible.",
                context,
            )
        target_id = pending.metadata.get("target_order_item_id")
        target = (
            self.order_building_service.target(
                order, ItemReference(order_item_id=target_id)
            )
            if order
            else None
        )
        try:
            UUID(candidate_id)
        except (TypeError, ValueError):
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Ce choix n'est plus disponible.",
                context,
            )
        product = None
        variant = None
        if selecting_variant:
            variant = ProductVariant.objects.filter(
                pk=candidate_id,
                status=ProductVariantStatus.ACTIVE,
                product__status=ProductStatus.ACTIVE,
            ).select_related("product").first()
            product = variant.product if variant else None
        else:
            product = Product.objects.filter(
                pk=candidate_id, status=ProductStatus.ACTIVE
            ).first()
        if operation == OrderAction.ADD_ITEM.value:
            if product is None:
                return self._outcome(
                    ConversationalOutcomeStatus.REJECTED,
                    "Ce choix n'est plus disponible.",
                    context,
                )
            facts = pending.metadata.get("item", {})
            item = ExtractedItem(
                requested_name=facts["requested_name"],
                quantity=Decimal(facts["quantity"]),
                unit=facts["unit"],
                variant_hint=facts.get("variant_hint"),
                notes=facts.get("notes"),
            )
            from apps.catalog.domain.grounding import (
                CatalogResolution,
                CatalogResolutionReason,
            )

            resolution = CatalogResolution(
                status=OrderItemResolutionStatus.MATCHED,
                requested_name=item.requested_name,
                product_id=product.id,
                variant_id=variant.id if variant else None,
                candidates=(),
                reason=CatalogResolutionReason.EXACT_MATCH,
            )
            self.order_building_service.add(
                order,
                item,
                resolution,
                actor=conversation.user,
                correlation_id=correlation_id,
            )
            self.context_service.clear_pending_clarification(
                conversation, flow=ConversationFlow.ORDER_BUILDING
            )
            return self._modified_outcome(conversation, order, "order_item_added")
        facts = pending.metadata.get("replacement", {})
        if not target or target.status != ItemTargetStatus.FOUND or product is None:
            return self._outcome(
                ConversationalOutcomeStatus.REJECTED,
                "Cette modification n'est plus disponible.",
                context,
            )
        replacement = ExtractedItem(
            requested_name=facts["requested_name"],
            quantity=Decimal(facts["quantity"])
            if facts.get("quantity")
            else target.item.quantity,
            unit=facts.get("unit") or target.item.unit,
            variant_hint=facts.get("variant_hint"),
            notes=facts.get("notes"),
        )
        from apps.catalog.domain.grounding import (
            CatalogResolution,
            CatalogResolutionReason,
        )

        resolution = CatalogResolution(
            status=OrderItemResolutionStatus.MATCHED,
            requested_name=replacement.requested_name,
            product_id=product.id,
            variant_id=variant.id if variant else None,
            candidates=(),
            reason=CatalogResolutionReason.EXACT_MATCH,
        )
        self.order_building_service.replace(
            target.item,
            replacement,
            resolution,
            actor=conversation.user,
            correlation_id=correlation_id,
        )
        if replacement.quantity is not None or replacement.unit is not None:
            self.order_building_service.update(
                target.item,
                quantity=replacement.quantity,
                unit=replacement.unit,
                actor=conversation.user,
                correlation_id=correlation_id,
            )
        self.context_service.clear_pending_clarification(
            conversation, flow=ConversationFlow.ORDER_BUILDING
        )
        return self._modified_outcome(conversation, order, "order_item_replaced")
