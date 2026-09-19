import pytest
from django.db import IntegrityError

from apps.identity.application.selectors import (
    get_active_users,
    get_user_by_whatsapp,
    get_user_roles,
    user_has_role,
)
from apps.identity.application.services import (
    assign_role,
    get_or_create_user_by_whatsapp,
    has_role,
    remove_role,
)
from apps.identity.domain.enums import UserRoleType, UserStatus
from apps.identity.infrastructure.models import User, UserRole


@pytest.mark.django_db
def test_create_user_normalizes_whatsapp_phone():
    user = User.objects.create_user(
        whatsapp_phone="6 99 88 77 66",
        first_name="Ada",
    )

    assert user.id
    assert user.whatsapp_phone == "+237699887766"
    assert user.first_name == "Ada"
    assert user.status == UserStatus.ACTIVE


@pytest.mark.django_db
def test_create_user_without_whatsapp_phone():
    user = User.objects.create_user(first_name="Telegram")

    assert user.whatsapp_phone is None
    assert user.first_name == "Telegram"


@pytest.mark.django_db
def test_whatsapp_phone_is_unique_after_normalization():
    User.objects.create_user(whatsapp_phone="+237699887766")

    with pytest.raises(IntegrityError):
        User.objects.create_user(whatsapp_phone="699887766")


@pytest.mark.django_db
def test_get_user_by_whatsapp_finds_normalized_equivalent():
    user = User.objects.create_user(whatsapp_phone="+237699887766")

    found_user = get_user_by_whatsapp("699 887 766")

    assert found_user == user


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        UserStatus.ACTIVE,
        UserStatus.INACTIVE,
        UserStatus.SUSPENDED,
    ],
)
def test_user_status_values(status):
    user = User.objects.create_user(
        whatsapp_phone="+237699887766",
        status=status,
    )

    assert user.status == status


@pytest.mark.django_db
def test_get_active_users_returns_only_active_users():
    active_user = User.objects.create_user(
        whatsapp_phone="+237699887766",
        status=UserStatus.ACTIVE,
    )
    User.objects.create_user(
        whatsapp_phone="+237699887767",
        status=UserStatus.INACTIVE,
    )
    User.objects.create_user(
        whatsapp_phone="+237699887768",
        status=UserStatus.SUSPENDED,
    )

    assert list(get_active_users()) == [active_user]


@pytest.mark.django_db
def test_assign_role_to_user():
    user = User.objects.create_user(whatsapp_phone="+237699887766")

    user_role = assign_role(user, UserRoleType.CLIENT)

    assert user_role.user == user
    assert user_role.role == UserRoleType.CLIENT


@pytest.mark.django_db
def test_user_can_have_multiple_roles():
    user = User.objects.create_user(whatsapp_phone="+237699887766")

    assign_role(user, UserRoleType.CLIENT)
    assign_role(user, UserRoleType.DRIVER)

    assert set(get_user_roles(user).values_list("role", flat=True)) == {
        UserRoleType.CLIENT,
        UserRoleType.DRIVER,
    }


@pytest.mark.django_db
def test_cannot_duplicate_same_role_for_user():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    assign_role(user, UserRoleType.CLIENT)

    with pytest.raises(IntegrityError):
        UserRole.objects.create(user=user, role=UserRoleType.CLIENT)


@pytest.mark.django_db
def test_remove_role_from_user():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    assign_role(user, UserRoleType.CLIENT)

    deleted_count = remove_role(user, UserRoleType.CLIENT)

    assert deleted_count == 1
    assert not has_role(user, UserRoleType.CLIENT)


@pytest.mark.django_db
def test_has_role_helpers():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    assign_role(user, UserRoleType.DRIVER)

    assert has_role(user, UserRoleType.DRIVER)
    assert user_has_role(user, UserRoleType.DRIVER)
    assert not has_role(user, UserRoleType.CLIENT)


@pytest.mark.django_db
def test_get_or_create_user_by_whatsapp_creates_user_if_absent():
    user, created = get_or_create_user_by_whatsapp(
        "699887766",
        first_name="Grace",
    )

    assert created is True
    assert user.whatsapp_phone == "+237699887766"
    assert user.first_name == "Grace"


@pytest.mark.django_db
def test_get_or_create_user_by_whatsapp_returns_existing_user():
    existing_user = User.objects.create_user(
        whatsapp_phone="+237699887766",
        first_name="Existing",
    )

    user, created = get_or_create_user_by_whatsapp(
        "+237 699 887 766",
        first_name="Ignored",
    )

    assert created is False
    assert user == existing_user
    assert user.first_name == "Existing"


@pytest.mark.django_db
def test_equivalent_phone_forms_do_not_create_two_users():
    first_user, first_created = get_or_create_user_by_whatsapp("699887766")
    second_user, second_created = get_or_create_user_by_whatsapp("+237 699-887-766")

    assert first_created is True
    assert second_created is False
    assert first_user == second_user
    assert User.objects.count() == 1
