"""Versioned deterministic ranking of already evaluated store candidates."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from time import monotonic

from apps.stores.application.eligibility import EligibleStore
from apps.stores.application.fulfillment import StoreFulfillmentResult


class StoreRankingStrategyVersion(StrEnum):
    DETERMINISTIC_V1 = "deterministic_v1"


@dataclass(frozen=True, kw_only=True)
class RankedStoreCandidate:
    rank: int | None
    store_id: str
    eligible: bool
    fully_covered: bool
    coverage_ratio: Decimal
    distance_meters: int
    reasons: tuple[str, ...]
    score_components: dict[str, str]

    def snapshot(self) -> dict:
        """Serializable ranking state for a future StoreOffer, without PII."""
        data = asdict(self)
        data["coverage_ratio"] = str(self.coverage_ratio)
        return data


@dataclass(frozen=True, kw_only=True)
class StoreRankingResult:
    order_id: str
    ranked_candidates: tuple[RankedStoreCandidate, ...]
    diagnostics: tuple[RankedStoreCandidate, ...]
    strategy: str
    strategy_version: StoreRankingStrategyVersion
    candidate_count: int
    metadata: dict[str, str]


class DeterministicStoreRankingStrategy:
    version = StoreRankingStrategyVersion.DETERMINISTIC_V1

    @staticmethod
    def sort_key(candidate: RankedStoreCandidate):
        return (
            not candidate.fully_covered,
            -candidate.coverage_ratio,
            candidate.distance_meters,
            candidate.store_id,
        )


class StoreRankingService:
    """Ranks existing eligibility/fulfillment results; no catalog or AI queries."""

    def __init__(self, strategy=None):
        self.strategy = strategy or DeterministicStoreRankingStrategy()

    def rank_candidates(
        self,
        order,
        eligible_stores: tuple[EligibleStore, ...],
        fulfillment_results: tuple[StoreFulfillmentResult, ...],
        *,
        max_candidates: int | None = None,
    ) -> StoreRankingResult:
        started = monotonic()
        distances = {str(item.store_id): item.distance_meters for item in eligible_stores}
        fulfillment = {result.store_id: result for result in fulfillment_results}
        diagnostics = []
        for store_id, distance in distances.items():
            result = fulfillment.get(store_id)
            if result is None:
                continue
            diagnostics.append(
                RankedStoreCandidate(
                    rank=None,
                    store_id=store_id,
                    eligible=True,
                    fully_covered=result.fully_covered,
                    coverage_ratio=result.coverage_ratio,
                    distance_meters=distance,
                    reasons=(
                        "full_coverage" if result.fully_covered else "partial_coverage",
                        "distance_used",
                    ),
                    score_components={
                        "fully_covered": str(result.fully_covered).lower(),
                        "coverage_ratio": str(result.coverage_ratio),
                        "distance_meters": str(distance),
                        "price": "ignored_v1",
                    },
                )
            )
        diagnostics.sort(key=self.strategy.sort_key)
        offerable = [candidate for candidate in diagnostics if candidate.fully_covered]
        if max_candidates is not None:
            offerable = offerable[:max_candidates]
        ranked = tuple(
            RankedStoreCandidate(
                **{**asdict(candidate), "rank": index}
            )
            for index, candidate in enumerate(offerable, start=1)
        )
        return StoreRankingResult(
            order_id=str(order.id),
            ranked_candidates=ranked,
            diagnostics=tuple(diagnostics),
            strategy="lexicographic_full_coverage_coverage_distance_id",
            strategy_version=self.strategy.version,
            candidate_count=len(diagnostics),
            metadata={
                "partial_candidates_offerable": "false",
                "pricing": "ignored_v1",
                "duration_ms": str(round((monotonic() - started) * 1000)),
            },
        )
