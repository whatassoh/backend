from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("stores", "0005_storeoffernotification")]

    operations = [
        migrations.AlterField(
            model_name="storeoffernotification",
            name="status",
            field=models.CharField(
                max_length=20,
                default="PENDING",
                choices=[
                    ("PENDING", "Pending"), ("SENDING", "Sending"),
                    ("SENT", "Sent"),
                    ("FAILED_RETRYABLE", "Failed retryable"),
                    ("FAILED_PERMANENT", "Failed permanent"),
                    ("CANCELLED", "Cancelled"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="storeoffernotificationattempt",
            name="status",
            field=models.CharField(max_length=20, default="STARTED", choices=[("STARTED", "Started"), ("SUCCEEDED", "Succeeded"), ("FAILED", "Failed")]),
        ),
        migrations.AddField(model_name="storeoffernotification", name="sent_at", field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name="storeoffernotification", name="failed_at", field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name="storeoffernotificationattempt", name="channel", field=models.CharField(max_length=20, default=""), preserve_default=False),
        migrations.AddField(model_name="storeoffernotificationattempt", name="correlation_id", field=models.CharField(max_length=100, null=True, blank=True)),
        migrations.AddField(model_name="storeoffernotificationattempt", name="metadata", field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name="storeoffernotificationattempt", name="message_event", field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name="store_offer_notification_attempts", to="messaging.messageevent")),
    ]
