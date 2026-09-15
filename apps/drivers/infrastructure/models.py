from django.conf import settings
from django.db import models

from apps.drivers.domain.enums import (
    DriverAvailability,
    DriverStatus,
    VehicleType,
)
from apps.drivers.domain.rules import (
    validate_driver_state,
    validate_service_radius_meters,
)
from common.models import BaseModel


class DriverProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="driver_profile",
    )
    status = models.CharField(
        max_length=20,
        choices=DriverStatus.choices,
        default=DriverStatus.INACTIVE,
    )
    availability_status = models.CharField(
        max_length=20,
        choices=DriverAvailability.choices,
        default=DriverAvailability.OFFLINE,
    )
    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
        blank=True,
        null=True,
    )
    vehicle_label = models.CharField(max_length=120, blank=True, null=True)
    license_plate = models.CharField(max_length=40, blank=True, null=True)
    service_radius_meters = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[validate_service_radius_meters],
    )
    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                name="drivers_status_valid",
                condition=models.Q(status__in=DriverStatus.values),
            ),
            models.CheckConstraint(
                name="drivers_availability_valid",
                condition=models.Q(availability_status__in=DriverAvailability.values),
            ),
            models.CheckConstraint(
                name="drivers_vehicle_type_valid",
                condition=(
                    models.Q(vehicle_type__in=VehicleType.values)
                    | models.Q(vehicle_type__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="drivers_service_radius_positive_or_null",
                condition=(
                    models.Q(service_radius_meters__gt=0)
                    | models.Q(service_radius_meters__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="drivers_available_requires_active",
                condition=(
                    ~models.Q(availability_status=DriverAvailability.AVAILABLE)
                    | models.Q(status=DriverStatus.ACTIVE)
                ),
            ),
        ]

    def clean(self) -> None:
        super().clean()
        validate_service_radius_meters(self.service_radius_meters)
        validate_driver_state(self.status, self.availability_status)

    def save(self, *args: object, **kwargs: object) -> None:
        validate_driver_state(self.status, self.availability_status)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} - {self.status}/{self.availability_status}"
