#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

# ==========================================================
# whatAssoh - Safe production deployment
#
# Usage:
#   ./deploy/deploy.sh api.example.com admin@example.com
#
# Options par variables d'environnement :
#
#   ENABLE_TLS=1
#   PORT_RANGE_START=8010
#   PORT_RANGE_END=8099
#   HEALTH_PATH=/
#   CORS_ORIGINS=https://frontend.example.com
#
# ==========================================================


# ----------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------

PROJECT_NAME="whatassoh"

ROOT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

COMPOSE_FILE="${ROOT_DIR}/docker-compose.prod.yml"
ENV_FILE="${ROOT_DIR}/.env.production"

BACKUP_DIR="${ROOT_DIR}/.deploy-backups"

DOMAIN="${1:-}"
LETSENCRYPT_EMAIL="${2:-}"

ENABLE_TLS="${ENABLE_TLS:-1}"

PORT_RANGE_START="${PORT_RANGE_START:-8010}"
PORT_RANGE_END="${PORT_RANGE_END:-8099}"

HEALTH_PATH="${HEALTH_PATH:-/}"
CORS_ORIGINS="${CORS_ORIGINS:-}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"


# ----------------------------------------------------------
# NGINX
# ----------------------------------------------------------

NGINX_SITE="/etc/nginx/sites-available/${PROJECT_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${PROJECT_NAME}.conf"

ACME_WEBROOT="/var/www/letsencrypt"


# ----------------------------------------------------------
# ÉTAT DU DÉPLOIEMENT
# ----------------------------------------------------------

NGINX_TOUCHED=0
WEB_REPLACED=0
MIGRATIONS_APPLIED=0

DB_BACKUP=""
OLD_WEB_IMAGE=""

NGINX_SITE_EXISTED=0
NGINX_LINK_EXISTED=0

NGINX_SITE_BACKUP=""
NGINX_LINK_BACKUP=""


# ----------------------------------------------------------
# SUDO
# ----------------------------------------------------------

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi


# ----------------------------------------------------------
# LOGGING
# ----------------------------------------------------------

log() {
    printf '\n[%s] %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" \
        "$*"
}

warn() {
    printf '\n[WARN] %s\n' "$*" >&2
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}


# ----------------------------------------------------------
# OUTILS
# ----------------------------------------------------------

require_cmd() {

    command -v "$1" >/dev/null 2>&1 || \
        die "Commande requise absente : $1"
}


# ----------------------------------------------------------
# GESTION .env.production
# ----------------------------------------------------------

env_get() {

    local key="$1"

    [[ -f "$ENV_FILE" ]] || return 1

    awk -v key="$key" '

        index($0, key "=") == 1 {

            sub("^[^=]*=", "", $0)

            print $0

            exit
        }

    ' "$ENV_FILE"
}


env_set() {

    local key="$1"
    local value="$2"
    local tmp

    tmp="$(mktemp "${ROOT_DIR}/.env.production.tmp.XXXXXX")"

    awk \
        -v key="$key" \
        -v value="$value" '

        BEGIN {
            done=0
        }

        index($0, key "=") == 1 {

            if (!done)
                print key "=" value

            done=1

            next
        }

        {
            print
        }

        END {

            if (!done)
                print key "=" value
        }

    ' "$ENV_FILE" > "$tmp"

    chmod 600 "$tmp"

    mv "$tmp" "$ENV_FILE"
}


ensure_value() {

    local key="$1"
    local default="$2"
    local current

    current="$(env_get "$key" 2>/dev/null || true)"

    if [[ -z "$current" ]]; then

        env_set "$key" "$default"

    fi
}


ensure_hex_secret() {

    local key="$1"
    local bytes="$2"
    local current

    current="$(env_get "$key" 2>/dev/null || true)"

    if [[ -z "$current" ]]; then

        env_set "$key" "$(openssl rand -hex "$bytes")"

        log "Secret ${key} généré."

    fi
}


# ----------------------------------------------------------
# PORTS
# ----------------------------------------------------------

port_in_use() {

    local port="$1"

    ss -H -ltn 2>/dev/null \
        | awk '{print $4}' \
        | grep -Eq ":${port}$"
}


port_owned_by_this_web() {

    local port="$1"

    docker ps \
        --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
        --filter "label=com.docker.compose.service=web" \
        --format '{{.Ports}}' \
        2>/dev/null \
        | grep -Fq "127.0.0.1:${port}->"
}


find_free_port() {

    local port

    for ((port=PORT_RANGE_START; port<=PORT_RANGE_END; port++)); do

        if ! port_in_use "$port"; then

            printf '%s\n' "$port"

            return 0
        fi

    done

    return 1
}


# ----------------------------------------------------------
# ATTENTE DOCKER
# ----------------------------------------------------------

wait_for_service() {

    local service="$1"
    local timeout="${2:-120}"

    local elapsed=0
    local cid
    local status

    while (( elapsed < timeout )); do

        cid="$(
            "${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true
        )"

        if [[ -n "$cid" ]]; then

            status="$(
                docker inspect \
                    --format \
                    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
                    "$cid" \
                    2>/dev/null || true
            )"

            case "$status" in

                healthy|running)

                    return 0
                    ;;

                unhealthy|exited|dead)

                    return 1
                    ;;
            esac

        fi

        sleep 2

        elapsed=$((elapsed + 2))

    done

    return 1
}


# ----------------------------------------------------------
# HEALTH CHECK DJANGO
# ----------------------------------------------------------

wait_for_http() {

    local timeout="${1:-90}"

    local elapsed=0
    local code

    while (( elapsed < timeout )); do

        code="$(
            curl \
                -sS \
                -o /dev/null \
                -w '%{http_code}' \
                --max-time 5 \
                -H "Host: ${DOMAIN}" \
                "http://127.0.0.1:${APP_PORT}${HEALTH_PATH}" \
                2>/dev/null || true
        )"

        # 1xx à 4xx = le serveur HTTP répond.
        # 5xx ou 000 = problème.
        if [[ "$code" =~ ^[1-4][0-9][0-9]$ ]]; then

            return 0

        fi

        sleep 3

        elapsed=$((elapsed + 3))

    done

    return 1
}


# ----------------------------------------------------------
# NGINX BACKUP
# ----------------------------------------------------------

backup_nginx() {

    mkdir -p "$BACKUP_DIR"

    if "${SUDO[@]}" test -e "$NGINX_SITE"; then

        NGINX_SITE_EXISTED=1

        NGINX_SITE_BACKUP="${BACKUP_DIR}/nginx-site-${STAMP}.bak"

        "${SUDO[@]}" cp -a \
            "$NGINX_SITE" \
            "$NGINX_SITE_BACKUP"

    fi


    if \
        "${SUDO[@]}" test -e "$NGINX_LINK" \
        || "${SUDO[@]}" test -L "$NGINX_LINK"
    then

        NGINX_LINK_EXISTED=1

        NGINX_LINK_BACKUP="${BACKUP_DIR}/nginx-link-${STAMP}.bak"

        "${SUDO[@]}" cp -a \
            "$NGINX_LINK" \
            "$NGINX_LINK_BACKUP"

    fi
}


# ----------------------------------------------------------
# NGINX ROLLBACK
# ----------------------------------------------------------

restore_nginx() {

    warn "Restauration de la configuration Nginx précédente..."

    if \
        [[ "$NGINX_SITE_EXISTED" -eq 1 ]] \
        && [[ -n "$NGINX_SITE_BACKUP" ]]
    then

        "${SUDO[@]}" cp -a \
            "$NGINX_SITE_BACKUP" \
            "$NGINX_SITE"

    else

        "${SUDO[@]}" rm -f "$NGINX_SITE"

    fi


    "${SUDO[@]}" rm -f "$NGINX_LINK"


    if \
        [[ "$NGINX_LINK_EXISTED" -eq 1 ]] \
        && [[ -n "$NGINX_LINK_BACKUP" ]]
    then

        "${SUDO[@]}" cp -a \
            "$NGINX_LINK_BACKUP" \
            "$NGINX_LINK"

    fi


    if "${SUDO[@]}" nginx -t; then

        "${SUDO[@]}" systemctl reload nginx || true

    else

        warn "La configuration restaurée échoue aussi à nginx -t."
        warn "Aucun reload Nginx supplémentaire effectué."

    fi
}


# ----------------------------------------------------------
# APPLICATION ROLLBACK
# ----------------------------------------------------------

rollback_web() {

    if [[ -z "$OLD_WEB_IMAGE" ]]; then

        warn "Premier déploiement."
        warn "Aucune ancienne image whatassoh disponible."

        return 0
    fi


    warn "Rollback web -> ${OLD_WEB_IMAGE}"


    env_set \
        "WEB_IMAGE" \
        "$OLD_WEB_IMAGE"


    "${COMPOSE[@]}" up \
        -d \
        --no-deps \
        web \
        || warn "Le rollback du conteneur web a échoué."
}


# ----------------------------------------------------------
# ROLLBACK GLOBAL
# ----------------------------------------------------------

on_error() {

    local rc=$?

    trap - ERR


    warn "Le déploiement a échoué (code ${rc})."


    # Nginx uniquement whatassoh
    if [[ "$NGINX_TOUCHED" -eq 1 ]]; then

        restore_nginx || true

    fi


    # Web uniquement whatassoh
    if [[ "$WEB_REPLACED" -eq 1 ]]; then

        rollback_web || true

    elif [[ -n "$OLD_WEB_IMAGE" ]]; then

        env_set \
            "WEB_IMAGE" \
            "$OLD_WEB_IMAGE"

    fi


    # On NE rollback JAMAIS automatiquement la DB.
    if [[ "$MIGRATIONS_APPLIED" -eq 1 ]]; then

        warn "ATTENTION : les migrations DB ne sont pas annulées automatiquement."

        if [[ -n "$DB_BACKUP" ]]; then

            warn "Backup pré-migration : ${DB_BACKUP}"

        fi

    fi


    warn "Aucun Docker prune/down/volume rm/network rm n'a été exécuté."
    warn "Aucune ressource bangobango n'est ciblée par ce script."

    exit "$rc"
}


trap on_error ERR


# ==========================================================
# 1. PREFLIGHT
# ==========================================================

[[ "$(uname -s)" == "Linux" ]] || \
    die "Ce script doit être lancé sur le serveur Linux de production."


[[ -n "$DOMAIN" ]] || \
    die "Usage : ./deploy/deploy.sh api.example.com admin@example.com"


[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || \
    die "Nom de domaine invalide : ${DOMAIN}"


[[ -f "$COMPOSE_FILE" ]] || \
    die "Fichier absent : ${COMPOSE_FILE}"


[[ -f "${ROOT_DIR}/Dockerfile" ]] || \
    die "Dockerfile absent dans ${ROOT_DIR}"


for cmd in \
    docker \
    nginx \
    ss \
    getent \
    openssl \
    curl \
    awk \
    grep \
    sed \
    ip

do

    require_cmd "$cmd"

done


if [[ "$ENABLE_TLS" == "1" ]]; then

    require_cmd certbot

    [[ -n "$LETSENCRYPT_EMAIL" ]] || \
        die "ENABLE_TLS=1 : ajoute l'email Let's Encrypt en deuxième argument."

fi


docker info >/dev/null 2>&1 || \
    die "Docker n'est pas accessible."


docker compose version >/dev/null 2>&1 || \
    die "Docker Compose v2 n'est pas disponible."


# Sécurité absolue :
# le compose whatassoh ne doit pas référencer bangobango.

if grep -qi 'bangobango' "$COMPOSE_FILE"; then

    die "SÉCURITÉ : docker-compose.prod.yml contient 'bangobango'. Arrêt."

fi


# container_name contournerait en partie l'isolation Compose.

if grep -Eq \
    '^[[:space:]]*container_name[[:space:]]*:' \
    "$COMPOSE_FILE"
then

    die "Supprime les container_name: du compose production."

fi


mkdir -p "$BACKUP_DIR"

chmod 700 "$BACKUP_DIR"


# ==========================================================
# 2. DNS
# ==========================================================

SERVER_IP="$(
    ip -4 route get 1.1.1.1 2>/dev/null \
        | awk '
            {
                for(i=1;i<=NF;i++) {

                    if($i=="src") {

                        print $(i+1)

                        exit
                    }
                }
            }
        '
)"


[[ -n "$SERVER_IP" ]] || \
    die "Impossible de déterminer l'IPv4 du serveur."


mapfile -t DNS_IPS < <(

    getent ahostsv4 "$DOMAIN" \
        | awk '{print $1}' \
        | sort -u
)


[[ "${#DNS_IPS[@]}" -gt 0 ]] || \
    die "${DOMAIN} ne résout vers aucune IPv4."


DNS_MATCH=0


for ip_addr in "${DNS_IPS[@]}"; do

    log "DNS ${DOMAIN} -> ${ip_addr}"

    if [[ "$ip_addr" == "$SERVER_IP" ]]; then

        DNS_MATCH=1

    fi

done


log "IP serveur -> ${SERVER_IP}"


if \
    [[ "$DNS_MATCH" -ne 1 ]] \
    && [[ "${ALLOW_DNS_MISMATCH:-0}" != "1" ]]
then

    die "
Le DNS de ${DOMAIN} ne pointe pas vers ${SERVER_IP}.

Corrige le DNS avant de déployer.

Si un CDN/proxy est volontaire :
ALLOW_DNS_MISMATCH=1 ./deploy/deploy.sh ...
"
fi


# ----------------------------------------------------------
# Refuse de reprendre accidentellement le domaine d'une
# autre application Nginx.
# ----------------------------------------------------------

if ! "${SUDO[@]}" test -e "$NGINX_SITE"; then

    if \
        "${SUDO[@]}" nginx -T 2>/dev/null \
        | grep 'server_name' \
        | grep -Fq "$DOMAIN"
    then

        die "
Le domaine ${DOMAIN} existe déjà dans Nginx.

Aucune modification n'a été faite.
Vérification manuelle nécessaire.
"
    fi

fi


# ==========================================================
# 3. .env.production
# ==========================================================

# Cas dangereux :
# volume whatassoh existant mais fichier contenant les secrets perdu.

if [[ ! -f "$ENV_FILE" ]]; then

    EXISTING_WHATASSOH_VOLUMES="$(
        docker volume ls \
            --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
            -q \
            2>/dev/null || true
    )"

    if [[ -n "$EXISTING_WHATASSOH_VOLUMES" ]]; then

        die "
Des volumes Docker whatassoh existent déjà,
mais ${ENV_FILE} est absent.

Je refuse de générer de nouveaux mots de passe,
car PostgreSQL pourrait avoir été initialisé avec
d'anciens secrets.
"

    fi


    umask 077

    : > "$ENV_FILE"

    chmod 600 "$ENV_FILE"

    log "Création de ${ENV_FILE}"

fi


# ----------------------------------------------------------
# Secrets créés uniquement s'ils n'existent pas
# ----------------------------------------------------------

ensure_hex_secret \
    "DJANGO_SECRET_KEY" \
    48


ensure_value \
    "POSTGRES_DB" \
    "whatassoh_db"


ensure_value \
    "POSTGRES_USER" \
    "whatassoh"


ensure_hex_secret \
    "POSTGRES_PASSWORD" \
    32


ensure_hex_secret \
    "REDIS_PASSWORD" \
    32


# ----------------------------------------------------------
# Variables contrôlées
# ----------------------------------------------------------

env_set \
    "DJANGO_SETTINGS_MODULE" \
    "config.settings.production"


env_set \
    "DJANGO_DEBUG" \
    "False"


env_set \
    "POSTGRES_HOST" \
    "postgres"


env_set \
    "POSTGRES_PORT" \
    "5432"


env_set \
    "REDIS_HOST" \
    "redis"


env_set \
    "REDIS_PORT" \
    "6379"


REDIS_PASSWORD="$(env_get REDIS_PASSWORD)"


env_set \
    "REDIS_URL" \
    "redis://:${REDIS_PASSWORD}@redis:6379/0"


env_set \
    "CELERY_BROKER_URL" \
    "redis://:${REDIS_PASSWORD}@redis:6379/1"


env_set \
    "CELERY_RESULT_BACKEND" \
    "redis://:${REDIS_PASSWORD}@redis:6379/2"


env_set \
    "DJANGO_ENV" \
    "production"


env_set \
    "DJANGO_ALLOWED_HOSTS" \
    "$DOMAIN"


env_set \
    "CSRF_TRUSTED_ORIGINS" \
    "https://${DOMAIN}"


if [[ -z "$CORS_ORIGINS" ]]; then

    CORS_ORIGINS="https://${DOMAIN}"

fi


env_set \
    "CORS_ALLOWED_ORIGINS" \
    "$CORS_ORIGINS"


# ==========================================================
# 4. PORT PRIVÉ DJANGO
# ==========================================================

EXISTING_PORT="$(
    env_get APP_PORT 2>/dev/null || true
)"


APP_PORT=""


if [[ "$EXISTING_PORT" =~ ^[0-9]+$ ]]; then

    if \
        ! port_in_use "$EXISTING_PORT" \
        || port_owned_by_this_web "$EXISTING_PORT"
    then

        APP_PORT="$EXISTING_PORT"

    else

        warn "
Le port ${EXISTING_PORT} est utilisé
par un autre service.

Recherche automatique d'un nouveau port.
"

    fi

fi


if [[ -z "$APP_PORT" ]]; then

    APP_PORT="$(find_free_port)" || \
        die "
Aucun port libre entre
${PORT_RANGE_START} et ${PORT_RANGE_END}.
"

fi


env_set \
    "APP_PORT" \
    "$APP_PORT"


log "
Port privé whatAssoh :

127.0.0.1:${APP_PORT}
        ↓
web:8000
"


# ==========================================================
# 5. IMAGE VERSIONNÉE
# ==========================================================

OLD_WEB_IMAGE="$(
    env_get WEB_IMAGE 2>/dev/null || true
)"


GIT_SHA="$(
    git -C "$ROOT_DIR" \
        rev-parse --short HEAD \
        2>/dev/null \
        || printf 'nogit'
)"


RELEASE_ID="${STAMP}-${GIT_SHA}"


NEW_WEB_IMAGE="whatassoh-web:${RELEASE_ID}"


env_set \
    "WEB_IMAGE" \
    "$NEW_WEB_IMAGE"


# Toutes les commandes Compose passent obligatoirement ici.

COMPOSE=(

    docker compose

    -p "$PROJECT_NAME"

    --env-file "$ENV_FILE"

    -f "$COMPOSE_FILE"
)


# ----------------------------------------------------------
# Validation Compose
# ----------------------------------------------------------

"${COMPOSE[@]}" config -q


SERVICES="$(
    "${COMPOSE[@]}" config --services
)"


for service in web postgres redis; do

    grep -qx "$service" <<<"$SERVICES" || \
        die "
Service '${service}'
absent de docker-compose.prod.yml
"

done


# ==========================================================
# 6. BUILD DJANGO
# ==========================================================

log "Build ${NEW_WEB_IMAGE}"


"${COMPOSE[@]}" build web


# ==========================================================
# 7. POSTGIS + REDIS
# ==========================================================

log "
Démarrage :

whatassoh/postgres
whatassoh/redis
"


"${COMPOSE[@]}" up \
    -d \
    postgres \
    redis


wait_for_service postgres 120 || {

    "${COMPOSE[@]}" logs \
        --tail=100 \
        postgres \
        >&2 || true

    die "PostgreSQL/PostGIS n'est pas healthy."
}


wait_for_service redis 120 || {

    "${COMPOSE[@]}" logs \
        --tail=100 \
        redis \
        >&2 || true

    die "Redis n'est pas healthy."
}


POSTGRES_DB="$(env_get POSTGRES_DB)"
POSTGRES_USER="$(env_get POSTGRES_USER)"


# ----------------------------------------------------------
# PostGIS
# ----------------------------------------------------------

log "Vérification PostGIS"


"${COMPOSE[@]}" exec \
    -T \
    postgres \
    psql \
    -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -c 'CREATE EXTENSION IF NOT EXISTS postgis;'


# ==========================================================
# 8. CONTRÔLES DJANGO AVANT MIGRATION
# ==========================================================

log "Recherche de migrations oubliées"


"${COMPOSE[@]}" run \
    --rm \
    --no-deps \
    web \
    python manage.py \
    makemigrations \
    --check \
    --dry-run


log "Django check --deploy"


"${COMPOSE[@]}" run \
    --rm \
    --no-deps \
    web \
    python manage.py \
    check \
    --deploy


log "Plan de migration"


"${COMPOSE[@]}" run \
    --rm \
    --no-deps \
    web \
    python manage.py \
    migrate \
    --plan \
    | tee "${BACKUP_DIR}/migration-plan-${STAMP}.txt"


# ==========================================================
# 9. BACKUP DB
# ==========================================================

DB_BACKUP="
${BACKUP_DIR}/postgres-before-${RELEASE_ID}.dump
"


# Retire le retour à la ligne produit par la lisibilité ci-dessus
DB_BACKUP="${DB_BACKUP//$'\n'/}"


log "Backup DB avant migration : ${DB_BACKUP}"


"${COMPOSE[@]}" exec \
    -T \
    postgres \
    pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --format=custom \
    > "$DB_BACKUP"


chmod 600 "$DB_BACKUP"


# ==========================================================
# 10. MIGRATIONS
# ==========================================================

log "Application des migrations"


"${COMPOSE[@]}" run \
    --rm \
    --no-deps \
    web \
    python manage.py \
    migrate \
    --noinput


MIGRATIONS_APPLIED=1


# ==========================================================
# 11. STATIC
# ==========================================================

log "collectstatic"


"${COMPOSE[@]}" run \
    --rm \
    --no-deps \
    web \
    python manage.py \
    collectstatic \
    --noinput


# ==========================================================
# 12. PROMOTION DU NOUVEAU WEB
# ==========================================================

log "Démarrage du nouveau web"


"${COMPOSE[@]}" up \
    -d \
    --no-deps \
    web


WEB_REPLACED=1


wait_for_service web 90 || {

    "${COMPOSE[@]}" logs \
        --tail=150 \
        web \
        >&2 || true

    die "
Le conteneur web ne reste pas
en état running.
"
}


if ! wait_for_http 90; then

    "${COMPOSE[@]}" logs \
        --tail=150 \
        web \
        >&2 || true

    die "
Health check HTTP échoué :

127.0.0.1:${APP_PORT}${HEALTH_PATH}
"
fi


log "Django répond correctement en local."


# ==========================================================
# 13. NGINX BACKUP
# ==========================================================

backup_nginx


NGINX_TOUCHED=1


TMP_NGINX="$(mktemp)"


trap 'rm -f "$TMP_NGINX"' EXIT


# ==========================================================
# 14. CONFIG NGINX HTTP
# ==========================================================

render_http_nginx() {

cat > "$TMP_NGINX" <<EOF

server {

    listen 80;
    listen [::]:80;

    server_name ${DOMAIN};


    location ^~ /.well-known/acme-challenge/ {

        root ${ACME_WEBROOT};

        default_type "text/plain";
    }


    location / {

        proxy_pass http://127.0.0.1:${APP_PORT};

        proxy_http_version 1.1;

        proxy_set_header Host \$host;

        proxy_set_header X-Real-IP \$remote_addr;

        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 30s;

        proxy_read_timeout 120s;
    }

}

EOF
}


# ==========================================================
# 15. CONFIG NGINX HTTPS
# ==========================================================

render_https_nginx() {

cat > "$TMP_NGINX" <<EOF

server {

    listen 80;
    listen [::]:80;

    server_name ${DOMAIN};


    location ^~ /.well-known/acme-challenge/ {

        root ${ACME_WEBROOT};

        default_type "text/plain";
    }


    location / {

        return 301 https://\$host\$request_uri;
    }

}


server {

    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name ${DOMAIN};


    ssl_certificate \
        /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;

    ssl_certificate_key \
        /etc/letsencrypt/live/${DOMAIN}/privkey.pem;


    client_max_body_size 20M;


    location / {

        proxy_pass http://127.0.0.1:${APP_PORT};

        proxy_http_version 1.1;

        proxy_set_header Host \$host;

        proxy_set_header X-Real-IP \$remote_addr;

        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        proxy_set_header X-Forwarded-Proto https;

        proxy_connect_timeout 30s;

        proxy_read_timeout 120s;
    }

}

EOF
}


# ==========================================================
# 16. ACTIVATION HTTP
# ==========================================================

render_http_nginx


"${SUDO[@]}" install \
    -m 0644 \
    "$TMP_NGINX" \
    "$NGINX_SITE"


"${SUDO[@]}" ln \
    -sfn \
    "$NGINX_SITE" \
    "$NGINX_LINK"


"${SUDO[@]}" mkdir \
    -p \
    "$ACME_WEBROOT"


# ----------------------------------------------------------
# TEST AVANT RELOAD
# ----------------------------------------------------------

log "nginx -t"


"${SUDO[@]}" nginx -t


log "Reload Nginx"


"${SUDO[@]}" systemctl reload nginx


# ==========================================================
# 17. LET'S ENCRYPT
# ==========================================================

if [[ "$ENABLE_TLS" == "1" ]]; then

    log "Certificat Let's Encrypt"


    "${SUDO[@]}" certbot certonly \
        --webroot \
        -w "$ACME_WEBROOT" \
        -d "$DOMAIN" \
        --email "$LETSENCRYPT_EMAIL" \
        --agree-tos \
        --non-interactive \
        --keep-until-expiring


    render_https_nginx


    "${SUDO[@]}" install \
        -m 0644 \
        "$TMP_NGINX" \
        "$NGINX_SITE"


    # Encore une fois :
    # JAMAIS de reload avant nginx -t.

    log "nginx -t HTTPS"


    "${SUDO[@]}" nginx -t


    log "Reload Nginx HTTPS"


    "${SUDO[@]}" systemctl reload nginx

fi


# ==========================================================
# 18. TEST PUBLIC FINAL
# ==========================================================

if [[ "$ENABLE_TLS" == "1" ]]; then

    FINAL_URL="https://${DOMAIN}${HEALTH_PATH}"

else

    FINAL_URL="http://${DOMAIN}${HEALTH_PATH}"

fi


log "Test final : ${FINAL_URL}"


HTTP_CODE="$(
    curl \
        -sS \
        -o /dev/null \
        -w '%{http_code}' \
        --max-time 15 \
        "$FINAL_URL" \
        || true
)"


if [[ ! "$HTTP_CODE" =~ ^[1-4][0-9][0-9]$ ]]; then

    die "
Test public échoué.

HTTP : ${HTTP_CODE:-000}
"

fi


# ==========================================================
# 19. SUCCÈS
# ==========================================================

trap - ERR


rm -f "$TMP_NGINX"

trap - EXIT


log "DÉPLOIEMENT TERMINÉ"


printf '\n%-20s %s\n' \
    "Projet Compose :" "$PROJECT_NAME" \
    "Image :" "$NEW_WEB_IMAGE" \
    "Port privé :" "127.0.0.1:${APP_PORT}" \
    "URL :" "$FINAL_URL" \
    "Backup DB :" "$DB_BACKUP"


log "Services WHATASSOH uniquement :"


"${COMPOSE[@]}" ps