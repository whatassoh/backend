from django.conf import settings
from django.db import models

from apps.audit.domain.enums import (
    AuditActorType,
    AuditEntityType,
    AuditEventType,
)
from apps.audit.domain.exceptions import AuditEventImmutableError
from common.models import BaseModel


class AuditEventQuerySet(models.QuerySet):
    def delete(self) -> tuple[int, dict[str, int]]:
        raise AuditEventImmutableError("Audit events cannot be deleted.")


class AuditEvent(BaseModel):
    objects = AuditEventQuerySet.as_manager()

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        blank=True,
        null=True,
    )
    actor_type = models.CharField(max_length=20, choices=AuditActorType.choices)
    entity_type = models.CharField(max_length=40, choices=AuditEntityType.choices)
    entity_id = models.UUIDField()
    event_type = models.CharField(max_length=60, choices=AuditEventType.choices)
    previous_state = models.CharField(max_length=60, blank=True, null=True)
    new_state = models.CharField(max_length=60, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    correlation_id = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id", "created_at"],
                name="audit_event_entity_idx",
            ),
            models.Index(fields=["actor"], name="audit_event_actor_idx"),
            models.Index(fields=["event_type"], name="audit_event_type_idx"),
            models.Index(fields=["created_at"], name="audit_event_created_idx"),
            models.Index(fields=["correlation_id"], name="audit_event_corr_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="audit_actor_type_valid",
                condition=models.Q(actor_type__in=AuditActorType.values),
            ),
            models.CheckConstraint(
                name="audit_entity_type_valid",
                condition=models.Q(entity_type__in=AuditEntityType.values),
            ),
            models.CheckConstraint(
                name="audit_event_type_valid",
                condition=models.Q(event_type__in=AuditEventType.values),
            ),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        if not self._state.adding:
            raise AuditEventImmutableError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]:
        raise AuditEventImmutableError("Audit events cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.event_type} {self.entity_type}:{self.entity_id}"
