import uuid

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db.models import QuerySet

from apps.stores.domain.enums import StoreStatus
from apps.stores.infrastructure.models import Store, StoreMembership


def get_store_by_id(store_id: uuid.UUID | str) -> Store | None:
    return (
        Store.objects.prefetch_related("memberships__user").filter(id=store_id).first()
    )


def get_user_stores(user: object) -> QuerySet[Store]:
    return Store.objects.filter(memberships__user=user).prefetch_related("memberships")


def get_store_members(store: Store) -> QuerySet[StoreMembership]:
    return store.memberships.select_related("user")


def get_active_stores() -> QuerySet[Store]:
    return Store.objects.filter(status=StoreStatus.ACTIVE).prefetch_related(
        "memberships"
    )


def get_open_stores() -> QuerySet[Store]:
    return get_active_stores().filter(is_open=True)


def get_stores_within_radius(
    point: Point,
    radius_meters: int,
) -> QuerySet[Store]:
    return (
        Store.objects.filter(location__distance_lte=(point, D(m=radius_meters)))
        .annotate(distance=Distance("location", point))
        .order_by("distance")
    )
