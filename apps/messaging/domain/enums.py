from django.db import models


class MessagingChannel(models.TextChoices):
    WHATSAPP = "WHATSAPP", "WhatsApp"
    TELEGRAM = "TELEGRAM", "Telegram"


class MessageDirection(models.TextChoices):
    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class MessageType(models.TextChoices):
    TEXT = "TEXT", "Text"
    LOCATION = "LOCATION", "Location"
    IMAGE = "IMAGE", "Image"
    DOCUMENT = "DOCUMENT", "Document"
    AUDIO = "AUDIO", "Audio"
    VIDEO = "VIDEO", "Video"
    INTERACTIVE = "INTERACTIVE", "Interactive"
    TEMPLATE = "TEMPLATE", "Template"
    UNKNOWN = "UNKNOWN", "Unknown"


class MessageStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    PROCESSING = "PROCESSING", "Processing"
    PROCESSED = "PROCESSED", "Processed"
    FAILED = "FAILED", "Failed"
    SENT = "SENT", "Sent"
    IGNORED = "IGNORED", "Ignored"
