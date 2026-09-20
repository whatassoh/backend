from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("audit", "0002_seller_dashboard_access_audit")]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="entity_type",
            field=models.CharField(
                choices=[
                    ("ORDER", "Order"), ("STORE_OFFER", "Store offer"),
                    ("DRIVER_OFFER", "Driver offer"), ("DELIVERY", "Delivery"),
                    ("SELLER_DASHBOARD_ACCESS", "Seller dashboard access"),
                ], max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="auditevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("ORDER_CREATED", "Order created"), ("ORDER_ITEM_ADDED", "Order item added"),
                    ("ORDER_ITEM_UPDATED", "Order item updated"), ("ORDER_ITEM_REMOVED", "Order item removed"),
                    ("ORDER_DELIVERY_LOCATION_UPDATED", "Order delivery location updated"),
                    ("ORDER_DELIVERY_INSTRUCTIONS_UPDATED", "Order delivery instructions updated"),
                    ("ORDER_CONFIRMED", "Order confirmed"), ("ORDER_CANCELLED", "Order cancelled"),
                    ("ORDER_FAILED", "Order failed"), ("ORDER_RETRY_REQUESTED", "Order retry requested"),
                    ("ORDER_STATUS_CHANGED", "Order status changed"),
                    ("SELLER_DASHBOARD_ACCESS_ISSUED", "Seller dashboard access issued"),
                    ("SELLER_DASHBOARD_ACCESS_CONSUMED", "Seller dashboard access consumed"),
                    ("SELLER_DASHBOARD_ACCESS_REJECTED", "Seller dashboard access rejected"),
                ], max_length=60,
            ),
        ),
    ]
