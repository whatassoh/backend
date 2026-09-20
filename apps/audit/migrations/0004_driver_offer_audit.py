from django.db import migrations, models
from apps.audit.domain.enums import AuditEventType


class Migration(migrations.Migration):
    dependencies = [("audit", "0003_alter_auditevent_dashboard_choices")]
    operations = [migrations.AlterField(model_name="auditevent", name="event_type", field=models.CharField(max_length=60, choices=AuditEventType.choices))]
