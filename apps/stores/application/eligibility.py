"""Deterministic, side-effect-free store eligibility for confirmed orders."""

from dataclasses import dataclass
from enum import StrEnum

from django.contrib.gis.db.models.functions import Distance
from django.db.models import F

from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreStatus
from apps.stores.infrastructure.models import Store


class StoreExclusionReason(StrEnum):
    ORDER_NOT_CONFIRMED = "ORDER_NOT_CONFIRMED"
    ORDER_LOCATION_MISSING = "ORDER_LOCATION_MISSING"
    STORE_INACTIVE = "STORE_INACTIVE"
    STORE_NOT_OPERATIONAL = "STORE_NOT_OPERATIONAL"
    TOO_FAR = "TOO_FAR"


@dataclass(frozen=True, kw_only=True)
class EligibleStore:
    store_id: str
    distance_meters: int
    eligibility_reasons: tuple[str, ...] = ("eligible",)


@dataclass(frozen=True, kw_only=True)
class ExcludedStore:
    store_id: str
    reason: StoreExclusionReason
    distance_meters: int | None = None


@dataclass(frozen=True, kw_only=True)
class StoreEligibilityResult:
    order_id: str
    eligible_stores: tuple[EligibleStore, ...]
    excluded_stores: tuple[ExcludedStore, ...]
    strategy: str
    total_considered: int
    total_eligible: int
    metadata: dict[str, str]


class StoreEligibilityService:
    """Query only: coverage, ranking and offers intentionally belong to later lots."""

    def find_eligible_stores(self, order: Order, *, limit: int | None = None):
        if order.status != OrderStatus.CONFIRMED:
            return self._invalid_order(order, StoreExclusionReason.ORDER_NOT_CONFIRMED)
        if order.delivery_location is None:
            return self._invalid_order(order, StoreExclusionReason.ORDER_LOCATION_MISSING)

        base = Store.objects.all()
        excluded = []
        inactive = base.exclude(status=StoreStatus.ACTIVE)
        excluded.extend(
            ExcludedStore(store_id=str(store.id), reason=StoreExclusionReason.STORE_INACTIVE)
            for store in inactive.only("id")
        )
        unavailable = base.filter(status=StoreStatus.ACTIVE, is_open=False)
        excluded.extend(
            ExcludedStore(store_id=str(store.id), reason=StoreExclusionReason.STORE_NOT_OPERATIONAL)
            for store in unavailable.only("id")
        )

        # Geography=True makes Distance return metres on PostGIS.  The DB both
        # calculates and filters distance; Python only maps rows to DTOs.
        candidates = (
            base.filter(status=StoreStatus.ACTIVE, is_open=True)
            .annotate(distance=Distance("location", order.delivery_location))
            .order_by("distance", "id")
        )
        eligible_rows = candidates.filter(distance__lte=F("service_radius_meters"))
        far_rows = candidates.filter(distance__gt=F("service_radius_meters"))
        excluded.extend(
            ExcludedStore(
                store_id=str(store.id),
                reason=StoreExclusionReason.TOO_FAR,
                distance_meters=int(round(store.distance.m)),
            )
            for store in far_rows
        )
        if limit is not None:
            eligible_rows = eligible_rows[:limit]
        eligible = [
            EligibleStore(
                store_id=str(store.id), distance_meters=int(round(store.distance.m))
            )
            for store in eligible_rows
        ]
        return StoreEligibilityResult(
            order_id=str(order.id),
            eligible_stores=tuple(eligible),
            excluded_stores=tuple(excluded),
            strategy="postgis_distance_then_store_radius",
            total_considered=base.count(),
            total_eligible=len(eligible),
            metadata={"distance_unit": "meters", "order_mutated": "false"},
        )

    @staticmethod
    def _invalid_order(order, reason):
        return StoreEligibilityResult(
            order_id=str(order.id),
            eligible_stores=(),
            excluded_stores=(),
            strategy="precondition_failed",
            total_considered=0,
            total_eligible=0,
            metadata={"precondition": reason.value, "distance_unit": "meters"},
        )
