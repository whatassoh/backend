# CI/CD de production

Le workflow `.github/workflows/ci-cd.yml` vérifie les pull requests vers
`main`. Après chaque push sur `main`, il exécute les mêmes contrôles puis
déploie exactement le commit testé dans `/opt/whatAssoh/backend`.

Le déploiement appelle `deploy/deploy.sh`. Le fichier `.env.production`, les
volumes Docker et les sauvegardes restent sur le serveur et ne transitent pas
par GitHub Actions.

La CI exécute `manage.py check`, vérifie qu'aucune migration n'a été oubliée et
lance toute la suite Pytest avec PostgreSQL/PostGIS et Redis. Ruff n'est pas
encore bloquant, car le dépôt possède un passif de formatage à corriger dans un
changement séparé.

## Configuration GitHub

Créer l'environnement GitHub `production`. Une approbation obligatoire peut
y être activée pour conserver une validation humaine avant chaque déploiement.

Ajouter les secrets suivants à cet environnement :

- `DEPLOY_HOST` : adresse IP ou nom SSH du serveur ;
- `DEPLOY_USER` : compte SSH autorisé à déployer ;
- `DEPLOY_PORT` : port SSH, facultatif (22 par défaut) ;
- `DEPLOY_SSH_KEY` : clé SSH privée dédiée au CI/CD ;
- `DEPLOY_KNOWN_HOSTS` : ligne de clé d'hôte vérifiée du serveur ;
- `DEPLOY_DOMAIN` : domaine public, par exemple `whatasso.dvybes.com` ;
- `LETSENCRYPT_EMAIL` : email utilisé par Certbot.

Le compte SSH doit pouvoir lire et modifier `/opt/whatAssoh/backend`, utiliser
Docker, récupérer `origin/main` et exécuter sans interaction les commandes
`sudo` déjà utilisées par `deploy/deploy.sh` pour Nginx et Certbot.

Le dépôt serveur doit être propre pour les fichiers suivis. En cas de
modification manuelle, le déploiement s'arrête avant tout changement.

## Premier lancement

1. Fusionner le workflow sur `main`.
2. Configurer les secrets et les protections de l'environnement `production`.
3. Lancer `CI/CD production` manuellement depuis GitHub Actions.
4. Vérifier le job de déploiement et `https://<DEPLOY_DOMAIN>/health/`.
