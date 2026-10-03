from datetime import timedelta
from uuid import UUID

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.catalog.application.services import (
    create_category,
    create_product,
    create_product_variant,
)
from apps.catalog.domain.enums import ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import add_order_item, create_order_draft
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.stores.application.offer_actions import (
    StoreOfferActionResult,
    StoreOfferActionService,
    decode_store_offer_action,
    encode_store_offer_action,
    present_offer,
)
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole, StoreOfferStatus
from apps.stores.infrastructure.models import StoreOffer


def make_offer(*, role=StoreMembershipRole.OWNER, expired=False):
    customer = User.objects.create_user(
        whatsapp_phone=f"+23767{User.objects.count():07d}"
    )
    actor = User.objects.create_user(
        whatsapp_phone=f"+23768{User.objects.count():07d}"
    )
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.STORE_OFFERED
    order.save(update_fields=["status", "updated_at"])
    store = create_store(
        name=f"Offer actions {order.id}",
        city="Douala",
        location=Point(9.7, 4.05, srid=4326),
    )
    add_store_member(store, actor, role)
    offer = StoreOffer.objects.create(
        order=order,
        store=store,
        rank=1,
        strategy_version="test",
        expires_at=timezone.now() + (-timedelta(seconds=1) if expired else timedelta(minutes=5)),
    )
    return actor, order, offer


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role",
    [StoreMembershipRole.OWNER, StoreMembershipRole.MANAGER, StoreMembershipRole.AGENT],
)
def test_authorized_operational_roles_accept(role, monkeypatch):
    actor, order, offer = make_offer(role=role)
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", lambda *_: None)
    result = StoreOfferActionService().handle(
        actor=actor, offer_id=offer.id, action_id="store_offer.accept"
    )
    offer.refresh_from_db()
    order.refresh_from_db()
    assert result == StoreOfferActionResult.ACCEPTED
    assert offer.status == StoreOfferStatus.ACCEPTED
    assert order.status == OrderStatus.STORE_ACCEPTED


@pytest.mark.django_db
def test_unauthorized_unknown_expired_and_already_handled_are_safe(monkeypatch):
    actor, _, offer = make_offer(expired=True)
    stranger = User.objects.create_user(whatsapp_phone="+237699991111")
    service = StoreOfferActionService()
    assert service.handle(actor=stranger, offer_id=offer.id, action_id="store_offer.accept") == StoreOfferActionResult.NOT_AUTHORIZED
    assert service.handle(actor=actor, offer_id=offer.id, action_id="store_offer.accept") == StoreOfferActionResult.NOT_AVAILABLE
    assert service.handle(actor=actor, offer_id="00000000-0000-0000-0000-000000000000", action_id="store_offer.accept") == StoreOfferActionResult.NOT_AUTHORIZED

    offer.expires_at = timezone.now() + timedelta(minutes=5)
    offer.status = StoreOfferStatus.REJECTED
    offer.save(update_fields=["expires_at", "status", "updated_at"])
    assert service.handle(actor=actor, offer_id=offer.id, action_id="store_offer.reject") == StoreOfferActionResult.NOT_AVAILABLE
    offer.status = StoreOfferStatus.ACCEPTED
    offer.save(update_fields=["status", "updated_at"])
    assert service.handle(actor=actor, offer_id=offer.id, action_id="store_offer.accept") == StoreOfferActionResult.ACCEPTED


def test_callback_encoding_is_deterministic_and_rejects_tampering():
    raw = "89157d6a-5904-45bb-8e04-d36b5996c641"
    encoded = encode_store_offer_action("store_offer.reject", raw)
    assert encoded == f"store_offer.reject:{raw}"
    assert decode_store_offer_action(encoded) == ("store_offer.reject", UUID(raw))
    assert decode_store_offer_action("store_offer.accept:not-a-uuid") is None
    assert decode_store_offer_action("store_offer.accept") is None


@pytest.mark.django_db
def test_offer_presentation_prefers_catalog_identity_variant_and_readable_quantity():
    actor, order, offer = make_offer()
    order.status = OrderStatus.DRAFT
    order.save(update_fields=["status", "updated_at"])
    category = create_category(name=f"Presentation {order.id}")
    product = create_product(name="Huile Mayor", category=category)
    variant = create_product_variant(product=product, name="1 L")
    add_order_item(
        order=order,
        requested_name="huile",
        quantity="2.000",
        unit=ProductUnit.UNIT,
        product=product,
        product_variant=variant,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    add_order_item(
        order=order,
        requested_name="Produit historique",
        quantity="1.500",
        unit=ProductUnit.UNIT,
    )
    assert present_offer(offer).items == (
        "2 × Huile Mayor — 1 L",
        "1.5 × Produit historique",
    )
