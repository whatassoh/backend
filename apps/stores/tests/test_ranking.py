from decimal import Decimal
from types import SimpleNamespace

from apps.stores.application.eligibility import EligibleStore
from apps.stores.application.fulfillment import StoreFulfillmentResult
from apps.stores.application.ranking import (
    StoreRankingService,
    StoreRankingStrategyVersion,
)


def fulfillment(store_id, covered, total, ratio):
    return StoreFulfillmentResult(
        store_id=store_id,
        order_id="order-1",
        item_results=(),
        total_items=total,
        covered_items=covered,
        unresolved_items=0,
        coverage_ratio=Decimal(ratio),
        fully_covered=covered == total,
        blocking_reasons=(),
        metadata={},
    )


def test_full_coverage_beats_closer_partial_and_price_is_ignored():
    result = StoreRankingService().rank_candidates(
        SimpleNamespace(id="order-1"),
        (EligibleStore(store_id="partial", distance_meters=10), EligibleStore(store_id="full", distance_meters=100)),
        (fulfillment("partial", 1, 2, "0.5"), fulfillment("full", 2, 2, "1")),
    )

    assert [candidate.store_id for candidate in result.ranked_candidates] == ["full"]
    assert result.strategy_version == StoreRankingStrategyVersion.DETERMINISTIC_V1


def test_equal_candidates_have_stable_store_id_tie_break_and_limit():
    result = StoreRankingService().rank_candidates(
        SimpleNamespace(id="order-1"),
        (EligibleStore(store_id="b", distance_meters=10), EligibleStore(store_id="a", distance_meters=10)),
        (fulfillment("b", 1, 1, "1"), fulfillment("a", 1, 1, "1")),
        max_candidates=1,
    )

    assert [candidate.store_id for candidate in result.ranked_candidates] == ["a"]
    assert result.ranked_candidates[0].snapshot()["rank"] == 1
