# Audit technique et fonctionnel — Whatassoh

Date : 29 septembre 2026. Périmètre : état du dépôt `backend` à cette date, sans modification du code métier. Les conclusions ci-dessous reposent sur la lecture du code, migrations, configuration et tests présents. La commande `pytest` n'est pas installée dans cet environnement (`/bin/bash: pytest: command not found`) : aucun test n'a donc été validé par exécution dans le cadre de cet audit.

## Résumé exécutif

Whatassoh possède une fondation backend déjà substantielle : modèle PostGIS, API DRF, machine à états de commande, catalogue par boutique, traitement multicanal normalisé, outbox/inbox conversationnelle, matching séquentiel des boutiques et livreurs, ainsi qu'un RAG particulièrement encadré. Ce n'est toutefois pas encore un MVP exploitable de bout en bout avec le niveau de sûreté attendu.

Le risque immédiat principal est l'autorisation des APIs REST : les `ViewSet` stores, catalogues et drivers exposent leurs querysets à tout utilisateur authentifié et plusieurs mutations opérationnelles de livreur ne vérifient pas le propriétaire. Le second risque est l'asymétrie de fiabilité : le pipeline générique de messaging et les notifications boutique ont une reprise de `SENDING`, tandis que les notifications livreur n'ont ni tentatives persistées ni réconciliation visible. Enfin, les flows conversationnels sont riches mais concentrés dans un orchestrateur de 3 380 lignes ; leur validation reste majoritairement unitaire/mockée.

Le projet ne nécessite pas de réécriture. Il faut d'abord fermer les brèches d'accès et terminer/valider le chemin critique « commande confirmée → boutique → préparation → livreur → livré » sur PostgreSQL/PostGIS/Redis/Celery réels.

## A. État général

| Domaine | État | Commentaire |
|---|---|---|
| Fondation Django / DRF / PostGIS | 🟡 | Structure, settings, pagination, gestion d'erreurs, healthcheck et Docker présents ; le compose local ne lance pas web/worker/beat. |
| Identité | 🟡 | Utilisateur, rôles multiples et identités de canal existent ; aucun flux public d'inscription/authentification JWT. |
| Stores / catalogue | 🟡 | Modèles, alias, disponibilité et API existent ; autorisation REST insuffisante. |
| Commandes | ✅ | Référence `ORD-YYMMDD-XXXX`, lignes, état, audit et reprise ciblée des échecs sont implémentés. |
| Messaging WhatsApp / Telegram | 🟡 | Webhooks signés et normalisation commune ; pas de validation E2E fournisseurs, médias seulement normalisés/stockés. |
| Conversation | 🟡 | Routage achat/vendre/livrer, interruptions, mémoire et outbox ordonnée existent ; complexité excessive et couverture E2E absente. |
| Store matching | 🟡 | Eligibilité/ranking déterministes, offres et reconciliation sont solides ; dépend de l'exploitation effective de Beat et de tests multi-workers. |
| Driver / livraison | 🟡 | Matching Redis geo, offres, assignment, pickup/livraison existent ; notifications moins fiables et API non sécurisée. |
| IA / grounding / RAG | 🟡 | Schémas structurés, garde-fous et évaluation présents ; fournisseur par défaut mock et absence de validation avec modèle/runtimes réels. |
| Recommandations / relances | 🔴 | Préférences de communication et rappels de localisation existent, pas de moteur d'achat/réactivation. |
| Tests d'intégration/E2E réelle | 🔴 | 78 fichiers de tests mais aucun scénario prouvé sur la pile distribuée réelle. |

## B. Fonctionnalités raisonnablement terminées

- Machine à états des commandes, validations et audit : `apps/orders/domain/rules.py`, `apps/orders/application/services.py`, `apps/orders/infrastructure/models.py`. Les transitions sont explicitement centralisées, verrouillées via `select_for_update`, et la référence est générée avec retry d'unicité.
- Catalogue marketplace et catalogue vendeur : produits, variantes, aliases, disponibilité par boutique, recherche trigramme et contraintes (`apps/catalog/infrastructure/models.py`, migrations `0003_storeproduct.py` et `0004_catalog_trigram.py`).
- Webhooks entrants : signature Meta (`apps/whatsapp/security.py`, `api/views.py`) et secret Telegram (`apps/telegram/security.py`, `api/views.py`), avec déduplication par message externe dans messaging/conversation.
- Politique WhatsApp 24 h : calcul à partir du dernier message entrant et bascule template/free-form (`apps/whatsapp/domain/service_window.py`, `outbound_policy.py`).
- Inbox/outbox conversationnelle durable : séquençage par conversation, lease, retry et reconciliation (`apps/messaging/application/reliable_pipeline.py`, `apps/messaging/tasks.py`, migration `0011_conversation_inbox_outbox.py`).
- Store matching séquentiel déterministe : snapshots, une offre acceptée par commande, expiration, annulation des concurrentes et reconciliation (`apps/stores/application/matching.py`, `offers.py`, `reconciliation.py`; contraintes `StoreOffer`).
- RAG de connaissance : ingestion/versioning, filtrage d'accès, retrieval hybride, citations vérifiées côté serveur et journaux d'évaluation (`apps/knowledge/application/*`, migrations `0001`/`0002`).

## C. Fonctionnalités partielles

### Messaging multicanal

Les adapters normalisent texte, interactive, localisation et types média, puis passent dans la couche conversationnelle commune. Les callbacks d'offres sont également traités côté conversation. En revanche, aucun test E2E fournisseur ni test de statut sortant WhatsApp n'établit que les payloads templates/interactifs sont réellement acceptés. Les médias ne constituent pas encore un flow métier (pas de téléchargement/scan/traitement). Fichiers : `apps/{whatsapp,telegram}/adapters`, `apps/messaging/application/services.py`.

### Conversation / premier message

`JourneyRoutingService`, `SellerOnboardingService` et `DriverOnboardingService` savent orienter acheter/vendre/livrer ; l'orchestrateur est bien channel-neutral et garde un contexte persistant. L'ambiguïté « Bonjour » est donc couverte conceptuellement. Mais la logique est éclatée parmi beaucoup de branches dans `apps/conversations/application/orchestrator.py` (3 380 lignes), avec un contexte JSON non versionné : la reprise après évolution de schéma et certains enchaînements inattendus restent fragiles.

### Seller flow

Onboarding conversationnel, création/rattachement de boutique, catalogue conversationnel, dashboard à jeton à usage unique et accept/refus d'offre existent (`seller_onboarding.py`, `seller_catalog_conversation.py`, `stores/api/dashboard_views.py`). Il manque un parcours vendeur public cohérent : les APIs de stores/catalog ne portent pas des permissions objet sûres et l'acceptation d'offre ne dispose pas d'un endpoint REST explicite (elle est surtout interactive).

### Livraison

`DriverProfile`, géolocalisation, fraîcheur, Redis GEO, dispatch séquentiel, `DriverAssignment`, pickup et delivery existent. Le matching dépend bien d'une boutique acceptée et d'une localisation fraîche. Il manque une surface API protégée pour les actions opérationnelles, ainsi que la même persistance d'essais/récupération que pour une notification boutique. Fichiers : `apps/drivers/application/{matching,dispatch,offers,delivery_execution,offer_notifications}.py`.

### IA

L'extraction de demande est structurée (`MESSAGE_UNDERSTANDING_SCHEMA`), les coordonnées/localisations et callbacks restent déterministes, les mutations ORM restent hors LLM (`MessageUnderstandingService`, `ConversationalOrchestrator`). Le fallback IA est traité comme erreur retryable selon le pipeline ; il n'y a pas de fallback déterministe d'intention pour une indisponibilité prolongée. `AI_PROVIDER` vaut `mock` par défaut : un déploiement sans configuration explicite ne fournit pas une IA réelle.

## D. Fonctionnalités absentes ou seulement préparées

- Authentification/inscription/refresh JWT exposés par l'API : seul `GET /api/v1/me/` est présent ; SimpleJWT est configuré mais aucune route token n'est incluse.
- Moteur de recommandations produits, achats fréquents, habitudes mensuelles, relance/réactivation : aucun modèle/service/job métier correspondant. Les profils de style et `preference_engine.py` ne sont pas un moteur de recommandation.
- Horaires de boutiques : seulement `is_open` booléen dans `Store`, pas de calendrier ni calcul d'horaires/fuseaux.
- Gestion de stock : volontairement limitée à `StoreProduct.availability`; pas de réservation ni quantité de stock.
- Tarification et rémunération livraison, ETA, tracking client continu et preuve de livraison : absents ou non exposés comme flow complet.
- API de conversations/messages/offres et API d'administration opérationnelle : non exposées sous `config/api_urls.py`.
- Gestion métier de médias entrants : types présents, aucune chaîne de stockage/antivirus/vision.

## E. Bugs et incohérences

### P0 — Bloquant avant exposition API

1. **Escalade de privilèges driver.** `DriverProfileViewSet` utilise `queryset = DriverProfile.objects.select_related("user")` sans filtrage ni permission objet. Tout utilisateur authentifié peut lire les profils et appeler `activate`, `deactivate`, `suspend`, `set-busy` sur l'UUID d'un autre profil ; ces services reçoivent parfois seulement le profil. Scénario : un client désactive tous les livreurs. Impact : indisponibilité et exposition de données. Fichier : `apps/drivers/api/views.py`. Recommandation : queryset « self » sauf staff, permissions objet systématiques dans chaque transition, actions administratives séparées.

2. **Exposition et mutations non autorisées sur stores/catalogue.** `StoreViewSet` liste toutes les boutiques et permet `PATCH` si `can_manage_store`, mais `members` en GET n'exige aucune appartenance. `CategoryViewSet` et `ProductViewSet` autorisent tout authentifié à créer/modifier catégories et produits. Impact : fuite de membres/téléphones potentiels, corruption du catalogue global. Fichiers : `apps/stores/api/views.py`, `apps/catalog/api/views.py`. Recommandation : permissions `IsAdmin` pour catalogue global, membership pour lecture membres, filtrage par objet et tests 403.

### P1 — Important

3. **Fiabilité des offres livreur inférieure aux offres boutique.** `DriverOfferNotification` ne possède pas de table d'attempt, de lease `SENDING`, de `last_error` ni méthode de recovery ; `DriverOfferNotificationService` ne fait que créer/enqueue. Le worker qui crashe après l'appel fournisseur peut redélivrer ou laisser une notification non traçable selon la tâche. À l'inverse, la boutique a `StoreOfferNotificationAttempt` et sweeper. Fichiers : `apps/drivers/infrastructure/models.py`, `apps/drivers/application/offer_notifications.py`, `apps/drivers/tasks.py`. Recommandation : réutiliser le service générique `OperationalNotification` ou porter le protocole complet boutique.

4. **Le chemin API ne protège pas la création de store.** Tout authentifié peut créer une boutique (`perform_create`), sans contrôle de rôle/vérification. Cela peut être volontaire pour onboarding, mais aucune limite/modération ne le montre. Fichier : `apps/stores/api/views.py`. Recommandation : expliciter la politique et anti-abus, attribuer le rôle avec transaction, ajouter validation géographique.

5. **Concurrence non validée sur l'infrastructure cible.** Les contraintes DB et locks sont bien conçus pour accept-vs-accept/expire, mais les tests visibles ne prouvent pas PostgreSQL + plusieurs processus Celery. SQLite/Celery eager ne valident pas les locks partiels, index conditionnels ni GeoDjango. Impact : doubles offres/assignations possibles en production non détectées. Fichiers concernés : `apps/{stores,drivers}/application/{offers,reconciliation}.py` et tests de concurrence.

6. **Compose de développement incomplet pour valider le produit.** `docker-compose.yml` lance seulement PostgreSQL et Redis ; pas de service web, worker ni beat. La pile événementielle n'est donc pas démarrable localement par un unique `docker compose up`. Fichier : `docker-compose.yml`. Recommandation : profil/app compose dev documenté avec migrations, web, worker, beat et env sans secrets réels.

### P2 — Moyen

7. **Etat cancellation du matching boutique ambigu.** `StoreMatchingAttemptStatus` n'a pas `CANCELLED`; la reconciliation encode une annulation sous `EXHAUSTED` + `failure_reason="ORDER_CANCELLED"`. Impact : reporting et automatisations confondent l'épuisement et l'annulation. Fichiers : `apps/stores/domain/enums.py`, `application/reconciliation.py`. Recommandation : statut dédié ou enum de raison structurée exhaustive.

8. **Fenêtre WhatsApp calculée sur les messages persistés, pas sur un horodatage fournisseur dédié.** `last_inbound_at` prend `ConversationMessage.occurred_at`. Un event arrivant tardivement, une date non fiable ou un message non persisté change la politique de template. Fichier : `apps/whatsapp/domain/service_window.py`. Recommandation : conserver le timestamp fournisseur validé et tester les frontières 24 h/retry.

9. **Création d'identité utilisateur via Telegram non explicitement cohérente avec identité WhatsApp.** Le modèle `User.whatsapp_phone` est nullable et `ChannelIdentity` couvre les canaux, mais le rapprochement cross-channel est une décision métier non documentée/testée. Risque de deux comptes pour une personne. Fichiers : `apps/identity/infrastructure/models.py`, `apps/messaging/infrastructure/models.py`.

10. **Orchestrateur monolithique et contexte JSON.** Beaucoup de responsabilités (IA, routage, commandes, vendeur, livreur, réponses) sont dans une méthode/fichier très grand. Impact : régressions et revues difficiles. Fichier : `apps/conversations/application/orchestrator.py`.

### P3 — Mineur / qualité

11. Les classes d'exception avec `pass` sont normales (classes marqueurs), pas des TODO. En revanche `apps/conversations/application/preference_engine.py` et `apps/ai/application/tools.py` contiennent des points à examiner comme extensions/infrastructure incomplètes.
12. Plusieurs fichiers d'archives `*.backup`, `*.bak`, `*.conflict-backup` et `celerybeat-schedule` sont versionnés au workspace ; ils brouillent la source opérationnelle et peuvent contenir de la configuration obsolète.

### Point spécifique `SENDING` — conclusion

Pour les **notifications de store**, le problème connu est traité : `claim_attempt()` persiste `SENDING` + un attempt `STARTED`, `recovery_candidates()` sélectionne les `SENDING` périmés, et `stores.reconcile_store_matching` appelle `recover_store_offer_notification` (`apps/stores/application/offer_notifications.py`, `apps/stores/tasks.py`, `tasks_reconciliation.py`). Un crash entre fournisseur et persistance reste fondamentalement *at-least-once* (donc un doublon fournisseur est possible), mais il ne devrait pas rester définitivement bloqué si Beat tourne. Il manque la preuve d'intégration Beat/worker et la configuration de `STORE_OFFER_NOTIFICATION_SENDING_STALE_SECONDS` est implicite.

Pour les **notifications driver**, l'équivalent manque : c'est le P1 n°3.

## F. Dette technique

- `orchestrator.py` (3 380 lignes), `orders/application/services.py` (994), `ai/application/tools.py` (894) et `reliable_pipeline.py` (582) dépassent une granularité facile à tester. Découper par cas d'usage, sans déplacer les invariants hors des services transactionnels.
- L'architecture mélange deux voies de notification (spécifique store/driver et `OperationalNotification` générique). Cette duplication fera diverger retries, observabilité et politiques WhatsApp.
- Beaucoup de données structurantes sont des JSON (`Conversation.context`, snapshots, mémoire). C'est légitime pour snapshot/audit, moins pour l'état de flow durable : versionner le payload et valider à chaque lecture.
- Les dépendances sont à la fois dans `requirements.txt` et `pyproject.toml`; vérifier une unique source verrouillée/reproductible. `gunicorn` n'est pas piné contrairement au reste.
- Les migrations sont nombreuses mais cohérentes et non squashed ; il faut tester leur application depuis une base vide PostGIS et un upgrade d'une base existante avant toute release.

## G. Tests : inventaire et manques

Inventaire statique : 78 fichiers `test_*.py`, dont conversations 17, drivers 13, stores 11, knowledge 9, messaging 6, IA 6, catalog 4, orders 4, WhatsApp 2, Telegram 1, identity/audit/evaluation/health et deux intégrations (`tests/integration/test_phase2_integration.py`, `test_phase4_multichannel.py`). Il existe des tests ciblés de concurrence : `stores/tests/test_expiry.py`, `drivers/tests/test_delivery_transition_concurrency.py`, `drivers/tests/test_offer_expiry_concurrency.py`, et de déduplication/reconciliation messaging. Les tests post-confirmation indiquent explicitement que le provider est mocké.

Checklist prioritaire manquante :

- [ ] CI installant les dépendances et exécutant toute la suite ; le présent environnement ne contient pas `pytest`.
- [ ] Tests PostgreSQL/PostGIS réels : index GiST/trigramme, `select_for_update`, contraintes conditionnelles, distances et migrations depuis zéro.
- [ ] Test multi-processus : deux boutiques acceptent simultanément ; accept vs expiration ; accept vs annulation ; deux sweepers ; fallback dupliqué.
- [ ] Même matrice pour deux livreurs et contrainte « une livraison active ».
- [ ] Crash injecté après envoi fournisseur et avant `record_success` pour store **et driver**, puis validation reconciliation/idempotency fournisseur.
- [ ] Webhook WhatsApp/Telegram dupliqué, hors ordre et simultané ; callback d'offre d'un non-membre/non-driver.
- [ ] E2E Docker : webhook → inbox → orchestrateur → outbox → adapter mock HTTP → confirmation → store → driver → delivered.
- [ ] Contrats HTTP fournisseurs : template WhatsApp hors 24 h, interactive dans fenêtre, status callbacks, rate-limit/retry.
- [ ] Tests d'autorisations 401/403 pour tous endpoints, notamment list/retrieve/update et actions drivers/stores/catalog.
- [ ] Tests de résilience Redis indisponible / geo index périmé et reprise Celery après broker restart.
- [ ] Évaluation IA avec fournisseur réel en environnement isolé, adversarial prompts et mesures de précision de grounding catalogue.

## H. Architecture et persistance

L'architecture par apps est cohérente : identité, catalogue, commandes, stores, drivers, messaging/conversations, audit, IA, knowledge et evaluation sont séparés. Les invariants les plus importants sont au bon endroit : services transactionnels + contraintes DB (une boutique/driver gagnant, un ordre en construction, assignment active). L'event bus et `transaction.on_commit` réduisent les tâches fantômes ; l'outbox conversationnelle compense le trou transaction-queue.

Les principaux domaines persistés sont bien présents : users/roles, stores/memberships/dashboard grant, catalog/variants/aliases/store products, orders/items, audit events, identities/message events/notifications/inbox/outbox, conversations/mémoires, store matching/offers/attempts, driver matching/offers/assignment/issues, IA runs, RAG sources/chunks/retrieval audits et evaluation datasets/runs. Les timestamps et indexes sont en général corrects ; les soft-deletes sont ciblés (ex. `MemoryFact`) plutôt qu'uniformes.

Le point faible architectural n'est pas l'absence de composants, mais leur convergence opérationnelle : les tâches et locks exigent Postgres/Redis/Celery/Beat réellement déployés. Docker production comporte web/worker/beat et healthchecks DB/Redis (`docker-compose.prod.yml`), mais il faut garantir un seul Beat et une procédure de migrations avant démarrage. Les `/internal/` métriques sont intentionnellement protégées par le proxy, mais cette protection est externe au dépôt Compose.

## Inventaire des endpoints importants

| Méthode | Endpoint | Domaine | Permission effective | État |
|---|---|---|---|---|
| GET | `/health/` | Runtime | Public | ✅ |
| POST | `/webhooks/whatsapp/` | WhatsApp | Signature Meta | ✅ |
| POST | `/webhooks/telegram/` | Telegram | Secret Telegram | ✅ |
| GET | `/api/v1/me/` | Identité | Authentifié | ✅ |
| GET/POST/PATCH | `/api/v1/stores/` | Stores | Authentifié ; contrôle seulement update | ⚠️ |
| GET/POST/PATCH/DELETE | `/api/v1/stores/{id}/members...` | Membership | GET non protégé par membership | ⚠️ |
| GET/POST/PATCH | `/api/v1/categories/`, `/products/` | Catalogue global | Tout authentifié | ⚠️ |
| GET/POST/PATCH/DELETE | `/api/v1/stores/{id}/products/` | Catalogue vendeur | Service vérifie l'écriture ; lecture non filtrée | 🟡 |
| GET/POST/PATCH/actions | `/api/v1/orders/` | Commandes | Propriétaire ou staff | ✅ |
| GET/POST/PATCH/actions | `/api/v1/drivers/` | Drivers | Tout authentifié sur tout profil | ⚠️ |
| POST/GET | `/api/v1/seller-dashboard/...` | Vendeur | Jeton + contrôles dédiés | 🟡 |

Les actions store-offer/driver-offer, conversations, messages, localisation dédiée et delivery execution n'ont pas d'endpoint REST inventorié : elles sont principalement destinées au canal conversationnel. C'est acceptable si assumé, mais doit être explicité dans l'OpenAPI et couvert par tests webhook.

## Roadmap de reprise

### Lot A — Autorisations et exposition des APIs

Objectif : rendre toutes les surfaces REST sûres. Fichiers : `apps/{drivers,stores,catalog}/api/views.py`, permissions/services/serializers et tests API. Travail : querysets scoped, permissions objet, séparation staff/owner, interdiction catalogue global hors admin. Tests : matrice client/vendeur/livreur/staff/anonyme. Critère : aucun UUID d'autrui ne permet lecture sensible ou mutation.

### Lot B — Fiabiliser uniformément les notifications opérationnelles

Objectif : même protocole durable pour store et driver. Fichiers : drivers models/migration/service/tasks, éventuellement consolidation vers `apps/messaging`. Travail : attempts, lease, erreurs classifiées, retry, sweeper, idempotency provider. Critère : après crash injecté, aucun `SENDING` bloqué ; doublon maîtrisé/documenté.

### Lot C — Validation du workflow distribué

Objectif : prouver le chemin client → store → driver sur infrastructure cible. Fichiers : compose dev/CI, fixtures et `tests/integration`, `tests/e2e`. Travail : services web/worker/beat, CI PostGIS+Redis, tests concurrence multi-processus. Critère : scénario E2E et toutes les courses critiques passent contre PostgreSQL/PostGIS.

### Lot D — Stabilisation conversationnelle

Objectif : réduire le risque de régression sans réécriture. Fichiers : `orchestrator.py`, context DTO/services, tests conversations. Travail : extraire les routeurs par journey, versionner le contexte, définir tables de décision/fallback. Critère : contrats de chaque journey, reprise/interruption et messages inattendus déterministes.

### Lot E — Compléter l'exploitation MVP

Objectif : clarifier seller onboarding, horaires et observabilité. Travail : modèle/calcul d'horaires, politique de création boutique, tableaux de bord erreurs/dead letters, runbooks. Les recommandations produit ne doivent venir qu'après le flow transactionnel stabilisé.

## Ordre recommandé de développement

1. **P0** — Lot A : corriger permissions/querysets exposés.
2. **P1** — Lot B : rendre le driver dispatch aussi récupérable que le store matching.
3. **P1** — Lot C : prouver l'intégrité/concurrence et les tâches réelles.
4. **P2** — Lot D : modulariser les journeys et versionner l'état conversationnel.
5. **P2** — Lot E : horaires, runbooks et observabilité opérationnelle.
6. **P3** — Recommandations/relances et améliorations IA après collecte de données fiables.

## Prochaine action recommandée

**Lot A — autorisations et exposition des APIs.**

- **Objectif :** empêcher qu'un utilisateur authentifié pilote ou consulte les ressources opérationnelles d'un autre utilisateur.
- **Raison :** c'est une faille directe, indépendante de l'avancement du matching, et elle bloque toute exposition de l'API.
- **Fichiers probablement concernés :** `apps/drivers/api/views.py`, `apps/stores/api/views.py`, `apps/catalog/api/views.py`, les services de permissions et `apps/*/tests/test_*api*.py`.
- **Modifications nécessaires :** scoper les querysets, appliquer des permissions objet aux actions, réserver les catégories/produits globaux aux admins et protéger la liste des membres.
- **Tests à écrire :** cas 401/403 et succès pour client, propriétaire, manager, driver propriétaire et staff, sur chaque action/list/retrieve/mutation.
- **Critères d'acceptation :** aucune ressource d'un tiers n'est lisible ou modifiable via UUID ; OpenAPI documente les règles ; suite API verte sur PostgreSQL.
- **Risques à surveiller :** ne pas casser les callbacks conversationnels légitimes ni les accès dashboard à jeton ; les permissions doivent être centralisées pour éviter une divergence API/chat.
