from django.contrib import admin

from apps.ai.infrastructure.models import (
    AgentExecutionRun,
    AgentShadowRun,
    ResponseCompositionRun,
)


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


@admin.register(AgentExecutionRun)
class AgentExecutionRunAdmin(admin.ModelAdmin):
    list_display = [
        "conversation",
        "status",
        "route",
        "risk_level",
        "policy_decision",
        "latency_ms",
        "created_at",
    ]
    list_filter = ["status", "route", "risk_level", "policy_decision"]
    search_fields = ["conversation__id", "correlation_id"]
    readonly_fields = [field.name for field in AgentExecutionRun._meta.fields]


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
