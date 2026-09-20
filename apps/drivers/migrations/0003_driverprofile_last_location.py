from django.contrib.gis.db.models import fields
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("drivers", "0002_driverprofile_last_location_at")]
    operations = [migrations.AddField(model_name="driverprofile", name="last_location", field=fields.PointField(blank=True, geography=True, null=True, srid=4326))]
