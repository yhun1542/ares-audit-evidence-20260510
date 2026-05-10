#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${RAM26_NATIVE_MONITOR_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_MONITOR_NATIVE_BRIDGE_FULL_LIVE" ]]; then
  echo "RAM26_NATIVE_MONITOR_CONFIRM must be YES_MONITOR_NATIVE_BRIDGE_FULL_LIVE"
  exit 2
fi
DURATION_SEC="${DURATION_SEC:-1800}"
INTERVAL_SEC="${INTERVAL_SEC:-30}"
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/native_bridge_v2/${TS}_monitor"
mkdir -p "$OUT_DIR"
if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
REDIS_CLI=(redis-cli)
if [[ -n "${REDIS_URL:-}" ]]; then
  if [[ "$REDIS_URL" == rediss://* ]]; then REDIS_CLI=(redis-cli -u "$REDIS_URL" --tls --insecure); else REDIS_CLI=(redis-cli -u "$REDIS_URL"); fi
fi
redis_get(){ "${REDIS_CLI[@]}" --raw GET "$1" 2>/dev/null || true; }
redis_hlen(){ "${REDIS_CLI[@]}" --raw HLEN "$1" 2>/dev/null || echo 0; }
redis_xlen(){ "${REDIS_CLI[@]}" --raw XLEN "$1" 2>/dev/null || echo 0; }
END=$(( $(date +%s) + DURATION_SEC ))
ALERTS=0
while [[ "$(date +%s)" -lt "$END" ]]; do
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mode="$(redis_get emarkos:v1:mode)"
  enabled="$(redis_get trading:enabled)"
  kill="$(redis_get kill-switch:active)"
  halt="$(redis_get ops:halt:active)"
  g8="$(redis_get ares:open009:g8:status)"
  hlen="$(redis_hlen champion:targets:ssot)"
  ct="$(redis_hlen champion:target)"
  fills="$(redis_xlen stream:fills)"
  canon="$(redis_xlen ares:fills:canonical)"
  echo "$ts mode=$mode enabled=$enabled kill=$kill halt=$halt g8=$g8 hlen=$hlen ct=$ct fills=$fills canonical=$canon" | tee -a "$OUT_DIR/samples.log"
  bad=0
  [[ "$mode" != "LIVE" ]] && bad=1
  [[ "$enabled" != "true" ]] && bad=1
  [[ "$kill" == "true" ]] && bad=1
  [[ -n "$halt" ]] && bad=1
  [[ "$hlen" -lt 8 ]] && bad=1
  [[ "$ct" -lt 8 ]] && bad=1
  if [[ "$bad" -eq 1 ]]; then ALERTS=$((ALERTS+1)); echo "$ts ALERT" | tee -a "$OUT_DIR/alerts.log"; fi
  sleep "$INTERVAL_SEC"
done
cat > "$OUT_DIR/RAM26_NATIVE_MONITOR_REPORT.md" <<EOF
# RAM26 Native Bridge Post Live Monitor

## Verdict
$(if [[ "$ALERTS" -eq 0 ]]; then echo "RAM26_NATIVE_POST_LIVE_MONITOR_CLEAR"; else echo "RAM26_NATIVE_POST_LIVE_MONITOR_ALERTS_${ALERTS}"; fi)

## Alerts
${ALERTS}
EOF
echo "REPORT=$OUT_DIR/RAM26_NATIVE_MONITOR_REPORT.md"
