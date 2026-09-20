from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("messaging", "0007_order_picked_up_notification_kind")]

    operations = [
        migrations.AlterField(
            model_name="operationalnotification", name="kind",
            field=models.CharField(choices=[
                ("ORDER_CONFIRMED", "Order confirmed"), ("STORE_OFFER", "Store offer"),
                ("STORE_PREPARATION_STARTED", "Store preparation started"),
                ("ORDER_READY_FOR_PICKUP", "Order ready for pickup"),
                ("DRIVER_OFFER", "Driver offer"), ("DRIVER_ASSIGNED", "Driver assigned"),
                ("ORDER_PICKED_UP", "Order picked up"), ("ORDER_IN_DELIVERY", "Order in delivery"),
                ("DRIVER_LOCATION_REQUIRED", "Driver location required"),
                ("DRIVER_LOCATION_REFRESH_REQUIRED", "Driver location refresh required"),
            ], max_length=50),
        ),
    ]
