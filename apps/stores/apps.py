from django.apps import AppConfig


class StoresConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.stores"
    verbose_name = "Stores"

    def ready(self):
        from apps.orders.application.customer_notifications import (
            notify_order_cancelled,
            notify_order_failed,
            notify_order_ready,
        )
        from apps.orders.domain.events import (
            OrderCancelled,
            OrderConfirmed,
            OrderFailed,
            OrderReadyForPickup,
        )
        from apps.stores.application.order_confirmed import enqueue_store_matching
        from common.events import subscribe

        subscribe(OrderConfirmed, enqueue_store_matching)
        subscribe(OrderFailed, notify_order_failed)
        subscribe(OrderCancelled, notify_order_cancelled)
        subscribe(OrderReadyForPickup, notify_order_ready)
