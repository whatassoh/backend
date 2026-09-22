#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

ROOT="/opt/whatAssoh/backend"
ENV_FILE="${ROOT}/.env.production"

GRAFANA_URL="http://127.0.0.1:3000/grafana"
DASHBOARD_UID="whatassoh-prod-overview"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${ROOT}/.deploy-backups/grafana-${STAMP}"

log() {
    printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
    echo "ERREUR: $*" >&2
    exit 1
}

cd "$ROOT"

[[ -f "$ENV_FILE" ]] || die ".env.production introuvable"

for cmd in curl python3; do
    command -v "$cmd" >/dev/null 2>&1 || die "$cmd absent"
done

# ----------------------------------------------------------
# PASSWORD
# ----------------------------------------------------------

GRAFANA_PASS="$(
    grep '^GRAFANA_ADMIN_PASSWORD=' "$ENV_FILE" |
    cut -d= -f2-
)"

[[ -n "$GRAFANA_PASS" ]] || \
    die "GRAFANA_ADMIN_PASSWORD absent."

# ----------------------------------------------------------
# VERIFY GRAFANA
# ----------------------------------------------------------

log "Vérification Grafana"

curl -fsS \
    "${GRAFANA_URL}/api/health" \
    >/dev/null

log "Grafana répond correctement"

# ----------------------------------------------------------
# DATASOURCE PROMETHEUS
# ----------------------------------------------------------

log "Recherche de la datasource Prometheus"

DATASOURCE_JSON="$(
    curl -fsS \
        -u "admin:${GRAFANA_PASS}" \
        "${GRAFANA_URL}/api/datasources/name/Prometheus"
)"

DATASOURCE_UID="$(
    printf '%s' "$DATASOURCE_JSON" |
    python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d["uid"])
'
)"

[[ -n "$DATASOURCE_UID" ]] || \
    die "UID Prometheus introuvable."

echo "Datasource Prometheus : OK"

# ----------------------------------------------------------
# BACKUP EXISTING DASHBOARD
# ----------------------------------------------------------

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

HTTP_CODE="$(
    curl -sS \
        -u "admin:${GRAFANA_PASS}" \
        -o "${BACKUP_DIR}/dashboard-existing.json" \
        -w '%{http_code}' \
        "${GRAFANA_URL}/api/dashboards/uid/${DASHBOARD_UID}"
)"

if [[ "$HTTP_CODE" == "200" ]]; then
    log "Dashboard existant sauvegardé"
else
    rm -f "${BACKUP_DIR}/dashboard-existing.json"
    log "Aucun dashboard précédent à sauvegarder"
fi

# ----------------------------------------------------------
# GENERATE DASHBOARD
# ----------------------------------------------------------

DASHBOARD_FILE="/tmp/whatassoh-production-overview.json"

log "Génération du dashboard"

python3 - "$DATASOURCE_UID" "$DASHBOARD_FILE" <<'PY'
import json
import sys

uid = sys.argv[1]
outfile = sys.argv[2]

DS = {
    "type": "prometheus",
    "uid": uid,
}


def target(expr, ref="A", legend="", instant=False):
    return {
        "datasource": DS,
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "instant": instant,
        "refId": ref,
    }


def thresholds(percent=False):
    return {
        "mode": "absolute",
        "steps": [
            {"color": "green", "value": None},
            {"color": "yellow", "value": 70 if percent else 1},
            {"color": "red", "value": 85 if percent else 2},
        ],
    }


def stat(
    pid,
    title,
    expr,
    x,
    y,
    w,
    h=4,
    unit="short",
    mappings=None,
    threshold_data=None,
):
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "datasource": DS,
        "gridPos": {
            "x": x,
            "y": y,
            "w": w,
            "h": h,
        },
        "targets": [
            target(expr, instant=True),
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "mappings": mappings or [],
                "thresholds": threshold_data or thresholds(),
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "auto",
            "wideLayout": True,
        },
    }


def up_stat(pid, title, job, x):
    return stat(
        pid,
        title,
        f'up{{job="{job}"}}',
        x,
        0,
        4,
        mappings=[
            {
                "type": "value",
                "options": {
                    "0": {
                        "text": "DOWN",
                        "color": "red",
                    },
                    "1": {
                        "text": "UP",
                        "color": "green",
                    },
                },
            }
        ],
        threshold_data={
            "mode": "absolute",
            "steps": [
                {"color": "red", "value": None},
                {"color": "green", "value": 1},
            ],
        },
    )


def timeseries(
    pid,
    title,
    expr,
    x,
    y,
    w,
    h=8,
    unit="short",
    legend="",
):
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "gridPos": {
            "x": x,
            "y": y,
            "w": w,
            "h": h,
        },
        "targets": [
            target(expr, legend=legend),
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {
                "mode": "multi",
                "sort": "desc",
            },
        },
    }


panels = []

# ==========================================================
# TOP STATUS
# ==========================================================

panels.append(
    stat(
        1,
        "Monitoring disponible",
        "100 * sum(up) / count(up)",
        0,
        0,
        4,
        unit="percent",
        threshold_data={
            "mode": "absolute",
            "steps": [
                {"color": "red", "value": None},
                {"color": "yellow", "value": 90},
                {"color": "green", "value": 100},
            ],
        },
    )
)

panels.append(
    up_stat(
        2,
        "API Django",
        "whatassoh-django",
        4,
    )
)

panels.append(
    up_stat(
        3,
        "PostgreSQL",
        "whatassoh-postgres",
        8,
    )
)

panels.append(
    up_stat(
        4,
        "Redis",
        "whatassoh-redis",
        12,
    )
)

panels.append(
    stat(
        5,
        "CPU serveur",
        '''
100 - (
  avg(
    rate(
      node_cpu_seconds_total{
        job="whatassoh-node",
        mode="idle"
      }[5m]
    )
  ) * 100
)
'''.strip(),
        16,
        0,
        4,
        unit="percent",
        threshold_data=thresholds(percent=True),
    )
)

panels.append(
    stat(
        6,
        "RAM serveur",
        '''
100 * (
  1 -
  (
    node_memory_MemAvailable_bytes{job="whatassoh-node"}
    /
    node_memory_MemTotal_bytes{job="whatassoh-node"}
  )
)
'''.strip(),
        20,
        0,
        4,
        unit="percent",
        threshold_data=thresholds(percent=True),
    )
)

# ==========================================================
# DJANGO
# ==========================================================

django_requests = r'''
sum(
  rate(
    {
      __name__=~"whatassoh_django_http_requests_before_middlewares_total|django_http_requests_before_middlewares_total"
    }[5m]
  )
)
'''.strip()

django_status = r'''
sum by (status) (
  rate(
    {
      __name__=~"whatassoh_django_http_responses_total_by_status|django_http_responses_total_by_status"
    }[5m]
  )
)
'''.strip()

django_5xx = r'''
sum(
  rate(
    {
      __name__=~"whatassoh_django_http_responses_total_by_status|django_http_responses_total_by_status",
      status=~"5.."
    }[5m]
  )
)
'''.strip()

django_p95 = r'''
histogram_quantile(
  0.95,
  sum by (le) (
    rate(
      {
        __name__=~"whatassoh_django_http_requests_latency_including_middlewares_seconds_bucket|django_http_requests_latency_including_middlewares_seconds_bucket"
      }[5m]
    )
  )
)
'''.strip()

panels.append(
    timeseries(
        7,
        "Django - requêtes / seconde",
        django_requests,
        0,
        4,
        8,
        unit="reqps",
    )
)

panels.append(
    timeseries(
        8,
        "Django - réponses par statut",
        django_status,
        8,
        4,
        8,
        unit="reqps",
        legend="HTTP {{status}}",
    )
)

panels.append(
    timeseries(
        9,
        "Django - latence p95",
        django_p95,
        16,
        4,
        8,
        unit="s",
    )
)

# ==========================================================
# SERVER
# ==========================================================

cpu_history = r'''
100 - (
  avg(
    rate(
      node_cpu_seconds_total{
        job="whatassoh-node",
        mode="idle"
      }[5m]
    )
  ) * 100
)
'''.strip()

ram_history = r'''
100 * (
  1 -
  (
    node_memory_MemAvailable_bytes{job="whatassoh-node"}
    /
    node_memory_MemTotal_bytes{job="whatassoh-node"}
  )
)
'''.strip()

disk_history = r'''
100 * (
  1 -
  (
    node_filesystem_avail_bytes{
      job="whatassoh-node",
      mountpoint="/",
      fstype!~"tmpfs|overlay"
    }
    /
    node_filesystem_size_bytes{
      job="whatassoh-node",
      mountpoint="/",
      fstype!~"tmpfs|overlay"
    }
  )
)
'''.strip()

panels.append(
    timeseries(
        10,
        "Serveur - CPU",
        cpu_history,
        0,
        12,
        8,
        unit="percent",
    )
)

panels.append(
    timeseries(
        11,
        "Serveur - RAM",
        ram_history,
        8,
        12,
        8,
        unit="percent",
    )
)

panels.append(
    timeseries(
        12,
        "Serveur - disque /",
        disk_history,
        16,
        12,
        8,
        unit="percent",
    )
)

# ==========================================================
# DATABASE / CACHE
# ==========================================================

postgres_connections = r'''
sum(
  pg_stat_database_numbackends{
    job="whatassoh-postgres"
  }
)
'''.strip()

redis_memory = r'''
redis_memory_used_bytes{
  job="whatassoh-redis"
}
'''.strip()

django_db_qps = r'''
sum(
  rate(
    {
      __name__=~"whatassoh_django_db_execute_total|django_db_execute_total"
    }[5m]
  )
)
'''.strip()

panels.append(
    timeseries(
        13,
        "PostgreSQL - connexions",
        postgres_connections,
        0,
        20,
        8,
        unit="short",
    )
)

panels.append(
    timeseries(
        14,
        "Redis - mémoire utilisée",
        redis_memory,
        8,
        20,
        8,
        unit="bytes",
    )
)

panels.append(
    timeseries(
        15,
        "Django - requêtes DB / seconde",
        django_db_qps,
        16,
        20,
        8,
        unit="ops",
    )
)

# ==========================================================
# ERRORS / CONTAINERS
# ==========================================================

panels.append(
    timeseries(
        16,
        "Django - erreurs HTTP 5xx",
        django_5xx,
        0,
        28,
        8,
        unit="reqps",
    )
)

container_cpu = r'''
sum by (name) (
  rate(
    container_cpu_usage_seconds_total{
      job="whatassoh-containers",
      image!=""
    }[5m]
  )
) * 100
'''.strip()

container_ram = r'''
sum by (name) (
  container_memory_working_set_bytes{
    job="whatassoh-containers",
    image!=""
  }
)
'''.strip()

panels.append(
    timeseries(
        17,
        "Conteneurs - CPU",
        container_cpu,
        8,
        28,
        8,
        unit="percent",
        legend="{{name}}",
    )
)

panels.append(
    timeseries(
        18,
        "Conteneurs - RAM",
        container_ram,
        16,
        28,
        8,
        unit="bytes",
        legend="{{name}}",
    )
)

dashboard = {
    "id": None,
    "uid": "whatassoh-prod-overview",
    "title": "WhatAssoh - Production Overview",
    "description": (
        "Vue générale de la production WhatAssoh : "
        "Django, PostgreSQL, Redis, serveur et conteneurs."
    ),
    "tags": [
        "whatassoh",
        "production",
        "prometheus",
    ],
    "timezone": "browser",
    "schemaVersion": 41,
    "version": 0,
    "refresh": "15s",
    "time": {
        "from": "now-1h",
        "to": "now",
    },
    "timepicker": {},
    "templating": {
        "list": [],
    },
    "annotations": {
        "list": [],
    },
    "panels": panels,
}

payload = {
    "dashboard": dashboard,
    "overwrite": True,
    "message": "WhatAssoh production monitoring dashboard",
}

with open(outfile, "w") as f:
    json.dump(payload, f, indent=2)

print(f"Dashboard généré : {outfile}")
PY

# ----------------------------------------------------------
# VALIDATE JSON
# ----------------------------------------------------------

python3 -m json.tool "$DASHBOARD_FILE" >/dev/null

log "JSON valide"

# ----------------------------------------------------------
# IMPORT INTO GRAFANA
# ----------------------------------------------------------

log "Import dans Grafana"

RESPONSE="$(
    curl -fsS \
        -u "admin:${GRAFANA_PASS}" \
        -H 'Content-Type: application/json' \
        --data-binary "@${DASHBOARD_FILE}" \
        "${GRAFANA_URL}/api/dashboards/db"
)"

printf '%s\n' "$RESPONSE" |
python3 -m json.tool

STATUS="$(
    printf '%s' "$RESPONSE" |
    python3 -c '
import json, sys
print(json.load(sys.stdin).get("status", ""))
'
)"

[[ "$STATUS" == "success" ]] || \
    die "Grafana n'a pas confirmé la création."

log "DASHBOARD CRÉÉ AVEC SUCCÈS"

echo
echo "======================================================"
echo "WhatAssoh - Production Overview"
echo "======================================================"
echo
echo "https://whatasso.dvybes.com/grafana/d/whatassoh-prod-overview/"
echo
echo
echo "Backup éventuel :"
echo "$BACKUP_DIR"
echo
