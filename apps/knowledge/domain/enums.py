from django.db import models


class KnowledgeSourceType(models.TextChoices):
    FAQ = "FAQ", "FAQ"
    POLICY = "POLICY", "Policy"
    GUIDE = "GUIDE", "Guide"
    OPERATIONS = "OPERATIONS", "Operations"
    OTHER = "OTHER", "Other"


class KnowledgeSourceStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
