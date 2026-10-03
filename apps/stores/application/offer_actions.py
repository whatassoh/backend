"""Authorization and channel-neutral actions for StoreOffer recipients."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from django.utils import timezone

from apps.messaging.infrastructure.models import ChannelIdentity
from apps.stores.application.offers import StoreOfferService
from apps.stores.domain.enums import (
    StoreMembershipRole,
    StoreMembershipStatus,
    StoreOfferStatus,
)
from apps.stores.infrastructure.models import StoreMembership, StoreOffer


class StoreOfferActionResult(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


STORE_OFFER_ACTIONS = ("store_offer.accept", "store_offer.reject")


def encode_store_offer_action(action_id: str, offer_id) -> str:
    if action_id not in STORE_OFFER_ACTIONS:
        raise ValueError("Unsupported StoreOffer action.")
    return f"{action_id}:{UUID(str(offer_id))}"


def decode_store_offer_action(callback_id: str) -> tuple[str, UUID] | None:
    action_id, separator, raw_offer_id = (callback_id or "").partition(":")
    if not separator or action_id not in STORE_OFFER_ACTIONS:
        return None
    try:
        return action_id, UUID(raw_offer_id)
    except (ValueError, AttributeError):
        return None


class StoreRecipientResolver:
    """V1 sends to active operational members, one identity per channel/user."""

    roles = (
        StoreMembershipRole.OWNER,
        StoreMembershipRole.MANAGER,
        StoreMembershipRole.AGENT,
    )

    def resolve(self, store):
        memberships = StoreMembership.objects.filter(
            store=store, status=StoreMembershipStatus.ACTIVE, role__in=self.roles
        ).select_related("user")
        identities = ChannelIdentity.objects.filter(
            user__in=memberships.values("user"), is_active=True
        ).order_by("user_id", "channel", "id")
        seen = set()
        result = []
        for identity in identities:
            key = (identity.user_id, identity.channel)
            if key not in seen:
                seen.add(key)
                result.append(identity)
        return tuple(result)


class StoreOfferAuthorizationPolicy:
    roles = StoreRecipientResolver.roles

    def can_view_offer(self, actor, offer):
        return StoreMembership.objects.filter(
            store_id=offer.store_id,
            user=actor,
            status=StoreMembershipStatus.ACTIVE,
            role__in=self.roles,
        ).exists()

    can_accept_offer = can_view_offer
    can_reject_offer = can_view_offer


@dataclass(frozen=True, kw_only=True)
class StoreOfferPresentation:
    offer_id: str
    order_reference: str
    items: tuple[str, ...]
    distance_meters: int | None
    expires_at: str
    available_actions: tuple[str, ...]


def present_offer(offer: StoreOffer) -> StoreOfferPresentation:
    items = offer.order.items.select_related("product", "product_variant").order_by(
        "created_at", "id"
    )
    snapshot = offer.selection_snapshot or {}
    return StoreOfferPresentation(
        offer_id=str(offer.id),
        order_reference=offer.order.reference,
        items=tuple(_present_item(item) for item in items),
        distance_meters=snapshot.get("distance_meters"),
        expires_at=offer.expires_at.isoformat(),
        available_actions=("store_offer.accept", "store_offer.reject")
        if offer.status == StoreOfferStatus.PENDING
        else (),
    )


def _present_item(item) -> str:
    quantity = format(item.quantity, "f").rstrip("0").rstrip(".")
    product_name = item.product.name if item.product_id else item.requested_name
    variant_name = item.product_variant.name if item.product_variant_id else None
    identity = f"{product_name} — {variant_name}" if variant_name else product_name
    return f"{quantity} × {identity}"


class StoreOfferActionService:
    def __init__(self, *, authorization_policy=None, offer_service=None, matching=None):
        self.authorization_policy = (
            authorization_policy or StoreOfferAuthorizationPolicy()
        )
        self.offer_service = offer_service or StoreOfferService()
        self.matching = matching

    def _matching(self):
        if self.matching is None:
            from apps.stores.application.matching import StoreMatchingOrchestrator

            self.matching = StoreMatchingOrchestrator()
        return self.matching

    def handle(self, *, actor, offer_id, action_id, correlation_id=None):
        offer = (
            StoreOffer.objects.select_related("store", "order")
            .filter(pk=offer_id)
            .first()
        )
        if offer is None or not self.authorization_policy.can_view_offer(actor, offer):
            return StoreOfferActionResult.NOT_AUTHORIZED
        if (
            action_id == "store_offer.accept"
            and offer.status == StoreOfferStatus.ACCEPTED
        ):
            return StoreOfferActionResult.ACCEPTED
        if (
            offer.status != StoreOfferStatus.PENDING
            or offer.expires_at <= timezone.now()
        ):
            return StoreOfferActionResult.NOT_AVAILABLE
        try:
            if action_id == "store_offer.accept":
                accepted = self.offer_service.accept_offer(
                    offer, correlation_id=correlation_id
                )
                attempt = getattr(accepted.order, "store_matching_attempt", None)
                if attempt is not None:
                    self._matching().advance_matching(
                        attempt.id, "ACCEPTED", offer_id=accepted.id
                    )
                return StoreOfferActionResult.ACCEPTED
            if action_id == "store_offer.reject":
                rejected = self.offer_service.reject_offer(
                    offer, correlation_id=correlation_id
                )
                attempt = getattr(rejected.order, "store_matching_attempt", None)
                if attempt is not None:
                    self._matching().advance_matching(
                        attempt.id, "REJECTED", offer_id=rejected.id
                    )
                return StoreOfferActionResult.REJECTED
        except ValueError:
            return StoreOfferActionResult.NOT_AVAILABLE
        return StoreOfferActionResult.NOT_AVAILABLE
