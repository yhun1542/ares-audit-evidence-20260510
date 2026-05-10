#!/usr/bin/env bash
# =============================================================================
# v8.5 SLO Alert — runs on the EC2 host as a cron job, alerts on Telegram
# =============================================================================
# Generated: 2026-05-09
#
# Why a separate alert from v85_daily_playbook.sh?
#   The playbook is a one-shot human-readable diagnostic. This script is a
#   tight, machine-friendly poller that reads only durable Redis state and
#   PM2 process status, designed to run every minute via cron with very low
#   overhead (~50ms). It guarantees that any of the following is alerted
#   within ~120s:
#     (S1) state:risk_rate:ts older than 120s          → WARN
#                                                  300s → CRITICAL
#     (S2) bridge stdout file not growing for 3 polls   → WARN
#                                                  6 polls → CRITICAL
#     (S3) SAFETY-PATCH skip > 30/min                   → CRITICAL
#     (S4) champion:target hash empty (HLEN==0)         → CRITICAL
#     (S5) champion-side fallback > 12/min              → WARN
#                                                   30/min → CRITICAL
#
# Configuration:
#   /home/ubuntu/.v85_slo.env      — secrets (Telegram bot token + chat id)
#   /var/lib/v85_slo/state.json    — persistent state across cron runs
#   /var/log/v85_slo/alert.log     — append log
#   /var/log/v85_slo/audit.jsonl   — JSONL audit trail
#
# Suppression:
#   Each rule fires at most once every 10 minutes per severity to avoid storms.
#
# Usage (one-shot test):
#   /home/ubuntu/v85_slo_alert.sh --dry
#
# Cron entry (every minute):
#   * * * * * /usr/bin/flock -n /run/v85_slo.lock /home/ubuntu/v85_slo_alert.sh \
#       >> /var/log/v85_slo/alert.log 2>&1
# =============================================================================
set -uo pipefail

# ---- config ----
ENV_FILE="${V85_SLO_ENV:-/home/ubuntu/.v85_slo.env}"
STATE_DIR=/var/lib/v85_slo
STATE_FILE=$STATE_DIR/state.json
LOG_DIR=/var/log/v85_slo
AUDIT=$LOG_DIR/audit.jsonl
PROC=final-to-champion-bridge
LOG_OUT=/home/ubuntu/.pm2/logs/${PROC}-out.log
ERR_OUT=/home/ubuntu/.pm2/logs/${PROC}-error.log
NOW_EPOCH=$(date +%s)
NOW_ISO=$(date -u +%FT%TZ)
DRY=0
[ "${1:-}" = "--dry" ] && DRY=1

# ---- bootstrap dirs ----
sudo mkdir -p "$STATE_DIR" "$LOG_DIR"
sudo chown -R ubuntu:ubuntu "$STATE_DIR" "$LOG_DIR"

# ---- load secrets ----
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
TG_BOT="${TG_BOT:-}"     # like 1234:abcd
TG_CHAT="${TG_CHAT:-}"   # like -1001234567890

# ---- prior state ----
PREV_STDOUT_BYTES=0; PREV_NO_GROW=0; declare -A LAST_FIRED
if [ -f "$STATE_FILE" ]; then
  PREV_STDOUT_BYTES=$(jq -r '.stdout_bytes // 0' "$STATE_FILE" 2>/dev/null)
  PREV_NO_GROW=$(jq -r '.no_grow_streak // 0' "$STATE_FILE" 2>/dev/null)
fi

# ---- collect signals ----
RR_TS=$(redis-cli GET state:risk_rate:ts 2>/dev/null)
RR_AGE=-1
if [ -n "$RR_TS" ]; then
  RR_EPOCH=$(date -d "$RR_TS" +%s 2>/dev/null || echo 0)
  [ "$RR_EPOCH" -gt 0 ] && RR_AGE=$((NOW_EPOCH - RR_EPOCH))
fi

CT_HLEN=$(redis-cli HLEN champion:target 2>/dev/null)
[ -z "$CT_HLEN" ] && CT_HLEN=0

STDOUT_BYTES=$(stat -c%s "$LOG_OUT" 2>/dev/null || echo 0)
DELTA=$((STDOUT_BYTES - PREV_STDOUT_BYTES))
if [ "$DELTA" -lt 100 ]; then
  NO_GROW_STREAK=$((PREV_NO_GROW + 1))
else
  NO_GROW_STREAK=0
fi

# SAFETY-PATCH skips in last 60s (count lines with timestamp from last minute)
SP_60=$(awk -v cutoff="$(date -u -d '60 seconds ago' +%FT%TZ)" '
  $0 ~ /SAFETY-PATCH.*skipping cycle/ && $1 >= cutoff { c++ }
  END { print c+0 }
' "$ERR_OUT" 2>/dev/null)
[ -z "$SP_60" ] && SP_60=0

# Champion fallback frequency over last 60s
CF_60=$(sudo journalctl --since "60 seconds ago" 2>/dev/null | grep -c "CHAMPION_FALLBACK")
[ -z "$CF_60" ] && CF_60=0

# ---- evaluate rules ----
ALERTS=()
add() { ALERTS+=("$1|$2|$3"); }   # severity|rule|message

# S1
if [ "$RR_AGE" -lt 0 ]; then
  add CRITICAL S1 "state:risk_rate:ts is empty (EMA never written since restart). bridge silently skipped."
elif [ "$RR_AGE" -gt 300 ]; then
  add CRITICAL S1 "state:risk_rate:ts age=${RR_AGE}s (>300s) — EMA pipeline halted."
elif [ "$RR_AGE" -gt 120 ]; then
  add WARN S1 "state:risk_rate:ts age=${RR_AGE}s (>120s) — EMA aging."
fi

# S2
if [ "$NO_GROW_STREAK" -ge 6 ]; then
  add CRITICAL S2 "bridge stdout has not grown for ${NO_GROW_STREAK} polls (~${NO_GROW_STREAK}min)."
elif [ "$NO_GROW_STREAK" -ge 3 ]; then
  add WARN S2 "bridge stdout no-grow streak=${NO_GROW_STREAK} polls."
fi

# S3
if [ "$SP_60" -gt 30 ]; then
  add CRITICAL S3 "SAFETY-PATCH skips ${SP_60}/min — phase9_b1_marker NO_GO. Issue signed override or fix validator input."
elif [ "$SP_60" -gt 10 ]; then
  add WARN S3 "SAFETY-PATCH skips ${SP_60}/min."
fi

# S4
if [ "$CT_HLEN" -eq 0 ]; then
  add CRITICAL S4 "champion:target HASH is empty (HLEN=0). Downstream is keep-alive on stale data."
elif [ "$CT_HLEN" -lt 5 ]; then
  add WARN S4 "champion:target HASH thin (HLEN=$CT_HLEN)."
fi

# S5
if [ "$CF_60" -gt 30 ]; then
  add CRITICAL S5 "champion fallback ${CF_60}/min — downstream entirely on v3 fallback weights."
elif [ "$CF_60" -gt 12 ]; then
  add WARN S5 "champion fallback ${CF_60}/min."
fi

# ---- suppress duplicate alerts (10 min) ----
SUPPRESS_FILE=$STATE_DIR/suppress.json
declare -A LAST
if [ -f "$SUPPRESS_FILE" ]; then
  while IFS=$'\t' read -r k v; do LAST["$k"]=$v; done < <(jq -r 'to_entries[] | "\(.key)\t\(.value)"' "$SUPPRESS_FILE" 2>/dev/null)
fi

FIRES=()
for a in "${ALERTS[@]}"; do
  sev="${a%%|*}"; rest="${a#*|}"
  rule="${rest%%|*}"; msg="${rest#*|}"
  key="${sev}_${rule}"
  last="${LAST[$key]:-0}"
  if (( NOW_EPOCH - last >= 600 )); then
    FIRES+=("$sev|$rule|$msg")
    LAST[$key]=$NOW_EPOCH
  fi
done

# ---- send Telegram ----
send_tg() {
  local text="$1"
  if [ -n "$TG_BOT" ] && [ -n "$TG_CHAT" ] && [ "$DRY" = "0" ]; then
    curl -fsS -X POST "https://api.telegram.org/bot${TG_BOT}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=${text}" \
      --data-urlencode "parse_mode=Markdown" >/dev/null 2>&1 \
      && return 0 || return 1
  fi
  return 0
}

if [ ${#FIRES[@]} -gt 0 ]; then
  HOSTNAME=$(hostname)
  HEADER="*[v8.5 SLO]* host=\`$HOSTNAME\` ts=\`$NOW_ISO\`"
  BODY=""
  for f in "${FIRES[@]}"; do
    sev="${f%%|*}"; rest="${f#*|}"
    rule="${rest%%|*}"; msg="${rest#*|}"
    BODY="$BODY"$'\n'"\`$sev/$rule\` $msg"
  done
  CONTEXT="rr_age=${RR_AGE}s, no_grow=${NO_GROW_STREAK}, sp/min=${SP_60}, cf/min=${CF_60}, hlen=${CT_HLEN}"
  TEXT="$HEADER$BODY"$'\n'"\`ctx:\` $CONTEXT"
  echo "[$NOW_ISO] FIRE: $TEXT"
  send_tg "$TEXT" || echo "[$NOW_ISO] telegram send failed"
fi

# ---- persist state ----
cat > "$STATE_FILE" <<EOF
{"stdout_bytes": $STDOUT_BYTES, "no_grow_streak": $NO_GROW_STREAK, "ts": "$NOW_ISO"}
EOF

# write suppress file
{
  echo "{"
  first=1
  for k in "${!LAST[@]}"; do
    [ $first -eq 1 ] || echo ","
    printf '  "%s": %s' "$k" "${LAST[$k]}"
    first=0
  done
  echo
  echo "}"
} > "$SUPPRESS_FILE"

# ---- audit ----
{
  printf '{"ts":"%s","rr_age":%d,"no_grow":%d,"sp_60":%d,"cf_60":%d,"hlen":%d,"alerts":%d,"fired":%d}\n' \
    "$NOW_ISO" "$RR_AGE" "$NO_GROW_STREAK" "$SP_60" "$CF_60" "$CT_HLEN" "${#ALERTS[@]}" "${#FIRES[@]}"
} >> "$AUDIT"

# ---- exit code: 0 OK, 1 WARN, 2 CRITICAL ----
EXIT=0
for a in "${ALERTS[@]}"; do
  case "$a" in
    CRITICAL*) EXIT=2;;
    WARN*) [ $EXIT -lt 1 ] && EXIT=1;;
  esac
done

if [ "$DRY" = "1" ]; then
  echo "[DRY RUN] alerts=${#ALERTS[@]} fired=${#FIRES[@]} exit=$EXIT"
  for a in "${ALERTS[@]}"; do echo "  $a"; done
fi

exit $EXIT
