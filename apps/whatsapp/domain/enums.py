from django.db import models


class WhatsAppTemplateKey(models.TextChoices):
    ORDER_UPDATE = "ORDER_UPDATE", "Order update"
    ORDER_ACTION_REQUIRED = "ORDER_ACTION_REQUIRED", "Order action required"
    OPERATIONAL_ACTION_REQUIRED = (
        "OPERATIONAL_ACTION_REQUIRED",
        "Operational action required",
    )


class WhatsAppOutboundDecision(models.TextChoices):
    FREE_FORM_ALLOWED = "FREE_FORM_ALLOWED", "Free-form allowed"
    TEMPLATE_REQUIRED = "TEMPLATE_REQUIRED", "Template required"
