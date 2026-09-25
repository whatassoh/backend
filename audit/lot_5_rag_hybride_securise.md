# Lot 5 - RAG hybride, securise et exploitable

## Statut

Le lot 5 implemente un canal de connaissance statique pour les questions d'aide.
Il ne remplace jamais les tables metier. Les prix, stocks, commandes, permissions,
positions et statuts temps reel continuent d'etre lus par les services et outils
metier autorises.

Le RAG est desactive par defaut. Son activation est progressive, reversible et
independante de l'indexation semantique.

## Invariants non negociables

1. Une autorisation est calculee par le serveur avant la recherche, jamais par le modele.
2. Un chunk non autorise ne participe ni au classement lexical ni au classement vectoriel.
3. Redis ne contient que des identifiants de candidats, jamais le texte des documents.
4. Un resultat venant du cache est reautorise et recharge depuis PostgreSQL.
5. Le contenu recupere est une donnee non fiable, jamais une instruction.
6. Chaque affirmation generee cite un chunk recupere et un extrait reel de ce chunk.
7. Une citation, URL, version et source affichees sont reconstruites par le serveur.
8. Une reponse insuffisamment fondee s'abstient; elle n'invente pas.
9. Une panne Redis, embeddings ou modele ne bloque pas le parcours conversationnel.
10. Le RAG ne peut appeler aucun outil et ne peut modifier aucune donnee metier.
11. Les sources externes sont mises en quarantaine jusqu'a une nouvelle ingestion revue.
12. Les secrets detectes ne sont jamais persistes dans le corpus.

## Architecture livree

### Ingestion

`knowledge_ingest` accepte un objet JSON ou une liste. Le service:

1. normalise Unicode, fins de ligne, espaces et caracteres invisibles;
2. valide namespace, cle, version, langue, audience, sensibilite et dates;
3. interdit les donnees metier dynamiques declarees;
4. impose des URL canoniques HTTPS sans identifiants;
5. detecte secrets, PII non revue, HTML cache, injections directes, indirectes,
   encodees en base64 et quelques variantes typoglycemiques;
6. rejette un secret avant toute ecriture;
7. met en quarantaine le contenu externe ou suspect;
8. decoupe deterministement par structure, paragraphes et phrases;
9. conserve hash, section, taille et provenance de chaque chunk;
10. construit le `tsvector` pondere dans PostgreSQL;
11. invalide la revision Redis seulement apres commit;
12. programme les embeddings seulement apres commit et uniquement si actives.

Un fichier est ingere dans une transaction unique: une entree invalide ou
`--fail-on-quarantine` annule tout le lot au lieu de laisser un corpus partiel.

Une nouvelle version active desactive les anciennes versions de la meme cle dans
le meme namespace. Une version en quarantaine ne remplace pas silencieusement une
version active anterieure.

### Autorisations

Les audiences disponibles sont:

- `PUBLIC`: visible sans identite;
- `AUTHENTICATED`: visible par tout utilisateur authentifie;
- `RESTRICTED`: exige un grant `USER`, `ROLE` ou `STORE`;
- `INTERNAL`: exige un membre du staff et un contexte interne explicite.

Une sensibilite `INTERNAL` ou `CONFIDENTIAL` exige une audience `RESTRICTED` ou
`INTERNAL`; cette regle existe dans l'ingestion et dans une contrainte SQL.

Les roles et appartenances boutique sont derives de la base. Le modele ne fournit
aucun identifiant d'autorisation. L'empreinte ACL utilise HMAC-SHA256 avec
`DJANGO_SECRET_KEY`; elle n'expose pas l'identite dans les cles Redis ou les audits.

### Recherche hybride

Le moteur applique dans cet ordre:

1. namespace, statut actif, langue et fenetre de validite;
2. audience et grants;
3. seuil maximal de risque d'injection;
4. recherche plein texte PostgreSQL avec configuration francaise ou anglaise;
5. repli `pg_trgm` pour les fautes et formulations proches;
6. recherche cosine pgvector si elle est disponible;
7. fusion Reciprocal Rank Fusion avec poids configurables;
8. limite de chunks par source pour eviter qu'un document monopolise le contexte;
9. cache d'identifiants avec TTL, revision de corpus et reautorisation au chargement.

Le volume de candidats est borne par `AI_RAG_CANDIDATE_LIMIT`; il doit rester
superieur ou egal au nombre final `AI_RAG_MAX_RESULTS`.

Si pgvector, le fournisseur d'embeddings ou Redis tombe, la recherche lexicale
reste disponible. Si PostgreSQL ne trouve rien de pertinent, le systeme s'abstient.

### Embeddings

Deux adaptateurs sont supportes sans ajouter une nouvelle bibliotheque Python:

- `openai`: endpoint officiel OpenAI;
- `openai_compatible`: endpoint local ou open source compatible avec l'API
  embeddings, par exemple un service separe gere par l'equipe.

La colonne physique est `vector(1024)` avec index HNSW cosine. Le system check
refuse une dimension differente. Les chunks portent modele, hash, etat, compteur
d'essais et categorie d'erreur. Les workers reclament les lots avec
`select_for_update(skip_locked=True)`. Un lease `INDEXING` expire est remis en
echec retryable par la reconciliation.

Un fournisseur externe ne recoit pas une source `INTERNAL` ou `CONFIDENTIAL`
sauf consentement documentaire explicite `external_embedding_allowed=true`.

### Generation et citations

Le modele recoit un JSON de preuves borne. Le schema strict retourne des claims
atomiques, chacun avec des IDs de chunks et une courte citation textuelle. Le
serveur verifie:

- tous les IDs appartiennent au resultat autorise;
- l'extrait cite existe reellement dans au moins un chunk cite;
- le claim et l'extrait ont un recouvrement lexical significatif;
- aucun lien, prompt, script ou instruction interdite n'apparait dans le claim;
- aucun fait dynamique n'est reintroduit par la reponse.

Les numeros, titres, versions et URL de sources sont ajoutes apres validation. Le
modele ne choisit donc jamais une URL libre ni une source hors contexte.

### Cache et concurrence

La cle de cache incorpore namespace, langue, hash HMAC de requete, empreinte ACL,
limite et revision de corpus. L'entree ne contient que des UUID de chunks. Toute
ingestion ou retraite incremente la revision apres commit. Cette approche borne
la stale data par le TTL tout en garantissant une nouvelle verification ACL.

L'indexation est idempotente sur `(chunk, modele, content_hash)`. Les appels
externes sont realises hors transaction longue. Les echecs utilisent backoff et
jitter Celery, puis la reconciliation reprend les lots admissibles.

### Observabilite et retention

Les metriques Prometheus couvrent strategie, hit cache, issue, latence, ingestion
et embeddings avec labels de faible cardinalite. `KnowledgeRetrievalAudit` garde
uniquement hashes HMAC, IDs, strategie, compteurs, latence et resultat de grounding.
Le texte utilisateur n'est pas journalise. Les audits expirent apres 30 jours par
defaut.

## Commandes d'exploitation

Etat du corpus:

```bash
python manage.py knowledge_status
```

Ingestion stricte:

```bash
python manage.py knowledge_ingest \
  --file /chemin/sources.json \
  --fail-on-quarantine
```

Reconstruction lexicale, puis semantique si activee:

```bash
python manage.py knowledge_reindex --namespace customer-help
python manage.py knowledge_reindex --namespace customer-help --embeddings --reset-failed
```

Retraite sans perte de lineage:

```bash
python manage.py knowledge_retire annulation --namespace customer-help --version 2
```

Evaluation de retrieval:

```bash
python manage.py knowledge_evaluate \
  --file /chemin/rag-evaluation.json \
  --min-hit-rate 0.90 \
  --min-mrr 0.80
```

Le gate echoue si le hit rate ou MRR descend sous le seuil, ou si une source
interdite apparait une seule fois.

## Format d'une source

```json
{
  "namespace": "customer-help",
  "key": "annulation",
  "title": "Annuler une commande",
  "source_type": "GUIDE",
  "language": "fr",
  "version": 2,
  "audience": "PUBLIC",
  "trust_level": "REVIEWED",
  "sensitivity": "PUBLIC",
  "canonical_url": "https://whatasso.dvybes.com/aide/annulation",
  "content": "Contenu valide et approuve.",
  "metadata": {
    "owner": "operations",
    "reviewed_by": "support-lead"
  }
}
```

Pour `RESTRICTED`, ajouter par exemple:

```json
{
  "grants": [
    {"principal_type": "ROLE", "principal_id": "STORE_OWNER"}
  ]
}
```

## Activation production

### Phase 0 - schema seulement

Deployer avec toutes les fonctions RAG desactivees. La nouvelle image PostgreSQL
conserve PostGIS et ajoute pgvector. Le script cree et verifie PostGIS, `pg_trgm`
et pgvector, fait le backup, puis applique la migration transactionnelle.

```dotenv
AI_RAG_ENABLED=False
AI_RAG_SHADOW_ENABLED=False
AI_RAG_ROLLOUT_PERCENT=0
AI_RAG_SEMANTIC_ENABLED=False
```

### Phase 1 - corpus lexical

Ingerer les sources, executer `knowledge_status`, les tests adversariaux et le
dataset d'evaluation. Corriger toute quarantaine au lieu de la contourner.

### Phase 2 - shadow

```dotenv
AI_RAG_SHADOW_ENABLED=True
AI_RAG_ENABLED=False
AI_RAG_ROLLOUT_PERCENT=0
```

Comparer latence, abstention, grounding et citations sans modifier les reponses.

### Phase 3 - semantique

Pour OpenAI:

```dotenv
AI_RAG_SEMANTIC_ENABLED=True
AI_RAG_EMBEDDING_PROVIDER=openai
AI_RAG_EMBEDDING_MODEL=text-embedding-3-small
AI_RAG_EMBEDDING_DIMENSIONS=1024
AI_RAG_EMBEDDING_SEND_DIMENSIONS=True
```

Pour un endpoint local compatible:

```dotenv
AI_RAG_SEMANTIC_ENABLED=True
AI_RAG_EMBEDDING_PROVIDER=openai_compatible
AI_RAG_EMBEDDING_BASE_URL=http://embedding-service:8000/v1
AI_RAG_EMBEDDING_API_KEY=local-service-secret
AI_RAG_EMBEDDING_MODEL=modele-local-1024
AI_RAG_EMBEDDING_SEND_DIMENSIONS=False
```

Reindexer, puis repasser le gate d'evaluation.

### Phase 4 - reponses actives

```dotenv
AI_RAG_ENABLED=True
AI_RAG_SHADOW_ENABLED=False
AI_RAG_ROLLOUT_PERCENT=1
```

Monter ensuite a 10, 25, 50 puis 100 uniquement si les seuils restent bons. Le
bucket HMAC est stable: un utilisateur ne change pas de cohorte entre deux messages.

### Arret immediat

`AI_EMERGENCY_STOP=True` bloque tous les appels modele. Pour couper seulement le
RAG, utiliser `AI_RAG_ENABLED=False`, `AI_RAG_SHADOW_ENABLED=False` et redemarrer
web/worker/beat. La recherche et les donnees metier deterministes restent actives.

## Tests et gates

- chunking deterministe et borne;
- secret non persiste;
- quarantaine des sources externes/injectees;
- corpus adversarial de 20 charges;
- aucune fuite entre utilisateurs restreints;
- contexte interne en double opt-in;
- citations hors retrieval refusees;
- extrait invente refuse;
- verite metier dynamique exclue du RAG;
- hit rate, recall, MRR, abstention et fuite interdite;
- configuration refusee par les system checks si incoherente;
- migration et image Compose validees avant activation.

## Limites assumees

- Aucun detecteur d'injection n'est parfait. La securite repose sur plusieurs
  couches: quarantaine, ACL, contexte non fiable, schema strict, citations,
  absence d'outils et abstention.
- Le classement doit etre calibre avec des questions reelles anonymisees. Les
  seuils livres sont conservateurs, pas une preuve universelle de pertinence.
- Le corpus doit avoir un responsable, une date de revue et une politique de
  retrait. Le code ne peut pas remplacer cette gouvernance.
- Les changements de dimension vectorielle exigent une nouvelle colonne/migration,
  un reindex complet et un nouveau gate; le system check bloque la derive silencieuse.

## Sources techniques consultees

Les choix ci-dessus s'appuient principalement sur des documentations officielles:

1. [PostgreSQL - introduction au full-text search](https://www.postgresql.org/docs/17/textsearch-intro.html)
2. [PostgreSQL - tables et index textuels](https://www.postgresql.org/docs/17/textsearch-tables.html)
3. [PostgreSQL - controle et ranking des recherches](https://www.postgresql.org/docs/17/textsearch-controls.html)
4. [PostgreSQL - index GIN recommandes](https://www.postgresql.org/docs/17/textsearch-indexes.html)
5. [PostgreSQL - module pg_trgm](https://www.postgresql.org/docs/17/pgtrgm.html)
6. [PostgreSQL - Row Security Policies](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)
7. [PostgreSQL - CREATE INDEX](https://www.postgresql.org/docs/17/sql-createindex.html)
8. [pgvector - recherche vectorielle, HNSW, filtrage et hybrid search](https://github.com/pgvector/pgvector)
9. [pgvector - Dockerfile officiel](https://github.com/pgvector/pgvector/blob/master/Dockerfile)
10. [pgvector-python - integration Django de reference](https://github.com/pgvector/pgvector-python)
11. [Django 6 - recherche plein texte PostgreSQL](https://docs.djangoproject.com/en/6.0/ref/contrib/postgres/search/)
12. [Django 6 - migrations](https://docs.djangoproject.com/en/6.0/topics/migrations/)
13. [Django - transactions et on_commit](https://docs.djangoproject.com/en/dev/topics/db/transactions/)
14. [Django 6 - cache](https://docs.djangoproject.com/en/6.0/topics/cache/)
15. [Django 6 - system checks](https://docs.djangoproject.com/en/6.0/topics/checks/)
16. [Django - contraintes de modeles](https://docs.djangoproject.com/en/dev/ref/models/constraints/)
17. [Celery - retry, backoff et jitter](https://docs.celeryq.dev/en/latest/userguide/tasks.html)
18. [Redis - cache-aside et invalidation](https://redis.io/docs/latest/develop/use-cases/cache-aside/)
19. [Redis - client-side cache et invalidation](https://redis.io/docs/latest/develop/clients/client-side-caching/)
20. [OpenAI - embeddings](https://developers.openai.com/api/docs/guides/embeddings)
21. [OpenAI - controles de donnees](https://developers.openai.com/api/docs/guides/your-data)
22. [OpenAI - bonnes pratiques d'evaluation](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
23. [OWASP - Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
24. [OWASP - LLM01 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
25. [OWASP Top 10 LLM 2025 - vector and embedding weaknesses](https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/)
26. [NIST AI RMF - Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
27. [MITRE SAFE-AI / ATLAS - Prompt Injection AML.T0051](https://atlas.mitre.org/pdf-files/SAFEAI_Full_Report.pdf)
28. [Unicode UTS #39 - mecanismes de securite Unicode](https://unicode.org/reports/tr39/)

Ces references ne justifient pas une confiance aveugle dans le RAG. Elles
justifient au contraire les controles explicites, la mesure continue, le moindre
privilege et le deploiement graduel retenus ici.
