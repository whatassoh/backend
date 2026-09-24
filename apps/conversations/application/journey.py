"""Channel-neutral routing for the temporary conversational journey."""

from dataclasses import dataclass, replace
from enum import StrEnum

from apps.conversations.domain.context import ActiveJourney, ConversationFlow


class JourneyRoutingStatus(StrEnum):
    SELECTED = "SELECTED"
    SELLER_ONBOARDING_REQUIRED = "SELLER_ONBOARDING_REQUIRED"
    SELLER_JOURNEY_READY = "SELLER_JOURNEY_READY"
    DRIVER_ONBOARDING_REQUIRED = "DRIVER_ONBOARDING_REQUIRED"
    DRIVER_JOURNEY_READY = "DRIVER_JOURNEY_READY"


@dataclass(frozen=True, kw_only=True)
class JourneyRoutingDecision:
    journey: ActiveJourney
    status: JourneyRoutingStatus
    changed: bool


class JourneyRoutingService:
    actions = {
        "journey.select.buy": ActiveJourney.BUY,
        "journey.select.sell": ActiveJourney.SELL,
        "journey.select.deliver": ActiveJourney.DELIVER,
    }

    def __init__(self, *, context_service=None):
        from apps.conversations.application.context import ConversationContextService
        self.context_service = context_service or ConversationContextService()

    @classmethod
    def journey_for_action(cls, action_id: str) -> ActiveJourney | None:
        return cls.actions.get(action_id)

    def select(self, conversation, journey: ActiveJourney) -> JourneyRoutingDecision:
        context = self.context_service.get_context(conversation)
        changed = context.active_journey != journey
        # Do not clear an active order or clarification while switching: those are
        # business records/state machines, not a role-selection side effect.
        if changed or context.flow == ConversationFlow.AWAITING_JOURNEY_SELECTION:
            metadata = {**context.metadata}
            metadata.pop("task_paused", None)
            updated = self.context_service.update_context(
                conversation,
                replace(
                    context,
                    active_journey=journey,
                    metadata=metadata,
                    flow=(
                        ConversationFlow.IDLE
                        if context.active_order_id is None
                        else context.flow
                    ),
                ),
            )
            # Keep this request's instance coherent for the immediately-following
            # onboarding start; durable state remains the locked DB row.
            conversation.context = updated.context
        return JourneyRoutingDecision(
            journey=journey,
            changed=changed,
            status=self._status_for(conversation.user, journey),
        )

    @staticmethod
    def _status_for(user, journey: ActiveJourney) -> JourneyRoutingStatus:
        if journey == ActiveJourney.BUY:
            return JourneyRoutingStatus.SELECTED
        if journey == ActiveJourney.SELL:
            from apps.stores.domain.enums import StoreMembershipStatus
            from apps.stores.infrastructure.models import StoreMembership
            exists = StoreMembership.objects.filter(
                user=user, status=StoreMembershipStatus.ACTIVE
            ).exists()
            return (
                JourneyRoutingStatus.SELLER_JOURNEY_READY
                if exists
                else JourneyRoutingStatus.SELLER_ONBOARDING_REQUIRED
            )
        from apps.drivers.infrastructure.models import DriverProfile
        exists = DriverProfile.objects.filter(user=user).exists()
        return (
            JourneyRoutingStatus.DRIVER_JOURNEY_READY
            if exists
            else JourneyRoutingStatus.DRIVER_ONBOARDING_REQUIRED
        )
