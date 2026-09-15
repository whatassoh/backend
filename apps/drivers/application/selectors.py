import uuid

from django.db.models import QuerySet

from apps.drivers.domain.enums import DriverAvailability, DriverStatus
from apps.drivers.infrastructure.models import DriverProfile


def get_driver_by_id(driver_id: uuid.UUID | str) -> DriverProfile | None:
    return DriverProfile.objects.select_related("user").filter(id=driver_id).first()


def get_driver_by_user(user: object) -> DriverProfile | None:
    return DriverProfile.objects.select_related("user").filter(user=user).first()


def get_active_drivers() -> QuerySet[DriverProfile]:
    return DriverProfile.objects.select_related("user").filter(
        status=DriverStatus.ACTIVE
    )


def get_available_drivers() -> QuerySet[DriverProfile]:
    return get_active_drivers().filter(availability_status=DriverAvailability.AVAILABLE)
