from django.db import migrations, models
from apps.audit.domain.enums import AuditEventType


class Migration(migrations.Migration):
    dependencies = [("audit", "0004_driver_offer_audit")]
    operations = [
        migrations.RemoveConstraint(model_name="auditevent", name="audit_event_type_valid"),
        migrations.AddConstraint(model_name="auditevent", constraint=models.CheckConstraint(condition=models.Q(("event_type__in", AuditEventType.values)), name="audit_event_type_valid")),
    ]
