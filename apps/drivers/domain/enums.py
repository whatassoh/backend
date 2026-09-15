from django.db import models


class DriverStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    SUSPENDED = "SUSPENDED", "Suspended"


class DriverAvailability(models.TextChoices):
    OFFLINE = "OFFLINE", "Offline"
    AVAILABLE = "AVAILABLE", "Available"
    BUSY = "BUSY", "Busy"


class VehicleType(models.TextChoices):
    MOTORBIKE = "MOTORBIKE", "Motorbike"
    BICYCLE = "BICYCLE", "Bicycle"
    CAR = "CAR", "Car"
    VAN = "VAN", "Van"
    OTHER = "OTHER", "Other"
