import uuid
from dataclasses import dataclass

from common.events.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class OrderCreated(DomainEvent):
    order_id: uuid.UUID
    customer_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class OrderConfirmed(DomainEvent):
    order_id: uuid.UUID
    customer_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class OrderCancelled(DomainEvent):
    order_id: uuid.UUID
    customer_id: uuid.UUID
    cancellation_code: str


@dataclass(frozen=True, kw_only=True)
class OrderFailed(DomainEvent):
    order_id: uuid.UUID
    failure_code: str
    failed_from_status: str


@dataclass(frozen=True, kw_only=True)
class OrderRetryRequested(DomainEvent):
    order_id: uuid.UUID
    failure_code: str
    retry_target_status: str


@dataclass(frozen=True, kw_only=True)
class OrderReadyForPickup(DomainEvent):
    """Boundary event for Phase 9; it deliberately does not start matching."""

    order_id: uuid.UUID
    store_id: uuid.UUID
    customer_id: uuid.UUID
