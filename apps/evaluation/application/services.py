import hashlib
import re

from django.db import transaction
from django.utils import timezone

from apps.evaluation.domain.enums import (
    DatasetCandidateStatus,
    DatasetSplit,
    DatasetVersionStatus,
    EvaluationRunStatus,
)
from apps.evaluation.infrastructure.models import (
    DatasetCandidate,
    DatasetExample,
    DatasetVersion,
    DatasetVersionExample,
    EvaluationResult,
    EvaluationRun,
)


class DatasetSanitizer:
    """Sanitize before persistence; never retain raw production payloads."""

    patterns = [
        (re.compile(r"\+?\d[\d\s.-]{7,}\d"), "[PHONE]"),
        (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
        (
            re.compile(
                r"\b(?:lat(?:itude)?|lon(?:gitude)?|token|api[_ -]?key)"
                r"\s*[:=]\s*[^,\s]+",
                re.I,
            ),
            "[REDACTED]",
        ),
    ]

    def sanitize_text(self, value: str) -> str:
        for pattern, replacement in self.patterns:
            value = pattern.sub(replacement, value)
        return value

    def sanitize_output(self, value):
        if isinstance(value, dict):
            return {
                key: self.sanitize_output(
                    "[REDACTED]"
                    if key.lower()
                    in {
                        "latitude",
                        "longitude",
                        "phone",
                        "external_user_id",
                        "token",
                        "api_key",
                    }
                    else nested
                )
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [self.sanitize_output(item) for item in value]
        return self.sanitize_text(value) if isinstance(value, str) else value


class DatasetCandidateService:
    def __init__(self, sanitizer=None):
        self.sanitizer = sanitizer or DatasetSanitizer()

    def create_candidate(
        self,
        *,
        task_type,
        input_text,
        predicted_output,
        quality_signals,
        source_reference=None,
    ):
        return DatasetCandidate.objects.create(
            task_type=task_type,
            sanitized_input=self.sanitizer.sanitize_text(input_text),
            predicted_output=self.sanitizer.sanitize_output(predicted_output),
            quality_signals=self.sanitizer.sanitize_output(quality_signals),
            source_reference=source_reference,
        )

    def create_from_understanding(
        self, *, input_text, predicted_output, confidence, source_reference=None
    ):
        if predicted_output.get("intent") != "UNKNOWN" and float(confidence) >= 0.60:
            return None
        return self.create_candidate(
            task_type="MESSAGE_UNDERSTANDING",
            input_text=input_text,
            predicted_output=predicted_output,
            quality_signals={
                "reason": "unknown_or_low_confidence",
                "confidence": float(confidence),
            },
            source_reference=source_reference,
        )

    @transaction.atomic
    def review(
        self,
        candidate,
        *,
        reviewer,
        accepted,
        expected_output=None,
        label_provenance="human_review",
    ):
        candidate = DatasetCandidate.objects.select_for_update().get(pk=candidate.pk)
        candidate.status = (
            DatasetCandidateStatus.ACCEPTED
            if accepted
            else DatasetCandidateStatus.REJECTED
        )
        candidate.reviewer = reviewer
        candidate.reviewed_at = timezone.now()
        candidate.expected_output = (
            self.sanitizer.sanitize_output(expected_output) if accepted else None
        )
        candidate.label_provenance = label_provenance if accepted else None
        candidate.save(
            update_fields=[
                "status",
                "reviewer",
                "reviewed_at",
                "expected_output",
                "label_provenance",
                "updated_at",
            ]
        )
        if accepted and not hasattr(candidate, "example"):
            DatasetExample.objects.create(
                task_type=candidate.task_type,
                input=candidate.sanitized_input,
                expected_output=candidate.expected_output,
                source_candidate=candidate,
                group_key=hashlib.sha256(
                    (candidate.source_reference or str(candidate.id)).encode()
                ).hexdigest()[:32],
            )
        return candidate


class DatasetVersionService:
    @transaction.atomic
    def snapshot(self, *, name, version, task_type):
        dataset = DatasetVersion.objects.create(
            name=name, version=version, task_type=task_type
        )
        examples = DatasetExample.objects.filter(task_type=task_type).order_by(
            "created_at", "id"
        )
        DatasetVersionExample.objects.bulk_create(
            [
                DatasetVersionExample(
                    dataset_version=dataset,
                    dataset_example=example,
                    split=_split_for(example.group_key),
                )
                for example in examples
            ]
        )
        return dataset

    @transaction.atomic
    def publish(self, dataset):
        dataset.status = DatasetVersionStatus.PUBLISHED
        dataset.published_at = timezone.now()
        dataset.save(update_fields=["status", "published_at", "updated_at"])
        return dataset


def _split_for(group_key: str) -> str:
    bucket = int(hashlib.sha256(group_key.encode()).hexdigest(), 16) % 100
    return (
        DatasetSplit.TEST
        if bucket < 10
        else DatasetSplit.VALIDATION
        if bucket < 20
        else DatasetSplit.TRAIN
    )


class EvaluationRunner:
    def run(self, *, dataset_version, provider, model, prompt_version, predict):
        run = EvaluationRun.objects.create(
            task_type=dataset_version.task_type,
            dataset_version=dataset_version,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        )
        results = []
        for membership in DatasetVersionExample.objects.filter(
            dataset_version=dataset_version
        ).select_related("dataset_example"):
            predicted = predict(membership.dataset_example.input)
            passed = predicted == membership.dataset_example.expected_output
            results.append(
                EvaluationResult(
                    evaluation_run=run,
                    dataset_example=membership.dataset_example,
                    predicted_output=predicted,
                    passed=passed,
                )
            )
        EvaluationResult.objects.bulk_create(results)
        run.metrics = {
            "exact_match_accuracy": sum(result.passed for result in results)
            / len(results)
            if results
            else 0,
            "case_count": len(results),
        }
        run.status = EvaluationRunStatus.COMPLETED
        run.save(update_fields=["metrics", "status", "updated_at"])
        return run
