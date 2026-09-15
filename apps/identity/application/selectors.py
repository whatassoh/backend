from django.db.models import QuerySet

from apps.identity.domain.enums import UserRoleType, UserStatus
from apps.identity.domain.services import normalize_whatsapp_phone
from apps.identity.infrastructure.models import User, UserRole


def get_user_by_whatsapp(phone: str) -> User | None:
    whatsapp_phone = normalize_whatsapp_phone(phone)

    return (
        User.objects.prefetch_related("roles")
        .filter(whatsapp_phone=whatsapp_phone)
        .first()
    )


def get_user_roles(user: User) -> QuerySet[UserRole]:
    return user.roles.all()


def get_active_users() -> QuerySet[User]:
    return User.objects.filter(status=UserStatus.ACTIVE).prefetch_related("roles")


def user_has_role(user: User, role: UserRoleType | str) -> bool:
    return user.roles.filter(role=role).exists()
