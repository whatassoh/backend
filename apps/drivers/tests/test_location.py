from datetime import timedelta

import pytest
from django.utils import timezone

from apps.drivers.application.location import (
    DriverGeoIndex, DriverLocationService, DriverLocationStatus, DriverLocationUpdate,
)
from apps.drivers.application.services import activate_driver, create_driver_profile, set_driver_available
from apps.identity.infrastructure.models import User


class Geo:
    def __init__(self, fail=False): self.fail, self.calls = fail, []
    def geoadd(self, key, value):
        if self.fail: raise RuntimeError("redis down")
        self.calls.append((key, value))
    def zrem(self, key, member): self.calls.append((key, member))


def profile():
    user = User.objects.create_user(whatsapp_phone=f"+237681{User.objects.count():06d}")
    driver = activate_driver(create_driver_profile(user))
    set_driver_available(driver)
    return driver


def update(now, **values):
    return DriverLocationUpdate(latitude=values.get("latitude", 4.05), longitude=values.get("longitude", 9.70), occurred_at=values.get("occurred_at", now), received_at=values.get("received_at", now), source="TEST", accuracy_meters=values.get("accuracy_meters"))


@pytest.mark.django_db
def test_persists_wgs84_lon_lat_and_indexes_opaque_driver_member():
    driver, now, redis = profile(), timezone.now(), Geo()
    result = DriverLocationService(geo_index=DriverGeoIndex(redis)).update_location(driver, update(now), actor=driver.user)
    driver.refresh_from_db()
    assert result.status == DriverLocationStatus.UPDATED and result.redis_indexed
    assert (driver.last_location.x, driver.last_location.y, driver.last_location.srid) == (9.70, 4.05, 4326)
    assert redis.calls[0][1][2] == str(driver.id)


@pytest.mark.django_db
def test_out_of_order_and_duplicate_never_overwrite_latest_position():
    driver, now = profile(), timezone.now()
    service = DriverLocationService(geo_index=DriverGeoIndex(Geo()))
    service.update_location(driver, update(now, longitude=9.71), actor=driver.user)
    driver.refresh_from_db()
    assert service.update_location(driver, update(now - timedelta(seconds=1), longitude=9.70), actor=driver.user).status == DriverLocationStatus.OUT_OF_ORDER
    assert service.update_location(driver, update(now, longitude=9.71), actor=driver.user).status == DriverLocationStatus.DUPLICATE
    driver.refresh_from_db(); assert driver.last_location.x == 9.71


@pytest.mark.django_db
@pytest.mark.parametrize("kwargs", [{"latitude": 91}, {"longitude": 181}, {"latitude": float("nan")}, {"longitude": float("inf")}, {"received_at": timezone.now() - timedelta(seconds=1)}])
def test_invalid_or_future_location_is_rejected(kwargs):
    driver, now = profile(), timezone.now()
    with pytest.raises(ValueError):
        DriverLocationService(geo_index=DriverGeoIndex(Geo())).update_location(driver, update(now, **kwargs), actor=driver.user)


@pytest.mark.django_db
def test_redis_failure_keeps_postgis_position_and_is_rebuildable():
    driver, now = profile(), timezone.now()
    result = DriverLocationService(geo_index=DriverGeoIndex(Geo(fail=True))).update_location(driver, update(now), actor=driver.user)
    driver.refresh_from_db()
    assert result.redis_indexed is False and driver.last_location is not None
    redis = Geo(); assert DriverGeoIndex(redis).rebuild() >= 1


@pytest.mark.django_db
def test_unauthorized_actor_cannot_update_location():
    driver, now = profile(), timezone.now()
    other = User.objects.create_user(whatsapp_phone="+237682000000")
    with pytest.raises(PermissionError):
        DriverLocationService(geo_index=DriverGeoIndex(Geo())).update_location(driver, update(now), actor=other)
