from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.drivers.domain.enums import (
    DriverAvailability,
    DriverMatchingAttemptStatus,
    DriverOfferNotificationStatus,
    DriverOfferStatus,
    DeliveryIssueStatus,
    DeliveryIssueType,
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
    # Set by the location ingestion boundary planned for lot 9.3.  Availability
    # uses it as freshness evidence; coordinates are deliberately not stored here.
    last_location_at = models.DateTimeField(blank=True, null=True)
    last_location = gis_models.PointField(geography=True, srid=4326, blank=True, null=True)
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


class DriverOffer(BaseModel):
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="driver_offers")
    driver = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name="offers")
    status = models.CharField(max_length=20, choices=DriverOfferStatus.choices, default=DriverOfferStatus.PENDING)
    rank = models.PositiveIntegerField()
    strategy_version = models.CharField(max_length=50)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(blank=True, null=True)
    rejected_at = models.DateTimeField(blank=True, null=True)
    expired_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.CharField(max_length=80, blank=True, null=True)
    candidate_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["order", "status"], name="driver_offer_order_status_idx"), models.Index(fields=["driver", "status"], name="driver_offer_driver_status_idx"), models.Index(fields=["status", "expires_at"], name="driver_offer_status_exp_idx")]
        constraints = [
            models.UniqueConstraint(fields=["order", "driver"], name="driver_offer_order_driver_unique"),
            models.UniqueConstraint(fields=["order"], condition=models.Q(status=DriverOfferStatus.PENDING), name="driver_offer_one_pending_per_order"),
            models.UniqueConstraint(fields=["order"], condition=models.Q(status=DriverOfferStatus.ACCEPTED), name="driver_offer_one_accepted_per_order"),
            models.CheckConstraint(condition=models.Q(status__in=DriverOfferStatus.values), name="driver_offer_status_valid"),
        ]


class DriverAssignment(BaseModel):
    """The durable active-delivery truth until a richer Delivery model exists."""
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="driver_assignment")
    driver = models.ForeignKey(DriverProfile, on_delete=models.PROTECT, related_name="assignments")
    offer = models.OneToOneField(DriverOffer, on_delete=models.PROTECT, related_name="assignment")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["driver"], condition=models.Q(is_active=True), name="driver_assignment_one_active_per_driver")]


class DeliveryIssue(BaseModel):
    """Persistent business incident; it never changes Order state by itself."""
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="delivery_issues")
    assignment = models.ForeignKey(DriverAssignment, on_delete=models.PROTECT, related_name="issues")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reported_delivery_issues")
    issue_type = models.CharField(max_length=40, choices=DeliveryIssueType.choices)
    status = models.CharField(max_length=20, choices=DeliveryIssueStatus.choices, default=DeliveryIssueStatus.OPEN)
    note = models.TextField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolution = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=["order", "status"], name="drv_issue_order_status_idx")]
        constraints = [models.UniqueConstraint(fields=["assignment", "issue_type"], condition=models.Q(status=DeliveryIssueStatus.OPEN), name="delivery_issue_one_open_type")]


class DriverLocationReminder(BaseModel):
    driver = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name="location_reminders")
    reason = models.CharField(max_length=40)
    cycle_id = models.UUIDField()
    reminder_count = models.PositiveIntegerField(default=0)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    exhausted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["driver", "resolved_at", "updated_at"], name="driver_loc_reminder_idx")]
        constraints = [models.UniqueConstraint(fields=["driver"], condition=models.Q(resolved_at__isnull=True, exhausted_at__isnull=True), name="driver_one_active_loc_reminder")]


class DriverMatchingAttempt(BaseModel):
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="driver_matching_attempt")
    status = models.CharField(max_length=30, choices=DriverMatchingAttemptStatus.choices, default=DriverMatchingAttemptStatus.RUNNING)
    strategy = models.CharField(max_length=30, default="SEQUENTIAL")
    strategy_version = models.CharField(max_length=50)
    candidate_snapshot = models.JSONField(default=list)
    current_position = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)


class DriverOfferNotification(BaseModel):
    offer = models.ForeignKey(DriverOffer, on_delete=models.CASCADE, related_name="notifications")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel_identity = models.ForeignKey("messaging.ChannelIdentity", on_delete=models.PROTECT)
    conversation = models.ForeignKey("conversations.Conversation", on_delete=models.SET_NULL, null=True, blank=True)
    channel = models.CharField(max_length=20)
    status = models.CharField(max_length=30, choices=DriverOfferNotificationStatus.choices, default=DriverOfferNotificationStatus.PENDING)
    idempotency_key = models.CharField(max_length=180, unique=True)
    presentation_snapshot = models.JSONField(default=dict)
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_retry_at"], name="driver_offer_notif_retry_idx")]
