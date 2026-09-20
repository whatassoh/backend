from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone

from apps.drivers.application.availability import (
    DriverAvailabilityService,
    DriverMatchability,
)
from apps.drivers.application.services import activate_driver, create_driver_profile
from apps.drivers.domain.enums import DriverAvailability
from apps.identity.infrastructure.models import User


def user(phone):
    return User.objects.create_user(whatsapp_phone=phone)


def driver(phone="+237680000001"):
    profile = create_driver_profile(user(phone))
    return activate_driver(profile)


@pytest.mark.django_db
def test_offline_is_not_matchable_and_mutations_are_idempotent():
    profile = driver()
    service = DriverAvailabilityService()
    assert service.get_matchability(profile).status == DriverMatchability.NOT_AVAILABLE
    assert service.set_offline(profile, profile.user).id == profile.id
    service.set_available(profile, profile.user)
    again = service.set_available(profile, profile.user)
    assert again.availability_status == DriverAvailability.AVAILABLE
    assert service.get_matchability(again).status == DriverMatchability.LOCATION_REQUIRED


@pytest.mark.django_db
@override_settings(DRIVER_LOCATION_FRESHNESS_SECONDS=120)
def test_freshness_is_derived_with_exact_boundary_and_future_controlled():
    profile = driver()
    service = DriverAvailabilityService()
    now = timezone.now()
    profile.last_location_at = now - timedelta(seconds=120)
    profile.save(update_fields=["last_location_at", "updated_at"])
    service.set_available(profile, profile.user)
    profile.refresh_from_db()
    assert service.get_matchability(profile, now=now).status == DriverMatchability.MATCHABLE
    profile.last_location_at = now - timedelta(seconds=121)
    profile.save(update_fields=["last_location_at", "updated_at"])
    assert service.get_matchability(profile, now=now).status == DriverMatchability.LOCATION_REFRESH_REQUIRED
    profile.last_location_at = now + timedelta(seconds=1)
    profile.save(update_fields=["last_location_at", "updated_at"])
    assert service.get_matchability(profile, now=now).status == DriverMatchability.LOCATION_REFRESH_REQUIRED
    with pytest.raises(ValueError):
        service.get_matchability(profile, now=timezone.datetime(2026, 1, 1))


@pytest.mark.django_db
def test_only_owner_can_change_availability_and_no_assignment_or_offer_is_created():
    profile = driver()
    intruder = user("+237680000002")
    with pytest.raises(PermissionDenied):
        DriverAvailabilityService().set_available(profile, intruder)
    assert not DriverAvailabilityService.has_active_delivery(profile)


@pytest.mark.django_db
def test_offline_does_not_touch_the_future_active_delivery_boundary():
    profile = driver()
    service = DriverAvailabilityService()
    service.set_available(profile, profile.user)
    service.set_offline(profile, profile.user)
    profile.refresh_from_db()
    assert profile.availability_status == DriverAvailability.OFFLINE
