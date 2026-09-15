from apps.stores.application.permissions import can_manage_store
from apps.stores.infrastructure.models import Store


def can_manage_store_catalog(user: object, store: Store) -> bool:
    return can_manage_store(user, store)
