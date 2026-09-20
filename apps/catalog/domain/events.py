from dataclasses import dataclass

from common.events.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class StoreProductAdded(DomainEvent):
    actor_id: str
    store_id: str
    store_product_id: str


@dataclass(frozen=True, kw_only=True)
class StoreProductPriceChanged(DomainEvent):
    actor_id: str
    store_id: str
    store_product_id: str


@dataclass(frozen=True, kw_only=True)
class StoreProductAvailabilityChanged(DomainEvent):
    actor_id: str
    store_id: str
    store_product_id: str
    action: str
