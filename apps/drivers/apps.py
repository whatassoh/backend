from django.apps import AppConfig


class DriversConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.drivers"
    verbose_name = "Drivers"

    def ready(self):
        from apps.drivers.application.order_ready import enqueue_driver_matching
        from apps.orders.domain.events import OrderReadyForPickup
        from common.events import subscribe

        subscribe(OrderReadyForPickup, enqueue_driver_matching)
