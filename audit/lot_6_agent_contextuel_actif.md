# Lot 6 - Agent contextuel actif en lecture seule

## Objectif

Le lot 6 corrige les défauts observés dans les conversations réelles sans remettre
les mutations métier au modèle. Il rend la compréhension multi-intention, apporte une
continuité conversationnelle bornée, active LangGraph pour les lectures autorisées et
permet au RAG de répondre dans le contexte du dialogue.

Le principe de sécurité ne change pas: le modèle comprend et propose; les services
Django, le registre d'outils et les règles métier autorisent, lisent ou modifient.

## Garanties

1. Une question sur la livraison client ne démarre jamais le parcours livreur lorsque
   le but détecté est `RECEIVE_DELIVERY`.
2. Une question expliquant comment annuler ne déclenche aucune annulation.
3. Une demande composée peut exécuter une seule interruption conversationnelle
   explicite et répondre à la question dans la même réponse.
4. Les messages récents et résumés sont des données non fiables. Ils ne donnent jamais
   une permission et ne remplacent aucune donnée métier.
5. L'agent actif n'exécute que les outils `R1` dont `side_effects` vaut `none` ou
   `read_only`. Les plans `R2`, `R3` et `R4` sont refusés avant l'exécution.
6. Les outils d'écriture restent sans handler et `AI_AGENT_WRITE_TOOLS_ENABLED=False`.
7. Les résultats bruts des outils ne sont ni checkpointés par LangGraph ni enregistrés
   dans l'audit agentique.
8. Une panne du graphe ou d'un outil de lecture rend la main à l'orchestrateur
   déterministe existant.

## Contrat de compréhension v2

Le schéma strict ajoute:

- `secondary_intents`: intentions explicites secondaires, maximum trois;
- `interaction_mode`: `ANSWER`, `ACT`, `ANSWER_AND_ACT` ou `CLARIFY`;
- `action_requested`: distingue une demande d'action d'une question procédurale;
- `journey_goal`: but précis, dont `RECEIVE_DELIVERY`, `BECOME_DRIVER` et
  `WORK_AS_DRIVER`;
- `standalone_question`: reformulation autonome d'un suivi contextuel;
- `task_target`: cible sémantique sans identifiant métier.

Les identifiants, prix, stocks, positions et permissions restent interdits dans cette
sortie. Le schéma OpenAI est strict: toutes les propriétés sont requises et les valeurs
absentes utilisent `null`, `false`, `NONE` ou une liste vide.

## Fenêtre de contexte

`ConversationContextWindowBuilder` assemble uniquement:

- tâche, parcours, flux et étape conversationnelle;
- types des tâches suspendues;
- au plus huit messages texte récents, avec rôle et troncature;
- dernier résumé validé, limité aux champs autorisés;
- trois épisodes clos;
- préférences de communication explicitement gouvernées.

La projection omet les coordonnées, positions, identifiants de commande ou boutique,
prix, stocks, permissions et données de paiement. Le message entrant courant est exclu
par son identifiant externe afin de ne pas être dupliqué dans le prompt.

## LangGraph actif

Le graphe `read-only-v2` comporte trois noeuds déterministes:

1. `route`: choisit le domaine conversationnel;
2. `plan`: sélectionne au plus `AI_AGENT_MAX_PLAN_STEPS` outils allowlistés;
3. `policy`: calcule le risque et rend `SHADOW_ONLY`, `ACTIVE_READ_ONLY` ou
   `DENIED_NON_READ_ONLY`.

L'exécution des lectures se fait après le graphe dans `ActiveReadAgentService`. Cette
séparation empêche les données retournées par les outils d'entrer dans le checkpoint.
Le `ToolContext` vérifie que l'utilisateur de l'outil est celui de la conversation.

`AgentExecutionRun` conserve seulement la version du graphe, la route, les outils
prévus/exécutés, leurs statuts, le risque, la décision de politique, la latence et la
catégorie d'erreur. Aucun texte utilisateur n'y est stocké.

## RAG contextuel

La question autonome sert à la recherche, tandis que la question originale et le
contexte borné servent à la formulation. Seuls les chunks récupérés peuvent justifier
les affirmations. Le contexte conversationnel n'est jamais une preuve.

La source `whatassoh-platform-fr.v2.json` explique notamment:

- la raison d'être de la plateforme;
- la différence entre WhatAssoh et son assistant;
- les parcours client, vendeur et livreur;
- la différence entre être livré et devenir livreur;
- les interruptions, demandes composées et limites de sécurité.

Le déploiement ingère cette version après le health check applicatif.

## Composition naturelle

Le composeur reçoit jusqu'à six messages récents en données non fiables. Il peut ainsi
éviter une présentation ou une formulation déjà utilisée. Les garde-fous mécaniques
continuent à vérifier les nombres, références, questions obligatoires, polarité et actes
de parole avant qu'une proposition remplace la réponse autorisée.

## Variables

| Variable | Valeur active | Rôle |
|---|---:|---|
| `AI_CONTEXT_ENGINE_ENABLED` | `True` | Active la fenêtre bornée |
| `AI_CONTEXT_MAX_RECENT_MESSAGES` | `8` | Nombre maximal de tours récents |
| `AI_CONTEXT_MAX_MESSAGE_CHARACTERS` | `500` | Limite par message |
| `AI_CONTEXT_MAX_SUMMARY_CHARACTERS` | `2000` | Budget du résumé |
| `AI_AGENT_ENABLED` | `True` | Active le graphe de production |
| `AI_AGENT_TOOLS_ENABLED` | `True` | Autorise le registre sous politique |
| `AI_AGENT_WRITE_TOOLS_ENABLED` | `False` | Interdit les écritures agentiques |
| `AI_AGENT_MEMORY_ENABLED` | `True` | Autorise le contexte gouverné |
| `AI_RAG_ENABLED` | `True` | Rend les réponses documentaires visibles |
| `AI_RAG_ROLLOUT_PERCENT` | `100` | Évite le retour aléatoire au texte fixe |

## Activation production

Après fusion et déploiement du code:

```bash
cd /opt/whatAssoh/backend
./deploy/configure-ai-lots.sh active WHATSAPP 100
./deploy/deploy.sh whatasso.dvybes.com "$LETSENCRYPT_EMAIL"
```

Le pipeline CI utilise `deploy-ci.sh`; la seconde commande ci-dessus est destinée au
déploiement manuel. Le script de configuration sauvegarde `.env.production` et ne
journalise pas les secrets.

## Vérifications attendues

- migration `ai.0004_agent_execution_run` appliquée;
- checkpoints LangGraph initialisés;
- source WhatAssoh version 2 ingérée et indexée;
- `AI_AGENT_WRITE_TOOLS_ENABLED=False` confirmé dans le conteneur web;
- question « comment être livré » répond sans créer de profil livreur;
- question « WhatAssoh c'est toi ? » explique plateforme contre assistant;
- annulation explicite plus question produit une réponse composée;
- disponibilité catalogue et statut de commande viennent d'un outil ou d'un sélecteur
  autoritaire;
- aucune réponse générique répétitive pour un utilisateur hors cohorte RAG, puisque le
  profil actif utilise désormais un rollout de 100%.

Commande de contrôle non sensible:

```bash
docker-compose -p whatassoh --env-file .env.production \
  -f docker-compose.prod.yml run --rm web \
  python manage.py ai_runtime_status --require-lot6-active
```
