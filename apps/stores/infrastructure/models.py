from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.stores.domain.enums import (
    StoreMembershipRole,
    StoreMembershipStatus,
    StoreOfferStatus,
    StoreMatchingAttemptStatus,
    StoreOfferNotificationStatus,
    StoreOfferNotificationAttemptStatus,
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


class StoreOffer(BaseModel):
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="store_offers")
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="offers")
    status = models.CharField(max_length=20, choices=StoreOfferStatus.choices, default=StoreOfferStatus.PENDING)
    rank = models.PositiveIntegerField()
    strategy_version = models.CharField(max_length=50)
    selection_snapshot = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(blank=True, null=True)
    rejected_at = models.DateTimeField(blank=True, null=True)
    expired_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.CharField(max_length=40, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "status"], name="store_offer_order_status_idx"),
            models.Index(fields=["status", "expires_at"], name="store_offer_status_exp_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["order", "store"], name="store_offer_order_store_unique"),
            models.UniqueConstraint(fields=["order"], condition=models.Q(status=StoreOfferStatus.ACCEPTED), name="store_offer_one_accepted_per_order"),
            models.CheckConstraint(condition=models.Q(status__in=StoreOfferStatus.values), name="store_offer_status_valid"),
        ]


class StoreMatchingAttempt(BaseModel):
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="store_matching_attempt")
    status = models.CharField(max_length=20, choices=StoreMatchingAttemptStatus.choices, default=StoreMatchingAttemptStatus.RUNNING)
    strategy_version = models.CharField(max_length=50)
    candidate_snapshot = models.JSONField(default=list)
    current_position = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    failure_reason = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=["status", "updated_at"], name="store_match_attempt_scan_idx")]


class StoreOfferNotification(BaseModel):
    offer = models.ForeignKey(StoreOffer, on_delete=models.CASCADE, related_name="notifications")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_offer_notifications")
    channel_identity = models.ForeignKey("messaging.ChannelIdentity", on_delete=models.PROTECT, related_name="store_offer_notifications")
    conversation = models.ForeignKey("conversations.Conversation", on_delete=models.SET_NULL, null=True, blank=True, related_name="store_offer_notifications")
    channel = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=StoreOfferNotificationStatus.choices, default=StoreOfferNotificationStatus.PENDING)
    presentation_snapshot = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=150, unique=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_error_category = models.CharField(max_length=40, null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_retry_at"], name="store_offer_notif_retry_idx"), models.Index(fields=["offer", "status"], name="store_offer_notif_offer_idx")]


class StoreOfferNotificationAttempt(BaseModel):
    notification = models.ForeignKey(StoreOfferNotification, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveIntegerField()
    provider = models.CharField(max_length=40)
    channel = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=StoreOfferNotificationAttemptStatus.choices, default=StoreOfferNotificationAttemptStatus.STARTED)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, null=True, blank=True)
    error_category = models.CharField(max_length=40, null=True, blank=True)
    retryable = models.BooleanField(default=False)
    correlation_id = models.CharField(max_length=100, null=True, blank=True)
    message_event = models.ForeignKey("messaging.MessageEvent", on_delete=models.SET_NULL, null=True, blank=True, related_name="store_offer_notification_attempts")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["notification", "attempt_number"], name="store_offer_notif_attempt_unique")]
