from django.db import models


class MessagingChannel(models.TextChoices):
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"


class MessageDirection(models.TextChoices):
    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class MessageType(models.TextChoices):
    TEXT = "TEXT", "Text"
    LOCATION = "LOCATION", "Location"
    IMAGE = "IMAGE", "Image"
    DOCUMENT = "DOCUMENT", "Document"
    AUDIO = "AUDIO", "Audio"
    VIDEO = "VIDEO", "Video"
    INTERACTIVE = "INTERACTIVE", "Interactive"
    TEMPLATE = "TEMPLATE", "Template"
    UNKNOWN = "UNKNOWN", "Unknown"


class MessageStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    PROCESSING = "PROCESSING", "Processing"
    PROCESSED = "PROCESSED", "Processed"
    FAILED = "FAILED", "Failed"
    SENT = "SENT", "Sent"
    IGNORED = "IGNORED", "Ignored"


class NotificationKind(models.TextChoices):
    ORDER_CONFIRMED = "ORDER_CONFIRMED", "Order confirmed"
    STORE_OFFER = "STORE_OFFER", "Store offer"
    STORE_PREPARATION_STARTED = "STORE_PREPARATION_STARTED", "Store preparation started"
    ORDER_READY_FOR_PICKUP = "ORDER_READY_FOR_PICKUP", "Order ready for pickup"
    DRIVER_OFFER = "DRIVER_OFFER", "Driver offer"
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED", "Driver assigned"
    ORDER_PICKED_UP = "ORDER_PICKED_UP", "Order picked up"
    ORDER_IN_DELIVERY = "ORDER_IN_DELIVERY", "Order in delivery"
    ORDER_DELIVERED = "ORDER_DELIVERED", "Order delivered"
    DRIVER_LOCATION_REQUIRED = "DRIVER_LOCATION_REQUIRED", "Driver location required"
    DRIVER_LOCATION_REFRESH_REQUIRED = "DRIVER_LOCATION_REFRESH_REQUIRED", "Driver location refresh required"


class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED_RETRYABLE = "FAILED_RETRYABLE", "Failed retryable"
    FAILED_PERMANENT = "FAILED_PERMANENT", "Failed permanent"
    CANCELLED = "CANCELLED", "Cancelled"


class NotificationAttemptOutcome(models.TextChoices):
    STARTED = "STARTED", "Started"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"
    STALE = "STALE", "Stale"
