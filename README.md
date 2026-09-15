# Whatassoh Backend

Backend principal de **Whatassoh**, une marketplace conversationnelle accessible principalement via **WhatsApp**.

L'objectif est de permettre à un utilisateur de rechercher, commander et faire livrer des produits à travers une conversation naturelle, tout en conservant un backend métier fiable, transactionnel et indépendant de l'IA ou du canal WhatsApp.

Le projet est construit principalement avec :

- **Python / Django**
- **Django REST Framework**
- **GeoDjango**
- **PostgreSQL / PostGIS**
- **Redis / Redis GEO**
- **Celery / Celery Beat**
- **WhatsApp Cloud API**
- Une couche **IA Python** isolée derrière des contrats stricts

---

## 1. Principes d'architecture

Whatassoh suit une architecture de type **Modular Monolith orientée domaines**.

Le backend est initialement déployé comme une seule application Django, mais le code est découpé en modules métier indépendants afin d'éviter la création d'un monolithe désorganisé.

### Principes fondamentaux

- **Django est la source de vérité métier.**
- **PostgreSQL/PostGIS contient les données persistantes et critiques.**
- **Redis contient uniquement des données temporaires ou reconstructibles.**
- **Celery exécute les traitements asynchrones, mais ne porte pas la vérité métier.**
- **WhatsApp est un canal de communication, pas le moteur métier.**
- **L'IA interprète et assiste, mais ne modifie jamais directement l'état métier.**
- Les changements critiques sont validés par le backend.
- Les opérations sensibles doivent être idempotentes.
- Les affectations critiques utilisent des transactions et des mécanismes de contrôle de concurrence.
- Les règles métier ne sont pas placées directement dans les vues HTTP.

### Flux général

```text
Utilisateur
    ↓
WhatsApp
    ↓
WhatsApp Cloud API
    ↓
Webhook
    ↓
Django / DRF
    ↓
Application Service
    ↓
Domain Rules
    ↓
PostgreSQL / PostGIS
```

L'IA intervient comme une couche d'interprétation :

```text
Message utilisateur
       ↓
     Django
       ↓
   AI Adapter
       ↓
Résultat structuré
       ↓
Validation Django
       ↓
Règles métier
       ↓
PostgreSQL
```

L'IA ne peut donc jamais effectuer directement une transition métier ou modifier la base de données.

---

## 2. Stack technique

### Backend

- Python
- Django
- Django REST Framework
- Django ASGI

### Base de données

- PostgreSQL
- PostGIS
- GeoDjango
- Psycopg

PostgreSQL/PostGIS constitue la **source de vérité transactionnelle et géographique**.

Les données géographiques importantes utilisent de vrais types spatiaux.

Exemple :

```python
from django.contrib.gis.db import models

location = models.PointField(
    geography=True,
    srid=4326,
)
```

Exemples de données géographiques persistantes :

```text
Store.location
Order.delivery_location
Delivery.pickup_location
Delivery.destination
TrackingPoint.location
```

---

### Redis

Technologies :

- Redis
- django-redis
- Redis GEO

Redis est utilisé notamment pour :

- le cache ;
- la présence temporaire des chauffeurs ;
- les positions live ;
- la recherche rapide de proximité ;
- certaines données temporaires d'idempotence ;
- certains verrous temporaires ;
- le broker Celery ;
- certains résultats Celery.

> Redis ne doit jamais contenir l'unique copie d'une donnée métier critique.

---

### Celery

Technologies :

- Celery
- Celery Beat
- django-celery-beat

Celery est utilisé pour les traitements qui ne doivent pas bloquer une requête HTTP.

Exemples de futures tâches :

```text
send_whatsapp_notification
expire_store_offer
expire_driver_offer
retry_matching
persist_tracking_sample
cleanup_stale_presence
daily_operational_metrics
```

Celery exécute des travaux demandés par l'application.

Il ne constitue pas une source de vérité métier.

---

### API

L'API est construite avec **Django REST Framework**.

Les APIs métier sont versionnées :

```text
/api/v1/
```

Exemples :

```text
/api/v1/identity/
/api/v1/stores/
/api/v1/catalog/
/api/v1/orders/
/api/v1/drivers/
/api/v1/deliveries/
/api/v1/tracking/
```

Les intégrations externes sont séparées des APIs métier.

Exemple :

```text
/webhooks/whatsapp/
/webhooks/telegram/
```

Les endpoints d'infrastructure sont également séparés :

```text
/health/
/ready/
```

La documentation API est exposée via :

```text
/api/schema/
/api/docs/
```

---

## 3. Configuration Telegram Bot API

Le bot Telegram est cree manuellement via BotFather. Le backend consomme
ensuite le token et recoit les updates en webhook.

Etapes:

1. Creer le bot avec BotFather et recuperer le token.
2. Configurer les variables d'environnement:

```text
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_WEBHOOK_SECRET=<telegram-webhook-secret>
TELEGRAM_WEBHOOK_URL=https://example.com/webhooks/telegram/
TELEGRAM_API_BASE_URL=https://api.telegram.org
```

3. Exposer le backend sur une URL HTTPS joignable par Telegram.
4. Configurer le webhook:

```bash
python manage.py telegram_set_webhook
```

Commandes utiles:

```bash
python manage.py telegram_webhook_info
python manage.py telegram_delete_webhook
python manage.py telegram_delete_webhook --drop-pending-updates
```

Ne jamais commiter de token BotFather ou de secret webhook. Si
`TELEGRAM_ENABLED=false`, Django demarre sans credentials Telegram.

---

## 4. Configuration multicanale

Les canaux de messagerie sont actives independamment. Les quatre modes suivants
sont valides:

```text
WHATSAPP_ENABLED=true
TELEGRAM_ENABLED=false

WHATSAPP_ENABLED=false
TELEGRAM_ENABLED=true

WHATSAPP_ENABLED=true
TELEGRAM_ENABLED=true

WHATSAPP_ENABLED=false
TELEGRAM_ENABLED=false
```

Variables principales:

```text
WHATSAPP_ENABLED=false
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_REQUEST_TIMEOUT=10
META_GRAPH_API_VERSION=v26.0

TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_WEBHOOK_URL=
TELEGRAM_API_BASE_URL=https://api.telegram.org
TELEGRAM_REQUEST_TIMEOUT=10
```

Le code applicatif commun utilise le registry et la facade messaging:

```python
from apps.messaging.application.orchestration import send_conversation_text

send_conversation_text(
    conversation=conversation,
    text="Comment puis-je vous aider ?",
)
```

Le canal vient de `Conversation.channel`. L'appelant n'a pas besoin de connaitre
`WhatsAppProvider` ou `TelegramProvider`.

---

## 5. Structure générale du projet

```text
backend/
│
├── manage.py
├── pyproject.toml
├── pytest.ini
├── docker-compose.yml
│
├── .env
├── .env.example
├── .gitignore
│
├── config/
│   ├── __init__.py
│   ├── urls.py
│   ├── api_urls.py
│   ├── asgi.py
│   ├── wsgi.py
│   ├── celery.py
│   │
│   └── settings/
│       ├── __init__.py
│       ├── base.py
│       ├── development.py
│       ├── production.py
│       └── test.py
│
├── apps/
│   └── __init__.py
│
├── common/
│   ├── domain/
│   ├── events/
│   ├── exceptions/
│   ├── pagination/
│   ├── permissions/
│   ├── responses/
│   ├── types/
│   ├── utils/
│   ├── health/
│   └── middleware.py
│
└── tests/
    ├── integration/
    └── e2e/
```

---

## 6. Dossier `config/`

Le dossier `config/` contient la configuration globale du projet.

Il ne doit pas contenir de logique métier.

### `config/settings/`

Les paramètres Django sont séparés par environnement :

```text
config/settings/
├── base.py
├── development.py
├── production.py
└── test.py
```

### `base.py`

Contient la configuration commune :

- applications Django ;
- Django REST Framework ;
- PostgreSQL/PostGIS ;
- GeoDjango ;
- Redis ;
- Celery ;
- pagination ;
- authentification ;
- permissions globales ;
- CORS ;
- OpenAPI ;
- sécurité commune ;
- internationalisation ;
- fichiers statiques ;
- configuration générale.

### `development.py`

Configuration utilisée pour le développement local.

Exemples :

```text
DEBUG=True
localhost autorisé
services locaux
logs développeur
```

### `production.py`

Configuration stricte de production.

Exemples :

```text
DEBUG=False
HTTPS obligatoire
cookies sécurisés
HSTS
configuration reverse proxy
```

### `test.py`

Configuration utilisée par les tests automatisés.

Elle permet notamment :

- d'accélérer certains traitements ;
- d'utiliser une configuration isolée ;
- d'exécuter certaines tâches Celery immédiatement ;
- d'utiliser des providers mock lorsque nécessaire.

---

## 7. Dossier `apps/`

Le dossier `apps/` contient les différents domaines métier.

À terme :

```text
apps/
├── identity/
├── conversations/
├── catalog/
├── stores/
├── orders/
├── matching/
├── drivers/
├── deliveries/
├── notifications/
├── whatsapp/
├── ai/
├── tracking/
└── audit/
```

### Responsabilités

| Module | Responsabilité |
|---|---|
| `identity` | Utilisateurs, identités, numéros WhatsApp, rôles, permissions et statuts |
| `conversations` | Contexte conversationnel et messages utiles |
| `catalog` | Produits, unités, variantes et données communes |
| `stores` | Boutiques, disponibilité, coordonnées, zones et catégories |
| `orders` | Commandes, articles, états, transitions et invariants |
| `matching` | Recherche et classement des boutiques et chauffeurs |
| `drivers` | Profils chauffeurs, disponibilité et statut opérationnel |
| `deliveries` | Missions de livraison et chauffeur assigné |
| `notifications` | Notifications métier |
| `whatsapp` | Webhooks Meta et client WhatsApp Cloud API |
| `ai` | Extraction d'intention, parsing et recommandations IA |
| `tracking` | Sessions et positions de tracking |
| `audit` | Traçabilité des événements et changements critiques |

---

## 8. Structure interne d'une application métier

Chaque domaine suit autant que possible cette organisation :

```text
apps/orders/
├── apps.py
├── migrations/
│
├── domain/
│   ├── enums.py
│   ├── rules.py
│   ├── events.py
│   └── services.py
│
├── application/
│   ├── commands/
│   ├── queries/
│   └── services/
│
├── infrastructure/
│   ├── models.py
│   ├── repositories.py
│   └── selectors.py
│
├── api/
│   ├── serializers.py
│   ├── views.py
│   └── urls.py
│
├── tasks.py
├── admin.py
│
└── tests/
    ├── test_domain.py
    ├── test_services.py
    └── test_api.py
```

---

### `domain/`

Contient les règles métier pures.

Exemples :

- états ;
- transitions ;
- invariants ;
- règles métier ;
- événements de domaine.

Le domaine ne doit pas dépendre directement de :

- Django REST Framework ;
- WhatsApp ;
- Meta ;
- OpenAI ;
- Redis ;
- Celery.

---

### `application/`

Contient les cas d'usage de l'application.

#### `commands/`

Actions qui modifient l'état du système.

Exemples :

```text
confirm_order
accept_store_offer
assign_driver
cancel_order
complete_delivery
```

#### `queries/`

Cas d'usage de lecture.

Exemples :

```text
get_order_status
find_available_stores
get_delivery_history
find_available_drivers
```

#### `services/`

Orchestration de plusieurs règles, repositories ou services afin d'exécuter un cas d'usage.

---

### `infrastructure/`

Contient les détails techniques de persistance et d'accès aux données.

#### `models.py`

Modèles Django ORM.

#### `repositories.py`

Abstraction de certaines opérations de persistance.

#### `selectors.py`

Requêtes de lecture optimisées et centralisées.

Exemples :

```text
get_nearby_active_stores
get_available_drivers
get_open_store_offers
```

---

### `api/`

Contient la couche HTTP.

Elle est responsable de :

1. récupérer les entrées HTTP ;
2. valider les données ;
3. identifier l'utilisateur ;
4. vérifier les permissions ;
5. appeler le cas d'usage correspondant ;
6. retourner la réponse HTTP.

Une vue ne doit pas contenir directement une logique métier complexe.

### À éviter

```python
order.status = "DELIVERED"
order.save()
```

directement dans une vue.

### Préférer

```python
complete_delivery(
    order_id=order_id,
    actor=request.user,
)
```

Le service métier décide ensuite si la transition est réellement autorisée.

---

## 9. Dossier `common/`

Le dossier `common/` contient les éléments partagés entre plusieurs domaines.

```text
common/
├── domain/
├── events/
├── exceptions/
├── pagination/
├── permissions/
├── responses/
├── types/
├── utils/
├── health/
└── middleware.py
```

Il ne doit pas devenir un dossier dans lequel placer arbitrairement tout le code qui ne semble appartenir à aucun module.

---

## 10. Routing

Toutes les APIs métier utilisent le préfixe :

```text
/api/v1/
```

Exemple :

```text
/api/v1/orders/
```

Chaque application possède son propre fichier :

```text
apps/<module>/api/urls.py
```

Les routes sont ensuite regroupées dans :

```text
config/api_urls.py
```

Puis incluses par :

```text
config/urls.py
```

La hiérarchie est donc :

```text
config/urls.py
       ↓
config/api_urls.py
       ↓
apps/orders/api/urls.py
       ↓
views
       ↓
application
       ↓
domain
```

Les webhooks ne sont pas mélangés aux APIs métier :

```text
/webhooks/whatsapp/
```

---

## 11. Pagination

La pagination globale utilise actuellement une pagination par numéro de page.

Exemple :

```http
GET /api/v1/orders?page=2&page_size=20
```

Configuration :

```text
page_size = 20
max_page_size = 100
```

Cela empêche notamment un client de demander arbitrairement :

```text
?page_size=100000
```

Les endpoints nécessitant une autre stratégie pourront utiliser une pagination spécifique.

---

## 12. Gestion des erreurs

Les erreurs API doivent utiliser une structure cohérente.

Exemple :

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request contains invalid data.",
    "details": {
      "phone": [
        "This field is required."
      ]
    }
  }
}
```

Les erreurs métier utilisent également des codes explicites.

Exemple :

```json
{
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Order cannot transition from PREPARING to DELIVERED.",
    "details": {
      "current_state": "PREPARING",
      "requested_state": "DELIVERED"
    }
  }
}
```

Les codes doivent permettre au frontend et aux intégrations de comprendre la catégorie d'erreur sans dépendre uniquement du texte du message.

---

## 13. Authentification et sécurité

Le projet utilise une approche restrictive par défaut.

Les endpoints DRF nécessitent une authentification sauf lorsqu'ils sont explicitement déclarés publics.

Les mécanismes d'authentification dépendent du type d'endpoint.

```text
API interne        → JWT
Django Admin       → Session Django
Webhook WhatsApp   → Vérification Meta
Tracking           → Token temporaire signé
```

### Principes de sécurité

- Les secrets sont stockés dans les variables d'environnement.
- Aucun secret ne doit être versionné dans Git.
- Les mots de passe utilisent Argon2 en priorité.
- Toutes les entrées externes sont validées.
- HTTPS est obligatoire en production.
- Les origines CORS sont explicitement configurées.
- Les endpoints sensibles sont protégés contre les abus.
- Les permissions suivent le principe du moindre privilège.
- Les données personnelles exposées sont limitées au strict nécessaire.
- Les opérations critiques utilisent des transactions.
- L'administration Django doit être fortement protégée en production.

---

## 14. Idempotence

Les systèmes externes peuvent envoyer plusieurs fois le même événement.

Le backend doit donc identifier les événements déjà traités.

Exemple :

```text
WhatsApp message_id
```

Recevoir deux fois le même événement ne doit jamais produire :

```text
2 commandes
2 affectations
2 traitements métier identiques
2 notifications métier non souhaitées
```

Un retry technique ne doit pas rejouer arbitrairement une transaction métier déjà validée.

---

## 15. Concurrence

Certaines opérations doivent être protégées contre les accès simultanés.

### Affectation boutique

```text
Boutique A ── accepte ──┐
                        ├──→ PostgreSQL ──→ 1 gagnant
Boutique B ── accepte ──┘
```

Même si deux boutiques acceptent presque simultanément, une seule doit gagner.

### Affectation chauffeur

```text
Chauffeur A ── accepte ──┐
                         ├──→ PostgreSQL ──→ 1 chauffeur assigné
Chauffeur B ── accepte ──┘
```

Les opérations critiques peuvent utiliser :

- transactions PostgreSQL ;
- `select_for_update()` ;
- contraintes de base de données ;
- mises à jour conditionnelles.

Redis ou Celery ne décident jamais seuls du résultat final.

---

## 16. Géolocalisation

Whatassoh distingue les données géographiques persistantes des données géographiques temps réel.

### PostGIS

PostGIS conserve la vérité géographique durable.

Exemples :

- boutiques ;
- destination d'une commande ;
- points de retrait ;
- positions persistées ;
- historique de tracking.

### Redis GEO

Redis GEO est utilisé comme index opérationnel rapide.

Exemples :

- chauffeurs actuellement disponibles ;
- dernière position live ;
- recherche rapide des chauffeurs proches.

Flux :

```text
Position chauffeur
        ↓
Tracking API
        ↓
Redis GEO
        ↓
Candidats proches
        ↓
Filtres métier Django
        ↓
Validation PostgreSQL
        ↓
Affectation
```

> Redis accélère la recherche. PostgreSQL/PostGIS valide toujours la décision finale.

---

## 17. Celery et traitements asynchrones

Les tâches asynchrones utilisent Celery.

Flux :

```text
Django
   ↓
Redis Broker
   ↓
Celery Worker
   ↓
Task
```

Exemple de worker :

```bash
celery -A config worker --loglevel=info
```

Les tâches périodiques utilisent Celery Beat :

```bash
celery -A config beat --loglevel=info
```

Les tâches liées à un domaine doivent idéalement être placées dans :

```text
apps/<domain>/tasks.py
```

Exemple :

```text
apps/orders/tasks.py
apps/notifications/tasks.py
apps/deliveries/tasks.py
```

---

## 18. Observabilité

Chaque requête HTTP reçoit un `correlation_id`.

Exemple :

```text
X-Correlation-ID: 84024d6c-...
```

Cet identifiant permet de suivre une opération complète :

```text
Webhook WhatsApp
       ↓
     Django
       ↓
Application Service
       ↓
   PostgreSQL
       ↓
     Celery
       ↓
Notification WhatsApp
```

Les logs sont structurés.

Exemple :

```json
{
  "event": "driver.assigned",
  "order_id": "...",
  "delivery_id": "...",
  "driver_id": "...",
  "correlation_id": "...",
  "level": "info"
}
```

Les informations importantes à tracer incluent notamment :

```text
order_id
delivery_id
actor_id
event_type
correlation_id
status
duration
result
```

---

## 19. Environnements

Le projet prévoit quatre environnements principaux :

```text
development
test
staging
production
```

### Development

Utilisé pour le développement local.

### Test

Utilisé pour les tests automatisés.

### Staging

Environnement proche de la production permettant :

- les tests de bout en bout ;
- les démonstrations ;
- la validation des intégrations.

### Production

Environnement destiné aux utilisateurs réels avec :

- HTTPS ;
- secrets gérés ;
- logs centralisés ;
- monitoring ;
- sauvegardes ;
- paramètres de sécurité stricts.

---

## 20. Variables d'environnement

Créer un fichier :

```text
.env
```

à partir de :

```text
.env.example
```

Exemple :

```dotenv
DJANGO_ENV=development

DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

POSTGRES_DB=marketplace
POSTGRES_USER=marketplace
POSTGRES_PASSWORD=
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

REDIS_URL=redis://localhost:6379/0

CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

CORS_ALLOWED_ORIGINS=http://localhost:3000

SENTRY_DSN=
```

> Le fichier `.env` ne doit jamais être versionné.

---

## 21. Installation locale

### Créer l'environnement virtuel

```bash
python3 -m venv .venv
```

Activer l'environnement :

```bash
source .venv/bin/activate
```

Mettre `pip` à jour :

```bash
python -m pip install --upgrade pip
```

---

## 22. Infrastructure locale

L'infrastructure locale utilise Docker Compose.

Les principaux services sont :

```text
PostgreSQL + PostGIS
Redis
```

Démarrer l'infrastructure :

```bash
docker compose up -d
```

Vérifier les services :

```bash
docker compose ps
```

Tester Redis :

```bash
docker compose exec redis redis-cli ping
```

Résultat attendu :

```text
PONG
```

---

## 23. Base de données

Appliquer les migrations :

```bash
python manage.py migrate
```

Vérifier la configuration Django :

```bash
python manage.py check
```

PostGIS peut être vérifié avec :

```bash
docker compose exec postgres \
    psql -U marketplace -d marketplace
```

Puis :

```sql
SELECT PostGIS_Version();
```

---

## 24. Lancer Django

```bash
python manage.py runserver
```

Le serveur local est accessible sur :

```text
http://127.0.0.1:8000/
```

### Health check

```http
GET /health/
```

Réponse attendue :

```json
{
  "status": "ok"
}
```

---

## 25. Documentation API

### OpenAPI Schema

```text
/api/schema/
```

### Swagger UI

```text
/api/docs/
```

---

## 26. Lancer Celery

Dans un autre terminal :

```bash
source .venv/bin/activate
```

Puis :

```bash
celery -A config worker --loglevel=info
```

Au démarrage, Celery affiche les tâches enregistrées :

```text
[tasks]
  . apps.orders.tasks.some_task
  . apps.notifications.tasks.some_task
```

Une tâche absente de cette liste n'est pas enregistrée par le worker.

---

## 27. Lancer Celery Beat

```bash
celery -A config beat --loglevel=info
```

Celery Beat est destiné aux traitements périodiques tels que :

```text
expiration d'offres
nettoyage des présences expirées
persistance périodique du tracking
métriques opérationnelles
```

---

## 28. Tests

Les tests utilisent `pytest`.

Lancer tous les tests :

```bash
pytest
```

Avec couverture :

```bash
pytest --cov=.
```

Les tests sont répartis entre :

```text
tests/
├── integration/
└── e2e/
```

Chaque application peut également posséder ses propres tests :

```text
apps/orders/tests/
├── test_domain.py
├── test_services.py
└── test_api.py
```

---

## 29. Qualité du code

Le projet utilise **Ruff** pour le linting et le formatage.

### Vérifier le code

```bash
ruff check .
```

### Corriger automatiquement certaines erreurs

```bash
ruff check . --fix
```

### Formater

```bash
ruff format .
```

### Vérifier le formatage sans modifier

```bash
ruff format --check .
```

Le typage statique peut être vérifié avec :

```bash
mypy .
```

---

## 30. Commandes de validation

Avant de considérer une modification comme prête :

```bash
python manage.py check
```

```bash
pytest
```

```bash
ruff check .
```

```bash
ruff format --check .
```

Selon les changements apportés :

```bash
python manage.py makemigrations --check --dry-run
```

---

## 31. Règles de développement

### Règle 1 — Les vues HTTP ne portent pas le métier

Une vue doit appeler un cas d'usage.

Elle ne doit pas modifier arbitrairement plusieurs modèles.

---

### Règle 2 — Les fournisseurs externes sont isolés

Les domaines métier ne doivent pas dépendre directement de :

```text
OpenAI SDK
Meta SDK
Redis client
```

Les intégrations passent par des interfaces, adapters ou services internes.

---

### Règle 3 — PostgreSQL est la source de vérité

Redis est considéré comme temporaire et reconstructible.

Une panne Redis ne doit pas entraîner la perte :

- d'une commande ;
- d'une affectation ;
- d'une livraison ;
- d'une décision métier critique.

---

### Règle 4 — L'IA ne prend aucune décision transactionnelle

L'IA peut :

- interpréter ;
- classifier ;
- extraire des informations ;
- recommander ;
- demander une clarification.

L'IA ne peut pas :

- attribuer définitivement une commande ;
- modifier directement un statut ;
- inventer une localisation ;
- inventer un stock ;
- inventer un prix ;
- créer directement une transaction métier.

---

### Règle 5 — Toute transition critique doit être explicite

Exemple valide :

```text
PREPARING
    ↓
READY_FOR_PICKUP
```

Une transition arbitraire comme :

```text
PREPARING
    ↓
DELIVERED
```

doit être refusée si elle n'est pas autorisée par la machine à états.

---

### Règle 6 — Les opérations critiques sont transactionnelles

Les opérations comme :

```text
accept_store_offer
accept_driver_offer
assign_driver
change_order_state
```

doivent être conçues pour résister :

- aux appels simultanés ;
- aux retries ;
- aux doubles webhooks ;
- aux erreurs partielles.

---

### Règle 7 — Un retry technique ne doit pas rejouer le métier

Un retry Celery ou WhatsApp doit pouvoir être effectué sans créer de doublons.

---

## 32. Machine à états des commandes

La commande possède un état explicite.

Le flow principal prévu est :

```text
DRAFT
  ↓
AWAITING_LOCATION
  ↓
CONFIRMED
  ↓
MATCHING_STORES
  ↓
STORE_OFFERED
  ↓
STORE_ACCEPTED
  ↓
PREPARING
  ↓
READY_FOR_PICKUP
  ↓
MATCHING_DRIVERS
  ↓
DRIVER_ASSIGNED
  ↓
PICKED_UP
  ↓
IN_DELIVERY
  ↓
DELIVERED
```

Des états terminaux ou d'échec existent également :

```text
CANCELLED
FAILED
```

Aucune transition ne doit être effectuée librement par WhatsApp, Celery ou l'IA.

Le domaine `orders` reste responsable de la validation des transitions.

---

## 33. Architecture IA

La couche IA est isolée du métier.

Structure cible :

```text
apps/ai/
├── application/
│   ├── intent_extractor.py
│   ├── order_parser.py
│   ├── recommender.py
│   └── complaint_classifier.py
│
├── contracts/
│   ├── inputs.py
│   └── outputs.py
│
├── providers/
│   ├── base.py
│   ├── openai_provider.py
│   └── mock_provider.py
│
└── prompts/
    ├── order_extraction.py
    └── clarification.py
```

Les réponses IA utilisent des structures strictes.

Exemple conceptuel :

```json
{
  "intent": "CREATE_ORDER",
  "items": [],
  "delivery": {},
  "missing_fields": [],
  "confidence": 0.94
}
```

Django valide toujours la réponse avant son utilisation.

---

## 34. Intégration WhatsApp

Flux entrant :

```text
Meta
  ↓
/webhooks/whatsapp/
  ↓
Validation webhook
  ↓
Normalisation événement
  ↓
Contrôle idempotence
  ↓
Identification acteur
  ↓
Application Service
  ↓
Domaine métier
```

Le module WhatsApp est un **adapter**.

Il ne doit pas contenir :

- la machine à états complète ;
- le matching ;
- les règles d'affectation ;
- les règles métier des commandes.

---

## 35. Matching

### Matching boutique

```text
Commande confirmée
       ↓
Localisation client
       ↓
PostGIS
       ↓
Boutiques proches
       ↓
Filtres métier
       ↓
Scoring
       ↓
StoreOffer
       ↓
Acceptation atomique
```

### Matching chauffeur

```text
READY_FOR_PICKUP
       ↓
Position boutique
       ↓
Redis GEO
       ↓
Chauffeurs proches
       ↓
Suppression positions expirées
       ↓
Validation métier Django
       ↓
DriverOffer
       ↓
Acceptation atomique
       ↓
DRIVER_ASSIGNED
```

---

## 36. Tracking

Le tracking live ne dépend pas directement de WhatsApp.

Flux prévu :

```text
Chauffeur
   ↓
Lien temporaire signé
   ↓
Page mobile
   ↓
GPS navigateur
   ↓
Tracking API
   ↓
Redis GEO
   ↓
Échantillons PostGIS
```

Le client peut recevoir un lien permettant de consulter la position autorisée du chauffeur.

Les tokens de tracking doivent :

- être liés à une livraison précise ;
- avoir une durée de vie limitée ;
- être impossibles à réutiliser pour une autre livraison ;
- être invalidés lorsque le tracking n'est plus autorisé.

---

## 37. Philosophie du projet

Whatassoh est volontairement construit comme un **MVP fonctionnel mais non jetable**.

Le MVP réduit le nombre de fonctionnalités, pas la qualité de l'architecture.

Les priorités sont :

- une architecture compréhensible ;
- des frontières métier explicites ;
- des règles métier isolées ;
- une base transactionnelle fiable ;
- une bonne observabilité ;
- une sécurité restrictive par défaut ;
- des intégrations externes remplaçables ;
- une architecture capable d'évoluer après le MVP.

> **Whatassoh est un backend Django modulaire et transactionnel qui transforme WhatsApp en interface de marketplace. L'IA comprend et assiste, PostGIS conserve la vérité géographique, Redis GEO accélère le temps réel, Celery gère les traitements asynchrones, et chaque décision métier critique reste contrôlée par le backend.**
