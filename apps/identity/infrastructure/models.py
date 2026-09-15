from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from apps.identity.domain.enums import UserRoleType, UserStatus
from apps.identity.domain.services import normalize_whatsapp_phone
from common.models import BaseModel


class UserManager(BaseUserManager["User"]):
    def create_user(
        self,
        whatsapp_phone: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "User":
        if not whatsapp_phone:
            raise ValueError("The WhatsApp phone must be set.")

        user = self.model(
            whatsapp_phone=normalize_whatsapp_phone(whatsapp_phone),
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(
        self,
        whatsapp_phone: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(whatsapp_phone, password, **extra_fields)


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    whatsapp_phone = models.CharField(max_length=16, unique=True)
    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150, blank=True, null=True)
    preferred_language = models.CharField(max_length=10, default="fr")
    status = models.CharField(
        max_length=20,
        choices=UserStatus.choices,
        default=UserStatus.ACTIVE,
    )
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    objects = UserManager()

    USERNAME_FIELD = "whatsapp_phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                name="identity_user_status_valid",
                condition=models.Q(status__in=UserStatus.values),
            ),
        ]

    def clean(self) -> None:
        super().clean()
        self.whatsapp_phone = normalize_whatsapp_phone(self.whatsapp_phone)

    def save(self, *args: object, **kwargs: object) -> None:
        self.whatsapp_phone = normalize_whatsapp_phone(self.whatsapp_phone)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.whatsapp_phone


class UserRole(BaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    role = models.CharField(
        max_length=30,
        choices=UserRoleType.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user_id", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role"],
                name="identity_user_role_unique",
            ),
            models.CheckConstraint(
                name="identity_user_role_valid",
                condition=models.Q(role__in=UserRoleType.values),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.whatsapp_phone} - {self.role}"
