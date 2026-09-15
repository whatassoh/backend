from django.db import models


class ConversationStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"
