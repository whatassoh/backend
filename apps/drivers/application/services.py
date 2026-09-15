from typing import Any

from django.db import transaction

from apps.drivers.domain.enums import DriverAvailability, DriverStatus
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.application.services import assign_role
from apps.identity.domain.enums import UserRoleType


@transaction.atomic
def create_driver_profile(user: Any, **fields: Any) -> DriverProfile:
    fields.setdefault("status", DriverStatus.INACTIVE)
    fields.setdefault("availability_status", DriverAvailability.OFFLINE)

    driver = DriverProfile.objects.create(user=user, **fields)
    assign_role(user, UserRoleType.DRIVER)

    return driver


def activate_driver(driver: DriverProfile) -> DriverProfile:
    driver.status = DriverStatus.ACTIVE
    driver.availability_status = DriverAvailability.OFFLINE
    driver.save(update_fields=["status", "availability_status", "updated_at"])

    return driver


def deactivate_driver(driver: DriverProfile) -> DriverProfile:
    driver.status = DriverStatus.INACTIVE
    driver.availability_status = DriverAvailability.OFFLINE
    driver.save(update_fields=["status", "availability_status", "updated_at"])

    return driver


def suspend_driver(driver: DriverProfile) -> DriverProfile:
    driver.status = DriverStatus.SUSPENDED
    driver.availability_status = DriverAvailability.OFFLINE
    driver.save(update_fields=["status", "availability_status", "updated_at"])

    return driver


def set_driver_available(driver: DriverProfile) -> DriverProfile:
    driver.status = DriverStatus(driver.status)
    driver.availability_status = DriverAvailability.AVAILABLE
    driver.save(update_fields=["availability_status", "updated_at"])

    return driver


def set_driver_offline(driver: DriverProfile) -> DriverProfile:
    driver.availability_status = DriverAvailability.OFFLINE
    driver.save(update_fields=["availability_status", "updated_at"])

    return driver


def set_driver_busy(driver: DriverProfile) -> DriverProfile:
    driver.availability_status = DriverAvailability.BUSY
    driver.save(update_fields=["availability_status", "updated_at"])

    return driver
