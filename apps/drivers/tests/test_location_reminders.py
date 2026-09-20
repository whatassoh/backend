import pytest

from apps.drivers.application.location_reminders import DriverLocationReminderService
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    set_driver_available,
    set_driver_offline,
)
from apps.drivers.infrastructure.models import DriverLocationReminder
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel


def driver(number):
    user = User.objects.create_user(whatsapp_phone=f"+237655{number:06d}")
    profile = activate_driver(create_driver_profile(user))
    set_driver_available(profile)
    create_channel_identity(user=user, channel=MessagingChannel.TELEGRAM, external_user_id=f"reminder-{number}")
    return profile


@pytest.mark.django_db
def test_available_driver_without_location_gets_one_bounded_reminder(monkeypatch):
    profile = driver(1)
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", lambda *args: None)
    first = DriverLocationReminderService().consider(profile.id)
    assert first is not None
    assert DriverLocationReminder.objects.get(driver=profile).reason == "LOCATION_REQUIRED"
    assert DriverLocationReminderService().consider(profile.id) is None


@pytest.mark.django_db
def test_offline_driver_gets_no_reminder(monkeypatch):
    profile = driver(2)
    set_driver_offline(profile)
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", lambda *args: None)
    assert DriverLocationReminderService().consider(profile.id) is None
