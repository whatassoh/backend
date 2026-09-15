from typing import Any

from django.contrib.gis.geos import Point
from django.db import transaction

from apps.stores.domain.enums import (
    StoreMembershipRole,
    StoreMembershipStatus,
    StoreStatus,
)
from apps.stores.infrastructure.models import Store, StoreMembership


@transaction.atomic
def create_store(
    *,
    name: str,
    location: Point,
    city: str,
    created_by: Any | None = None,
    owner_role: StoreMembershipRole | str = StoreMembershipRole.OWNER,
    **store_fields: Any,
) -> Store:
    store = Store.objects.create(
        name=name,
        location=location,
        city=city,
        **store_fields,
    )

    if created_by is not None:
        add_store_member(store, created_by, owner_role)

    return store


def add_store_member(
    store: Store,
    user: Any,
    role: StoreMembershipRole | str,
) -> StoreMembership:
    membership, _ = StoreMembership.objects.get_or_create(
        store=store,
        user=user,
        defaults={
            "role": role,
            "status": StoreMembershipStatus.ACTIVE,
        },
    )

    return membership


def remove_store_member(store: Store, user: Any) -> int:
    deleted_count, _ = StoreMembership.objects.filter(
        store=store,
        user=user,
    ).delete()

    return deleted_count


def change_store_member_role(
    store: Store,
    user: Any,
    role: StoreMembershipRole | str,
) -> StoreMembership:
    membership = StoreMembership.objects.get(store=store, user=user)
    membership.role = role
    membership.save(update_fields=["role", "updated_at"])

    return membership


def update_store_location(store: Store, point: Point) -> Store:
    store.location = point
    store.save(update_fields=["location", "updated_at"])

    return store


def set_store_open_state(store: Store, is_open: bool) -> Store:
    store.is_open = is_open
    store.save(update_fields=["is_open", "updated_at"])

    return store


def activate_store(store: Store) -> Store:
    store.status = StoreStatus.ACTIVE
    store.save(update_fields=["status", "updated_at"])

    return store


def suspend_store(store: Store) -> Store:
    store.status = StoreStatus.SUSPENDED
    store.save(update_fields=["status", "updated_at"])

    return store
