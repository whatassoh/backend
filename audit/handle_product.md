# Product Grounding & Store Matching Diagnostic

## 1. Observed architecture

Flux réel : texte entrant → `ConversationalOrchestrator._process` →
`MessageUnderstandingService.understand_normalized` → `CatalogGroundingService`
→ `OrderBuildingService.add` / `add_order_item` → `OrderReviewService` →
`confirm_order`. Le matching est séparé : `StoreMatchingOrchestrator.start_matching`
→ éligibilité → fulfillment → ranking → une offre, ou épuisement.

## 2. AI understanding

Le schéma et `ExtractedItem` ne contiennent que `requested_name`, `quantity`, `unit`
et `notes`. Il n'y a ni `product_id` ni `variant_id`. Le LLM ne peut donc pas être
l'autorité d'un Product ID ; les IDs proviennent exclusivement du grounding PostgreSQL.

## 3. Empty Product catalog

Si `Product.objects.count() == 0`, chaque item retourne réellement :

| status | product_id | variant_id | candidates | reason |
| --- | --- | --- | --- | --- |
| `UNMATCHED` | `None` | `None` | `()` | `NO_CANDIDATE` |

Les noms réels sont `OrderItemResolutionStatus.UNMATCHED` et
`CatalogResolutionReason.NO_CANDIDATE`; aucun produit n'est inventé.

## 4. OrderItem persistence

Avec quantité et unité, un UNMATCHED est créé : `requested_name` extrait, Product et
variant nuls, `resolution_status=UNMATCHED`. `MATCHED` exige un Product ; UNMATCHED
exige l'absence de Product ; un variant exige un Product. AMBIGUOUS avec Product nul
est permis. Preuves : contraintes `orders_item_matched_has_product`,
`orders_item_unmatched_no_product`, `validate_order_item_resolution`, et
`test_unmatched_order_item_without_catalog_product_is_valid`.

## 5. Why the recap can display products

`OrderReviewService._item` construit `display_label` depuis `quantity`, `unit` et
**`OrderItem.requested_name`**. Le recap peut donc afficher huile/riz sans Product.

## 6. Confirmation readiness

`OrderReviewService.build_review` bloque : statut hors DRAFT/AWAITING_LOCATION,
absence d'item ou de location, clarification pending, et item AMBIGUOUS. UNMATCHED
est seulement le warning `unmatched_items`.

| condition | bloque confirmation |
| --- | --- |
| UNMATCHED | NON |
| AMBIGUOUS | OUI |
| product_id NULL seul | NON |

`confirm_order` appelle `ensure_order_confirmable`: statut, location, items et
`item.full_clean()` seulement. Il ne valide ni Product actif, ni variant, ni
StoreProduct, disponibilité ou couverture.

## 7. Meaning of “Order confirmed”

CONFIRMED signifie demande persistée, localisée, avec items valides et non ambigus. Il
ne garantit pas l'existence d'un Product, qu'un Store le vende, ni qu'un Store unique
puisse fournir la commande.

## 8. Store product model

`StoreProduct` relie Store et Product, avec variant/prix optionnels et
`availability_status`. AVAILABLE est une disponibilité déclarée, non un stock, une
réservation, ou une conversion d'unités.

## 9. Store matching input

`StoreFulfillmentService` utilise `resolution_status`, `product_id` et
`product_variant_id`; il cherche StoreProduct par `(store_id, product_id, variant_id)`.
Il ne fait ni matching textuel sur `requested_name`, ni re-grounding.

## 10. Full coverage

Full coverage signifie que chaque item a un StoreProduct AVAILABLE. Le ratio est
`covered_items / total_items`. Les partiels sont seulement des diagnostics : le ranking
ne rend offerable que `candidate.fully_covered`. Une boutique partielle reçoit une
offre : **NON**. Preuve : `StoreRankingService.rank_candidates` et
`test_full_coverage_beats_closer_partial_and_price_is_ignored`.

## 11. One-order-one-store invariant

Implémenté : **OUI**. Le ranking filtre les candidats full-coverage et
`start_matching` choisit un seul Store pour un seul StoreOffer. Aucun code ne
partitionne les items; deux stores partiels ne produisent pas de split order.

## 12. UNMATCHED OrderItem during Store Matching

`_evaluate_item` retourne immédiatement `covered=False`, motif
`ORDER_ITEM_UNMATCHED`, sans requête StoreProduct. Chaque Store est incomplet, le
ranking n'a aucun candidat offerable. C'est le cas A : pas de Product ID, donc pas de
couverture; aucun matching textuel, re-grounding, item ignoré ou exception.

## 13. Product exists / StoreProduct absent

Un item MATCHED sans StoreProduct produit `PRODUCT_NOT_CARRIED` ou
`VARIANT_NOT_CARRIED`. Aucun Store n'est full-coverage. Lors d'un matching exécuté,
la tentative devient EXHAUSTED et `fail_order` utilise `NO_STORE_AVAILABLE`.

## 14. Full and partial stores

Riz + Huile : Store A avec les deux StoreProducts AVAILABLE couvre 2/2 et est
offerable; Store B avec Riz seul couvre 1/2, diagnostic seulement. Deux stores
partiels (A/Riz, B/Huile) donnent zéro candidat offerable. Split performed : **NON**.

## 15. No matching Store

Sur appel explicite à `start_matching` : CONFIRMED → MATCHING_STORES → snapshot vide
→ tentative EXHAUSTED → Order FAILED avec `failure_code=NO_STORE_AVAILABLE`.

Notification client : **NON confirmée**; aucun chemin de notification client de cet
échec n'a été trouvé. `confirm_order` publie `OrderConfirmed`, mais ce checkout ne
contient aucun subscriber non-test appelant `start_matching`. Une confirmation seule
reste donc CONFIRMED jusqu'à l'appel par un service ou une tâche externe.

## 16. Three-level model

Niveau 1 — IA : comprend une demande structurée; ne prouve ni Product ni Store.

Niveau 2 — grounding : associe éventuellement un Product PostgreSQL MATCHED ou marque
AMBIGUOUS/UNMATCHED; ne prouve pas qu'un Store le vend.

Niveau 3 — fulfillment : vérifie StoreProduct AVAILABLE, pour chaque item d'un même
Store. Seule une couverture 100 % est offerable.

## 17. Exact current flow

```text
Texte → deux ExtractedItem → deux UNMATCHED / NO_CANDIDATE si Product est vide
→ deux OrderItem(product=NULL, UNMATCHED) → demande de location → recap
→ order.confirm → CONFIRMED → OrderConfirmed publié
```

`MATCHING_STORES` n'est atteint que lorsque
`StoreMatchingOrchestrator.start_matching` est invoqué. Les UNMATCHED mènent alors à
zéro Store full-coverage puis FAILED / NO_STORE_AVAILABLE. Aucun code applicatif,
modèle, migration, règle ou prompt n'a été modifié pour ce diagnostic.
