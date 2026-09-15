from typing import Any

from django.db import IntegrityError, transaction

from apps.identity.domain.enums import UserRoleType, UserStatus
from apps.identity.domain.services import normalize_whatsapp_phone
from apps.identity.infrastructure.models import User, UserRole


@transaction.atomic
def get_or_create_user_by_whatsapp(
    phone: str,
    **defaults: Any,
) -> tuple[User, bool]:
    whatsapp_phone = normalize_whatsapp_phone(phone)

    create_defaults = {
        "status": UserStatus.ACTIVE,
        **defaults,
    }

    try:
        return User.objects.get_or_create(
            whatsapp_phone=whatsapp_phone,
            defaults=create_defaults,
        )
    except IntegrityError:
        transaction.set_rollback(False)
        return User.objects.get(whatsapp_phone=whatsapp_phone), False


def assign_role(user: User, role: UserRoleType | str) -> UserRole:
    user_role, _ = UserRole.objects.get_or_create(
        user=user,
        role=role,
    )

    return user_role


def remove_role(user: User, role: UserRoleType | str) -> int:
    deleted_count, _ = UserRole.objects.filter(
        user=user,
        role=role,
    ).delete()

    return deleted_count


def has_role(user: User, role: UserRoleType | str) -> bool:
    return UserRole.objects.filter(
        user=user,
        role=role,
    ).exists()
