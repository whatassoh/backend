## Phase 11.1 - Delivery Domain & Assignment Execution Foundation

### Implémenté

- Réutilisation de `Order + DriverAssignment` comme mission physique durable ; aucun modèle `Delivery` créé.
- Ajout du `DeliveryExecutionService`, qui récupère une mission active depuis PostgreSQL et autorise le chemin `ChannelIdentity → User → DriverProfile → DriverAssignment → Order`.
- Ajout du DTO channel-neutral et minimisé `DriverMissionDto` (pickup gagnant, destination, instructions et actions stables), sans conversation, téléphone client, payload provider ni donnée IA.
- Une mission active est un `DriverAssignment.is_active=True` dont l’Order est `DRIVER_ASSIGNED`, `PICKED_UP` ou `IN_DELIVERY`.
- La contrainte PostgreSQL existante `driver_assignment_one_active_per_driver` reste la protection concurrency-safe de l’unicité ; les transitions terminales existantes (`CANCELLED`, `FAILED`, `DELIVERED`) ferment désormais le flag opérationnel d’assignment dans la même transaction.
- Les transitions Order Phase 3 (`DRIVER_ASSIGNED → PICKED_UP → IN_DELIVERY → DELIVERED`) et leurs audits restent réutilisées ; ce lot n’expose aucune mutation de livraison et aucun chemin LLM.

### Préparation des lots suivants

- Actions stables : `delivery.confirm_pickup`, `delivery.start_delivery`, `delivery.confirm_delivered`, `delivery.report_issue`.
- `assigned_at` est `DriverAssignment.created_at`; pickup/start/delivered seront des timestamps métier sur Order ou événements append-only lorsque les mutations seront introduites. `Order.completed_at` existe déjà pour `DELIVERED`.
- L’événement de domaine `DriverAssigned` est publié après commit lors de la création durable d’un assignment ; aucun doublon `DeliveryMissionAssigned` n’est créé. La notification d’offre Phase 9/10 est conservée, sans envoi supplémentaire ici.
- Contact client/proxy téléphonique et les règles d’exception après pickup restent à définir dans 11.3/11.6. Les règles actuelles permettent `CANCELLED` seulement depuis `DRIVER_ASSIGNED`, et `DELIVERY_FAILED` depuis les trois états actifs.

## Phase 11.2 - Pickup Workflow & Proof of Pickup

- Ajout de `Order.picked_up_at`, écrit par `mark_picked_up` dans la transition existante et auditée `DRIVER_ASSIGNED → PICKED_UP`.
- `DeliveryExecutionService.confirm_pickup` verrouille puis réautorise l’Order et l’assignment, vérifie le StoreOffer accepté, est idempotent pour `PICKED_UP`, et ne dépend ni d’un `store_id` entrant ni de `Conversation.context`.
- La preuve MVP est `AUDIT_AND_TIMESTAMP` : Driver authentifié + audit de transition + timestamp persistant. QR, OTP, photo, signature, géofence et NFC restent hors scope, mais pourront enrichir le contrat ultérieurement.
- Ajout de l’action structurée channel-neutral `delivery.confirm_pickup` dans le routeur conversationnel WA/TG ; aucune interprétation LLM n’est appelée.
- `OrderPickedUp` est publié après commit, une seule fois lors de la transition effective. Les intents Phase 10 client et vendeurs actifs sont persistés dans la transaction puis livrés async ; leur échec ne rollback jamais le pickup.

## Phase 11.3 - In-Delivery Workflow & Driver Progress

- Décision : transition `EXPLICIT`. `PICKED_UP` confirme la possession; `IN_DELIVERY` confirme explicitement le départ, conformément aux deux états distincts de la state machine Phase 3.
- Ajout de `Order.delivery_started_at` et de `DeliveryExecutionService.start_delivery`, qui verrouille, réautorise, utilise `mark_in_delivery`, audite, publie `DeliveryStarted` après commit et persiste une notification client Phase 10 sans ETA.
- `delivery.start_delivery` est routé de façon structurée et identique pour WhatsApp/Telegram. Après succès, les actions contractuelles `delivery.confirm_delivered` et `delivery.report_issue` sont présentées mais ne mutent rien avant les lots 11.5/11.6.
- La localisation Driver et les rappels stale Phase 10 sont réutilisés sans transition GPS automatique, sans failure/cancellation/unassignment. Passer OFFLINE retire seulement les nouvelles offres : l’assignment IN_DELIVERY reste active.

## Phase 11.4 - Delivery Location, Driver Location & Customer Tracking Foundation

- Ajout de `DeliveryTrackingService` et `DeliveryTrackingSnapshot`, lecture dérivée depuis `Order`, `DriverAssignment`, StoreOffer gagnante et `DriverProfile.last_location` durable. Redis GEO n’est jamais une source tracking.
- `order_status` et `location_status` restent deux dimensions distinctes (`FRESH`, `STALE`, `UNAVAILABLE`). Une position stale n’est jamais renvoyée comme position courante.
- Politique MVP : coordonnées Driver exactes uniquement au client propriétaire, uniquement en `IN_DELIVERY`, uniquement si fraîches et jamais après un état terminal. Le vendeur reçoit l’état opérationnel mais jamais la position live.
- Aucun historique GPS, ETA, route, geofence, push à chaque point ou transition automatique GPS n’est créé. Les rappels de fraîcheur Phase 10 et le broker `DriverLocationService` restent réutilisés.

## Phase 11.5 - Delivery Completion & Proof of Delivery

- Ajout de `Order.delivered_at` et de `DeliveryExecutionService.confirm_delivered`, qui réutilise exclusivement `mark_delivered`, vérifie l’assignment sous verrou, persiste la clôture et conserve l’historique de `DriverAssignment` en le marquant inactif.
- Décision evidence : `AUDIT_AND_TIMESTAMP`. Pour le MVP, Driver assigné authentifié + action `delivery.confirm_delivered` + audit de transition + timestamp serveur constituent la preuve; aucun OTP/photo/signature/GPS n’est imposé.
- Ajout de `OrderDelivered`, publié après commit une fois par transition effective. C’est le hook métier propre pour la future Phase 12, sans implémenter rating/recommendation.
- Notification client persistante `ORDER_DELIVERED` via Phase 10; aucun provider failure ne rollback `DELIVERED`. Le tracking 11.4 masque déjà la position Driver pour l’état terminal.

## Phase 11.6 - Delivery Exceptions, Failure & Operational Recovery

- Ajout du modèle durable `DeliveryIssue`, séparé de l’état Order, avec types contrôlés, statut `OPEN/RESOLVED/TERMINAL`, note optionnelle et contrainte d’un seul incident ouvert par type/assignment.
- `report_issue` n’échoue jamais automatiquement une commande; `resolve_issue` conserve l’historique. `confirm_failure` est une action explicite séparée, reliée à un incident ouvert et réutilise `fail_order(..., DELIVERY_FAILED)`.
- Les incidents sont autorisés sur les missions actives; `PICKUP_PROBLEM` est limité à `DRIVER_ASSIGNED`. Après pickup, aucun rematching automatique n’est déclenché. Les pannes techniques Messaging/Redis/Celery ne créent aucun incident métier.

## Phase 11.7 - Validation status

- Environnement audité : `.venv` Python 3.12/Django 6 et Docker Compose PostGIS/Redis. `manage.py check` et `makemigrations --check --dry-run` passent hors connexion DB; un index DeliveryIssue invalide (>30 caractères PostgreSQL) a été corrigé avant migration.
- Validation transactionnelle bloquée : `.env` configure `postgres@localhost:5432`/DB `whatassoh`, tandis que le Compose sain expose `whatassoh@localhost:5433`/DB `whatassoh_db`; Django charge cette configuration et les tests ne peuvent établir la connexion.
- Aucune validation PostgreSQL/PostGIS/Redis, race, E2E, provider ou régression complète n’est revendiquée tant que cette configuration n’est pas alignée et les tests exécutés.

## Lot 2.1 - Identity

### Refactor transversal - BaseModel

- Ajout de `common.models.BaseModel`.
- `BaseModel` fournit désormais les champs communs:
  - `id` UUID primary key;
  - `created_at`;
  - `updated_at`.
- Tous les modèles métier existants héritent de `BaseModel`:
  - `User`;
  - `UserRole`;
  - `Store`;
  - `StoreMembership`;
  - `DriverProfile`;
  - `Category`;
  - `Product`;
  - `ProductVariant`;
  - `ProductAlias`.
- Ajout des migrations nécessaires pour uniformiser les modèles qui n'avaient pas encore UUID ou `updated_at`.

### Implémenté

- Création de l'app modulaire `apps.identity`.
- Ajout du custom user Django `identity.User` comme identité commune du système.
- Ajout de `UserRole` pour permettre plusieurs rôles simultanés par utilisateur.
- Ajout des enums `UserStatus` (`ACTIVE`, `INACTIVE`, `SUSPENDED`) et `UserRoleType` (`CLIENT`, `STORE_OWNER`, `STORE_MANAGER`, `STORE_AGENT`, `DRIVER`, `ADMIN`).
- Ajout de la normalisation des téléphones WhatsApp avant persistance.
- Ajout des contraintes DB:
  - unicité de `User.whatsapp_phone`;
  - validité de `User.status`;
  - unicité `(user, role)` sur `UserRole`;
  - validité de `UserRole.role`.
- Ajout des services métier:
  - `get_or_create_user_by_whatsapp`;
  - `assign_role`;
  - `remove_role`;
  - `has_role`.
- Ajout des selectors:
  - `get_user_by_whatsapp`;
  - `get_user_roles`;
  - `get_active_users`;
  - `user_has_role`.
- Intégration Django Admin pour rechercher les utilisateurs par téléphone, filtrer par statut et gérer les rôles via inline.
- Ajout d'une API minimale `GET /api/v1/me/`.
- Ajout des tests unitaires et d'intégration Identity.

### Choix techniques

- `AUTH_USER_MODEL = "identity.User"` est configuré car aucun custom user existant n'était présent.
- L'identifiant primaire du user est un UUID.
- Le téléphone WhatsApp est stocké en format E.164 normalisé, avec prise en charge des formes locales camerounaises courantes (`6...`, `237...`, `+237...`).
- Les rôles restent indépendants des futurs profils Driver ou Store.

### Points à surveiller

- Les lots suivants devront référencer le user via `settings.AUTH_USER_MODEL`.
- Si des règles pays supplémentaires sont nécessaires, étendre la normalisation téléphone sans coupler Identity à WhatsApp SDK.

## Lot 2.2 - Stores + geographie

### Implémenté

- Création de l'app modulaire `apps.stores`.
- Ajout du modèle `Store` comme entité métier indépendante des utilisateurs.
- Ajout du modèle `StoreMembership` pour représenter explicitement les relations User/Store.
- Réutilisation du user Identity via `settings.AUTH_USER_MODEL`.
- Ajout des enums:
  - `StoreStatus`: `ACTIVE`, `INACTIVE`, `SUSPENDED`;
  - `StoreMembershipRole`: `OWNER`, `MANAGER`, `AGENT`;
  - `StoreMembershipStatus`: `ACTIVE`, `INACTIVE`.
- Ajout de la géolocalisation persistante via GeoDjango:
  - `Store.location = PointField(geography=True, srid=4326)`.
- Ajout du rayon de service MVP via `service_radius_meters`, avec défaut à `5000` mètres.
- Ajout des contraintes DB:
  - validité de `Store.status`;
  - `Store.service_radius_meters > 0`;
  - unicité `(store, user)` sur `StoreMembership`;
  - validité de `StoreMembership.role`;
  - validité de `StoreMembership.status`.
- Ajout des services métier:
  - `create_store`;
  - `add_store_member`;
  - `remove_store_member`;
  - `change_store_member_role`;
  - `update_store_location`;
  - `set_store_open_state`;
  - `activate_store`;
  - `suspend_store`.
- Ajout des selectors:
  - `get_store_by_id`;
  - `get_user_stores`;
  - `get_store_members`;
  - `get_active_stores`;
  - `get_open_stores`;
  - `get_stores_within_radius`.
- Intégration Django Admin pour les stores et memberships.
- Ajout d'une API minimale:
  - `GET /api/v1/stores/`;
  - `POST /api/v1/stores/`;
  - `GET /api/v1/stores/{id}/`;
  - `PATCH /api/v1/stores/{id}/`;
  - `GET /api/v1/stores/{id}/members/`;
  - `POST /api/v1/stores/{id}/members/`;
  - `PATCH /api/v1/stores/{id}/members/{membership_id}/`;
  - `DELETE /api/v1/stores/{id}/members/{membership_id}/`.
- Ajout des tests unitaires, d'intégration et géographiques pour exécution ultérieure.

### Choix techniques

- Une boutique n'a pas de champ direct `owner`; la propriété passe par `StoreMembership(role=OWNER)`.
- La création API d'une boutique crée un membership `OWNER` pour l'utilisateur authentifié via le service métier `create_store`.
- Les serializers exposent `latitude` et `longitude`, mais la source de vérité persistante reste `Store.location`.
- La recherche spatiale `get_stores_within_radius` utilise les lookups GeoDjango/PostGIS avec annotation `Distance`.
- Le lot ne crée aucun `StoreProduct`, matching, commande, livraison, Redis GEO ou tracking.

### Validation

- `python manage.py check` passe.
- `python manage.py makemigrations --check --dry-run` indique qu'aucune migration ne manque.
- `ruff check apps/stores config/api_urls.py config/settings/base.py` passe.
- Les tests n'ont pas été exécutés volontairement.
- Le warning PostgreSQL pendant `makemigrations --check` vient de la base locale non joignable.

### Points à surveiller

- Les lots Catalog devront référencer `Store` sans déplacer la responsabilité produit dans ce lot.
- Le futur Matching pourra réutiliser `get_stores_within_radius`, mais devra porter ses propres règles métier.
- Avant validation finale, aligner l'environnement PostgreSQL/PostGIS local puis exécuter les tests Identity + Stores.

## Lot 2.3 - Drivers

### Implémenté

- Création de l'app modulaire `apps.drivers`.
- Ajout du modèle `DriverProfile` relié à `User` via `OneToOneField`.
- Réutilisation de `settings.AUTH_USER_MODEL`.
- Ajout automatique du rôle Identity `DRIVER` à la création d'un profil chauffeur.
- Ajout des enums:
  - `DriverStatus`: `ACTIVE`, `INACTIVE`, `SUSPENDED`;
  - `DriverAvailability`: `OFFLINE`, `AVAILABLE`, `BUSY`;
  - `VehicleType`: `MOTORBIKE`, `BICYCLE`, `CAR`, `VAN`, `OTHER`.
- Ajout du champ optionnel `service_radius_meters`, validé strictement positif s'il est renseigné.
- Ajout des services métier:
  - `create_driver_profile`;
  - `activate_driver`;
  - `deactivate_driver`;
  - `suspend_driver`;
  - `set_driver_available`;
  - `set_driver_offline`;
  - `set_driver_busy`.
- Ajout des selectors:
  - `get_driver_by_id`;
  - `get_driver_by_user`;
  - `get_active_drivers`;
  - `get_available_drivers`.
- Intégration Django Admin avec recherche par téléphone WhatsApp du user.
- Ajout d'une API minimale:
  - `GET /api/v1/drivers/`;
  - `POST /api/v1/drivers/`;
  - `GET /api/v1/drivers/{id}/`;
  - `PATCH /api/v1/drivers/{id}/`;
  - `POST /api/v1/drivers/{id}/activate/`;
  - `POST /api/v1/drivers/{id}/deactivate/`;
  - `POST /api/v1/drivers/{id}/suspend/`;
  - `POST /api/v1/drivers/{id}/go-online/`;
  - `POST /api/v1/drivers/{id}/go-offline/`;
  - `POST /api/v1/drivers/{id}/set-busy/`.
- Ajout des tests unitaires et d'intégration Drivers.

### Invariants

- Un user ne peut avoir qu'un seul `DriverProfile`.
- Seul un chauffeur `ACTIVE` peut passer `AVAILABLE`.
- Un chauffeur `SUSPENDED` ou `INACTIVE` ne peut pas rester `AVAILABLE`.
- `suspend_driver` force `availability_status=OFFLINE`.
- `deactivate_driver` force `availability_status=OFFLINE`.
- `activate_driver` force `availability_status=OFFLINE` par défaut.
- Les changements d'état opérationnel passent par les services métier.

### Contraintes DB

- `DriverProfile.user` est unique via `OneToOneField`.
- Validité de `status`.
- Validité de `availability_status`.
- Validité de `vehicle_type` lorsqu'il est renseigné.
- `service_radius_meters > 0` ou `NULL`.
- `availability_status=AVAILABLE` implique `status=ACTIVE`.

### Validation

- `python manage.py check` passe.
- `python manage.py makemigrations --check --dry-run` indique qu'aucune migration ne manque.
- `ruff check apps/drivers apps/identity apps/stores config/api_urls.py config/settings/base.py` passe.
- `pytest apps/identity/tests apps/stores/tests apps/drivers/tests` a été lancé mais bloqué au setup DB:
  - PostgreSQL ne répond pas sur `localhost:5432`;
  - aucune erreur métier Drivers/Stores/Identity n'a encore été atteinte.

### Non implémenté volontairement

- Redis GEO.
- Position live chauffeur.
- Timestamp de présence.
- `DriverOffer`.
- `Delivery`.
- Matching chauffeur.

## Lot 2.4 - Catalog Core

### Implémenté

- Création de l'app modulaire `apps.catalog`.
- Ajout du catalogue canonique sans gestion de stock.
- Ajout du modèle `Category` avec relation parent/enfant simple.
- Ajout du modèle `Product` comme entité canonique principale.
- Ajout du modèle optionnel `ProductVariant`.
- Ajout du modèle optionnel `ProductAlias`.
- Ajout des enums:
  - `CategoryStatus`: `ACTIVE`, `INACTIVE`;
  - `ProductStatus`: `ACTIVE`, `INACTIVE`;
  - `ProductVariantStatus`: `ACTIVE`, `INACTIVE`;
  - `ProductUnit`: `KG`, `G`, `L`, `ML`, `UNIT`, `PACK`, `BOTTLE`, `BAG`, `OTHER`.
- Ajout de la normalisation déterministe:
  - `Huile Mayor` -> `huile mayor`;
  - `Riz Parfumé` -> `riz parfume`.
- Ajout des services métier:
  - `create_category`;
  - `update_category`;
  - `deactivate_category`;
  - `create_product`;
  - `update_product`;
  - `deactivate_product`;
  - `create_product_variant`;
  - `deactivate_product_variant`;
  - `add_product_alias`;
  - `remove_product_alias`.
- Ajout des selectors:
  - `get_category_by_id`;
  - `get_active_categories`;
  - `get_category_children`;
  - `get_product_by_id`;
  - `get_active_products`;
  - `get_products_by_category`;
  - `get_product_variants`;
  - `get_product_aliases`;
  - `search_products`.
- Ajout d'une recherche déterministe simple sur:
  - `Product.name`;
  - `Product.normalized_name`;
  - `ProductAlias.normalized_alias`.
- Intégration Django Admin avec inlines variantes et alias sur `Product`.
- Ajout d'une API minimale:
  - `GET /api/v1/categories/`;
  - `POST /api/v1/categories/`;
  - `GET /api/v1/categories/{id}/`;
  - `PATCH /api/v1/categories/{id}/`;
  - `GET /api/v1/products/`;
  - `POST /api/v1/products/`;
  - `GET /api/v1/products/{id}/`;
  - `PATCH /api/v1/products/{id}/`;
  - `GET /api/v1/products/?search=mayor`;
  - `GET /api/v1/products/?category=<uuid>`.
- Ajout des tests unitaires et d'intégration Catalog.

### Contraintes et indexes

- `Category.slug` unique.
- `Category.status` valide.
- Une catégorie ne peut pas être son propre parent.
- `Product.status` valide.
- `Product.default_unit` valide ou `NULL`.
- Unicité `(category, normalized_name)` pour éviter les doublons évidents.
- `ProductVariant.status` valide.
- Unicité `(product, normalized_name)` sur les variantes.
- Unicité `(product, normalized_alias)` sur les alias.
- Indexes sur slugs, statuts, noms normalisés, catégories et alias normalisés.

### Validation

- `python manage.py check` passe.
- `python manage.py makemigrations --check --dry-run` indique qu'aucune migration ne manque.
- `ruff check apps/catalog apps/identity apps/stores apps/drivers config/api_urls.py config/settings/base.py` passe.
- `pytest apps/identity/tests apps/stores/tests apps/drivers/tests apps/catalog/tests` a été lancé mais bloqué au setup DB:
  - PostgreSQL ne répond pas sur `localhost:5432`;
  - 66 tests collectés;
  - aucune erreur métier Catalog/Drivers/Stores/Identity n'a encore été atteinte.

### Non implémenté volontairement

- `StoreProduct`.
- Stock, réservation, inventaire et mouvements de stock.
- `StoreOffer`.
- `Order` et `OrderItem`.
- Matching boutique.
- IA / LLM.
- Redis ou Celery pour le catalogue.

### Préparation Lot 2.5

- `Product` est prêt à être référencé par `StoreProduct`.
- `ProductVariant` est optionnel et pourra être référencé seulement si nécessaire.
- La disponibilité par boutique reste hors catalogue canonique et devra vivre dans `StoreProduct`.

## Lot 2.5 - Store Catalog

### Implémenté

- Ajout de `StoreProduct` dans `apps.catalog`.
- Ajout de `ProductAvailability`: `AVAILABLE`, `UNAVAILABLE`, `UNKNOWN`.
- `StoreProduct` relie:
  - `Store`;
  - `Product`;
  - `ProductVariant` optionnel.
- Ajout des champs:
  - `display_name` optionnel;
  - `price` optionnel;
  - `currency` avec défaut `XAF`;
  - `availability_status`.
- Aucun stock, aucune réservation, aucun mouvement d'inventaire.

### Contraintes et indexes

- `availability_status` limité aux valeurs de `ProductAvailability`.
- `price` nullable ou supérieur/égal à `0`.
- Unicité robuste quand `product_variant IS NULL`:
  - unique `(store, product)`.
- Unicité robuste quand `product_variant IS NOT NULL`:
  - unique `(store, product, product_variant)`.
- Indexes:
  - `store`;
  - `product`;
  - `availability_status`;
  - `(store, availability_status)`;
  - `(product, availability_status)`.

### Services

- `add_product_to_store`.
- `update_store_product`.
- `set_product_availability`.
- `set_store_product_price`.
- `remove_product_from_store`.

`remove_product_from_store` ne supprime pas physiquement la ligne; il passe `availability_status` à `UNAVAILABLE`.

### Selectors

- `get_store_catalog`.
- `get_store_products`.
- `get_available_store_products`.
- `get_stores_for_product`.
- `get_store_product`.
- `get_candidate_store_products`.

`get_candidate_store_products` reste un selector de données et exclut les stores/products/variants non opérationnels, sans distance, score, ranking ou matching.

### Permissions

- Ajout de `can_manage_store_catalog`.
- Les rôles `OWNER` et `MANAGER` actifs d'un `StoreMembership` peuvent modifier le catalogue boutique.
- Le `store` est toujours résolu depuis l'URL scoped, pas depuis le body.

### API

- `GET /api/v1/stores/{store_id}/products/`.
- `POST /api/v1/stores/{store_id}/products/`.
- `GET /api/v1/stores/{store_id}/products/{store_product_id}/`.
- `PATCH /api/v1/stores/{store_id}/products/{store_product_id}/`.
- `DELETE /api/v1/stores/{store_id}/products/{store_product_id}/`.

### Validation

- `python manage.py check` passe.
- `python manage.py makemigrations --check --dry-run` indique qu'aucune migration ne manque.
- `ruff check apps/identity apps/stores apps/drivers apps/catalog common/models.py config/api_urls.py config/settings/base.py` passe.
- `pytest apps/identity/tests apps/stores/tests apps/drivers/tests apps/catalog/tests` a été lancé mais bloqué au setup DB:
  - PostgreSQL ne répond pas sur `localhost:5432`;
  - 84 tests collectés;
  - aucune erreur métier Store Catalog n'a encore été atteinte.

### Reporté volontairement

- Stock.
- `StoreOffer`.
- Matching géographique ou scoring boutique.
- `Order` et `OrderItem`.
- IA / LLM.
- Redis.
- Celery.

## Lot 2.6 - Integration et validation finale Phase 2

### Audit

- Les relations de domaine Phase 2 sont en place:
  - `User` -> `UserRole`;
  - `User` -> `DriverProfile`;
  - `User` -> `StoreMembership` -> `Store`;
  - `Store` -> `StoreProduct` -> `Product`;
  - `Product` -> `Category`;
  - `Product` -> `ProductVariant`;
  - `Product` -> `ProductAlias`.
- `Identity` ne dépend pas de `Stores`, `Drivers` ou `Catalog`.
- `Drivers` dépend de `Identity` uniquement pour assigner le rôle `DRIVER`.
- `Stores` référence `settings.AUTH_USER_MODEL`.
- `Catalog` référence `Store` uniquement pour `StoreProduct` et l'API scoped.
- Aucun stock, matching, order, offer, Redis GEO ou LLM n'est introduit en Phase 2.

### Incohérences corrigées

- Permissions Store trop larges:
  - ajout de `apps.stores.application.permissions`;
  - `can_manage_store`, `is_store_owner`, `is_store_manager`.
- Permissions catalogue boutique centralisées:
  - `can_manage_store_catalog` réutilise maintenant `can_manage_store`.
- Actions membership Store resserrées:
  - `OWNER` seul modifie/supprime les memberships;
  - `OWNER` et `MANAGER` peuvent modifier la boutique/catalogue.
- API Driver resserrée:
  - création de `DriverProfile` pour `request.user`;
  - suppression du `user_id` write-only exposé au client.

### Seed

- Ajout de la commande `python manage.py seed_phase2`.
- Commande idempotente autant que raisonnablement possible via recherches normalisées et `get_or_create`.
- Dataset de démonstration:
  - 10 users;
  - rôles multiples;
  - 4 boutiques de démonstration à Douala;
  - 5 chauffeurs;
  - 8 catégories;
  - 28 produits;
  - variantes utiles;
  - alias produits;
  - environ 40 liens `StoreProduct`.
- Les coordonnées sont explicitement des données de démonstration, pas de vraies boutiques.
- Aucun champ de stock n'est généré.

### Tests ajoutés

- `tests/integration/test_phase2_integration.py`.
- Scénarios couverts:
  - owner -> store -> catalog -> product;
  - driver active/available;
  - user multi-rôle unique;
  - catalogue souple avec prix nul;
  - cohérence ProductVariant/Product;
  - permissions OWNER/MANAGER/outsider;
  - PostGIS distance/rayon.

### Validation

- `python manage.py check` passe.
- `python manage.py makemigrations --check --dry-run` indique qu'aucune migration ne manque.
- `ruff check apps/identity apps/stores apps/drivers apps/catalog common/models.py tests/integration/test_phase2_integration.py config/api_urls.py` passe.
- `python manage.py migrate` a été tenté mais bloqué:
  - PostgreSQL ne répond pas sur `localhost:5432`.
- `python manage.py seed_phase2` n'a pas pu être exécuté utilement car `migrate` ne peut pas se connecter.
- `pytest apps/identity/tests apps/stores/tests apps/drivers/tests apps/catalog/tests tests/integration` a été lancé:
  - 91 tests collectés;
  - blocage au setup DB;
  - PostgreSQL ne répond pas sur `localhost:5432`;
  - aucune assertion métier n'a été atteinte.

### Politique permissions MVP

- `OWNER`:
  - administration boutique;
  - gestion memberships;
  - gestion catalogue.
- `MANAGER`:
  - gestion boutique opérationnelle;
  - gestion catalogue.
- `AGENT`:
  - lecture et opérations limitées futures;
  - pas de modification catalogue dans ce MVP.

### Pret pour Phase 3

- Identity expose `User` et `UserRole`.
- Stores expose `Store`, `StoreMembership`, `Store.location`, `service_radius_meters`.
- Drivers expose `DriverProfile`, status administratif et disponibilité opérationnelle.
- Catalog expose `Category`, `Product`, `ProductVariant`, `ProductAlias`.
- Store Catalog expose `StoreProduct`, disponibilité souple, prix optionnel et devise.
- Les futurs lots Orders + Audit pourront référencer ces entités sans introduire de stock dans Phase 2.

## Lot 3.1 - Order Core

### Conventions reutilisees

- `common.models.BaseModel` pour les UUID et timestamps.
- Organisation modulaire existante:
  - `domain/enums.py`;
  - `domain/rules.py`;
  - `domain/services.py`;
  - `application/services.py`;
  - `application/selectors.py`;
  - `infrastructure/models.py`.
- Enums Django via `models.TextChoices`.
- GeoDjango via `PointField(geography=True, srid=4326)`.
- User central via `settings.AUTH_USER_MODEL`.
- Unite de quantite reutilisee depuis `catalog.ProductUnit`.
- Validations applicatives dans `domain/rules.py`, appelees par les services et les modeles.

### Modeles

- Ajout de l'app `apps.orders`.
- Ajout de `Order`:
  - `reference`;
  - `customer`;
  - `status`;
  - `delivery_location`;
  - `delivery_address_label`;
  - `delivery_instructions`;
  - `currency`;
  - timestamps de confirmation, annulation, completion et echec;
  - champs structures d'annulation;
  - champs structures d'echec.
- Ajout de `OrderItem`:
  - `order`;
  - `requested_name`;
  - `quantity`;
  - `unit`;
  - `notes`;
  - `product` optionnel;
  - `product_variant` optionnel;
  - `resolution_status`.

### Enums

- `OrderStatus`:
  - `DRAFT`;
  - `AWAITING_LOCATION`;
  - `CONFIRMED`;
  - `MATCHING_STORES`;
  - `STORE_OFFERED`;
  - `STORE_ACCEPTED`;
  - `PREPARING`;
  - `READY_FOR_PICKUP`;
  - `MATCHING_DRIVERS`;
  - `DRIVER_ASSIGNED`;
  - `PICKED_UP`;
  - `IN_DELIVERY`;
  - `DELIVERED`;
  - `CANCELLED`;
  - `FAILED`.
- `OrderItemResolutionStatus`:
  - `MATCHED`;
  - `UNMATCHED`;
  - `AMBIGUOUS`.
- `OrderCancellationCode`:
  - `CUSTOMER_REQUEST`;
  - `STORE_REQUEST`;
  - `ADMIN_REQUEST`;
  - `OTHER`.
- `OrderFailureCode`:
  - `NO_STORE_AVAILABLE`;
  - `NO_DRIVER_AVAILABLE`;
  - `STORE_FULFILLMENT_FAILED`;
  - `DELIVERY_FAILED`;
  - `OTHER`.

### Contraintes et indexes

- `Order.reference` unique, valide au format `ORD-YYMMDD-XXXX`.
- `Order.status` limite aux valeurs de `OrderStatus`.
- Codes d'annulation, codes d'echec et `failed_from_status` valides ou `NULL`.
- `OrderItem.quantity > 0`.
- `OrderItem.unit` limite aux valeurs de `ProductUnit`.
- `OrderItem.resolution_status` limite aux valeurs de `OrderItemResolutionStatus`.
- `MATCHED` implique `product IS NOT NULL`.
- `UNMATCHED` implique `product IS NULL`.
- `product_variant IS NOT NULL` implique `product IS NOT NULL`.
- Validation applicative supplementaire:
  - `product_variant.product_id == product.id`.
- Indexes:
  - `Order.customer`;
  - `Order.status`;
  - `(Order.customer, Order.status)`;
  - `Order.created_at`;
  - `OrderItem.order`;
  - `OrderItem.product`;
  - `OrderItem.resolution_status`.
- `delivery_location` conserve l'index spatial implicite de `PointField`.

### Reference Order

- Format public: `ORD-YYMMDD-XXXX`.
- Suffixe genere avec `secrets.choice`.
- Alphabet sans caracteres ambigus `O`, `0`, `I`, `1`.
- Collision geree par tentatives bornees:
  - generation;
  - tentative d'insertion;
  - retry sur `IntegrityError`;
  - contrainte unique DB comme protection finale.

### Services

- `create_order_draft`.
- `add_order_item`.
- `update_order_item`.
- `remove_order_item`.
- `set_delivery_location`.
- `set_delivery_instructions`.

Les services de modification autorisent seulement `DRAFT` et `AWAITING_LOCATION` pour eviter d'ouvrir une modification arbitraire sur des commandes terminees ou arretees avant la machine a etats du Lot 3.2.

### Selectors

- `get_order_by_id`.
- `get_order_by_reference`.
- `get_customer_orders`.
- `get_order_items`.
- `get_order_with_items`.

Les selectors utilisent `select_related` et `prefetch_related` pour preparer les futures lectures API sans N+1 evident.

### Admin

- Ajout de `OrderAdmin`.
- Ajout de `OrderItemAdmin`.
- Inline `OrderItem` dans `OrderAdmin`.
- Recherche par `reference` et `customer__whatsapp_phone`.
- `reference`, `id`, `created_at` et `updated_at` en readonly.

### Migrations

- Ajout de `apps/orders/migrations/0001_initial.py`.
- Ajout de `apps.orders` dans `LOCAL_APPS`.

### Choix on_delete

- `Order.customer`: `PROTECT` pour preserver l'historique transactionnel.
- `OrderItem.order`: `CASCADE`, car les lignes appartiennent a l'aggregate Order.
- `OrderItem.product`: `PROTECT` pour ne pas casser l'historique d'une commande resolue.
- `OrderItem.product_variant`: `PROTECT` pour la meme raison.

### Tests ajoutes

- `apps/orders/tests/test_orders.py`.
- Couverture:
  - creation draft;
  - reference et collision simulee;
  - unicite DB de `reference`;
  - devise par defaut;
  - `delivery_location` nullable et persistance `Point(longitude, latitude)`;
  - catalogue souple avec item `UNMATCHED`;
  - item `MATCHED` avec produit;
  - item `MATCHED` avec variante;
  - rejet variante/produit incoherents;
  - invariants `MATCHED`, `UNMATCHED`, `AMBIGUOUS`;
  - quantite strictement positive;
  - `requested_name` obligatoire;
  - plusieurs commandes par user;
  - plusieurs items par commande;
  - selectors;
  - protection de suppression customer.

### Validation

- `.venv/bin/python manage.py check` passe.
- `.venv/bin/ruff check apps/orders config/settings/base.py` passe.
- `.venv/bin/pytest apps/orders/tests` a ete lance:
  - 15 tests collectes;
  - 1 test pur reference passe;
  - 14 tests DB bloques au setup;
  - PostgreSQL/PostGIS ne repond pas sur `localhost:5432`;
  - aucune assertion metier DB Orders n'a encore ete atteinte.

### Reporte volontairement au Lot 3.2+

- Machine complete de transitions.
- Retry des echecs recuperables.
- `AuditEvent`.
- Domain Events.
- `StoreOffer`.
- `DriverOffer`.
- `Delivery`.
- Matching boutique ou chauffeur.
- WhatsApp.
- IA.
- Celery lie aux commandes.
- Paiement et calcul de prix.

## Lot 3.2 - Order State Machine

### Implémenté

- Ajout d'une machine a etats explicite pour `Order`.
- Ajout des exceptions metier:
  - `InvalidOrderTransition`;
  - `OrderNotEditable`;
  - `OrderNotConfirmable`;
  - `OrderNotRetryable`;
  - `InvalidFailureCode`;
  - `InvalidCancellation`;
  - `InvalidOrderState`.
- Ajout des services de transitions:
  - `mark_awaiting_location`;
  - `mark_draft`;
  - `confirm_order`;
  - `cancel_order`;
  - `fail_order`;
  - `retry_failed_order`;
  - `start_store_matching`;
  - `mark_store_offered`;
  - `mark_store_accepted`;
  - `mark_preparing`;
  - `mark_ready_for_pickup`;
  - `start_driver_matching`;
  - `assign_driver`;
  - `mark_picked_up`;
  - `mark_in_delivery`;
  - `mark_delivered`.

### Matrice finale des transitions

- `DRAFT` -> `AWAITING_LOCATION`, `CONFIRMED`, `CANCELLED`.
- `AWAITING_LOCATION` -> `DRAFT`, `CONFIRMED`, `CANCELLED`.
- `CONFIRMED` -> `MATCHING_STORES`, `CANCELLED`.
- `MATCHING_STORES` -> `STORE_OFFERED`, `FAILED`, `CANCELLED`.
- `STORE_OFFERED` -> `STORE_ACCEPTED`, `MATCHING_STORES`, `FAILED`, `CANCELLED`.
- `STORE_ACCEPTED` -> `PREPARING`, `FAILED`, `CANCELLED`.
- `PREPARING` -> `READY_FOR_PICKUP`, `FAILED`, `CANCELLED`.
- `READY_FOR_PICKUP` -> `MATCHING_DRIVERS`, `FAILED`, `CANCELLED`.
- `MATCHING_DRIVERS` -> `DRIVER_ASSIGNED`, `FAILED`, `CANCELLED`.
- `DRIVER_ASSIGNED` -> `PICKED_UP`, `FAILED`, `CANCELLED`.
- `PICKED_UP` -> `IN_DELIVERY`, `FAILED`.
- `IN_DELIVERY` -> `DELIVERED`, `FAILED`.
- `DELIVERED` -> aucune transition normale.
- `CANCELLED` -> aucune transition normale.
- `FAILED` -> aucune transition normale; reprise uniquement via `retry_failed_order`.

### Confirmation

- `confirm_order` accepte `DRAFT` et `AWAITING_LOCATION`.
- Preconditions:
  - au moins un item;
  - `delivery_location` renseignee;
  - items valides selon les invariants OrderItem;
  - quantites strictement positives.
- Les items `UNMATCHED` restent confirmables pour respecter le catalogue souple.
- Les items `AMBIGUOUS` sont aussi acceptes dans ce MVP: l'intention textuelle reste exploitable par les boutiques, sans inventer de `Product`.
- `confirmed_at` est renseigne a la premiere confirmation et n'est pas ecrase par un appel idempotent.

### Politique d'edition

- `DRAFT`: contenu modifiable.
- `AWAITING_LOCATION`: contenu modifiable.
- `CONFIRMED` et suivants: les services CRUD standards refusent:
  - ajout d'item;
  - modification d'item;
  - suppression d'item;
  - changement de localisation de livraison;
  - changement d'instructions de livraison.

Une modification post-confirmation devra passer plus tard par un workflow explicite.

### Politique CANCELLED

- `cancel_order` exige un `cancellation_code`.
- `cancelled_at`, `cancellation_code` et `cancellation_reason` sont renseignes.
- Annulation autorisee jusqu'a `DRIVER_ASSIGNED` inclus selon la matrice.
- Annulation refusee depuis `PICKED_UP`, `IN_DELIVERY`, `DELIVERED`, `FAILED` et `CANCELLED`.
- `CANCELLED` est terminal pour la commande actuelle.

### Politique FAILED / Retry

- `fail_order` memorise:
  - `failed_from_status`;
  - `failure_code`;
  - `failure_reason`;
  - `failed_at`;
  - puis met `status=FAILED`.
- Une erreur technique ne doit pas appeler `fail_order`; `FAILED` reste un echec metier/operationnel.
- `retry_failed_order` ne prend pas de `target_status` fourni par l'appelant.
- Lors d'un retry accepte, les champs d'echec actif sont nettoyes:
  - `failed_at`;
  - `failure_code`;
  - `failure_reason`;
  - `failed_from_status`.
- L'historique permanent sera couvert par AuditEvent au Lot 3.3.

Politique par `FailureCode`:

- `NO_STORE_AVAILABLE`:
  - sources: `MATCHING_STORES`, `STORE_OFFERED`;
  - retryable: oui;
  - cible: `MATCHING_STORES`.
- `NO_DRIVER_AVAILABLE`:
  - sources: `MATCHING_DRIVERS`;
  - retryable: oui;
  - cible: `MATCHING_DRIVERS`.
- `STORE_FULFILLMENT_FAILED`:
  - sources: `STORE_ACCEPTED`, `PREPARING`, `READY_FOR_PICKUP`;
  - retryable: non pour l'instant.
- `DELIVERY_FAILED`:
  - sources: `DRIVER_ASSIGNED`, `PICKED_UP`, `IN_DELIVERY`;
  - retryable: non.
- `OTHER`:
  - sources: etats operationnels non terminaux apres confirmation;
  - retryable: non par defaut.

### Transactions et locking

- Les transitions critiques utilisent `transaction.atomic()`.
- Les services relisent `Order` via `select_for_update()` avant de valider la transition.
- La validation ne s'appuie donc pas sur une instance Python potentiellement stale.
- Les services CRUD d'items relisent aussi l'order ou l'item sous lock avant mutation.

### Politique d'idempotence

- `confirm_order` sur `CONFIRMED`: no-op idempotent.
- `mark_delivered` sur `DELIVERED`: no-op idempotent.
- Les transitions semantiques vers leur propre etat courant sont no-op.
- Les transitions depuis `DELIVERED`, `CANCELLED` ou `FAILED` vers des etats normaux restent refusees.
- `cancel_order` n'est pas idempotent sur `CANCELLED`: une nouvelle annulation est refusee.
- `fail_order` n'est pas idempotent sur `FAILED`: la reprise doit passer par `retry_failed_order`.

### Tests ajoutes

- Extension de `apps/orders/tests/test_orders.py`.
- Couverture ajoutee:
  - confirmation avec items et localisation;
  - refus de confirmation sans item ou sans localisation;
  - compatibilite `UNMATCHED` et `AMBIGUOUS`;
  - idempotence de confirmation;
  - `AWAITING_LOCATION` et retour `DRAFT`;
  - blocage des editions apres confirmation;
  - scenario complet du Definition of Done;
  - progression normale jusqu'a `DELIVERED`;
  - transitions incoherentes refusees;
  - annulation et champs associes;
  - refus d'annulation tardive;
  - `FAILED` + retry pour store matching;
  - `FAILED` + retry pour driver matching;
  - `DELIVERY_FAILED` non retryable;
  - coherence failure code / source status;
  - rejet de retry hors `FAILED`;
  - decision sur instance stale relue sous lock.

### Validation

- `.venv/bin/python manage.py check` passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`:
  - aucune migration detectee;
  - warning attendu car PostgreSQL local ne repond pas.
- `.venv/bin/ruff check apps/orders` passe.
- `.venv/bin/pytest apps/orders/tests`:
  - 36 tests collectes;
  - 1 test sans DB passe;
  - 35 tests DB bloques au setup;
  - PostgreSQL/PostGIS ne repond pas sur `localhost:5432`.
- `.venv/bin/pytest apps/identity/tests apps/stores/tests apps/drivers/tests apps/catalog/tests apps/orders/tests tests/integration`:
  - 127 tests collectes;
  - 1 test sans DB passe;
  - 126 tests bloques au setup DB;
  - meme cause PostgreSQL/PostGIS local inaccessible.

### Reporte volontairement aux Lots 3.3 et 3.4+

- `AuditEvent`.
- Historique permanent des echecs et transitions.
- Domain Events.
- Publication d'evenements.
- Effets secondaires WhatsApp, matching, offres, livraison et Celery.

## Lot 3.3 - Audit Trail

### Inspection et conventions reutilisees

- Aucune app `audit` existante n'etait presente.
- `common.models.BaseModel` est reutilise pour UUID, `created_at` et `updated_at`.
- `common.middleware.CorrelationIdMiddleware` existe deja:
  - lit `X-Correlation-ID`;
  - genere un UUID si absent;
  - expose `request.correlation_id`.
- L'audit expose donc un champ `correlation_id` et les services acceptent ce parametre sans ajouter de middleware supplementaire.
- Les services Orders existants utilisent deja `transaction.atomic()` et `select_for_update()`, l'audit est ecrit dans ces memes transactions.

### Modele AuditEvent

- Ajout de l'app transversale `apps.audit`.
- Ajout de `AuditEvent`:
  - `id` UUID;
  - `actor` nullable vers `settings.AUTH_USER_MODEL`;
  - `actor_type`;
  - `entity_type`;
  - `entity_id`;
  - `event_type`;
  - `previous_state` nullable;
  - `new_state` nullable;
  - `metadata` JSONField;
  - `correlation_id` nullable;
  - `created_at`;
  - `updated_at`.

### ActorTypes

- `USER`: action declenchee par un utilisateur authentifie.
- `SYSTEM`: action automatique ou systeme, `actor` peut etre `NULL`.
- `ADMIN`: action administrative, avec `actor` possible vers le user admin.

`actor` utilise `on_delete=SET_NULL` pour conserver l'historique si un utilisateur est supprime.

### Entity strategy

- Pas de `GenericForeignKey` pour le MVP.
- Strategie simple:
  - `entity_type = "ORDER"`;
  - `entity_id = <UUID>`.
- Enums preparees:
  - `ORDER`;
  - `STORE_OFFER`;
  - `DRIVER_OFFER`;
  - `DELIVERY`.

### EventTypes

- `ORDER_CREATED`.
- `ORDER_ITEM_ADDED`.
- `ORDER_ITEM_UPDATED`.
- `ORDER_ITEM_REMOVED`.
- `ORDER_DELIVERY_LOCATION_UPDATED`.
- `ORDER_DELIVERY_INSTRUCTIONS_UPDATED`.
- `ORDER_CONFIRMED`.
- `ORDER_CANCELLED`.
- `ORDER_FAILED`.
- `ORDER_RETRY_REQUESTED`.
- `ORDER_STATUS_CHANGED`.

Convention retenue:

- evenement semantique quand une operation metier existe;
- `ORDER_STATUS_CHANGED` pour les transitions operationnelles sans evenement plus specifique dans ce lot;
- pas de double emission systematique pour eviter le bruit.

### Metadata

- `metadata` est un `JSONField(default=dict)`.
- Les metadonnees contiennent des snapshots minimaux utiles:
  - ids;
  - noms demandes;
  - quantites serialisees en string;
  - unite;
  - statut de resolution;
  - codes/reasons d'annulation ou d'echec;
  - coordonnees de livraison sous forme longitude/latitude.
- Aucun objet Django entier n'est serialize.
- Aucun token, secret ou payload externe complet n'est stocke.

### Correlation ID

- Champ `correlation_id` nullable sur `AuditEvent`.
- Les services Orders acceptent `correlation_id`.
- Les futurs canaux REST/WhatsApp pourront passer `request.correlation_id` depuis le middleware existant.

### Service audit

- Ajout de `record_audit_event(...)`.
- Les appels directs a `AuditEvent.objects.create(...)` ne sont pas disperses dans Orders.
- Si l'ecriture d'audit echoue, l'exception remonte et la transaction metier rollback.

### Integration Orders

- `create_order_draft` -> `ORDER_CREATED`.
- `add_order_item` -> `ORDER_ITEM_ADDED`.
- `update_order_item` -> `ORDER_ITEM_UPDATED`.
- `remove_order_item` -> `ORDER_ITEM_REMOVED`.
- `set_delivery_location` -> `ORDER_DELIVERY_LOCATION_UPDATED`.
- `set_delivery_instructions` -> `ORDER_DELIVERY_INSTRUCTIONS_UPDATED`.
- `confirm_order` -> `ORDER_CONFIRMED`.
- `cancel_order` -> `ORDER_CANCELLED`.
- `fail_order` -> `ORDER_FAILED`.
- `retry_failed_order` -> `ORDER_RETRY_REQUESTED`.
- Transitions operationnelles -> `ORDER_STATUS_CHANGED`.

Pour `retry_failed_order`, le contexte d'echec est capture avant nettoyage des champs actifs de `Order`.

### Garantie transactionnelle

- AuditEvent est cree dans la meme transaction DB que la mutation Order/OrderItem.
- Pas de `transaction.on_commit()` pour l'audit.
- Si la transaction rollback:
  - la mutation metier rollback;
  - l'audit rollback aussi.
- Si AuditEvent echoue, l'operation metier critique echoue aussi afin d'eviter un etat sans trace.

### Immutabilite

- `AuditEvent.save()` refuse les updates.
- `AuditEvent.delete()` refuse les suppressions.
- `AuditEventQuerySet.delete()` refuse les suppressions en masse.
- `AuditEventAdmin` est readonly:
  - pas d'ajout manuel;
  - pas de modification;
  - pas de suppression.

### Indexes

- `(entity_type, entity_id, created_at)`.
- `actor`.
- `event_type`.
- `created_at`.
- `correlation_id`.

Le cas principal `get_order_audit_history(order)` est donc optimise par entity + ordre chronologique.

### Selectors

- `get_entity_audit_history(entity_type, entity_id)`.
- `get_order_audit_history(order)`.
- `get_audit_events_by_correlation_id(correlation_id)`.
- `get_actor_audit_history(actor)`.

Les histories sont retournes en ordre chronologique clair: `created_at`, puis `id`.

### Admin

- Ajout de `AuditEventAdmin`.
- Affichage:
  - `created_at`;
  - `event_type`;
  - `entity_type`;
  - `entity_id`;
  - `actor_type`;
  - `actor`;
  - `previous_state`;
  - `new_state`;
  - `correlation_id`.
- Filtres:
  - `event_type`;
  - `entity_type`;
  - `actor_type`;
  - `created_at`.
- Recherche:
  - `entity_id`;
  - `correlation_id`;
  - `actor__whatsapp_phone`.

### Migrations

- Ajout de `apps/audit/migrations/0001_initial.py`.
- Ajout de `apps.audit` dans `LOCAL_APPS`.

### Tests ajoutes

- `apps/audit/tests/test_audit.py`.
- Couverture:
  - creation Order -> `ORDER_CREATED`;
  - add/update/remove item;
  - confirmation et previous/new state;
  - cancellation avec actor admin et metadata;
  - failed + retry avec preservation du contexte d'echec;
  - actor system nullable;
  - correlation id selector;
  - immutabilite instance et queryset;
  - rollback Order + Audit;
  - echec audit rollback la mutation metier.

### Validation

- `.venv/bin/python manage.py check` passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`:
  - aucune migration manquante;
  - warning attendu car PostgreSQL local ne repond pas.
- `.venv/bin/ruff check apps/audit apps/orders config/settings/base.py` passe.
- `.venv/bin/pytest apps/audit/tests apps/orders/tests`:
  - 46 tests collectes;
  - 1 test sans DB passe;
  - 45 tests DB bloques au setup;
  - PostgreSQL/PostGIS ne repond pas sur `localhost:5432`.
- Suite globale pertinente:
  - 137 tests collectes;
  - 1 test sans DB passe;
  - 136 tests bloques au setup DB;
  - meme cause PostgreSQL/PostGIS local inaccessible.

### Reporte volontairement au Lot 3.4+

- Domain Event Bus.
- Domain Events tels que `OrderConfirmed`, `OrderFailed`, etc.
- Publication post-commit.
- Transactional outbox.
- Celery dispatch.
- StoreOffer.
- DriverOffer.
- Delivery.
- Matching.
- WhatsApp.
- IA.
- Paiement.

## Lot 3.4 - Domain Events + Transactions

### Inspection et conventions reutilisees

- `common/events` existait mais etait vide.
- Aucune abstraction EventBus existante n'etait implementee.
- Celery est configure dans `config/celery.py`, mais aucun usage event-driven n'existait.
- Aucun `transaction.on_commit()` existant n'a ete trouve dans le code metier.
- Les services Orders possedent deja:
  - `transaction.atomic()`;
  - `select_for_update()`;
  - AuditEvent dans la meme transaction metier.

### Abstraction DomainEvent

- Ajout de `common.events.domain.DomainEvent`.
- Base dataclass immutable avec:
  - `event_id` UUID;
  - `occurred_at`;
  - `correlation_id` nullable.
- Les evenements transportent des IDs et des snapshots simples, jamais d'objet Django complet.
- Les payloads sont facilement serialisables pour une future outbox ou Celery.

### Dispatcher / registry

- Ajout de `common.events.bus`.
- API:
  - `subscribe(event_type, handler)`;
  - `unsubscribe(event_type, handler)`;
  - `publish(event)`;
  - `clear_subscribers()`;
  - `subscribed(...)` comme context manager de test.
- Registry in-process:
  - mapping `EventClass -> handlers`.
- `publish()` appelle les handlers synchronement.
- En cas d'erreur handler:
  - exception loggee avec `event_type`, `event_id`, handler;
  - exception remontee;
  - aucune mutation automatique de `Order`;
  - jamais de passage automatique a `FAILED`.

### Evenements Orders

- Ajout de `apps.orders.domain.events`.
- `OrderCreated`:
  - `event_id`;
  - `occurred_at`;
  - `order_id`;
  - `customer_id`;
  - `correlation_id`.
- `OrderConfirmed`:
  - `event_id`;
  - `occurred_at`;
  - `order_id`;
  - `customer_id`;
  - `correlation_id`.
- `OrderCancelled`:
  - `event_id`;
  - `occurred_at`;
  - `order_id`;
  - `customer_id`;
  - `cancellation_code`;
  - `correlation_id`.
- `OrderFailed`:
  - `event_id`;
  - `occurred_at`;
  - `order_id`;
  - `failure_code`;
  - `failed_from_status`;
  - `correlation_id`.
- `OrderRetryRequested`:
  - `event_id`;
  - `occurred_at`;
  - `order_id`;
  - `failure_code` precedent;
  - `retry_target_status`;
  - `correlation_id`.

### Integration Orders

- `create_order_draft` publie `OrderCreated` apres commit.
- `confirm_order` publie `OrderConfirmed` apres commit uniquement si la confirmation est effective.
- `cancel_order` publie `OrderCancelled` apres commit.
- `fail_order` publie `OrderFailed` apres commit.
- `retry_failed_order` publie `OrderRetryRequested` apres commit.
- Les transitions operationnelles generiques ne publient pas encore de DomainEvent specifique.
- Les modifications d'items ne publient pas encore de DomainEvent, car aucun autre domaine n'en a besoin dans ce lot.

### Strategie transaction.on_commit

- Les mutations Order/OrderItem et AuditEvent restent dans `transaction.atomic()`.
- Les DomainEvents sont planifies via `transaction.on_commit()`.
- Si la transaction rollback:
  - mutation metier absente;
  - AuditEvent absent;
  - DomainEvent non publie.
- Si le commit reussit:
  - `publish(event)` est appele apres commit.

### Idempotence

- Les no-op idempotents ne publient pas de nouvel evenement.
- Exemple:
  - premier `confirm_order()` effectif -> `OrderConfirmed`;
  - second `confirm_order()` sur `CONFIRMED` -> aucun nouvel event.
- `OrderRetryRequested` capture `failure_code` avant nettoyage des champs `Order`.

### Correlation ID

- `correlation_id` est propage depuis les services Orders vers:
  - AuditEvent;
  - DomainEvent.
- L'absence de correlation id est acceptee.

### Tests ajoutes

- Ajout de `apps/orders/tests/test_order_domain_events.py`.
- Couverture:
  - `OrderCreated` pas publie avant commit, publie apres commit;
  - `OrderConfirmed` avec audit present et payload correct;
  - rollback empeche Order, AuditEvent et DomainEvent;
  - confirmation repetee ne publie pas deux fois;
  - `OrderCancelled` seulement pour transition effective;
  - `OrderFailed` payload failure code/source;
  - `OrderRetryRequested` conserve le failure code precedent;
  - echec handler apres commit ne transforme pas Order en `FAILED`;
  - propagation `correlation_id`.

### Validation

- `.venv/bin/python manage.py check` passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`:
  - aucune migration manquante;
  - warning attendu car PostgreSQL local ne repond pas.
- `.venv/bin/ruff check common/events apps/orders` passe.
- `.venv/bin/pytest apps/orders/tests apps/audit/tests`:
  - 54 tests collectes;
  - 1 test sans DB passe;
  - 53 tests DB bloques au setup;
  - PostgreSQL/PostGIS ne repond pas sur `localhost:5432`.
- Suite globale pertinente:
  - 145 tests collectes;
  - 1 test sans DB passe;
  - 144 tests bloques au setup DB;
  - meme cause PostgreSQL/PostGIS local inaccessible.

### Limite MVP sans transactional outbox

- `transaction.on_commit()` garantit que le DomainEvent n'est pas publie avant commit.
- Il ne garantit pas une livraison durable si le processus meurt immediatement apres le commit et avant ou pendant la publication.
- Evolution future recommandee:
  - `OutboxEvent` persiste dans la meme transaction;
  - worker/Celery dispatch;
  - retries et deduplication durable par `event_id`.

### Pret pour Lot 3.5 et futures phases

- Orders ne depend pas du matching, WhatsApp, Delivery ou notifications.
- Les handlers futurs pourront s'abonner a `OrderConfirmed`, `OrderFailed`, etc.
- Les consumers pourront relire `Order` depuis la DB via `order_id`.
- `event_id` et `correlation_id` preparent l'idempotence et le tracing inter-canaux.

## Lot 3.5 - API Orders et validation finale Phase 3

### Objectif

Exposer les use cases Orders via DRF sans dupliquer la logique metier:

HTTP request -> authentication -> serializer/input validation -> service applicatif -> serializer/output.

La couche API ne modifie pas directement `Order.status`, les timestamps metier, les champs d'echec ou les champs d'annulation.

### Fichiers crees/modifies

- `apps/orders/api/serializers.py`
- `apps/orders/api/views.py`
- `apps/orders/api/urls.py`
- `apps/orders/tests/test_orders_api.py`
- `apps/orders/admin.py`
- `config/api_urls.py`
- `Implementation.md`

### Architecture finale Orders

- `apps.orders.infrastructure.models` contient `Order` et `OrderItem`.
- `apps.orders.domain.enums` contient les statuts, codes d'annulation, codes d'echec et statuts de resolution.
- `apps.orders.domain.rules` contient la matrice de transitions, les statuts editables, les sources d'echec autorisees et les cibles retry.
- `apps.orders.application.services` contient les use cases transactionnels.
- `apps.orders.application.selectors` contient les lectures optimisees.
- `apps.orders.domain.events` contient les DomainEvents Orders.
- `apps.orders.api` expose le canal REST.
- `apps.audit` conserve l'historique append-only.
- `common.events` publie les DomainEvents apres commit.

### Modeles

- `Order`:
  - UUID interne;
  - reference publique `ORD-YYMMDD-XXXX`;
  - customer vers `settings.AUTH_USER_MODEL`;
  - status;
  - localisation PostGIS nullable;
  - adresse/instructions de livraison;
  - devise;
  - timestamps de confirmation, annulation, completion et echec;
  - codes/raisons d'annulation et d'echec;
  - `failed_from_status`;
  - timestamps techniques.
- `OrderItem`:
  - UUID interne;
  - order;
  - `requested_name` obligatoire;
  - quantity decimal strictement positive;
  - unit;
  - notes;
  - Product/ProductVariant optionnels;
  - resolution_status;
  - timestamps.

### State Machine

Matrice finale:

- `DRAFT` -> `AWAITING_LOCATION`, `CONFIRMED`, `CANCELLED`
- `AWAITING_LOCATION` -> `DRAFT`, `CONFIRMED`, `CANCELLED`
- `CONFIRMED` -> `MATCHING_STORES`, `CANCELLED`
- `MATCHING_STORES` -> `STORE_OFFERED`, `FAILED`, `CANCELLED`
- `STORE_OFFERED` -> `STORE_ACCEPTED`, `MATCHING_STORES`, `FAILED`, `CANCELLED`
- `STORE_ACCEPTED` -> `PREPARING`, `FAILED`, `CANCELLED`
- `PREPARING` -> `READY_FOR_PICKUP`, `FAILED`, `CANCELLED`
- `READY_FOR_PICKUP` -> `MATCHING_DRIVERS`, `FAILED`, `CANCELLED`
- `MATCHING_DRIVERS` -> `DRIVER_ASSIGNED`, `FAILED`, `CANCELLED`
- `DRIVER_ASSIGNED` -> `PICKED_UP`, `FAILED`, `CANCELLED`
- `PICKED_UP` -> `IN_DELIVERY`, `FAILED`
- `IN_DELIVERY` -> `DELIVERED`, `FAILED`
- `DELIVERED` -> aucune transition normale
- `CANCELLED` -> aucune transition normale
- `FAILED` -> retry conditionnel uniquement via `retry_failed_order()`

### Failure / Retry

- `NO_STORE_AVAILABLE`
  - sources autorisees: `MATCHING_STORES`, `STORE_OFFERED`;
  - retryable: oui;
  - target: `MATCHING_STORES`.
- `NO_DRIVER_AVAILABLE`
  - sources autorisees: `MATCHING_DRIVERS`;
  - retryable: oui;
  - target: `MATCHING_DRIVERS`.
- `STORE_FULFILLMENT_FAILED`
  - sources autorisees: `STORE_ACCEPTED`, `PREPARING`, `READY_FOR_PICKUP`;
  - retryable: non.
- `DELIVERY_FAILED`
  - sources autorisees: `DRIVER_ASSIGNED`, `PICKED_UP`, `IN_DELIVERY`;
  - retryable: non.
- `OTHER`
  - sources autorisees: etats operationnels de matching, preparation et livraison;
  - retryable: non.

### Services metier

- Construction draft:
  - `create_order_draft()`
  - `add_order_item()`
  - `update_order_item()`
  - `remove_order_item()`
  - `set_delivery_location()`
  - `set_delivery_instructions()`
- Transitions:
  - `mark_awaiting_location()`
  - `mark_draft()`
  - `confirm_order()`
  - `cancel_order()`
  - `fail_order()`
  - `retry_failed_order()`
  - `start_store_matching()`
  - `mark_store_offered()`
  - `mark_store_accepted()`
  - `mark_preparing()`
  - `mark_ready_for_pickup()`
  - `start_driver_matching()`
  - `assign_driver()`
  - `mark_picked_up()`
  - `mark_in_delivery()`
  - `mark_delivered()`

### Audit Trail

- Les services Orders ecrivent `AuditEvent` dans la meme transaction que le changement metier.
- Les evenements d'audit couvrent:
  - creation Order;
  - ajout/update/suppression item;
  - localisation/instructions;
  - confirmation;
  - annulation;
  - echec;
  - retry;
  - transitions operationnelles generiques.
- `AuditEvent` est append-only et readonly en admin.

### Domain Events

- `OrderCreated`
- `OrderConfirmed`
- `OrderCancelled`
- `OrderFailed`
- `OrderRetryRequested`

Les DomainEvents sont planifies avec `transaction.on_commit()` et ne sont pas publies si la transaction rollback.

### Endpoints API

Tous les endpoints Orders exigent `IsAuthenticated`.

- `POST /api/v1/orders/`
  - permission: utilisateur authentifie;
  - use case: `create_order_draft(customer=request.user)`;
  - ignore tout `customer_id` fourni par le client.
- `GET /api/v1/orders/`
  - permission: utilisateur authentifie;
  - use case: queryset scoping;
  - client: ses commandes uniquement;
  - staff: toutes les commandes;
  - filtre: `status`.
- `GET /api/v1/orders/{order_id}/`
  - permission: proprietaire ou staff;
  - retourne Order + items + produits resumes.
- `GET /api/v1/orders/by-reference/{reference}/`
  - permission: proprietaire ou staff;
  - resolution par reference publique.
- `GET /api/v1/orders/{order_id}/items/`
  - permission: proprietaire ou staff;
  - use case lecture: `get_order_items(order)`.
- `POST /api/v1/orders/{order_id}/items/`
  - permission: proprietaire ou staff;
  - use case: `add_order_item()`.
- `PATCH /api/v1/orders/{order_id}/items/{item_id}/`
  - permission: proprietaire ou staff;
  - use case: `update_order_item()`.
- `DELETE /api/v1/orders/{order_id}/items/{item_id}/`
  - permission: proprietaire ou staff;
  - use case: `remove_order_item()`.
- `PATCH /api/v1/orders/{order_id}/delivery-location/`
  - permission: proprietaire ou staff;
  - input: `latitude`, `longitude`, `address_label`;
  - conversion: `Point(longitude, latitude, srid=4326)`;
  - use case: `set_delivery_location()`.
- `PATCH /api/v1/orders/{order_id}/delivery-instructions/`
  - permission: proprietaire ou staff;
  - use case: `set_delivery_instructions()`.
- `POST /api/v1/orders/{order_id}/confirm/`
  - permission: proprietaire ou staff;
  - use case: `confirm_order()`.
- `POST /api/v1/orders/{order_id}/cancel/`
  - permission: proprietaire ou staff;
  - input: `code`, `reason`;
  - use case: `cancel_order()`.
- `POST /api/v1/orders/{order_id}/retry/`
  - permission: proprietaire ou staff;
  - use case: `retry_failed_order()`;
  - n'accepte pas de `target_status`.
- `GET /api/v1/orders/{order_id}/history/`
  - permission: proprietaire ou staff;
  - use case: `get_order_audit_history(order)`;
  - readonly.

### Permissions

- Les clients ne voient que leurs commandes.
- Les acces cross-user retournent 404 par scoping de queryset.
- Le customer est toujours derive de `request.user` a la creation.
- Les champs sensibles sont readonly dans les serializers de sortie:
  - reference;
  - customer;
  - status;
  - timestamps metier;
  - failure/cancellation fields.
- Le staff Django peut consulter toutes les commandes via l'API.

### Validation et frontiere de confiance

- L'API Phase 3 expose `product_id`, `product_variant_id` et `resolution_status` sur les endpoints item pour supporter les orchestrateurs internes et tests d'integration.
- Les invariants restent imposes par les services/modeles:
  - `MATCHED` exige un Product;
  - `UNMATCHED` refuse un Product;
  - ProductVariant doit appartenir au Product;
  - quantity strictement positive.
- Pour une API client publique plus stricte, une phase ulterieure pourra separer serializer client et serializer interne afin d'eviter qu'un client declare arbitrairement une resolution IA.

### Erreurs API

Les exceptions metier Orders sont converties en erreurs DRF compatibles avec le handler global:

- `OrderNotEditable` -> 409
- `InvalidOrderTransition` -> 409
- `OrderNotRetryable` -> 409
- `InvalidFailureCode` -> 409
- `InvalidOrderState` -> 409
- `OrderNotConfirmable` -> 400
- `InvalidCancellation` -> 400
- validations serializer -> 400
- objets hors scope -> 404

### Transactions / locking

- Les services critiques utilisent `transaction.atomic()`.
- Les commandes/items modifies sont relus avec `select_for_update()`.
- `Order + AuditEvent` commitent ensemble.
- Les DomainEvents sont publies apres commit via `transaction.on_commit()`.
- Une erreur technique de handler apres commit ne transforme pas automatiquement la commande en `FAILED`.

### Idempotence

- `confirm_order()` sur une commande deja `CONFIRMED` retourne la commande sans nouvel audit ni nouvel `OrderConfirmed`.
- Les transitions operationnelles semantiques sont idempotentes quand la commande est deja dans le statut cible.
- Les endpoints API respectent cette politique en deleguant aux services.
- `retry_failed_order()` n'accepte jamais de cible fournie par l'appelant.

### Indexes / performances

- La liste/detail Orders utilisent `select_related("customer")` et `prefetch_related("items__product", "items__product_variant")`.
- Les indexes Phase 3 couvrent les cas principaux:
  - reference unique;
  - customer;
  - status;
  - customer + status;
  - created_at;
  - index spatial sur `delivery_location`;
  - audit par entity, actor, event_type, correlation_id et created_at.

### Django Admin

- `OrderAdmin`:
  - recherche par reference et `customer__whatsapp_phone`;
  - filtres status/created_at;
  - inline `OrderItem`;
  - champs sensibles readonly, notamment reference, status, timestamps metier, cancellation/failure fields.
- `OrderItemAdmin`:
  - recherche par reference commande, requested_name, Product et ProductVariant.
- `AuditEventAdmin`:
  - readonly;
  - pas d'ajout, modification ou suppression standard.

### Migrations

- Aucune migration nouvelle pour le Lot 3.5.
- `makemigrations --check --dry-run` ne detecte aucune migration manquante.

### Tests ajoutes

- `apps/orders/tests/test_orders_api.py`
- Couverture:
  - scenario API principal draft -> items -> location -> confirm;
  - catalogue souple avec Product nullable;
  - confirmation sans localisation;
  - permissions cross-user;
  - modification apres confirmation;
  - cancellation;
  - FAILED/retry retryable;
  - FAILED/retry non retryable;
  - idempotence confirmation;
  - history readonly/scoped;
  - audit et DomainEvents observes via API.

### Validation executee

- `.venv/bin/ruff check apps/orders config/api_urls.py`
  - resultat: passe.
- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning attendu: PostgreSQL local indisponible.
- `.venv/bin/pytest apps/orders/tests apps/audit/tests`
  - resultat: bloque au setup DB;
  - cause: PostgreSQL/PostGIS ne repond pas sur `localhost:5432`.
- Suite globale pertinente tentee:
  - `apps/identity/tests apps/stores/tests apps/drivers/tests apps/catalog/tests apps/orders/tests apps/audit/tests tests/integration`;
  - meme blocage DB attendu/observe pendant le setup.

### Limites volontaires Phase 3

Non inclus:

- WhatsApp webhook;
- conversations;
- parsing LLM;
- StoreOffer;
- DriverOffer;
- matching boutiques;
- matching drivers;
- Delivery;
- live tracking;
- notifications Celery;
- stock;
- paiement;
- transactional outbox durable.

### Ce que les prochaines phases peuvent consommer

- WhatsApp peut appeler les use cases Orders sans passer par REST.
- L'IA peut produire des `OrderItem` structures avec catalogue souple.
- Le futur Matching peut consommer `OrderConfirmed`.
- Le futur Matching peut appeler `start_store_matching()` puis les transitions suivantes.
- Les actions restent auditees.
- Les reactions inter-domaines peuvent utiliser les DomainEvents.
- Les workflows futurs peuvent propager `correlation_id` de webhook vers audit et events.

## Phase 4 - Lot 4.1 Messaging Core & Channel Identity

### Conventions reutilisees

- Nouveau domaine modulaire `apps.messaging`, distinct de `conversations`.
- Modeles persistants dans `apps/messaging/infrastructure/models.py`.
- Reexport public via `apps/messaging/models.py`.
- Enums Django `TextChoices` dans `apps/messaging/domain/enums.py`.
- DTOs immutables avec `dataclass(frozen=True, kw_only=True)`, comme les DomainEvents.
- UUID, `created_at`, `updated_at` via `common.models.BaseModel`.
- Selectors et services dans `apps/messaging/application`.
- Contraintes DB explicites avec `UniqueConstraint`, `CheckConstraint` et indexes nommes.

### ChannelIdentity

- `ChannelIdentity` represente le lien explicite entre un `User` metier et une identite externe.
- Champs: `user`, `channel`, `external_user_id`, `external_username`, `phone_number`, `metadata`, `is_active`.
- `external_user_id` est une chaine afin de supporter des identifiants non numeriques.
- Un meme `User` peut avoir plusieurs canaux.
- Une meme identite externe ne peut appartenir qu'a un seul `User` pour un canal donne.

### Strategie whatsapp_phone

- `User.whatsapp_phone` reste en place et continue d'etre le login central actuel.
- Aucune relation existante vers `User`, notamment `Order.customer`, n'a ete modifiee.
- La migration `apps/messaging/migrations/0002_backfill_whatsapp_channel_identities.py`
  cree une `ChannelIdentity` `WHATSAPP` pour chaque `User` ayant un `whatsapp_phone`.
- `phone_number` conserve la valeur normalisee existante, par exemple `+237699887766`.
- `external_user_id` est derive en retirant le `+`, par exemple `237699887766`,
  ce qui correspond au format attendu des plateformes de messagerie.
- Le champ historique pourra etre retire dans une phase ulterieure seulement apres
  migration des usages applicatifs/admin/API qui le referencent encore.

### Messaging Core

- `MessagingChannel`: `WHATSAPP`, `TELEGRAM`.
- `MessageDirection`: `INBOUND`, `OUTBOUND`.
- `MessageType`: `TEXT`, `LOCATION`, `IMAGE`, `DOCUMENT`, `AUDIO`, `VIDEO`,
  `INTERACTIVE`, `UNKNOWN`.
- `MessageStatus`: `RECEIVED`, `PROCESSING`, `PROCESSED`, `FAILED`, `SENT`, `IGNORED`.

### DTOs normalises

- `MessageLocation` transporte `latitude`, `longitude`, `name`, `address`.
- Validation latitude `[-90, 90]` et longitude `[-180, 180]`.
- `NormalizedInboundMessage` represente l'entree commune post-adapter.
- `NormalizedOutboundMessage` represente la sortie commune pre-provider.
- Aucun payload WhatsApp ou Telegram specifique n'est interprete dans ce lot.

### MessageEvent

- `MessageEvent` trace les echanges techniques/applicatifs avec les plateformes.
- Il est volontairement separe de `AuditEvent`, qui reste l'historique metier.
- Champs principaux: canal, direction, ids externes, type, statut, payload brut,
  metadata, timestamps de reception/envoi/traitement.
- `raw_payload` est conserve pour debug/investigation, sans devenir source de verite metier.

### Idempotence et contraintes

- `ChannelIdentity`: unique sur `(channel, external_user_id)`.
- `MessageEvent`: unique conditionnel sur `(channel, external_message_id)` quand
  `external_message_id` n'est pas nul.
- `WHATSAPP / 123` et `TELEGRAM / 123` restent distincts.
- Indexes principaux: recherche par user/canal, identite externe, message externe,
  utilisateur externe, chat externe, statut et creation.

### Provider contract

- `MessagingProvider` definit un contrat abstrait `send(message)`.
- `ProviderCapabilities` expose un jeu leger de capacites: text, location,
  interactive, media.
- Aucun provider WhatsApp ou Telegram reel n'est implemente dans ce lot.

### Selectors et services

- Selectors:
  - `get_channel_identity`;
  - `get_user_channel_identities`;
  - `get_message_event`;
  - `get_message_event_by_external_id`;
  - `get_recent_message_events`.
- Services:
  - `resolve_user_from_channel_identity`;
  - `create_channel_identity`;
  - `get_or_create_channel_identity`;
  - `record_inbound_message_event`;
  - `record_outbound_message_event`.

### Django Admin

- `ChannelIdentityAdmin`: affichage user, canal, id externe, username, telephone,
  actif, date de creation.
- `MessageEventAdmin`: affichage canal, direction, id message externe, user externe,
  type, statut, dates.
- Filtres canal/direction/type/statut et recherche par ids externes.
- `raw_payload` est readonly.

### Tests ajoutes

- `apps/messaging/tests/test_messaging.py`
- Couverture:
  - plusieurs canaux pour un meme `User`;
  - unicite externe par canal;
  - meme id externe autorise sur deux canaux differents;
  - resolution channel identity -> user;
  - DTO inbound text/location/unknown;
  - validation `MessageLocation`;
  - `MessageEvent` inbound/outbound;
  - idempotence `(channel, external_message_id)`.

### Validation executee

- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning: connexion DB indisponible pendant la verification d'historique.
- `.venv/bin/ruff check apps/messaging config/settings/base.py`
  - resultat: passe.
- `.venv/bin/pytest apps/identity/tests apps/messaging/tests -q`
  - resultat partiel: 8 tests sans DB passent;
  - 21 tests DB bloques au setup;
  - cause observee: PostgreSQL ne repond pas sur `localhost:5432` depuis ce shell.
- `pg_isready -h localhost -p 5432`
  - resultat: `no response`.

### Limites volontaires Phase 4.1

Non inclus:

- webhook WhatsApp;
- Meta Cloud API;
- Telegram Bot API;
- webhook Telegram;
- Conversation et ConversationMessage;
- IA et parsing d'intention;
- creation de commande depuis message;
- matching;
- notifications Celery;
- commandes ou boutons specifiques aux plateformes.

## Phase 4 - Lot 4.2 Conversation Core

### Conventions reutilisees

- Nouveau domaine modulaire `apps.conversations`, separe de `apps.messaging`.
- Modeles persistants dans `apps/conversations/infrastructure/models.py`.
- Reexport public via `apps/conversations/models.py`.
- Enum Django `TextChoices` dans `apps/conversations/domain/enums.py`.
- DTO applicatif immutable dans `apps/conversations/domain/dtos.py`.
- Erreurs domaine dans `apps/conversations/domain/exceptions.py`.
- Services/selectors dans `apps/conversations/application`.
- UUID, `created_at`, `updated_at` via `common.models.BaseModel`.
- Transactions Django et contraintes DB nommees pour l'idempotence/concurrence.

### Modeles finaux

- `Conversation`:
  - `user`;
  - `channel`;
  - `channel_identity`;
  - `external_chat_id`;
  - `status`;
  - `context`;
  - `started_at`;
  - `last_activity_at`;
  - `closed_at`.
- `ConversationMessage`:
  - `conversation`;
  - `message_event` nullable en `OneToOneField`;
  - `direction`;
  - `message_type`;
  - `text`;
  - `location`;
  - `metadata`;
  - `occurred_at`.
- `ConversationStatus`: `ACTIVE`, `CLOSED`.

### Relations Messaging / Conversation

- `MessageEvent` reste la trace transport externe.
- `ConversationMessage` est l'integration logique dans une conversation.
- `ConversationMessage.message_event` est nullable et utilise `on_delete=SET_NULL`
  pour preserver l'historique conversationnel si une trace technique est supprimee.
- `Conversation.channel` est conserve comme snapshot/index operationnel.
- Le service et le `save()` de `Conversation` alignent `channel` et `user` sur
  `channel_identity`, afin d'eviter une divergence applicative.

### Strategie de resolution

- `get_or_create_active_conversation(channel_identity, external_chat_id)` cherche
  une conversation `ACTIVE` pour l'identite et le chat.
- Une conversation `CLOSED` n'est pas reutilisee.
- Meme `User` sur WhatsApp et Telegram produit par defaut deux conversations
  distinctes.
- Meme identite Telegram avec deux `external_chat_id` differents produit deux
  contextes conversationnels distincts.

### Strategie utilisateurs inconnus

- Pour WhatsApp, `ingest_inbound_message()` peut creer/resoudre le `User` via la
  politique existante `get_or_create_user_by_whatsapp`, puis creer/resoudre la
  `ChannelIdentity`.
- Pour Telegram inconnu, le service leve `UnknownChannelIdentityError`, car le
  modele `User` actuel requiert encore `whatsapp_phone`. Un workflow explicite de
  creation/liaison Telegram est laisse a une phase ulterieure.
- La contrainte `ChannelIdentity(channel, external_user_id)` reste la protection
  finale contre les doublons.

### Ingestion inbound

- `ingest_inbound_message(normalized_message, message_event)`:
  - resout ou cree l'identite selon la politique ci-dessus;
  - resout le `User`;
  - trouve ou cree la `Conversation` active;
  - cree un `ConversationMessage`;
  - met a jour `last_activity_at`;
  - retourne `InboundMessageContext`.

### Idempotence et concurrence

- `ConversationMessage.message_event` est un `OneToOneField`: un meme
  `MessageEvent` ne peut produire qu'un seul message conversationnel.
- En cas de retraitement, `ingest_inbound_message()` retourne le message existant
  sans mettre a jour `last_activity_at`.
- Une seule conversation active est autorisee par `channel_identity` et
  `external_chat_id`.
- Une contrainte dediee couvre aussi le cas `external_chat_id IS NULL`.
- Le resolver utilise `transaction.atomic()`, `select_for_update()` sur les lignes
  trouvees, et rattrape `IntegrityError` pour les courses de creation.

### Context management

- Helpers centralises:
  - `get_conversation_context`;
  - `update_conversation_context`;
  - `set_context_value`;
  - `remove_context_value`;
  - `clear_conversation_context`.
- `context` reste un stockage conversationnel temporaire et n'est pas une source
  de verite metier Orders/Catalog/Delivery.

### Outbound et fermeture

- `record_outbound_message()` enregistre le message bot/systeme dans la conversation
  sans provider reel.
- `close_conversation()` verrouille la ligne, passe le status a `CLOSED` et renseigne
  `closed_at`.
- Aucun evenement Audit n'est cree automatiquement afin d'eviter le bruit non metier.

### Selectors

- `get_conversation`;
- `get_active_conversation`;
- `get_user_conversations`;
- `get_conversation_messages`;
- `get_conversation_with_messages`.

### Admin

- `ConversationAdmin`: id, user, channel, channel identity, status, external chat,
  timestamps.
- `ConversationMessageAdmin`: conversation, direction, type, apercu texte,
  `occurred_at`.
- Context/location/metadata sont affiches prudemment en readonly quand pertinent.

### Migrations

- `apps/conversations/migrations/0001_initial.py` cree `Conversation` et
  `ConversationMessage`, avec indexes et contraintes.
- `apps.conversations` est ajoute a `LOCAL_APPS`.

### Tests ajoutes

- `apps/conversations/tests/test_conversations.py`
- Couverture:
  - premier inbound cree une conversation active;
  - deuxieme inbound meme identite/chat reutilise la conversation;
  - conversation fermee puis nouveau resolver cree une nouvelle conversation;
  - historique inbound/outbound/inbound ordonne;
  - deux canaux pour le meme user restent independants;
  - `external_chat_id` separe les contextes;
  - idempotence par `MessageEvent`;
  - `last_activity_at` ne bouge pas sur duplicate;
  - context set/update/remove/clear;
  - identite WhatsApp inconnue cree user + identity;
  - identite Telegram inconnue refusee explicitement;
  - unicite active protege la creation concurrente.

### Validation executee

- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning: connexion DB indisponible pendant la verification d'historique.
- `.venv/bin/ruff check apps/conversations apps/messaging config/settings/base.py`
  - resultat: passe.
- `.venv/bin/pytest apps/messaging/tests apps/conversations/tests -q`
  - resultat partiel: 8 tests sans DB passent;
  - 20 tests DB bloques au setup;
  - cause observee: PostgreSQL ne repond pas sur `localhost:5432` depuis ce shell.
- Suites Phase 2/3 pertinentes:
  - `apps/stores/tests apps/drivers/tests apps/catalog/tests apps/orders/tests apps/audit/tests tests/integration`;
  - resultat: 1 test sans DB passe, 138 tests DB bloques au setup;
  - meme cause PostgreSQL locale inaccessible depuis ce shell.

### Limites volontaires Phase 4.2

Non inclus:

- OpenAI/LLM;
- extraction d'intention;
- parsing de commande;
- creation Order depuis Conversation;
- webhook WhatsApp ou Telegram;
- providers reels;
- matching;
- Delivery;
- prompts;
- reponse automatique intelligente.

## Phase 4 - Lot 4.3 WhatsApp Adapter

### Documentation Meta verifiee

- Graph API changelog: version latest `v26.0`, introduite le 29 juillet 2026.
- WhatsApp Cloud API messages endpoint:
  `POST https://graph.facebook.com/{version}/{phone-number-id}/messages`.
- Webhook verification Meta:
  `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`.
- Webhook security:
  signature `X-Hub-Signature-256` calculee en HMAC-SHA256 sur le raw body avec
  l'App Secret.
- Messages utilises: `text` et `location`.
- Status updates reconnus: `sent`, `delivered`, `read`, `failed`.

### Configuration

- `WHATSAPP_ENABLED`, defaut `False`.
- `WHATSAPP_ACCESS_TOKEN`.
- `WHATSAPP_PHONE_NUMBER_ID`.
- `WHATSAPP_BUSINESS_ACCOUNT_ID`.
- `WHATSAPP_VERIFY_TOKEN`.
- `WHATSAPP_APP_SECRET`.
- `WHATSAPP_REQUEST_TIMEOUT`, defaut `10.0`.
- `META_GRAPH_API_VERSION`, defaut `v26.0`.
- `.env.example` contient uniquement des placeholders.
- Si WhatsApp est desactive, Django demarre sans credentials Meta.
- Si WhatsApp est active ou si le provider est appele explicitement, une
  `WhatsAppConfigurationError` signale les variables obligatoires manquantes.

### Structure

- Nouveau module `apps.whatsapp`.
- `apps.whatsapp` depend de `apps.messaging` et `apps.conversations`.
- `apps.messaging` ne depend pas de WhatsApp.
- Sous-modules:
  - `adapters/inbound.py`;
  - `adapters/outbound.py`;
  - `adapters/statuses.py`;
  - `api/views.py`;
  - `api/urls.py`;
  - `application/services.py`;
  - `providers/cloud_api.py`;
  - `conf.py`;
  - `security.py`;
  - `exceptions.py`.

### Webhook

- Route racine ajoutee:
  - `GET /webhooks/whatsapp/`;
  - `POST /webhooks/whatsapp/`.
- `GET` valide le verify token et retourne uniquement le challenge en cas de
  succes.
- `POST` valide la signature avant parsing JSON ou traitement.
- Les throttles DRF globaux sont desactives sur cette view pour eviter une
  dependance Redis avant validation Meta.

### Security

- Validation HMAC-SHA256 dans `apps/whatsapp/security.py`.
- Utilise le raw body et `WHATSAPP_APP_SECRET`.
- Comparaison constante avec `hmac.compare_digest`.
- Les tokens/secrets ne sont pas loggues.

### Inbound

- `parse_whatsapp_webhook(payload)` retourne une liste de `NormalizedInboundMessage`.
- Un webhook peut contenir plusieurs `entry/change/message`.
- Types:
  - `text` -> `MessageType.TEXT` avec `text.body`;
  - `location` -> `MessageType.LOCATION` avec `MessageLocation`;
  - `image`, `document`, `audio`, `video`, `interactive` sont reconnus;
  - le reste devient `MessageType.UNKNOWN`.
- Les donnees Meta specifiques restent dans `metadata["whatsapp"]` ou
  `raw_payload`.

### Status updates

- Les `statuses` WhatsApp ne creent pas de `ConversationMessage`.
- Ils mettent a jour le `MessageEvent` correspondant quand il existe.
- Mapping minimal:
  - `sent` -> `SENT`;
  - `delivered` -> `PROCESSED`;
  - `read` -> `PROCESSED`;
  - `failed` -> `FAILED`.
- Aucun status WhatsApp n'est confondu avec `Order.status`.

### Ingestion

- `process_whatsapp_webhook(payload)`:
  - parse les messages entrants;
  - cree/recupere `MessageEvent` idempotent;
  - saute les doublons deja attaches a une conversation;
  - appelle `ingest_inbound_message`;
  - marque le `MessageEvent` `PROCESSED`;
  - applique les status updates separement.
- La transaction englobe `MessageEvent`, `ConversationMessage` et
  `Conversation.last_activity_at`.

### Outbound

- `WhatsAppProvider` implemente `MessagingProvider`.
- Methodes:
  - `send(message)`;
  - `send_text(recipient_external_id, text)`;
  - `send_location(recipient_external_id, location)`.
- Payload text:
  - `messaging_product=whatsapp`;
  - `recipient_type=individual`;
  - `to`;
  - `type=text`;
  - `text.body`.
- Payload location:
  - `type=location`;
  - `location.latitude`;
  - `location.longitude`;
  - `location.name/address` si presents.
- Apres acceptation Meta, un `MessageEvent OUTBOUND` est enregistre avec le
  `wamid` retourne quand disponible.
- Le client HTTP est centralise dans `providers/cloud_api.py` avec timeout explicite.

### Provider errors

- `WhatsAppConfigurationError`.
- `WhatsAppAPIError`.
- `WhatsAppAuthenticationError` pour 401/403, non retryable.
- `WhatsAppRateLimitError` pour 429, retryable.
- 5xx et timeouts sont retryable.
- Payload/reponse invalide leve `WhatsAppInvalidPayloadError`.
- Aucune erreur WhatsApp ne modifie le domaine Orders.

### Logging / Observability

- Logs structurés via le logging standard existant:
  - channel;
  - direction;
  - external_message_id;
  - message_type;
  - result.
- Pas de nouveau systeme d'observabilite.
- Pas de logs de token, app secret ou verify token.

### Idempotence

- L'idempotence repose sur `MessageEvent(channel, external_message_id)`.
- Un duplicate webhook `wamid.X` ne cree ni deux `MessageEvent`, ni deux
  `ConversationMessage`, ni deux `Conversation`.
- Redis n'est pas source de verite.

### Migrations

- Aucune migration specifique WhatsApp.
- Les modeles persistants utilises restent `MessageEvent`, `ChannelIdentity`,
  `Conversation`, `ConversationMessage`.

### Tests ajoutes

- `apps/whatsapp/tests/test_whatsapp.py`.
- Couverture:
  - configuration active/desactivee;
  - webhook GET succes, mauvais token, params manquants;
  - signature valide/invalide;
  - payload invalide;
  - parsing text/location/unsupported/multiple events;
  - status updates sans ConversationMessage;
  - integration webhook -> MessageEvent -> ChannelIdentity -> Conversation;
  - duplicate webhook;
  - send_text;
  - send_location;
  - erreurs 401/403/429/5xx/400;
  - timeout retryable.

### Validation executee

- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning: connexion DB indisponible pendant la verification d'historique.
- `.venv/bin/ruff check apps/whatsapp config/settings/base.py config/urls.py`
  - resultat: passe.
- Tests WhatsApp sans DB:
  - 15 passent.
- `.venv/bin/pytest apps/whatsapp/tests -q`
  - resultat partiel: 15 passent;
  - 5 tests DB bloques au setup;
  - cause observee: PostgreSQL ne repond pas sur `localhost:5432`.
- `.venv/bin/pytest apps/messaging/tests apps/conversations/tests apps/whatsapp/tests -q`
  - resultat partiel: 20 passent;
  - 25 tests DB bloques au setup;
  - meme cause PostgreSQL.
- Suites Phase 2/3 pertinentes:
  - `apps/stores/tests apps/drivers/tests apps/catalog/tests apps/orders/tests apps/audit/tests tests/integration`;
  - resultat: 1 test sans DB passe, 138 tests DB bloques au setup;
  - meme cause PostgreSQL.
- `pg_isready -h localhost -p 5432`
  - resultat: `no response`.

### Limites volontaires Phase 4.3

Non inclus:

- Telegram adapter;
- Telegram webhook;
- OpenAI/LLM;
- orchestration conversationnelle;
- parsing d'intention;
- creation automatique Order;
- matching Stores/Drivers;
- Delivery/live tracking;
- paiement;
- stock;
- moteur complet de templates WhatsApp;
- telechargement media complet;
- endpoints publics d'envoi manuel.

## Phase 4 - Lot 4.4 Telegram Adapter

### Documentation Telegram verifiee

- Documentation officielle Bot API: `https://core.telegram.org/bots/api`.
- Version courante observee: Bot API 10.3, annoncee le 24 aout 2026.
- Objets utilises: `Update`, `Message`, `User`, `Chat`, `CallbackQuery`.
- Webhook: endpoint HTTPS recevant les updates, securise avec le header
  `X-Telegram-Bot-Api-Secret-Token`.
- Methodes sortantes: `sendMessage`, `sendLocation`, `setWebhook`,
  `deleteWebhook`, `getWebhookInfo`.

### Configuration

- `TELEGRAM_ENABLED`, defaut `False`.
- `TELEGRAM_BOT_TOKEN`.
- `TELEGRAM_WEBHOOK_SECRET`.
- `TELEGRAM_WEBHOOK_URL`.
- `TELEGRAM_API_BASE_URL`, defaut `https://api.telegram.org`.
- `TELEGRAM_REQUEST_TIMEOUT`, defaut `10.0`.
- `.env.example` contient uniquement des placeholders.
- Si Telegram est desactive, Django demarre sans credentials Telegram.
- Si Telegram est active ou si le provider est instancie, les credentials
  obligatoires manquants declenchent `TelegramConfigurationError`.

### Structure

- Nouveau module `apps.telegram`.
- `apps.telegram` depend de `apps.messaging` et `apps.conversations`.
- `apps.messaging` ne depend pas de Telegram.
- Sous-modules:
  - `adapters/inbound.py`;
  - `adapters/outbound.py`;
  - `api/views.py`;
  - `api/urls.py`;
  - `application/services.py`;
  - `providers/bot_api.py`;
  - `conf.py`;
  - `security.py`;
  - `exceptions.py`;
  - `management/commands`.

### Webhook et securite

- Route ajoutee: `POST /webhooks/telegram/`.
- Pas de GET verification Meta artificielle.
- Le secret est valide avant parsing applicatif avec comparaison constante.
- Les throttles DRF globaux sont desactives sur la view webhook pour eviter une
  dependance Redis avant validation du secret.
- Le token Bot API et le secret webhook ne sont jamais loggues.

### Strategie d'identifiants

- `external_user_id` = `message.from.id` converti en string.
- `external_chat_id` = `message.chat.id` converti en string.
- `external_message_id` = `telegram:{chat_id}:{message_id}`.
- Cette strategie evite les collisions car `message_id` Telegram est propre au
  chat et non global.
- Pour `callback_query`, l'identifiant est construit avec `update_id` et l'id du
  callback.
- Le username Telegram reste une donnee descriptive, jamais l'identifiant stable.

### Inbound

- `parse_telegram_update(payload)` retourne une liste de
  `NormalizedInboundMessage`.
- Types:
  - `Message.text` -> `MessageType.TEXT`;
  - `Message.location` -> `MessageType.LOCATION` avec `MessageLocation`;
  - `/start` est reconnu comme commande via metadata, mais reste un texte;
  - `photo`, `document`, `audio`, `voice`, `video`, `video_note`, `sticker`
    sont mappes quand possible;
  - autres updates non conversationnels -> ignores sans crash;
  - `callback_query` -> `MessageType.INTERACTIVE` minimal.
- Aucune conversion directe vers Order ou Catalog.

### ChannelIdentity

- Resolution via le mecanisme central `channel + external_user_id`.
- Politique retenue pour Telegram: l'identite doit exister explicitement.
- Aucun User n'est cree silencieusement pour Telegram, car le modele User actuel
  reste centre sur `whatsapp_phone`.
- Aucune fusion automatique WhatsApp/Telegram depuis username ou contact.

### Conversation integration

- `process_telegram_webhook(payload)`:
  - parse l'update;
  - cree ou recupere le `MessageEvent` idempotent;
  - saute les doublons deja lies a un `ConversationMessage`;
  - appelle `ingest_inbound_message`;
  - marque le `MessageEvent` `PROCESSED`.
- La transaction englobe `MessageEvent`, `ConversationMessage` et
  `Conversation.last_activity_at`.

### Outbound

- `TelegramProvider` implemente `MessagingProvider`.
- Methodes:
  - `send(message)`;
  - `send_text(chat_id, text)`;
  - `send_location(chat_id, location)`;
  - `set_webhook()`;
  - `delete_webhook()`;
  - `get_webhook_info()`.
- Telegram envoie vers un chat: l'API expose donc explicitement `chat_id`.
- Apres succes Bot API, un `MessageEvent OUTBOUND` est enregistre avec
  `telegram:{chat_id}:{message_id}` quand la reponse le permet.

### Provider errors et secrets

- `TelegramConfigurationError`.
- `TelegramAPIError`.
- `TelegramAuthenticationError` pour 401/403, non retryable.
- `TelegramRateLimitError` pour 429, retryable, avec `retry_after`.
- 5xx et timeouts sont retryable.
- Les erreurs `ok=false` de Telegram sont categorisees.
- Les exceptions reseau ne chainent pas d'objet contenant l'URL Bot API avec le
  token.
- `sanitize_telegram_url()` masque le token si une URL doit etre manipulee.

### Idempotence

- L'idempotence repose sur `MessageEvent(channel, external_message_id)`.
- Un duplicate webhook Telegram ne cree ni second `MessageEvent`, ni second
  `ConversationMessage`.
- Redis n'est pas source de verite.

### Management commands

- `python manage.py telegram_set_webhook`.
- `python manage.py telegram_delete_webhook`.
- `python manage.py telegram_webhook_info`.
- Ces commandes respectent la configuration active et ne sortent pas le token.

### Migrations

- Aucune migration specifique Telegram.
- Les modeles persistants utilises restent ceux des lots 4.1 et 4.2.

### Tests ajoutes

- `apps/telegram/tests/test_telegram.py`.
- Couverture:
  - configuration active/desactivee;
  - secret webhook valide/invalide;
  - payload invalide;
  - update sans message;
  - texte;
  - localisation;
  - commande `/start`;
  - media non supporte;
  - callback query;
  - strategie anti-collision `telegram:{chat_id}:{message_id}`;
  - idempotence webhook;
  - integration MessageEvent -> ChannelIdentity -> Conversation;
  - provider `send_text` et `send_location`;
  - erreurs timeout, 401/403, 429 avec `retry_after`, 5xx, `ok=false`;
  - sanitization du token.

### Limites volontaires Phase 4.4

Non inclus:

- IA/OpenAI;
- parsing d'intention;
- creation automatique Order;
- matching;
- Delivery/live tracking;
- paiement;
- stock;
- linking automatique WhatsApp/Telegram;
- telechargement complet des medias Telegram;
- polling production;
- framework complet de commandes Telegram;
- synchronisation de conversations entre canaux.

## Phase 4 - Lot 4.5 Channel Orchestration & Configuration

### Architecture ProviderRegistry

- Le point central de resolution est `apps.messaging.application.registry`.
- `get_provider(channel)` retourne une implementation `MessagingProvider`.
- `is_channel_enabled(channel)` et `get_enabled_channels()` centralisent les
  flags d'activation.
- Les providers concrets sont importes paresseusement dans le registry, afin que
  les modules metier ne dependent pas de WhatsApp ou Telegram.
- Aucun singleton mutable n'est utilise.

### Providers disponibles

- `MessagingChannel.WHATSAPP` -> `WhatsAppProvider`.
- `MessagingChannel.TELEGRAM` -> `TelegramProvider`.
- Les deux providers implementent maintenant `send(NormalizedOutboundMessage)`
  et retournent un `ProviderSendResult`.

### Enabled / disabled

- Les quatre configurations sont supportees:
  - WhatsApp ON, Telegram OFF;
  - WhatsApp OFF, Telegram ON;
  - WhatsApp ON, Telegram ON;
  - WhatsApp OFF, Telegram OFF.
- Un canal desactive leve `MessagingChannelDisabled` via `get_provider`.
- Django demarre sans credentials pour les providers desactives.
- Les credentials ne sont valides qu'a l'instanciation/utilisation du provider.

### MessagingService

- Facade commune dans `apps.messaging.application.orchestration`.
- Fonctions:
  - `send(message)`;
  - `send_text(...)`;
  - `send_location(...)`;
  - `send_text_to_identity(...)`;
  - `send_conversation_text(...)`;
  - `send_conversation_location(...)`.
- Les callers metier peuvent envoyer vers une conversation sans connaitre
  WhatsAppProvider ou TelegramProvider.

### Routing outbound

- Pour une conversation, le canal vient exclusivement de `Conversation.channel`.
- Le destinataire sortant est `Conversation.external_chat_id` quand present,
  sinon `Conversation.channel_identity.external_user_id`.
- Cela preserve Telegram, ou `User.id` et `Chat.id` peuvent diverger.
- Aucune diffusion automatique multi-canal n'est faite.

### Capabilities

- `ProviderCapabilities` reste l'abstraction commune legere:
  - `text`;
  - `location`;
  - `interactive`;
  - `media`.
- `supports(channel, capability)` expose le diagnostic centralise.
- Une capacite manquante leve `UnsupportedMessagingCapability`.

### ProviderSendResult

- Nouveau DTO normalise:
  - `channel`;
  - `external_message_id`;
  - `status`;
  - `provider_metadata`;
  - `message_event`.
- Le Conversation Core ne consomme plus directement les JSON Meta/Telegram.

### Strategie MessageEvent / ConversationMessage

- Frontiere retenue:
  - provider = appel reseau fournisseur + creation d'un seul `MessageEvent OUTBOUND`;
  - orchestration = choix du provider + creation d'un seul `ConversationMessage OUTBOUND`.
- En cas de succes `send_conversation_text`, le `ConversationMessage` reference le
  `MessageEvent` retourne par `ProviderSendResult`.
- En cas d'echec provider avant acceptation fournisseur, aucun
  `ConversationMessage` n'est cree.
- Les erreurs de transport restent dans Messaging et ne touchent pas `Order.status`.

### Error normalization

- Taxonomie commune:
  - `MessagingError`;
  - `MessagingConfigurationError`;
  - `MessagingChannelDisabled`;
  - `MessagingAuthenticationError`;
  - `MessagingRateLimitError`;
  - `MessagingTemporaryError`;
  - `MessagingPermanentError`;
  - `UnsupportedMessagingCapability`.
- Les erreurs WhatsApp/Telegram sont converties dans cette taxonomie par
  `normalize_provider_error`.
- `retry_after` Telegram est conserve sur les rate limits normalises.

### Correlation ID

- `send_conversation_text` et `send_conversation_location` acceptent
  `correlation_id`.
- Le `correlation_id` est propage dans `NormalizedOutboundMessage.metadata`, puis
  dans `MessageEvent.metadata` et `ConversationMessage.metadata`.

### Health / readiness

- `/health/` expose maintenant un diagnostic local non secret:
  - `messaging.whatsapp.enabled`;
  - `messaging.whatsapp.configured`;
  - `messaging.telegram.enabled`;
  - `messaging.telegram.configured`.
- Aucun appel externe Meta/Telegram n'est effectue pendant la healthcheck.

### Fichiers ajoutes/modifies

- `apps/messaging/application/registry.py`.
- `apps/messaging/application/orchestration.py`.
- `apps/messaging/domain/exceptions.py`.
- `apps/messaging/domain/dtos.py`.
- `apps/messaging/domain/providers.py`.
- `apps/messaging/tests/test_orchestration.py`.
- `apps/whatsapp/providers/cloud_api.py`.
- `apps/whatsapp/tests/test_whatsapp.py`.
- `apps/telegram/providers/bot_api.py`.
- `apps/telegram/tests/test_telegram.py`.
- `common/health/views.py`.
- `Implementation.md`.

### Migrations

- Aucune migration Lot 4.5.
- Les modeles existants suffisent.

### Validation executee

- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning: connexion DB indisponible pendant la verification d'historique.
- `.venv/bin/ruff check apps/messaging apps/whatsapp apps/telegram common/health/views.py`
  - resultat: passe.
- `.venv/bin/pytest apps/messaging/tests apps/conversations/tests apps/whatsapp/tests apps/telegram/tests -m 'not django_db' -q`
  - resultat: 46 passent, 33 deselectionnes.
- `.venv/bin/pytest apps/messaging/tests apps/conversations/tests apps/whatsapp/tests apps/telegram/tests -q --tb=short --maxfail=1`
  - resultat: bloque au setup DB avant le premier test DB;
  - cause observee: PostgreSQL ne repond pas sur `localhost:5432`.
- `.venv/bin/pytest apps/stores/tests apps/drivers/tests apps/catalog/tests apps/orders/tests apps/audit/tests tests/integration -m 'not django_db' -q`
  - resultat: 1 passe, 138 deselectionnes.
- `.venv/bin/pytest apps/stores/tests apps/drivers/tests apps/catalog/tests apps/orders/tests apps/audit/tests tests/integration -q --tb=short --maxfail=1`
  - resultat: bloque au setup DB;
  - meme cause PostgreSQL.
- `pg_isready -h localhost -p 5432`
  - resultat: `no response`.

### Limites volontaires Phase 4.5

Non inclus:

- IA/OpenAI;
- parsing d'intention;
- creation Order;
- matching;
- Delivery;
- fallback notification multi-canal;
- broadcast;
- preferred channel;
- Celery retry complet;
- nouveaux providers;
- endpoint public d'envoi manuel.

## Phase 4 - Validation finale infrastructure multicanale

### Audit architectural

- `apps.messaging` contient les primitives communes: channels, DTOs,
  `ChannelIdentity`, `MessageEvent`, provider contract, registry et orchestration.
- Les imports WhatsApp/Telegram dans Messaging sont limites au registry central,
  sous forme d'imports paresseux de factories.
- `apps.conversations` ne connait pas `WhatsAppProvider` ou `TelegramProvider`.
- Les adapters WhatsApp/Telegram ne contiennent pas de logique Orders, Catalog,
  Matching, Delivery ou IA.
- `apps.orders` n'a pas ete modifie et ne depend pas des providers.

### Corrections apportees

- Les webhooks WhatsApp/Telegram retourent maintenant `503` si le canal est
  desactive ou mal configure, au lieu de laisser fuiter une exception config.
- Les parsers WhatsApp/Telegram convertissent les locations/timestamps/IDs
  invalides en erreurs provider structurees.
- Les callbacks Telegram incomplets sont refuses proprement.
- L'admin rend les IDs externes et evenements historiques readonly apres creation.

### Tests E2E ajoutes

- Nouveau fichier `tests/integration/test_phase4_multichannel.py`.
- Couverture:
  - webhook WhatsApp signe -> `MessageEvent` -> `ChannelIdentity` -> `Conversation`
    -> `ConversationMessage`;
  - webhook Telegram avec secret -> meme abstraction interne;
  - meme `User` avec WhatsApp + Telegram -> deux conversations independantes;
  - pas de contamination de `context`;
  - pas de fusion automatique avec username similaire;
  - duplicate webhook -> une seule trace conversationnelle;
  - meme `external_message_id` autorise sur deux canaux differents;
  - conversation fermee -> nouveau message cree une nouvelle active;
  - location entrante -> `ConversationMessage.location` sans creation Order;
  - outbound conversationnel -> bon provider mocke et une seule ecriture.

### Securite et secrets

- Webhook WhatsApp: HMAC `X-Hub-Signature-256` sur raw body.
- Webhook Telegram: `X-Telegram-Bot-Api-Secret-Token`.
- Aucun token/access secret n'est loggue par les services.
- Les URL Telegram contenant le bot token sont sanitizables et les exceptions
  reseau ne chainent pas d'objet contenant l'URL sensible.
- `raw_payload` conserve les messages provider, pas les credentials.

### Idempotence et concurrence

- `ChannelIdentity`: unicite DB `(channel, external_user_id)`.
- `MessageEvent`: unicite DB conditionnelle `(channel, external_message_id)`.
- `ConversationMessage.message_event`: `OneToOneField`.
- `Conversation`: unicite DB d'une conversation `ACTIVE` par
  `channel_identity + external_chat_id`, avec contrainte dediee pour `NULL`.
- Les services utilisent `transaction.atomic()`, contraintes DB et rattrapage
  d'`IntegrityError`; la DB reste la protection finale en concurrence.

### Performance et indexes

- Indexes confirmes:
  - `ChannelIdentity(user, channel)`;
  - `ChannelIdentity(channel, external_user_id)`;
  - `MessageEvent(channel, external_message_id)`;
  - `MessageEvent(external_user_id)`;
  - `MessageEvent(external_chat_id)`;
  - `MessageEvent(status)`;
  - `MessageEvent(created_at)`;
  - `Conversation(channel_identity, external_chat_id, status)`;
  - `Conversation(user, status)`;
  - `Conversation(channel, status)`;
  - `Conversation(last_activity_at)`;
  - `ConversationMessage(conversation, occurred_at)`.
- Les selectors utilisent `select_related`/`prefetch_related` quand pertinent.

### Documentation

- `README.md` documente maintenant:
  - configuration Telegram Bot API;
  - BotFather;
  - `setWebhook`;
  - modes WhatsApp/Telegram ON/OFF;
  - facade `send_conversation_text`.
- `.env.example` contient les placeholders WhatsApp et Telegram.
- `Implementation.md` contient les rapports 4.1 a 4.5 et cette validation finale.

### Validation executee

- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning: connexion DB indisponible pendant la verification d'historique.
- `.venv/bin/ruff check apps/messaging apps/conversations apps/whatsapp apps/telegram tests/integration common/health/views.py`
  - resultat: passe.
- `.venv/bin/pytest apps/messaging/tests apps/conversations/tests apps/whatsapp/tests apps/telegram/tests tests/integration/test_phase4_multichannel.py -m 'not django_db' -q`
  - resultat: 55 passent, 43 deselectionnes.
- Suites DB Phase 4, Phase 3 et Phase 2 tentees avec `--maxfail=1`.
  - resultat: bloquees au setup Django avant test applicatif;
  - cause observee: PostgreSQL ne repond pas sur `localhost:5432`.
- `pg_isready -h localhost -p 5432`
  - resultat: `no response`.

### Primitives disponibles pour la future IA

- La future couche IA pourra recevoir:
  - `Conversation`;
  - `NormalizedInboundMessage`;
  - `ConversationMessage`;
  - `Conversation.context`.
- Elle pourra repondre via:
  - `send_conversation_text(conversation=..., text=...)`;
  - `send_conversation_location(conversation=..., location=...)`.
- Elle n'aura pas besoin de connaitre WhatsApp, Telegram, Meta payloads,
  Telegram updates, tokens ou endpoints providers.

### Limites conservees

Non inclus:

- OpenAI/LLM;
- extraction d'intention;
- Order parser;
- creation automatique Order depuis message;
- matching;
- Delivery/live tracking;
- paiement;
- stock;
- fallback notification multi-canal avance;
- broadcast;
- preferred channel;
- Celery retry complet.

## Phase 4 - Lot 4.7 WhatsApp Interactive Messaging, Templates & 24h Service Window

### Regles WhatsApp verifiees

- Documentation/reference utilisee:
  - WhatsApp Business Policy: fenetre client 24h et templates approuves hors
    fenetre;
  - Meta Postman WhatsApp Cloud API collection: `/messages`, `type=template`,
    `type=interactive`;
  - WhatsApp Business Platform features: templates, categories et messages
    interactifs.
- Regles retenues:
  - free-form autorise dans les 24h suivant le dernier message utilisateur;
  - hors fenetre, un message proactif doit utiliser un template approuve;
  - les reply buttons dynamiques sont des messages interactifs;
  - les callbacks bouton exposent un ID machine distinct du label;
  - l'interaction utilisateur reouvre/recalcule la fenetre via un nouvel inbound;
  - aucun LLM ne decide de la fenetre, du template ou du callback metier.

### Service Window Policy

- `WhatsAppServiceWindowPolicy` vit dans
  `apps.whatsapp.domain.service_window`.
- Source du timestamp:
  - dernier `ConversationMessage` inbound de la conversation WhatsApp;
  - jamais le dernier outbound;
  - jamais `Conversation.last_activity_at`, car il inclut l'outbound.
- Boundary:
  - `now < last_inbound_at + 24h` -> open;
  - a expiration exacte -> closed.
- La source de verite reste PostgreSQL; pas de cache Redis requis.

### Outbound Policy

- `WhatsAppOutboundPolicy.decide(conversation)` retourne:
  - `FREE_FORM_ALLOWED`;
  - `TEMPLATE_REQUIRED`.
- `send_conversation_text()` bloque maintenant les conversations WhatsApp hors
  fenetre avec `WhatsAppTemplateRequired`.
- `send_whatsapp_text_with_template_fallback()` choisit deterministiquement:
  - fenetre ouverte -> free-form;
  - fenetre fermee -> template explicite fourni par le caller.
- Aucun texte libre n'est automatiquement injecte dans un template generique.

### Templates MVP

- `WhatsAppTemplateKey`:
  - `ORDER_UPDATE`;
  - `ORDER_ACTION_REQUIRED`;
  - `OPERATIONAL_ACTION_REQUIRED`.
- Configuration:
  - `WHATSAPP_TEMPLATE_LANGUAGE_CODE`;
  - `WHATSAPP_TEMPLATE_ORDER_UPDATE`;
  - `WHATSAPP_TEMPLATE_ORDER_ACTION_REQUIRED`;
  - `WHATSAPP_TEMPLATE_OPERATIONAL_ACTION_REQUIRED`.
- Langue par defaut MVP: `fr`.
- Les templates doivent etre crees/approuves dans Meta WhatsApp Manager.
- Le code reference uniquement les keys internes.

### Template Registry

- `TemplateDefinition` decrit:
  - key;
  - nom Meta;
  - language;
  - category/use case;
  - parametres attendus.
- `get_template_definition()` lit la configuration courante.
- `validate_template_parameters()` detecte avant HTTP:
  - parametres manquants;
  - parametres en trop;
  - types evidemment invalides;
  - langue non configuree;
  - template non configure.

### send_template

- `WhatsAppProvider.send_template(WhatsAppTemplateMessage)`:
  - valide le template;
  - construit le payload Meta `type=template`;
  - reutilise `_post_message`, auth, timeout et erreurs existants;
  - enregistre un `MessageEvent OUTBOUND` avec `MessageType.TEMPLATE`;
  - retourne `ProviderSendResult`.

### Interactive Messages

- `WhatsAppInteractiveMessage` et `WhatsAppReplyButton` restent specifiques au
  module WhatsApp.
- Support MVP:
  - reply buttons;
  - 1 a 3 boutons;
  - `action_id` stable;
  - label affiche separe.
- Parser inbound:
  - `button_reply` -> `MessageType.INTERACTIVE`;
  - `metadata.whatsapp.action_id`;
  - aucun appel Orders;
  - aucun LLM necessaire pour comprendre l'action structuree.

### Reengagement

- Hors fenetre, utiliser explicitement un template comme
  `ORDER_ACTION_REQUIRED`.
- L'envoi du template ne simule pas une fenetre ouverte.
- Seul un nouvel inbound utilisateur rouvre/recalcule la fenetre.

### MessageEvent / ConversationMessage

- Template outbound:
  - `MessageEvent.message_type=TEMPLATE`;
  - metadata contient key, nom Meta, langue;
  - `ConversationMessage.message_type=TEMPLATE`;
  - texte conversationnel controle: representation `[template:<key>]`.
- Interactif outbound:
  - `MessageEvent.message_type=INTERACTIVE`;
  - `ConversationMessage.message_type=INTERACTIVE`;
  - metadata liste les actions.
- Pas de duplication d'ecriture: provider cree l'event, service applicatif cree
  le message conversationnel.

### Error handling

- Erreurs ajoutees:
  - `WhatsAppTemplateRequired`;
  - `WhatsAppTemplateConfigurationError`;
  - `WhatsAppTemplateValidationError`;
  - `WhatsAppInteractiveValidationError`.
- Les erreurs HTTP Meta continuent d'utiliser:
  - `WhatsAppAPIError`;
  - `WhatsAppAuthenticationError`;
  - `WhatsAppRateLimitError`.
- Pas de fallback free-form si Meta refuse un template.

### Security

- Pas de token dans les logs.
- Les parametres template sont valides et minimises.
- `action_id` n'est pas une autorisation metier.
- Le futur orchestrateur devra verifier user/order/permissions/status avant toute
  action.

### Configuration Meta necessaire

- Creer/approuver les templates dans Meta WhatsApp Manager.
- Configurer les noms approuves dans `.env`.
- Le backend ne cree pas, ne soumet pas et n'approuve pas les templates.

### Fichiers crees/modifies

- `apps/whatsapp/domain/dtos.py`.
- `apps/whatsapp/domain/enums.py`.
- `apps/whatsapp/domain/exceptions.py`.
- `apps/whatsapp/domain/templates.py`.
- `apps/whatsapp/domain/service_window.py`.
- `apps/whatsapp/domain/outbound_policy.py`.
- `apps/whatsapp/application/outbound.py`.
- `apps/whatsapp/adapters/outbound.py`.
- `apps/whatsapp/adapters/inbound.py`.
- `apps/whatsapp/providers/cloud_api.py`.
- `apps/whatsapp/tests/test_whatsapp_interactive_templates.py`.
- `apps/messaging/domain/enums.py`.
- `apps/messaging/application/orchestration.py`.
- `config/settings/base.py`.
- `.env.example`.
- `README.md`.

### Migrations

- `apps/messaging/migrations/0003_add_template_message_type.py`.
- `apps/conversations/migrations/0002_add_template_message_type.py`.
- Elles ajoutent `MessageType.TEMPLATE` aux choices/contraintes.

### Validation executee

- `.venv/bin/python manage.py check`
  - resultat: passe.
- `.venv/bin/python manage.py makemigrations --check --dry-run`
  - resultat: aucune migration manquante;
  - warning: connexion DB indisponible pendant la verification d'historique.
- `.venv/bin/ruff check apps/whatsapp apps/messaging config/settings/base.py`
  - resultat: passe.
- `.venv/bin/pytest apps/whatsapp/tests/test_whatsapp_interactive_templates.py -m 'not django_db' -q`
  - resultat: 10 passent, 8 deselectionnes.
- `.venv/bin/pytest apps/messaging/tests apps/conversations/tests apps/whatsapp/tests apps/telegram/tests tests/integration/test_phase4_multichannel.py -m 'not django_db' -q`
  - resultat: 65 passent, 51 deselectionnes.

### Non-regressions

- Telegram n'a pas recu de templates, service window ou logique Meta.
- Messaging expose seulement `MessageType.TEMPLATE` pour la tracabilite.
- Orders reste independant de WhatsApp.

### Limites volontaires

Non inclus:

- IA/OpenAI;
- confirmation Order depuis bouton;
- mapping `order.confirm -> confirm_order`;
- moteur complet de campagnes;
- synchronisation/approval automatique de templates Meta;
- clone WhatsApp Manager;
- templates Telegram;
- template par `OrderStatus`.

### Readiness Phase 5

- La future couche conversationnelle peut utiliser:
  - `send_conversation_text(...)`;
  - `send_whatsapp_interactive_reply_buttons(...)`;
  - `send_whatsapp_template_to_conversation(...)`;
  - `send_whatsapp_text_with_template_fallback(...)`.
- Les boutons structures peuvent etre traites sans LLM via `action_id`.
- La politique 24h/template est deterministe.
- Telegram reste independant.
- Orders reste independant de WhatsApp.

---

## Phase 5 - Couche conversationnelle assistee

### Objectif et principes

Cette phase ajoute des capacites d'interpretation, de recherche et de guidage
conversationnel autour du coeur existant. L'IA ne recoit ni modele Django ni
autorisation metier, et ne peut pas modifier directement les commandes, le
catalogue ou les conversations. Toute ecriture reste deleguee aux services
applicatifs existants et aux regles de domaine.

L'IA est desactivee par defaut (`AI_ENABLED=False`). Les donnees structurees
recues des canaux (position et actions interactives) restent deterministes et
ne sont pas reconstruites par un modele.

### Lot 5.1 - AI Core

- Nouveau module `apps.ai`, independant des modeles metier.
- Contrats `AIStructuredRequest`, `AIStructuredResult` et `AIProvider` pour les
  appels structures, avec schema JSON explicite.
- `AIService` valide le schema de sortie avant de retourner le resultat a
  l'application.
- Registre de providers avec `MockAIProvider` deterministe pour les tests et
  `OpenAIProvider` base sur l'API Responses et les Structured Outputs.
- Configuration par `AI_ENABLED`, `AI_PROVIDER`, `AI_MODEL`,
  `AI_TIMEOUT_SECONDS` et `OPENAI_API_KEY`.
- Les appels OpenAI utilisent `store=False`. Les logs couvrent le provider, le
  modele, la version de prompt, la latence et les compteurs de tokens, sans
  journaliser le texte utilisateur, le contexte ou les secrets.

### Lot 5.2 - Comprehension structuree des messages

- `MessageUnderstandingService` transforme un message normalise en
  `UserMessageUnderstanding` type : intention, confiance, articles demandes,
  quantites, unites, ambiguities, langue et informations manquantes.
- Le schema contraint les intentions a `CREATE_OR_UPDATE_ORDER`,
  `PROVIDE_LOCATION`, `CONFIRM`, `MODIFY`, `CANCEL`, `GREETING`, `HELP` ou
  `UNKNOWN`.
- Les sorties sont revalidees en objets de domaine : confiance entre 0 et 1,
  noms non vides et quantites strictement positives.
- Une localisation normalisee est reconnue avec une confiance de 1 sans appel
  IA. Les actions interactives structurees sont laissees a l'orchestrateur.
- Les prompts et leur version sont isoles dans `apps.ai.prompts`.

### Lot 5.3 - Grounding catalogue

- `CatalogGroundingService` ne resout les articles que contre les produits et
  variantes reels du catalogue PostgreSQL.
- La recherche couvre les noms canoniques, alias, noms affiches en magasin et
  variantes ; PostgreSQL ajoute la similarite trigramme pour les fautes de
  frappe.
- Chaque resolution conserve ses candidats et sa preuve de matching, puis
  retourne `MATCHED`, `AMBIGUOUS` ou `UNMATCHED` avec une raison explicite.
- Les seuils, marges de score et le nombre maximal de candidats sont centralises
  dans `CatalogGroundingPolicy`.
- Aucun produit, prix, stock, magasin ou `Order` n'est cree ou modifie par cette
  couche. La migration `apps/catalog/migrations/0004_catalog_trigram.py` ajoute
  le support d'indexation necessaire au fuzzy matching PostgreSQL.

### Lot 5.4 - Base de connaissances et reponses ancrees

- Nouveau module `apps.knowledge` avec sources versionnees, statut actif/inactif
  et chunks deterministes, decoupes par paragraphes.
- `knowledge_ingest` ingere une source et remplace atomiquement ses chunks ; une
  nouvelle version inactive les versions precedentes de la meme cle.
- `PostgresKnowledgeRetriever` utilise la recherche plein texte PostgreSQL en
  francais, avec un fallback explicite hors PostgreSQL.
- `KnowledgeAnswerService` ne fournit une reponse ancree que sur les chunks
  recuperes : les citations retournees par le modele sont verifiees et doivent
  appartenir au resultat de retrieval.
- En l'absence de source pertinente, le systeme retourne explicitement une
  reponse d'information insuffisante. Les embeddings et une base vectorielle ne
  font pas partie de ce lot.

### Lot 5.5 - Contexte et clarifications conversationnelles

- `ConversationContextService` est la frontiere typee de lecture/ecriture du
  JSON `Conversation.context`, avec verrou transactionnel et conservation des
  vingt derniers identifiants de messages traites.
- Le contexte contient le flux conversationnel, la commande active et une
  clarification en attente. Il est efface a la fermeture d'une conversation,
  apres confirmation ou annulation.
- `ClarificationEngine` produit des decisions deterministes : demander quantite,
  unite, choix produit, position ou confirmation.
- Les positions et confirmations interactives sont traitees sans IA. Les
  interruptions par annulation, aide ou nouvelle demande sont explicites.
- Cette couche produit des instructions de reponse ; elle n'envoie aucun message
  et n'ecrit aucune commande elle-meme.

### Lot 5.6 - Orchestration conversationnelle des commandes

- `ConversationalOrchestrator` compose comprehension, grounding catalogue,
  contexte, clarification et, optionnellement, connaissances.
- Il retourne un `ConversationalOutcome` et un `ResponsePlan` canal-neutres,
  comprenant le texte, les actions proposees et une eventuelle demande de
  localisation.
- Les commandes sont creees et modifiees uniquement via les services existants :
  brouillon, ajout d'article, attente de localisation, localisation, confirmation
  et annulation.
- Les actions `order.confirm` et `order.cancel` sont interpretees de facon
  deterministe. Un message deja traite est detecte par son identifiant externe.
- Les erreurs IA sont converties en reponse recuperable ; elles ne sont jamais
  assimilees a un article inconnu et ne declenchent pas d'ecriture partielle.

### Lot 5.7 - Collecte dataset, feedback et evaluation hors ligne

- Nouveau module `apps.evaluation` pour construire un jeu de donnees revu par un
  humain sans conserver les payloads de production bruts.
- `DatasetSanitizer` masque notamment telephones, emails, coordonnees et secrets
  avant toute persistance d'un candidat.
- Les candidats peuvent etre acceptes ou rejetes par un relecteur. Une
  acceptation produit un exemple avec provenance de label et cle de groupe.
- Les snapshots versionnes repartissent deterministiquement les exemples entre
  train, validation et test, par cle de groupe afin d'eviter les fuites entre
  ensembles.
- `EvaluationRunner` enregistre provider, modele, version de prompt, resultats
  par exemple et metriques d'exact-match dans des executions immuables.
- Cette evaluation est hors ligne : elle n'entraine pas automatiquement un
  modele et n'influence pas les decisions de production.

### Lot 5.8 - Integration, resilience, securite et validation E2E

- Audit des boundaries : providers IA sans ORM metier, Orders sans SDK IA,
  orchestrateur sans payload WhatsApp/Telegram et datasets sans mutation metier.
- Les donnees structurees (location, `order.confirm`, `order.cancel`) passent
  avant l'interpretation IA et sont revalidees par l'utilisateur et le statut
  reel de la commande.
- Les erreurs IA sont recuperables, ne marquent jamais une commande `FAILED` et
  ne font progresser l'idempotence de contexte qu'apres une decision complete.
- Le contexte est verrouille lors de ses mises a jour, nettoye a la fermeture
  d'une conversation, a la confirmation et a l'annulation.
- Les controles de sanitization dataset et les tests unitaires ciblent les PII,
  les sorties IA invalides, les injections de faits catalogue et les reponses
  RAG sans evidence.
- La validation E2E complete webhook -> provider -> Order.CONFIRMED reste a
  executer sur PostgreSQL disponible. Dans l'environnement courant, les tests
  dependants de PostgreSQL sont bloques par l'absence de connexion locale.

### Migrations et validation associees

- `apps/catalog/migrations/0004_catalog_trigram.py` pour la recherche catalogue.
- `apps/knowledge/migrations/0001_initial.py` pour les sources et chunks de
  connaissances.
- `apps/evaluation/migrations/0001_initial.py` pour les candidats, jeux de
  donnees, versions, executions et resultats d'evaluation.
- Tests ajoutes dans `apps/ai/tests`, `apps/catalog/tests/test_grounding.py`,
  `apps/knowledge/tests`, `apps/conversations/tests/test_clarification.py`,
  `apps/conversations/tests/test_orchestrator.py` et
  `apps/evaluation/tests/test_sanitizer.py`.

### Limites connues de cloture Phase 5

- `ResponsePlan` prepare le raccordement aux canaux, mais n'est pas encore
  distribue automatiquement par les services de webhook vers Messaging.
- Les E2E WhatsApp/Telegram complets, les tests de concurrence DB et les tests
  de migration depuis une base vide doivent etre executes avec PostgreSQL.
- La publication des Domain Events utilise toujours `transaction.on_commit` ;
  une transactional outbox reste une evolution ulterieure.
- La phase s'arrete a `Order.CONFIRMED` : aucun Store Matching, Driver Matching,
  paiement, stock reel ou livraison n'est implemente.

## Phase 6 — Lot 6.1 : Customer Order Session / Active Draft

### Politique active-order

La session est `Conversation + ConversationContext.active_order_id + Order` ;
aucun modele `CustomerOrderSession` supplementaire n'est necessaire. Le
contexte n'est qu'un pointeur non fiable et n'est jamais utilise comme preuve
d'autorisation. Seuls `DRAFT` et `AWAITING_LOCATION`, issus de
`DRAFT_MUTABLE_STATUSES`, sont reprenables.

`ActiveCustomerOrderService` (`apps/conversations/application/active_orders.py`)
est la frontiere unique pour lire, attacher, creer, reprendre ou abandonner un
draft. Il verrouille la ligne Conversation dans une transaction avant de lire
et modifier le contexte. Cela serialize la creation par conversation ; la
contrainte ne limite volontairement pas les drafts au niveau global User afin
de conserver l'isolation des canaux et les usages futurs.

### Validation, reprise et abandon

Chaque pointeur est parse comme UUID, charge depuis PostgreSQL, puis controle
contre `conversation.user` et les statuts actifs. Un UUID invalide, commande
supprimee, terminale ou d'un autre proprietaire est detache et produit une
decision `STALE_CONTEXT` tracee. Une conversation `CLOSED` produit
`CLOSED_CONVERSATION`, sans mutation Order ni heritance automatique lors de la
creation d'une autre conversation.

`get_or_create_draft` reutilise exclusivement le draft rattache a la
conversation ; il ne recherche pas arbitrairement un ancien draft du User.
`resume_order` est explicite et revalide proprietaire/statut. Les actions
stables sont `order.resume`, `order.start_new` et
`order.abandon_and_start_new`. Demarrer une nouvelle commande avec un draft
demande d'abord un choix ; l'abandon explicite applique `cancel_order` avec
`CUSTOMER_REQUEST`, donc conserve transition, audit et evenement domaine.

Le recapitulatif reste reconstruit depuis `Order`/`OrderItem` en base. Les
futures statistiques et recommandations doivent partir des commandes dans des
etats metier aboutis, et exclure brouillons, items retires et annulations.

### Observabilite et validation

Les evenements de log sans PII sont : `draft_created`, `draft_resumed`,
`draft_abandoned`, `stale_active_order` et `active_order_conflict`; ils portent
`conversation_id`, `order_id` et `correlation_id`. Les tests
`apps/conversations/tests/test_active_orders.py` couvrent creation/reutilisation,
contextes stale ou corrompus, ownership, conversation fermee, isolation
multi-canal, reprise explicite et abandon. Aucune migration n'est requise : le
champ JSON de contexte et les index Conversation existants suffisent.

## Phase 6 — Lot 6.2 : Order Building & Modification Experience

### Item targeting et mutations

`apps/conversations/application/order_building.py` introduit
`ItemReference` (identifiant de ligne backend, nom demandé ou position affichée)
et `OrderBuildingService`. Une cible est acceptée seulement si elle est unique ;
un nom correspondant à plusieurs lignes retourne `AMBIGUOUS`. Un identifiant
provenant du modèle de langage n'est jamais utilisé comme autorité.

La politique de doublon est « une demande, une ligne » : ajouter à nouveau
Mayor ne fusionne pas silencieusement la quantité. `add`, `update`, `remove` et
`replace` délèguent aux services applicatifs Orders. `replace_order_item` met à
jour une ligne sous transaction, après résolution/revalidation du nouveau
catalogue, au lieu de supprimer l'ancien item en avance.

### Grounding, clarifications et résumé

Les articles `MATCHED` conservent `requested_name` et sont revalidés contre le
Product/Variant actif avant écriture. `UNMATCHED` produit une ligne avec
`product=NULL`; aucun Product ni alias n'est créé. Une requête multi-item écrit
les items sûrs et ouvre une clarification pour le premier item non résolu.

L'action stable `order.modify` ouvre le mode de modification. Les suppressions,
mises à jour d'une cible unique et remplacements « remplace X par Y » passent
par le service; le résumé est reconstruit à partir de `OrderItem`. Les prix et
le stock ne sont pas inférés : StoreProduct ne fournit pas encore une promesse
de stock ou un prix final client.

### Données futures et tests

Les audits `ORDER_ITEM_ADDED`, `ORDER_ITEM_UPDATED` et `ORDER_ITEM_REMOVED`
restent des signaux de cycle de vie de draft, pas des achats. Les futures
recommandations doivent utiliser les commandes confirmées puis les états de
livraison. Le test `apps/conversations/tests/test_order_building.py` couvre
doublons, ciblage ambigu/position, quantité/unité, remplacement et suppression.
Aucune migration n'est nécessaire.

## Phase 7 — Lot 7.5 : Store Conversation & Offer Actions

`StoreRecipientResolver` sélectionne les StoreMembership actifs ayant un rôle
opérationnel réel (OWNER, MANAGER, AGENT), puis leurs ChannelIdentity actives,
dédupliquées par user/canal. En l'absence de primary contact dans le modèle, la
policy V1 ne choisit pas arbitrairement un unique destinataire.

`StoreOfferAuthorizationPolicy` revalide Store, user, rôle et statut de
membership à chaque action. `StoreOfferActionService` traite seulement les IDs
stables `store_offer.accept`/`store_offer.reject`, recharge l'offre et retourne
un résultat contrôlé pour UUID forgé, membership retiré, offre expirée ou déjà
résolue. La présentation est reconstruite depuis Order/OrderItem et expose
référence, lignes, expiration et distance snapshot éventuelle, sans téléphone,
adresse ou coordonnées client.

Le renderer devra transmettre `offer_id` comme contexte opaque et utiliser la
policy WhatsApp/template ou Telegram existante. La livraison de notification est
distincte du statut StoreOffer; aucun provider, texte libre d'acceptation,
fallback matching ou notification tracking séparée n'est ajouté dans ce lot.

## Phase 7 — Lot 7.3 : Store Ranking & Candidate Selection

`StoreRankingService` consomme exclusivement les DTOs issus de 7.1 et 7.2 :
il ne recharge ni catalogue ni IA. La stratégie versionnée `deterministic_v1`
utilise un ordre lexicographique explicable : couverture complète, ratio de
couverture décroissant, distance croissante en mètres, UUID croissant. Ce choix
fonctionne également au cold start et évite des poids inventés.

Les boutiques partielles sont conservées dans `diagnostics` pour support et
analytics, mais exclues de `ranked_candidates` : le MVP n'implémente ni split
order ni offer partielle. Les prix sont volontairement ignorés car ils peuvent
être absents et aucune politique de panier comparable n'existe. Chaque candidat
produit des composants, raisons et un snapshot sérialisable qui pourront être
persistés lors des futures StoreOffers. L'API accepte un `max_candidates`
explicite, sans valeur business cachée.

Les futurs modèles ML pourront remplacer la stratégie derrière la même
interface, avec rang, version et snapshot pour mesurer le feedback. Le risque
futur de concentration/fairness est donc visible et l'ordre reste modifiable.
`apps/stores/tests/test_ranking.py` couvre priorité full/partial, tie-break,
limite, version et snapshot. Aucune migration n'est nécessaire.

## Phase 7 — Lot 7.4 : Store Offer Lifecycle

Le modèle `StoreOffer` est ajouté dans Stores avec états contrôlés `PENDING`,
`ACCEPTED`, `REJECTED`, `EXPIRED`, `CANCELLED`, expiration, timestamps et
snapshot JSON minimal du ranking. Les contraintes garantissent une offre par
couple Order/Store et au plus une offre acceptée par Order; les index couvrent
les consultations par Order/statut et les sweeps d'expiration.

`StoreOfferService` crée des offres seulement pour Order en matching/offered et
boutique active/ouverte. La première offre déclenche la transition
`MATCHING_STORES → STORE_OFFERED`. L'acceptation verrouille Order et offres,
vérifie l'expiration et le gagnant existant, accepte une seule offre, passe
l'Order à `STORE_ACCEPTED` et annule les autres pending. Refus, expiration et
annulation sont idempotents pour leur propre état. `expires_at` est la vérité;
le worker/sweep n'est pas encore ajouté.

Aucun provider messaging, offre Store UX, fallback, pricing, stock, Driver
Matching ou ML n'est introduit. Les événements/audits StoreOffer dédiés restent
à brancher dans le lot d'orchestration suivant. La migration
`stores/0003_storeoffer.py` doit être appliquée avec PostgreSQL pour valider la
contrainte conditionnelle.

## Phase 6 — Lot 6.3 : Delivery Location & Delivery Details

### Modèle, validation et canaux

Le modèle existant est conservé : `Order.delivery_location` est un PointField
géographique SRID 4326, `delivery_address_label` est le texte humain et
`delivery_instructions` la consigne opérationnelle. `MessageLocation` est le
contrat normalisé reçu autant de WhatsApp que de Telegram. Il valide les bornes
WGS84 et, désormais, les valeurs non finies. L'orchestrateur utilise
`Point(longitude, latitude, srid=4326)` et appelle exclusivement
`set_delivery_location`.

Le service Orders vérifie à son tour SRID, bornes, finitude et statut mutable,
ce qui protège également les appels API. `set_delivery_address_label` persiste
une adresse texte sans la géocoder ni inventer de GPS. Les instructions sont
normalisées, optionnelles et limitées à 1000 caractères.

### Flow, confidentialité et préparation

Une position définie ou remplacée invalide le parcours de confirmation courant,
puis propose une indication optionnelle. `order.change_location` ramène le
contexte à `AWAITING_LOCATION`; après consigne ou refus, un nouveau récapitulatif
est produit. La state machine Orders bloque déjà les états terminaux via les
services draft mutables; l'active order revalide également ownership.

Les audits de localisation ne contiennent plus latitude/longitude : seulement
un signal de position, SRID et présence de libellé. Les coordonnées restent dans
le PointField, prêt pour les futures requêtes PostGIS Store/Driver Matching,
mais ne constituent ni donnée dataset ni feature de recommandation. Aucun
geocoding, zone métier, stock ou matching n'est ajouté.

Le test Messaging couvre maintenant NaN et infini. Aucune migration n'est
requise : le champ spatial existant est déjà indexable et compatible PostGIS.

## Phase 6 — Lot 6.4 : Order Review, Confirmation & Cancellation UX

### Review et readiness

`apps/orders/application/review.py` fournit `OrderReviewService`,
`OrderReviewItem` et `OrderConfirmationReadiness`. Le review relit Order et
OrderItem depuis PostgreSQL et expose référence, lignes, destination, consignes,
readiness, blockers, warnings, actions et fingerprint. Les blockers sont :
statut non mutable, commande vide, position absente, clarification en attente
et item ambigu. Les items `UNMATCHED` suivent la policy Phase 3 : ils restent
autorisés avec un avertissement, pas une résolution inventée.

Le fingerprint SHA-256 porte les données visibles pertinentes. Un callback
`order.confirm` qui le fournit est refusé s'il ne correspond plus à l'état
persisté ; la readiness est toujours réévaluée juste avant `confirm_order`.
La confirmation textuelle est explicite et converge vers le même service.
`confirm_order` fournit déjà transition, verrouillage, audit et publication
unique de `OrderConfirmed`; `cancel_order` fournit les garanties équivalentes
pour l'annulation.

### Présentation et limites

`ResponsePlan` reste neutre vis-à-vis de WhatsApp/Telegram et porte le
fingerprint dans ses métadonnées pour les renderers. Les actions restent stables
(`order.confirm`, `order.modify`, `order.cancel`). Les renderers de canaux et
la politique de fenêtre WhatsApp demeurent à la frontière Messaging; aucune
logique Meta n'est introduite dans le review.

Le review ne présente ni prix final ni disponibilité garantie. Les audits et
événements confirmation/annulation existants restent les signaux métier;
`CONFIRMED` n'est pas assimilé à une livraison ou à une fréquence d'achat. Le
test `apps/orders/tests/test_review.py` couvre vérité DB, fingerprint, readiness
et avertissement UNMATCHED. Aucune migration n'est requise.

## Phase 6 — Lot 6.5 : Customer Notifications & Conversation Recovery

### Récupération et réponses client

`ConversationRecoveryService` et `RecoveryDecision` reconstruisent le flow à
partir de PostgreSQL et du contexte typé, sans cache RAM ni appel provider. Les
états couverts sont absence de flow, draft à reprendre, clarification, demande
de position, review prêt, contexte stale récupéré et conversation fermée. Sur
un salut, l'orchestrateur propose la suite adaptée au lieu de créer un nouveau
draft arbitrairement. Ownership et statuts sont revalidés par
`ActiveCustomerOrderService`.

`customer_responses.technical_retry_message` produit des messages contrôlés
pour les erreurs techniques, sans exposer API, secret ou détail transport. Les
erreurs IA restent récupérables et ne modifient pas l'Order.

### Boundary outbound et notifications

La mutation Order est toujours achevée avant tout envoi : l'orchestrateur
retourne un `ResponsePlan`, et Messaging l'envoie ensuite. Une panne outbound
ne rollback donc pas une confirmation déjà commitée; une reprise relit l'état
réel au lieu de rejouer la transition. Les `MessageEvent`, correlation IDs,
classification `MessagingTemporaryError`/rate-limit/permanent et tests provider
existants portent les garanties réelles : mutation métier idempotente et réseau
au mieux at-least-once, pas exactly-once.

WhatsApp continue d'utiliser `WhatsAppOutboundPolicy` et
`send_whatsapp_text_with_template_fallback` avec template explicitement fourni;
un échec template ne devient jamais du free-form hors fenêtre. Telegram reste
sur le provider normal, sans règle Meta. Aucun retry Celery durable, campagne,
notification d'engagement ou moteur anti-spam n'est ajouté : les ResponsePlan
transactionnels laissent cette extension derrière une frontière métier/canal.

`apps/conversations/tests/test_recovery.py` couvre reprise de localisation,
clarification et review persistées. Aucune migration n'est nécessaire.

## Phase 6 — Lot 6.6 : intégration et durcissement final

L'audit a identifié que les webhooks Phase 4 persistaient correctement les
messages mais ne déclenchaient pas encore le flow conversationnel. Le point de
composition `process_inbound_conversation` corrige cette frontière : chaque
webhook WhatsApp/Telegram réalise ingestion et idempotence dans une transaction
courte, puis appelle l'orchestrateur après commit avec le MessageEvent comme
correlation ID. Aucun appel IA ou provider outbound n'est introduit dans la
transaction d'ingestion.

Les deux canaux utilisent ainsi le même métier : adapters → Messaging →
Conversation → Orchestrator → Active Order → services Orders → review →
confirmation/annulation. Les actions structurées évitent l'IA. Lorsque l'IA est
désactivée, l'ingestion reste disponible et les messages structurés peuvent
toujours être traités; les messages texte nécessitant compréhension retournent
une décision contrôlée, sans mutation arbitraire.

La Phase 6 s'arrête strictement à `Order.CONFIRMED`. Les données d'audit et les
événements existants distinguent création/modification/retrait d'item,
confirmation et annulation, sans les convertir en recommandations. Aucun Store
Matching, Driver Matching, prix final, paiement, campagne ou Recommendation
Engine n'est implémenté. Les E2E fournisseurs/PostGIS complets doivent être
exécutés dans l'environnement Django/PostgreSQL avec AI mock configuré; ils ne
sont pas substituables par les vérifications syntaxiques de cette image.

## Phase 7 — Lot 7.1 : Store Matching Foundation & Eligibility

### Architecture inspectée et règles réelles

Avant ce lot, `Store` possédait déjà un PointField geography SRID 4326, un
statut (`ACTIVE`, `INACTIVE`, `SUSPENDED`), `is_open` et un
`service_radius_meters` validé, de 5000 m par défaut. `Order` possède la même
représentation géographique et `start_store_matching()` est déjà la transition
explicite `CONFIRMED → MATCHING_STORES`. Aucun service d'éligibilité ou contrat
de candidats n'existait.

`StoreEligibilityService` (`apps/stores/application/eligibility.py`) est une
query pure : il exige `Order.CONFIRMED` et une destination, sans modifier
l'Order. Il utilise `Distance` PostGIS sur les Geography PointFields, en mètres,
filtre activité et ouverture puis compare la distance au rayon déclaré par
boutique. Les résultats sont ordonnés par distance puis UUID et décrivent les
exclusions `STORE_INACTIVE`, `STORE_NOT_OPERATIONAL` et `TOO_FAR`.

### Limites et suite

Le résultat DTO ne transporte que IDs, distance et raisons, sans coordonnées
client précises ni modèle Store complet. Il n'appelle pas l'IA, n'inspecte pas
les membres humains et ne réalise aucune couverture OrderItem, ranking ou offre.
La validité reste instantanée : les Lots 7.2+ devront revalider statut et rayon
au moment de la couverture/offre. Le test `apps/stores/tests/test_eligibility.py`
couvre précondition, exclusions et distance; il doit être exécuté sur
PostgreSQL/PostGIS, pas présenté comme une preuve SQLite. Aucune migration n'est
requise.

## Phase 7 — Lot 7.2 : Product Fulfillment & Store Coverage

`StoreProduct` porte un Product, éventuellement une ProductVariant, un prix
nullable et une disponibilité déclarée (`AVAILABLE`, `UNAVAILABLE`, `UNKNOWN`),
mais aucun stock quantifié. `StoreFulfillmentService`
(`apps/stores/application/fulfillment.py`) en déduit une couverture de catalogue,
jamais une réservation ou une garantie de quantité.

Les StoreProducts sont préchargés en une requête groupée pour toutes les
boutiques/items à évaluer. Une variante demandée exige la même variante; un
produit global ne suffit pas. `UNMATCHED` et `AMBIGUOUS` restent non couverts,
sans inférence. Les résultats exposent raisons contrôlées, ratio par OrderItem,
couverture totale et prix réel nullable. La policy est diagnostique : les
boutiques partielles sont retournées mais ne deviennent pas offerables dans ce
lot; aucun split-order, conversion d'unités, solver de packages ou ranking.

Les tests `apps/stores/tests/test_fulfillment.py` couvrent disponibilité
déclarée, absence de fausse garantie de stock/prix, UNMATCHED et indisponibilité.
Aucune migration n'est nécessaire.

## Phase 7 — Lots 7.6C.1/7.6C.2 : frontière de livraison StoreOffer

`MessageEvent` représente l'envoi provider effectif mais ne pouvait pas porter
l'intention métier destinataire/offre, son idempotence logique et ses retries.
Les modèles dédiés `StoreOfferNotification` et
`StoreOfferNotificationAttempt` complètent donc Messaging sans remplacer ses
providers. La première est unique par `offer + ChannelIdentity`, référence la
Conversation multicanal existante et conserve un snapshot sans GPS, adresse,
téléphone, conversation brute ni secret. La seconde est numérotée et mémorise
le provider, le canal, le résultat, MessageEvent éventuel, corrélation et une
catégorie d'erreur contrôlée.

`StoreConversationResolver` réutilise `Conversation` avec l'identité du membre
Store ; aucune table parallèle ni conversation par offre n'est créée. Le
renderer est sans HTTP : WhatsApp utilise soit boutons stables
`store_offer.accept/reject`, soit le template
`OPERATIONAL_ACTION_REQUIRED` hors fenêtre ; Telegram garde le même contrat
métier sans dépendance Meta.

La tâche Celery réclame l'intention dans une transaction courte (`SENDING` et
attempt créé), envoie hors verrou, puis persiste `SENT`,
`FAILED_RETRYABLE` ou `FAILED_PERMANENT`. Backoff et plafond d'essais sont
configurables. Un timeout/rate-limit ne modifie jamais StoreOffer ni Order. Une
offre résolue annule les notifications non envoyées. Le cas crash après appel
provider reste explicitement ambigu et peut produire une livraison at-least-once
lors d'une future réconciliation ; il n'est pas présenté comme exactly-once.

## Phase 7 — Lot 7.6D : recovery et concurrence

Les invariants contrôlés sont : au plus une offre acceptée par Order (contrainte
conditionnelle PostgreSQL), `STORE_ACCEPTED` avec exactement un gagnant, une
seule offre `PENDING` actionable pour la stratégie séquentielle, et absence
d'offre actionable après annulation. L'ordre de verrouillage appliqué par les
services est Order, puis attempt, puis offers ; aucun appel provider ne se fait
sous verrou de transaction.

`StoreMatchingReconciliationService` lit exclusivement l'état persistant et
utilise `StoreOfferService` pour expirer/annuler, et
`StoreMatchingOrchestrator.advance_matching()` pour progresser. Une expiration
oubliée converge donc vers le candidat suivant; une notification `PENDING` ou
retryable est ré-enqueue depuis la base. Les outcomes comportant un `offer_id`
sont refusés s'ils ne correspondent plus au rang courant : un callback tardif
ou un sweep dupliqué ne peut pas consommer deux candidats.

Les incohérences non déterminables (gagnants multiples, succès sans gagnant,
Order acceptée sans offre) sont loggées `critical` et retournées sous forme
`INVARIANT_VIOLATION`, sans réparation silencieuse. La tâche
`reconcile_store_matching` est bornée par
`STORE_MATCHING_RECONCILIATION_BATCH_SIZE`; son ordonnanceur Beat reste à
configurer par l'environnement, afin de ne pas imposer une cadence produit.

## Phase 7 — Lot 7.6E : corrections finales et limite de validation

Un ranking sans candidat offerable passe maintenant par `fail_order()` avec
`NO_STORE_AVAILABLE`, au lieu de laisser une commande `MATCHING_STORES` sans
issue. `StoreOfferActionService` transmet les actions autorisées au matching :
un refus déclenche le fallback séquentiel et une acceptation clôture l'attempt
lorsque l'Order est devenue `STORE_ACCEPTED`.

Les contrôles disponibles dans cette image sont `compileall` et
`git diff --check`. Django, pytest, PostgreSQL/PostGIS et le broker Celery ne
sont pas installés ici : migrations, E2E provider, contraintes PostgreSQL,
tests transactionnels et régression complète doivent encore être exécutés dans
l'environnement de test du projet avant de déclarer la Phase 7 clôturée.

## Phase 7 — Lot 7.6F : exécution runtime

Le dépôt contenait déjà `.venv` avec Django 6.0.8 et pytest 9.1.1. Compose a
fourni une base PostGIS isolée ; toutes les migrations ont été appliquées avec
succès depuis zéro, jusqu'à `stores.0007`. `manage.py check` et
`makemigrations --check` retournent zéro erreur, et `apps/stores/tests` passe
24/24 sur PostgreSQL/PostGIS. La suite complète atteint 334/338 après la
correction PostgreSQL du verrouillage `OrderItem` (`OF self` sur une jointure
nullable), des méthodes PATCH/DELETE Order et du chemin sans candidat.

Les quatre échecs restants ne sont pas masqués : ils portent sur des assertions
antérieures incompatibles avec la policy WhatsApp fermée, le contexte typed et
la réponse health enrichie. Les E2E matching, tests TransactionTestCase de
race et smoke providers réels restent à ajouter/exécuter; la Phase 7 ne peut
donc pas être marquée CLOSED à ce stade.

## Phase 7 — Lot 7.6G : contrats de régression runtime

Les quatre tests rouges ont été traités au niveau de leur contrat : les tests
Messaging créent un inbound récent avant de vérifier une panne provider, les
tests multicanal vérifient l'isolation métier du contexte typed plutôt que la
forme JSON historique, et le health check vérifie son statut ainsi que son
diagnostic Messaging sans imposer une égalité fragile du payload. PostgreSQL a
également validé le correctif `FOR UPDATE OF self` sur OrderItem nullable.

Résultat réel : `pytest -q` passe **338/338** sur PostgreSQL/PostGIS,
`manage.py check` et `makemigrations --check` passent. Cela ne remplace pas
les E2E Phase 7 dédiés ni les TransactionTestCase de courses concurrentes,
qui restent nécessaires à la fermeture formelle.

## Phase 8 — Lot 8.1 : Role Routing & Conversational Entry

### État initial et composants réutilisés

L'audit a trouvé `User`/`UserRole` et les rôles `CLIENT`, `STORE_OWNER`,
`STORE_MANAGER`, `STORE_AGENT`, `DRIVER`, `ADMIN`; `ChannelIdentity` est la
source de l'utilisateur pour les messages entrants. Les conversations sont
persistées par `Conversation` avec `Conversation.context` JSON et les messages
par `ConversationMessage`/`MessageEvent` (contrainte unique
`channel + external_message_id`). Le flow client déjà en place est
`ConversationalOrchestrator`, composé depuis `process_inbound_conversation`,
avec `MessageUnderstandingService`/`AIService`, `ConversationContextService`,
`ConversationRecoveryService`, `ActiveCustomerOrderService`, les actions
structurées Order et `MessagingService` (`apps.messaging.application.orchestration`).

Le lot réutilise ces composants; il n'ajoute ni modèle parallèle, ni rôle
durable, ni moteur IA séparé. Il complète le contrat `MessageIntent` par
`SELL` et `DELIVER`, tandis que l'achat reste l'intention client existante
`CREATE_OR_UPDATE_ORDER`.

### Architecture et données

`ActiveJourney` (`BUY`, `SELL`, `DELIVER`) est ajouté au `ConversationContext`
typé et sérialisé dans `Conversation.context`; aucune migration n'est requise.
`JourneyRoutingService` est le routeur métier channel-agnostic : il verrouille
et met à jour ce contexte via `ConversationContextService`, puis lit seulement
les `StoreMembership` actives ou le `DriverProfile` pour produire les états
`SELLER_ONBOARDING_REQUIRED` / `SELLER_JOURNEY_READY` et
`DRIVER_ONBOARDING_REQUIRED` / `DRIVER_JOURNEY_READY`. Il ne crée ni Store,
ni profil livreur, ni commande.

Une intention explicite BUY/SELL/DELIVER est routée directement. Une salutation,
une intention inconnue ou une confiance sous le seuil sans parcours actif
retourne un `ResponsePlan` avec les IDs stables
`journey.select.buy`, `journey.select.sell`, `journey.select.deliver` et les
labels français indépendants. Une action structurée est traitée avant l'appel
au LLM. Le changement BUY ⇄ SELL ne modifie ni `UserRole`, ni Order, Store ou
DriverProfile; un ordre actif n'est pas effacé par le changement de contexte.

### Canaux, recovery, idempotence et sécurité

La présentation reste à la frontière : `send_response_plan` utilise les
boutons WhatsApp existants (donc `WhatsAppOutboundPolicy` et fenêtre de service)
ou un clavier inline Telegram. L'adapter Telegram normalise maintenant
`callback_query.data` vers `metadata.action_id`; les deux canaux atteignent le
même routeur. WhatsApp ne contourne pas la règle template lorsque la fenêtre
est fermée.

Le parcours actif et les IDs de messages traités sont persistés dans PostgreSQL,
donc redémarrables sans Redis. La déduplication existante `MessageEvent` et la
liste bornée `processed_message_ids` évitent de retraiter un callback. Le
routeur n'accepte aucune identité, rôle, store ou profil depuis le bouton ou le
texte : il part de `Conversation.channel_identity.user` et interroge les
relations persistées.

### Tests et validation réelle

Ajout de `apps/conversations/tests/test_journey_routing.py`, couvrant la
sélection ambiguë, les routes explicites, BUY sans création d'Order, les trois
actions déterministes sans LLM, absence de création Store/DriverProfile,
multi-rôles, switch BUY/SELL, callback dupliqué et persistance Telegram.
Les tests existants WhatsApp/Telegram et orchestrator ont aussi été ciblés.

Commandes réellement exécutées :

```text
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/pytest -q apps/conversations/tests/test_journey_routing.py apps/conversations/tests/test_orchestrator.py apps/telegram/tests/test_telegram.py apps/whatsapp/tests/test_whatsapp.py
.venv/bin/pytest -q apps/conversations/tests/test_orchestrator.py
.venv/bin/python -m compileall -q apps
git diff --check
```

Résultats réels : `manage.py check`, `compileall` et `git diff --check` passent;
`test_orchestrator.py` passe 2/2. La suite ciblée avec DB ne peut pas être
validée dans cette session : la connexion PostgreSQL/PostGIS
`localhost:5432` échoue avant setup des tests. `makemigrations --check` ne
détecte aucune migration applicative, avec un avertissement de connexion DB.

Limites : l'onboarding vendeur (8.2), onboarding/disponibilité/matching
livreur (Phase 9), ainsi que les E2E providers réels restent hors scope. Le
contrat `SELLER_ONBOARDING_REQUIRED` est prêt pour que le Lot 8.2 branche son
onboarding sans modifier les rôles ni le routeur.

## Phase 8 — Lot 8.2 : Seller Onboarding & Store Creation

### État initial et réutilisation

L'audit a confirmé que `Store` possède un `PointField(geography=True, srid=4326)`
et exige `name`, `location` et `city`; `StoreMembership` a déjà les rôles
`OWNER`, `MANAGER`, `AGENT`, et le service transactionnel
`apps.stores.application.services.create_store()` crée déjà le couple Store +
membership quand `created_by` est fourni. Les permissions réutilisables sont
`can_manage_store` / `is_store_owner`. `StoreMembership` ne limite pas un
utilisateur à une seule boutique : l'onboarding reprend donc le workspace
vendeur si une membership active existe, sans créer automatiquement une autre
boutique.

Les conventions GeoDjango et les messages normalisés sont réutilisés :
`MessageLocation` vérifie latitude/longitude avant que le service métier ne
reçoive les données; la création utilise explicitement
`Point(longitude, latitude, srid=4326)`. Aucun payload Meta/Telegram, adresse
textuelle ou géocodage n'entre dans le service.

### Architecture et draft

`SellerOnboardingService` est un service conversationnel channel-agnostic avec
`start`, transitions nom/position/ville, `review`, `confirm`, `cancel`, reprise
et choix catalogue. Le draft `SellerOnboardingDraft` persiste dans
`Conversation.context` avec les étapes `NAME`, `LOCATION`, `CITY`, `REVIEW`,
`COMPLETED`; il est donc récupérable après restart sans Redis. Une Store n'est
pas créée avant confirmation : c'est l'option A (état temporaire puis création
définitive), cohérente avec l'absence d'état Store DRAFT dans le modèle.

Les données minimales demandées sont nom, position et ville. La ville est une
contrainte technique réelle du modèle actuel, pas une étape métier ajoutée
arbitrairement. Le nom est normalisé (espaces, forme « ma boutique s'appelle »),
refusé s'il est vide ou dépasse 255 caractères; la ville est refusée vide ou
au-delà de 120 caractères. Les coordonnées non finies ou hors bornes sont
refusées et ne sont jamais inversées ou géocodées.

### Confirmation, sécurité et concurrence

Le résumé expose les actions stables `seller_onboarding.confirm`, `.edit`,
`.cancel`. La confirmation verrouille `Conversation` puis `User`, revalide le
draft et la journey SELL, crée via `create_store(..., created_by=user)` dans la
transaction, ajoute/réutilise `UserRole.STORE_OWNER`, puis persiste le
`store_id` complété dans le draft. Ainsi Store et membership ne peuvent pas
être partiellement validées; une seconde confirmation retourne le même état.
Le verrou utilisateur et la recherche de membership active empêchent deux
conversations concurrentes d'aboutir à deux premières boutiques.

L'utilisateur est exclusivement celui de `Conversation.channel_identity.user`;
aucun `user_id`, `store_id`, rôle ou coordonnée texte client n'est accepté.
Les événements d'audit disponibles sont des logs structurés
`seller_onboarding_started` et `seller_onboarding_completed`, sans coordonnées
GPS. Après création, les actions `seller.catalog.chat` et
`seller.catalog.dashboard` conservent uniquement le choix de prochaine étape
pour les lots 8.3/8.4/8.5.

### Canaux et tests

L'orchestrateur existant délègue les messages du draft au même service avant
l'IA. WhatsApp et Telegram utilisent la présentation déjà ajoutée au lot 8.1;
Telegram fournit `metadata.action_id` depuis callback data, et WhatsApp reste
soumis à `WhatsAppOutboundPolicy`/template hors fenêtre. Les prompts
d'onboarding et les actions sont envoyés via `send_response_plan`, sans placer
de règles channel dans le service métier.

Ajout de `apps/conversations/tests/test_seller_onboarding.py` : parcours SELL
complet, création après confirmation seulement, OWNER + UserRole, ordre
longitude/latitude/SRID, location WhatsApp et Telegram, annulation/reprise,
double confirmation, boutique existante, coordonnées invalides et confirmation
concurrente. Les tests ne couvrent pas de géocodage car aucun géocodeur n'est
introduit.

Commandes réellement exécutées pour ce lot :

```text
.venv/bin/python -m compileall -q apps
.venv/bin/python manage.py check
.venv/bin/pytest -q apps/conversations/tests/test_orchestrator.py apps/telegram/tests/test_telegram.py -k 'test_greeting_does_not_create_order_or_call_catalog or callback'
.venv/bin/pytest -q apps/conversations/tests/test_seller_onboarding.py
git diff --check
```

Résultats réels : compilation, `manage.py check` et diff check passent; les deux
tests sans dépendance DB passent (2 passés). La suite onboarding a été lancée :
son test pur de coordonnées passe (1 passé), mais les six tests nécessitant DB
échouent avant leur setup car PostgreSQL/PostGIS sur `localhost:5432` reste
indisponible. Elle doit être relancée dans l'environnement de test avant
validation finale de la concurrence et des contraintes transactionnelles.

Aucune migration : seuls les états JSON existants de `Conversation.context`
sont étendus. Les limites restantes sont volontairement hors scope : édition
catalogue effective, dashboard vendeur, KYC, paiements, préparation de commande
et fonctionnalités livreur. Le résultat est prêt pour le Lot 8.3 : une Store
valide, localisée, avec son créateur membre OWNER, et un choix persistant de
point d'entrée catalogue.

## Phase 8 — Lot 8.3 : Seller Catalog Management Core

L'audit confirme la séparation existante entre `Product`, `ProductVariant`,
`ProductAlias` et `StoreProduct`. `StoreProduct` possède déjà `price`
(`DecimalField(12,2)`, nullable), currency XAF, disponibilité déclarative
`AVAILABLE`/`UNAVAILABLE`/`UNKNOWN`, indexes de recherche et contraintes DB de
prix et d'unicité Store/Product/Variant. Le retrait existant est un soft-disable
`UNAVAILABLE`, qui préserve les références historiques. `StoreFulfillmentService`
ne couvre les commandes futures que lorsque cette disponibilité est AVAILABLE;
aucun stock physique n'a été ajouté.

Ajout de `SellerCatalogService` : façade unique et déterministe pour le chat
8.4 et dashboard 8.5. Elle réutilise les primitives catalogue existantes pour
liste, ajout, prix, disponibilité, retrait et restauration, mais impose
`StoreMembership`: tout membre actif peut lister, OWNER/MANAGER seulement peut
muter via `can_manage_store_catalog`. Un rôle global ne donne aucun accès hors
membership de la Store visée.

L'ajout charge exclusivement les IDs Product/Variant persistés, refuse Product
absent/inactif, variante absente/inactive/d'un autre produit, et ne crée jamais
de Product depuis texte/IA. La politique doublon est `ALREADY_EXISTS` sans mise
à jour implicite. Prix : `Decimal` fini non négatif et compatible (12,2);
`None` signifie non déclaré, pas gratuit. Currency est ISO alphabétique à trois
caractères. Transactions, verrou StoreProduct, contraintes uniques et rattrapage
d'IntegrityError couvrent ajouts/updates concurrents.

L'API existante `/stores/{store_pk}/products/` passe désormais par cette façade
pour POST/PATCH/DELETE. Les changements produisent logs structurés minimaux
(actor/store/StoreProduct/old/new/correlation ID) et événements post-commit
`StoreProductAdded`, `StoreProductPriceChanged`,
`StoreProductAvailabilityChanged` via `common.events`. Les lectures utilisent
le selector existant avec `select_related`, sans N+1.

Ajout de `apps/catalog/tests/test_seller_catalog_service.py`: mutation/listing
autorisé, event, Decimal/null, disponibilité/retrait/restauration, isolation
inter-boutiques, Product absent/inactif, variantes, prix négatif/NaN, doublon
et ajout concurrent. Aucune migration n'est nécessaire : les indexes et
contraintes requis existaient déjà.

Commandes réellement exécutées : `.venv/bin/python -m compileall -q apps`,
`.venv/bin/python manage.py check`, `git diff --check` et
`.venv/bin/python manage.py makemigrations --check --dry-run`,
`.venv/bin/pytest -q apps/catalog/tests/test_seller_catalog_service.py`, ainsi
qu'un shell Django validant prix Decimal/NaN.
Compilation, check et diff passent; makemigrations ne détecte aucune migration
(avec l'avertissement de connexion DB connu). La suite catalogue a été lancée, mais ses
quatre tests DB échouent avant setup car PostgreSQL/PostGIS `localhost:5432`
est indisponible. Les limites volontaires sont le parsing LLM conversationnel
(8.4), UI dashboard, CSV/images et inventaire physique. La façade est prête
pour 8.4 : le chat devra seulement résoudre l'intention en IDs persistés puis
l'appeler.

## Phase 8 — Lot 8.4 : Conversational Catalog Management

L'audit a trouvé `AIService`/providers mock, validation JSON Schema,
`CatalogGroundingService` Phase 5 (`MATCHED`, `AMBIGUOUS`, `UNMATCHED`), le
contexte Conversation persistant, les actions normalisées WhatsApp/Telegram,
la façade 8.3 `SellerCatalogService` et `DatasetSanitizer` (suppression PII et
coordonnées avant persistance evaluation).

Ajout de `SellerCatalogUnderstandingService` avec le schéma strict
`seller_catalog_command` et les cinq intents LIST, ADD, UPDATE_PRICE,
SET_AVAILABILITY, REMOVE. Le contrat contient requête produit/variante, prix,
devise, disponibilité, confiance et champs manquants, mais aucun ID Product,
Variant, Store ou User. Une réponse IA malformée produit une erreur récupérable
sans mutation.

`SellerCatalogConversationService` réutilise le grounding Phase 5 : MATCH crée
une proposition, AMBIGUOUS persiste des candidats backend et affiche les choix
contrôlés `seller.catalog.select.{index}`, NOT_FOUND ne crée jamais un Product.
`PendingSellerCatalogMutation`, persisté dans `Conversation.context`, garde
uniquement action, IDs résolus, prix/devise/disponibilité, version `updated_at`
attendue et candidats — jamais le prompt complet.

Toute mutation nécessite `seller.catalog.confirm`; `.modify` et `.cancel`
effacent le draft sans toucher la base. Confirmation verrouille Conversation,
revalide journey SELL, membership, produit/StoreProduct et version stale, puis
appelle exclusivement `SellerCatalogService`. Un callback forgé ne peut utiliser
que les candidats du draft et la façade 8.3 revalide store/membership. Le draft
est supprimé après succès, rendant les doubles confirmations convergentes.
ADD/prix exigent un Decimal explicite; SET_AVAILABILITY exige l'enum réel;
REMOVE devient soft-disable UNAVAILABLE. Une faible confiance ou une intention
inconnue ne déclenche aucune mutation ni revendication de stock physique.

Le service est channel-agnostic et est appelé par l'orchestrateur. La
présentation passe par les adapters existants : WhatsApp conserve sa policy de
fenêtre/template, Telegram ses callbacks. Des logs sans texte brut couvrent
intent, grounding, proposition, confirmation/annulation et échec AI. Aucun
exemple n'est automatiquement ajouté aux datasets : l'infrastructure de
sanitization existe, mais la revue humaine reste obligatoire.

Ajout de `apps/ai/tests/test_seller_catalog_understanding.py`, couvrant contrat
sans IDs inventés, Decimal et réponse IA invalide. Commandes réellement
exécutées : tests AI + orchestrateur, compileall, `manage.py check`, diff check.
Résultat : 4 tests passent; compilation, check et diff passent. Aucune migration
n'est nécessaire, `Conversation.context` est seulement étendu. Les E2E DB
(ambiguïté, stale membership, callbacks et pagination) restent à exécuter quand
PostgreSQL/PostGIS sera disponible. La couche est prête pour 8.5 : le dashboard
peut appeler la façade 8.3 sans reproduire la logique métier.

## Phase 8 — Lot 8.5 : Seller Dashboard Access & Management API

L'audit a trouvé JWT comme auth DRF principale, sessions Django et middleware
CSRF déjà installés, CORS par allowlist, throttles DRF anon/user, corrélation
middleware et audit append-only. Aucun grant/magic link n'existait. Le modèle
StoreMembership supporte plusieurs boutiques, et les endpoints StoreProduct
appellent déjà la façade 8.3 après le lot précédent.

Le mécanisme retenu est stateful : `SellerDashboardAccessGrant` persiste user,
hash SHA-256 du token opaque généré par `secrets.token_urlsafe(32)`, purpose,
expiration, consommation et révocation. Ce choix permet usage unique,
révocation et concurrence, contrairement à un lien signed stateless. Le token
brut n'est jamais stocké ni loggué. Son TTL est configurable par
`SELLER_DASHBOARD_LINK_TTL_SECONDS` (défaut 600 secondes); l'émission révoque
les grants actifs précédents du même vendeur.

`SellerDashboardAccessService.issue_access_link()` vérifie une membership
OWNER/MANAGER locale avant émission. `consume()` verrouille le grant, vérifie
hash/purpose/expiration/révocation, marque `consumed_at` dans la transaction et
refuse toute seconde consommation. L'URL de conversation place le token dans le
fragment `#token=`, non transmis au serveur ni Referer; le frontend doit le
POSTer immédiatement vers `seller-dashboard/access/consume/`, puis remplacer
l'historique par une URL propre. La consommation fait un `login()` Django et
ouvre une session HttpOnly; DRF accepte maintenant JWT puis SessionAuthentication,
donc les mutations session restent protégées par CSRF. Les settings production
possédaient déjà cookies Secure et le CORS reste une allowlist, sans désactivation
globale CSRF.

Le parcours chat reconnaît `seller.dashboard.open` et « espace vendeur » puis
appelle le service d'accès, jamais le webhook. Les APIs Dashboard ajoutées listent
les Stores accessibles, détail Store, catalogue et commandes liées aux offres,
avec validation StoreMembership pour chaque `store_id`. La gestion catalogue web
réutilise les endpoints StoreProduct existants et `SellerCatalogService` 8.3;
elle ne crée ni ne modifie StoreProduct directement. Les commandes sont lecture
seule en attente du lot 8.6.

L'action structurée reste disponible même quand `AI_ENABLED=False` : le
composition root instancie la couche conversationnelle avec un understander
inactif qui ne peut produire qu'une intention inconnue. Ainsi les callbacks
déterministes (dont `seller.dashboard.open`) ne dépendent ni d'un LLM ni d'une
réponse IA. Les tentatives de consommation d'un grant connu mais expiré, révoqué
ou déjà consommé produisent aussi un audit `REJECTED`; un token inconnu ne peut
être associé à une entité et est uniquement journalisé sans valeur secrète.

Audit persistant ajouté via `AuditEvent` pour émission et consommation; logs
structurés sans raw token couvrent issued/consumed/expired/invalid/denied.
Les throttles DRF génériques protègent la consommation publique; aucune
infrastructure de rate limiting distincte n'existait pour l'émission chat.

Migrations ajoutées : `stores.0008_seller_dashboard_access_grant`, ainsi que
`audit.0002`/`0003` pour les nouveaux types d'audit et leurs contraintes. Tests
ajoutés dans `apps/stores/tests/test_dashboard_access.py` pour hash, usage unique,
révocation, absence de membership, expiration et token inconnu. Commandes
réellement exécutées : compileall, `manage.py check`, makemigrations check,
diff check, tests AI/orchestrateur et suite dashboard grant. Checks et les 4
tests AI/orchestrateur passent; makemigrations ne détecte aucun changement (avec
l'avertissement DB connu). Les 2 tests grant ont été lancés mais échouent avant
setup car PostgreSQL/PostGIS `localhost:5432` est indisponible.

Limites/risques : le frontend réel doit respecter l'échange fragment→POST→clean
history et fournir le CSRF token pour les mutations session; des tests E2E de
session, CSRF, CORS, concurrence et Dashboard API doivent être exécutés avec
PostgreSQL. Le contrat d'accès et les endpoints read-only commandes sont prêts
pour le lot 8.6, sans introduire un second backend métier.

## Phase 8 — Lot 8.6 : Store Preparation Workflow

L'audit a confirmé que Phase 3 possède déjà la machine d'état canonique et les
fonctions `mark_preparing()` et `mark_ready_for_pickup()`. Ce lot utilise
uniquement `STORE_ACCEPTED → PREPARING → READY_FOR_PICKUP`; aucun statut Driver
ni matching n'est appelé. La Store gagnante est déterminée depuis la
`StoreOffer` persistée avec statut `ACCEPTED`, jamais depuis un `store_id` client.

`StorePreparationService` est la façade commune Chat/Dashboard. Dans une courte
transaction, il verrouille Order et l'offre acceptée, revalide la membership via
`StoreOfferAuthorizationPolicy`, puis appelle exclusivement les services Orders
existants. Les répétitions renvoient `ALREADY_PREPARING` ou `ALREADY_READY`, sans
audit ou événement supplémentaire; les conflits et droits retirés sont refusés.
L'audit status-change existant conserve actor, Order, états et correlation id;
les logs structurés ajoutent les mesures started, ready, denied et conflict.

Les action IDs sont `store_order.start_preparation` et
`store_order.mark_ready`. Ils contournent le LLM et passent par les adapters
WhatsApp/Telegram existants. Une action conversationnelle ne résout une commande
que si elle est l'unique commande autorisée dans l'état pertinent. Le Dashboard
expose les POST `seller-dashboard/orders/<order_id>/start-preparation/` et
`mark-ready/`, qui appellent ce même service.

`OrderReadyForPickup` est publié via `transaction.on_commit` une seule fois à la
transition effective READY. Aucun subscriber Driver, DriverOffer ou transition
au-delà de READY n'est créé. Aucune primitive persistante de notification client
post-acceptation n'existait; son raccordement post-commit reste à faire sans
rollback métier. La machine existante permet FAILED/CANCELLED pendant la
préparation; aucun timeout ou workflow de substitution n'est ajouté.

Ajout de `apps/stores/tests/test_preparation.py` : chemin autorisé, idempotence,
event unique, ready direct refusé et non-membre. Aucune migration n'est requise.
Les contrôles statiques et les 4 tests AI/orchestrateur passent. Les 2 tests de
préparation ont été lancés mais échouent au setup car PostgreSQL/PostGIS
`localhost:5432` est indisponible. Les tests DB de concurrence, API et adapters
doivent être exécutés avec PostgreSQL avant clôture. Le contrat READY est prêt
pour Phase 9 sans démarrer le matching.

## Phase 8 — Lot 8.7 : Integration, Security, Recovery & End-to-End Validation

### Inventaire inspecté

Les lots 8.1 à 8.6 sont réellement présents : journey temporaire dans
`Conversation.context` (`ActiveJourney`, draft onboarding et mutation catalogue
en attente), `JourneyRoutingService`, `SellerOnboardingService`,
`SellerCatalogService`, `SellerCatalogConversationService`, grant Dashboard
stateful, `StorePreparationService` et `OrderReadyForPickup`. Les nouvelles
migrations sont `stores.0008` et `audit.0002/0003`; aucune migration 8.6/8.7
n'est requise. Les contraintes pertinentes trouvées sont l'unicité
StoreOffer acceptée par Order, StoreOffer(Order, Store), StoreMembership(Store,
User), le grant hash unique et les index de grant, StoreOffer et catalog déjà
existants.

Les actions stables sont `journey.select.*`, `seller_onboarding.*`,
`seller.catalog.*`, `seller.dashboard.open` et `store_order.*`. Les endpoints
Dashboard sont sous `/api/v1/seller-dashboard/`; les mutations catalogue restent
les endpoints StoreProduct qui passent par `SellerCatalogService`; préparation
utilise les endpoints Dashboard dédiés. Les tâches persistantes inspectées ne
concernent que StoreOffer notifications/réconciliation. Aucun TODO métier 8.x,
matching Driver, DriverOffer ou subscriber de `OrderReadyForPickup` n'a été
trouvé.

### Résultats d'audit

Les intentions ambiguës passent par la clarification BUY/SELL/DELIVER et les
intentions explicites réutilisent l'understanding existant. SELL conserve le
draft persistant et crée Store, membership OWNER et rôle STORE_OWNER dans une
transaction; `city` est aussi demandé car le modèle Store l'exige. La location
native est validée finie/ranges et stockée `Point(longitude, latitude, 4326)`;
aucun géocodage ou log GPS explicite n'est ajouté. BUY reste dans
l'orchestrateur client existant; DELIVER retourne le handoff Phase 9 sans créer
de profil.

Le catalogue Chat fait extraction structurée sans IDs, grounding backend,
clarification AMBIGUOUS et refus NOT_FOUND; les confirmations passent par la
façade 8.3. Cette façade impose Product/variant persistés, membership locale,
Decimal fini/non négatif, currency et enum availability. `None` signifie prix
non déclaré, jamais gratuit; AVAILABLE reste une disponibilité déclarée, pas un
stock physique. Les services IA ne font aucune écriture ORM. Les écritures ORM
trouvées hors façade sont des services historiques de catalogue, matching,
notifications, infrastructure, fixtures/migrations/tests; les vues vendeur
passent bien par les façades.

L'accès Dashboard utilise token opaque hashé, TTL configurable, révocation,
consommation atomique et fragment URL; sessions Django restent protégées par
CSRF et CORS est une allowlist. Les vues revalident StoreMembership; UUID de
Store/Order arbitraire ne confère aucun accès. `StorePreparationService` verrouille
Order + StoreOffer acceptée, réutilise la state machine, est idempotent et publie
OrderReadyForPickup après commit. Aucune notification/provider externe n'est
effectuée dans cette transaction. Les callbacks conversationnels sont dédupliqués
par MessageEvent/contexte persistant et les drafts permettent reprise après
restart; les scénarios réels DB restent à exécuter.

### Commandes et résultats réels

- `.venv/bin/python -m compileall -q apps config` : succès.
- `.venv/bin/python manage.py check` : succès.
- `.venv/bin/python manage.py makemigrations --check --dry-run` : `No changes
  detected`, avec avertissement de connexion PostgreSQL indisponible.
- `git diff --check` : succès.
- `.venv/bin/pytest --collect-only -q` : 364 tests collectés.
- `.venv/bin/pytest -q apps/ai/tests/test_seller_catalog_understanding.py
  apps/conversations/tests/test_orchestrator.py` : 4 passed.
- Suites WhatsApp/Telegram ciblées : 34 passed, 13 errors au setup DB.
- Suites journey/onboarding/catalog ciblées : 5 passed, 15 errors au setup DB.
- `apps/stores/tests/test_dashboard_access.py` : 2 errors au setup DB.
- `apps/stores/tests/test_preparation.py` : 2 errors au setup DB.

Tous les errors listés proviennent de l'absence de PostgreSQL/PostGIS sur
`localhost:5432`; aucun test concerné n'a atteint son assertion métier. `migrate`
et la suite complète ne sont donc pas exécutables dans cet environnement.

### Matrice Definition of Done

YES (inspection/contrôles unitaires disponibles) : 1-6, 8-17, 19-24, 30, 34.
NO non validé par test PostgreSQL réel : 7, 18, 25-29, 31-33, 35. Les NO 18,
25-29 et 31-33 sont des blockers critiques de clôture car ils couvrent isolation,
adapters E2E, concurrence, récupération et régression. Le NO 7 est aussi à
revalider sur persistence réelle. Une dette non critique reste le raccordement
des notifications client post-commit et un scénario seed E2E documenté/exécutable
sur base de test.

# Phase 8 Closure

```yaml
Overall status: BLOCKED
Critical blockers: PostgreSQL/PostGIS indisponible; E2E, isolation et races non validés
Non-critical debt: notification client post-commit; seed/demo exécutable
Tests executed: collect, checks, suites ciblées
Tests passed: 4 + 34 + 5 (commandes distinctes)
Tests failed: 0 assertion métier observée
Tests skipped: 0 rapporté
Tests blocked: 32 errors de setup PostgreSQL sur les suites citées
Migrations status: no changes detected, non appliquées ici
Security status: design inspecté, non validé E2E DB
Concurrency status: code/locks inspectés, non validés PostgreSQL
Recovery status: persistence inspectée, non validée E2E DB
Phase 1-7 regression status: non validée intégralement
```

# Phase 10 — Async Operations Foundation

Phase 10 consolide le travail opérationnel asynchrone sans déplacer la vérité
métier hors PostgreSQL/PostGIS. Les tasks Celery reçoivent des IDs opaques,
rechargent l'état et appellent les services applicatifs existants.

- 10.1 : app Celery unique, JSON, corrélation, enqueue post-commit, acks tardifs
  sur tâches critiques, services worker/Beat de production.
- 10.2–10.3 : notifications persistantes, attempts append-only, routage canal,
  retry borné/backoff, taxonomie d'échecs et replay sûr.
- 10.4 : ETA après commit et sweeps DB batchés pour StoreOffer/DriverOffer.
- 10.5 : cycles bornés de rappel de localisation Driver; une assignment active
  n'est jamais annulée ni rematchée à cause d'une position stale.
- 10.6 : reconciliation Store/Driver/notifications/reminders par services
  existants, sans mutation directe d'état métier dans les reconcilers.

Le core Phase 10 n'appelle aucun LLM et n'implémente pas les états Phase 11
`PICKED_UP`, `IN_DELIVERY` ou `DELIVERED`.

Validation locale `.venv` avec PostgreSQL/PostGIS : migrations appliquées,
`manage.py check` OK et 16 tests Phase 10 ciblés passants. La suite complète
conserve une collision de fixture catalogue reproductible (téléphone WhatsApp
dupliqué dans `test_authorization_product_variant_and_price_validation`), sans
lien avec les changements async Phase 10.
