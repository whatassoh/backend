from django.db import migrations


def backfill_whatsapp_channel_identities(apps, schema_editor):
    user_model = apps.get_model("identity", "User")
    channel_identity_model = apps.get_model("messaging", "ChannelIdentity")

    users = user_model.objects.exclude(whatsapp_phone__isnull=True).exclude(
        whatsapp_phone=""
    )
    for user in users.iterator():
        phone_number = user.whatsapp_phone
        external_user_id = phone_number.lstrip("+")
        channel_identity_model.objects.get_or_create(
            channel="WHATSAPP",
            external_user_id=external_user_id,
            defaults={
                "user_id": user.id,
                "phone_number": phone_number,
                "metadata": {"source": "identity.whatsapp_phone"},
                "is_active": True,
            },
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            backfill_whatsapp_channel_identities,
            noop_reverse,
        ),
    ]
