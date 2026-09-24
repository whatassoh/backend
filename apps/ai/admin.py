from django.contrib import admin

from apps.ai.infrastructure.models import AgentShadowRun, ResponseCompositionRun


@admin.register(AgentShadowRun)
class AgentShadowRunAdmin(admin.ModelAdmin):
    list_display = [
        "conversation",
        "status",
        "route",
        "risk_level",
        "parity",
        "latency_ms",
        "created_at",
    ]
    list_filter = ["status", "route", "risk_level", "parity"]
    search_fields = ["conversation__id", "correlation_id"]
    readonly_fields = [field.name for field in AgentShadowRun._meta.fields]


@admin.register(ResponseCompositionRun)
class ResponseCompositionRunAdmin(admin.ModelAdmin):
    list_display = [
        "conversation",
        "use_case",
        "accepted",
        "reason",
        "shadow",
        "created_at",
    ]
    list_filter = ["use_case", "accepted", "reason", "shadow"]
    search_fields = ["conversation__id", "correlation_id"]
    readonly_fields = [field.name for field in ResponseCompositionRun._meta.fields]
