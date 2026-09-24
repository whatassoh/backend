# Lot 0 - Baseline, observabilite et controles de deploiement

## Statut et limite

Ce lot prepare la transformation agentique sans l'activer. Il ne change ni le
routeur conversationnel, ni les textes, ni les mutations metier, ni le schema de
base de donnees. LangGraph, la memoire, les outils agentiques et la voix restent
desactives.

## Controles de fonctionnement

Les switches sont lus uniquement par `get_ai_runtime_policy()`. Les dependances
sont fail-safe : un outil ne peut pas etre active sans agent, une ecriture ne
peut pas etre activee sans registre d'outils, et `AI_EMERGENCY_STOP=True` coupe
toute capacite qui appelle un modele.

| Variable | Defaut | Effet actuel |
|---|---:|---|
| `AI_ENABLED` | `False` | Autorise le moteur IA historique |
| `AI_EMERGENCY_STOP` | `False` | Coupe immediatement les appels modele |
| `AI_AGENT_ENABLED` | `False` | Reserve au futur graphe principal |
| `AI_AGENT_SHADOW_ENABLED` | `False` | Reserve au futur shadow mode sans effet metier |
| `AI_AGENT_TOOLS_ENABLED` | `False` | Reserve au registre d'outils |
| `AI_AGENT_WRITE_TOOLS_ENABLED` | `False` | Reserve aux outils de mutation |
| `AI_AGENT_MEMORY_ENABLED` | `False` | Reserve a la memoire structuree |
| `AI_RAG_ENABLED` | `True` | Controle futur du RAG modele; depend de l'IA |
| `AI_VOICE_ENABLED` | `False` | Reserve au pipeline de transcription |
| `AI_DECISION_OBSERVABILITY_ENABLED` | `True` | Active les mesures non sensibles du lot 0 |

### Arret d'urgence

En incident fournisseur, cout ou securite :

```dotenv
AI_EMERGENCY_STOP=True
```

Redeployer ensuite l'application et les workers avec la meme configuration.
Les actions structurees et les chemins deterministes restent disponibles; les
services de comprehension recoivent leur implementation indisponible. Pour
reactiver, diagnostiquer d'abord la cause, verifier les metriques et remettre la
valeur a `False` pendant une fenetre surveillee.

## Metriques

- `whatassoh_conversation_decisions_total{channel,status,route}`
- `whatassoh_conversation_ai_failures_total{channel,category,retryable}`
- `whatassoh_conversation_repeated_responses_total{channel,flow}`
- `whatassoh_conversation_decision_duration_seconds{channel,status}`

Les labels sont des enums ou categories bornees. Aucun texte utilisateur,
numero, identifiant externe, position, commande, prompt ou payload fournisseur
n'est expose. La comparaison de repetition reste locale et ne publie pas le
texte compare. Une panne Prometheus est interceptee et ne change jamais la
decision conversationnelle.

## Alertes initiales recommandees

Les seuils definitifs doivent partir d'une semaine de baseline :

1. taux d'erreurs IA par categorie et canal;
2. hausse du ratio de reponses repetees;
3. hausse du p95 de decision;
4. hausse des `RECOVERABLE_FAILURE`;
5. absence soudaine de decisions alors que les webhooks continuent.

Ne pas alerter sur un identifiant de conversation : cela cree une cardinalite
non bornee et peut exposer une donnee operationnelle.

## Baseline de regression

`audit/conversation_baseline_v1.json` est le contrat initial lisible par machine.
Les tests existants restent la preuve executable principale. Un lot futur peut
ameliorer une reponse, mais ne peut retirer un invariant de securite sans revue
explicite et nouvelle version du fichier.

Commandes de verification du lot :

```bash
ruff check apps/ai/application/runtime.py \
  apps/conversations/application/observability.py \
  apps/ai/tests/test_runtime_policy.py \
  apps/conversations/tests/test_observability.py
pytest apps/ai/tests apps/conversations/tests
python manage.py check
```

## Conditions de passage au lot 1

- suite IA et conversations verte;
- configuration fail-safe testee;
- aucune migration inattendue;
- aucune PII dans les labels et logs ajoutes;
- metriques visibles sur `/internal/metrics` dans l'environnement autorise;
- arret d'urgence teste en preproduction;
- baseline archivee avant toute correction des interruptions.
