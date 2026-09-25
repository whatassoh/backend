#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

# Configure the production switches delivered by AI lots 1 through 5 without
# sourcing the environment file or exposing secrets in process arguments.

PROFILE="${1:-shadow}"
ROOT_DIR="${DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${AI_ENV_FILE:-${ROOT_DIR}/.env.production}"
LOCK_TARGET="${AI_ENV_LOCK_FILE:-${ENV_FILE}}"

case "$PROFILE" in
    baseline)
        DEFAULT_CHANNELS=""
        DEFAULT_ROLLOUT=0
        ;;
    shadow)
        DEFAULT_CHANNELS="WHATSAPP"
        DEFAULT_ROLLOUT=0
        ;;
    active)
        DEFAULT_CHANNELS="WHATSAPP"
        DEFAULT_ROLLOUT=10
        ;;
    *)
        printf '[ERROR] Profil invalide: %s (baseline, shadow ou active).\n' \
            "$PROFILE" >&2
        exit 1
        ;;
esac

CHANNELS="${2-${DEFAULT_CHANNELS}}"
RAG_ROLLOUT="${3-${DEFAULT_ROLLOUT}}"

die() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

for command in awk chmod chown cp date id mkdir mktemp mv rm stat; do
    command -v "$command" >/dev/null 2>&1 || die "Commande requise absente: ${command}"
done

[[ -f "$ENV_FILE" ]] || die "Fichier absent: ${ENV_FILE}"
[[ -w "$ENV_FILE" ]] || die "Fichier non modifiable: ${ENV_FILE}"
[[ "$CHANNELS" =~ ^$|^(WHATSAPP|TELEGRAM)(,(WHATSAPP|TELEGRAM))*$ ]] || \
    die "Canaux invalides: utilisez WHATSAPP, TELEGRAM ou les deux."
[[ "$RAG_ROLLOUT" =~ ^[0-9]+$ ]] || die "Le rollout RAG doit etre un entier."
(( RAG_ROLLOUT >= 0 && RAG_ROLLOUT <= 100 )) || \
    die "Le rollout RAG doit etre compris entre 0 et 100."
if [[ "$PROFILE" == "active" && "$RAG_ROLLOUT" -eq 0 ]]; then
    die "Le profil active exige un rollout RAG superieur a zero."
fi

LOCK_DIR=""
if command -v flock >/dev/null 2>&1; then
    [[ -r "$LOCK_TARGET" ]] || die "Cible de verrou absente: ${LOCK_TARGET}"
    exec 9<"$LOCK_TARGET"
    flock -n 9 || die "Une autre configuration IA est deja en cours."
else
    LOCK_DIR="${AI_ENV_LOCK_FILE:-/tmp/whatassoh-ai-env.lock}.d"
    mkdir "$LOCK_DIR" 2>/dev/null || \
        die "Une autre configuration IA est deja en cours."
fi

if stat -c '%u' "$ENV_FILE" >/dev/null 2>&1; then
    ENV_UID="$(stat -c '%u' "$ENV_FILE")"
    ENV_GID="$(stat -c '%g' "$ENV_FILE")"
    ENV_MODE="$(stat -c '%a' "$ENV_FILE")"
else
    ENV_UID="$(stat -f '%u' "$ENV_FILE")"
    ENV_GID="$(stat -f '%g' "$ENV_FILE")"
    ENV_MODE="$(stat -f '%Lp' "$ENV_FILE")"
fi
CURRENT_UID="$(id -u)"
if [[ "$CURRENT_UID" -ne 0 && "$CURRENT_UID" -ne "$ENV_UID" ]]; then
    die "Executez ce script avec le proprietaire de .env.production ou root."
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="${ROOT_DIR}/.deploy-backups"
mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/ai-env-${STAMP}.XXXXXX")"
chmod 700 "$BACKUP_DIR"
cp -a "$ENV_FILE" "${BACKUP_DIR}/env.production"

WORK_FILE="$(mktemp "${ROOT_DIR}/.env.production.ai.XXXXXX")"
NEXT_FILE=""
cleanup() {
    [[ -z "${NEXT_FILE:-}" ]] || rm -f "$NEXT_FILE"
    [[ -z "${WORK_FILE:-}" ]] || rm -f "$WORK_FILE"
    [[ -z "${LOCK_DIR:-}" ]] || rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
cp "$ENV_FILE" "$WORK_FILE"

env_get() {
    local key="$1"
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
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || \
        die "Valeur multiligne interdite pour ${key}."
    NEXT_FILE="$(mktemp "${ROOT_DIR}/.env.production.ai.next.XXXXXX")"
    awk -v key="$key" -v value="$value" '
        BEGIN { done=0 }
        index($0, key "=") == 1 {
            if (!done) print key "=" value
            done=1
            next
        }
        { print }
        END { if (!done) print key "=" value }
    ' "$WORK_FILE" > "$NEXT_FILE"
    mv "$NEXT_FILE" "$WORK_FILE"
    NEXT_FILE=""
}

OPENAI_KEY="${OPENAI_API_KEY:-$(env_get OPENAI_API_KEY 2>/dev/null || true)}"
if [[ -z "$OPENAI_KEY" && -t 0 ]]; then
    read -r -s -p 'OPENAI_API_KEY: ' OPENAI_KEY
    printf '\n'
fi
[[ -n "$OPENAI_KEY" ]] || \
    die "OPENAI_API_KEY est absente. Relancez interactivement ou exportez-la."

MODEL="${WHATASSOH_AI_MODEL:-$(env_get AI_MODEL 2>/dev/null || true)}"
if [[ -z "$MODEL" && -t 0 ]]; then
    read -r -p 'AI_MODEL (identifiant autorise dans votre compte OpenAI): ' MODEL
fi
[[ -n "$MODEL" ]] || \
    die "AI_MODEL est absent. Definissez WHATASSOH_AI_MODEL avant de relancer."

EMBEDDING_PROVIDER="${AI_RAG_EMBEDDING_PROVIDER:-$(env_get AI_RAG_EMBEDDING_PROVIDER 2>/dev/null || true)}"
EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER:-openai}"
case "$EMBEDDING_PROVIDER" in
    openai)
        EMBEDDING_BASE_URL=""
        EMBEDDING_API_KEY=""
        EMBEDDING_MODEL="${AI_RAG_EMBEDDING_MODEL:-text-embedding-3-small}"
        EMBEDDING_SEND_DIMENSIONS=True
        ;;
    openai_compatible)
        EMBEDDING_BASE_URL="${AI_RAG_EMBEDDING_BASE_URL:-$(env_get AI_RAG_EMBEDDING_BASE_URL 2>/dev/null || true)}"
        EMBEDDING_API_KEY="${AI_RAG_EMBEDDING_API_KEY:-$(env_get AI_RAG_EMBEDDING_API_KEY 2>/dev/null || true)}"
        EMBEDDING_MODEL="${AI_RAG_EMBEDDING_MODEL:-$(env_get AI_RAG_EMBEDDING_MODEL 2>/dev/null || true)}"
        EMBEDDING_SEND_DIMENSIONS="${AI_RAG_EMBEDDING_SEND_DIMENSIONS:-False}"
        [[ "$EMBEDDING_BASE_URL" =~ ^https?:// ]] || \
            die "Le fournisseur local exige AI_RAG_EMBEDDING_BASE_URL en HTTP(S)."
        [[ -n "$EMBEDDING_MODEL" ]] || \
            die "Le fournisseur local exige AI_RAG_EMBEDDING_MODEL."
        ;;
    *)
        die "AI_RAG_EMBEDDING_PROVIDER doit valoir openai ou openai_compatible."
        ;;
esac

AGENT_SHADOW=False
STYLE=False
CONVERSATION_MEMORY=False
MEMORY_SUMMARY=False
COMPOSER_SHADOW=False
COMPOSER_ACTIVE=False
RAG_ACTIVE=False
RAG_SHADOW=False
RAG_SEMANTIC=False

if [[ "$PROFILE" == "shadow" || "$PROFILE" == "active" ]]; then
    AGENT_SHADOW=True
    STYLE=True
    CONVERSATION_MEMORY=True
    MEMORY_SUMMARY=True
    RAG_SEMANTIC=True
fi
if [[ "$PROFILE" == "shadow" ]]; then
    COMPOSER_SHADOW=True
    RAG_SHADOW=True
fi
if [[ "$PROFILE" == "active" ]]; then
    COMPOSER_ACTIVE=True
    RAG_ACTIVE=True
fi

# AI core and global safety.
env_set AI_ENABLED True
env_set AI_PROVIDER openai
env_set AI_MODEL "$MODEL"
env_set AI_TIMEOUT_SECONDS 15
env_set OPENAI_API_KEY "$OPENAI_KEY"
env_set AI_EMERGENCY_STOP False
env_set AI_DECISION_OBSERVABILITY_ENABLED True
env_set AI_VOICE_ENABLED False

# Lot 2: ordered inbox/outbox. Lot 1 has no environment switch.
env_set CONVERSATION_INBOX_CHANNELS "$CHANNELS"
env_set CONVERSATION_INBOX_LEASE_SECONDS 300
env_set CONVERSATION_OUTBOX_LEASE_SECONDS 120
env_set CONVERSATION_INBOX_MAX_ATTEMPTS 3
env_set CONVERSATION_OUTBOX_MAX_ATTEMPTS 3
env_set CONVERSATION_RETRY_BASE_SECONDS 15
env_set CONVERSATION_RETRY_MAX_SECONDS 300
env_set CONVERSATION_RECONCILIATION_BATCH_SIZE 100

# Lot 3: the graph remains shadow-only; no production execution node exists yet.
env_set AI_AGENT_ENABLED False
env_set AI_AGENT_SHADOW_ENABLED "$AGENT_SHADOW"
env_set AI_AGENT_TOOLS_ENABLED False
env_set AI_AGENT_WRITE_TOOLS_ENABLED False
env_set AI_AGENT_MEMORY_ENABLED False
env_set AI_AGENT_MAX_PLAN_STEPS 4
env_set AI_AGENT_SHADOW_CACHE_SECONDS 300
env_set AI_TOOL_MANIFEST_CACHE_SECONDS 300
env_set AI_TOOL_MAX_RESULT_BYTES 65536
env_set MESSAGING_CONFIGURATION_CACHE_SECONDS 60

# Lot 4: governed memory, writing style and natural composition.
env_set AI_STYLE_ADAPTATION_ENABLED "$STYLE"
env_set AI_CONVERSATION_MEMORY_ENABLED "$CONVERSATION_MEMORY"
env_set AI_MEMORY_SUMMARY_ENABLED "$MEMORY_SUMMARY"
env_set AI_NATURAL_COMPOSER_SHADOW_ENABLED "$COMPOSER_SHADOW"
env_set AI_NATURAL_COMPOSER_ENABLED "$COMPOSER_ACTIVE"
env_set AI_MEMORY_SUMMARY_MIN_MESSAGES 8
env_set AI_MEMORY_SUMMARY_BATCH_SIZE 24
env_set AI_MEMORY_FACT_TTL_DAYS 365
env_set AI_MEMORY_RECONCILIATION_BATCH_SIZE 100

# Lot 5: ACL-first hybrid RAG.
env_set AI_RAG_ENABLED "$RAG_ACTIVE"
env_set AI_RAG_SHADOW_ENABLED "$RAG_SHADOW"
env_set AI_RAG_ROLLOUT_PERCENT "$RAG_ROLLOUT"
env_set AI_RAG_SEMANTIC_ENABLED "$RAG_SEMANTIC"
env_set AI_RAG_EMBEDDING_PROVIDER "$EMBEDDING_PROVIDER"
env_set AI_RAG_EMBEDDING_MODEL "$EMBEDDING_MODEL"
env_set AI_RAG_EMBEDDING_DIMENSIONS 1024
env_set AI_RAG_EMBEDDING_BASE_URL "$EMBEDDING_BASE_URL"
env_set AI_RAG_EMBEDDING_API_KEY "$EMBEDDING_API_KEY"
env_set AI_RAG_EMBEDDING_SEND_DIMENSIONS "$EMBEDDING_SEND_DIMENSIONS"
env_set AI_RAG_MAX_QUERY_CHARACTERS 1200
env_set AI_RAG_MAX_SOURCE_CHARACTERS 500000
env_set AI_RAG_MAX_METADATA_CHARACTERS 20000
env_set AI_RAG_CHUNK_TARGET_CHARACTERS 1200
env_set AI_RAG_CHUNK_MAX_CHARACTERS 1800
env_set AI_RAG_CHUNK_OVERLAP_CHARACTERS 160
env_set AI_RAG_MAX_RESULTS 6
env_set AI_RAG_CANDIDATE_LIMIT 24
env_set AI_RAG_MAX_CONTEXT_CHARACTERS 12000
env_set AI_RAG_MAX_CHUNKS_PER_SOURCE 2
env_set AI_RAG_MIN_LEXICAL_SCORE 0.02
env_set AI_RAG_MIN_TRIGRAM_SCORE 0.12
env_set AI_RAG_MIN_SEMANTIC_SCORE 0.45
env_set AI_RAG_MAX_CHUNK_INJECTION_RISK 0.45
env_set AI_RAG_RRF_RANK_CONSTANT 60
env_set AI_RAG_LEXICAL_WEIGHT 0.55
env_set AI_RAG_SEMANTIC_WEIGHT 0.45
env_set AI_RAG_CACHE_SECONDS 300
env_set AI_RAG_EMBEDDING_BATCH_SIZE 32
env_set AI_RAG_EMBEDDING_MAX_ATTEMPTS 5
env_set AI_RAG_EMBEDDING_LEASE_SECONDS 900
env_set AI_RAG_AUDIT_ENABLED True
env_set AI_RAG_AUDIT_RETENTION_DAYS 30

chmod "$ENV_MODE" "$WORK_FILE"
if [[ "$CURRENT_UID" -eq 0 ]]; then
    chown "${ENV_UID}:${ENV_GID}" "$WORK_FILE"
fi
mv "$WORK_FILE" "$ENV_FILE"
WORK_FILE=""

printf '[OK] Configuration IA lots 1-5 mise a jour.\n'
printf '     profil=%s canaux=%s rag_rollout=%s%%\n' \
    "$PROFILE" "${CHANNELS:-aucun}" "$RAG_ROLLOUT"
printf '     env=%s\n' "$ENV_FILE"
printf '     backup=%s\n' "${BACKUP_DIR}/env.production"
printf '     OPENAI_API_KEY conservee sans affichage.\n'
printf '[NEXT] Redeployez web, worker et beat pour appliquer ces variables.\n'
if [[ "$RAG_SEMANTIC" == "True" ]]; then
    printf '[NEXT] Apres migration et ingestion: python manage.py knowledge_reindex --embeddings --reset-failed\n'
fi
