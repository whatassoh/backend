#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

# ==========================================================
# whatAssoh - HTTPS observability configuration
#
# Public URLs:
#   https://DOMAIN/grafana/
#   https://DOMAIN/graylog/
#
# This script NEVER recreates:
#   web / worker / beat / postgres / redis
# ==========================================================

DOMAIN="${1:-whatasso.dvybes.com}"

ROOT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

ENV_FILE="${ROOT_DIR}/.env.production"
PROD_COMPOSE="${ROOT_DIR}/docker-compose.prod.yml"
MONITORING_COMPOSE="${ROOT_DIR}/docker-compose.monitoring.yml"
GRAYLOG_COMPOSE="${ROOT_DIR}/docker-compose.graylog.yml"
DEPLOY_SCRIPT="${ROOT_DIR}/deploy/deploy.sh"

NGINX_SITE="/etc/nginx/sites-available/whatassoh.conf"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${ROOT_DIR}/.deploy-backups/observability-https-${STAMP}"

BACKUP_READY=0
SUCCESS=0

# ----------------------------------------------------------
# LOGGING
# ----------------------------------------------------------

log() {
    printf '\n[%s] %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" \
        "$*"
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}

# ----------------------------------------------------------
# PREFLIGHT
# ----------------------------------------------------------

[[ "$(uname -s)" == "Linux" ]] || \
    die "Ce script doit être exécuté sur le serveur Linux."

[[ "$(id -u)" -eq 0 ]] || \
    die "Lance ce script en root ou avec sudo."

for cmd in \
    docker \
    docker-compose \
    nginx \
    systemctl \
    curl \
    python3 \
    cp \
    chmod \
    grep
do
    command -v "$cmd" >/dev/null 2>&1 || \
        die "Commande absente : $cmd"
done

docker info >/dev/null 2>&1 || \
    die "Docker n'est pas accessible."

for f in \
    "$ENV_FILE" \
    "$PROD_COMPOSE" \
    "$MONITORING_COMPOSE" \
    "$GRAYLOG_COMPOSE" \
    "$DEPLOY_SCRIPT" \
    "$NGINX_SITE"
do
    [[ -f "$f" ]] || \
        die "Fichier absent : $f"
done

# Refuse de travailler sur un deploy.sh contenant encore
# des marqueurs de conflit Git.
if grep -Eq '^(<<<<<<<|=======|>>>>>>>)' "$DEPLOY_SCRIPT"; then
    die "deploy/deploy.sh contient encore des marqueurs de conflit Git."
fi


# ----------------------------------------------------------
# DOCKER-COMPOSE V1 RECREATE WORKAROUND
#
# docker-compose 1.29.2 peut échouer avec:
# KeyError: 'ContainerConfig'
#
# On supprime UNIQUEMENT les conteneurs Grafana/Graylog.
# Les volumes et toutes les autres ressources sont conservés.
# ----------------------------------------------------------

remove_observability_containers() {

    local service
    local ids
    local cid

    for service in grafana graylog; do

        ids="$(
            docker ps -aq \
                --filter "label=com.docker.compose.project=whatassoh" \
                --filter "label=com.docker.compose.service=${service}" \
                2>/dev/null || true
        )"

        [[ -n "$ids" ]] || continue

        log "Suppression de l'ancien conteneur ${service} uniquement"

        while IFS= read -r cid; do

            [[ -n "$cid" ]] || continue

            docker rm -f "$cid"

        done <<< "$ids"

    done
}

# ----------------------------------------------------------
# BACKUP
# ----------------------------------------------------------

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

cp -a "$ENV_FILE" \
    "${BACKUP_DIR}/env.production"

cp -a "$MONITORING_COMPOSE" \
    "${BACKUP_DIR}/docker-compose.monitoring.yml"

cp -a "$GRAYLOG_COMPOSE" \
    "${BACKUP_DIR}/docker-compose.graylog.yml"

cp -a "$DEPLOY_SCRIPT" \
    "${BACKUP_DIR}/deploy.sh"

cp -a "$NGINX_SITE" \
    "${BACKUP_DIR}/whatassoh.conf"

BACKUP_READY=1

log "Backup créé : ${BACKUP_DIR}"

# ----------------------------------------------------------
# ROLLBACK
# ----------------------------------------------------------

rollback() {

    [[ "$BACKUP_READY" -eq 1 ]] || return 0
    [[ "$SUCCESS" -eq 0 ]] || return 0

    printf '\n[ROLLBACK] Restauration de la configuration précédente...\n' >&2

    cp -a \
        "${BACKUP_DIR}/env.production" \
        "$ENV_FILE"

    cp -a \
        "${BACKUP_DIR}/docker-compose.monitoring.yml" \
        "$MONITORING_COMPOSE"

    cp -a \
        "${BACKUP_DIR}/docker-compose.graylog.yml" \
        "$GRAYLOG_COMPOSE"

    cp -a \
        "${BACKUP_DIR}/deploy.sh" \
        "$DEPLOY_SCRIPT"

    cp -a \
        "${BACKUP_DIR}/whatassoh.conf" \
        "$NGINX_SITE"

    chmod 600 "$ENV_FILE"
    chmod +x "$DEPLOY_SCRIPT"

    # Recrée uniquement les deux services éventuellement
    # modifiés par ce script.
    #
    # Compose V1 workaround: supprimer uniquement leurs
    # conteneurs avant recréation. Les volumes sont conservés.
    remove_observability_containers || true

    docker-compose \
        -p whatassoh \
        --env-file "$ENV_FILE" \
        -f "$PROD_COMPOSE" \
        -f "$MONITORING_COMPOSE" \
        -f "$GRAYLOG_COMPOSE" \
        up -d --no-deps \
        grafana \
        graylog \
        >/dev/null 2>&1 || true

    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx || true
    fi

    printf '[ROLLBACK] Terminé.\n' >&2
}

trap rollback ERR

# ----------------------------------------------------------
# .env.production
# ----------------------------------------------------------

log "Configuration de l'URL publique"

python3 - "$ENV_FILE" "$DOMAIN" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
domain = sys.argv[2]

key = "OBSERVABILITY_PUBLIC_URL"
value = f"https://{domain}"

text = path.read_text()
lines = text.splitlines()

result = []
found = False

for line in lines:
    if line.startswith(f"{key}="):
        result.append(f"{key}={value}")
        found = True
    else:
        result.append(line)

if not found:
    if result and result[-1] != "":
        result.append("")
    result.append("# Observability public URL")
    result.append(f"{key}={value}")

path.write_text("\n".join(result) + "\n")
path.chmod(0o600)

print(f"{key} configuré.")
PY

# ----------------------------------------------------------
# GRAFANA COMPOSE
# ----------------------------------------------------------

log "Configuration Grafana pour /grafana/"

python3 - "$MONITORING_COMPOSE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text()

settings = {
    "GF_SERVER_ROOT_URL":
        "${OBSERVABILITY_PUBLIC_URL}/grafana/",
    "GF_SERVER_SERVE_FROM_SUB_PATH":
        '"true"',
    "GF_SERVER_PROTOCOL":
        "http",
}

for key, value in settings.items():

    pattern = re.compile(
        rf"^(\s+){re.escape(key)}:\s*.*$",
        re.MULTILINE,
    )

    match = pattern.search(text)

    if match:
        indent = match.group(1)
        text = pattern.sub(
            f"{indent}{key}: {value}",
            text,
            count=1,
        )
        continue

    anchor = re.search(
        r'^(\s+)GF_USERS_ALLOW_SIGN_UP:\s*.*$',
        text,
        re.MULTILINE,
    )

    if not anchor:
        raise SystemExit(
            "Impossible de trouver environment Grafana."
        )

    indent = anchor.group(1)

    insertion = (
        anchor.group(0)
        + f"\n{indent}{key}: {value}"
    )

    text = (
        text[:anchor.start()]
        + insertion
        + text[anchor.end():]
    )

path.write_text(text)

print("Grafana configuré.")
PY

# ----------------------------------------------------------
# GRAYLOG COMPOSE
# ----------------------------------------------------------

log "Configuration Graylog pour /graylog/"

python3 - "$GRAYLOG_COMPOSE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text()

replacement = (
    "      GRAYLOG_HTTP_EXTERNAL_URI: "
    "${OBSERVABILITY_PUBLIC_URL}/graylog/"
)

pattern = re.compile(
    r"^\s+GRAYLOG_HTTP_EXTERNAL_URI:\s*.*$",
    re.MULTILINE,
)

if pattern.search(text):
    text = pattern.sub(
        replacement,
        text,
        count=1,
    )
else:
    anchor = re.search(
        r"^\s+GRAYLOG_HTTP_BIND_ADDRESS:\s*.*$",
        text,
        re.MULTILINE,
    )

    if not anchor:
        raise SystemExit(
            "GRAYLOG_HTTP_BIND_ADDRESS introuvable."
        )

    text = (
        text[:anchor.end()]
        + "\n"
        + replacement
        + text[anchor.end():]
    )

path.write_text(text)

print("Graylog configuré.")
PY

# ----------------------------------------------------------
# NGINX BLOCK - VERSION ACTIVE
# ----------------------------------------------------------

log "Configuration du Nginx actif"

python3 - "$NGINX_SITE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

begin = "    # WHATASSOH OBSERVABILITY HTTPS BEGIN"
end = "    # WHATASSOH OBSERVABILITY HTTPS END"

block = r'''    # WHATASSOH OBSERVABILITY HTTPS BEGIN

    # ------------------------------------------------------
    # Grafana
    # ------------------------------------------------------

    location = /grafana {
        return 301 /grafana/;
    }

    location ^~ /grafana/api/live/ {
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        rewrite ^/grafana/(.*)$ /$1 break;

        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 120s;
    }

    location ^~ /grafana/ {
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        rewrite ^/grafana/(.*)$ /$1 break;

        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 120s;
    }

    # ------------------------------------------------------
    # Graylog
    # ------------------------------------------------------

    location = /graylog {
        return 301 /graylog/;
    }

    location ^~ /graylog/ {
        proxy_http_version 1.1;

        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Server $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_set_header X-Graylog-Server-URL https://$host/graylog/;

        rewrite ^/graylog/(.*)$ /$1 break;

        proxy_pass http://127.0.0.1:9000;
        proxy_read_timeout 120s;
    }

    # WHATASSOH OBSERVABILITY HTTPS END

'''

# Idempotence
if begin in text:
    print("Bloc observabilité déjà présent dans Nginx.")
    raise SystemExit(0)

https_pos = text.find("listen 443 ssl http2;")

if https_pos == -1:
    raise SystemExit(
        "Bloc HTTPS Nginx introuvable."
    )

location_pos = text.find(
    "    location / {",
    https_pos,
)

if location_pos == -1:
    raise SystemExit(
        "location / HTTPS introuvable."
    )

text = (
    text[:location_pos]
    + block
    + text[location_pos:]
)

path.write_text(text)

print("Nginx actif configuré.")
PY

# ----------------------------------------------------------
# deploy/deploy.sh
#
# Important:
# le Nginx est généré à chaque déploiement.
# Il faut donc rendre la modification persistante.
# ----------------------------------------------------------

log "Mise à jour du générateur Nginx dans deploy.sh"

python3 - "$DEPLOY_SCRIPT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

begin = "    # WHATASSOH OBSERVABILITY HTTPS BEGIN"

# IMPORTANT :
# Ce contenu se trouve dans un heredoc Bash.
# Les variables Nginx doivent donc être échappées avec \$.
block = r'''    # WHATASSOH OBSERVABILITY HTTPS BEGIN

    # ------------------------------------------------------
    # Grafana
    # ------------------------------------------------------

    location = /grafana {
        return 301 /grafana/;
    }

    location ^~ /grafana/api/live/ {
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        rewrite ^/grafana/(.*)\$ /\$1 break;

        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 120s;
    }

    location ^~ /grafana/ {
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        rewrite ^/grafana/(.*)\$ /\$1 break;

        proxy_pass http://127.0.0.1:3000;
        proxy_read_timeout 120s;
    }

    # ------------------------------------------------------
    # Graylog
    # ------------------------------------------------------

    location = /graylog {
        return 301 /graylog/;
    }

    location ^~ /graylog/ {
        proxy_http_version 1.1;

        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Server \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_set_header X-Graylog-Server-URL https://\$host/graylog/;

        rewrite ^/graylog/(.*)\$ /\$1 break;

        proxy_pass http://127.0.0.1:9000;
        proxy_read_timeout 120s;
    }

    # WHATASSOH OBSERVABILITY HTTPS END

'''

if begin in text:
    print("deploy.sh possède déjà le bloc observabilité.")
    raise SystemExit(0)

https_pos = text.find(
    "    listen 443 ssl http2;"
)

if https_pos == -1:
    raise SystemExit(
        "Bloc HTTPS généré par deploy.sh introuvable."
    )

location_pos = text.find(
    "    location / {",
    https_pos,
)

if location_pos == -1:
    raise SystemExit(
        "location / HTTPS de deploy.sh introuvable."
    )

text = (
    text[:location_pos]
    + block
    + text[location_pos:]
)

path.write_text(text)

print("deploy.sh configuré.")
PY

chmod +x "$DEPLOY_SCRIPT"

# ----------------------------------------------------------
# VALIDATION BASH
# ----------------------------------------------------------

log "Validation syntaxique deploy.sh"

bash -n "$DEPLOY_SCRIPT"

# ----------------------------------------------------------
# COMPOSE
# ----------------------------------------------------------

COMPOSE=(
    docker-compose
    -p whatassoh
    --env-file "$ENV_FILE"
    -f "$PROD_COMPOSE"
    -f "$MONITORING_COMPOSE"
    -f "$GRAYLOG_COMPOSE"
)

log "Validation Docker Compose"

"${COMPOSE[@]}" config -q

# ----------------------------------------------------------
# NGINX VALIDATION
# ----------------------------------------------------------

log "Validation Nginx AVANT reload"

nginx -t

# ----------------------------------------------------------
# RECREATE ONLY GRAFANA + GRAYLOG
# ----------------------------------------------------------

log "Reconfiguration de Grafana et Graylog uniquement"

# docker-compose 1.29.2 + Docker récent:
# évite KeyError: ContainerConfig.
#
# IMPORTANT:
# seuls les conteneurs Grafana et Graylog sont supprimés.
# Les volumes, MongoDB, Data Node, Prometheus, Django,
# worker, beat, PostgreSQL et Redis ne sont pas touchés.
remove_observability_containers

"${COMPOSE[@]}" up \
    -d \
    --no-deps \
    grafana \
    graylog

# ----------------------------------------------------------
# WAIT GRAFANA
# ----------------------------------------------------------

log "Attente de Grafana"

grafana_ok=0

for _ in $(seq 1 40); do

    code="$(
        curl \
            -sS \
            -o /dev/null \
            -w '%{http_code}' \
            --max-time 5 \
            http://127.0.0.1:3000/grafana/api/health \
            2>/dev/null || true
    )"

    if [[ "$code" == "200" ]]; then
        grafana_ok=1
        break
    fi

    sleep 3
done

[[ "$grafana_ok" -eq 1 ]] || \
    die "Grafana ne répond pas correctement."

log "Grafana local : OK"

# ----------------------------------------------------------
# WAIT GRAYLOG
# ----------------------------------------------------------

log "Attente de Graylog"

graylog_ok=0

for _ in $(seq 1 60); do

    code="$(
        curl \
            -sS \
            -o /dev/null \
            -w '%{http_code}' \
            --max-time 5 \
            http://127.0.0.1:9000/ \
            2>/dev/null || true
    )"

    case "$code" in
        200|301|302|303|307|308|401|403)
            graylog_ok=1
            break
            ;;
    esac

    sleep 3
done

[[ "$graylog_ok" -eq 1 ]] || \
    die "Graylog ne répond pas correctement."

log "Graylog local : OK (HTTP ${code})"

# ----------------------------------------------------------
# ACTIVATE NGINX
# ----------------------------------------------------------

log "Reload Nginx"

nginx -t
systemctl reload nginx

# ----------------------------------------------------------
# PUBLIC TESTS
# ----------------------------------------------------------

log "Tests HTTPS publics"

sleep 2

test_public_url() {

    local name="$1"
    local url="$2"
    local code

    code="$(
        curl \
            -sS \
            -o /dev/null \
            -w '%{http_code}' \
            --max-time 15 \
            "$url" \
            2>/dev/null || true
    )"

    case "$code" in
        200|301|302|303|307|308|401|403)
            printf '%-10s HTTP %s  %s\n' \
                "$name" \
                "$code" \
                "$url"
            ;;
        *)
            die "
${name} inaccessible.

URL  : ${url}
HTTP : ${code:-000}
"
            ;;
    esac
}

test_public_url \
    "Grafana" \
    "https://${DOMAIN}/grafana/"

test_public_url \
    "Graylog" \
    "https://${DOMAIN}/graylog/"

# ----------------------------------------------------------
# ENSURE APP SERVICES WERE NOT RECREATED
# ----------------------------------------------------------

log "État des services applicatifs"

"${COMPOSE[@]}" ps \
    web \
    worker \
    beat \
    postgres \
    redis

# ----------------------------------------------------------
# SUCCESS
# ----------------------------------------------------------

SUCCESS=1
trap - ERR

log "CONFIGURATION HTTPS TERMINÉE"

printf '\n'
printf 'Grafana : https://%s/grafana/\n' "$DOMAIN"
printf 'Graylog : https://%s/graylog/\n' "$DOMAIN"
printf '\n'
printf 'Backup : %s\n' "$BACKUP_DIR"
printf '\n'
printf 'Prometheus reste privé sur 127.0.0.1:9090.\n'
