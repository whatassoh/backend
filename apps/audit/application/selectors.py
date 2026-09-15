import uuid

from django.db.models import QuerySet

from apps.audit.domain.enums import AuditEntityType
from apps.audit.infrastructure.models import AuditEvent


def get_entity_audit_history(
    entity_type: AuditEntityType | str,
    entity_id: uuid.UUID | str,
) -> QuerySet[AuditEvent]:
    return (
        AuditEvent.objects.filter(entity_type=entity_type, entity_id=entity_id)
        .select_related("actor")
        .order_by("created_at", "id")
    )


def get_order_audit_history(order: object) -> QuerySet[AuditEvent]:
    return get_entity_audit_history(AuditEntityType.ORDER, order.id)


def get_audit_events_by_correlation_id(correlation_id: str) -> QuerySet[AuditEvent]:
    return (
        AuditEvent.objects.filter(correlation_id=correlation_id)
        .select_related("actor")
        .order_by("created_at", "id")
    )


def get_actor_audit_history(actor: object) -> QuerySet[AuditEvent]:
    return (
        AuditEvent.objects.filter(actor=actor)
        .select_related("actor")
        .order_by("created_at", "id")
    )
