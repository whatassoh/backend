from apps.stores.domain.enums import StoreMembershipRole, StoreMembershipStatus
from apps.stores.infrastructure.models import Store, StoreMembership

STORE_MANAGEMENT_ROLES = [
    StoreMembershipRole.OWNER,
    StoreMembershipRole.MANAGER,
]


def user_has_store_role(
    user: object,
    store: Store,
    roles: list[StoreMembershipRole],
) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False

    return StoreMembership.objects.filter(
        store=store,
        user=user,
        status=StoreMembershipStatus.ACTIVE,
        role__in=roles,
    ).exists()


def is_store_owner(user: object, store: Store) -> bool:
    return user_has_store_role(user, store, [StoreMembershipRole.OWNER])


def is_store_manager(user: object, store: Store) -> bool:
    return user_has_store_role(user, store, [StoreMembershipRole.MANAGER])


def can_manage_store(user: object, store: Store) -> bool:
    return user_has_store_role(user, store, STORE_MANAGEMENT_ROLES)
