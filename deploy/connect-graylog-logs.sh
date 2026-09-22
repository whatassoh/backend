#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/opt/whatAssoh/backend"

ENV_FILE="${ROOT}/.env.production"
PROD_COMPOSE="${ROOT}/docker-compose.prod.yml"
MONITORING_COMPOSE="${ROOT}/docker-compose.monitoring.yml"
GRAYLOG_COMPOSE="${ROOT}/docker-compose.graylog.yml"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${ROOT}/.deploy-backups/graylog-logs-${STAMP}"

SUCCESS=0
BACKUP_READY=0
CURRENT_IMAGE=""

log() {
    printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
    echo
    echo "ERREUR: $*" >&2
    exit 1
}

COMPOSE=(
    docker-compose
    -p whatassoh
    --env-file "$ENV_FILE"
    -f "$PROD_COMPOSE"
    -f "$MONITORING_COMPOSE"
    -f "$GRAYLOG_COMPOSE"
)

# ==========================================================
# PREFLIGHT
# ==========================================================

[[ "$(id -u)" -eq 0 ]] || die "Exécute le script en root."

for cmd in docker docker-compose python3 curl; do
    command -v "$cmd" >/dev/null 2>&1 || die "Commande absente: $cmd"
done

for file in \
    "$ENV_FILE" \
    "$PROD_COMPOSE" \
    "$MONITORING_COMPOSE" \
    "$GRAYLOG_COMPOSE"
do
    [[ -f "$file" ]] || die "Fichier absent: $file"
done

docker info >/dev/null 2>&1 || die "Docker indisponible."

# ==========================================================
# GRAYLOG CHECK
# ==========================================================

log "Vérification Graylog"

GRAYLOG_CID="$("${COMPOSE[@]}" ps -q graylog)"

[[ -n "$GRAYLOG_CID" ]] || die "Conteneur Graylog introuvable."

GRAYLOG_RUNNING="$(
    docker inspect \
        -f '{{.State.Running}}' \
        "$GRAYLOG_CID"
)"

[[ "$GRAYLOG_RUNNING" == "true" ]] || die "Graylog n'est pas démarré."

# Vérifie que le port GELF est réellement publié sur localhost.
docker port "$GRAYLOG_CID" 12201/udp \
    | grep -q '127.0.0.1:12201' \
    || die "Le port GELF UDP 127.0.0.1:12201 n'est pas publié."

log "Graylog GELF UDP disponible sur 127.0.0.1:12201"

# ==========================================================
# CURRENT RELEASE CHECK
# ==========================================================

log "Vérification de la release actuelle"

declare -A IMAGES

for service in web worker beat; do

    cid="$("${COMPOSE[@]}" ps -q "$service")"

    [[ -n "$cid" ]] || die "Conteneur ${service} introuvable."

    running="$(
        docker inspect \
            -f '{{.State.Running}}' \
            "$cid"
    )"

    [[ "$running" == "true" ]] || die "${service} n'est pas Running."

    IMAGES["$service"]="$(
        docker inspect \
            -f '{{.Config.Image}}' \
            "$cid"
    )"

    printf '%-8s %s\n' \
        "$service" \
        "${IMAGES[$service]}"
done

if [[ "${IMAGES[web]}" != "${IMAGES[worker]}" ]] || \
   [[ "${IMAGES[web]}" != "${IMAGES[beat]}" ]]; then
    die "web/worker/beat n'utilisent pas la même image. Arrêt par sécurité."
fi

CURRENT_IMAGE="${IMAGES[web]}"

log "Image conservée: ${CURRENT_IMAGE}"

# ==========================================================
# BACKUP
# ==========================================================

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

cp -a \
    "$PROD_COMPOSE" \
    "${BACKUP_DIR}/docker-compose.prod.yml"

BACKUP_READY=1

log "Backup: ${BACKUP_DIR}"

# ==========================================================
# HELPERS
# ==========================================================

remove_service_container() {

    local service="$1"
    local timeout="$2"
    local cid

    cid="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"

    [[ -n "$cid" ]] || return 0

    log "Arrêt gracieux de ${service}"

    docker stop \
        --time "$timeout" \
        "$cid" \
        >/dev/null

    log "Suppression du conteneur ${service} uniquement"

    docker rm \
        "$cid" \
        >/dev/null
}

start_service() {

    local service="$1"

    log "Création de ${service} avec GELF"

    WEB_IMAGE="$CURRENT_IMAGE" \
        "${COMPOSE[@]}" \
        up -d --no-deps "$service"
}

wait_running() {

    local service="$1"
    local cid=""
    local running=""

    for _ in $(seq 1 40); do

        cid="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"

        if [[ -n "$cid" ]]; then
            running="$(
                docker inspect \
                    -f '{{.State.Running}}' \
                    "$cid" \
                    2>/dev/null || true
            )"

            if [[ "$running" == "true" ]]; then
                return 0
            fi
        fi

        sleep 2
    done

    return 1
}

# ==========================================================
# ROLLBACK
# ==========================================================

rollback() {

    [[ "$SUCCESS" -eq 0 ]] || return 0
    [[ "$BACKUP_READY" -eq 1 ]] || return 0

    echo
    echo "======================================================"
    echo "ROLLBACK : restauration de docker-compose.prod.yml"
    echo "======================================================"

    cp -a \
        "${BACKUP_DIR}/docker-compose.prod.yml" \
        "$PROD_COMPOSE"

    # Important:
    # on ne touche qu'aux 3 conteneurs applicatifs.
    for service in worker beat web; do

        cid="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"

        if [[ -n "$cid" ]]; then
            docker stop --time 60 "$cid" >/dev/null 2>&1 || true
            docker rm "$cid" >/dev/null 2>&1 || true
        fi

        WEB_IMAGE="$CURRENT_IMAGE" \
            "${COMPOSE[@]}" \
            up -d --no-deps "$service" \
            >/dev/null 2>&1 || true
    done

    echo "Rollback terminé."
}

trap rollback ERR

# ==========================================================
# PATCH docker-compose.prod.yml
# ==========================================================

log "Ajout du driver GELF à web / worker / beat"

python3 - "$PROD_COMPOSE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])

lines = path.read_text().splitlines(keepends=True)

targets = {"web", "worker", "beat"}

# Trouver services:
try:
    services_start = next(
        i for i, line in enumerate(lines)
        if re.match(r"^services:\s*(?:#.*)?$", line.rstrip("\n"))
    )
except StopIteration:
    raise SystemExit("Section services: introuvable.")

# Fin de services:
services_end = len(lines)

for i in range(services_start + 1, len(lines)):
    stripped = lines[i].rstrip("\n")

    if (
        stripped
        and not stripped.startswith((" ", "\t", "#"))
        and re.match(r"^[A-Za-z0-9_.-]+:\s*(?:#.*)?$", stripped)
    ):
        services_end = i
        break

# Tous les services au niveau 2 espaces.
service_starts = {}

for i in range(services_start + 1, services_end):
    m = re.match(
        r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$",
        lines[i].rstrip("\n"),
    )

    if m:
        service_starts[m.group(1)] = i

missing = targets - service_starts.keys()

if missing:
    raise SystemExit(
        "Services introuvables: " + ", ".join(sorted(missing))
    )

all_starts = sorted(service_starts.values())

def end_for(start):
    later = [x for x in all_starts if x > start]
    return min(later) if later else services_end

logging_block = [
    "\n",
    "    # Logs centralisés WhatAssoh -> Graylog\n",
    "    logging:\n",
    "      driver: gelf\n",
    "      options:\n",
    '        gelf-address: "udp://127.0.0.1:12201"\n',
    '        gelf-compression-type: "none"\n',
    '        mode: "non-blocking"\n',
    '        max-buffer-size: "4m"\n',
    '        tag: "whatassoh-{{.Name}}"\n',
]

# Travailler du bas vers le haut pour ne pas casser les index.
ordered = sorted(
    targets,
    key=lambda name: service_starts[name],
    reverse=True,
)

for service in ordered:

    start = service_starts[service]
    end = end_for(start)

    block = lines[start:end]

    logging_indexes = [
        i for i, line in enumerate(block)
        if re.match(r"^    logging:\s*(?:#.*)?$", line.rstrip("\n"))
    ]

    if logging_indexes:

        block_text = "".join(block)

        if (
            "driver: gelf" in block_text
            and "udp://127.0.0.1:12201" in block_text
        ):
            print(f"{service}: GELF déjà configuré.")
            continue

        raise SystemExit(
            f"{service}: une configuration logging existe déjà. "
            "Aucune modification supplémentaire effectuée."
        )

    lines[end:end] = logging_block

    print(f"{service}: GELF ajouté.")

path.write_text("".join(lines))
PY

# ==========================================================
# VALIDATION
# ==========================================================

log "Validation Docker Compose"

"${COMPOSE[@]}" config -q

log "Configuration Compose valide"

# ==========================================================
# RECREATE
#
# Compose 1.29.2 possède le bug ContainerConfig.
# On retire donc volontairement chaque ancien conteneur
# avant son 'up'.
# ==========================================================

# Worker d'abord : arrêt gracieux plus long pour laisser
# les tâches Celery en cours terminer.
remove_service_container worker 120
start_service worker
wait_running worker || die "worker n'est pas reparti."

# Beat ensuite.
remove_service_container beat 30
start_service beat
wait_running beat || die "beat n'est pas reparti."

# Web en dernier afin de minimiser l'interruption HTTP.
remove_service_container web 30
start_service web
wait_running web || die "web n'est pas reparti."

# ==========================================================
# WEB HEALTH
# ==========================================================

log "Vérification HTTP Django"

WEB_OK=0

for _ in $(seq 1 30); do

    CODE="$(
        curl \
            -sS \
            -o /dev/null \
            -w '%{http_code}' \
            --max-time 5 \
            http://127.0.0.1:8010/health/ \
            2>/dev/null || true
    )"

    if [[ "$CODE" == "200" ]]; then
        WEB_OK=1
        break
    fi

    sleep 2
done

[[ "$WEB_OK" -eq 1 ]] || die "Health Django non valide après recréation."

log "Django /health/ : HTTP 200"

# ==========================================================
# VERIFY LOG DRIVER
# ==========================================================

log "Vérification des drivers de logs"

for service in web worker beat; do

    cid="$("${COMPOSE[@]}" ps -q "$service")"

    driver="$(
        docker inspect \
            -f '{{.HostConfig.LogConfig.Type}}' \
            "$cid"
    )"

    address="$(
        docker inspect \
            -f '{{index .HostConfig.LogConfig.Config "gelf-address"}}' \
            "$cid"
    )"

    mode="$(
        docker inspect \
            -f '{{index .HostConfig.LogConfig.Config "mode"}}' \
            "$cid"
    )"

    printf '%-8s driver=%-5s address=%-28s mode=%s\n' \
        "$service" \
        "$driver" \
        "$address" \
        "$mode"

    [[ "$driver" == "gelf" ]] || die "${service}: driver GELF absent."

    [[ "$address" == "udp://127.0.0.1:12201" ]] || \
        die "${service}: mauvaise adresse GELF."
done

# ==========================================================
# TEST MARKERS
#
# Écriture directement dans stdout du PID 1.
# Cela passe par le vrai logging driver Docker.
# ==========================================================

log "Envoi de marqueurs dans les vrais flux stdout"

for service in web worker beat; do

    cid="$("${COMPOSE[@]}" ps -q "$service")"

    docker exec "$cid" \
        sh -c \
        "printf '%s\n' 'graylog_real_logs_connected service=${service}' > /proc/1/fd/1" \
        >/dev/null 2>&1 || true
done

# ==========================================================
# FINAL CHECK
# ==========================================================

log "État final des services"

"${COMPOSE[@]}" ps \
    web \
    worker \
    beat \
    postgres \
    redis \
    graylog

SUCCESS=1
trap - ERR

echo
echo "======================================================"
echo "GRAYLOG CONNECTÉ AUX LOGS WHATASSOH"
echo "======================================================"
echo
echo "web    -> GELF UDP -> Graylog"
echo "worker -> GELF UDP -> Graylog"
echo "beat   -> GELF UDP -> Graylog"
echo
echo "Recherche Graylog:"
echo
echo "    graylog_real_logs_connected"
echo
echo "Tu devrais voir 3 messages."
echo
echo "Backup:"
echo "    ${BACKUP_DIR}"
echo
