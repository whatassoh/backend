import logging
import re
from dataclasses import dataclass
from time import monotonic

from django.contrib.postgres.search import TrigramSimilarity
from django.db import connection
from django.db.models import F, Q, Value
from django.db.models.functions import Concat

from apps.ai.domain.understanding import ExtractedItem
from apps.catalog.domain.enums import (
    ProductAvailability,
    ProductStatus,
    ProductVariantStatus,
)
from apps.catalog.domain.grounding import (
    CatalogCandidate,
    CatalogMatchStrategy,
    CatalogResolution,
    CatalogResolutionReason,
)
from apps.catalog.domain.services import normalize_product_name
from apps.catalog.infrastructure.models import Product, ProductVariant, StoreProduct
from apps.orders.domain.enums import OrderItemResolutionStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class CatalogGroundingPolicy:
    candidate_limit: int = 5
    fuzzy_min_score: float = 0.55
    fuzzy_match_score: float = 0.82
    minimum_score_margin: float = 0.10
    exact_conflict_score: float = 0.96


class CatalogRetriever:
    """Database-backed candidate retrieval; it never creates catalog records."""

    def __init__(self, policy: CatalogGroundingPolicy | None = None) -> None:
        self.policy = policy or CatalogGroundingPolicy()

    def retrieve(self, requested_name: str) -> list[CatalogCandidate]:
        normalized = normalize_product_name(requested_name)
        if not normalized:
            return []
        candidates = self._exact_products(normalized)
        candidates += self._exact_aliases(normalized)
        candidates += self._exact_display_names(normalized)
        candidates += self._exact_variants(normalized)
        if not candidates and connection.vendor == "postgresql":
            candidates = self._fuzzy_products(normalized)
        return _deduplicate_and_limit(candidates, self.policy.candidate_limit)

    def _products(self):
        return Product.objects.filter(status=ProductStatus.ACTIVE).select_related(
            "category"
        )

    def _exact_products(self, normalized: str) -> list[CatalogCandidate]:
        return [
            _product_candidate(product, CatalogMatchStrategy.EXACT_CANONICAL, 1.0)
            for product in self._products().filter(normalized_name=normalized)
        ]

    def _exact_aliases(self, normalized: str) -> list[CatalogCandidate]:
        aliases = (
            self._products()
            .filter(aliases__normalized_alias=normalized)
            .values("id", "name", "category__name", "aliases__alias")
        )
        return [
            CatalogCandidate(
                product_id=row["id"],
                canonical_name=row["name"],
                matched_on=CatalogMatchStrategy.EXACT_ALIAS,
                score=0.98,
                matched_alias=row["aliases__alias"],
                category_name=row["category__name"],
            )
            for row in aliases
        ]

    def _exact_display_names(self, normalized: str) -> list[CatalogCandidate]:
        offers = (
            StoreProduct.objects.filter(
                product__status=ProductStatus.ACTIVE,
                availability_status=ProductAvailability.AVAILABLE,
            )
            .select_related("product__category")
            .filter(display_name__iexact=normalized)
        )
        return [
            _product_candidate(
                offer.product, CatalogMatchStrategy.EXACT_DISPLAY_NAME, 0.96
            )
            for offer in offers
        ]

    def _exact_variants(self, normalized: str) -> list[CatalogCandidate]:
        variants = (
            ProductVariant.objects.filter(
                status=ProductVariantStatus.ACTIVE, product__status=ProductStatus.ACTIVE
            )
            .select_related("product__category")
            .annotate(
                full_name=Concat(
                    F("product__normalized_name"), Value(" "), F("normalized_name")
                )
            )
            .filter(Q(normalized_name=normalized) | Q(full_name=normalized))
        )
        return [
            _product_candidate(
                variant.product,
                CatalogMatchStrategy.EXACT_VARIANT,
                1.0,
                variant_id=variant.id,
            )
            for variant in variants
        ]

    def _fuzzy_products(self, normalized: str) -> list[CatalogCandidate]:
        products = (
            self._products()
            .annotate(similarity=TrigramSimilarity("normalized_name", normalized))
            .filter(similarity__gte=self.policy.fuzzy_min_score)
            .order_by("-similarity", "name")[: self.policy.candidate_limit]
        )
        return [
            _product_candidate(
                product, CatalogMatchStrategy.FUZZY_NAME, float(product.similarity)
            )
            for product in products
        ]


class CatalogResolver:
    def __init__(self, policy: CatalogGroundingPolicy | None = None) -> None:
        self.policy = policy or CatalogGroundingPolicy()

    def resolve(
        self, requested_name: str, candidates: list[CatalogCandidate]
    ) -> CatalogResolution:
        if not candidates:
            return CatalogResolution(
                status=OrderItemResolutionStatus.UNMATCHED,
                requested_name=requested_name,
                product_id=None,
                variant_id=None,
                candidates=(),
                reason=CatalogResolutionReason.NO_CANDIDATE,
            )
        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        exact = best.matched_on != CatalogMatchStrategy.FUZZY_NAME
        if not exact and best.score < self.policy.fuzzy_match_score:
            return CatalogResolution(
                status=OrderItemResolutionStatus.UNMATCHED,
                requested_name=requested_name,
                product_id=None,
                variant_id=None,
                candidates=tuple(candidates),
                reason=CatalogResolutionReason.BELOW_THRESHOLD,
                score=best.score,
            )
        is_clear = (
            exact
            and (second is None or second.score < self.policy.exact_conflict_score)
        ) or (
            not exact
            and (
                second is None
                or best.score - second.score >= self.policy.minimum_score_margin
            )
        )
        if not is_clear:
            reason = (
                CatalogResolutionReason.MULTIPLE_STRONG_CANDIDATES
                if second
                else CatalogResolutionReason.BELOW_THRESHOLD
            )
            return CatalogResolution(
                status=OrderItemResolutionStatus.AMBIGUOUS,
                requested_name=requested_name,
                product_id=None,
                variant_id=None,
                candidates=tuple(candidates),
                reason=reason,
                score=best.score,
            )
        if (
            best.variant_id is None
            and ProductVariant.objects.filter(
                product_id=best.product_id, status=ProductVariantStatus.ACTIVE
            ).exists()
        ):
            return CatalogResolution(
                status=OrderItemResolutionStatus.AMBIGUOUS,
                requested_name=requested_name,
                product_id=None,
                variant_id=None,
                candidates=tuple(candidates),
                reason=CatalogResolutionReason.VARIANT_SELECTION_REQUIRED,
                score=best.score,
            )
        reason = (
            CatalogResolutionReason.ALIAS_MATCH
            if best.matched_on == CatalogMatchStrategy.EXACT_ALIAS
            else CatalogResolutionReason.FUZZY_MATCH
            if not exact
            else CatalogResolutionReason.EXACT_MATCH
        )
        return CatalogResolution(
            status=OrderItemResolutionStatus.MATCHED,
            requested_name=requested_name,
            product_id=best.product_id,
            variant_id=best.variant_id,
            candidates=tuple(candidates),
            reason=reason,
            score=best.score,
        )


class CatalogGroundingService:
    def __init__(
        self,
        retriever: CatalogRetriever | None = None,
        resolver: CatalogResolver | None = None,
    ) -> None:
        policy = CatalogGroundingPolicy()
        self.retriever = retriever or CatalogRetriever(policy)
        self.resolver = resolver or CatalogResolver(policy)

    def ground_item(self, item: ExtractedItem) -> CatalogResolution:
        started = monotonic()
        candidates = self.retriever.retrieve(item.requested_name)
        resolution = self.resolver.resolve(item.requested_name, candidates)
        if (
            item.variant_hint
            and resolution.status == OrderItemResolutionStatus.AMBIGUOUS
            and resolution.reason == CatalogResolutionReason.VARIANT_SELECTION_REQUIRED
            and candidates
        ):
            resolution = self._resolve_variant(item, candidates[0], resolution)
        logger.info(
            "catalog_item_grounded",
            extra={
                "normalized_query": normalize_product_name(item.requested_name),
                "status": resolution.status,
                "candidate_count": len(candidates),
                "match_strategy": candidates[0].matched_on if candidates else None,
                "score": resolution.score,
                "duration_ms": round((monotonic() - started) * 1000),
            },
        )
        return resolution

    @staticmethod
    def _resolve_variant(item, product_candidate, base_resolution):
        variants = list(
            ProductVariant.objects.filter(
                product_id=product_candidate.product_id,
                status=ProductVariantStatus.ACTIVE,
            ).order_by("normalized_name", "id")
        )
        hint = _normalize_variant_hint(item.variant_hint)
        matches = [
            variant for variant in variants
            if _variant_hint_matches(hint, _normalize_variant_hint(variant.name))
        ]
        candidates = tuple(
            CatalogCandidate(
                product_id=product_candidate.product_id,
                variant_id=variant.id,
                canonical_name=f"{product_candidate.canonical_name} — {variant.name}",
                matched_on=CatalogMatchStrategy.EXACT_VARIANT,
                score=1.0,
                metadata={"variant_label": variant.name},
            )
            for variant in variants
        )
        if len(matches) == 1:
            return CatalogResolution(
                status=OrderItemResolutionStatus.MATCHED,
                requested_name=item.requested_name,
                product_id=product_candidate.product_id,
                variant_id=matches[0].id,
                candidates=candidates,
                reason=CatalogResolutionReason.EXACT_MATCH,
                score=base_resolution.score,
            )
        return CatalogResolution(
            status=OrderItemResolutionStatus.UNMATCHED,
            requested_name=item.requested_name,
            product_id=product_candidate.product_id,
            variant_id=None,
            candidates=candidates,
            reason=CatalogResolutionReason.VARIANT_NOT_FOUND,
            score=base_resolution.score,
        )

    def ground_items(self, items: tuple[ExtractedItem, ...]) -> list[CatalogResolution]:
        return [self.ground_item(item) for item in items]


def _product_candidate(product, strategy, score, variant_id=None):
    return CatalogCandidate(
        product_id=product.id,
        canonical_name=product.name,
        matched_on=strategy,
        score=score,
        variant_id=variant_id,
        category_name=product.category.name,
    )


def _deduplicate_and_limit(candidates, limit):
    unique = {}
    for candidate in candidates:
        key = (candidate.product_id, candidate.variant_id)
        if key not in unique or candidate.score > unique[key].score:
            unique[key] = candidate
    return sorted(
        unique.values(), key=lambda c: (-c.score, c.canonical_name, str(c.product_id))
    )[:limit]


def _normalize_variant_hint(value: str | None) -> str:
    """Small deterministic equivalence for catalogue variant labels."""
    normalized = (value or "").casefold()
    normalized = re.sub(r"(\d)\s*(millilitres?|ml)\b", r"\1 ml", normalized)
    normalized = re.sub(r"(\d)\s*(litres?|l)\b", r"\1 l", normalized)
    normalized = re.sub(r"(\d)\s*(kilogrammes?|kgs?|kg)\b", r"\1 kg", normalized)
    normalized = normalize_product_name(normalized)
    normalized = normalized.replace("litres", "l").replace("litre", "l")
    normalized = normalized.replace("kilogrammes", "kg").replace("kilogramme", "kg")
    normalized = normalized.replace("millilitres", "ml").replace("millilitre", "ml")
    normalized = normalized.replace("taille", "size")
    return " ".join(normalized.split())


def _variant_hint_matches(hint: str, variant_name: str) -> bool:
    """Match a short normalized format against a real product variant only."""
    if not hint:
        return False
    compact_hint = re.sub(r"[^a-z0-9]", "", hint)
    compact_variant = re.sub(r"[^a-z0-9]", "", variant_name)
    if hint == variant_name or compact_hint == compact_variant:
        return True
    # Packaging words are catalogue presentation, not part of the requested
    # format: ``5kg`` must resolve ``Sac 5 kg``.
    hint_measure = re.search(r"\d+\s*(?:ml|l|kg)\b", hint)
    variant_measure = re.search(r"\d+\s*(?:ml|l|kg)\b", variant_name)
    return bool(
        hint_measure
        and variant_measure
        and hint_measure.group().replace(" ", "")
        == variant_measure.group().replace(" ", "")
    )
