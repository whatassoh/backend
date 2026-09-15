from django.contrib import admin

from apps.audit.infrastructure.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "event_type",
        "entity_type",
        "entity_id",
        "actor_type",
        "actor",
        "previous_state",
        "new_state",
        "correlation_id",
    ]
    list_filter = ["event_type", "entity_type", "actor_type", "created_at"]
    search_fields = [
        "entity_id",
        "correlation_id",
        "actor__whatsapp_phone",
    ]
    readonly_fields = [
        "id",
        "actor",
        "actor_type",
        "entity_type",
        "entity_id",
        "event_type",
        "previous_state",
        "new_state",
        "metadata",
        "correlation_id",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
