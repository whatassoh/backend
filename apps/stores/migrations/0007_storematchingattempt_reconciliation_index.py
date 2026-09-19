from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("stores", "0006_storeoffernotification_delivery_state")]

    operations = [
        migrations.AddIndex(
            model_name="storematchingattempt",
            index=models.Index(fields=["status", "updated_at"], name="store_match_attempt_scan_idx"),
        ),
    ]
