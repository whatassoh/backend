from django.db import models

from common.models import BaseModel


class AgentShadowRun(BaseModel):
    """Sanitized comparison record; it never stores the user's raw message."""

    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="agent_shadow_runs",
    )
    correlation_id = models.CharField(max_length=100, blank=True, null=True)
    graph_version = models.CharField(max_length=40)
    status = models.CharField(max_length=30)
    route = models.CharField(max_length=50, blank=True)
    planned_tools = models.JSONField(default=list)
    risk_level = models.CharField(max_length=4, blank=True)
    parity = models.BooleanField(blank=True, null=True)
    comparison = models.JSONField(default=dict)
    state_snapshot = models.JSONField(default=dict)
    latency_ms = models.PositiveIntegerField(blank=True, null=True)
    error_category = models.CharField(max_length=80, blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "correlation_id"],
                name="ai_shadow_conv_correlation_unique",
            )
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="ai_shadow_status_idx"),
            models.Index(
                fields=["conversation", "created_at"], name="ai_shadow_conv_idx"
            ),
        ]


class AgentExecutionRun(BaseModel):
    """Audit of an active read-only graph run without raw messages or tool data."""

    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="agent_execution_runs",
    )
    correlation_id = models.CharField(max_length=100, blank=True, null=True)
    graph_version = models.CharField(max_length=40)
    status = models.CharField(max_length=30)
    route = models.CharField(max_length=50, blank=True)
    planned_tools = models.JSONField(default=list)
    executed_tools = models.JSONField(default=list)
    result_statuses = models.JSONField(default=dict)
    risk_level = models.CharField(max_length=4, blank=True)
    policy_decision = models.CharField(max_length=40, blank=True)
    latency_ms = models.PositiveIntegerField(blank=True, null=True)
    error_category = models.CharField(max_length=80, blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "correlation_id"],
                name="ai_execution_conv_correlation_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="ai_execution_status_idx"
            ),
            models.Index(
                fields=["conversation", "created_at"], name="ai_execution_conv_idx"
            ),
        ]


class ResponseCompositionRun(BaseModel):
    """Auditable baseline/candidate pair for shadow evaluation and rollout."""

    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="composition_runs",
    )
    correlation_id = models.CharField(max_length=100, blank=True, null=True)
    baseline_text = models.TextField()
    candidate_text = models.TextField()
    baseline_hash = models.CharField(max_length=64)
    candidate_hash = models.CharField(max_length=64)
    delivered_candidate_hash = models.CharField(max_length=64, blank=True)
    use_case = models.CharField(max_length=40, default="GENERIC")
    style = models.JSONField(default=dict)
    evaluation = models.JSONField(default=dict)
    accepted = models.BooleanField(default=False)
    reason = models.CharField(max_length=80)
    shadow = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "correlation_id"],
                name="ai_composition_conv_correlation_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["accepted", "created_at"], name="ai_composition_accept_idx"
            ),
            models.Index(
                fields=["use_case", "created_at"], name="ai_composition_case_idx"
            ),
        ]
