"""Channel-neutral conversation façade for authoritative product discovery."""

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.ai.domain.understanding import ExtractedItem, UserMessageUnderstanding
from apps.catalog.application.discovery import ProductDiscoveryService
from apps.catalog.domain.discovery import (
    ProductSearchHit,
    ProductSearchQuery,
    ProductSearchResult,
)
from apps.catalog.domain.services import normalize_product_name
from apps.conversations.application.context import ConversationContextService

MORE_ACTION = "product.discovery.more"
_CONTINUATION = re.compile(
    r"\b(autres?|suite|suivants?|continue|continuation)\b", re.IGNORECASE
)
_ORDINALS = {
    "premier": 0,
    "premiere": 0,
    "1er": 0,
    "1ere": 0,
    "deuxieme": 1,
    "second": 1,
    "seconde": 1,
    "2e": 1,
    "troisieme": 2,
    "3e": 2,
}


@dataclass(frozen=True, kw_only=True)
class ProductDiscoveryAction:
    action_id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class ProductDiscoveryOutcome:
    text: str
    actions: tuple[ProductDiscoveryAction, ...] = ()
    metadata: dict[str, Any] | None = None


class ProductDiscoveryConversationService:
    """Translate understood search hints into deterministic catalog responses."""

    initial_page_size = 8
    continuation_page_size = 20

    def __init__(self, *, discovery_service=None, context_service=None) -> None:
        self.discovery_service = discovery_service or ProductDiscoveryService()
        self.context_service = context_service or ConversationContextService()

    @staticmethod
    def handles_action(action_id: str | None) -> bool:
        return action_id == MORE_ACTION

    def discover(
        self,
        *,
        conversation,
        context,
        understanding: UserMessageUnderstanding,
        text: str,
        continue_previous: bool = False,
    ) -> ProductDiscoveryOutcome:
        snapshot = self._snapshot(context)
        ordinal = self._ordinal(text)
        if ordinal is not None and snapshot:
            selected = self._snapshot_hit(snapshot, ordinal)
            if selected is not None:
                return self._selected_outcome(selected)

        continuation = continue_previous or bool(_CONTINUATION.search(text or ""))
        if continuation and snapshot and snapshot.get("next_cursor") is not None:
            query = self._query_from_snapshot(
                snapshot,
                cursor=int(snapshot["next_cursor"]),
                limit=self.continuation_page_size,
            )
            result = self.discovery_service.search(query)
            return self._single_result(conversation, context, result, continuation=True)

        budget_min, budget_max = self._budgets(understanding, text)
        items = understanding.items
        if len(items) > 1:
            results = [
                self.discovery_service.search(
                    self._query_for_item(
                        item,
                        understanding,
                        budget_min=budget_min,
                        budget_max=budget_max,
                        limit=5,
                    )
                )
                for item in items
            ]
            return self._multiple_results(conversation, context, results)

        item = items[0] if items else None
        query = self._query_for_item(
            item,
            understanding,
            budget_min=budget_min,
            budget_max=budget_max,
            limit=self.initial_page_size,
        )
        result = self.discovery_service.search(query)
        return self._single_result(conversation, context, result, continuation=False)

    def continue_from_callback(self, *, conversation, context):
        snapshot = self._snapshot(context)
        if not snapshot or snapshot.get("next_cursor") is None:
            return ProductDiscoveryOutcome(
                text="Cette liste est déjà terminée.",
                metadata={"capability": "product_discovery", "authoritative": True},
            )
        query = self._query_from_snapshot(
            snapshot,
            cursor=int(snapshot["next_cursor"]),
            limit=self.continuation_page_size,
        )
        return self._single_result(
            conversation,
            context,
            self.discovery_service.search(query),
            continuation=True,
        )

    def enrich_order_understanding(self, understanding, text: str, context):
        """Resolve deictic BUY wording to server-stored real search results."""

        snapshot = self._snapshot(context)
        if not snapshot:
            return understanding
        visible = snapshot.get("visible") or []
        index = self._ordinal(text)
        selected = (
            visible[index] if index is not None and index < len(visible) else None
        )
        if selected is None:
            product_ids = {item.get("product_id") for item in visible}
            if len(product_ids) == 1 and visible:
                candidate = understanding.items[0] if understanding.items else None
                hint = candidate.variant_hint if candidate else None
                if hint:
                    normalized_hint = normalize_product_name(hint).replace(" ", "")
                    selected = next(
                        (
                            item
                            for item in visible
                            if normalized_hint
                            in normalize_product_name(
                                item.get("variant_name") or ""
                            ).replace(" ", "")
                        ),
                        visible[0],
                    )
                else:
                    selected = visible[0]
        if selected is None:
            return understanding
        original = understanding.items[0] if understanding.items else None
        grounded_reference = ExtractedItem(
            requested_name=selected["product_name"],
            quantity=original.quantity if original else None,
            unit=original.unit if original else None,
            variant_hint=(
                (original.variant_hint if original else None)
                or selected.get("variant_name")
            ),
            notes=original.notes if original else None,
        )
        return replace(understanding, items=(grounded_reference,))

    def _single_result(self, conversation, context, result, *, continuation):
        label = self._query_label(result.query)
        if not result.products:
            self._save_snapshot(
                conversation,
                context,
                result,
                visible=result.variant_suggestions,
            )
            if result.variant_suggestions:
                suggestions = "\n".join(
                    self._hit_line(index, hit)
                    for index, hit in enumerate(result.variant_suggestions[:8], 1)
                )
                text = (
                    f"Je n'ai pas trouvé le format {result.query.variant_hint} "
                    f"pour {label}. Voici les formats réellement disponibles :\n"
                    f"{suggestions}"
                )
            elif result.query.budget_max is not None:
                text = (
                    f"Je n'ai trouvé aucun {label} disponible correspondant à un "
                    f"budget maximum de {self._amount(result.query.budget_max)} FCFA."
                )
            elif result.known_but_unavailable:
                text = (
                    f"{label.capitalize()} existe dans le catalogue, mais n'est pas "
                    "disponible actuellement."
                )
            else:
                text = f"Je n'ai pas trouvé de {label} disponible actuellement."
            return ProductDiscoveryOutcome(
                text=text,
                metadata=self._metadata(result, continuation=continuation),
            )

        lines = [
            self._hit_line(index, hit) for index, hit in enumerate(result.products, 1)
        ]
        if self._is_global_query(result.query):
            heading = f"{result.total} produit(s) disponible(s) dans le catalogue :"
        elif result.query.budget_max is not None:
            heading = (
                f"Avec un budget maximum de {self._amount(result.query.budget_max)} "
                f"FCFA, j'ai trouvé {result.total} résultat(s) pour {label} :"
            )
        else:
            heading = (
                f"J'ai trouvé {result.total} résultat(s) disponible(s) pour {label} :"
            )
        if continuation:
            heading = "Voici la suite des résultats réels :"
        actions = ()
        if result.next_cursor is not None:
            remaining = result.total - result.next_cursor
            lines.append(f"{remaining} autre(s) résultat(s) sont encore disponibles.")
            actions = (
                ProductDiscoveryAction(action_id=MORE_ACTION, label="Voir les autres"),
            )
        self._save_snapshot(conversation, context, result, visible=result.products)
        return ProductDiscoveryOutcome(
            text="\n".join((heading, *lines)),
            actions=actions,
            metadata=self._metadata(result, continuation=continuation),
        )

    def _multiple_results(self, conversation, context, results):
        sections = []
        visible = []
        for result in results:
            label = self._query_label(result.query)
            if not result.products:
                sections.append(f"{label.capitalize()} : aucun résultat disponible.")
                continue
            sections.append(
                f"{label.capitalize()} :\n"
                + "\n".join(f"- {self._hit_label(hit)}" for hit in result.products)
            )
            visible.extend(result.products)
        anchor = results[0]
        self._save_snapshot(conversation, context, anchor, visible=visible)
        return ProductDiscoveryOutcome(
            text="\n\n".join(sections),
            metadata={
                "capability": "product_discovery",
                "authoritative": True,
                "multi_product": True,
                "query_count": len(results),
            },
        )

    def _save_snapshot(self, conversation, context, result, *, visible):
        payload = {
            "query": self._query_payload(result.query),
            "total": result.total,
            "next_cursor": result.next_cursor,
            "visible": [self._hit_payload(hit) for hit in visible],
        }
        updated = replace(
            context,
            metadata={**context.metadata, "product_discovery": payload},
        )
        self.context_service.update_context(conversation, updated)

    @staticmethod
    def _snapshot(context):
        value = context.metadata.get("product_discovery")
        return value if isinstance(value, dict) else None

    @staticmethod
    def _query_for_item(item, understanding, *, budget_min, budget_max, limit):
        product_hint = item.requested_name if item is not None else None
        return ProductSearchQuery(
            text=product_hint or "",
            product_hint=product_hint,
            category_hint=None,
            variant_hint=item.variant_hint if item is not None else None,
            budget_min=budget_min,
            budget_max=budget_max,
            currency=(
                "XAF" if budget_min is not None or budget_max is not None else None
            ),
            limit=limit,
        )

    @staticmethod
    def _budgets(understanding, text):
        return parse_budget(text)

    @staticmethod
    def _query_payload(query):
        return {
            "text": query.text,
            "product_hint": query.product_hint,
            "category_hint": query.category_hint,
            "variant_hint": query.variant_hint,
            "budget_min": str(query.budget_min)
            if query.budget_min is not None
            else None,
            "budget_max": str(query.budget_max)
            if query.budget_max is not None
            else None,
            "currency": query.currency,
        }

    @staticmethod
    def _query_from_snapshot(snapshot, *, cursor, limit):
        payload = snapshot["query"]
        return ProductSearchQuery(
            text=payload.get("text") or "",
            product_hint=payload.get("product_hint"),
            category_hint=payload.get("category_hint"),
            variant_hint=payload.get("variant_hint"),
            budget_min=(
                Decimal(payload["budget_min"]) if payload.get("budget_min") else None
            ),
            budget_max=(
                Decimal(payload["budget_max"]) if payload.get("budget_max") else None
            ),
            currency=payload.get("currency"),
            limit=limit,
            cursor=cursor,
        )

    @staticmethod
    def _hit_payload(hit):
        return {
            "product_id": str(hit.product_id),
            "product_name": hit.product_name,
            "variant_id": str(hit.variant_id) if hit.variant_id else None,
            "variant_name": hit.variant_name,
            "category_name": hit.category_name,
            "minimum_price": (
                str(hit.minimum_price) if hit.minimum_price is not None else None
            ),
            "maximum_price": (
                str(hit.maximum_price) if hit.maximum_price is not None else None
            ),
            "currency": hit.currency,
        }

    @staticmethod
    def _snapshot_hit(snapshot, index):
        visible = snapshot.get("visible") or []
        if index >= len(visible):
            return None
        item = visible[index]
        return ProductSearchHit(
            product_id=item["product_id"],
            product_name=item["product_name"],
            category_name=item["category_name"],
            variant_id=item.get("variant_id"),
            variant_name=item.get("variant_name"),
            minimum_price=(
                Decimal(item["minimum_price"]) if item.get("minimum_price") else None
            ),
            maximum_price=(
                Decimal(item["maximum_price"]) if item.get("maximum_price") else None
            ),
            currency=item.get("currency"),
        )

    def _selected_outcome(self, hit):
        return ProductDiscoveryOutcome(
            text=f"Votre sélection : {self._hit_label(hit)}.",
            metadata={
                "capability": "product_discovery",
                "authoritative": True,
                "selected_product_id": str(hit.product_id),
                "selected_variant_id": str(hit.variant_id) if hit.variant_id else None,
            },
        )

    @classmethod
    def _hit_line(cls, index, hit):
        return f"{index}. {cls._hit_label(hit)}"

    @classmethod
    def _hit_label(cls, hit):
        label = hit.product_name
        if hit.variant_name:
            label += f" — {hit.variant_name}"
        if hit.minimum_price is not None:
            currency = "FCFA" if hit.currency == "XAF" else (hit.currency or "")
            if hit.maximum_price is not None and hit.maximum_price != hit.minimum_price:
                label += (
                    f" — {cls._amount(hit.minimum_price)} à "
                    f"{cls._amount(hit.maximum_price)} {currency}"
                )
            else:
                label += f" — {cls._amount(hit.minimum_price)} {currency}"
        return label

    @staticmethod
    def _amount(value):
        integral = value.quantize(Decimal("1"))
        return f"{integral:,.0f}".replace(",", " ")

    @staticmethod
    def _query_label(query):
        return query.product_hint or query.category_hint or "produits"

    @staticmethod
    def _is_global_query(query):
        return not query.product_hint and not query.category_hint

    @staticmethod
    def _ordinal(text):
        normalized = normalize_product_name(text or "")
        for token, index in _ORDINALS.items():
            if re.search(rf"\b{re.escape(token)}\b", normalized):
                return index
        return None

    @staticmethod
    def _metadata(result: ProductSearchResult, *, continuation):
        return {
            "capability": "product_discovery",
            "authoritative": True,
            "product_count": result.total,
            "next_cursor": result.next_cursor,
            "continuation": continuation,
            "no_results": not bool(result.products),
        }


def parse_budget(text: str) -> tuple[Decimal | None, Decimal | None]:
    """Parse common FCFA budget wording without trusting it as a catalog fact."""

    source = text or ""
    normalized = normalize_product_name(source).replace("fcfa", "")
    amounts = [
        _parse_amount(match.group(0))
        for match in re.finditer(r"\d[\d\s.,]*(?:k|mille)?", normalized)
    ]
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return None, None
    if re.search(r"\bentre\b", normalized) and len(amounts) >= 2:
        return min(amounts[0], amounts[1]), max(amounts[0], amounts[1])
    # A stated spending amount, maximum or approximate target is a hard upper
    # bound for filtering; only real priced offers at or below it are returned.
    explicit_budget = re.search(
        r"\b(budget|j ai|jai|maximum|max|moins de|pas plus|autour|environ)\b",
        normalized,
    )
    compact_source = source.casefold().replace(" ", "")
    abbreviated_amount = bool(re.search(r"\d+(?:[.,]\d+)?k\b", compact_source))
    if explicit_budget or abbreviated_amount or "fcfa" in source.casefold():
        return None, amounts[0]
    return None, None


def _parse_amount(value: str) -> Decimal | None:
    compact = value.casefold().strip()
    multiplier = (
        Decimal("1000") if compact.endswith("k") or "mille" in compact else Decimal("1")
    )
    compact = re.sub(r"(?:k|mille)$", "", compact).strip()
    compact = compact.replace(" ", "").replace(",", ".")
    try:
        return Decimal(compact) * multiplier
    except InvalidOperation:
        return None
