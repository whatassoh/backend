# Lot 2 - Inbox ordonnee et outbox conversationnelle

## 1. Objectif livre

Ce lot retire le traitement conversationnel long du chemin critique des webhooks,
sans changer le comportement metier de l'orchestrateur. Il garantit qu'une
conversation est traitee dans son ordre PostgreSQL, meme lorsque Celery livre les
taches plusieurs fois ou dans le desordre.

La chaine cible est :

```text
Webhook signe
  -> normalisation
  -> MessageEvent + ConversationMessage
  -> sequence inbox par conversation
  -> ACK HTTP
  -> drain Celery avec bail et fencing token
  -> orchestrateur existant
  -> mutations metier + intention outbox dans une transaction
  -> livraison canal
  -> ConversationMessage sortant + outbox DELIVERED
```

PostgreSQL est la source de verite. Redis/Celery transporte des identifiants et
peut perdre ou redelivrer une notification sans perdre le travail durable.

## 2. Invariants

1. `(conversation, sequence)` est unique dans l'inbox et l'outbox.
2. L'allocation de sequence verrouille la ligne `Conversation`.
3. Un tour ne demarre pas tant qu'un tour anterieur n'est pas `PROCESSED`.
4. Une DLQ inbox bloque les tours suivants jusqu'a intervention explicite.
5. Le traitement metier et la creation outbox sont atomiques.
6. Un worker ne peut finaliser qu'avec son `fencing_token` courant.
7. Une sortie ne depasse pas une sortie anterieure non terminale.
8. Un retry de livraison ne rejoue jamais l'orchestrateur metier.
9. La cle `conversation-response:<inbox UUID>` identifie la reponse logique.
10. Une sortie deja presente dans `ConversationMessage` est marquee livree sans
    nouvel appel fournisseur.
11. Un bail `SENDING` expire sans trace locale devient `UNCERTAIN_DELIVERY` en
    DLQ. Il n'est jamais rejoue automatiquement, car le fournisseur a pu accepter
    le message avant le crash local.

## 3. Etats

### Inbox

- `PENDING` : persiste, pret a etre reclame;
- `PROCESSING` : bail actif et token de fencing attribue;
- `FAILED_RETRYABLE` : echec borne, prochaine tentative datee;
- `PROCESSED` : mutations et intention de reponse validees;
- `DEAD_LETTER` : echec permanent ou budget de tentatives epuise.

### Outbox

- `PENDING` : intention de reponse durable;
- `SENDING` : livraison en cours sous bail;
- `FAILED_RETRYABLE` : erreur fournisseur classee temporaire;
- `DELIVERED` : sortie enregistree;
- `DEAD_LETTER` : erreur permanente, budget epuise ou livraison incertaine.

## 4. Reprises et reconciliation

`messaging.reconcile_conversation_pipeline` balaie regulierement :

- les elements prets dont la publication Celery a echoue;
- les echecs temporaires dont `available_at` est atteint;
- les baux expires apres perte d'un worker.

Les taches utilisent `acks_late=True` et `reject_on_worker_lost=True`. Les retries
sont exponentiels et bornes. La concurrence est tranchee en base, jamais par une
supposition sur l'ordre du broker.

L'administration Django expose les deux files en lecture seule. Une inbox DLQ
peut etre remise en attente. Une outbox DLQ peut etre rejouee uniquement si elle
n'est pas marquee `UNCERTAIN_DELIVERY`; ce dernier cas exige une verification
operateur et un choix explicite sur le risque de doublon.

## 5. Activation progressive

Le chemin historique reste actif tant qu'aucun canal n'est configure :

```env
CONVERSATION_INBOX_CHANNELS=
```

Ordre de deploiement obligatoire :

1. deployer le code avec le flag vide;
2. appliquer la migration `messaging.0011_conversation_inbox_outbox`;
3. verifier qu'un worker Celery et un seul Celery Beat utilisent la nouvelle version;
4. verifier les taches enregistrees et la reconciliation;
5. activer `TELEGRAM` en canari;
6. observer ordre, DLQ, latence et doublons;
7. activer ensuite `WHATSAPP`;
8. rollback instantane en retirant le canal de la variable, sans supprimer les tables.

Valeurs possibles :

```env
CONVERSATION_INBOX_CHANNELS=TELEGRAM
CONVERSATION_INBOX_CHANNELS=TELEGRAM,WHATSAPP
```

Changer le flag exige le redemarrage des processus Django et Celery.

## 6. Configuration

| Variable | Defaut | Role |
|---|---:|---|
| `CONVERSATION_INBOX_CHANNELS` | vide | canaux utilisant la chaine durable |
| `CONVERSATION_INBOX_LEASE_SECONDS` | 300 | duree maximale d'un traitement de tour |
| `CONVERSATION_OUTBOX_LEASE_SECONDS` | 120 | duree maximale d'une livraison |
| `CONVERSATION_INBOX_MAX_ATTEMPTS` | 3 | budget de traitement |
| `CONVERSATION_OUTBOX_MAX_ATTEMPTS` | 3 | budget de livraison |
| `CONVERSATION_RETRY_BASE_SECONDS` | 15 | base du backoff exponentiel |
| `CONVERSATION_RETRY_MAX_SECONDS` | 300 | plafond du backoff |
| `CONVERSATION_RECONCILIATION_BATCH_SIZE` | 100 | taille d'un balayage |

Le bail inbox doit rester superieur au timeout maximal du modele. Le bail outbox
doit etre superieur au timeout HTTP maximal du fournisseur.

## 7. Tests de non-regression et de faute

La suite ajoute les preuves suivantes :

- sequences monotones pour deux messages rapides;
- refus de reclamer le second avant le premier;
- rejet de la finalisation par un ancien fencing token;
- commit atomique du tour et de son intention de reponse;
- detection d'une sortie deja enregistree;
- webhook asynchrone court et idempotent;
- absence de second traitement sur retry du meme webhook.

Les tests Django doivent etre executes dans l'environnement PostGIS/GDAL du CI.
Les controles locaux sans dependance systeme sont `ruff`, `compileall` et
`git diff --check`.

## 8. Limite honnete de l'exactly-once externe

Aucune application ne peut garantir un exactly-once physique avec un fournisseur
HTTP qui n'accepte pas de cle d'idempotence. Ce lot garantit l'exactly-once
logique avant l'appel et apres l'enregistrement local. La fenetre residuelle est
le crash entre l'acceptation distante et la trace locale; elle est volontairement
transformee en `UNCERTAIN_DELIVERY`, jamais masquee par un retry automatique.

## 9. Passage vers le lot 3

Le lot 3 peut brancher LangGraph en shadow mode dans
`process_inbound_conversation`. Il recevra un tour deja ordonne, ne pourra pas
executer une seconde mutation et produira une comparaison silencieuse avec
l'orchestrateur courant. Les tables inbox/outbox ne dependent pas de LangGraph.
