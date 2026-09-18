from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from apps.orders.domain.enums import OrderItemResolutionStatus


class CatalogMatchStrategy(StrEnum):
    EXACT_CANONICAL = "EXACT_CANONICAL"
    EXACT_ALIAS = "EXACT_ALIAS"
    EXACT_DISPLAY_NAME = "EXACT_DISPLAY_NAME"
    EXACT_VARIANT = "EXACT_VARIANT"
    FUZZY_NAME = "FUZZY_NAME"


class CatalogResolutionReason(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"
    ALIAS_MATCH = "ALIAS_MATCH"
    FUZZY_MATCH = "FUZZY_MATCH"
    MULTIPLE_STRONG_CANDIDATES = "MULTIPLE_STRONG_CANDIDATES"
    VARIANT_SELECTION_REQUIRED = "VARIANT_SELECTION_REQUIRED"
    NO_CANDIDATE = "NO_CANDIDATE"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"


@dataclass(frozen=True, kw_only=True)
class CatalogCandidate:
    product_id: UUID
    canonical_name: str
    matched_on: CatalogMatchStrategy
    score: float
    variant_id: UUID | None = None
    matched_alias: str | None = None
    category_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class CatalogResolution:
    status: OrderItemResolutionStatus
    requested_name: str
    product_id: UUID | None
    variant_id: UUID | None
    candidates: tuple[CatalogCandidate, ...]
    reason: CatalogResolutionReason
    score: float | None = None
