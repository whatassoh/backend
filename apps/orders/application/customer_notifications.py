"""Customer-facing, idempotent order milestone notification intents."""

from apps.messaging.application.notifications import NotificationService
from apps.messaging.domain.enums import NotificationKind
from apps.orders.domain.enums import OrderFailureCode
from apps.orders.infrastructure.models import Order


def _intent(*, order_id, kind, key, text, correlation_id=None):
    order = Order.objects.filter(pk=order_id).select_related("customer").first()
    if order is None:
        return None
    return NotificationService().create_intent(
        kind=kind,
        recipient=order.customer,
        business_entity_type="orders.Order",
        business_entity_id=order.id,
        idempotency_key=key,
        payload={"reference": order.reference, "text": text},
        correlation_id=correlation_id,
    )


def notify_store_accepted(*, order_id, correlation_id=None):
    return _intent(
        order_id=order_id,
        kind=NotificationKind.ORDER_STORE_ACCEPTED,
        key=f"order:{order_id}:store-accepted",
        text="Une boutique a accepté votre commande. Elle va maintenant être préparée.",
        correlation_id=correlation_id,
    )


def notify_order_failed(event):
    if event.failure_code != OrderFailureCode.NO_STORE_AVAILABLE:
        return None
    return _intent(
        order_id=event.order_id,
        kind=NotificationKind.ORDER_NO_STORE_AVAILABLE,
        key=f"order:{event.order_id}:no-store",
        text="Je n'ai trouvé aucune boutique disponible capable de fournir les produits de cette commande.",
        correlation_id=event.correlation_id,
    )


def notify_order_cancelled(event):
    return _intent(
        order_id=event.order_id,
        kind=NotificationKind.ORDER_CANCELLED,
        key=f"order:{event.order_id}:cancelled",
        text="Votre commande est annulée.",
        correlation_id=event.correlation_id,
    )
