import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError

from apps.identity.infrastructure.models import User
from apps.stores.application.selectors import (
    get_open_stores,
    get_store_members,
    get_stores_within_radius,
    get_user_stores,
)
from apps.stores.application.services import (
    activate_store,
    add_store_member,
    change_store_member_role,
    create_store,
    remove_store_member,
    set_store_open_state,
    suspend_store,
    update_store_location,
)
from apps.stores.domain.enums import (
    StoreMembershipRole,
    StoreMembershipStatus,
    StoreStatus,
)
from apps.stores.domain.rules import DEFAULT_SERVICE_RADIUS_METERS
from apps.stores.domain.services import make_store_point
from apps.stores.infrastructure.models import Store, StoreMembership

BONAMOUSSADI = Point(9.7357, 4.0907, srid=4326)
AKWA = Point(9.7043, 4.0511, srid=4326)
YAOUNDE = Point(11.5021, 3.8480, srid=4326)


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


@pytest.mark.django_db
def test_create_store_with_valid_point():
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    assert store.id
    assert store.location == BONAMOUSSADI
    assert store.status == StoreStatus.ACTIVE
    assert store.service_radius_meters == DEFAULT_SERVICE_RADIUS_METERS


@pytest.mark.django_db
def test_store_rejects_non_positive_service_radius():
    with pytest.raises(IntegrityError):
        Store.objects.create(
            name="Invalid radius",
            location=BONAMOUSSADI,
            city="Douala",
            service_radius_meters=0,
        )


@pytest.mark.django_db
def test_suspend_and_activate_store():
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    suspend_store(store)
    store.refresh_from_db()

    assert store.status == StoreStatus.SUSPENDED

    activate_store(store)
    store.refresh_from_db()

    assert store.status == StoreStatus.ACTIVE


@pytest.mark.django_db
def test_set_store_open_state():
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    set_store_open_state(store, False)
    store.refresh_from_db()

    assert store.is_open is False
    assert list(get_open_stores()) == []


@pytest.mark.django_db
def test_update_store_location():
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    update_store_location(store, AKWA)
    store.refresh_from_db()

    assert store.location == AKWA


@pytest.mark.django_db
def test_add_owner_membership():
    user = create_user()
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    membership = add_store_member(store, user, StoreMembershipRole.OWNER)

    assert membership.store == store
    assert membership.user == user
    assert membership.role == StoreMembershipRole.OWNER
    assert membership.status == StoreMembershipStatus.ACTIVE


@pytest.mark.django_db
def test_create_store_can_attach_creator_as_owner():
    user = create_user()

    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
        created_by=user,
    )

    assert StoreMembership.objects.get(store=store, user=user).role == (
        StoreMembershipRole.OWNER
    )


@pytest.mark.django_db
def test_add_multiple_members_to_store():
    owner = create_user("+237699887766")
    agent = create_user("+237699887767")
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    add_store_member(store, owner, StoreMembershipRole.OWNER)
    add_store_member(store, agent, StoreMembershipRole.AGENT)

    assert set(get_store_members(store).values_list("role", flat=True)) == {
        StoreMembershipRole.OWNER,
        StoreMembershipRole.AGENT,
    }


@pytest.mark.django_db
def test_same_user_can_belong_to_multiple_stores():
    user = create_user()
    first_store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )
    second_store = create_store(
        name="Boutique Akwa",
        location=AKWA,
        city="Douala",
    )

    add_store_member(first_store, user, StoreMembershipRole.OWNER)
    add_store_member(second_store, user, StoreMembershipRole.MANAGER)

    assert set(get_user_stores(user)) == {first_store, second_store}


@pytest.mark.django_db
def test_cannot_duplicate_store_user_membership():
    user = create_user()
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )
    add_store_member(store, user, StoreMembershipRole.OWNER)

    with pytest.raises(IntegrityError):
        StoreMembership.objects.create(
            store=store,
            user=user,
            role=StoreMembershipRole.AGENT,
        )


@pytest.mark.django_db
def test_change_store_member_role():
    user = create_user()
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )
    add_store_member(store, user, StoreMembershipRole.AGENT)

    membership = change_store_member_role(store, user, StoreMembershipRole.MANAGER)

    assert membership.role == StoreMembershipRole.MANAGER


@pytest.mark.django_db
def test_remove_membership_does_not_delete_user():
    user = create_user()
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )
    add_store_member(store, user, StoreMembershipRole.AGENT)

    deleted_count = remove_store_member(store, user)

    assert deleted_count == 1
    assert StoreMembership.objects.count() == 0
    assert User.objects.filter(id=user.id).exists()


@pytest.mark.django_db
def test_make_store_point_uses_longitude_latitude_order():
    point = make_store_point(longitude=9.7357, latitude=4.0907)

    assert point.x == 9.7357
    assert point.y == 4.0907
    assert point.srid == 4326


@pytest.mark.django_db
def test_geo_distance_between_point_and_store():
    store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )

    result = get_stores_within_radius(AKWA, 10_000).get(id=store.id)

    assert result.distance.m > 0


@pytest.mark.django_db
def test_geo_search_within_radius_includes_near_store_and_excludes_far_store():
    near_store = create_store(
        name="Boutique Bonamoussadi",
        location=BONAMOUSSADI,
        city="Douala",
    )
    far_store = create_store(
        name="Boutique Yaounde",
        location=YAOUNDE,
        city="Yaounde",
    )

    stores = list(get_stores_within_radius(AKWA, 10_000))

    assert near_store in stores
    assert far_store not in stores
