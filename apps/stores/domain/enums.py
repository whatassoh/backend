from django.db import models


class StoreStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    SUSPENDED = "SUSPENDED", "Suspended"


class StoreOfferStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    EXPIRED = "EXPIRED", "Expired"
    CANCELLED = "CANCELLED", "Cancelled"


class StoreMatchingAttemptStatus(models.TextChoices):
    RUNNING = "RUNNING", "Running"
    EXHAUSTED = "EXHAUSTED", "Exhausted"
    SUCCEEDED = "SUCCEEDED", "Succeeded"


class PartialStoreCoverageProposalStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    CANCELLED = "CANCELLED", "Cancelled"


class StoreOfferNotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    FAILED_RETRYABLE = "FAILED_RETRYABLE", "Failed retryable"
    FAILED_PERMANENT = "FAILED_PERMANENT", "Failed permanent"
    CANCELLED = "CANCELLED", "Cancelled"


class StoreOfferNotificationAttemptStatus(models.TextChoices):
    STARTED = "STARTED", "Started"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"


class StoreMembershipRole(models.TextChoices):
    OWNER = "OWNER", "Owner"
    MANAGER = "MANAGER", "Manager"
    AGENT = "AGENT", "Agent"


class StoreMembershipStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
