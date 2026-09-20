"""Pure driver discovery, eligibility revalidation and deterministic proximity rank."""

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from django.conf import settings
from django.utils import timezone

from apps.drivers.application.availability import DriverAvailabilityService, DriverMatchability
from apps.drivers.application.location import DriverGeoIndex
from apps.drivers.infrastructure.models import DriverProfile
from apps.orders.domain.enums import OrderStatus
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer

logger = logging.getLogger(__name__)


class DriverMatchingStatus(StrEnum):
    OK = "OK"
    INVALID_ORDER_STATE = "INVALID_ORDER_STATE"
    PICKUP_LOCATION_REQUIRED = "PICKUP_LOCATION_REQUIRED"
    TECHNICAL_UNAVAILABLE = "TECHNICAL_UNAVAILABLE"


@dataclass(frozen=True, kw_only=True)
class RankedDriverCandidate:
    driver_id: str
    rank: int
    distance_meters: float
    matchability: str
    location_age_seconds: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class DriverMatchingResult:
    order_id: str
    pickup_store_id: str | None
    status: DriverMatchingStatus
    strategy: str = "PROXIMITY_V1"
    strategy_version: str = "PROXIMITY_V1"
    candidates: tuple[RankedDriverCandidate, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def candidate_count(self):
        return len(self.candidates)


class DriverMatchingService:
    """Read-only snapshot.  9.5 must revalidate before creating any offer."""

    strategy_version = "PROXIMITY_V1"

    def __init__(self, *, geo_index=None, availability_service=None):
        self.geo_index = geo_index or DriverGeoIndex()
        self.availability_service = availability_service or DriverAvailabilityService()

    def find_candidates(self, order, *, limit=None, context=None):
        if order.status not in {OrderStatus.READY_FOR_PICKUP, OrderStatus.MATCHING_DRIVERS}:
            return self._result(order, status=DriverMatchingStatus.INVALID_ORDER_STATE)
        offer = StoreOffer.objects.select_related("store").filter(
            order=order, status=StoreOfferStatus.ACCEPTED
        ).first()
        if offer is None or offer.store.location is None:
            return self._result(order, status=DriverMatchingStatus.PICKUP_LOCATION_REQUIRED, store_id=(str(offer.store_id) if offer else None))
        limit = settings.DRIVER_MATCHING_MAX_CANDIDATES if limit is None else limit
        if limit <= 0:
            raise ValueError("limit must be positive")
        # Overscan permits stale/offline Redis members to be filtered before the
        # public limit is applied, while keeping the query bounded.
        discovery_limit = limit * 5
        try:
            nearby = self.geo_index.nearby(
                longitude=offer.store.location.x, latitude=offer.store.location.y,
                radius_meters=settings.DRIVER_MATCHING_RADIUS_METERS, count=discovery_limit,
            )
        except Exception:
            logger.warning("driver_matching_geo_unavailable", extra={"order_id": str(order.id), "strategy_version": self.strategy_version})
            return self._result(order, status=DriverMatchingStatus.TECHNICAL_UNAVAILABLE, store_id=str(offer.store_id))
        ids = []
        distances = {}
        for raw_id, distance in nearby:
            try:
                driver_id = uuid.UUID(raw_id)
            except ValueError:
                continue
            ids.append(driver_id)
            distances[str(driver_id)] = distance
        drivers = DriverProfile.objects.in_bulk(ids)
        candidates = []
        for driver_id in ids:
            driver = drivers.get(driver_id)
            if driver is None:
                continue
            matchability = self.availability_service.get_matchability(driver)
            if matchability.status != DriverMatchability.MATCHABLE:
                continue
            age = max(0, int((timezone.now() - driver.last_location_at).total_seconds()))
            candidates.append((distances[str(driver.id)], str(driver.id), age))
        candidates.sort(key=lambda item: (item[0], item[1]))
        ranked = tuple(RankedDriverCandidate(driver_id=driver_id, rank=index, distance_meters=distance, matchability=DriverMatchability.MATCHABLE.value, location_age_seconds=age) for index, (distance, driver_id, age) in enumerate(candidates[:limit], start=1))
        logger.info("driver_matching_executed", extra={"order_id": str(order.id), "candidate_count": len(ranked), "strategy_version": self.strategy_version})
        return DriverMatchingResult(order_id=str(order.id), pickup_store_id=str(offer.store_id), status=DriverMatchingStatus.OK, candidates=ranked, metadata={"snapshot": True, "radius_meters": settings.DRIVER_MATCHING_RADIUS_METERS})

    def _result(self, order, *, status, store_id=None):
        return DriverMatchingResult(order_id=str(order.id), pickup_store_id=store_id, status=status, strategy=self.strategy_version, strategy_version=self.strategy_version)
