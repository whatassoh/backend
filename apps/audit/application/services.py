import uuid
from typing import Any

from apps.audit.domain.enums import AuditActorType, AuditEntityType, AuditEventType
from apps.audit.infrastructure.models import AuditEvent


def record_audit_event(
    *,
    actor: Any | None = None,
    actor_type: AuditActorType | str = AuditActorType.SYSTEM,
    entity_type: AuditEntityType | str,
    entity_id: uuid.UUID | str,
    event_type: AuditEventType | str,
    previous_state: str | None = None,
    new_state: str | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        previous_state=previous_state,
        new_state=new_state,
        metadata=metadata or {},
        correlation_id=correlation_id,
    )
