import uuid
from dataclasses import dataclass

from common.events.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class DriverAssigned(DomainEvent):
    """The durable DriverAssignment has been committed for an Order."""

    assignment_id: uuid.UUID
    order_id: uuid.UUID
    driver_id: uuid.UUID
