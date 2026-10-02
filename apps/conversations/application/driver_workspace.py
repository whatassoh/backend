"""Deterministic Driver Workspace built from authoritative driver services."""

import re
import unicodedata
from dataclasses import dataclass, field, replace
from enum import StrEnum

from django.utils import timezone

from apps.conversations.application.next_steps import NextStep, system_wait
from apps.conversations.domain.context import ActiveJourney, ConversationFlow
from apps.drivers.application.availability import (
    DriverAvailabilityService,
    DriverMatchability,
)
from apps.drivers.application.delivery_execution import (
    DeliveryExecutionService,
    DeliveryMissionAuthorizationError,
    DeliveryMissionNotFound,
    encode_delivery_action,
)
from apps.drivers.application.offers import encode_driver_offer_action
from apps.drivers.application.selectors import get_driver_by_user
from apps.drivers.domain.enums import DriverOfferStatus, DriverStatus
from apps.drivers.infrastructure.models import DriverOffer


class DriverWorkspaceRequest(StrEnum):
    OPEN = "OPEN"


@dataclass(frozen=True, kw_only=True)
class DriverWorkspaceAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class DriverWorkspaceOutcome:
    text: str
    actions: tuple[DriverWorkspaceAction, ...] = ()
    request_location: bool = False
    next_step: NextStep | None = None
    metadata: dict[str, object] = field(
        default_factory=lambda: {"driver_workspace": True}
    )


class DriverWorkspaceService:
    """Read-only operational state composer; mutations stay in Driver services."""

    status_action = "driver.workspace.status"
    legacy_actions = {"driver_onboarding.confirm"}

    def __init__(
        self,
        *,
        context_service=None,
        availability_service=None,
        delivery_service=None,
    ):
        from apps.conversations.application.context import ConversationContextService

        self.context_service = context_service or ConversationContextService()
        self.availability_service = (
            availability_service or DriverAvailabilityService()
        )
        self.delivery_service = delivery_service or DeliveryExecutionService()

    def classify(self, *, user, context, text: str | None):
        if not text or get_driver_by_user(user) is None:
            return None
        normalized = self._normalize(text)
        explicitly_driver = "en tant que livreur" in normalized
        driver_context = context.active_journey == ActiveJourney.DELIVER
        neutral_context = (
            context.active_journey is None
            and context.active_order_id is None
            and context.pending_clarification is None
        )
        if self._is_status_question(normalized) and (
            explicitly_driver or driver_context or neutral_context
        ):
            return DriverWorkspaceRequest.OPEN
        return None

    def handles_action(self, action_id: str | None) -> bool:
        return bool(
            action_id
            and (action_id == self.status_action or action_id in self.legacy_actions)
        )

    @staticmethod
    def resolve_driver(user):
        return get_driver_by_user(user)

    def present(self, *, conversation, prefix: str | None = None):
        driver = get_driver_by_user(conversation.user)
        if driver is None:
            return DriverWorkspaceOutcome(
                text="Créez d'abord votre profil livreur.",
                metadata={"driver_workspace": True, "driver_profile_missing": True},
            )
        self._set_workspace_context(conversation, driver.id)

        mission = self._active_mission(conversation.user)
        if mission is not None:
            outcome = self._mission_outcome(mission)
            return self._with_prefix(outcome, prefix)

        offer = (
            DriverOffer.objects.filter(
                driver=driver,
                status=DriverOfferStatus.PENDING,
                expires_at__gt=timezone.now(),
            )
            .select_related("order")
            .order_by("expires_at", "created_at")
            .first()
        )
        if offer is not None:
            outcome = DriverWorkspaceOutcome(
                text=(
                    f"Vous avez une proposition de livraison en attente pour la "
                    f"commande {offer.order.reference}."
                ),
                actions=(
                    DriverWorkspaceAction(
                        action_id=encode_driver_offer_action(
                            "driver_offer.accept", offer.id
                        ),
                        label="Accepter",
                    ),
                    DriverWorkspaceAction(
                        action_id=encode_driver_offer_action(
                            "driver_offer.reject", offer.id
                        ),
                        label="Refuser",
                    ),
                ),
                metadata={
                    "driver_workspace": True,
                    "driver_workspace_state": "PENDING_OFFER",
                    "driver_offer_id": str(offer.id),
                },
            )
            return self._with_prefix(outcome, prefix)

        if driver.status != DriverStatus.ACTIVE:
            outcome = DriverWorkspaceOutcome(
                text=(
                    "Votre profil livreur n'est pas actif. "
                    "Vous ne pouvez pas recevoir de proposition pour le moment."
                ),
                actions=(
                    DriverWorkspaceAction(
                        action_id=self.status_action, label="Voir mon statut"
                    ),
                ),
                metadata={
                    "driver_workspace": True,
                    "driver_workspace_state": "PROFILE_INACTIVE",
                    "driver_status": driver.status,
                },
            )
            return self._with_prefix(outcome, prefix)

        matchability = self.availability_service.get_matchability(driver)
        outcome = self._matchability_outcome(driver, matchability)
        return self._with_prefix(outcome, prefix)

    def set_available(self, *, conversation):
        driver = get_driver_by_user(conversation.user)
        if driver is None:
            return self.present(conversation=conversation)
        if driver.status != DriverStatus.ACTIVE:
            return self.present(conversation=conversation)
        self.availability_service.set_available(driver, conversation.user)
        return self.present(conversation=conversation)

    def set_offline(self, *, conversation):
        driver = get_driver_by_user(conversation.user)
        if driver is None:
            return self.present(conversation=conversation)
        self.availability_service.set_offline(driver, conversation.user)
        return self.present(conversation=conversation)

    def _active_mission(self, actor):
        try:
            return self.delivery_service.get_active_mission(actor)
        except (DeliveryMissionNotFound, DeliveryMissionAuthorizationError):
            return None

    @staticmethod
    def _mission_outcome(mission):
        labels = {
            "delivery.confirm_pickup": "Commande récupérée",
            "delivery.start_delivery": "Démarrer la livraison",
            "delivery.confirm_delivered": "Commande livrée",
            "delivery.report_issue": "Signaler un problème",
        }
        actions = tuple(
            DriverWorkspaceAction(
                action_id=(
                    action
                    if action == "delivery.report_issue"
                    else encode_delivery_action(action, mission.assignment_id)
                ),
                label=labels[action],
            )
            for action in mission.allowed_actions
        )
        state_text = {
            "DRIVER_ASSIGNED": "La commande doit être récupérée.",
            "PICKED_UP": "La commande a été récupérée et attend le départ.",
            "IN_DELIVERY": "La livraison est en cours.",
        }.get(mission.order_status, "Une mission est active.")
        return DriverWorkspaceOutcome(
            text=f"Mission {mission.order_reference}. {state_text}",
            actions=actions,
            metadata={
                "driver_workspace": True,
                "driver_workspace_state": "ACTIVE_ASSIGNMENT",
                "assignment_id": mission.assignment_id,
                "order_status": mission.order_status,
            },
        )

    def _matchability_outcome(self, driver, matchability):
        common = {
            "driver_workspace": True,
            "driver_id": str(driver.id),
            "matchability": matchability.status.value,
        }
        if matchability.status == DriverMatchability.NOT_AVAILABLE:
            return DriverWorkspaceOutcome(
                text=(
                    "Votre profil livreur est prêt, mais vous êtes actuellement "
                    "indisponible pour de nouvelles livraisons."
                ),
                actions=(
                    DriverWorkspaceAction(
                        action_id="driver.availability.available",
                        label="Me rendre disponible",
                    ),
                    DriverWorkspaceAction(
                        action_id=self.status_action, label="Voir mon statut"
                    ),
                ),
                metadata={**common, "driver_workspace_state": "UNAVAILABLE"},
            )
        if matchability.status in {
            DriverMatchability.LOCATION_REQUIRED,
            DriverMatchability.LOCATION_REFRESH_REQUIRED,
        }:
            missing = matchability.status == DriverMatchability.LOCATION_REQUIRED
            return DriverWorkspaceOutcome(
                text=(
                    "Vous êtes disponible, mais partagez votre position avant de "
                    "pouvoir recevoir des propositions de livraison."
                    if missing
                    else "Vous êtes disponible, mais votre position doit être mise "
                    "à jour avant de pouvoir recevoir des propositions de livraison."
                ),
                actions=(
                    DriverWorkspaceAction(
                        action_id="driver.availability.offline",
                        label="Me rendre indisponible",
                    ),
                ),
                request_location=True,
                metadata={
                    **common,
                    "driver_workspace_state": (
                        "LOCATION_MISSING" if missing else "LOCATION_STALE"
                    ),
                },
            )
        if matchability.status == DriverMatchability.MATCHABLE:
            return DriverWorkspaceOutcome(
                text=(
                    "Vous êtes prêt à recevoir des propositions : votre position est à jour. "
                    "Vous n'avez rien à faire pour l'instant ; je vous enverrai une "
                    "proposition de course dès qu'une livraison correspond."
                ),
                actions=(
                    DriverWorkspaceAction(
                        action_id="driver.availability.offline",
                        label="Me rendre indisponible",
                    ),
                    DriverWorkspaceAction(
                        action_id=self.status_action, label="Voir mon statut"
                    ),
                ),
                next_step=system_wait("Vous n'avez rien à faire pour l'instant."),
                metadata={**common, "driver_workspace_state": "MATCHABLE"},
            )
        return DriverWorkspaceOutcome(
            text="Une mission est active.",
            metadata={**common, "driver_workspace_state": "ACTIVE_ASSIGNMENT"},
        )

    def _set_workspace_context(self, conversation, driver_id):
        context = self.context_service.get_context(conversation)
        metadata = {
            **context.metadata,
            "driver_workspace_active": True,
            "driver_profile_id": str(driver_id),
        }
        metadata.pop("task_paused", None)
        self.context_service.update_context(
            conversation,
            replace(
                context,
                active_journey=ActiveJourney.DELIVER,
                flow=(
                    ConversationFlow.IDLE
                    if context.active_order_id is None
                    else context.flow
                ),
                driver_onboarding=None,
                metadata=metadata,
            ),
        )

    @staticmethod
    def _with_prefix(outcome, prefix):
        if not prefix:
            return outcome
        return replace(outcome, text=f"{prefix}\n\n{outcome.text}")

    @staticmethod
    def _normalize(text: str) -> str:
        value = unicodedata.normalize("NFKD", text.casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    @staticmethod
    def _is_status_question(text: str) -> bool:
        return bool(
            re.fullmatch(
                r"(c est quoi la suite( en tant que livreur)?|"
                r"que dois je faire( maintenant)?|"
                r"que puis je faire( maintenant)?|"
                r"je fais quoi maintenant|quel est mon statut|"
                r"je suis pret je fais quoi)",
                text,
            )
        )
