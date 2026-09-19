"""Authorization and channel-neutral actions for StoreOffer recipients."""

from dataclasses import dataclass
from enum import StrEnum

from apps.messaging.infrastructure.models import ChannelIdentity
from apps.stores.application.offers import StoreOfferService
from apps.stores.domain.enums import StoreMembershipRole, StoreMembershipStatus, StoreOfferStatus
from apps.stores.infrastructure.models import StoreMembership, StoreOffer


class StoreOfferActionResult(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class StoreRecipientResolver:
    """V1 sends to active operational members, one identity per channel/user."""

    roles = (StoreMembershipRole.OWNER, StoreMembershipRole.MANAGER, StoreMembershipRole.AGENT)

    def resolve(self, store):
        memberships = StoreMembership.objects.filter(
            store=store, status=StoreMembershipStatus.ACTIVE, role__in=self.roles
        ).select_related("user")
        identities = ChannelIdentity.objects.filter(user__in=memberships.values("user"), is_active=True).order_by("user_id", "channel", "id")
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
            store_id=offer.store_id, user=actor, status=StoreMembershipStatus.ACTIVE,
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
    items = offer.order.items.order_by("created_at", "id")
    snapshot = offer.selection_snapshot or {}
    return StoreOfferPresentation(
        offer_id=str(offer.id), order_reference=offer.order.reference,
        items=tuple(f"{item.quantity} {item.unit} {item.requested_name}" for item in items),
        distance_meters=snapshot.get("distance_meters"), expires_at=offer.expires_at.isoformat(),
        available_actions=("store_offer.accept", "store_offer.reject") if offer.status == StoreOfferStatus.PENDING else (),
    )


class StoreOfferActionService:
    def __init__(self, *, authorization_policy=None, offer_service=None, matching=None):
        self.authorization_policy = authorization_policy or StoreOfferAuthorizationPolicy()
        self.offer_service = offer_service or StoreOfferService()
        self.matching = matching

    def _matching(self):
        if self.matching is None:
            from apps.stores.application.matching import StoreMatchingOrchestrator
            self.matching = StoreMatchingOrchestrator()
        return self.matching

    def handle(self, *, actor, offer_id, action_id):
        offer = StoreOffer.objects.select_related("store", "order").filter(pk=offer_id).first()
        if offer is None or not self.authorization_policy.can_view_offer(actor, offer):
            return StoreOfferActionResult.NOT_AUTHORIZED
        try:
            if action_id == "store_offer.accept":
                accepted = self.offer_service.accept_offer(offer)
                attempt = getattr(accepted.order, "store_matching_attempt", None)
                if attempt is not None:
                    self._matching().advance_matching(attempt.id, "ACCEPTED", offer_id=accepted.id)
                return StoreOfferActionResult.ACCEPTED
            if action_id == "store_offer.reject":
                rejected = self.offer_service.reject_offer(offer)
                attempt = getattr(rejected.order, "store_matching_attempt", None)
                if attempt is not None:
                    self._matching().advance_matching(attempt.id, "REJECTED", offer_id=rejected.id)
                return StoreOfferActionResult.REJECTED
        except ValueError:
            return StoreOfferActionResult.NOT_AVAILABLE
        return StoreOfferActionResult.NOT_AVAILABLE
