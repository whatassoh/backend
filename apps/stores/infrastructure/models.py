from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.stores.domain.enums import (
    StoreMembershipRole,
    StoreMembershipStatus,
    StoreStatus,
)
from apps.stores.domain.rules import (
    DEFAULT_SERVICE_RADIUS_METERS,
    validate_service_radius_meters,
)
from common.models import BaseModel


class Store(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=StoreStatus.choices,
        default=StoreStatus.ACTIVE,
    )
    phone = models.CharField(max_length=32, blank=True, null=True)
    location = gis_models.PointField(geography=True, srid=4326)
    address_label = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=120)
    neighborhood = models.CharField(max_length=120, blank=True, null=True)
    service_radius_meters = models.PositiveIntegerField(
        default=DEFAULT_SERVICE_RADIUS_METERS,
        validators=[validate_service_radius_meters],
    )
    is_open = models.BooleanField(default=True)
    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                name="stores_store_status_valid",
                condition=models.Q(status__in=StoreStatus.values),
            ),
            models.CheckConstraint(
                name="stores_service_radius_positive",
                condition=models.Q(service_radius_meters__gt=0),
            ),
        ]

    def __str__(self) -> str:
        return self.name


class StoreMembership(BaseModel):
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=StoreMembershipRole.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=StoreMembershipStatus.choices,
        default=StoreMembershipStatus.ACTIVE,
    )
    class Meta:
        ordering = ["store_id", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "user"],
                name="stores_membership_store_user_unique",
            ),
            models.CheckConstraint(
                name="stores_membership_role_valid",
                condition=models.Q(role__in=StoreMembershipRole.values),
            ),
            models.CheckConstraint(
                name="stores_membership_status_valid",
                condition=models.Q(status__in=StoreMembershipStatus.values),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.store.name} - {self.user} - {self.role}"
