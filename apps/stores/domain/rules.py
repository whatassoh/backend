from django.core.exceptions import ValidationError

DEFAULT_SERVICE_RADIUS_METERS = 5000


def validate_service_radius_meters(value: int) -> None:
    if value <= 0:
        raise ValidationError("Service radius must be strictly positive.")
