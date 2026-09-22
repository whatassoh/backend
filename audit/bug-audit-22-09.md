# Audit d'intégration conversationnelle — 22-09-2026

## Périmètre

Audit statique du repository : adaptateurs Telegram/WhatsApp, ingestion,
orchestrateur, onboarding vendeur, commandes client et sortie provider. Aucun
correctif applicatif n'a été appliqué. La base et les journaux de production ne sont
pas accessibles dans ce checkout; les valeurs réelles doivent être vérifiées en base.

## Synthèse des causes racines confirmées

1. **Critique — callbacks WhatsApp perdus par le routeur.**
   `apps/whatsapp/adapters/inbound.py:_interactive_metadata` stocke l'ID sous
   `metadata["whatsapp"]["action_id"]`. Les routeurs lisent
   `message.metadata.get("action_id")`. Une action WhatsApp devient donc un message
   sans action. En seller onboarding `REVIEW`, le fallback re-rend la review : cela
   explique la boucle Modifier/Annuler.
2. **Critique — location client : mutation suivie d'un `TypeError` avant outbound.**
   `ResponseAction` est une dataclass `kw_only=True`, mais `_handle_location` appelle
   `ResponseAction("order.skip_delivery_instructions", "Non, continuer")`. La
   position et le contexte sont écrits avant cette ligne ; l'exception remonte, aucun
   outcome n'est délivré.
3. **Élevé — choix de catalogue non persisté.** `seller.catalog.chat` et
   `seller.catalog.dashboard` ne font que retourner `catalog_setup_choice` dans les
   métadonnées de l'outcome. Les vieux boutons restent donc exécutables indéfiniment.

## A — onboarding vendeur et actions structurées

### IDs réellement émis

| Libellé | action_id |
| --- | --- |
| Créer ma boutique | `seller_onboarding.confirm` |
| Modifier | `seller_onboarding.edit` |
| Annuler | `seller_onboarding.cancel` |
| Dans le chat | `seller.catalog.chat` |
| Via mon espace vendeur | `seller.catalog.dashboard` |

Ils sont générés dans `SellerOnboardingService.review` et `_catalog_choice`.
Telegram les rend dans `callback_data` et les normalise correctement à
`metadata.action_id` dans `_parse_callback_query`. WhatsApp les rend aussi comme IDs
de boutons mais les normalise seulement dans le sous-dictionnaire `whatsapp`.

### Trace des trois actions de review

| Étape | Telegram avec ce code | WhatsApp avec ce code |
| --- | --- | --- |
| Callback → NormalizedInboundMessage | `metadata.action_id` présent | seulement `metadata.whatsapp.action_id` |
| Persistences | `process_*_webhook` crée `MessageEvent`, puis `ingest_inbound_message` crée `ConversationMessage` | identique |
| Routeur | `_process` → `SellerOnboardingService.handle` → `handle_action` | `handle` croit qu'il n'y a pas d'action |
| Modifier | `REVIEW` → draft `NAME`, flow `SELLER_ONBOARDING` | fallback `_prompt_for(REVIEW)` → même review |
| Annuler | `flow=IDLE`, `seller_onboarding=None`, autres clés conservées | fallback → même review |
| Créer | `confirm()` verrouille, valide SELL/REVIEW/draft, crée Store + membership, stage `COMPLETED` | fallback → pas de création |
| Renderer/outbound | `_from_seller_onboarding` → `ResponsePlan` → `deliver_outcome_response` → provider | même, mais réponse fallback |

La création est atomique et idempotente pour un double confirm : conversation et
utilisateur sont verrouillés, une membership existante est réutilisée. L'UX d'édition
MVP existe déjà : `NAME → LOCATION → CITY → REVIEW`; ne pas en créer une seconde.

Le code Telegram de ce checkout ne reproduit pas la boucle : `edit`/`cancel` mutent
bien le draft. Si elle est observée sur Telegram, inspecter le
`MessageEvent.metadata.action_id` et le commit déployé : le callback reçu ou le code
de production diffère de ce chemin statique.

### Stale callbacks

Après cancel, un vieux confirm ne crée pas de Store (draft absent → `INVALID`). Après
create, un vieux confirm ne duplique pas le Store mais réaffiche le choix catalogue.
En `COMPLETED`, `handle` laisse les actions catalogue passer, puis `handle_action`
accepte encore `edit` et `cancel`; les anciens boutons peuvent donc repartir au nom ou
effacer le draft. La validation action/état doit être centralisée : REVIEW uniquement
pour confirm/edit/cancel; COMPLETED + choix non consommé uniquement pour le catalogue.
Toute autre combinaison doit être non mutante et explicitement stale.

Cancel ne vide pas le contexte global, ce qui est correct, mais n'affiche pas le
sélecteur de parcours et laisse `active_journey=SELL`. Le comportement MVP attendu est
de conserver le reste du contexte et de retourner le sélecteur.

## B — catalogue

`SellerOnboardingService.handle_action` ne persiste pas le mode choisi et ne lance pas
de workflow catalogue. Les deux IDs ont donc aujourd'hui le même effet : réponse
textuelle temporaire. Il faut persister/consommer l'étape dans l'état métier ou dans
un marqueur temporaire minimal, puis interdire la mutation lors du rejeu; le changement
ultérieur de mode doit relever d'un parcours explicite de gestion catalogue.

## C — achat avec catalogue vide

Chemin nominal : webhook provider → parseur → `MessageEvent` →
`ingest_inbound_message`/`ConversationMessage` →
`process_inbound_conversation` → `ConversationalOrchestrator` → extraction IA →
`CatalogGroundingService.ground_items` → `ActiveCustomerOrderService` → Order/Item →
clarification/outcome → `deliver_outcome_response` → provider.

Avec zéro Product, `CatalogResolver.resolve` renvoie `UNMATCHED`, `product_id=None`,
raison `NO_CANDIDATE`; aucun produit fictif n'est créé. Si quantité+unité sont
extraites, `_handle_order_items` crée un OrderItem `UNMATCHED`, passe l'Order à
`AWAITING_LOCATION`, persiste `MISSING_LOCATION` et construit « Où souhaitez-vous
être livré ? ». Si elles manquent, il construit une clarification quantité/unité.
Dans les deux cas, **UNMATCHED n'est pas silence**. La review traite UNMATCHED comme
warning, pas comme blocker.

Le « Hello → préciser location » est cohérent avec un Order déjà muté et la recovery
au greeting (`ConversationRecoveryService`), mais le checkout seul ne permet pas de
prouver l'intention IA, l'item, l'Order, les blockers ou un éventuel échec provider du
test réel. À relever anonymisé : MessageEvent/ConversationMessage entrants, context
(`active_order_id`, clarification), Order/OrderItem, statut de résolution, outbound
event/message et logs de corrélation.

## D — location client

Telegram et WhatsApp créent tous deux un `MessageLocation` depuis latitude/longitude.
Le vendeur fonctionne car l'onboarding est prioritaire; ce n'est pas une panne de
parsing Telegram. Pour BUY, `_process` va à `_handle_location`, qui récupère le bon
ordre via `ActiveCustomerOrderService` (pas de `.first()` arbitraire), construit
correctement `Point(longitude, latitude)`, appelle `set_delivery_location`, puis écrit
le contexte des instructions livreur.

La construction du bouton suivante lève toutefois `TypeError` avant outcome/outbound.
Même défaut dans les appels positionnels : `order.abandon_and_start_new`, recovery
`order.resume` / `order.start_new`, et `_recap` (confirm/modify/change-location/cancel).
Tous doivent employer `ResponseAction(action_id=..., label=...)`. C'est un défaut
commun aux canaux. Après correction, la suite actuelle est : position enregistrée →
question d'instructions avec bouton « Non, continuer » → recap/review; elle ne
recalcule pas immédiatement la readiness après la location.

## Outbound, contexte et précédence

`deliver_outcome_response` n'envoie qu'une réponse non vide; `send_response_plan`
choisit texte/interactif et persiste l'outbound après l'appel provider. Les erreurs
provider connues sont capturées, mais les exceptions de programmation du routeur ne le
sont pas : elles produisent le silence observé sans event outbound.

Précédence réelle dans `_process` : onboarding vendeur → onboarding driver → catalogue
vendeur → location native → instructions livraison → action structurée (offre driver,
delivery, journey, disponibilité, order) → disponibilité textuelle driver → IA/intention
→ journey/recovery/knowledge/commande. Les boutons court-circuitent le LLM si l'ID est
normalisé.

Contexte typé : `flow`, `active_order_id`, `pending_clarification`,
`last_understanding`, `processed_message_ids`, `active_journey`,
`seller_onboarding`, `driver_onboarding`, `pending_seller_catalog_mutation`,
`metadata.awaiting_delivery_instructions`. `active_journey` est distinct des rôles
permanents; aucun routage automatique SELL→BUY n'est présent. Les changements BUY,
SELL et DELIVER sont centralisés dans `JourneyRoutingService`.

## Correctifs et régressions à ajouter

1. Normaliser WhatsApp vers `metadata.action_id` en conservant les détails sous
   `metadata.whatsapp`; tester actions seller/catalogue/order/journey sur les canaux.
2. Remplacer chaque `ResponseAction` positionnel par des mots-clés; tester location
   client → `Order.delivery_location` → outbound interactif persistant/envoyé, avec
   l'ordre longitude/latitude.
3. Ajouter les gardes atomiques action+stage vendeur et une réponse stale non mutante;
   tester vieux create/edit/cancel après cancel et après create.
4. Persister/consommer le choix catalogue; tester que le second vieux bouton ne change
   rien.
5. Ajouter des logs structurés : corrélation, canal, type, journey, stage, statut
   Order, grounding, outcome et résultat outbound — sans payload brut, PII ou GPS.
