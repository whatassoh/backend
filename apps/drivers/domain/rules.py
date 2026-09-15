from django.core.exceptions import ValidationError

from apps.drivers.domain.enums import DriverAvailability, DriverStatus


def validate_service_radius_meters(value: int | None) -> None:
    if value is not None and value <= 0:
        raise ValidationError("Service radius must be strictly positive.")


def validate_driver_can_be_available(status: DriverStatus | str) -> None:
    if status != DriverStatus.ACTIVE:
        raise ValidationError("Only an active driver can be available.")


def validate_driver_state(
    status: DriverStatus | str,
    availability_status: DriverAvailability | str,
) -> None:
    if availability_status == DriverAvailability.AVAILABLE:
        validate_driver_can_be_available(status)
