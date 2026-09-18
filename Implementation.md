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
