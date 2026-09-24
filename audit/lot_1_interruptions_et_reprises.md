# Lot 1 - Interruptions universelles, suspension et reprise

## Objectif

Le lot 1 supprime le verrou conversationnel qui obligeait chaque texte a etre
consomme par le formulaire actif. L'utilisateur peut maintenant saluer,
demander de l'aide, revenir en arriere, mettre en pause, reprendre, annuler ou
changer de parcours sans que ces messages deviennent un nom, une ville, un type
de vehicule, une position ou une instruction de livraison.

Ce lot ne remplace pas encore l'orchestrateur par LangGraph. Il rend les
machines d'etat existantes interruptibles et prepare leur future integration au
graphe. Les services Django restent les seuls executants metier.

## Ordre de decision

Pour tout message entrant :

1. verifier l'idempotence du message;
2. ignorer le pre-routeur pour une position native ou un callback structure;
3. reconnaitre les commandes universelles exactes;
4. sinon demander au moteur de comprehension avec un contexte de navigation
   minimal;
5. executer une interruption sure ou un changement de sujet;
6. seulement ensuite presenter le message au formulaire actif;
7. poursuivre le routeur metier historique.

Une expression riche n'est jamais reduite a un mot-cle. Par exemple,
`annule la commande de Paul` et `bonjour je veux 3 kg de riz` passent par la
comprehension contextuelle. En revanche, `annuler`, `stop`, `plus tard`,
`reprendre`, `retour`, `aide` et `bonjour` peuvent fonctionner sans fournisseur
IA lorsqu'ils sont autonomes.

## Modele de tache

`ConversationContext` ajoute deux structures retrocompatibles :

- `suspended_tasks`, pile bornee a cinq snapshots de navigation;
- `social_state`, memoire minimale des salutations et de la presentation.

Un snapshot contient uniquement :

- le type de tache;
- le parcours `BUY`, `SELL` ou `DELIVER`;
- le flow a restaurer;
- la date et la raison de suspension.

Les donnees utiles ne sont pas dupliquees dans la pile. Le brouillon vendeur,
le brouillon livreur, la clarification, la commande et la proposition catalogue
restent dans leurs champs types ou leurs tables metier. La pile est donc un
index de navigation, pas une nouvelle source de verite.

Types couverts :

- commande;
- inscription vendeur;
- catalogue vendeur;
- inscription livreur;
- instructions de livraison;
- parcours sans sous-tache plus precise.

## Semantique des commandes

### `annuler`

L'annulation vise la tache au premier plan :

- inscription vendeur non confirmee : suppression du brouillon, aucune boutique;
- inscription livreur non confirmee : suppression du brouillon, aucun profil;
- commande active : service d'abandon existant et autorise;
- proposition catalogue : proposition retiree, aucune mutation catalogue;
- instructions de livraison : attente fermee;
- parcours seul : sortie du parcours;
- tache suspendue sans tache courante : reprise interne puis annulation ciblee.

Une boutique ou un profil deja confirme n'est jamais supprime par une commande
conversationnelle generique. L'annulation termine seulement le parcours actif.

### `plus tard`

La tache courante est empilee, ses donnees restent intactes, puis la navigation
revient a `IDLE`. Le bot expose une action stable `conversation.resume`.

### `reprendre`

La derniere tache suspendue est restauree. Si une autre tache est actuellement
au premier plan, les deux sont echangees : la tache courante est suspendue et la
precedente devient active. Cela evite de perdre un achat commence pendant la
pause d'une inscription vendeur.

Sans pile suspendue, `reprendre` reaffiche l'etape active ou utilise la reprise
de commande existante.

### `retour`

- vendeur : `REVIEW -> CITY -> LOCATION -> NAME`;
- livreur : `REVIEW -> VEHICLE_TYPE`;
- catalogue : proposition retiree, retour a la saisie;
- commande : clarification retiree, retour a la construction;
- premiere etape : reponse explicite sans mutation.

Les valeurs deja saisies sont conservees. Revenir de `CITY` vers `LOCATION` ne
supprime donc ni le nom ni les coordonnees; la nouvelle saisie pourra les
remplacer explicitement.

### changement de sujet

Un passage `SELL -> BUY`, `BUY -> DELIVER` ou equivalent suspend le parcours
precedent avant d'activer le nouveau. Les boutons de parcours et les intentions
naturelles utilisent la meme politique. Si un message de changement vers
`BUY` contient deja des articles valides, ces articles continuent dans le
service de commande au lieu d'etre perdus.

## Dialogue social

Le premier salut d'une conversation :

- choisit bonjour/bonsoir selon l'heure locale;
- utilise le prenom disponible;
- presente WhatAssoh une seule fois;
- rappelle la tache courante sans forcer une reponse de formulaire.

Les salutations suivantes repondent `Rebonjour` et ne repetent pas la
presentation. `social_state` enregistre uniquement compteur et dates; il ne
contient ni message brut ni profil psychologique.

Une salutation ne change jamais : commande, brouillon, etape, position,
catalogue ou role utilisateur.

## Securite et invariants

1. Le pre-routeur lexical utilise des correspondances de phrase entiere.
2. Une instruction ambiguë reste confiee au moteur contextuel.
3. Le modele ne choisit aucun identifiant de boutique, produit, commande ou
   profil dans ce lot.
4. L'annulation metier reutilise les services autorises existants.
5. Pause, reprise et retour ne creent aucune ligne metier.
6. Les callbacks universels possedent des identifiants stables et passent par
   l'idempotence fournisseur existante.
7. La pile est bornee et dedupliquee par type de tache.
8. Les anciens payloads sans pile ou memoire sociale se chargent avec des
   valeurs par defaut.
9. Aucun schema de base ni migration n'est necessaire; le JSON est enrichi de
   facon additive.
10. L'observabilite du lot 0 ne journalise toujours aucun texte utilisateur.

## Fichiers principaux

- `apps/conversations/domain/context.py` : contrats persistants;
- `apps/conversations/application/interruptions.py` : politique pure;
- `apps/conversations/application/orchestrator.py` : priorite et composition;
- `apps/conversations/application/seller_onboarding.py` : annulation/retour;
- `apps/conversations/application/driver_onboarding.py` : annulation/retour;
- `apps/conversations/application/journey.py` : activation et sortie de pause;
- `apps/ai/domain/understanding.py` : intentions `PAUSE` et `BACK`;
- `apps/conversations/tests/test_universal_interruptions.py` : regression.

## Matrice de validation

| Etat | Message | Resultat obligatoire |
|---|---|---|
| vendeur/position | bonjour | salut + etape intacte |
| vendeur/position | annuler | brouillon retire, aucune boutique |
| vendeur/position | plus tard | snapshot SELL, donnees intactes |
| vendeur suspendu | reprendre | retour LOCATION, demande de position |
| vendeur/ville | retour | etape LOCATION, valeurs conservees |
| livreur/review | retour | choix vehicule, aucun profil |
| livreur/toute etape | stop | brouillon retire, aucun profil |
| catalogue/proposition | annuler | aucune mutation appliquee |
| commande/clarification | retour | retour construction, commande intacte |
| tache quelconque | aide | capacites + commandes, tache preservee |
| SELL actif | bouton BUY | SELL suspendu, BUY actif |
| BUY actif + SELL suspendu | reprendre | BUY suspendu, SELL restaure |
| salut repete | salut | aucune seconde presentation |
| callback rejoue | reprendre | aucune seconde transition |

## Verification sans installation

Conformement a la decision projet, aucune dependance locale n'est installee par
ce lot. Les controles sans GDAL sont :

```bash
ruff check apps/conversations/application/interruptions.py \
  apps/conversations/domain/context.py \
  apps/conversations/tests/test_universal_interruptions.py
python -m compileall -q apps/ai apps/conversations
git diff --check
```

La suite Django complete reste obligatoire dans la CI ou le serveur de test qui
possede deja GDAL/PostGIS avant de pousser vers la production.

## Rollback

Tous les champs sont additifs. Pour desactiver fonctionnellement le lot avant
suppression du code, le rollback applicatif consiste a redeployer la revision
precedente : les anciennes versions ignorent les nouvelles cles JSON. Aucun
rollback de base n'est requis. Les objets metier crees avant rollback restent
inchanges.
