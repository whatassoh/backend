from django.db import models


class AuditActorType(models.TextChoices):
    USER = "USER", "User"
    SYSTEM = "SYSTEM", "System"
    ADMIN = "ADMIN", "Admin"


class AuditEntityType(models.TextChoices):
    ORDER = "ORDER", "Order"
    STORE_OFFER = "STORE_OFFER", "Store offer"
    DRIVER_OFFER = "DRIVER_OFFER", "Driver offer"
    DELIVERY = "DELIVERY", "Delivery"


class AuditEventType(models.TextChoices):
    ORDER_CREATED = "ORDER_CREATED", "Order created"
    ORDER_ITEM_ADDED = "ORDER_ITEM_ADDED", "Order item added"
    ORDER_ITEM_UPDATED = "ORDER_ITEM_UPDATED", "Order item updated"
    ORDER_ITEM_REMOVED = "ORDER_ITEM_REMOVED", "Order item removed"
    ORDER_DELIVERY_LOCATION_UPDATED = (
        "ORDER_DELIVERY_LOCATION_UPDATED",
        "Order delivery location updated",
    )
    ORDER_DELIVERY_INSTRUCTIONS_UPDATED = (
        "ORDER_DELIVERY_INSTRUCTIONS_UPDATED",
        "Order delivery instructions updated",
    )
    ORDER_CONFIRMED = "ORDER_CONFIRMED", "Order confirmed"
    ORDER_CANCELLED = "ORDER_CANCELLED", "Order cancelled"
    ORDER_FAILED = "ORDER_FAILED", "Order failed"
    ORDER_RETRY_REQUESTED = "ORDER_RETRY_REQUESTED", "Order retry requested"
    ORDER_STATUS_CHANGED = "ORDER_STATUS_CHANGED", "Order status changed"
