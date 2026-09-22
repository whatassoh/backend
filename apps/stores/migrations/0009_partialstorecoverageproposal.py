import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("stores", "0008_seller_dashboard_access_grant")]
    operations = [
        migrations.CreateModel(
            name="PartialStoreCoverageProposal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("ACCEPTED", "Accepted"), ("CANCELLED", "Cancelled")], default="PENDING", max_length=20)),
                ("proposal_snapshot", models.JSONField(default=dict)), ("consumed_at", models.DateTimeField(blank=True, null=True)), ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("matching_attempt", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="partial_proposals", to="stores.storematchingattempt")),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="partial_store_coverage_proposal", to="orders.order")),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="partial_coverage_proposals", to="stores.store")),
            ],
        ),
        migrations.AddIndex(model_name="partialstorecoverageproposal", index=models.Index(fields=["status", "updated_at"], name="partial_proposal_scan_idx")),
    ]
