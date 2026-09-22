from django.apps import AppConfig


class StoresConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.stores"
    verbose_name = "Stores"

    def ready(self):
        from apps.orders.domain.events import OrderConfirmed
        from apps.stores.application.order_confirmed import enqueue_store_matching
        from common.events import subscribe

        subscribe(OrderConfirmed, enqueue_store_matching)
