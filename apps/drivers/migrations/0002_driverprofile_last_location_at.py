# Generated manually for lot 9.2; location ingestion is deferred to lot 9.3.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("drivers", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="driverprofile",
            name="last_location_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
