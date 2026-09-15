import uuid

from django.db.models import QuerySet

from apps.orders.infrastructure.models import Order, OrderItem


def get_order_by_id(order_id: uuid.UUID | str) -> Order | None:
    return (
        Order.objects.select_related("customer")
        .prefetch_related("items", "items__product", "items__product_variant")
        .filter(id=order_id)
        .first()
    )


def get_order_by_reference(reference: str) -> Order | None:
    return (
        Order.objects.select_related("customer")
        .prefetch_related("items", "items__product", "items__product_variant")
        .filter(reference=reference)
        .first()
    )


def get_customer_orders(customer: object) -> QuerySet[Order]:
    return (
        Order.objects.filter(customer=customer)
        .select_related("customer")
        .prefetch_related("items")
    )


def get_order_items(order: Order) -> QuerySet[OrderItem]:
    return OrderItem.objects.filter(order=order).select_related(
        "order",
        "product",
        "product_variant",
    )


def get_order_with_items(order_id: uuid.UUID | str) -> Order | None:
    return get_order_by_id(order_id)
