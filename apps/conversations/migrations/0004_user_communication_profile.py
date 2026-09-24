import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0003_conversation_memory"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserCommunicationProfile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("language", models.CharField(default="fr", max_length=12)),
                ("register", models.CharField(default="neutral", max_length=20)),
                ("address_form", models.CharField(default="vous", max_length=12)),
                ("verbosity", models.CharField(default="balanced", max_length=12)),
                ("warmth", models.CharField(default="warm", max_length=12)),
                ("emoji_level", models.CharField(default="none", max_length=12)),
                ("technical_level", models.CharField(default="simple", max_length=12)),
                ("sample_count", models.PositiveIntegerField(default=0)),
                ("confidence", models.JSONField(blank=True, default=dict)),
                ("signals", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="communication_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        )
    ]
