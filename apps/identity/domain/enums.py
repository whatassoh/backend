from django.db import models


class UserStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    SUSPENDED = "SUSPENDED", "Suspended"


class UserRoleType(models.TextChoices):
    CLIENT = "CLIENT", "Client"
    STORE_OWNER = "STORE_OWNER", "Store owner"
    STORE_MANAGER = "STORE_MANAGER", "Store manager"
    STORE_AGENT = "STORE_AGENT", "Store agent"
    DRIVER = "DRIVER", "Driver"
    ADMIN = "ADMIN", "Admin"
