import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.drivers.application.selectors import (
    get_active_drivers,
    get_available_drivers,
    get_driver_by_user,
)
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    deactivate_driver,
    set_driver_available,
    set_driver_busy,
    set_driver_offline,
    suspend_driver,
)
from apps.drivers.domain.enums import (
    DriverAvailability,
    DriverStatus,
    VehicleType,
)
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.application.services import has_role
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


@pytest.mark.django_db
def test_create_driver_profile():
    user = create_user()

    driver = create_driver_profile(
        user,
        vehicle_type=VehicleType.MOTORBIKE,
        vehicle_label="Moto 125cc",
    )

    assert driver.id
    assert driver.user == user
    assert driver.status == DriverStatus.INACTIVE
    assert driver.availability_status == DriverAvailability.OFFLINE
    assert driver.vehicle_type == VehicleType.MOTORBIKE


@pytest.mark.django_db
def test_cannot_create_two_driver_profiles_for_same_user():
    user = create_user()
    create_driver_profile(user)

    with pytest.raises(IntegrityError):
        create_driver_profile(user)


@pytest.mark.django_db
def test_create_driver_profile_assigns_driver_role():
    user = create_user()

    create_driver_profile(user)

    assert has_role(user, UserRoleType.DRIVER)


@pytest.mark.django_db
def test_service_radius_must_be_positive_when_provided():
    user = create_user()

    with pytest.raises(IntegrityError):
        create_driver_profile(user, service_radius_meters=0)


@pytest.mark.django_db
def test_active_driver_can_be_available():
    driver = create_driver_profile(create_user())
    activate_driver(driver)

    set_driver_available(driver)
    driver.refresh_from_db()

    assert driver.status == DriverStatus.ACTIVE
    assert driver.availability_status == DriverAvailability.AVAILABLE


@pytest.mark.django_db
def test_active_driver_can_be_busy():
    driver = create_driver_profile(create_user())
    activate_driver(driver)

    set_driver_busy(driver)
    driver.refresh_from_db()

    assert driver.status == DriverStatus.ACTIVE
    assert driver.availability_status == DriverAvailability.BUSY


@pytest.mark.django_db
def test_active_driver_can_be_offline():
    driver = create_driver_profile(create_user())
    activate_driver(driver)
    set_driver_available(driver)

    set_driver_offline(driver)
    driver.refresh_from_db()

    assert driver.availability_status == DriverAvailability.OFFLINE


@pytest.mark.django_db
def test_suspended_driver_cannot_be_available():
    driver = create_driver_profile(create_user(), status=DriverStatus.SUSPENDED)

    with pytest.raises(ValidationError):
        set_driver_available(driver)


@pytest.mark.django_db
def test_inactive_driver_cannot_be_available():
    driver = create_driver_profile(create_user(), status=DriverStatus.INACTIVE)

    with pytest.raises(ValidationError):
        set_driver_available(driver)


@pytest.mark.django_db
def test_suspended_available_state_is_rejected():
    user = create_user()

    with pytest.raises(ValidationError):
        DriverProfile.objects.create(
            user=user,
            status=DriverStatus.SUSPENDED,
            availability_status=DriverAvailability.AVAILABLE,
        )


@pytest.mark.django_db
def test_suspend_driver_sets_status_and_offline():
    driver = create_driver_profile(create_user())
    activate_driver(driver)
    set_driver_available(driver)

    suspend_driver(driver)
    driver.refresh_from_db()

    assert driver.status == DriverStatus.SUSPENDED
    assert driver.availability_status == DriverAvailability.OFFLINE


@pytest.mark.django_db
def test_deactivate_driver_sets_status_and_offline():
    driver = create_driver_profile(create_user())
    activate_driver(driver)
    set_driver_available(driver)

    deactivate_driver(driver)
    driver.refresh_from_db()

    assert driver.status == DriverStatus.INACTIVE
    assert driver.availability_status == DriverAvailability.OFFLINE


@pytest.mark.django_db
def test_activate_driver_sets_active_and_keeps_offline_default():
    driver = create_driver_profile(create_user())

    activate_driver(driver)
    driver.refresh_from_db()

    assert driver.status == DriverStatus.ACTIVE
    assert driver.availability_status == DriverAvailability.OFFLINE


@pytest.mark.django_db
def test_get_driver_by_user():
    user = create_user()
    driver = create_driver_profile(user)

    assert get_driver_by_user(user) == driver


@pytest.mark.django_db
def test_driver_selectors_filter_operational_state():
    available_driver = create_driver_profile(create_user("+237699887766"))
    activate_driver(available_driver)
    set_driver_available(available_driver)

    busy_driver = create_driver_profile(create_user("+237699887767"))
    activate_driver(busy_driver)
    set_driver_busy(busy_driver)

    offline_driver = create_driver_profile(create_user("+237699887768"))
    activate_driver(offline_driver)

    suspended_driver = create_driver_profile(create_user("+237699887769"))
    suspend_driver(suspended_driver)

    assert set(get_active_drivers()) == {
        available_driver,
        busy_driver,
        offline_driver,
    }
    assert list(get_available_drivers()) == [available_driver]
