from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ai", "0002_response_composition_run")]

    operations = [
        migrations.AddField(
            model_name="responsecompositionrun",
            name="delivered_candidate_hash",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="responsecompositionrun",
            name="evaluation",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="responsecompositionrun",
            name="use_case",
            field=models.CharField(default="GENERIC", max_length=40),
        ),
        migrations.AddIndex(
            model_name="responsecompositionrun",
            index=models.Index(
                fields=["use_case", "created_at"],
                name="ai_composition_case_idx",
            ),
        ),
    ]
