# Lot 4 - Composeur naturel et mémoire gouvernée

## Statut et objectif

Le lot 4 est implémenté derrière des feature flags désactivés par défaut. Il ajoute une
couche de présentation naturelle après la décision métier, une mémoire structurée et
effaçable, des résumés incrémentaux asynchrones et des seuils d'évaluation. Il ne change
ni les services métier, ni leurs autorisations, ni les règles de mutation.

Le principe de sécurité est strict : la base métier reste l'unique source de vérité pour
les commandes, boutiques, catalogues, prix, stocks, paiements, positions, identités et
permissions. Une mémoire conversationnelle ne peut jamais autoriser une action ni
prouver qu'une action a réussi.

## Architecture de bout en bout

1. Le message entrant est persisté par le pipeline ordonné du lot 2.
2. L'observateur de style agrège uniquement des signaux faibles, jamais le texte brut.
3. L'extracteur déterministe conserve uniquement une préférence sociale explicitement
   formulée et allowlistée.
4. L'orchestrateur existant décide et exécute le tour avec les services métier.
5. Le composeur reçoit un `ResponseBrief` après cette décision. Il peut reformuler, mais
   il ne dispose d'aucun outil et ne peut changer les actions interactives.
6. Les contrôles mécaniques et les seuils d'évaluation acceptent la reformulation ou
   rétablissent immédiatement la réponse déterministe.
7. Une tâche Celery produit plus tard un résumé incrémental borné et traçable.

Cette séparation garantit qu'une panne fournisseur, un JSON invalide ou un candidat
infidèle ne remplace jamais une réponse métier déjà valide par le message générique
« impossible de traiter ».

## Composeur naturel

`ResponseBrief` contient l'objectif du tour, le texte faisant autorité, le style, le
canal, la tâche active, les choix valides et les interdictions factuelles. Le modèle ne
reçoit pas un droit de décision. `NaturalResponseComposer` conserve l'intégralité du
`ResponsePlan` et ne propose qu'un nouveau texte.

Les gardes synchrones imposent :

- les mêmes nombres et URL que la réponse source;
- aucune action, métadonnée ou demande de position modifiée;
- aucune affirmation de succès non présente dans la réponse faisant autorité;
- aucun prix, stock, produit, vendeur, position ou droit inventé;
- repli sur le texte déterministe à la moindre exception.

Chaque comparaison est enregistrée dans `ResponseCompositionRun`. Le shadow mode garde
le texte historique en production tout en mesurant le candidat. La suppression de la
conversation supprime aussi ces comparaisons.

## Style et comportement social

`ConversationStyleProfile` agrège la forme d'adresse, le registre, la longueur moyenne,
le niveau d'emoji et les marqueurs techniques. Il ne tente pas d'inférer l'âge, le genre,
l'origine, la santé, la religion, les opinions ou d'autres attributs sensibles. Le bot
n'imite ni fautes d'orthographe, ni insultes, ni vulnérabilités linguistiques.

Les préférences explicites `tu/vous`, langue, longueur, emojis et niveau technique
priment sur les observations. Les politiques de salutation et de présentation déjà
portées par `ConversationSocialState` empêchent leur répétition à chaque message. Un
changement de sujet reste traité par les interruptions universelles et la suspension de
tâche, sans forcer l'utilisateur à répondre au champ précédent.

## Mémoire durable gouvernée

`MemoryFact` accepte uniquement cinq catégories sociales :

- `address_form`;
- `preferred_language`;
- `response_length`;
- `emoji_preference`;
- `technical_level`.

Une écriture exige une formulation explicite reconnue à haute précision. Chaque fait a
une provenance, un consentement explicite, une confiance, une date de validité et une
expiration. Une nouvelle valeur supplante l'ancienne sans effacer l'historique. Les
lectures filtrent systématiquement l'utilisateur, l'expiration, l'effacement et la
supersession.

Les commandes « qu'as-tu mémorisé sur moi ? » et « oublie mes préférences » fonctionnent
même sans tâche active. L'effacement est logique pour l'audit et supprime les profils de
style dérivés. Il ne touche jamais les commandes ou les données de compte.

## Résumé incrémental et épisodes

`IncrementalConversationMemory` traite uniquement les messages postérieurs au dernier
résumé, avec un minimum et une taille de lot configurables. Les identifiants sources et
un hash de contenu rendent l'opération idempotente. L'appel modèle a lieu hors
transaction; seule l'écriture courte est transactionnelle.

Le schéma sépare les faits confirmés, hypothèses, actions exécutées, tâches actives ou
suspendues, informations manquantes, engagements du bot et préférences sociales. Une
instruction contenue dans un message est explicitement traitée comme donnée non fiable.
Un résumé ne crée jamais automatiquement un `MemoryFact`.

Un épisode n'est créé que lorsqu'un sujet se termine ou change clairement. La tâche
`conversations.refresh_memory` est relançable et idempotente; la réconciliation
périodique récupère les conversations éligibles après un crash ou une perte de message
Celery.

## Activation et retour arrière

Variables disponibles :

```dotenv
AI_STYLE_ADAPTATION_ENABLED=False
AI_CONVERSATION_MEMORY_ENABLED=False
AI_MEMORY_SUMMARY_ENABLED=False
AI_NATURAL_COMPOSER_SHADOW_ENABLED=False
AI_NATURAL_COMPOSER_ENABLED=False
AI_MEMORY_SUMMARY_MIN_MESSAGES=8
AI_MEMORY_SUMMARY_BATCH_SIZE=24
AI_MEMORY_FACT_TTL_DAYS=365
AI_MEMORY_RECONCILIATION_BATCH_SIZE=100
```

Ordre recommandé après migrations : style, mémoire explicite, résumé sur une cohorte
interne, composeur shadow, puis composeur actif par cohorte. `AI_EMERGENCY_STOP=True`
coupe immédiatement le résumé et le composeur. Les réponses déterministes restent
disponibles. Désactiver les flags suffit au rollback applicatif; les tables peuvent
rester en place sans être consultées.

## Critères de passage

Les tests couvrent la conservation du `ResponsePlan`, le refus d'un nombre inventé,
l'idempotence des résumés, la provenance, l'isolation entre utilisateurs, l'effacement
et le refus de mémoriser une affirmation métier. Avant activation générale, le corpus
d'évaluation doit atteindre : exactitude mécanique 100 %, préservation du contexte au
moins 80 %, alignement de ton au moins 80 %, similarité de répétition au plus 85 %, et
zéro fait métier provenant de la mémoire seule.

Les comparaisons shadow doivent en plus être relues sur les parcours achat, vendeur,
livreur, interruptions, erreurs fournisseur et messages adversariaux. Toute régression
sur une mutation, une autorisation, une fuite inter-utilisateur ou une affirmation
factuelle bloque le déploiement.
