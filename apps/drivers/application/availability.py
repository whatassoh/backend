"""Availability mutations and derived matchability, independent of channels."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.drivers.domain.enums import DriverAvailability
from apps.drivers.infrastructure.models import DriverProfile

logger = logging.getLogger(__name__)


class DriverMatchability(StrEnum):
    MATCHABLE = "MATCHABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    LOCATION_REQUIRED = "LOCATION_REQUIRED"
    LOCATION_REFRESH_REQUIRED = "LOCATION_REFRESH_REQUIRED"
    ACTIVE_DELIVERY = "ACTIVE_DELIVERY"


@dataclass(frozen=True, kw_only=True)
class MatchabilityResult:
    status: DriverMatchability
    detail: str

    @property
    def can_receive_offer(self) -> bool:
        return self.status == DriverMatchability.MATCHABLE


class DriverAvailabilityService:
    """The persisted manual intent is availability_status; matchability is derived."""

    def __init__(self, *, geo_index=None):
        self.geo_index = geo_index

    def set_available(self, driver: DriverProfile, actor) -> DriverProfile:
        return self._set(driver, actor, DriverAvailability.AVAILABLE)

    def set_offline(self, driver: DriverProfile, actor) -> DriverProfile:
        # This only opts out of *new* work. It never changes an assigned order.
        return self._set(driver, actor, DriverAvailability.OFFLINE)

    @transaction.atomic
    def _set(self, driver, actor, target):
        locked = DriverProfile.objects.select_for_update().select_related("user").get(pk=driver.pk)
        if actor is None or locked.user_id != getattr(actor, "id", None):
            raise PermissionDenied("Only the driver can change this availability.")
        changed = locked.availability_status != target
        if changed:
            # Model validation preserves the existing active-driver requirement.
            locked.availability_status = target
            locked.save(update_fields=["availability_status", "updated_at"])
        try:
            from apps.drivers.application.location import DriverGeoIndex

            geo_index = self.geo_index or DriverGeoIndex()
            if target == DriverAvailability.AVAILABLE:
                geo_index.index(locked)
            else:
                geo_index.remove(locked)
        except Exception:
            logger.warning(
                "driver_geo_index_sync_failed",
                extra={"driver_id": str(locked.id), "availability": target},
            )
        if changed:
            event = (
                "driver_became_available"
                if target == DriverAvailability.AVAILABLE
                else "driver_went_offline"
            )
            logger.info(event, extra={"driver_id": str(locked.id)})
        return locked

    def get_matchability(self, driver: DriverProfile, *, now=None) -> MatchabilityResult:
        now = now or timezone.now()
        if timezone.is_naive(now):
            raise ValueError("now must be timezone-aware")
        # Explicit database query, not cache/Redis: the assignment relation will
        # be added in 9.5. Until then no persistent assignment can exist.
        if self.has_active_delivery(driver):
            return MatchabilityResult(status=DriverMatchability.ACTIVE_DELIVERY, detail="active_delivery")
        if driver.availability_status != DriverAvailability.AVAILABLE:
            return MatchabilityResult(status=DriverMatchability.NOT_AVAILABLE, detail="offline_or_busy")
        location_at = driver.last_location_at
        if location_at is None:
            return MatchabilityResult(status=DriverMatchability.LOCATION_REQUIRED, detail="no_location")
        if timezone.is_naive(location_at):
            return MatchabilityResult(status=DriverMatchability.LOCATION_REFRESH_REQUIRED, detail="invalid_location_timestamp")
        freshness = timedelta(seconds=settings.DRIVER_LOCATION_FRESHNESS_SECONDS)
        if location_at > now or now - location_at > freshness:
            return MatchabilityResult(status=DriverMatchability.LOCATION_REFRESH_REQUIRED, detail="stale_or_future_location")
        return MatchabilityResult(status=DriverMatchability.MATCHABLE, detail="fresh_location")

    def can_receive_offer(self, driver: DriverProfile, *, now=None) -> bool:
        return self.get_matchability(driver, now=now).can_receive_offer

    @staticmethod
    def has_active_delivery(driver: DriverProfile) -> bool:
        """PostgreSQL truth hook.

        There is currently no Delivery/assignment model or driver FK on Order,
        so an active delivery cannot yet be represented.  This deliberately
        remains a single application boundary rather than a Redis flag.
        """
        from apps.drivers.infrastructure.models import DriverAssignment
        return DriverAssignment.objects.filter(driver=driver, is_active=True).exists()
