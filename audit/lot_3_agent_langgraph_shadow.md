# Lot 3 - Agent LangGraph en shadow mode

## 1. But et limite de securite

Le lot 3 observe ce qu'un agent borne aurait decide, sans remplacer
l'orchestrateur actuel et sans executer une seconde action metier.

```text
message ordonne
  -> orchestrateur historique (seul chemin autorise a agir/repondre)
  -> classifieur shadow
  -> LangGraph route -> plan -> policy
  -> validation allowlist/budget
  -> comparaison silencieuse
  -> checkpoint Postgres + AgentShadowRun
```

Une panne du shadow est journalisee et persistee avec `status=FAILED`. Elle ne
modifie jamais le `ConversationalOutcome`, l'outbox ou la reponse utilisateur.

## 2. Etat du graphe

`AgentGraphState` est type et borne. Il contient uniquement : references du
tour, contexte actif compact, intention structuree, entites extraites bornees,
route, plan, risque, confirmation et decision de politique.

Il exclut les objets ORM, clients, secrets, payloads fournisseur et message brut.
`thread_id` est l'UUID de conversation. Le namespace de checkpoint est
`shadow-v1`.

## 3. Graphe borne

Le graphe comporte trois noeuds lisibles :

1. `route` choisit social, interruption, achat, vente, livraison, support ou inconnu;
2. `plan` propose au maximum `AI_AGENT_MAX_PLAN_STEPS` appels allowlistes;
3. `policy` calcule le risque et force `SHADOW_ONLY`.

Apres le graphe, une validation applicative rejette tout outil inconnu, tout
depassement de budget et toute politique autre que `SHADOW_ONLY`. Aucun noeud
d'execution d'outil n'existe dans ce lot.

## 4. PostgresSaver officiel

Les dependances sont epinglees :

```text
langgraph==1.2.12
langgraph-checkpoint-postgres==3.1.2
```

Elles ont ete ajoutees au manifeste mais pas installees localement. Apres
installation par le pipeline de build, initialiser une fois les tables officielles :

```bash
python manage.py migrate
python manage.py setup_agent_checkpoints
```

La commande construit l'URI depuis `DATABASES["default"]` et ne la journalise
jamais. `PostgresSaver.setup()` est une operation de deploiement, pas une action
executee a chaque message.

## 5. Registre d'outils

Chaque `ToolSpec` declare : nom, version, description, schema JSON, risque,
permission, confirmation, timeout, retry et champs a masquer.

Les lectures implementees reutilisent les selecteurs existants : tache courante,
recherche produit, commande active, liste des commandes, boutiques propres et
profil livreur propre. Les mutations sont declarees pour permettre la mesure du
plan, mais ne possedent aucun chemin executable dans le shadow.

L'executant central applique dans cet ordre :

1. outil present dans l'allowlist;
2. validation JSON Schema;
3. interdiction des risques autres que R1 en shadow;
4. confirmation obligatoire selon le niveau;
5. presence d'un adaptateur explicitement implemente;
6. execution avec utilisateur et conversation injectes par le serveur.

Le modele ne choisit jamais l'identite authentifiee.

## 6. Niveaux de risque

- `R0` : dialogue sans outil;
- `R1` : lecture propre ou publique autorisee;
- `R2` : brouillon ou mutation reversible;
- `R3` : action sensible avec confirmation explicite;
- `R4` : interdite ou validation humaine.

`cancel_task`, `confirm_order`, `cancel_order`, catalogue publie et incident de
livraison sont au minimum R3. Le shadow peut les proposer, jamais les executer.

## 7. Redis

Redis est un accelerateur fail-open, pas une source de verite.

Caches introduits :

- manifeste du registre d'outils;
- decision shadow reconstructible, indexee par hash de version/conversation/tour/contexte;
- statut de configuration WhatsApp/Telegram pour les vues operationnelles.

Ne sont jamais caches comme autorite : permissions, confirmations, commandes,
stocks, prix, etat inbox/outbox, checkpoints ou succes d'une livraison.

Une panne Redis produit un log sans contenu utilisateur puis poursuit via
PostgreSQL et le code local. Une decision relue depuis Redis repasse par le budget,
l'allowlist et la politique `SHADOW_ONLY`.

Le backend `django-redis` utilise explicitement `JSONSerializer`. Aucun cache de
ce lot ne repose sur `pickle` ou sur la deserialisation d'objets Python arbitraires.

## 8. Persistance de comparaison

`AgentShadowRun` conserve une seule ligne par conversation/correlation :

- version du graphe;
- statut;
- route;
- noms des outils proposes;
- risque;
- parite avec la route historique;
- snapshot borne;
- latence et categorie d'erreur.

Le texte brut, les secrets et les arguments rediges ne sont pas journalises.
Django Admin est strictement en lecture seule pour ces traces.

## 9. Activation et rollback

Les flags restent desactives par defaut :

```env
AI_AGENT_ENABLED=False
AI_AGENT_SHADOW_ENABLED=False
AI_AGENT_TOOLS_ENABLED=False
AI_AGENT_WRITE_TOOLS_ENABLED=False
```

Ordre de canari :

1. installer les dependances epinglees dans l'image;
2. appliquer les migrations Django;
3. executer `setup_agent_checkpoints`;
4. garder `AI_AGENT_ENABLED=False` et `AI_AGENT_SHADOW_ENABLED=False` pour smoke test;
5. activer seulement `AI_AGENT_SHADOW_ENABLED=True` avec `AI_ENABLED=True`;
6. laisser `AI_AGENT_ENABLED`, `AI_AGENT_TOOLS_ENABLED` et les write tools a `False`;
7. mesurer parite, erreurs, latence et cout;
8. rollback immediat avec `AI_AGENT_SHADOW_ENABLED=False`.

Le shadow ne doit pas etre active avant la validation CI de l'import LangGraph et
de la connexion `PostgresSaver`.

## 10. Criteres de passage

- aucun appel d'outil mutateur en shadow;
- aucune difference de reponse utilisateur avec shadow active/desactive;
- une panne LangGraph/Postgres/Redis ne casse pas le tour principal;
- aucune duplication par retry du meme `correlation_id`;
- plans toujours bornes et allowlistes;
- parite mesuree par parcours, pas seulement en moyenne;
- latence et cout shadow acceptables;
- tests inbox/outbox du lot 2 toujours verts.

Le lot 4 ne devra promouvoir aucune execution agentique. Il ajoutera d'abord le
composeur naturel et la memoire gouvernee, toujours derriere des flags distincts.
