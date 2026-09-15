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
