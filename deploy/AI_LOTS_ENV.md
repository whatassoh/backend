# Configuration production des lots IA 1 a 5

## Reponse courte sur les embeddings

Avec le fournisseur `openai`, ces deux variables restent volontairement vides:

```dotenv
AI_RAG_EMBEDDING_BASE_URL=
AI_RAG_EMBEDDING_API_KEY=
```

Le SDK utilise directement OpenAI avec `OPENAI_API_KEY`. Il n'existe pas une
deuxieme cle OpenAI a recuperer pour les embeddings.

Ces variables ne sont renseignees que pour un serveur d'embeddings
`openai_compatible` exploite par l'equipe:

- `AI_RAG_EMBEDDING_BASE_URL`: URL `/v1` fournie par ce serveur;
- `AI_RAG_EMBEDDING_API_KEY`: secret configure sur ce serveur, ou vide si le
  service interne n'impose pas d'authentification.

## Pourquoi les flags commencent a False

`False` est un etat de deploiement, pas un oubli. Il permet d'appliquer les
migrations, construire pgvector, charger le corpus et verifier les metriques
avant de modifier une reponse utilisateur. Les trois profils du script font
ensuite evoluer cette configuration de facon explicite.

## Script idempotent

Le script ne charge jamais `.env.production` comme du code shell. Il:

1. verrouille l'operation;
2. cree un backup dans `.deploy-backups`;
3. reconstruit un fichier temporaire;
4. preserve proprietaire et permissions;
5. remplace `.env.production` en une seule operation atomique;
6. ne journalise jamais `OPENAI_API_KEY`.

Sur le serveur:

```bash
cd /opt/whatAssoh/backend
chmod +x deploy/configure-ai-lots.sh
./deploy/configure-ai-lots.sh baseline
./deploy/configure-ai-lots.sh shadow WHATSAPP
./deploy/configure-ai-lots.sh active WHATSAPP 10
```

Le script reutilise `OPENAI_API_KEY` et `AI_MODEL` deja presents. S'ils sont
absents, il les demande interactivement. Pour une execution non interactive:

```bash
export OPENAI_API_KEY='secret'
export WHATASSOH_AI_MODEL='modele-autorise-par-le-compte'
./deploy/configure-ai-lots.sh shadow WHATSAPP
unset OPENAI_API_KEY WHATASSOH_AI_MODEL
```

Eviter de placer le secret directement dans les arguments du script ou dans
l'historique shell.

## Profils

### `baseline`

- active le moteur IA historique OpenAI;
- conserve le lot 1 interruptible;
- laisse inbox durable, agent, memoire, composeur et RAG desactives;
- permet le deploiement des migrations sans changer les parcours.

### `shadow`

- active l'inbox/outbox pour les canaux demandes;
- execute LangGraph en observation uniquement;
- active style, memoire gouvernee et resumes;
- execute le composeur sans montrer sa proposition;
- execute le RAG hybride sans montrer sa reponse;
- conserve tous les outils agentiques et outils d'ecriture desactives.

Le shadow consomme des appels modele. Il ne modifie pas la reponse envoyee par
le composeur ou le RAG, mais la memoire structuree et les traces shadow sont
reellement persistees.

### `active`

- conserve LangGraph en shadow, car le lot 3 n'a pas de noeud d'execution actif;
- active le composeur naturel;
- active le RAG hybride pour une cohorte stable;
- utilise le troisieme argument comme pourcentage de rollout, `10` par defaut;
- maintient les outils agentiques et mutations IA a `False`.

Montee recommandee du RAG: `1`, `10`, `25`, `50`, puis `100` apres validation
des metriques et conversations reelles.

## Socle IA

| Variable | Valeur script | Role |
|---|---:|---|
| `AI_ENABLED` | `True` | Autorise le moteur de comprehension et les appels modele |
| `AI_PROVIDER` | `openai` | Selectionne l'adaptateur OpenAI |
| `AI_MODEL` | conserve/demande | Modele conversationnel autorise par le compte |
| `AI_TIMEOUT_SECONDS` | `15` | Timeout d'un appel modele |
| `OPENAI_API_KEY` | conserve/demandee | Secret commun au chat et aux embeddings OpenAI |
| `AI_EMERGENCY_STOP` | `False` | Coupe chat, resumes, composeur, RAG et embeddings si mis a `True` |
| `AI_DECISION_OBSERVABILITY_ENABLED` | `True` | Metriques de decision sans texte utilisateur |
| `AI_VOICE_ENABLED` | `False` | Voix non activee dans les lots 1 a 5 |

## Lot 1

Le lot 1 n'a aucun flag. Les interruptions, salutations, pause, reprise, retour
et changement de sujet sont toujours disponibles dans le code deploye.
La comprehension contextuelle depend cependant de `AI_ENABLED=True`.

## Lot 2: inbox/outbox ordonnee

| Variable | Valeur | Role |
|---|---:|---|
| `CONVERSATION_INBOX_CHANNELS` | profil/canaux | Canaux sur le pipeline durable |
| `CONVERSATION_INBOX_LEASE_SECONDS` | `300` | Bail maximal du traitement entrant |
| `CONVERSATION_OUTBOX_LEASE_SECONDS` | `120` | Bail maximal d'un envoi sortant |
| `CONVERSATION_INBOX_MAX_ATTEMPTS` | `3` | Tentatives avant DLQ entrante |
| `CONVERSATION_OUTBOX_MAX_ATTEMPTS` | `3` | Tentatives avant DLQ sortante |
| `CONVERSATION_RETRY_BASE_SECONDS` | `15` | Base du backoff |
| `CONVERSATION_RETRY_MAX_SECONDS` | `300` | Plafond du backoff |
| `CONVERSATION_RECONCILIATION_BATCH_SIZE` | `100` | Taille de reconciliation |

Valeurs de canaux valides: vide, `WHATSAPP`, `TELEGRAM` ou
`WHATSAPP,TELEGRAM`.

## Lot 3: LangGraph et outils

| Variable | Active | Role |
|---|---:|---|
| `AI_AGENT_ENABLED` | `False` | Reste coupe tant que l'agent actif n'est pas livre |
| `AI_AGENT_SHADOW_ENABLED` | `True` | Compare le graphe sans changer la reponse |
| `AI_AGENT_TOOLS_ENABLED` | `False` | Interdit l'execution agentique des outils |
| `AI_AGENT_WRITE_TOOLS_ENABLED` | `False` | Interdit toute mutation proposee par l'agent |
| `AI_AGENT_MEMORY_ENABLED` | `False` | Memoire du futur agent actif |
| `AI_AGENT_MAX_PLAN_STEPS` | `4` | Nombre maximal d'etapes planifiees |
| `AI_AGENT_SHADOW_CACHE_SECONDS` | `300` | Cache reconstructible des decisions shadow |
| `AI_TOOL_MANIFEST_CACHE_SECONDS` | `300` | Cache du manifeste d'outils |
| `AI_TOOL_MAX_RESULT_BYTES` | `65536` | Taille maximale d'un resultat d'outil |
| `MESSAGING_CONFIGURATION_CACHE_SECONDS` | `60` | Cache du statut des canaux |

## Lot 4: style, memoire et composeur

| Variable | Shadow | Active | Role |
|---|---:|---:|---|
| `AI_STYLE_ADAPTATION_ENABLED` | `True` | `True` | Observe la facon d'ecrire |
| `AI_CONVERSATION_MEMORY_ENABLED` | `True` | `True` | Memoire explicite gouvernee |
| `AI_MEMORY_SUMMARY_ENABLED` | `True` | `True` | Resumes incrementaux |
| `AI_NATURAL_COMPOSER_SHADOW_ENABLED` | `True` | `False` | Compare une formulation naturelle |
| `AI_NATURAL_COMPOSER_ENABLED` | `False` | `True` | Envoie la formulation composee |
| `AI_MEMORY_SUMMARY_MIN_MESSAGES` | `8` | `8` | Seuil avant resume |
| `AI_MEMORY_SUMMARY_BATCH_SIZE` | `24` | `24` | Messages traites par lot |
| `AI_MEMORY_FACT_TTL_DAYS` | `365` | `365` | Retention des faits memorises |
| `AI_MEMORY_RECONCILIATION_BATCH_SIZE` | `100` | `100` | Reprise des resumes perdus |

## Lot 5: RAG hybride

| Variable | Valeur | Role |
|---|---:|---|
| `AI_RAG_ENABLED` | profil | Rend la reponse RAG visible |
| `AI_RAG_SHADOW_ENABLED` | profil | Mesure le RAG sans changer la reponse |
| `AI_RAG_ROLLOUT_PERCENT` | `0..100` | Cohorte stable des reponses actives |
| `AI_RAG_SEMANTIC_ENABLED` | profil | Ajoute les embeddings a FTS et pg_trgm |
| `AI_RAG_EMBEDDING_PROVIDER` | `openai` | `openai` ou `openai_compatible` |
| `AI_RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | Modele vectoriel |
| `AI_RAG_EMBEDDING_DIMENSIONS` | `1024` | Dimension imposee par `vector(1024)` |
| `AI_RAG_EMBEDDING_BASE_URL` | vide avec OpenAI | Endpoint du service compatible local |
| `AI_RAG_EMBEDDING_API_KEY` | vide avec OpenAI | Secret du service compatible local |
| `AI_RAG_EMBEDDING_SEND_DIMENSIONS` | `True` | Demande explicitement 1024 dimensions |
| `AI_RAG_MAX_QUERY_CHARACTERS` | `1200` | Taille maximale d'une question |
| `AI_RAG_MAX_SOURCE_CHARACTERS` | `500000` | Taille maximale d'une source |
| `AI_RAG_MAX_METADATA_CHARACTERS` | `20000` | Taille maximale des metadonnees |
| `AI_RAG_CHUNK_TARGET_CHARACTERS` | `1200` | Taille cible d'un chunk |
| `AI_RAG_CHUNK_MAX_CHARACTERS` | `1800` | Limite dure d'un chunk |
| `AI_RAG_CHUNK_OVERLAP_CHARACTERS` | `160` | Recouvrement de contexte |
| `AI_RAG_MAX_RESULTS` | `6` | Chunks finaux |
| `AI_RAG_CANDIDATE_LIMIT` | `24` | Candidats avant fusion |
| `AI_RAG_MAX_CONTEXT_CHARACTERS` | `12000` | Preuves maximales envoyees au modele |
| `AI_RAG_MAX_CHUNKS_PER_SOURCE` | `2` | Diversite des sources |
| `AI_RAG_MIN_LEXICAL_SCORE` | `0.02` | Seuil full-text |
| `AI_RAG_MIN_TRIGRAM_SCORE` | `0.12` | Seuil tolerance aux fautes |
| `AI_RAG_MIN_SEMANTIC_SCORE` | `0.45` | Seuil vectoriel |
| `AI_RAG_MAX_CHUNK_INJECTION_RISK` | `0.45` | Risque maximal d'un chunk |
| `AI_RAG_RRF_RANK_CONSTANT` | `60` | Constante de fusion RRF |
| `AI_RAG_LEXICAL_WEIGHT` | `0.55` | Poids lexical |
| `AI_RAG_SEMANTIC_WEIGHT` | `0.45` | Poids semantique |
| `AI_RAG_CACHE_SECONDS` | `300` | TTL des UUID candidats dans Redis |
| `AI_RAG_EMBEDDING_BATCH_SIZE` | `32` | Taille d'un lot Celery |
| `AI_RAG_EMBEDDING_MAX_ATTEMPTS` | `5` | Tentatives maximales |
| `AI_RAG_EMBEDDING_LEASE_SECONDS` | `900` | Bail d'indexation |
| `AI_RAG_AUDIT_ENABLED` | `True` | Audits sans texte utilisateur |
| `AI_RAG_AUDIT_RETENTION_DAYS` | `30` | Retention des audits |

## Application et verification

Le script modifie uniquement le fichier. Appliquer ensuite la configuration par
le pipeline de deploiement ou recreer `web`, `worker` et `beat` avec Compose.

Apres le premier deploiement des lots:

```bash
docker-compose -p whatassoh --env-file .env.production \
  -f docker-compose.prod.yml run --rm web python manage.py setup_agent_checkpoints

docker-compose -p whatassoh --env-file .env.production \
  -f docker-compose.prod.yml run --rm web python manage.py knowledge_status
```

Apres activation semantique et ingestion du corpus:

```bash
docker-compose -p whatassoh --env-file .env.production \
  -f docker-compose.prod.yml run --rm web \
  python manage.py knowledge_reindex --embeddings --reset-failed
```

Pour couper uniquement les fonctions avancees, repasser en `baseline`. Pour un
incident IA global, mettre `AI_EMERGENCY_STOP=True` puis recreer `web`, `worker`
et `beat`.
