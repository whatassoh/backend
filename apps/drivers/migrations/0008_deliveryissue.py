import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("drivers", "0007_driver_location_reminders"), ("orders", "0004_order_delivered_at")]
    operations = [migrations.CreateModel(
        name="DeliveryIssue", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("issue_type", models.CharField(max_length=40, choices=[("CUSTOMER_UNREACHABLE", "Customer unreachable"), ("ADDRESS_PROBLEM", "Address problem"), ("PICKUP_PROBLEM", "Pickup problem"), ("VEHICLE_PROBLEM", "Vehicle problem"), ("DELIVERY_IMPOSSIBLE", "Delivery impossible"), ("OTHER", "Other")])),
            ("status", models.CharField(max_length=20, default="OPEN", choices=[("OPEN", "Open"), ("RESOLVED", "Resolved"), ("TERMINAL", "Terminal")])),
            ("note", models.TextField(blank=True, null=True)), ("resolved_at", models.DateTimeField(blank=True, null=True)), ("resolution", models.CharField(max_length=255, blank=True, null=True)),
            ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="issues", to="drivers.driverassignment")),
            ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="delivery_issues", to="orders.order")),
            ("reported_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reported_delivery_issues", to=settings.AUTH_USER_MODEL)),
        ], options={"indexes": [models.Index(fields=["order", "status"], name="drv_issue_order_status_idx")]},
    ), migrations.AddConstraint(model_name="deliveryissue", constraint=models.UniqueConstraint(fields=("assignment", "issue_type"), condition=models.Q(("status", "OPEN")), name="delivery_issue_one_open_type"))]
