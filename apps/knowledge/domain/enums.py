from django.db import models


class KnowledgeSourceType(models.TextChoices):
    FAQ = "FAQ", "FAQ"
    POLICY = "POLICY", "Policy"
    GUIDE = "GUIDE", "Guide"
    OPERATIONS = "OPERATIONS", "Operations"
    OTHER = "OTHER", "Other"


class KnowledgeSourceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    QUARANTINED = "QUARANTINED", "Quarantined"
    REJECTED = "REJECTED", "Rejected"


class KnowledgeAudience(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    AUTHENTICATED = "AUTHENTICATED", "Authenticated users"
    RESTRICTED = "RESTRICTED", "Explicit grants"
    INTERNAL = "INTERNAL", "Internal staff"


class KnowledgeTrustLevel(models.TextChoices):
    FIRST_PARTY = "FIRST_PARTY", "First-party"
    REVIEWED = "REVIEWED", "Human reviewed"
    EXTERNAL = "EXTERNAL", "External and untrusted"


class KnowledgeSensitivity(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    INTERNAL = "INTERNAL", "Internal"
    CONFIDENTIAL = "CONFIDENTIAL", "Confidential"


class KnowledgeGrantPrincipal(models.TextChoices):
    USER = "USER", "User"
    ROLE = "ROLE", "Role"
    STORE = "STORE", "Store"


class KnowledgeEmbeddingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    INDEXING = "INDEXING", "Indexing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"
