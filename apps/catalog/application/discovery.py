"""Authoritative marketplace product discovery over real available offers."""

from collections import defaultdict

from django.db.models import Q

from apps.catalog.application.grounding import (
    CatalogRetriever,
    _normalize_variant_hint,
    _variant_hint_matches,
)
from apps.catalog.domain.discovery import (
    ProductSearchHit,
    ProductSearchQuery,
    ProductSearchResult,
)
from apps.catalog.domain.enums import (
    CategoryStatus,
    ProductAvailability,
    ProductStatus,
    ProductVariantStatus,
)
from apps.catalog.domain.grounding import CatalogMatchStrategy
from apps.catalog.domain.services import normalize_product_name
from apps.catalog.infrastructure.models import Category, Product, StoreProduct
from apps.stores.domain.enums import StoreStatus


class ProductDiscoveryService:
    """Search catalog offers; language-model output is only used as search hints."""

    maximum_page_size = 20

    def __init__(self, *, retriever=None) -> None:
        self.retriever = retriever or CatalogRetriever()

    def search(self, query: ProductSearchQuery) -> ProductSearchResult:
        limit = max(1, min(int(query.limit), self.maximum_page_size))
        cursor = max(0, int(query.cursor))
        offers = self._available_offers(query.currency)
        product_hint = normalize_product_name(query.product_hint or query.text)
        category_ids = self._category_ids(query.category_hint)

        if category_ids:
            offers = offers.filter(product__category_id__in=category_ids)
        elif query.category_hint:
            return ProductSearchResult(
                query=query, products=(), total=0, next_cursor=None
            )

        if product_hint:
            concept_category_ids = self._category_ids(query.product_hint or query.text)
            if concept_category_ids:
                offers = offers.filter(product__category_id__in=concept_category_ids)
            else:
                direct = offers.filter(
                    Q(product__normalized_name__icontains=product_hint)
                    | Q(product__aliases__normalized_alias__icontains=product_hint)
                    | Q(
                        display_name__icontains=(
                            query.product_hint or query.text
                        ).strip()
                    )
                )
                if direct.exists():
                    offers = direct
                else:
                    candidate_ids = [
                        candidate.product_id
                        for candidate in self.retriever.retrieve(
                            query.product_hint or query.text
                        )
                        if candidate.matched_on != CatalogMatchStrategy.FUZZY_NAME
                        or candidate.score >= self.retriever.policy.fuzzy_match_score
                    ]
                    offers = offers.filter(product_id__in=candidate_ids)

        if query.budget_min is not None:
            offers = offers.filter(price__isnull=False, price__gte=query.budget_min)
        if query.budget_max is not None:
            offers = offers.filter(price__isnull=False, price__lte=query.budget_max)

        all_rows = list(
            offers.distinct().order_by("product__name", "product_variant__name", "id")
        )
        variant_rows = self._matching_variant_rows(all_rows, query.variant_hint)
        variant_suggestions: tuple[ProductSearchHit, ...] = ()
        if query.variant_hint and not variant_rows and all_rows:
            variant_suggestions = tuple(self._aggregate(all_rows))
            rows = []
        else:
            rows = variant_rows if query.variant_hint else all_rows

        hits = sorted(
            self._aggregate(rows),
            key=lambda hit: self._rank_key(hit, product_hint),
        )
        total = len(hits)
        page = tuple(hits[cursor : cursor + limit])
        next_cursor = cursor + len(page) if cursor + len(page) < total else None
        known_unavailable = False
        if not hits and product_hint:
            known_unavailable = self._known_product_exists(
                query.product_hint or query.text
            )
        return ProductSearchResult(
            query=query,
            products=page,
            total=total,
            next_cursor=next_cursor,
            known_but_unavailable=known_unavailable,
            variant_suggestions=variant_suggestions,
        )

    @staticmethod
    def _available_offers(currency: str | None):
        queryset = (
            StoreProduct.objects.filter(
                store__status=StoreStatus.ACTIVE,
                product__status=ProductStatus.ACTIVE,
                availability_status=ProductAvailability.AVAILABLE,
            )
            .filter(
                Q(product_variant__isnull=True)
                | Q(product_variant__status=ProductVariantStatus.ACTIVE)
            )
            .select_related("product", "product__category", "product_variant")
        )
        return queryset.filter(currency=currency) if currency else queryset

    @staticmethod
    def _category_ids(hint: str | None) -> list:
        normalized = normalize_product_name(hint or "")
        if not normalized:
            return []
        categories = list(
            Category.objects.filter(status=CategoryStatus.ACTIVE).only(
                "id", "name", "parent_id"
            )
        )
        matched = {
            item.id
            for item in categories
            if normalized in normalize_product_name(item.name)
            or normalize_product_name(item.name) in normalized
        }
        # A parent category query includes its real active descendants.
        changed = True
        while changed:
            before = len(matched)
            matched.update(item.id for item in categories if item.parent_id in matched)
            changed = len(matched) != before
        return list(matched)

    @staticmethod
    def _matching_variant_rows(rows, hint):
        normalized_hint = _normalize_variant_hint(hint)
        if not normalized_hint:
            return rows
        return [
            row
            for row in rows
            if row.product_variant is not None
            and _variant_hint_matches(
                normalized_hint,
                _normalize_variant_hint(row.product_variant.name),
            )
        ]

    @staticmethod
    def _aggregate(rows) -> list[ProductSearchHit]:
        grouped = defaultdict(list)
        representatives = {}
        for row in rows:
            key = (row.product_id, row.product_variant_id, row.currency)
            grouped[key].append(row.price)
            representatives[key] = row
        hits = []
        for key, prices in grouped.items():
            row = representatives[key]
            known_prices = sorted(price for price in prices if price is not None)
            hits.append(
                ProductSearchHit(
                    product_id=row.product_id,
                    product_name=row.product.name,
                    category_name=row.product.category.name,
                    variant_id=row.product_variant_id,
                    variant_name=(
                        row.product_variant.name if row.product_variant else None
                    ),
                    minimum_price=known_prices[0] if known_prices else None,
                    maximum_price=known_prices[-1] if known_prices else None,
                    currency=row.currency if known_prices else None,
                )
            )
        return sorted(
            hits,
            key=lambda item: (
                normalize_product_name(item.product_name),
                normalize_product_name(item.variant_name or ""),
                item.currency or "",
                str(item.product_id),
            ),
        )

    @staticmethod
    def _rank_key(hit: ProductSearchHit, normalized_hint: str):
        name = normalize_product_name(hit.product_name)
        if normalized_hint and name == normalized_hint:
            relevance = 0
        elif normalized_hint and name.startswith(normalized_hint):
            relevance = 1
        elif normalized_hint and normalized_hint in name:
            relevance = 2
        else:
            relevance = 3
        return (
            relevance,
            name,
            normalize_product_name(hit.variant_name or ""),
            hit.currency or "",
            str(hit.product_id),
        )

    def _known_product_exists(self, text: str) -> bool:
        normalized = normalize_product_name(text)
        if not normalized:
            return False
        if (
            Product.objects.filter(status=ProductStatus.ACTIVE)
            .filter(
                Q(normalized_name__icontains=normalized)
                | Q(aliases__normalized_alias__icontains=normalized)
            )
            .exists()
        ):
            return True
        return bool(self.retriever.retrieve(text))
