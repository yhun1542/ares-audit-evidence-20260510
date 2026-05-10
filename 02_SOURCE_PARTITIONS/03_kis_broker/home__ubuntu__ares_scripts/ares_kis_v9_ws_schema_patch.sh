#!/usr/bin/env bash
set -Eeuo pipefail

################################################################################
# ARES-KIS-US-AUTOPILOT V9 WS Schema Patch
#
# Purpose:
#   - P0 KIS fill gap guard
#   - Patch WebSocket WS_FIELD_COUNT values in tr_ids.mjs
#   - Verify idempotently
#   - Optionally restart target PM2 apps only
#
# Does NOT:
#   - write production fill streams
#   - place test orders
#   - restart nextgen2-live
#   - restart all PM2 apps
#   - mutate champion keys
################################################################################

V9_CONFIRM="${V9_CONFIRM:-}"
ALLOW_BUY_GUARD="${ALLOW_BUY_GUARD:-NO}"
ALLOW_TARGET_RESTART="${ALLOW_TARGET_RESTART:-NO}"
SESSION_OVERRIDE="${SESSION_OVERRIDE:-NO}"

CHAMPION_ID="${CHAMPION_ID:-RAM26_ALPHA_0001_b215c119ea23}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
ARES_ROOT="${ARES_ROOT:-/home/ubuntu/ares_current}"
KIS_BROKER_ROOT="${KIS_BROKER_ROOT:-/home/ubuntu/kis-us-broker-v7}"
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"

RESTART_APPS_CSV="${RESTART_APPS_CSV:-ares-kis-us-broker,execution-reconciler-v4.1}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
PATCH_ID="ARES_KIS_V9_WS_SCHEMA_${TS}"
OUT_DIR="${PROMOTION_ROOT}/patches/${PATCH_ID}"
BACKUP_DIR="${OUT_DIR}/backup"

mkdir -p "${OUT_DIR}" "${BACKUP_DIR}"

LOG_FILE="${OUT_DIR}/run.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
warn() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARNING: $*" >&2; }
fail() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
need() { have "$1" || fail "missing command: $1"; }

if [[ "${V9_CONFIRM}" != "YES_ARES_KIS_V9_PATCH" ]]; then
  fail "V9_CONFIRM must be YES_ARES_KIS_V9_PATCH"
fi

need redis-cli
need python3
need grep
need awk
need sed
need find

if [[ -f "${REDIS_ENV}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REDIS_ENV}"
  set +a
fi

REDIS_CLI=(redis-cli)
if [[ -n "${REDIS_URL:-}" ]]; then
  if [[ "${REDIS_URL}" == rediss://* ]]; then
    REDIS_CLI=(redis-cli -u "${REDIS_URL}" --tls --insecure)
  else
    REDIS_CLI=(redis-cli -u "${REDIS_URL}")
  fi
fi

redis_get() { "${REDIS_CLI[@]}" --raw GET "$1" 2>/dev/null || true; }
redis_set() { "${REDIS_CLI[@]}" SET "$1" "$2" >/dev/null; }

redact() {
  sed -E 's#(rediss?://)[^ @]+@#\1***REDACTED***@#g; s#([A-Z0-9_]*KEY=)[^[:space:]]+#\1***REDACTED***#g; s#(SECRET=)[^[:space:]]+#\1***REDACTED***#g; s#(TOKEN=)[^[:space:]]+#\1***REDACTED***#g'
}

log "=== ARES KIS V9 WS Schema Patch ==="
log "PATCH_ID=${PATCH_ID}"
log "OUT_DIR=${OUT_DIR}"
log "CHAMPION_ID=${CHAMPION_ID}"
log "ALLOW_BUY_GUARD=${ALLOW_BUY_GUARD}"
log "ALLOW_TARGET_RESTART=${ALLOW_TARGET_RESTART}"

###############################################################################
# 1. Runtime preflight
###############################################################################

REDIS_BEFORE="${OUT_DIR}/redis_before.txt"
REDIS_AFTER="${OUT_DIR}/redis_after.txt"
PM2_BEFORE="${OUT_DIR}/pm2_before.txt"
PM2_AFTER="${OUT_DIR}/pm2_after.txt"
PATCH_SUMMARY="${OUT_DIR}/patch_summary.json"
REPORT_MD="${OUT_DIR}/ARES_KIS_V9_WS_SCHEMA_PATCH_REPORT.md"
DIFF_FILE="${OUT_DIR}/tr_ids_patch.diff"
TR_IDS_CANDIDATES="${OUT_DIR}/tr_ids_candidates.txt"

{
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for k in \
    emarkos:v1:mode \
    emarkos:v1:ram26:active_strategy_id \
    emarkos:v1:ram26:champion_params_file \
    emarkos:v1:ram26:order_routing \
    emarkos:v1:ram26:paper_trading \
    emarkos:v1:ram26:shadow_only \
    emarkos:v1:ram26:dry_run \
    trading:enabled \
    kill-switch:active
  do
    echo "$k=$(redis_get "$k")"
  done
} | redact > "${REDIS_BEFORE}"

MODE_BEFORE="$(redis_get emarkos:v1:mode)"
ACTIVE="$(redis_get emarkos:v1:ram26:active_strategy_id)"

if have pm2; then
  pm2 status > "${PM2_BEFORE}" 2>&1 || true
fi

###############################################################################
# 2. Apply P0 BUY Guard
###############################################################################

GUARD_APPLIED="false"
if [[ "${ALLOW_BUY_GUARD}" == "YES_SET_SAFE_TO_BLOCK_BUY" ]]; then
  if [[ "${MODE_BEFORE}" != "SAFE" ]]; then
    log "Applying P0 BUY guard (emarkos:v1:mode=SAFE)"
    redis_set "emarkos:v1:mode" "SAFE"
    GUARD_APPLIED="true"
  else
    log "BUY guard already active (emarkos:v1:mode=SAFE)"
  fi
else
  warn "BUY guard NOT allowed. Continuing without SAFE mode."
fi

###############################################################################
# 3. Find candidates
###############################################################################

find "${KIS_BROKER_ROOT}" "${ARES_ROOT}" -type f -name "tr_ids.mjs" -not -path "*/node_modules/*" > "${TR_IDS_CANDIDATES}" 2>/dev/null || true
if [[ ! -s "${TR_IDS_CANDIDATES}" ]]; then
  fail "No tr_ids.mjs found in ${KIS_BROKER_ROOT} or ${ARES_ROOT}"
fi

###############################################################################
# 4. Patch files
###############################################################################

PATCHED_FILES=""

while read -r f; do
  if grep -q "WS_FIELD_COUNT" "$f"; then
    log "Patching $f"
    cp -a "$f" "${BACKUP_DIR}/$(basename "$f")_${TS}.bak"
    
    # 26 -> 25
    sed -i 's/HDFSCNT0:\s*26/HDFSCNT0: 25/' "$f"
    sed -i 's/H0GSCNI0:\s*26/H0GSCNI0: 25/' "$f"
    sed -i 's/H0GSCNI9:\s*26/H0GSCNI9: 25/' "$f"
    
    # 11 -> 16
    sed -i 's/HDFSASP0:\s*11/HDFSASP0: 16/' "$f"
    sed -i 's/HDFSASP1:\s*11/HDFSASP1: 16/' "$f"
    
    diff -u "${BACKUP_DIR}/$(basename "$f")_${TS}.bak" "$f" >> "${DIFF_FILE}" || true
    PATCHED_FILES="${PATCHED_FILES}${f},"
  fi
done < "${TR_IDS_CANDIDATES}"

PATCHED_FILES="${PATCHED_FILES%,}"

###############################################################################
# 5. Syntax check
###############################################################################

log "Running syntax smoke test..."
IFS=',' read -ra FILES <<< "${PATCHED_FILES}"
for f in "${FILES[@]}"; do
  f="$(echo "$f" | xargs)"
  [[ -z "$f" ]] && continue
  if ! node --check "$f"; then
    fail "Syntax check failed on $f"
  fi
done

###############################################################################
# 6. Verify idempotently via python
###############################################################################

VERIFY_JSON="${OUT_DIR}/verify.json"
python3 - "${TR_IDS_CANDIDATES}" "${VERIFY_JSON}" <<'PY'
import sys, re, json, pathlib

files_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])

expected = {
    "HDFSCNT0": 25,
    "H0GSCNI0": 25,
    "H0GSCNI9": 25,
    "HDFSASP0": 16,
    "HDFSASP1": 16,
}

result = {}
hard_fail = False

for line in files_path.read_text().splitlines():
    p = pathlib.Path(line.strip())
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    per = {}
    for key, exp in expected.items():
        m = re.search(r'(?:"%s"|%s)\s*:\s*(\d+)' % (re.escape(key), re.escape(key)), text)
        if m:
            val = int(m.group(1))
            per[key] = {"value": val, "expected": exp, "ok": val == exp}
            if val != exp:
                hard_fail = True
        else:
            per[key] = {"value": None, "expected": exp, "ok": "not_present"}
    result[str(p)] = per

payload = {"expected": expected, "files": result, "hard_fail": hard_fail}
out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

if hard_fail:
    sys.exit(2)
PY

log "Verification JSON:"
cat "${VERIFY_JSON}"

###############################################################################
# 7. Optional targeted restart
###############################################################################

RESTART_PERFORMED="false"
RESTART_DEFERRED_REASON=""

if [[ "${ALLOW_TARGET_RESTART}" == "YES_TARGETED_RESTART" ]]; then
  if ! have pm2; then
    fail "pm2 not found but restart requested"
  fi

  KST_DOW="$(TZ=Asia/Seoul date '+%u')"
  KST_HHMM="$(TZ=Asia/Seoul date '+%H%M')"

  IN_US_SESSION="false"
  if { [[ "${KST_DOW}" -ge 1 && "${KST_DOW}" -le 5 && "${KST_HHMM}" -ge 2230 ]]; } || \
     { [[ "${KST_DOW}" -ge 2 && "${KST_DOW}" -le 6 && "${KST_HHMM}" -le 0600 ]]; }; then
    IN_US_SESSION="true"
  fi

  if [[ "${IN_US_SESSION}" == "true" && "${SESSION_OVERRIDE}" != "YES_ALLOW_SESSION_RESTART" ]]; then
    warn "US regular session lock active. Target restart deferred."
    RESTART_DEFERRED_REASON="US_SESSION_LOCK"
  else
    IFS=',' read -ra APPS <<< "${RESTART_APPS_CSV}"
    for app in "${APPS[@]}"; do
      app="$(echo "$app" | xargs)"
      [[ -z "$app" ]] && continue
      if pm2 describe "$app" >/dev/null 2>&1; then
        log "Restarting target PM2 app: $app"
        pm2 restart "$app" --update-env
        sleep 5
      else
        warn "PM2 app not found: $app"
      fi
    done
    RESTART_PERFORMED="true"
  fi
else
  RESTART_DEFERRED_REASON="RESTART_NOT_REQUESTED"
fi

if have pm2; then
  pm2 status > "${PM2_AFTER}" 2>&1 || true
fi

###############################################################################
# 8. Post snapshot
###############################################################################

{
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for k in \
    emarkos:v1:mode \
    emarkos:v1:ram26:active_strategy_id \
    emarkos:v1:ram26:champion_params_file \
    emarkos:v1:ram26:order_routing \
    emarkos:v1:ram26:paper_trading \
    emarkos:v1:ram26:shadow_only \
    emarkos:v1:ram26:dry_run \
    trading:enabled \
    kill-switch:active
  do
    echo "$k=$(redis_get "$k")"
  done
} | redact > "${REDIS_AFTER}"

MODE_AFTER="$(redis_get emarkos:v1:mode)"
ACTIVE_AFTER="$(redis_get emarkos:v1:ram26:active_strategy_id)"

###############################################################################
# 9. Write summary/report
###############################################################################

python3 - "${PATCH_SUMMARY}" \
  "${PATCH_ID}" "${CHAMPION_ID}" "${MODE_BEFORE}" "${MODE_AFTER}" "${ACTIVE}" "${ACTIVE_AFTER}" \
  "${GUARD_APPLIED}" "${RESTART_PERFORMED}" "${RESTART_DEFERRED_REASON}" \
  "${PATCHED_FILES}" "${VERIFY_JSON}" "${DIFF_FILE}" "${REDIS_BEFORE}" "${REDIS_AFTER}" "${PM2_BEFORE}" "${PM2_AFTER}" <<'PY'
import json, sys, datetime, pathlib

(
    out, patch_id, champion_id, mode_before, mode_after, active_before, active_after,
    guard_applied, restart_performed, restart_deferred_reason,
    patched_files, verify_json, diff_file, redis_before, redis_after, pm2_before, pm2_after
) = sys.argv[1:]

payload = {
    "patch_id": patch_id,
    "verdict": "ARES_KIS_V9_WS_SCHEMA_PATCH_COMPLETE",
    "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
    "champion_id": champion_id,
    "runtime": {
        "mode_before": mode_before,
        "mode_after": mode_after,
        "active_before": active_before,
        "active_after": active_after,
        "buy_guard_applied": guard_applied == "true",
        "restart_performed": restart_performed == "true",
        "restart_deferred_reason": restart_deferred_reason,
    },
    "artifacts": {
        "patched_files": patched_files,
        "verify_json": verify_json,
        "diff_file": diff_file,
        "redis_before": redis_before,
        "redis_after": redis_after,
        "pm2_before": pm2_before,
        "pm2_after": pm2_after,
    }
}

pathlib.Path(out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
PY

cat > "${REPORT_MD}" <<EOF
# ARES-KIS-US-AUTOPILOT V9 WS Schema Patch Report

## Verdict

\`\`\`
ARES_KIS_V9_WS_SCHEMA_PATCH_COMPLETE
\`\`\`

## Runtime

| 항목 | Before | After |
|---|---:|---:|
| emarkos:v1:mode | ${MODE_BEFORE} | ${MODE_AFTER} |
| active_strategy_id | ${ACTIVE} | ${ACTIVE_AFTER} |
| BUY guard applied | - | ${GUARD_APPLIED} |
| targeted restart performed | - | ${RESTART_PERFORMED} |
| restart deferred reason | - | ${RESTART_DEFERRED_REASON} |

## Patched Values

| TR_ID | Expected |
|---|---:|
| HDFSCNT0 | 25 |
| H0GSCNI0 | 25 |
| H0GSCNI9 | 25 |
| HDFSASP0 | 16 |
| HDFSASP1 | 16 |

## Artifacts

- Patched files: \`${PATCHED_FILES}\`
- Verify JSON: \`${VERIFY_JSON}\`
- Diff: \`${DIFF_FILE}\`
- Redis before: \`${REDIS_BEFORE}\`
- Redis after: \`${REDIS_AFTER}\`
- PM2 before: \`${PM2_BEFORE}\`
- PM2 after: \`${PM2_AFTER}\`
- Backup dir: \`${BACKUP_DIR}\`

## Non-Mutation Confirmation

- No production fill stream backfill.
- No test order.
- No champion key mutation.
- No nextgen2-live restart.
- No PM2 global restart.
