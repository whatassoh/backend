import uuid
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("stores", "0003_storeoffer")]
    operations = [migrations.CreateModel(name="StoreMatchingAttempt", fields=[
        ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
        ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
        ("status", models.CharField(max_length=20, default="RUNNING", choices=[("RUNNING","Running"),("EXHAUSTED","Exhausted"),("SUCCEEDED","Succeeded")])),
        ("strategy_version", models.CharField(max_length=50)), ("candidate_snapshot", models.JSONField(default=list)),
        ("current_position", models.PositiveIntegerField(default=0)), ("started_at", models.DateTimeField(auto_now_add=True)),
        ("completed_at", models.DateTimeField(blank=True,null=True)), ("failure_reason", models.CharField(max_length=50,blank=True,null=True)),
        ("order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="store_matching_attempt", to="orders.order")),
    ])]
