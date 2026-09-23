from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0003_order_delivery_started_at")]
    operations = [
        migrations.AddField(
            model_name="order",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        )
    ]
