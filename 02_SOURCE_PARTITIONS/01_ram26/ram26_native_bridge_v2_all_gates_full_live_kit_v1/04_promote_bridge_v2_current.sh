#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM="${RAM26_BRIDGE_V2_PROMOTE_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_PROMOTE_RAM26_BRIDGE_V2_CURRENT" ]]; then
  echo "RAM26_BRIDGE_V2_PROMOTE_CONFIRM must be YES_PROMOTE_RAM26_BRIDGE_V2_CURRENT"
  exit 2
fi

REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
BRIDGE_FILE="${BRIDGE_FILE:-/home/ubuntu/ares_current/ops/ram26_final_to_champion_bridge_v2.mjs}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/native_bridge_v2/${TS}_promote"
mkdir -p "$OUT_DIR"

LOG="$OUT_DIR/run.log"
exec > >(tee -a "$LOG") 2>&1

[[ -f "$BRIDGE_FILE" ]] || { echo "BRIDGE_FILE missing: $BRIDGE_FILE"; exit 2; }

if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi

set +e
REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" \
RAM26_STAGED_MAX_AGE_SEC="${RAM26_STAGED_MAX_AGE_SEC:-172800}" \
node "$BRIDGE_FILE" --once true --publish-staged true --promote-current true --allow-current-ssot-write true --feature-max-age-sec "${RAM26_STAGED_MAX_AGE_SEC:-172800}" \
  > "$OUT_DIR/bridge_stdout.json" 2> "$OUT_DIR/bridge_stderr.txt"
RC="$?"
set -e
echo "$RC" > "$OUT_DIR/bridge_rc.txt"

if [[ "$RC" -ne 0 ]]; then
  echo "Bridge promote failed."
  cat "$OUT_DIR/bridge_stderr.txt"
  exit "$RC"
fi

cat > "$OUT_DIR/RAM26_BRIDGE_V2_PROMOTE_REPORT.md" <<EOF
# RAM26 Bridge v2 Promote Report

## Verdict
RAM26_BRIDGE_V2_CURRENT_PROMOTED

## What changed
- ssot:target:v2:current
- policy:champion:active
- champion:targets:ssot
- champion:target
- ares:bridge:active=LIVE_FINAL_TO_CHAMPION
- truth:champion:* RAM26 alignment

## What did not change
- No LIVE mode
- No trading enable
- No orders
EOF

echo "REPORT=$OUT_DIR/RAM26_BRIDGE_V2_PROMOTE_REPORT.md"
