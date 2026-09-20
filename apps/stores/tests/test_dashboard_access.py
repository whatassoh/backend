from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.stores.application.dashboard_access import SellerDashboardAccessService
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole
from apps.stores.infrastructure.models import SellerDashboardAccessGrant
from apps.identity.infrastructure.models import User


def seller(with_membership=True):
    user = User.objects.create_user(whatsapp_phone=f"+237677{User.objects.count():06d}")
    if with_membership:
        store = create_store(name="Dashboard", city="Douala", location=Point(9.7, 4.05, srid=4326))
        add_store_member(store, user, StoreMembershipRole.OWNER)
    return user


@pytest.mark.django_db
def test_opaque_grant_is_hashed_single_use_and_revokes_previous():
    actor = seller()
    service = SellerDashboardAccessService()
    first = service.issue_access_link(actor=actor)
    second = service.issue_access_link(actor=actor)
    assert first.status == second.status == "ISSUED"
    assert first.url.startswith("http") and "#token=" in first.url
    grants = list(SellerDashboardAccessGrant.objects.filter(user=actor).order_by("created_at"))
    assert len(grants) == 2
    assert grants[0].revoked_at is not None
    raw_token = second.url.split("#token=", 1)[1]
    assert raw_token != grants[-1].token_hash
    assert service.consume(raw_token=raw_token).status == "CONSUMED"
    assert service.consume(raw_token=raw_token).status == "USED"


@pytest.mark.django_db
def test_grant_denies_non_seller_and_expired_or_unknown_tokens():
    service = SellerDashboardAccessService()
    assert service.issue_access_link(actor=seller(with_membership=False)).status == "FORBIDDEN"
    actor = seller()
    issued = service.issue_access_link(actor=actor)
    raw_token = issued.url.split("#token=", 1)[1]
    grant = SellerDashboardAccessGrant.objects.get(user=actor, consumed_at__isnull=True)
    grant.expires_at = timezone.now() - timedelta(seconds=1)
    grant.save(update_fields=["expires_at", "updated_at"])
    assert service.consume(raw_token=raw_token).status == "EXPIRED"
    assert service.consume(raw_token="x" * 40).status == "INVALID"
