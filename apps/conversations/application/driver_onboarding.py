"""Persistent, channel-neutral driver onboarding; provider payloads never enter here."""

import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum

from django.db import transaction

from apps.conversations.domain.context import (
    ActiveJourney, ConversationContext, ConversationFlow, DriverOnboardingDraft,
    DriverOnboardingStage,
)
from apps.drivers.domain.enums import DriverStatus, VehicleType
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.application.services import assign_role
from apps.identity.domain.enums import UserRoleType

logger = logging.getLogger(__name__)


class DriverOnboardingStatus(StrEnum):
    STARTED = "STARTED"
    AWAITING_VEHICLE_TYPE = "AWAITING_VEHICLE_TYPE"
    REVIEW_READY = "REVIEW_READY"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    EXISTING_PROFILE = "EXISTING_PROFILE"
    INVALID = "INVALID"


@dataclass(frozen=True, kw_only=True)
class DriverOnboardingAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class DriverOnboardingOutcome:
    status: DriverOnboardingStatus
    text: str
    actions: tuple[DriverOnboardingAction, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


class DriverOnboardingService:
    """Owns the draft; a DriverProfile is created only at explicit confirmation."""

    confirm_action = "driver_onboarding.confirm"
    modify_action = "driver_onboarding.modify"
    cancel_action = "driver_onboarding.cancel"

    def __init__(self, *, context_service=None):
        from apps.conversations.application.context import ConversationContextService
        self.context_service = context_service or ConversationContextService()

    def start(self, conversation):
        if DriverProfile.objects.filter(user=conversation.user).exists():
            return self._ready(existing=True)
        context = self.context_service.get_context(conversation)
        draft = context.driver_onboarding
        if draft and draft.stage != DriverOnboardingStage.COMPLETED:
            return self._prompt_for(draft)
        draft = DriverOnboardingDraft(stage=DriverOnboardingStage.VEHICLE_TYPE)
        self._save_draft(conversation, context, draft)
        self._audit("driver_onboarding_started", conversation)
        return self._prompt_for(draft)

    def handle(self, conversation, context: ConversationContext, message):
        draft = context.driver_onboarding
        if context.active_journey != ActiveJourney.DELIVER or not draft:
            return None
        # Completion is durable; subsequent driver controls belong to the normal
        # journey, not to the onboarding state machine.
        if draft.stage == DriverOnboardingStage.COMPLETED:
            return None
        action_id = message.metadata.get("action_id")
        if action_id in {"journey.select.buy", "journey.select.sell", "journey.select.deliver"}:
            return None
        if action_id:
            return self.handle_action(conversation, context, action_id)
        if draft.stage == DriverOnboardingStage.VEHICLE_TYPE and message.text is not None:
            return self.set_vehicle_type(conversation, context, message.text)
        return self._prompt_for(draft)

    def set_vehicle_type(self, conversation, context, raw_vehicle_type: str):
        vehicle_type = self._parse_vehicle_type(raw_vehicle_type)
        if vehicle_type is None:
            return DriverOnboardingOutcome(
                status=DriverOnboardingStatus.INVALID,
                text="Indiquez un type de véhicule valide : moto, vélo, voiture, camionnette ou autre.",
                metadata={"driver_onboarding": True},
            )
        draft = replace(context.driver_onboarding, stage=DriverOnboardingStage.REVIEW, vehicle_type=vehicle_type)
        self._save_draft(conversation, context, draft)
        return self.review(draft)

    def handle_action(self, conversation, context, action_id):
        if action_id == self.confirm_action:
            return self.confirm(conversation)
        if action_id == self.modify_action:
            draft = DriverOnboardingDraft(stage=DriverOnboardingStage.VEHICLE_TYPE)
            self._save_draft(conversation, context, draft)
            return self._prompt_for(draft)
        if action_id == self.cancel_action:
            self.context_service.update_context(conversation, replace(context, flow=ConversationFlow.IDLE, driver_onboarding=None))
            return DriverOnboardingOutcome(status=DriverOnboardingStatus.CANCELLED, text="Création de profil livreur annulée.", metadata={"driver_onboarding": True})
        return self._prompt_for(context.driver_onboarding)

    @transaction.atomic
    def confirm(self, conversation):
        from apps.conversations.infrastructure.models import Conversation
        locked = Conversation.objects.select_for_update().select_related("user").get(pk=conversation.pk)
        user = locked.user.__class__.objects.select_for_update().get(pk=locked.user_id)
        context = ConversationContext.from_payload(locked.context)
        draft = context.driver_onboarding
        if context.active_journey != ActiveJourney.DELIVER or not draft:
            return DriverOnboardingOutcome(status=DriverOnboardingStatus.INVALID, text="Cet onboarding n'est pas disponible.")
        profile = DriverProfile.objects.select_for_update().filter(user=user).first()
        if profile is None:
            if draft.stage != DriverOnboardingStage.REVIEW or draft.vehicle_type not in VehicleType.values:
                return self._prompt_for(draft)
            # No KYC/admin approval exists in the MVP.  ACTIVE permits a later
            # availability choice; the default OFFLINE still prevents matching.
            profile = DriverProfile.objects.create(
                user=user, vehicle_type=draft.vehicle_type, status=DriverStatus.ACTIVE
            )
            self._audit("driver_profile_created", locked, driver_profile_id=str(profile.id))
        assign_role(user, UserRoleType.DRIVER)
        completed = DriverOnboardingDraft(stage=DriverOnboardingStage.COMPLETED, vehicle_type=profile.vehicle_type, driver_profile_id=str(profile.id))
        locked.context = replace(context, flow=ConversationFlow.IDLE, driver_onboarding=completed).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        self._audit("driver_onboarding_completed", locked, driver_profile_id=str(profile.id))
        return self._ready(existing=False)

    def review(self, draft):
        return DriverOnboardingOutcome(
            status=DriverOnboardingStatus.REVIEW_READY,
            text=f"Profil livreur\nVéhicule : {VehicleType(draft.vehicle_type).label}",
            actions=(DriverOnboardingAction(action_id=self.confirm_action, label="Confirmer"), DriverOnboardingAction(action_id=self.modify_action, label="Modifier"), DriverOnboardingAction(action_id=self.cancel_action, label="Annuler")),
            metadata={"driver_onboarding": True},
        )

    def _ready(self, *, existing):
        return DriverOnboardingOutcome(status=DriverOnboardingStatus.EXISTING_PROFILE if existing else DriverOnboardingStatus.COMPLETED, text="Votre profil livreur est prêt. Vous pourrez vous rendre disponible et partager votre position.", metadata={"driver_profile_ready": True, "journey_status": "DRIVER_PROFILE_READY"})

    def _prompt_for(self, draft):
        if draft.stage == DriverOnboardingStage.REVIEW:
            return self.review(draft)
        if draft.stage == DriverOnboardingStage.COMPLETED:
            return self._ready(existing=False)
        return DriverOnboardingOutcome(status=DriverOnboardingStatus.AWAITING_VEHICLE_TYPE, text="Quel type de véhicule utilisez-vous ? (moto, vélo, voiture, camionnette ou autre)", metadata={"driver_onboarding": True, "journey_status": "DRIVER_ONBOARDING_REQUIRED"})

    def _save_draft(self, conversation, context, draft):
        self.context_service.update_context(conversation, replace(context, flow=ConversationFlow.DRIVER_ONBOARDING, driver_onboarding=draft))

    @staticmethod
    def _parse_vehicle_type(raw):
        normalized = " ".join(raw.strip().upper().split())
        aliases = {"MOTO": VehicleType.MOTORBIKE, "MOTORBIKE": VehicleType.MOTORBIKE, "MOTORCYCLE": VehicleType.MOTORBIKE, "VÉLO": VehicleType.BICYCLE, "VELO": VehicleType.BICYCLE, "BICYCLE": VehicleType.BICYCLE, "VOITURE": VehicleType.CAR, "CAR": VehicleType.CAR, "CAMIONNETTE": VehicleType.VAN, "VAN": VehicleType.VAN, "AUTRE": VehicleType.OTHER, "OTHER": VehicleType.OTHER}
        if normalized in VehicleType.values:
            return normalized
        for word, value in aliases.items():
            if word in normalized:
                return value.value
        return None

    @staticmethod
    def _audit(event, conversation, **extra):
        logger.info(event, extra={"conversation_id": str(conversation.id), **extra})
