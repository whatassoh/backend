import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=timezone.now)
    correlation_id: str | None = None
