from django.conf import settings
from django.db import models

from apps.evaluation.domain.enums import (
    DatasetCandidateStatus,
    DatasetSplit,
    DatasetTaskType,
    DatasetVersionStatus,
    EvaluationRunStatus,
)
from common.models import BaseModel


class DatasetCandidate(BaseModel):
    task_type = models.CharField(max_length=30, choices=DatasetTaskType.choices)
    status = models.CharField(
        max_length=20,
        choices=DatasetCandidateStatus.choices,
        default=DatasetCandidateStatus.PENDING,
    )
    sanitized_input = models.TextField()
    predicted_output = models.JSONField(default=dict)
    expected_output = models.JSONField(blank=True, null=True)
    label_provenance = models.CharField(max_length=40, blank=True, null=True)
    quality_signals = models.JSONField(default=dict, blank=True)
    source_reference = models.CharField(max_length=128, blank=True, null=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_dataset_candidates",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)


class DatasetExample(BaseModel):
    task_type = models.CharField(max_length=30, choices=DatasetTaskType.choices)
    input = models.TextField()
    expected_output = models.JSONField()
    source_candidate = models.OneToOneField(
        DatasetCandidate, on_delete=models.PROTECT, related_name="example"
    )
    group_key = models.CharField(max_length=64)


class DatasetVersion(BaseModel):
    name = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    task_type = models.CharField(max_length=30, choices=DatasetTaskType.choices)
    status = models.CharField(
        max_length=20,
        choices=DatasetVersionStatus.choices,
        default=DatasetVersionStatus.DRAFT,
    )
    published_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    examples = models.ManyToManyField(
        DatasetExample, through="DatasetVersionExample", related_name="versions"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"], name="evaluation_dataset_version_unique"
            )
        ]


class DatasetVersionExample(models.Model):
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE)
    dataset_example = models.ForeignKey(DatasetExample, on_delete=models.PROTECT)
    split = models.CharField(max_length=20, choices=DatasetSplit.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_version", "dataset_example"],
                name="evaluation_version_example_unique",
            )
        ]


class EvaluationRun(BaseModel):
    task_type = models.CharField(max_length=30, choices=DatasetTaskType.choices)
    dataset_version = models.ForeignKey(DatasetVersion, on_delete=models.PROTECT)
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=EvaluationRunStatus.choices,
        default=EvaluationRunStatus.RUNNING,
    )
    metrics = models.JSONField(default=dict, blank=True)


class EvaluationResult(BaseModel):
    evaluation_run = models.ForeignKey(
        EvaluationRun, on_delete=models.CASCADE, related_name="results"
    )
    dataset_example = models.ForeignKey(DatasetExample, on_delete=models.PROTECT)
    predicted_output = models.JSONField(default=dict)
    passed = models.BooleanField()
    details = models.JSONField(default=dict, blank=True)
