from django.db import models


class OrderStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    AWAITING_LOCATION = "AWAITING_LOCATION", "Awaiting location"
    CONFIRMED = "CONFIRMED", "Confirmed"
    MATCHING_STORES = "MATCHING_STORES", "Matching stores"
    AWAITING_PARTIAL_COVERAGE = (
        "AWAITING_PARTIAL_COVERAGE",
        "Awaiting partial coverage decision",
    )
    STORE_OFFERED = "STORE_OFFERED", "Store offered"
    STORE_ACCEPTED = "STORE_ACCEPTED", "Store accepted"
    PREPARING = "PREPARING", "Preparing"
    READY_FOR_PICKUP = "READY_FOR_PICKUP", "Ready for pickup"
    MATCHING_DRIVERS = "MATCHING_DRIVERS", "Matching drivers"
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED", "Driver assigned"
    PICKED_UP = "PICKED_UP", "Picked up"
    IN_DELIVERY = "IN_DELIVERY", "In delivery"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"
    FAILED = "FAILED", "Failed"


class OrderItemResolutionStatus(models.TextChoices):
    MATCHED = "MATCHED", "Matched"
    UNMATCHED = "UNMATCHED", "Unmatched"
    AMBIGUOUS = "AMBIGUOUS", "Ambiguous"


class OrderCancellationCode(models.TextChoices):
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST", "Customer request"
    STORE_REQUEST = "STORE_REQUEST", "Store request"
    ADMIN_REQUEST = "ADMIN_REQUEST", "Admin request"
    OTHER = "OTHER", "Other"


class OrderFailureCode(models.TextChoices):
    NO_STORE_AVAILABLE = "NO_STORE_AVAILABLE", "No store available"
    NO_DRIVER_AVAILABLE = "NO_DRIVER_AVAILABLE", "No driver available"
    STORE_FULFILLMENT_FAILED = "STORE_FULFILLMENT_FAILED", "Store fulfillment failed"
    DELIVERY_FAILED = "DELIVERY_FAILED", "Delivery failed"
    OTHER = "OTHER", "Other"
