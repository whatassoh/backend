from decimal import Decimal

import pytest

from apps.ai.domain.understanding import ExtractedItem, ExtractedUnit
from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.grounding import CatalogResolution, CatalogResolutionReason
from apps.conversations.application.order_building import (
    ItemReference,
    ItemTargetStatus,
    OrderBuildingService,
)
from apps.identity.infrastructure.models import User
from apps.orders.application.services import add_order_item, create_order_draft
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus


def product(name):
    return create_product(name=name, category=create_category(name=f"cat-{name}"))


def resolution(name, item):
    return CatalogResolution(
        status=OrderItemResolutionStatus.MATCHED,
        requested_name=name,
        product_id=item.id,
        variant_id=None,
        candidates=(),
        reason=CatalogResolutionReason.EXACT_MATCH,
    )


@pytest.mark.django_db
def test_add_merges_an_identical_catalog_line():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    mayor = product("Mayor")
    service = OrderBuildingService()
    item = ExtractedItem(
        requested_name="Mayor", quantity=Decimal("2"), unit=ExtractedUnit.BOTTLE
    )

    service.add(order, item, resolution("Mayor", mayor))
    service.add(order, item, resolution("Mayor", mayor))

    summary = service.summary(order)
    assert len(summary) == 1
    assert summary[0]["quantity"] == "4.000"


@pytest.mark.django_db
def test_targeting_refuses_ambiguous_name_and_accepts_display_index():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    first = add_order_item(order=order, requested_name="riz", quantity=1, unit="KG")
    add_order_item(order=order, requested_name="riz", quantity=2, unit="KG")
    service = OrderBuildingService()

    assert (
        service.target(order, ItemReference(requested_name="riz")).status
        == ItemTargetStatus.AMBIGUOUS
    )
    decision = service.target(order, ItemReference(display_index=1))
    assert decision.status == ItemTargetStatus.FOUND
    assert decision.item.id == first.id


@pytest.mark.django_db
def test_update_remove_and_replace_use_existing_order_services():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    order = create_order_draft(customer=user)
    rice, pasta = product("Riz"), product("Pates")
    item = add_order_item(
        order=order,
        requested_name="riz",
        quantity=5,
        unit="KG",
        product=rice,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    service = OrderBuildingService()

    updated = service.update(item, quantity=Decimal("10"), unit=ExtractedUnit.BAG)
    replaced = service.replace(
        updated,
        ExtractedItem(
            requested_name="pâtes", quantity=Decimal("10"), unit=ExtractedUnit.BAG
        ),
        resolution("pâtes", pasta),
    )
    service.remove(replaced)

    assert updated.quantity == Decimal("10")
    assert updated.unit == "BAG"
    assert replaced.product_id == pasta.id
    assert service.summary(order) == ()


@pytest.mark.django_db
@pytest.mark.parametrize("spoken", ["arachides", "ARACHIDE", " arachide ", "arachidé"])
def test_target_normalizes_plural_case_spacing_and_accents(spoken):
    user = User.objects.create_user(whatsapp_phone=f"+237611{User.objects.count():06d}")
    order = create_order_draft(customer=user)
    item = add_order_item(order=order, requested_name="arachide", quantity=1, unit="KG")
    decision = OrderBuildingService().target(
        order, ItemReference(requested_name=spoken)
    )
    assert decision.status == ItemTargetStatus.FOUND
    assert decision.item.id == item.id


@pytest.mark.django_db
def test_target_uses_product_alias_and_never_crosses_order_scope():
    from apps.catalog.application.services import add_product_alias

    user = User.objects.create_user(whatsapp_phone="+237699001001")
    other = create_order_draft(customer=user)
    other.status = OrderStatus.CONFIRMED
    other.save(update_fields=["status", "updated_at"])
    order = create_order_draft(customer=user)
    rice = product("Riz parfumé")
    add_product_alias(product=rice, alias="riz long")
    expected = add_order_item(
        order=order,
        requested_name="riz",
        quantity=1,
        unit="KG",
        product=rice,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    decision = OrderBuildingService().target(
        order, ItemReference(requested_name="riz long")
    )
    assert decision.status == ItemTargetStatus.FOUND
    assert decision.item.id == expected.id


@pytest.mark.django_db
def test_target_returns_ambiguous_without_selecting_any_item():
    user = User.objects.create_user(whatsapp_phone="+237699001002")
    order = create_order_draft(customer=user)
    add_order_item(order=order, requested_name="arachide", quantity=1, unit="KG")
    add_order_item(order=order, requested_name="arachide", quantity=2, unit="KG")
    decision = OrderBuildingService().target(
        order, ItemReference(requested_name="arachide")
    )
    assert decision.status == ItemTargetStatus.AMBIGUOUS
    assert decision.item is None
    assert len(decision.candidates) == 2
