from django.db import models


class DatasetCandidateStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"


class DatasetTaskType(models.TextChoices):
    MESSAGE_UNDERSTANDING = "MESSAGE_UNDERSTANDING", "Message understanding"
    CATALOG_RESOLUTION = "CATALOG_RESOLUTION", "Catalog resolution"
    CLARIFICATION = "CLARIFICATION", "Clarification"
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL", "Knowledge retrieval"


class DatasetVersionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"


class DatasetSplit(models.TextChoices):
    TRAIN = "TRAIN", "Train"
    VALIDATION = "VALIDATION", "Validation"
    TEST = "TEST", "Test"


class EvaluationRunStatus(models.TextChoices):
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
