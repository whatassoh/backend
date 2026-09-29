# Architecture cible de l'IA conversationnelle WhatAssoh

> **Statut : proposition d'architecture complete, avant implementation**  
> **Date : 24 septembre 2026**  
> **Portee : WhatsApp, Telegram, texte, voix, memoire, outils metier, RAG, securite, concurrence, observabilite et evaluation.**  
> Ce document ne modifie aucun comportement de production. Il definit la cible et les garde-fous qui devront guider les lots d'implementation.

## 1. Decision executive

WhatAssoh ne doit pas devenir un chatbot qui improvise les operations metier. Il doit devenir un **agent conversationnel outille** : le modele comprend, planifie et formule; Django autorise, valide et execute; PostgreSQL conserve la verite; Celery ordonne les travaux asynchrones; LangGraph porte l'orchestration durable et les reprises.

La cible retenue est donc :

```text
Canal -> Inbox durable -> file ordonnee par conversation -> agent LangGraph
      -> contexte + memoires autorisees + RAG -> plan d'outils types
      -> politique/confirmation -> services Django existants
      -> composeur naturel -> Outbox durable -> canal
```

Principes non negociables :

1. Le LLM n'ecrit jamais directement dans l'ORM et ne fabrique jamais d'identifiant metier.
2. Une commande sensible n'est executee qu'avec un outil type, autorise, idempotent et, si necessaire, confirme par l'utilisateur.
3. Les messages d'une meme conversation sont traites dans l'ordre. Un deuxieme message reste en attente tant que le premier n'a pas atteint un point terminal ou interruptible.
4. Le dialogue social, le sujet courant et la tache metier sont des dimensions distinctes. Dire bonjour ne valide, n'annule et ne remplit aucun champ.
5. Le bot peut changer de sujet, suspendre une tache, repondre, puis proposer de la reprendre sans perdre les donnees deja valides.
6. PostgreSQL demeure la source de verite. La memoire IA ne remplace ni les commandes, ni les boutiques, ni les produits, ni les autorisations.
7. Toute sortie externe, tout document RAG et toute transcription sont des **donnees non fiables**, jamais des instructions.
8. Le deploiement se fait par ajouts compatibles, feature flags, shadow mode, canari, mesures et rollback.

## 2. Diagnostic de l'existant

### 2.1 Fondations deja solides

- `apps/ai/providers/openai.py` isole correctement la Responses API et utilise un schema JSON strict avec `store=False`.
- `MessageUnderstandingService` produit des faits types et ne mute pas l'ORM.
- Les services de commandes, catalogues, boutiques et livreurs gardent les mutations deterministes et utilisent deja des transactions/verrous.
- `ConversationMessage` conserve l'historique des tours et `Conversation.context` conserve un etat durable.
- L'idempotence fournisseur existe autour des evenements de messagerie.
- Celery, Redis, les taches `acks_late`, les journaux structures, Sentry et Prometheus sont deja presents.
- Le RAG actuel separe ingestion, retrieval et reponse fondee; il traite deja le texte recupere comme non fiable et valide les citations.

Ces elements doivent etre etendus, pas remplaces en bloc.

### 2.2 Causes exactes du dialogue bloque

Dans `ConversationalOrchestrator._process`, les gestionnaires d'inscription vendeur, livreur, catalogue et certaines attentes sont executes avant la comprehension generale. A l'etape `LOCATION`, `SellerOnboardingService` accepte une position native et reaffiche la demande pour tout texte. Ainsi, `annuler`, `bonjour` ou un changement complet de sujet peuvent etre absorbes comme une mauvaise reponse au champ attendu.

`_try_universal_interrupt` ne considere pas toutes les taches actives : l'inscription vendeur/livreur et `active_journey` ne sont pas toutes representees par `_has_pending_task`. Meme quand `CANCEL` est compris, la logique actuelle vise surtout la commande active et non la tache conversationnelle courante.

Enfin, l'appel principal a `understand_normalized` n'injecte pas le contexte limite disponible. Le modele voit souvent un message isole. L'IA est donc essentiellement un extracteur JSON ponctuel, puis une longue priorite de `if` decide de la reponse.

### 2.3 Pourquoi le message generique remonte

`ConversationalOrchestrator.process_message` intercepte toute `AIError` et retourne :

> Je n'arrive pas a traiter votre demande pour le moment. Reessayez.

Ce texte melange authentification, quota, timeout, panne temporaire, reponse invalide et probleme de schema. Il ne preserve ni l'intention deja comprise, ni la tache, ni une reprise automatique. L'utilisateur voit donc une panne opaque alors que plusieurs categories peuvent etre gerees differemment.

### 2.4 RAG et voix aujourd'hui

Le RAG utilise PostgreSQL Full Text Search en francais, ou `icontains` hors PostgreSQL. Il ne possede pas encore d'embeddings, de recherche hybride, de filtrage d'acces riche, de reranking ni de politique de fraicheur.

WhatsApp et Telegram classent deja les messages audio, mais les adaptateurs ne normalisent pas encore l'identifiant du media, le MIME type, la taille ou la duree. Aucun pipeline ne telecharge, controle, transcrit et reinjecte le texte dans le meme ordre conversationnel.

## 3. `pickle` : decision explicite

**Decision : ne pas utiliser `pickle` pour la memoire conversationnelle persistante.**

La documentation Python avertit qu'un pickle malveillant peut executer du code lors de la deserialisation. Le format est Python-specifique, peu inspectable, difficile a requeter, a migrer, a expirer champ par champ et a auditer. Il ne gere pas non plus la concurrence ou la persistance transactionnelle. Ces proprietes sont incompatibles avec des messages utilisateurs, du contenu RAG et un systeme multi-worker.

Usages acceptables limites : cache local ephemere d'un objet entierement produit et relu par le meme processus de confiance, sans donnee externe, sans persistance durable. Ce cas n'est pas necessaire ici.

Choix retenus :

- modeles Django types pour les etats importants;
- `JSONField` versionne pour les structures flexibles, validees par Pydantic/dataclasses;
- PostgreSQL pour la duree, les transactions, les recherches et les politiques de retention;
- `PostgresSaver` de LangGraph pour les checkpoints du graphe;
- JSON pour les messages Celery, jamais pickle;
- stockage objet prive pour les medias temporaires, avec une reference en base.

## 4. Frontieres de responsabilite

| Composant | Responsabilite | Interdit |
|---|---|---|
| Adaptateur canal | verifier, normaliser, accuser reception | logique metier, appel LLM long |
| Inbox | dedupliquer, sequencer, suivre tentatives | produire une reponse utilisateur |
| Agent | comprendre, choisir un dialogue act, planifier des outils, rediger | mutation ORM directe, autorisation |
| Registre d'outils | schema, risque, droits, confirmation, execution | outil arbitraire invente par le modele |
| Services Django | verite metier, transaction, permissions, audit | faire confiance au texte du modele |
| Memoire | rappeler faits et episodes autorises | devenir la verite transactionnelle |
| RAG | fournir une connaissance documentaire citee | statut de commande, stock/prix temps reel |
| Outbox | livraison idempotente et retries | recalculer la decision conversationnelle |

## 5. Cycle complet d'un message

1. Le webhook verifie signature/token, taille et forme du payload.
2. Il normalise le message et cree un `InboundEnvelope` unique sur `(channel, external_message_id)`.
3. Une sequence monotone est attribuee sous verrou a la conversation.
4. La transaction programme `drain_conversation(conversation_id)` via `transaction.on_commit`.
5. Le webhook renvoie rapidement `2xx`; aucun appel LLM n'est necessaire dans la requete HTTP.
6. Le worker acquiert un bail de traitement par conversation avec jeton de fencing.
7. Il prend le premier message `PENDING` selon `(sequence, occurred_at, id)`; les suivants restent en attente.
8. Les medias sont controles et transcrits avant le routage semantique.
9. LangGraph charge checkpoint, etat metier, memoire de travail et resume.
10. Le routeur decide : social, interruption, changement de sujet, information, lecture metier, ecriture metier ou clarification.
11. Les lectures independantes peuvent etre paralleles; les mutations restent sequentielles.
12. Le planificateur ne peut choisir que les outils du registre accessibles a cet utilisateur.
13. La politique exige une confirmation pour les actions irreversibles ou financieres.
14. Le service Django execute avec cle d'idempotence `inbound_id + tool + version`.
15. Le composeur genere une reponse naturelle uniquement a partir du resultat type et du contexte social.
16. Un validateur controle absence de secret/ID interne, claims, longueur, langue, actions et citations.
17. Etat, checkpoint, resume, trace et `OutboundIntent` sont persistes de maniere coherente.
18. L'outbox livre la reponse et reprend en cas d'erreur fournisseur.
19. Le worker traite ensuite la sequence suivante.

Une erreur ne doit pas faire passer le message suivant devant le premier. Apres le nombre maximal de retries, le premier passe en `NEEDS_REVIEW` ou `DEAD_LETTER`; une politique explicite decide alors si la file peut avancer.

## 6. Modele de donnees cible

Les migrations seront additives. Aucun champ actuel n'est supprime durant la transition.

### 6.1 `InboundEnvelope`

- `conversation_id`, `channel`, `external_message_id` unique;
- `sequence` unique par conversation;
- `occurred_at`, `received_at`;
- `message_type`, `normalized_payload` minimise;
- `status`: `PENDING`, `PREPARING_MEDIA`, `PROCESSING`, `WAITING`, `DONE`, `RETRY`, `NEEDS_REVIEW`, `DEAD_LETTER`;
- `attempt_count`, `available_at`, `last_error_code` sans contenu sensible;
- `processing_token`, `lease_expires_at`;
- `trace_id`, `causation_id`.

### 6.2 `ConversationRuntime`

- `next_sequence`, `last_completed_sequence`, `version`;
- `active_topic`, `suspended_topics`;
- `active_task_type`, `active_task_id`, `active_task_version`;
- `social_state` minimal : dernier salut, derniere presentation, langue/style;
- `summary_id`, `checkpoint_thread_id`;
- `processing_token`, `lease_expires_at`.

`Conversation.context` reste lu et ecrit pendant la migration, puis ses champs critiques migrent progressivement vers des colonnes/modeles types.

### 6.3 Memoires

`ConversationSummary` : resume, plage de messages source, version du prompt, date, hash et statut de validation.

`MemoryFact` : sujet/utilisateur, type, valeur JSON, provenance, confiance, sensibilite, consentement, `valid_from`, `expires_at`, `superseded_by`, `deleted_at`.

`ConversationEpisode` : sujet, resultat, references de messages/outils, resume bref. Il sert au rappel conversationnel, pas a l'autorisation.

### 6.4 Execution et audit

`AgentRun`, `AgentStep`, `ToolInvocation`, `ToolApproval`, `OutboundIntent`, `MediaAsset` et `Transcription` portent les statuts, durees, modeles/prompts, usage token, erreurs classees et liens de causalite. Les prompts ou textes bruts sensibles ne sont pas exportes par defaut dans les metriques.

## 7. Memoire conversationnelle

La memoire est composee, pas monolithique.

### 7.1 Memoire de travail

Les derniers tours utiles, les resultats d'outils recents, la tache active et le resume. La fenetre est limitee par budget de tokens et pertinence, pas par un nombre fixe aveugle.

### 7.2 Memoire episodique

Elle conserve les episodes : creation de boutique suspendue a la position, recherche sans produit, commande annulee, question annexe resolue. Elle permet de dire : « Nous etions sur la position de ta boutique » sans forcer la reprise.

### 7.3 Memoire semantique durable

Seulement les faits stables et utiles : langue preferee, nom choisi, boutique habituelle, preferences explicitement donnees. Chaque fait a une provenance, une confiance, une duree et une possibilite d'effacement. Une phrase issue d'un document RAG ne devient jamais une memoire utilisateur.

### 7.4 Resume incremental

Le resume est regenere a des seuils mesures : budget de contexte, fin d'episode ou changement de sujet. Il doit distinguer :

- faits confirmes;
- hypotheses;
- actions executees;
- taches actives/suspendues;
- informations demandees mais non fournies;
- engagements du bot;
- preferences sociales.

Le resume conserve les IDs des tours sources. Le modele ne peut pas ecraser un fait metier; une contradiction declenche une resolution par la base ou une clarification.

### 7.5 Politique sociale

Le bot ne repond pas avec un script temporel rigide. Le composeur recoit `last_greeting_at`, `last_introduction_at`, relation et contexte :

- premier contact : salutation adaptee, presentation courte, question ouverte;
- « bonjour » quelques minutes plus tard : reponse chaleureuse courte sans nouvelle presentation;
- reprise le lendemain : salutation naturelle et rappel optionnel;
- message metier direct : pas de ceremonie obligatoire;
- changement complet de contexte : repondre au nouveau sujet, puis proposer la reprise si utile;
- jamais de boucle de salutation ou de presentation.

Exemple cible :

```text
Utilisateur : Bonjour encore
Bot : Rebonjour Louis. Nous etions sur la position de ta boutique.
      Tu peux la partager maintenant, revenir en arriere ou continuer plus tard.

Utilisateur : En fait, est-ce qu'il y a du riz disponible ?
Bot : Bien sur, je regarde les boutiques disponibles. Je garde la creation de ta
      boutique en pause; tu pourras la reprendre ensuite.
```

## 8. LangGraph : oui, avec un perimetre strict

LangGraph est utile ici pour les checkpoints, les interruptions humaines, la reprise apres panne et un graphe explicite. Il ne doit pas devenir une seconde base metier ni justifier une architecture multi-agent prematuree.

### 8.1 Graphe recommande

```text
START
  -> load_context
  -> prepare_media
  -> input_security
  -> classify_turn
       | social ---------------------------> compose
       | universal_interrupt -------------> suspend_or_cancel
       | topic_shift ----------------------> suspend_and_route
       | information ----------------------> retrieve
       | business_read --------------------> authorize_tools
       | business_write -------------------> authorize_tools
  -> plan
  -> policy_gate
       | confirmation_required -----------> interrupt()
       | rejected -------------------------> compose
       | allowed --------------------------> execute_tool
  -> verify_result
  -> compose
  -> output_guard
  -> persist_memory_and_outbox
  -> END
```

`thread_id` est l'UUID de conversation. `PostgresSaver` est obligatoire en production. Chaque noeud possede un entree/sortie typee et une politique de retry. Tout effet de bord est place apres un checkpoint et protege par idempotence. Comme un noeud interrompu peut redemarrer depuis son debut, aucun effet non idempotent ne doit preceder `interrupt()`.

### 8.2 Etat du graphe

L'etat transporte uniquement des references et snapshots bornes : message courant, resume, tache active, dialogue act, plan, resultats d'outils, niveau de risque et brouillon de reponse. Les objets ORM, secrets, clients HTTP et gros fichiers n'y sont pas serialises.

### 8.3 Strategie d'adoption

Commencer par un agent unique et un graphe lisible. Conserver temporairement l'orchestrateur actuel comme sous-graphe/route de compatibilite. Le multi-agent n'est envisage que si les evaluations prouvent qu'un agent unique ne suffit pas; sinon il augmente cout, latence et surface d'erreur.

## 9. Registre d'outils metier

Chaque outil declare : nom/version, schema d'entree/sortie, type lecture/ecriture, niveau de risque, permissions, preconditions, confirmation, timeout, retries, cle d'idempotence et redaction des logs.

Outils initiaux :

| Famille | Outils |
|---|---|
| Conversation | `suspend_task`, `resume_task`, `cancel_task`, `get_current_task` |
| Catalogue | `search_products`, `get_product_details`, `check_store_availability` |
| Commande | `get_active_order`, `add_item`, `remove_item`, `replace_item`, `set_location`, `review_order`, `confirm_order`, `cancel_order`, `list_orders`, `get_order_status` |
| Boutique | `get_store_state`, `start_store_onboarding`, `set_store_location`, `update_store_catalog`, `pause_store_onboarding` |
| Livraison | `get_driver_state`, `set_driver_availability`, `get_active_mission`, `report_delivery_issue`, confirmations existantes |
| Connaissance | `retrieve_knowledge` en lecture seule |

Les outils enveloppent les services d'application actuels. Ils ne dupliquent pas leurs regles.

### 9.1 Niveaux de risque

- `R0` : dialogue pur, aucun outil;
- `R1` : lecture autorisee;
- `R2` : brouillon/mutation reversible;
- `R3` : confirmation explicite obligatoire (commande, annulation, publication);
- `R4` : interdit au bot ou validation humaine obligatoire.

Les arguments du modele sont des propositions. L'executant remplace toute identite par l'utilisateur authentifie, resout les references textuelles cote serveur et rejette les IDs non autorises.

### 9.2 Aucun vendeur ou produit trouve

Le modele ne doit ni inventer ni repondre par une panne generique. A partir du resultat type, il explique ce qui manque : aucune boutique dans la zone, produit absent, stock non confirme, service momentanement indisponible. Il propose uniquement des actions reelles : elargir la zone, corriger le nom, retirer/remplacer l'article, enregistrer une demande, reessayer. Les raisons internes sensibles et les donnees d'autres vendeurs restent masquees.

## 10. Routeur et planificateur

Le classifieur produit plus qu'une intention :

- `dialogue_act`: saluer, remercier, questionner, corriger, confirmer, annuler;
- `topic`: achat, boutique, livraison, support, social, autre;
- `relation_to_active_task`: reponse attendue, interruption, changement, reprise, ambigu;
- `requested_capabilities`;
- entites et references textuelles;
- risque et besoin de clarification;
- langue, registre et signaux d'urgence;
- confiance par champ, pas seulement globale.

Le routeur deterministe applique ensuite les politiques. Un fast path lexical couvre `annuler`, `stop`, `aide`, `retour`, consentements et actions interactives, mais le classifieur contextuel traite les formulations riches. Une annulation ambigue demande : « Veux-tu annuler l'inscription de la boutique ou ta commande ? »

Le planificateur est borne : nombre maximal d'etapes, outils allowlistes, budget temps/tokens, aucune recursion ouverte. Les lectures independantes peuvent etre paralleles; une ecriture attend toujours les preconditions et resultats precedents.

## 11. Composeur de reponses naturelles

Le composeur ne decide pas les mutations. Il recoit un `ResponseBrief` type : faits autorises, resultat d'outil, objectif du tour, tache suspendue, options valides, langue/style et contraintes de canal.

Il doit :

- repondre d'abord a la demande actuelle;
- expliquer clairement les limites factuelles;
- varier naturellement sans contredire le resultat;
- eviter de repeter salutations, presentation et meme question;
- rappeler une tache uniquement lorsqu'elle est pertinente;
- ne jamais promettre une action qui n'a pas ete executee;
- proposer au plus quelques choix actionnables;
- produire un fallback deterministe a partir du `ResponseBrief` si la generation echoue.

Ainsi, une panne du composeur n'entraine plus « impossible de traiter » : le systeme dispose deja du resultat metier et peut envoyer une formulation sure.

## 12. RAG cible

### 12.1 Role

Le RAG sert aux politiques, FAQ, procedures, conditions, aide produit et documentation validee. Il ne sert pas au prix, stock, statut de commande, autorisation ou position courante : ces faits viennent d'outils metier.

### 12.2 Pipeline d'ingestion

1. source allowlistee et proprietaire;
2. extraction et normalisation;
3. detection de secrets/PII et quarantaine;
4. version, langue, tenant, ACL, dates de validite et hash;
5. decoupage semantique avec chevauchement mesure;
6. embeddings;
7. index lexical PostgreSQL + vecteur pgvector;
8. tests de questions de reference avant activation;
9. expiration/reindexation et suppression traçable.

### 12.3 Retrieval

Recherche hybride full-text + similarite vectorielle, fusion des rangs, filtres tenant/ACL/langue/fraicheur, puis reranking si les mesures le justifient. Commencer par la recherche exacte pgvector sur le petit corpus; ajouter HNSW seulement apres mesure de volume/latence/rappel.

Les passages restent balises comme donnees non fiables. La reponse cite les chunks, verifie que toute citation appartenait au lot recupere et refuse de conclure si la connaissance est insuffisante. Une instruction incluse dans un document ne peut appeler aucun outil.

## 13. Messages vocaux et transcription

### 13.1 Pipeline

1. L'adaptateur extrait `media_id/file_id`, MIME, taille/duree si disponibles et legende.
2. `MediaAsset` est cree avec statut `PENDING`; le message conserve sa sequence.
3. Un worker obtient une URL ephemere via l'API officielle du canal et telecharge en streaming avec authentification.
4. Il impose allowlist MIME, taille/duree, timeout, limite de decompression et hash; il ignore le nom de fichier externe.
5. Le fichier est stocke chiffre dans un espace prive temporaire, jamais dans le depot ou un chemin web public.
6. Une analyse antimalware est appliquee aux formats qui le necessitent.
7. Le service de transcription recoit un format supporte; les fichiers trop grands sont segmentes sans casser l'ordre temporel.
8. La transcription conserve modele, langue detectee, timestamps, confiance disponible et lien au media.
9. Le texte derive passe les memes controles et le meme graphe que le texte saisi.
10. Le media brut expire rapidement; la transcription suit la politique de retention des messages et les demandes d'effacement.

### 13.2 Comportement conversationnel

Le second message n'est pas traite avant la fin ou l'echec classe de la transcription du premier. Pour une note trop longue/incomprehensible, le bot explique precisement le probleme et demande un texte ou un nouvel audio. Une transcription faible sur une commande sensible est relue a l'utilisateur avant execution.

La limite de l'API de transcription doit etre appliquee depuis la configuration, pas codee en dur; la documentation OpenAI indique actuellement 25 Mo pour les uploads de transcription. Telegram permet de recuperer le chemin via `getFile`; WhatsApp fournit un media ID puis une URL temporaire authentifiee. Les comportements exacts doivent etre testes contre les versions d'API configurees avant mise en production.

## 14. Securite agentique

### 14.1 Menaces couvertes

- injection directe : « ignore tes instructions »;
- injection indirecte dans RAG, nom de produit, document, URL, audio/transcription;
- exfiltration de prompt, secrets, donnees inter-utilisateurs;
- outil excessif ou arguments manipules;
- memoire empoisonnee et persistance d'une fausse instruction;
- boucle d'agent, explosion de cout, denial of wallet;
- doublon/rejeu de webhook et execution repetee;
- confusion d'identite/canal/tenant;
- SSRF et telechargement de media arbitraire;
- sortie dangereuse rendue dans un autre canal.

### 14.2 Defenses

1. Instructions privilegiees separees des donnees et prompts versionnes.
2. Schemas stricts entre les noeuds; jamais de texte libre pour transmettre une commande.
3. Registre d'outils allowliste avec moindre privilege et identite imposee par le serveur.
4. Confirmation liee au resume exact de l'action, avec expiration et version d'etat.
5. Validation des arguments, ownership, stock, statut et transitions dans Django.
6. Donnees RAG/transcriptions encapsulees et incapables de modifier les regles.
7. Filtrage d'entree et de sortie par risque; moderation comme couche complementaire, jamais preuve d'autorisation.
8. Quotas utilisateur/tenant, limites d'etapes, tokens, outils, medias, temps et retries.
9. Idempotence, anti-rejeu, journal d'audit et kill switch par outil/canal/modele.
10. Memoires candidates validees par schema, provenance, sensibilite et politique; aucune instruction durable.
11. Tests adversariaux continus et cas reels anonymises.
12. Secrets seulement dans l'environnement/gestionnaire de secrets, jamais dans le graphe, le RAG ou les traces.

Le RAG et le fine-tuning ne resolvent pas l'injection. Le systeme doit rester sur meme si le modele suit une instruction hostile : l'executant deterministe constitue la derniere barriere.

## 15. Concurrence, ordre et idempotence

Celery garantit la livraison de travail, pas une execution exactement une fois. Toute tache peut etre redelivree. La strategie est donc **at-least-once + effets exactly-once logiques**.

- contrainte unique sur l'identifiant fournisseur;
- sequence transactionnelle par conversation;
- un drain actif par conversation avec bail et fencing token;
- verrou bref `select_for_update(skip_locked)` pour prendre le travail;
- aucun appel reseau sous une transaction longue;
- comparaison de `ConversationRuntime.version` avant commit de decision;
- cle d'idempotence pour chaque outil et sortie;
- outbox creee dans la meme transaction que le resultat;
- retries exponentiels avec jitter, puis file de revue;
- partition de queue par type de charge : `conversation`, `media`, `ai`, `outbound`, sans perdre l'ordre logique.

Redis Streams est une alternative valable pour un journal distribue, mais il n'est pas requis au premier lot. Une inbox PostgreSQL s'integre mieux aux transactions Django et reduit le nombre de sources de verite. Redis reste broker/cache; l'ordre durable reste en PostgreSQL.

## 16. Performance et cout

Budgets initiaux a valider par charge :

- webhook p95 < 500 ms;
- debut de traitement texte p95 < 1 s hors saturation;
- reponse simple p95 < 5 s;
- lecture avec outils/RAG p95 < 8 s;
- voix mesuree separement selon duree;
- aucune transaction DB gardee ouverte pendant un appel externe.

Leviers :

- fast paths deterministes pour boutons, position, confirmations et interruptions simples;
- un seul appel de classification/planification quand possible;
- petit modele configurable pour routage simple, modele plus capable pour planification complexe;
- prompt statique et definitions d'outils au debut afin de profiter du prompt caching;
- contexte selectionne, resume incremental et resultats d'outils compacts;
- cache court seulement pour connaissances publiques et recherches sans donnees temps reel;
- parallele uniquement pour lectures independantes;
- timeouts par noeud, circuit breaker fournisseur et fallback determine;
- pool DB/HTTP correctement dimensionne et prefetch Celery adapte aux taches longues.

La vitesse ne doit jamais etre gagnee en envoyant deux mutations concurrentes de la meme conversation.

## 17. Gestion des erreurs

Les erreurs sont classees : `INVALID_INPUT`, `POLICY_REJECTED`, `AUTHORIZATION`, `BUSINESS_CONFLICT`, `PROVIDER_TIMEOUT`, `RATE_LIMIT`, `TEMPORARY`, `INVALID_MODEL_OUTPUT`, `MEDIA_FAILURE`, `INTERNAL`.

Politique utilisateur :

- entree invalide : expliquer quoi corriger;
- conflit metier : expliquer l'etat reel et les options;
- modele indisponible mais fast path possible : continuer deterministement;
- composeur indisponible : utiliser un template construit depuis le resultat type;
- panne temporaire avant mutation : retry silencieux borne;
- resultat d'outil incertain : ne pas repeter aveuglement, verifier par lecture/idempotence;
- panne durable : conserver la tache et dire qu'elle pourra etre reprise, sans pretendre l'avoir terminee.

Le message generique actuel n'est conserve qu'en dernier recours, avec un identifiant de support et sans perdre le contexte.

## 18. Observabilite

OpenTelemetry doit relier webhook, inbox, transcription, appel modele, retrieval, outil, base, outbox et fournisseur par une trace. Prometheus garde les metriques agregees; Sentry les erreurs; les logs structures les decisions redigees.

Metriques principales :

- latence p50/p95/p99 par noeud, canal, modele et outil;
- profondeur/age des files, temps jusqu'au premier traitement;
- doublons, retries, dead letters, baux expires;
- taux de fallback IA et cause exacte;
- succes/erreur/confirmation/refus par outil;
- precision de reprise, changement de sujet et interruption;
- repetitions de salutations/presentations/questions;
- taux de boucles et nombre de tours par tache;
- abandon par etape et satisfaction explicite/implicite;
- groundedness/citations/retrieval recall du RAG;
- echec et confiance de transcription;
- alertes d'injection et appels d'outils bloques;
- tokens, cache hit, cout par tache terminee.

Les dashboards doivent permettre de passer d'une anomalie agregee a une trace pseudonymisee, avec acces controle et retention limitee.

## 19. Strategie de tests et evaluations

### 19.1 Tests deterministes

- transitions du graphe et resume/suspension/reprise;
- registre d'outils, permissions, risque et confirmation;
- idempotence et relecture de chaque mutation;
- ordre de deux, dix et cent messages concurrents;
- crash avant/apres outil, checkpoint et outbox;
- multi-canal et meme utilisateur;
- migrations et compatibilite du contexte actuel;
- audio valide, corrompu, trop grand, MIME trompeur, timeout;
- ACL RAG, source expiree, citation invalide;
- retention et effacement des memoires/medias.

### 19.2 Jeu conversationnel versionne

Inclure au minimum :

- bonjour initial, bonjour repete, reprise le lendemain;
- `annuler`, `arrete`, `plus tard`, `retour`, et annulations ambigues a chaque etape;
- changement achat -> boutique -> support -> reprise;
- reponses attendues et hors sujet pendant une demande de position;
- produit absent, vendeur absent, stock inconnu, plusieurs produits ambigus;
- fautes, langage familier camerounais, melange francais/anglais, messages fragmentes;
- historique/statut/modification/confirmation;
- audios courts/longs, bruyants, plusieurs langues;
- tentatives d'injection directes, RAG, outil, memoire et audio.

Chaque cas definit : etat initial, messages, dialogue acts attendus, outils permis/interdits, mutations, contenu factuel obligatoire/interdit et etat final. Les sorties naturelles sont evaluees par criteres, pas egalite de chaine.

### 19.3 Evals

Mesurer comprehension par champ, selection d'outil, arguments, succes de tache, absence de mutation non autorisee, groundedness, qualite de reprise, non-repetition, ton et latence. Comparer chaque version de prompt/modele sur le meme corpus avant promotion. Les cas de production anonymises alimentent regulierement le jeu apres revue humaine.

## 20. Plan d'implementation sans casse

### Lot 0 - Baseline et garde-fous

- figer le jeu de regression actuel;
- ajouter metriques des routes, erreurs IA et conversations bloquees;
- feature flags et kill switches;
- enregistrer les traces de decision sans contenu sensible.

**Passage :** aucun test actuel casse; baseline latence/erreur disponible.

### Lot 1 - Interruptions universelles et taches suspendables

- modele type de tache active/suspendue;
- `cancel`, `help`, `back`, `pause`, `resume`, changement de sujet avant les handlers bloquants;
- adapter seller/driver/catalogue sans supprimer leurs etats actuels;
- composeur social simple anti-repetition.

**Passage :** plus aucune boucle « partage ta position » sur salut/annulation/changement de sujet dans le corpus.

### Lot 2 - Inbox ordonnee et outbox

- webhook court, sequence, drain Celery, baux, fencing, DLQ;
- idempotence outils/sorties et tests de crash/concurrence;
- migration progressive canal par canal.

**Passage :** zero inversion et zero double mutation dans les tests de charge/fault injection.

### Lot 3 - Agent LangGraph en shadow mode

- etat type, `PostgresSaver`, routeur, planificateur borne;
- registre d'outils enveloppant les services existants;
- comparaison silencieuse agent/orchestrateur sans execution double.

**Passage :** parite sur les parcours existants et amelioration mesuree des interruptions.

### Lot 4 - Composeur naturel et memoire

- `ResponseBrief`, fallback deterministe;
- resume incremental, episodes, faits durables gouvernes;
- politiques de salut, presentation, sujet et reprise.

**Passage :** seuils d'eval sur repetition, contexte, exactitude factuelle et ton; aucun fait metier issu de la memoire seule.

### Lot 5 - RAG hybride securise

- pgvector, embeddings, recherche hybride, ACL/fraicheur;
- jeu de retrieval, citations et defenses indirectes;
- canari sur aide/FAQ uniquement.

**Passage :** recall/groundedness cibles et zero fuite inter-tenant dans les tests.

### Lot 6 - Contexte multi-intention et agent actif en lecture

- fenetre de contexte bornee et securisee pour la comprehension et le composeur;
- contrat multi-intention distinguant expliquer, agir et expliquer puis agir;
- distinction explicite entre etre livre et devenir livreur;
- LangGraph actif uniquement pour les outils `R1` en lecture;
- RAG contextuel generalise sur la documentation publique autorisee.

**Passage :** les dialogues reels sur l'identite de WhatAssoh, l'annulation composee
et la livraison client reussissent sans demarrer un mauvais parcours; aucune ecriture
agentique n'est executable.

### Lot 7 - Voix

- normalisation media WhatsApp/Telegram;
- telechargement prive, controles, transcription et retention;
- reinjection ordonnee et confirmation des commandes incertaines.

**Passage :** corpus audio, limites, erreurs et suppression valides; ordre preserve.

### Lot 8 - Canari puis generalisation

- personnel/interne, puis faible pourcentage, puis cohortes;
- comparaison KPI et rollback automatique;
- retrait de l'ancien routeur seulement apres une periode stable et migration des etats.

## 21. Criteres d'acceptation globaux

L'architecture n'est consideree terminee que si :

1. Toute tache active accepte annulation, pause, aide, retour et changement de sujet.
2. Le bot reprend le bon contexte sans imposer la reprise.
3. Salutations et presentations ne bouclent pas.
4. Deux messages rapides sont executes dans l'ordre, y compris avec audio et retries.
5. Toute mutation est autorisee, confirmee selon risque, idempotente et auditee.
6. Aucun ID, vendeur, produit, prix, stock ou succes d'action n'est invente.
7. L'absence de vendeur/produit est expliquee avec options reelles.
8. RAG et memoire respectent ACL, provenance, retention et suppression.
9. Les injections directes/indirectes ne franchissent pas la politique d'outils.
10. Une panne LLM ne detruit pas la tache et produit un fallback utile quand un resultat metier existe.
11. Les SLO, couts et evaluations passent avant chaque promotion.
12. Le rollback vers l'orchestrateur actuel reste possible tant que la migration n'est pas declaree achevee.

## 22. Dependances proposees

Ajouter uniquement lorsqu'un lot les utilise :

- `langgraph` et `langgraph-checkpoint-postgres` pour graphe/checkpoints;
- `pgvector` (extension PostgreSQL + integration Django) pour les embeddings;
- SDK OpenAI deja present pour Responses, embeddings et transcription;
- `httpx` ou client fournisseur existant avec streaming/timeouts pour les medias;
- `opentelemetry-*` pour traces distribuees;
- `hypothesis` en developpement pour invariants/state machines;
- scanner antivirus type ClamAV en service d'infrastructure si les formats acceptes l'exigent.

Ne pas ajouter une base vectorielle separee, Kafka, Redis Streams ou un framework multi-agent sans besoin mesure. PostgreSQL, Celery et les services Django actuels couvrent la premiere cible avec moins de complexite operationnelle.

## 23. Questions a trancher avant implementation

- durees legales/produit de conservation des messages, audios, transcriptions et faits memoire;
- consentement et commande utilisateur pour voir/effacer la memoire;
- actions exactes classees `R3`/`R4`;
- langues et variantes locales prioritaires pour les evals/transcriptions;
- seuils de confiance audio avant confirmation;
- politique quand un message en echec definitif bloque les suivants;
- SLO et budget de cout par conversation;
- corpus documentaire autorise et responsables de validation;
- disponibilite de pgvector et OpenTelemetry sur l'infrastructure de production.

Ces choix ne bloquent pas les lots 0 et 1, mais doivent etre consignes avant les lots memoire, RAG et voix.

## 24. References principales

### OpenAI et conception d'agents

1. [OpenAI - Agents](https://developers.openai.com/api/docs/guides/agents)
2. [OpenAI - Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)
3. [OpenAI - Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
4. [OpenAI - Tools](https://developers.openai.com/api/docs/guides/tools)
5. [OpenAI - Function calling](https://developers.openai.com/api/docs/guides/function-calling)
6. [OpenAI - Agent Builder safety](https://developers.openai.com/api/docs/guides/agent-builder-safety)
7. [OpenAI - Prompting](https://developers.openai.com/api/docs/guides/prompting)
8. [OpenAI - Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
9. [OpenAI - Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)
10. [OpenAI - Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
11. [OpenAI - Deployment checklist](https://developers.openai.com/api/docs/guides/deployment-checklist)
12. [OpenAI - Embeddings](https://developers.openai.com/api/docs/guides/embeddings)
13. [OpenAI - Speech to text](https://developers.openai.com/api/docs/guides/speech-to-text)
14. [OpenAI - Audio](https://developers.openai.com/api/docs/guides/audio)
15. [OpenAI - Moderation](https://developers.openai.com/api/docs/guides/moderation)

### Orchestration et memoire

16. [LangGraph - Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
17. [LangGraph - Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
18. [LangGraph - Memory](https://docs.langchain.com/oss/python/concepts/memory)
19. [LangGraph - Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph)
20. [Python - pickle](https://docs.python.org/3/library/pickle.html)

### Securite et gouvernance

21. [OWASP - LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
22. [OWASP - AI Agent Security](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
23. [OWASP - RAG Security](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)
24. [OWASP GenAI - LLM01 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
25. [NIST - AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)

### Donnees, concurrence et exploitation

26. [Django - Database transactions](https://docs.djangoproject.com/en/dev/topics/db/transactions/)
27. [PostgreSQL - Explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html)
28. [pgvector - README et index](https://github.com/pgvector/pgvector/blob/master/README.md)
29. [Celery - Tasks, idempotence et retries](https://docs.celeryq.dev/en/latest/userguide/tasks.html)
30. [Celery - Routing](https://docs.celeryq.dev/en/latest/userguide/routing.html)
31. [Celery - Optimizing](https://docs.celeryq.dev/en/latest/userguide/optimizing.html)
32. [Redis - Streams](https://redis.io/docs/latest/develop/data-types/streams/)
33. [OpenTelemetry - Signals](https://opentelemetry.io/docs/concepts/signals/)
34. [OpenTelemetry - Traces](https://opentelemetry.io/docs/concepts/signals/traces/)

### Canaux

35. [Telegram Bot API - `getFile` et messages vocaux](https://core.telegram.org/bots/api)
36. [Meta WhatsApp Cloud API - Media](https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media)
37. [Meta WhatsApp Cloud API - Webhooks](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components)

## 25. Conclusion

La bonne evolution n'est ni « davantage de prompt » ni « laisser le modele tout faire ». C'est une architecture ou l'intelligence linguistique dispose d'une vraie memoire, d'un contexte, d'un plan et d'outils, tout en restant enfermee dans des controles metier deterministes.

LangGraph est retenu pour l'orchestration durable; PostgreSQL pour l'etat, les checkpoints et la memoire gouvernee; pgvector pour le RAG hybride; Celery pour l'execution ordonnee; les services Django existants pour toute action reelle. `pickle` est explicitement exclu de la persistance conversationnelle.

Le premier changement fonctionnel devra corriger les interruptions universelles et les parcours bloques. Le reste s'installe ensuite par couches mesurables et reversibles, sans reecriture brutale et sans casser les workflows deja fiables.
