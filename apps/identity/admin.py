from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.identity.infrastructure.models import User, UserRole


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0
    readonly_fields = ["created_at", "updated_at"]


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    inlines = [UserRoleInline]
    list_display = [
        "whatsapp_phone",
        "first_name",
        "last_name",
        "status",
        "is_staff",
        "created_at",
    ]
    list_filter = ["status", "is_staff", "is_superuser"]
    search_fields = ["whatsapp_phone", "first_name", "last_name"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at", "last_login"]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "id",
                    "whatsapp_phone",
                    "password",
                ],
            },
        ),
        (
            "Personal information",
            {
                "fields": [
                    "first_name",
                    "last_name",
                    "preferred_language",
                    "status",
                ],
            },
        ),
        (
            "Permissions",
            {
                "fields": [
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ],
            },
        ),
        (
            "Important dates",
            {
                "fields": [
                    "last_login",
                    "created_at",
                    "updated_at",
                ],
            },
        ),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": [
                    "whatsapp_phone",
                    "password1",
                    "password2",
                    "status",
                    "is_staff",
                    "is_superuser",
                ],
            },
        ),
    ]


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "created_at"]
    list_filter = ["role"]
    search_fields = ["user__whatsapp_phone"]
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]
