#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="${DEPLOY_ROOT:-/opt/whatAssoh/backend}"
EXPECTED_REPOSITORY="${EXPECTED_REPOSITORY:-git@github.com:whatassoh/backend.git}"
LOCK_FILE="${DEPLOY_LOCK_FILE:-/tmp/whatassoh-deploy.lock}"

GIT_SHA="${1:-}"
DOMAIN="${2:-}"
LETSENCRYPT_EMAIL="${3:-}"

die() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || die "Commit Git invalide."
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "Nom de domaine invalide."
[[ "$LETSENCRYPT_EMAIL" == *@* ]] || die "Email Let's Encrypt invalide."
[[ -d "$ROOT_DIR/.git" ]] || die "Dépôt Git absent dans ${ROOT_DIR}."

command -v git >/dev/null 2>&1 || die "git est requis."
command -v flock >/dev/null 2>&1 || die "flock est requis."

exec 9>"$LOCK_FILE"
flock -n 9 || die "Un autre déploiement WhatAssoh est déjà en cours."

cd "$ROOT_DIR"

origin_url="$(git remote get-url origin)"
case "$origin_url" in
    "$EXPECTED_REPOSITORY"|https://github.com/whatassoh/backend.git)
        ;;
    *)
        die "Le remote origin ne correspond pas au dépôt WhatAssoh attendu."
        ;;
esac

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    die "Le dépôt serveur contient des modifications suivies. Déploiement refusé."
fi

previous_sha="$(git rev-parse HEAD)"

git fetch --prune origin main
git cat-file -e "${GIT_SHA}^{commit}" 2>/dev/null || \
    die "Le commit ${GIT_SHA} est introuvable après le fetch."
git merge-base --is-ancestor "$GIT_SHA" origin/main || \
    die "Le commit demandé n'appartient pas à origin/main."

git checkout --detach "$GIT_SHA"

restore_checkout_on_error() {
    local rc=$?

    trap - ERR
    printf '[WARN] Restauration du checkout serveur vers %s.\n' "$previous_sha" >&2
    git checkout --detach "$previous_sha" || true
    exit "$rc"
}

trap restore_checkout_on_error ERR

MANAGE_NGINX=0 ./deploy/deploy.sh "$DOMAIN" "$LETSENCRYPT_EMAIL"

trap - ERR
printf '[OK] Commit %s déployé avec succès.\n' "$GIT_SHA"
