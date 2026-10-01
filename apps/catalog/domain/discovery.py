from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class ProductSearchQuery:
    """Typed, non-authoritative search hints supplied by conversation parsing."""

    text: str = ""
    product_hint: str | None = None
    category_hint: str | None = None
    variant_hint: str | None = None
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    currency: str | None = None
    limit: int = 8
    cursor: int = 0


@dataclass(frozen=True, kw_only=True)
class ProductSearchHit:
    product_id: UUID
    product_name: str
    category_name: str
    variant_id: UUID | None = None
    variant_name: str | None = None
    minimum_price: Decimal | None = None
    maximum_price: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, kw_only=True)
class ProductSearchResult:
    query: ProductSearchQuery
    products: tuple[ProductSearchHit, ...]
    total: int
    next_cursor: int | None
    understood: bool = True
    known_but_unavailable: bool = False
    variant_suggestions: tuple[ProductSearchHit, ...] = ()
