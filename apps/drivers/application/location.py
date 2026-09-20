"""Channel-neutral durable driver location and rebuildable Redis GEO index."""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite

from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone
from django_redis import get_redis_connection

from apps.drivers.domain.enums import DriverAvailability
from apps.drivers.infrastructure.models import DriverProfile

logger = logging.getLogger(__name__)


class DriverLocationStatus(StrEnum):
    UPDATED = "UPDATED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"


@dataclass(frozen=True, kw_only=True)
class DriverLocationUpdate:
    latitude: float
    longitude: float
    occurred_at: datetime
    received_at: datetime
    source: str
    accuracy_meters: float | None = None


@dataclass(frozen=True, kw_only=True)
class DriverLocationResult:
    status: DriverLocationStatus
    redis_indexed: bool


class DriverGeoIndex:
    key = "drivers:geo:available"

    def __init__(self, client=None):
        self.client = client

    def _client(self):
        return self.client or get_redis_connection("default")

    def index(self, driver):
        if driver.last_location is None or driver.availability_status != DriverAvailability.AVAILABLE:
            return self.remove(driver)
        self._client().geoadd(self.key, (driver.last_location.x, driver.last_location.y, str(driver.id)))

    def remove(self, driver):
        self._client().zrem(self.key, str(driver.id))

    def nearby(self, *, longitude, latitude, radius_meters, count):
        """Return opaque members with Redis' distance in meters, nearest first."""
        rows = self._client().geosearch(
            self.key, longitude=longitude, latitude=latitude,
            radius=radius_meters, unit="m", sort="ASC", count=count, withdist=True,
        )
        return [
            ((member.decode() if isinstance(member, bytes) else str(member)), float(distance))
            for member, distance in rows
        ]

    def rebuild(self):
        count = 0
        for driver in DriverProfile.objects.filter(availability_status=DriverAvailability.AVAILABLE, last_location__isnull=False).iterator():
            self.index(driver)
            count += 1
        return count


class DriverLocationService:
    """PostGIS is authoritative; Redis indexing is best-effort and rebuildable."""

    def __init__(self, *, geo_index=None):
        self.geo_index = geo_index or DriverGeoIndex()

    def update_location(self, driver, update, *, actor):
        self._validate(update)
        if actor is None or driver.user_id != getattr(actor, "id", None):
            raise PermissionError("Only the driver may update this location.")
        with transaction.atomic():
            locked = DriverProfile.objects.select_for_update().get(pk=driver.pk)
            if locked.last_location_at and update.occurred_at < locked.last_location_at:
                return DriverLocationResult(status=DriverLocationStatus.OUT_OF_ORDER, redis_indexed=False)
            point = Point(update.longitude, update.latitude, srid=4326)
            if locked.last_location_at == update.occurred_at:
                if locked.last_location and locked.last_location.equals_exact(point):
                    return DriverLocationResult(status=DriverLocationStatus.DUPLICATE, redis_indexed=False)
                return DriverLocationResult(status=DriverLocationStatus.OUT_OF_ORDER, redis_indexed=False)
            locked.last_location, locked.last_location_at = point, update.occurred_at
            locked.save(update_fields=["last_location", "last_location_at", "updated_at"])
        try:
            self.geo_index.index(locked)
            indexed = True
        except Exception:
            indexed = False
            logger.warning("driver_geo_index_update_failed", extra={"driver_id": str(locked.id)})
        logger.info("driver_location_updated", extra={"driver_id": str(locked.id), "source": update.source})
        return DriverLocationResult(status=DriverLocationStatus.UPDATED, redis_indexed=indexed)

    @staticmethod
    def _validate(update):
        try:
            valid_coordinates = isfinite(update.latitude) and isfinite(update.longitude) and -90 <= update.latitude <= 90 and -180 <= update.longitude <= 180
        except TypeError:
            valid_coordinates = False
        if not valid_coordinates or timezone.is_naive(update.occurred_at) or timezone.is_naive(update.received_at) or update.occurred_at > update.received_at:
            raise ValueError("driver_location_update_invalid")
        if update.accuracy_meters is not None and (not isfinite(update.accuracy_meters) or update.accuracy_meters < 0):
            raise ValueError("driver_location_update_invalid")
