from django.core.exceptions import ValidationError

from apps.drivers.domain.enums import DriverAvailability, DriverStatus
from apps.drivers.domain.rules import validate_driver_state


def clean_driver_state(
    status: DriverStatus | str,
    availability_status: DriverAvailability | str,
) -> None:
    try:
        validate_driver_state(status, availability_status)
    except ValidationError:
        raise
