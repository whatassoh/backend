import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("drivers", "0004_driveroffer_driverassignment")]
    operations = [migrations.CreateModel(name="DriverMatchingAttempt", fields=[("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("status", models.CharField(max_length=30, default="RUNNING", choices=[("RUNNING", "Running"), ("SUCCEEDED", "Succeeded"), ("EXHAUSTED", "Exhausted"), ("CANCELLED", "Cancelled"), ("FAILED_TECHNICAL", "Failed technical")])), ("strategy", models.CharField(max_length=30, default="SEQUENTIAL")), ("strategy_version", models.CharField(max_length=50)), ("candidate_snapshot", models.JSONField(default=list)), ("current_position", models.PositiveIntegerField(default=0)), ("completed_at", models.DateTimeField(null=True, blank=True)), ("metadata", models.JSONField(default=dict, blank=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("order", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="driver_matching_attempt", to="orders.order"))])]
