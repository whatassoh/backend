from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError

from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.enums import ProductUnit
from apps.catalog.infrastructure.models import ProductVariant
from apps.identity.infrastructure.models import User
from apps.orders.application.selectors import (
    get_customer_orders,
    get_order_by_reference,
    get_order_items,
    get_order_with_items,
)
from apps.orders.application.services import (
    add_order_item,
    assign_driver,
    cancel_order,
    confirm_order,
    create_order_draft,
    fail_order,
    mark_awaiting_location,
    mark_delivered,
    mark_draft,
    mark_in_delivery,
    mark_picked_up,
    mark_preparing,
    mark_ready_for_pickup,
    mark_store_accepted,
    mark_store_offered,
    remove_order_item,
    retry_failed_order,
    set_delivery_instructions,
    set_delivery_location,
    start_driver_matching,
    start_store_matching,
    update_order_item,
)
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderFailureCode,
    OrderItemResolutionStatus,
    OrderStatus,
)
from apps.orders.domain.exceptions import (
    InvalidCancellation,
    InvalidFailureCode,
    InvalidOrderTransition,
    OrderNotConfirmable,
    OrderNotEditable,
    OrderNotRetryable,
)
from apps.orders.domain.services import generate_order_reference
from apps.orders.infrastructure.models import Order, OrderItem

BONAMOUSSADI = Point(9.7357, 4.0907, srid=4326)
AKWA = Point(9.7043, 4.0511, srid=4326)


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def create_catalog_product(name: str):
    category = create_category(name=f"Category {name}")
    return create_product(name=name, category=category)


def add_matched_rice(order):
    product = create_catalog_product(f"Riz {order.id}")
    return add_order_item(
        order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG,
        product=product, resolution_status=OrderItemResolutionStatus.MATCHED,
    )


@pytest.mark.django_db
def test_create_order_draft_defaults():
    customer = create_user()

    order = create_order_draft(customer=customer)

    assert order.id
    assert order.customer == customer
    assert order.status == OrderStatus.DRAFT
    assert order.reference.startswith("ORD-")
    assert order.currency == "XAF"
    assert order.delivery_location is None


@pytest.mark.django_db
def test_order_reference_format_and_multiple_generation():
    customer = create_user()

    first = create_order_draft(customer=customer)
    second = create_order_draft(customer=customer)

    assert first.reference != second.reference
    assert first.reference[0:4] == "ORD-"
    assert first.reference[10] == "-"
    assert len(first.reference) == 15


def test_generate_order_reference_avoids_confusing_characters():
    reference = generate_order_reference()
    suffix = reference.rsplit("-", maxsplit=1)[1]

    assert "O" not in suffix
    assert "0" not in suffix
    assert "I" not in suffix
    assert "1" not in suffix


@pytest.mark.django_db
def test_order_reference_is_unique_in_database():
    customer = create_user()
    reference = "ORD-260915-A7K2"
    Order.objects.create(reference=reference, customer=customer)

    with pytest.raises(IntegrityError):
        Order.objects.create(reference=reference, customer=customer)


@pytest.mark.django_db
def test_create_order_draft_retries_reference_collision(monkeypatch):
    customer = create_user()
    existing_reference = "ORD-260915-A7K2"
    Order.objects.create(reference=existing_reference, customer=customer)
    references = iter([existing_reference, "ORD-260915-B8L3"])

    monkeypatch.setattr(
        "apps.orders.application.services.generate_order_reference",
        lambda: next(references),
    )

    order = create_order_draft(customer=customer)

    assert order.reference == "ORD-260915-B8L3"


@pytest.mark.django_db
def test_order_delivery_location_can_store_point():
    customer = create_user()
    order = create_order_draft(customer=customer)

    set_delivery_location(
        order=order,
        point=BONAMOUSSADI,
        address_label="Bonamoussadi",
    )
    order.refresh_from_db()

    assert order.delivery_location.x == pytest.approx(9.7357)
    assert order.delivery_location.y == pytest.approx(4.0907)
    assert order.delivery_address_label == "Bonamoussadi"


@pytest.mark.django_db
def test_unmatched_order_item_without_catalog_product_is_valid():
    customer = create_user()
    order = create_order_draft(customer=customer)

    item = add_order_item(
        order=order,
        requested_name="riz parfumé",
        quantity=Decimal("0.5"),
        unit=ProductUnit.KG,
        resolution_status=OrderItemResolutionStatus.UNMATCHED,
    )

    assert item.requested_name == "riz parfumé"
    assert item.quantity == Decimal("0.5")
    assert item.product is None
    assert item.product_variant is None


@pytest.mark.django_db
def test_matched_order_item_with_product_is_valid():
    customer = create_user()
    product = create_catalog_product("Huile Mayor")
    order = create_order_draft(customer=customer)

    item = add_order_item(
        order=order,
        requested_name="Mayor",
        quantity=2,
        unit=ProductUnit.BOTTLE,
        product=product,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )

    assert item.product == product
    assert item.product_variant is None


@pytest.mark.django_db
def test_matched_order_item_with_matching_variant_is_valid():
    customer = create_user()
    product = create_catalog_product("Huile Mayor")
    variant = ProductVariant.objects.create(product=product, name="1 L")
    order = create_order_draft(customer=customer)

    item = add_order_item(
        order=order,
        requested_name="Mayor 1L",
        quantity=2,
        unit=ProductUnit.BOTTLE,
        product=product,
        product_variant=variant,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )

    assert item.product_variant == variant


@pytest.mark.django_db
def test_order_item_rejects_variant_from_another_product():
    customer = create_user()
    rice = create_catalog_product("Riz")
    mayor = create_catalog_product("Huile Mayor")
    mayor_variant = ProductVariant.objects.create(product=mayor, name="1 L")
    order = create_order_draft(customer=customer)

    with pytest.raises(ValidationError):
        add_order_item(
            order=order,
            requested_name="Mayor 1L",
            quantity=2,
            unit=ProductUnit.BOTTLE,
            product=rice,
            product_variant=mayor_variant,
            resolution_status=OrderItemResolutionStatus.MATCHED,
        )


@pytest.mark.django_db
def test_resolution_status_invariants():
    customer = create_user()
    product = create_catalog_product("Huile Mayor")
    order = create_order_draft(customer=customer)

    with pytest.raises(ValidationError):
        add_order_item(
            order=order,
            requested_name="Mayor",
            quantity=1,
            unit=ProductUnit.BOTTLE,
            resolution_status=OrderItemResolutionStatus.MATCHED,
        )

    with pytest.raises(ValidationError):
        add_order_item(
            order=order,
            requested_name="Mayor",
            quantity=1,
            unit=ProductUnit.BOTTLE,
            product=product,
            resolution_status=OrderItemResolutionStatus.UNMATCHED,
        )

    item = add_order_item(
        order=order,
        requested_name="Mayor ou autre huile",
        quantity=1,
        unit=ProductUnit.BOTTLE,
        resolution_status=OrderItemResolutionStatus.AMBIGUOUS,
    )

    assert item.resolution_status == OrderItemResolutionStatus.AMBIGUOUS


@pytest.mark.django_db
def test_order_item_requires_positive_quantity_and_requested_name():
    customer = create_user()
    order = create_order_draft(customer=customer)

    with pytest.raises(ValidationError):
        add_order_item(
            order=order,
            requested_name="",
            quantity=1,
            unit=ProductUnit.UNIT,
            resolution_status=OrderItemResolutionStatus.UNMATCHED,
        )

    with pytest.raises(ValidationError):
        add_order_item(
            order=order,
            requested_name="Riz",
            quantity=0,
            unit=ProductUnit.KG,
            resolution_status=OrderItemResolutionStatus.UNMATCHED,
        )


@pytest.mark.django_db
def test_multiple_orders_and_items_selectors():
    customer = create_user()
    first_order = create_order_draft(customer=customer)
    second_order = create_order_draft(customer=customer)
    first_item = add_order_item(
        order=first_order,
        requested_name="Riz",
        quantity=5,
        unit=ProductUnit.KG,
    )
    second_item = add_order_item(
        order=first_order,
        requested_name="Mayor",
        quantity=2,
        unit=ProductUnit.BOTTLE,
    )

    assert set(get_customer_orders(customer)) == {first_order, second_order}
    assert list(get_order_items(first_order)) == [first_item, second_item]
    assert get_order_by_reference(first_order.reference) == first_order
    assert get_order_with_items(first_order.id) == first_order


@pytest.mark.django_db
def test_update_remove_and_delivery_instructions_require_mutable_draft():
    customer = create_user()
    order = create_order_draft(customer=customer)
    item = add_order_item(
        order=order,
        requested_name="Riz",
        quantity=5,
        unit=ProductUnit.KG,
    )

    update_order_item(item, notes="Bonne qualité")
    set_delivery_instructions(order, "Appeler en arrivant")
    item.refresh_from_db()
    order.refresh_from_db()

    assert item.notes == "Bonne qualité"
    assert order.delivery_instructions == "Appeler en arrivant"

    order.status = OrderStatus.DELIVERED
    order.save(update_fields=["status", "updated_at"])

    with pytest.raises(OrderNotEditable):
        update_order_item(item, quantity=6)

    with pytest.raises(OrderNotEditable):
        remove_order_item(item)


@pytest.mark.django_db
def test_order_items_are_deleted_with_order_but_customer_is_protected():
    customer = create_user()
    order = create_order_draft(customer=customer)
    add_matched_rice(order)

    with pytest.raises(ProtectedError):
        customer.delete()

    order.delete()

    assert OrderItem.objects.count() == 0


@pytest.mark.django_db
def test_confirm_order_with_items_and_location():
    customer = create_user()
    order = create_order_draft(customer=customer)
    add_matched_rice(order)
    set_delivery_location(order=order, point=BONAMOUSSADI)

    confirmed = confirm_order(order)

    assert confirmed.status == OrderStatus.CONFIRMED
    assert confirmed.confirmed_at is not None


@pytest.mark.django_db
def test_confirm_order_is_idempotent_once_confirmed():
    customer = create_user()
    order = create_order_draft(customer=customer)
    add_matched_rice(order)
    set_delivery_location(order=order, point=BONAMOUSSADI)
    confirmed = confirm_order(order)
    confirmed_at = confirmed.confirmed_at

    confirmed_again = confirm_order(confirmed)

    assert confirmed_again.status == OrderStatus.CONFIRMED
    assert confirmed_again.confirmed_at == confirmed_at


@pytest.mark.django_db
def test_confirm_order_requires_items_and_location():
    customer = create_user()
    order = create_order_draft(customer=customer)

    with pytest.raises(OrderNotConfirmable):
        confirm_order(order)

    add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG)

    with pytest.raises(OrderNotConfirmable):
        confirm_order(order)


@pytest.mark.django_db
def test_confirm_order_rejects_unmatched_and_ambiguous_items():
    customer = create_user()
    order = create_order_draft(customer=customer)
    add_order_item(
        order=order,
        requested_name="riz parfumé",
        quantity=5,
        unit=ProductUnit.KG,
        resolution_status=OrderItemResolutionStatus.UNMATCHED,
    )
    add_order_item(
        order=order,
        requested_name="huile Mayor ou Jadida",
        quantity=2,
        unit=ProductUnit.BOTTLE,
        resolution_status=OrderItemResolutionStatus.AMBIGUOUS,
    )
    set_delivery_location(order=order, point=BONAMOUSSADI)

    with pytest.raises(OrderNotConfirmable):
        confirm_order(order)


@pytest.mark.django_db
def test_awaiting_location_can_return_to_draft_and_confirm():
    customer = create_user()
    order = create_order_draft(customer=customer)

    awaiting = mark_awaiting_location(order)
    draft = mark_draft(awaiting)

    assert awaiting.status == OrderStatus.AWAITING_LOCATION
    assert draft.status == OrderStatus.DRAFT


@pytest.mark.django_db
def test_content_editing_is_blocked_after_confirmation():
    customer = create_user()
    order = create_order_draft(customer=customer)
    item = add_order_item(
        order=order,
        requested_name="Riz", quantity=5, unit=ProductUnit.KG,
        product=create_catalog_product("Riz editing"),
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    set_delivery_location(order=order, point=BONAMOUSSADI)
    confirmed = confirm_order(order)

    with pytest.raises(OrderNotEditable):
        add_order_item(
            order=confirmed,
            requested_name="Mayor",
            quantity=2,
            unit=ProductUnit.BOTTLE,
        )
    with pytest.raises(OrderNotEditable):
        update_order_item(item, quantity=6)
    with pytest.raises(OrderNotEditable):
        remove_order_item(item)
    with pytest.raises(OrderNotEditable):
        set_delivery_location(order=confirmed, point=AKWA)


@pytest.mark.django_db
def test_happy_path_and_retry_definition_of_done():
    customer = create_user()
    order = create_order_draft(customer=customer)
    add_matched_rice(order)
    set_delivery_location(order=order, point=BONAMOUSSADI)

    order = confirm_order(order)
    order = start_store_matching(order)
    order = fail_order(order, OrderFailureCode.NO_STORE_AVAILABLE)
    assert order.status == OrderStatus.FAILED
    assert order.failed_from_status == OrderStatus.MATCHING_STORES

    order = retry_failed_order(order)
    assert order.status == OrderStatus.MATCHING_STORES
    assert order.failure_code is None

    order = mark_store_offered(order)
    order = mark_store_accepted(order)
    order = mark_preparing(order)
    order = mark_ready_for_pickup(order)
    order = start_driver_matching(order)
    order = fail_order(order, OrderFailureCode.NO_DRIVER_AVAILABLE)
    assert order.failed_from_status == OrderStatus.MATCHING_DRIVERS

    order = retry_failed_order(order)

    assert order.status == OrderStatus.MATCHING_DRIVERS


@pytest.mark.django_db
def test_transition_matrix_allows_expected_progression_to_delivered():
    customer = create_user()
    order = create_order_draft(customer=customer)
    add_matched_rice(order)
    set_delivery_location(order=order, point=BONAMOUSSADI)

    order = confirm_order(order)
    order = start_store_matching(order)
    order = mark_store_offered(order)
    order = mark_store_accepted(order)
    order = mark_preparing(order)
    order = mark_ready_for_pickup(order)
    order = start_driver_matching(order)
    order = assign_driver(order)
    order = mark_picked_up(order)
    order = mark_in_delivery(order)
    order = mark_delivered(order)

    assert order.status == OrderStatus.DELIVERED
    assert order.completed_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("initial_status", "operation"),
    [
        (OrderStatus.DRAFT, mark_delivered),
        (OrderStatus.CONFIRMED, mark_picked_up),
        (OrderStatus.MATCHING_STORES, assign_driver),
        (OrderStatus.DELIVERED, start_store_matching),
        (OrderStatus.CANCELLED, start_store_matching),
        (OrderStatus.FAILED, start_store_matching),
    ],
)
def test_invalid_normal_transitions_are_rejected(initial_status, operation):
    customer = create_user()
    order = create_order_draft(customer=customer)
    order.status = initial_status
    order.save(update_fields=["status", "updated_at"])

    with pytest.raises(InvalidOrderTransition):
        operation(order)


@pytest.mark.django_db
def test_cancel_order_sets_fields_and_blocks_late_cancellation():
    customer = create_user()
    draft = create_order_draft(customer=customer)

    cancelled = cancel_order(
        draft,
        OrderCancellationCode.CUSTOMER_REQUEST,
        reason="Client request",
    )

    assert cancelled.status == OrderStatus.CANCELLED
    assert cancelled.cancelled_at is not None
    assert cancelled.cancellation_code == OrderCancellationCode.CUSTOMER_REQUEST
    assert cancelled.cancellation_reason == "Client request"

    with pytest.raises(InvalidOrderTransition):
        cancel_order(cancelled, OrderCancellationCode.CUSTOMER_REQUEST)

    late = create_order_draft(customer=create_user("+237699887767"))
    late.status = OrderStatus.PICKED_UP
    late.save(update_fields=["status", "updated_at"])

    with pytest.raises(InvalidOrderTransition):
        cancel_order(late, OrderCancellationCode.ADMIN_REQUEST)


@pytest.mark.django_db
def test_cancel_order_requires_code_and_allows_preparing():
    customer = create_user()
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.PREPARING
    order.save(update_fields=["status", "updated_at"])

    with pytest.raises(InvalidCancellation):
        cancel_order(order, "")

    cancelled = cancel_order(order, OrderCancellationCode.ADMIN_REQUEST)

    assert cancelled.status == OrderStatus.CANCELLED


@pytest.mark.django_db
def test_fail_and_retry_store_matching_failure():
    customer = create_user()
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.MATCHING_STORES
    order.save(update_fields=["status", "updated_at"])

    failed = fail_order(
        order,
        OrderFailureCode.NO_STORE_AVAILABLE,
        reason="No store available",
    )

    assert failed.status == OrderStatus.FAILED
    assert failed.failed_from_status == OrderStatus.MATCHING_STORES
    assert failed.failure_code == OrderFailureCode.NO_STORE_AVAILABLE
    assert failed.failure_reason == "No store available"
    assert failed.failed_at is not None

    retried = retry_failed_order(failed)

    assert retried.status == OrderStatus.MATCHING_STORES
    assert retried.failed_at is None
    assert retried.failure_code is None
    assert retried.failed_from_status is None


@pytest.mark.django_db
def test_fail_and_retry_driver_matching_failure():
    customer = create_user()
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.MATCHING_DRIVERS
    order.save(update_fields=["status", "updated_at"])

    failed = fail_order(order, OrderFailureCode.NO_DRIVER_AVAILABLE)
    retried = retry_failed_order(failed)

    assert retried.status == OrderStatus.MATCHING_DRIVERS


@pytest.mark.django_db
def test_non_retryable_delivery_failure():
    customer = create_user()
    order = create_order_draft(customer=customer)
    order.status = OrderStatus.IN_DELIVERY
    order.save(update_fields=["status", "updated_at"])

    failed = fail_order(order, OrderFailureCode.DELIVERY_FAILED)

    with pytest.raises(OrderNotRetryable):
        retry_failed_order(failed)


@pytest.mark.django_db
def test_invalid_failure_code_for_status_is_rejected():
    customer = create_user()
    order = create_order_draft(customer=customer)

    with pytest.raises(InvalidOrderTransition):
        fail_order(order, OrderFailureCode.NO_DRIVER_AVAILABLE)

    order.status = OrderStatus.MATCHING_STORES
    order.save(update_fields=["status", "updated_at"])

    with pytest.raises(InvalidFailureCode):
        fail_order(order, OrderFailureCode.NO_DRIVER_AVAILABLE)


@pytest.mark.django_db
def test_retry_requires_failed_status():
    customer = create_user()
    order = create_order_draft(customer=customer)

    with pytest.raises(OrderNotRetryable):
        retry_failed_order(order)


@pytest.mark.django_db
def test_transition_services_use_fresh_locked_order_state():
    customer = create_user()
    stale_order = create_order_draft(customer=customer)
    stale_order.status = OrderStatus.CONFIRMED

    Order.objects.filter(pk=stale_order.pk).update(status=OrderStatus.CANCELLED)

    with pytest.raises(InvalidOrderTransition):
        start_store_matching(stale_order)
