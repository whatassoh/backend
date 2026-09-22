from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("messaging", "0009_order_delivered_notification_kind")]
    operations = [migrations.AlterField(
        model_name="operationalnotification", name="kind", field=models.CharField(max_length=50, choices=[
            ("ORDER_CONFIRMED", "Order confirmed"), ("ORDER_STORE_ACCEPTED", "Store accepted order"),
            ("ORDER_NO_STORE_AVAILABLE", "No store available"), ("ORDER_CANCELLED", "Order cancelled"),
            ("STORE_OFFER", "Store offer"), ("STORE_PREPARATION_STARTED", "Store preparation started"),
            ("ORDER_READY_FOR_PICKUP", "Order ready for pickup"), ("DRIVER_OFFER", "Driver offer"),
            ("DRIVER_ASSIGNED", "Driver assigned"), ("ORDER_PICKED_UP", "Order picked up"),
            ("ORDER_IN_DELIVERY", "Order in delivery"), ("ORDER_DELIVERED", "Order delivered"),
            ("DRIVER_LOCATION_REQUIRED", "Driver location required"),
            ("DRIVER_LOCATION_REFRESH_REQUIRED", "Driver location refresh required"),
        ]),
    )]
