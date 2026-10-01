"""Persistent, channel-neutral seller onboarding; no provider payloads enter here."""

import logging
import math
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from django.contrib.gis.geos import Point
from django.db import transaction

from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
    SellerOnboardingDraft,
    SellerOnboardingStage,
)
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User, UserRole
from apps.stores.application.services import create_store
from apps.stores.domain.enums import StoreMembershipStatus
from apps.stores.infrastructure.models import StoreMembership

logger = logging.getLogger(__name__)


class SellerOnboardingStatus(StrEnum):
    STARTED = "STARTED"
    EXISTING_STORE = "EXISTING_STORE"
    AWAITING_NAME = "AWAITING_NAME"
    AWAITING_LOCATION = "AWAITING_LOCATION"
    AWAITING_CITY = "AWAITING_CITY"
    REVIEW_READY = "REVIEW_READY"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    INVALID = "INVALID"


@dataclass(frozen=True, kw_only=True)
class SellerOnboardingAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class SellerOnboardingOutcome:
    status: SellerOnboardingStatus
    text: str
    actions: tuple[SellerOnboardingAction, ...] = ()
    request_location: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


class SellerOnboardingService:
    """Owns draft transitions; Store rows exist only after explicit confirmation."""

    confirm_action = "seller_onboarding.confirm"
    edit_action = "seller_onboarding.edit"
    cancel_action = "seller_onboarding.cancel"

    def __init__(self, *, context_service=None):
        from apps.conversations.application.context import ConversationContextService

        self.context_service = context_service or ConversationContextService()

    def start(self, conversation) -> SellerOnboardingOutcome:
        membership = self._existing_membership(conversation.user)
        if membership:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.EXISTING_STORE,
                text="Votre espace vendeur est prêt.",
                metadata={
                    "seller_onboarding": True,
                    "open_seller_workspace": True,
                    "store_id": str(membership.store_id),
                },
            )
        context = self.context_service.get_context(conversation)
        draft = context.seller_onboarding
        if draft and draft.stage != SellerOnboardingStage.COMPLETED:
            return self._prompt_for(draft)
        self.context_service.update_context(
            conversation,
            replace(
                context,
                flow=ConversationFlow.SELLER_ONBOARDING,
                seller_onboarding=SellerOnboardingDraft(
                    stage=SellerOnboardingStage.NAME
                ),
            ),
        )
        self._audit("seller_onboarding_started", conversation)
        return self._prompt_for(SellerOnboardingDraft(stage=SellerOnboardingStage.NAME))

    def handle(
        self, conversation, context: ConversationContext, message
    ) -> SellerOnboardingOutcome | None:
        draft = context.seller_onboarding
        if context.active_journey != ActiveJourney.SELL or not draft:
            return None
        # COMPLETED is legacy history, never a foreground task. The workspace
        # service clears it when it next resolves the authorized store.
        if draft.stage == SellerOnboardingStage.COMPLETED:
            return None
        action_id = message.metadata.get("action_id")
        # Journey selection is global navigation, never an onboarding command.
        if action_id in {
            "journey.select.buy",
            "journey.select.sell",
            "journey.select.deliver",
        }:
            return None
        if action_id:
            return self.handle_action(conversation, context, action_id)
        if draft.stage == SellerOnboardingStage.NAME and message.text is not None:
            return self.set_store_name(conversation, context, message.text)
        if (
            draft.stage == SellerOnboardingStage.LOCATION
            and message.location is not None
        ):
            return self.set_location(conversation, context, message.location)
        if draft.stage == SellerOnboardingStage.CITY and message.text is not None:
            return self.set_city(conversation, context, message.text)
        return self._prompt_for(draft)

    def set_store_name(
        self, conversation, context, raw_name: str
    ) -> SellerOnboardingOutcome:
        name = self._normalize_name(raw_name)
        if not name:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.INVALID,
                text="Indiquez un nom de boutique valide.",
                metadata={"seller_onboarding": True},
            )
        draft = replace(
            context.seller_onboarding,
            stage=SellerOnboardingStage.LOCATION,
            store_name=name,
        )
        self._save_draft(conversation, context, draft)
        return self._prompt_for(draft)

    def set_location(self, conversation, context, location) -> SellerOnboardingOutcome:
        latitude, longitude = location.latitude, location.longitude
        if not self._valid_coordinates(latitude, longitude):
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.INVALID,
                text="La position reçue est invalide. Partagez votre position native.",
                request_location=True,
                metadata={"seller_onboarding": True},
            )
        draft = replace(
            context.seller_onboarding,
            stage=SellerOnboardingStage.CITY,
            latitude=float(latitude),
            longitude=float(longitude),
        )
        self._save_draft(conversation, context, draft)
        return self._prompt_for(draft)

    def set_city(self, conversation, context, raw_city: str) -> SellerOnboardingOutcome:
        city = " ".join(raw_city.split())
        if not city or len(city) > 120:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.INVALID,
                text="Indiquez une ville valide.",
                metadata={"seller_onboarding": True},
            )
        draft = replace(
            context.seller_onboarding, stage=SellerOnboardingStage.REVIEW, city=city
        )
        self._save_draft(conversation, context, draft)
        return self.review(draft)

    def cancel(self, conversation, context) -> SellerOnboardingOutcome:
        """Cancel an uncommitted onboarding draft from any stage."""

        self.context_service.update_context(
            conversation,
            replace(
                context,
                flow=ConversationFlow.IDLE,
                active_journey=None,
                seller_onboarding=None,
            ),
        )
        self._audit("seller_onboarding_cancelled", conversation)
        return SellerOnboardingOutcome(
            status=SellerOnboardingStatus.CANCELLED,
            text="Création de boutique annulée. Aucune boutique n'a été créée.",
            actions=(
                SellerOnboardingAction(action_id="journey.select.buy", label="Acheter"),
                SellerOnboardingAction(action_id="journey.select.sell", label="Vendre"),
                SellerOnboardingAction(action_id="journey.select.deliver", label="Livrer"),
            ),
            metadata={"seller_onboarding": True, "task_cancelled": True},
        )

    def back(self, conversation, context) -> SellerOnboardingOutcome:
        """Move to the preceding editable stage without discarding the draft."""

        draft = context.seller_onboarding
        if draft is None:
            return self._stale_action()
        previous = {
            SellerOnboardingStage.LOCATION: SellerOnboardingStage.NAME,
            SellerOnboardingStage.CITY: SellerOnboardingStage.LOCATION,
            SellerOnboardingStage.REVIEW: SellerOnboardingStage.CITY,
        }.get(draft.stage)
        if previous is None:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.INVALID,
                text="Vous êtes déjà à la première étape de création de boutique.",
                metadata={"seller_onboarding": True, "at_first_step": True},
            )
        draft = replace(draft, stage=previous)
        self._save_draft(conversation, context, draft)
        return self._prompt_for(draft)

    @transaction.atomic
    def handle_action(
        self, conversation, context, action_id: str
    ) -> SellerOnboardingOutcome:
        from apps.conversations.infrastructure.models import Conversation

        locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
        context = ConversationContext.from_payload(locked.context)
        draft = context.seller_onboarding
        review_actions = {self.confirm_action, self.edit_action, self.cancel_action}
        if action_id in review_actions and (
            draft is None or draft.stage != SellerOnboardingStage.REVIEW
        ):
            return self._stale_action()
        if action_id == self.confirm_action:
            return self.confirm(locked)
        if action_id == self.edit_action:
            draft = SellerOnboardingDraft(stage=SellerOnboardingStage.NAME)
            self._save_draft(locked, context, draft)
            return self._prompt_for(draft)
        if action_id == self.cancel_action:
            return self.cancel(locked, context)
        return self._prompt_for(context.seller_onboarding)

    @staticmethod
    def _stale_action() -> SellerOnboardingOutcome:
        return SellerOnboardingOutcome(
            status=SellerOnboardingStatus.INVALID,
            text="Cette action n'est plus disponible.",
            metadata={"seller_onboarding": True, "stale_action": True},
        )

    @transaction.atomic
    def confirm(self, conversation) -> SellerOnboardingOutcome:
        from apps.conversations.infrastructure.models import Conversation

        locked = (
            Conversation.objects.select_for_update()
            .select_related("user")
            .get(pk=conversation.pk)
        )
        user = User.objects.select_for_update().get(pk=locked.user_id)
        context = ConversationContext.from_payload(locked.context)
        draft = context.seller_onboarding
        if context.active_journey != ActiveJourney.SELL or not draft:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.INVALID,
                text="Cet onboarding n'est pas disponible.",
            )
        if draft.stage == SellerOnboardingStage.COMPLETED:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.COMPLETED,
                text="Votre espace vendeur est prêt.",
                metadata={
                    "seller_onboarding": True,
                    "open_seller_workspace": True,
                    "store_id": draft.store_id,
                },
            )
        if draft.stage != SellerOnboardingStage.REVIEW or not self._is_complete(draft):
            return self._prompt_for(draft)
        membership = self._existing_membership(user, lock=True)
        if membership:
            store_id = str(membership.store_id)
        else:
            store = create_store(
                name=draft.store_name,
                location=Point(draft.longitude, draft.latitude, srid=4326),
                city=draft.city,
                created_by=user,
            )
            UserRole.objects.get_or_create(user=user, role=UserRoleType.STORE_OWNER)
            store_id = str(store.id)
            self._audit("store_created", locked, store_id=store_id)
        metadata = {
            **context.metadata,
            "seller_workspace_active": True,
            "seller_catalog_active": False,
            "seller_store_id": store_id,
        }
        locked.context = replace(
            context,
            flow=ConversationFlow.IDLE,
            seller_onboarding=None,
            pending_seller_catalog_mutation=None,
            metadata=metadata,
        ).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        self._audit("seller_onboarding_completed", locked, store_id=store_id)
        return SellerOnboardingOutcome(
            status=SellerOnboardingStatus.COMPLETED,
            text="Votre espace vendeur est prêt.",
            metadata={
                "seller_onboarding": True,
                "open_seller_workspace": True,
                "store_id": store_id,
            },
        )

    def review(self, draft: SellerOnboardingDraft) -> SellerOnboardingOutcome:
        return SellerOnboardingOutcome(
            status=SellerOnboardingStatus.REVIEW_READY,
            text=f"Boutique : {draft.store_name}\nLocalisation : reçue\nVille : {draft.city}",
            actions=(
                SellerOnboardingAction(
                    action_id=self.confirm_action, label="Créer ma boutique"
                ),
                SellerOnboardingAction(action_id=self.edit_action, label="Modifier"),
                SellerOnboardingAction(action_id=self.cancel_action, label="Annuler"),
            ),
            metadata={"seller_onboarding": True},
        )

    def _prompt_for(self, draft: SellerOnboardingDraft) -> SellerOnboardingOutcome:
        prompts = {
            SellerOnboardingStage.NAME: (
                SellerOnboardingStatus.AWAITING_NAME,
                "Comment s'appelle votre boutique ?",
                False,
            ),
            SellerOnboardingStage.LOCATION: (
                SellerOnboardingStatus.AWAITING_LOCATION,
                "Partagez la position de votre boutique.",
                True,
            ),
            SellerOnboardingStage.CITY: (
                SellerOnboardingStatus.AWAITING_CITY,
                "Dans quelle ville se trouve votre boutique ?",
                False,
            ),
        }
        if draft.stage == SellerOnboardingStage.REVIEW:
            return self.review(draft)
        if draft.stage == SellerOnboardingStage.COMPLETED:
            return SellerOnboardingOutcome(
                status=SellerOnboardingStatus.COMPLETED,
                text="Votre espace vendeur est prêt.",
                metadata={
                    "seller_onboarding": True,
                    "open_seller_workspace": True,
                    "store_id": draft.store_id,
                },
            )
        status, text, request_location = prompts[draft.stage]
        return SellerOnboardingOutcome(
            status=status,
            text=text,
            request_location=request_location,
            metadata={"seller_onboarding": True},
        )

    def _save_draft(self, conversation, context, draft) -> None:
        self.context_service.update_context(
            conversation,
            replace(
                context,
                flow=ConversationFlow.SELLER_ONBOARDING,
                seller_onboarding=draft,
            ),
        )

    @staticmethod
    def _normalize_name(raw_name: str) -> str | None:
        name = re.sub(
            r"^ma boutique s['’]appelle\s+", "", raw_name.strip(), flags=re.IGNORECASE
        )
        name = " ".join(name.split())
        return name if 1 <= len(name) <= 255 else None

    @staticmethod
    def _valid_coordinates(latitude, longitude) -> bool:
        try:
            return (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90 <= latitude <= 90
                and -180 <= longitude <= 180
            )
        except TypeError:
            return False

    @staticmethod
    def _is_complete(draft) -> bool:
        return bool(
            draft.store_name
            and draft.city
            and draft.latitude is not None
            and draft.longitude is not None
        )

    @staticmethod
    def _existing_membership(user, *, lock=False):
        queryset = StoreMembership.objects.filter(
            user=user, status=StoreMembershipStatus.ACTIVE
        ).order_by("created_at")
        if lock:
            queryset = queryset.select_for_update()
        return queryset.first()

    @staticmethod
    def _audit(event, conversation, **extra):
        logger.info(event, extra={"conversation_id": str(conversation.id), **extra})
