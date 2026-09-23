from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0002_order_picked_up_at")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="delivery_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
