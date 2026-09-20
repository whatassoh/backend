from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.RemoveConstraint(model_name="auditevent", name="audit_entity_type_valid"),
        migrations.RemoveConstraint(model_name="auditevent", name="audit_event_type_valid"),
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.CheckConstraint(
                name="audit_entity_type_valid",
                condition=models.Q(("entity_type__in", ["ORDER", "STORE_OFFER", "DRIVER_OFFER", "DELIVERY", "SELLER_DASHBOARD_ACCESS"])),
            ),
        ),
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.CheckConstraint(
                name="audit_event_type_valid",
                condition=models.Q(("event_type__in", ["ORDER_CREATED", "ORDER_ITEM_ADDED", "ORDER_ITEM_UPDATED", "ORDER_ITEM_REMOVED", "ORDER_DELIVERY_LOCATION_UPDATED", "ORDER_DELIVERY_INSTRUCTIONS_UPDATED", "ORDER_CONFIRMED", "ORDER_CANCELLED", "ORDER_FAILED", "ORDER_RETRY_REQUESTED", "ORDER_STATUS_CHANGED", "SELLER_DASHBOARD_ACCESS_ISSUED", "SELLER_DASHBOARD_ACCESS_CONSUMED", "SELLER_DASHBOARD_ACCESS_REJECTED"])),
            ),
        ),
    ]
