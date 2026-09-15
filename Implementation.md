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
