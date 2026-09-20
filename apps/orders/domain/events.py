import uuid
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True, kw_only=True)
class OrderPickedUp(DomainEvent):
    """A driver explicitly confirmed pickup of its durable assignment."""

    order_id: uuid.UUID
    assignment_id: uuid.UUID
    driver_id: uuid.UUID
    picked_up_at: datetime


@dataclass(frozen=True, kw_only=True)
class DeliveryStarted(DomainEvent):
    order_id: uuid.UUID
    assignment_id: uuid.UUID
    driver_id: uuid.UUID
    delivery_started_at: datetime


@dataclass(frozen=True, kw_only=True)
class OrderDelivered(DomainEvent):
    order_id: uuid.UUID
    assignment_id: uuid.UUID
    driver_id: uuid.UUID
    delivered_at: datetime


@dataclass(frozen=True, kw_only=True)
class DeliveryIssueReported(DomainEvent):
    order_id: uuid.UUID
    assignment_id: uuid.UUID
    issue_id: uuid.UUID
    issue_type: str


@dataclass(frozen=True, kw_only=True)
class DeliveryFailed(DomainEvent):
    order_id: uuid.UUID
    assignment_id: uuid.UUID
    driver_id: uuid.UUID
    issue_id: uuid.UUID
