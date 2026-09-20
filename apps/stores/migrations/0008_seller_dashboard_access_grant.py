import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stores", "0007_storematchingattempt_reconciliation_index"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SellerDashboardAccessGrant",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("purpose", models.CharField(default="SELLER_DASHBOARD", max_length=40)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="seller_dashboard_access_grants", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(model_name="sellerdashboardaccessgrant", index=models.Index(fields=["user", "purpose", "expires_at"], name="store_dash_grant_user_idx")),
        migrations.AddIndex(model_name="sellerdashboardaccessgrant", index=models.Index(fields=["expires_at"], name="store_dash_grant_exp_idx")),
    ]
