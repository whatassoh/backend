"""Bounded, DB-backed requests for a native driver location update."""

import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.drivers.application.availability import (
    DriverAvailabilityService,
    DriverMatchability,
)
from apps.drivers.infrastructure.models import DriverLocationReminder, DriverProfile
from apps.messaging.application.notifications import NotificationService
from apps.messaging.domain.enums import NotificationKind


class DriverLocationReminderService:
    def __init__(self, *, availability=None, notifications=None):
        self.availability = availability or DriverAvailabilityService()
        self.notifications = notifications or NotificationService()

    @transaction.atomic
    def consider(self, driver_id, *, correlation_id=None):
        driver = DriverProfile.objects.select_for_update().select_related("user").get(pk=driver_id)
        active_assignment = self.availability.has_active_delivery(driver)
        matchability = self.availability.get_matchability(driver)
        if active_assignment:
            reason = "ACTIVE_DELIVERY_REFRESH"
            if driver.last_location_at and matchability.detail != "active_delivery":
                return None
            # Active delivery hides freshness in matchability; evaluate via the
            # same service on an equivalent available profile is not allowed.
            freshness = settings.DRIVER_LOCATION_FRESHNESS_SECONDS
            stale = driver.last_location_at is None or (timezone.now() - driver.last_location_at).total_seconds() > freshness
            if not stale:
                return None
        else:
            if matchability.status not in (DriverMatchability.LOCATION_REQUIRED, DriverMatchability.LOCATION_REFRESH_REQUIRED):
                return None
            reason = str(matchability.status)
        reminder, _ = DriverLocationReminder.objects.get_or_create(driver=driver, defaults={"reason": reason, "cycle_id": uuid.uuid4()})
        if reminder.resolved_at or reminder.exhausted_at:
            return None
        interval = settings.DRIVER_LOCATION_REMINDER_INTERVAL_SECONDS
        if reminder.last_reminded_at and (timezone.now() - reminder.last_reminded_at).total_seconds() < interval:
            return None
        if reminder.reminder_count >= settings.DRIVER_LOCATION_REMINDER_MAX_ATTEMPTS:
            reminder.exhausted_at = timezone.now()
            reminder.save(update_fields=["exhausted_at", "updated_at"])
            return None
        attempt = reminder.reminder_count + 1
        kind = NotificationKind.DRIVER_LOCATION_REQUIRED if reason == "LOCATION_REQUIRED" else NotificationKind.DRIVER_LOCATION_REFRESH_REQUIRED
        notification = self.notifications.create_intent(
            kind=kind, recipient=driver.user, business_entity_type="drivers.DriverLocationReminder",
            business_entity_id=reminder.id, idempotency_key=f"driver-location-refresh:{driver.id}:{reminder.cycle_id}:{attempt}",
            payload={"reason": reason}, correlation_id=correlation_id,
        )
        reminder.reminder_count, reminder.last_reminded_at = attempt, timezone.now()
        reminder.save(update_fields=["reminder_count", "last_reminded_at", "updated_at"])
        return notification

    @transaction.atomic
    def resolve_for_driver(self, driver_id):
        return DriverLocationReminder.objects.select_for_update().filter(driver_id=driver_id, resolved_at__isnull=True, exhausted_at__isnull=True).update(resolved_at=timezone.now())

    @transaction.atomic
    def reconcile(self, reminder_id, *, correlation_id=None):
        reminder = DriverLocationReminder.objects.select_for_update().select_related("driver", "driver__user").get(pk=reminder_id)
        if reminder.resolved_at or reminder.exhausted_at:
            return "NOOP"
        driver = reminder.driver
        if not self.availability.has_active_delivery(driver) and driver.availability_status != "AVAILABLE":
            reminder.resolved_at = timezone.now()
            reminder.save(update_fields=["resolved_at", "updated_at"])
            return "CANCELLED"
        if not self.availability.has_active_delivery(driver) and self.availability.get_matchability(driver).status == DriverMatchability.MATCHABLE:
            reminder.resolved_at = timezone.now()
            reminder.save(update_fields=["resolved_at", "updated_at"])
            return "RESOLVED"
        notification = self.consider(driver.id, correlation_id=correlation_id)
        return "REQUEUED" if notification else "NOOP"
