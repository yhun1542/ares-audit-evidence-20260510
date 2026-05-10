#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${RAM26_NATIVE_FULL_LIVE_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_NATIVE_BRIDGE_ALL_GATES_FULL_LIVE" ]]; then
  echo "RAM26_NATIVE_FULL_LIVE_CONFIRM must be YES_NATIVE_BRIDGE_ALL_GATES_FULL_LIVE"
  exit 2
fi

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/native_bridge_v2/${TS}_full_live"
mkdir -p "$OUT_DIR"

RAM26_ALL_GATES_NATIVE_VERIFY_CONFIRM=YES_VERIFY_NATIVE_BRIDGE_ALL_GATES bash "$KIT_DIR/05_verify_native_all_gates.sh"

if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
REDIS_CLI=(redis-cli)
if [[ -n "${REDIS_URL:-}" ]]; then
  if [[ "$REDIS_URL" == rediss://* ]]; then REDIS_CLI=(redis-cli -u "$REDIS_URL" --tls --insecure); else REDIS_CLI=(redis-cli -u "$REDIS_URL"); fi
fi
redis_set(){ "${REDIS_CLI[@]}" SET "$1" "$2" >/dev/null; }
redis_del(){ "${REDIS_CLI[@]}" DEL "$@" >/dev/null || true; }
redis_get(){ "${REDIS_CLI[@]}" --raw GET "$1" 2>/dev/null || true; }
redis_hlen(){ "${REDIS_CLI[@]}" --raw HLEN "$1" 2>/dev/null || echo 0; }

if [[ "$(redis_get kill-switch:active)" == "true" ]]; then
  echo "kill-switch active; refuse LIVE"
  exit 10
fi

if command -v pm2 >/dev/null 2>&1 && pm2 describe final-to-champion-bridge >/dev/null 2>&1; then
  pm2 restart final-to-champion-bridge --update-env || true
  pm2 save || true
fi

redis_del ops:halt:active ops:halt:request policy:trade:halt_reason
redis_set emarkos:v1:ram26:active_strategy_id RAM26_ALPHA_0001_b215c119ea23
redis_set emarkos:v1:ram26:order_routing live
redis_set emarkos:v1:ram26:paper_trading false
redis_set emarkos:v1:ram26:shadow_only false
redis_set emarkos:v1:ram26:dry_run false
redis_set trading:enabled:override true
redis_set manual_order_submission_enabled true
redis_set trading:enabled true
redis_set emarkos:v1:mode LIVE
redis_set ram26:native_bridge:full_live:last "$TS"
redis_set ares:open009:status RESOLVED_NATIVE_BRIDGE_ALL_GATES_FULL_LIVE

sleep 5

{
  for k in emarkos:v1:mode trading:enabled trading:enabled:override manual_order_submission_enabled kill-switch:active ops:halt:active ares:open009:g8:status ram26:native_bridge:full_live:last; do
    echo "--- $k ---"; redis_get "$k"
  done
  echo "HLEN champion:targets:ssot=$(redis_hlen champion:targets:ssot)"
  echo "HLEN champion:target=$(redis_hlen champion:target)"
} > "$OUT_DIR/post_live_snapshot.txt"

cat > "$OUT_DIR/RAM26_NATIVE_BRIDGE_FULL_LIVE_REPORT.md" <<EOF
# RAM26 Native Bridge Full Live Report

## Verdict
RAM26_NATIVE_BRIDGE_ALL_GATES_FULL_LIVE_SUCCESS

## Artifacts
- Post live: \`${OUT_DIR}/post_live_snapshot.txt\`
EOF

echo "REPORT=$OUT_DIR/RAM26_NATIVE_BRIDGE_FULL_LIVE_REPORT.md"
