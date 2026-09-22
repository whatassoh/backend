from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0004_order_delivered_at")]
    operations = [
        migrations.RemoveConstraint(model_name="order", name="orders_order_status_valid"),
        migrations.RemoveConstraint(model_name="order", name="orders_order_failed_from_valid"),
        migrations.AlterField(model_name="order", name="status", field=models.CharField(max_length=30, choices=[
            ("DRAFT", "Draft"), ("AWAITING_LOCATION", "Awaiting location"), ("CONFIRMED", "Confirmed"),
            ("MATCHING_STORES", "Matching stores"), ("AWAITING_PARTIAL_COVERAGE", "Awaiting partial coverage decision"),
            ("STORE_OFFERED", "Store offered"), ("STORE_ACCEPTED", "Store accepted"), ("PREPARING", "Preparing"),
            ("READY_FOR_PICKUP", "Ready for pickup"), ("MATCHING_DRIVERS", "Matching drivers"), ("DRIVER_ASSIGNED", "Driver assigned"),
            ("PICKED_UP", "Picked up"), ("IN_DELIVERY", "In delivery"), ("DELIVERED", "Delivered"), ("CANCELLED", "Cancelled"), ("FAILED", "Failed"),
        ], default="DRAFT")),
        migrations.AlterField(model_name="order", name="failed_from_status", field=models.CharField(blank=True, null=True, max_length=30, choices=[
            ("DRAFT", "Draft"), ("AWAITING_LOCATION", "Awaiting location"), ("CONFIRMED", "Confirmed"),
            ("MATCHING_STORES", "Matching stores"), ("AWAITING_PARTIAL_COVERAGE", "Awaiting partial coverage decision"),
            ("STORE_OFFERED", "Store offered"), ("STORE_ACCEPTED", "Store accepted"), ("PREPARING", "Preparing"),
            ("READY_FOR_PICKUP", "Ready for pickup"), ("MATCHING_DRIVERS", "Matching drivers"), ("DRIVER_ASSIGNED", "Driver assigned"),
            ("PICKED_UP", "Picked up"), ("IN_DELIVERY", "In delivery"), ("DELIVERED", "Delivered"), ("CANCELLED", "Cancelled"), ("FAILED", "Failed"),
        ])),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                name="orders_order_status_valid",
                condition=models.Q(status__in=[
                    "DRAFT", "AWAITING_LOCATION", "CONFIRMED", "MATCHING_STORES", "AWAITING_PARTIAL_COVERAGE",
                    "STORE_OFFERED", "STORE_ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "MATCHING_DRIVERS",
                    "DRIVER_ASSIGNED", "PICKED_UP", "IN_DELIVERY", "DELIVERED", "CANCELLED", "FAILED",
                ]),
            ),
        ),
        migrations.AddConstraint(model_name="order", constraint=models.CheckConstraint(name="orders_order_failed_from_valid", condition=models.Q(failed_from_status__in=[
            "DRAFT", "AWAITING_LOCATION", "CONFIRMED", "MATCHING_STORES", "AWAITING_PARTIAL_COVERAGE", "STORE_OFFERED", "STORE_ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "MATCHING_DRIVERS", "DRIVER_ASSIGNED", "PICKED_UP", "IN_DELIVERY", "DELIVERED", "CANCELLED", "FAILED"
        ]) | models.Q(failed_from_status__isnull=True))),
    ]
