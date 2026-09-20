from django.db import models


class DriverStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    SUSPENDED = "SUSPENDED", "Suspended"


class DriverAvailability(models.TextChoices):
    OFFLINE = "OFFLINE", "Offline"
    AVAILABLE = "AVAILABLE", "Available"
    BUSY = "BUSY", "Busy"


class VehicleType(models.TextChoices):
    MOTORBIKE = "MOTORBIKE", "Motorbike"
    BICYCLE = "BICYCLE", "Bicycle"
    CAR = "CAR", "Car"
    VAN = "VAN", "Van"
    OTHER = "OTHER", "Other"


class DriverOfferStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    EXPIRED = "EXPIRED", "Expired"
    CANCELLED = "CANCELLED", "Cancelled"


class DriverMatchingAttemptStatus(models.TextChoices):
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    EXHAUSTED = "EXHAUSTED", "Exhausted"
    CANCELLED = "CANCELLED", "Cancelled"
    FAILED_TECHNICAL = "FAILED_TECHNICAL", "Failed technical"


class DriverOfferNotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    FAILED_RETRYABLE = "FAILED_RETRYABLE", "Failed retryable"
    FAILED_PERMANENT = "FAILED_PERMANENT", "Failed permanent"
    CANCELLED = "CANCELLED", "Cancelled"


class DeliveryIssueType(models.TextChoices):
    CUSTOMER_UNREACHABLE = "CUSTOMER_UNREACHABLE", "Customer unreachable"
    ADDRESS_PROBLEM = "ADDRESS_PROBLEM", "Address problem"
    PICKUP_PROBLEM = "PICKUP_PROBLEM", "Pickup problem"
    VEHICLE_PROBLEM = "VEHICLE_PROBLEM", "Vehicle problem"
    DELIVERY_IMPOSSIBLE = "DELIVERY_IMPOSSIBLE", "Delivery impossible"
    OTHER = "OTHER", "Other"


class DeliveryIssueStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    RESOLVED = "RESOLVED", "Resolved"
    TERMINAL = "TERMINAL", "Terminal"
