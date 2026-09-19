import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial"), ("stores", "0002_alter_storemembership_id")]

    operations = [
        migrations.CreateModel(
            name="StoreOffer",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(max_length=20, default="PENDING", choices=[("PENDING", "Pending"), ("ACCEPTED", "Accepted"), ("REJECTED", "Rejected"), ("EXPIRED", "Expired"), ("CANCELLED", "Cancelled")])),
                ("rank", models.PositiveIntegerField()),
                ("strategy_version", models.CharField(max_length=50)),
                ("selection_snapshot", models.JSONField(default=dict, blank=True)),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                ("expired_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_reason", models.CharField(max_length=40, blank=True, null=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="store_offers", to="orders.order")),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="offers", to="stores.store")),
            ],
            options={"indexes": [models.Index(fields=["order", "status"], name="store_offer_order_status_idx"), models.Index(fields=["status", "expires_at"], name="store_offer_status_exp_idx")]},
        ),
        migrations.AddConstraint(model_name="storeoffer", constraint=models.UniqueConstraint(fields=("order", "store"), name="store_offer_order_store_unique")),
        migrations.AddConstraint(model_name="storeoffer", constraint=models.UniqueConstraint(fields=("order",), condition=models.Q(("status", "ACCEPTED")), name="store_offer_one_accepted_per_order")),
        migrations.AddConstraint(model_name="storeoffer", constraint=models.CheckConstraint(condition=models.Q(("status__in", ["PENDING", "ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED"])), name="store_offer_status_valid")),
    ]
