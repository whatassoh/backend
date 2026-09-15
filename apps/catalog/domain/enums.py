from django.db import models


class CategoryStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class ProductStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class ProductVariantStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class ProductUnit(models.TextChoices):
    KG = "KG", "Kilogram"
    G = "G", "Gram"
    L = "L", "Liter"
    ML = "ML", "Milliliter"
    UNIT = "UNIT", "Unit"
    PACK = "PACK", "Pack"
    BOTTLE = "BOTTLE", "Bottle"
    BAG = "BAG", "Bag"
    OTHER = "OTHER", "Other"


class ProductAvailability(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Available"
    UNAVAILABLE = "UNAVAILABLE", "Unavailable"
    UNKNOWN = "UNKNOWN", "Unknown"
