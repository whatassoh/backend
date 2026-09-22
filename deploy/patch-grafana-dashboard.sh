#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/opt/whatAssoh/backend"
ENV_FILE="${ROOT}/.env.production"

GRAFANA_URL="http://127.0.0.1:3000/grafana"
DASHBOARD_UID="whatassoh-prod-overview"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${ROOT}/.deploy-backups/grafana-patch-${STAMP}"

log() {
    printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
    echo "ERREUR: $*" >&2
    exit 1
}

cd "$ROOT"

[[ -f "$ENV_FILE" ]] || die ".env.production introuvable"

GRAFANA_PASS="$(
    grep '^GRAFANA_ADMIN_PASSWORD=' "$ENV_FILE" |
    cut -d= -f2-
)"

[[ -n "$GRAFANA_PASS" ]] || die "Mot de passe Grafana absent."

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

BACKUP_FILE="${BACKUP_DIR}/dashboard-before.json"
PATCHED_FILE="/tmp/whatassoh-dashboard-patched.json"

# ==========================================================
# BACKUP
# ==========================================================

log "Sauvegarde du dashboard actuel"

curl -fsS \
  -u "admin:${GRAFANA_PASS}" \
  "${GRAFANA_URL}/api/dashboards/uid/${DASHBOARD_UID}" \
  > "$BACKUP_FILE"

python3 -m json.tool "$BACKUP_FILE" >/dev/null

log "Backup créé : ${BACKUP_FILE}"

# ==========================================================
# PATCH JSON
# ==========================================================

log "Correction des panneaux"

python3 - "$BACKUP_FILE" "$PATCHED_FILE" <<'PY'
import json
import sys

source = sys.argv[1]
destination = sys.argv[2]

with open(source) as f:
    raw = json.load(f)

dashboard = raw["dashboard"]

found = set()


def patch_target(panel, expression, legend=""):
    if not panel.get("targets"):
        raise RuntimeError(
            f"Panel sans target: {panel.get('title')}"
        )

    target = panel["targets"][0]

    target["expr"] = expression
    target["editorMode"] = "code"
    target["legendFormat"] = legend
    target["range"] = True
    target["instant"] = False


for panel in dashboard.get("panels", []):

    title = panel.get("title", "")

    # ------------------------------------------------------
    # Django responses by HTTP status
    # ------------------------------------------------------

    if title == "Django - réponses par statut":

        patch_target(
            panel,
            '''
sum by (status) (
  rate(
    whatassoh_django_http_responses_total_by_status_total[5m]
  )
)
'''.strip(),
            "HTTP {{status}}",
        )

        found.add(title)

    # ------------------------------------------------------
    # Django 5xx:
    # absence de série = 0 erreurs.
    # ------------------------------------------------------

    elif title == "Django - erreurs HTTP 5xx":

        patch_target(
            panel,
            '''
sum(
  rate(
    whatassoh_django_http_responses_total_by_status_total{
      status=~"5.."
    }[5m]
  )
) or vector(0)
'''.strip(),
            "5xx / sec",
        )

        panel.setdefault(
            "fieldConfig", {}
        ).setdefault(
            "defaults", {}
        )["noValue"] = "0"

        found.add(title)

    # ------------------------------------------------------
    # La métrique django_db n'existe actuellement pas.
    # On remplace par une vraie métrique postgres_exporter.
    # ------------------------------------------------------

    elif title == "Django - requêtes DB / seconde":

        panel["title"] = "PostgreSQL - transactions / seconde"

        patch_target(
            panel,
            '''
sum(
  rate(
    pg_stat_database_xact_commit{
      job="whatassoh-postgres"
    }[5m]
  )
)
+
sum(
  rate(
    pg_stat_database_xact_rollback{
      job="whatassoh-postgres"
    }[5m]
  )
)
'''.strip(),
            "Transactions / sec",
        )

        found.add(title)

    # ------------------------------------------------------
    # CPU containers:
    # uniquement whatassoh
    # ------------------------------------------------------

    elif title == "Conteneurs - CPU":

        patch_target(
            panel,
            '''
sum by (name) (
  rate(
    container_cpu_usage_seconds_total{
      job="whatassoh-containers",
      image!="",
      name=~"whatassoh_.*"
    }[5m]
  )
) * 100
'''.strip(),
            "{{name}}",
        )

        found.add(title)

    # ------------------------------------------------------
    # RAM containers:
    # uniquement whatassoh
    # ------------------------------------------------------

    elif title == "Conteneurs - RAM":

        patch_target(
            panel,
            '''
sum by (name) (
  container_memory_working_set_bytes{
    job="whatassoh-containers",
    image!="",
    name=~"whatassoh_.*"
  }
)
'''.strip(),
            "{{name}}",
        )

        found.add(title)


required = {
    "Django - réponses par statut",
    "Django - erreurs HTTP 5xx",
    "Django - requêtes DB / seconde",
    "Conteneurs - CPU",
    "Conteneurs - RAM",
}

missing = required - found

if missing:
    raise RuntimeError(
        "Panels introuvables: " + ", ".join(sorted(missing))
    )

# Grafana ne doit pas recevoir l'ancien id interne.
dashboard["id"] = None

payload = {
    "dashboard": dashboard,
    "overwrite": True,
    "message": "Fix WhatAssoh metrics and container filters",
}

with open(destination, "w") as f:
    json.dump(payload, f, indent=2)

print("Panels corrigés:")
for name in sorted(found):
    print(" -", name)
PY

python3 -m json.tool "$PATCHED_FILE" >/dev/null

# ==========================================================
# PUSH TO GRAFANA
# ==========================================================

log "Application du patch dans Grafana"

RESPONSE="$(
    curl -fsS \
      -u "admin:${GRAFANA_PASS}" \
      -H 'Content-Type: application/json' \
      --data-binary "@${PATCHED_FILE}" \
      "${GRAFANA_URL}/api/dashboards/db"
)"

echo "$RESPONSE" | python3 -m json.tool

STATUS="$(
    echo "$RESPONSE" |
    python3 -c '
import json,sys
print(json.load(sys.stdin).get("status", ""))
'
)"

[[ "$STATUS" == "success" ]] || die "Grafana n'a pas confirmé le patch."

log "PATCH TERMINÉ"

echo
echo "Dashboard :"
echo "https://whatasso.dvybes.com/grafana/d/${DASHBOARD_UID}/"
echo
echo "Backup :"
echo "$BACKUP_FILE"
echo
