#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

PROJECT_NAME="whatassoh"

ROOT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

ENV_FILE="${ROOT_DIR}/.env.production"

PROD_COMPOSE="${ROOT_DIR}/docker-compose.prod.yml"
MONITORING_COMPOSE="${ROOT_DIR}/docker-compose.monitoring.yml"
GRAYLOG_COMPOSE="${ROOT_DIR}/docker-compose.graylog.yml"

log() {
    printf '\n[%s] %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" \
        "$*"
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || \
        die "Commande requise absente : $1"
}

# ----------------------------------------------------------
# PREFLIGHT
# ----------------------------------------------------------

[[ "$(uname -s)" == "Linux" ]] || \
    die "Ce script doit être exécuté sur le serveur Linux."

for cmd in \
    docker \
    docker-compose \
    curl \
    sysctl
do
    require_cmd "$cmd"
done

docker info >/dev/null 2>&1 || \
    die "Docker n'est pas accessible."

[[ -f "$ENV_FILE" ]] || \
    die ".env.production absent."

[[ -f "$PROD_COMPOSE" ]] || \
    die "docker-compose.prod.yml absent."

[[ -f "$MONITORING_COMPOSE" ]] || \
    die "docker-compose.monitoring.yml absent."

[[ -f "$GRAYLOG_COMPOSE" ]] || \
    die "docker-compose.graylog.yml absent."

# ----------------------------------------------------------
# DATANODE / OPENSEARCH REQUIREMENT
# ----------------------------------------------------------

MAX_MAP_COUNT="$(sysctl -n vm.max_map_count)"

if (( MAX_MAP_COUNT < 262144 )); then
    die "
vm.max_map_count=${MAX_MAP_COUNT}

Graylog Data Node exige au minimum :

vm.max_map_count=262144

Configure le serveur puis relance ce script.
"
fi

# ----------------------------------------------------------
# COMPOSE
# ----------------------------------------------------------

COMPOSE=(
    docker-compose
    -p "$PROJECT_NAME"
    --env-file "$ENV_FILE"
    -f "$PROD_COMPOSE"
    -f "$MONITORING_COMPOSE"
    -f "$GRAYLOG_COMPOSE"
)

log "Validation Docker Compose"

"${COMPOSE[@]}" config -q

# ----------------------------------------------------------
# DOWNLOAD OBSERVABILITY IMAGES
# ----------------------------------------------------------

log "Téléchargement des images d'observabilité"

"${COMPOSE[@]}" pull \
    prometheus \
    grafana \
    node-exporter \
    cadvisor \
    postgres-exporter \
    redis-exporter \
    mongodb \
    datanode \
    graylog

# ----------------------------------------------------------
# PROMETHEUS / GRAFANA
# ----------------------------------------------------------

log "Démarrage Prometheus / Grafana"

"${COMPOSE[@]}" up -d --no-deps \
    node-exporter \
    cadvisor \
    postgres-exporter \
    redis-exporter \
    prometheus \
    grafana

# ----------------------------------------------------------
# GRAYLOG STACK
# ----------------------------------------------------------

log "Démarrage MongoDB / Data Node / Graylog"

"${COMPOSE[@]}" up -d --no-deps \
    mongodb \
    datanode \
    graylog

# ----------------------------------------------------------
# WAIT GRAYLOG HTTP
# ----------------------------------------------------------

log "Attente de Graylog"

elapsed=0
timeout=240

while (( elapsed < timeout )); do

    code="$(
        curl \
            -sS \
            -o /dev/null \
            -w '%{http_code}' \
            --max-time 5 \
            http://127.0.0.1:9000/ \
            2>/dev/null || true
    )"

    if [[ "$code" =~ ^[1-4][0-9][0-9]$ ]]; then
        log "Graylog répond : HTTP ${code}"
        break
    fi

    sleep 5
    elapsed=$((elapsed + 5))
done

if (( elapsed >= timeout )); then

    "${COMPOSE[@]}" logs \
        --tail=150 \
        mongodb \
        datanode \
        graylog \
        >&2 || true

    die "Graylog ne répond pas après ${timeout}s."
fi

# ----------------------------------------------------------
# STATUS
# ----------------------------------------------------------

log "État des services"

"${COMPOSE[@]}" ps \
    prometheus \
    grafana \
    node-exporter \
    cadvisor \
    postgres-exporter \
    redis-exporter \
    mongodb \
    datanode \
    graylog

log "OBSERVABILITÉ DÉPLOYÉE"

printf '\nGraylog local serveur : http://127.0.0.1:9000/\n'
printf 'Grafana local serveur : http://127.0.0.1:3000/\n'
printf 'Prometheus local       : http://127.0.0.1:9090/\n'
