#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${RAM26_RISK_RATE_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_INSTALL_RAM26_RISK_RATE_PUBLISHER" ]]; then
  echo "RAM26_RISK_RATE_CONFIRM must be YES_INSTALL_RAM26_RISK_RATE_PUBLISHER"
  exit 2
fi
ARES_ROOT="${ARES_ROOT:-/home/ubuntu/ares_current}"
DEST_DIR="${ARES_ROOT}/ops"
DEST_FILE="${DEST_DIR}/ram26_risk_rate_publisher_v1.mjs"
PM2_NAME="${PM2_NAME:-ram26-risk-rate-publisher}"
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/contract_runtime_hardening/${TS}_risk_rate"
mkdir -p "$OUT_DIR" "$DEST_DIR"
if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -a "$SCRIPT_DIR/ram26_risk_rate_publisher_v1.mjs" "$DEST_FILE"
chmod +x "$DEST_FILE"
node --check "$DEST_FILE"
REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" node "$DEST_FILE" --once > "$OUT_DIR/oneshot.json"
if command -v pm2 >/dev/null 2>&1; then
  if pm2 describe "$PM2_NAME" >/dev/null 2>&1; then pm2 delete "$PM2_NAME" || true; fi
  REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" pm2 start "$DEST_FILE" --name "$PM2_NAME" --interpreter node --update-env
  pm2 save || true
fi
cat > "$OUT_DIR/RISK_RATE_REPORT.md" <<EOF
# RAM26 Risk Rate Publisher Install Report

## Verdict
RAM26_RISK_RATE_PUBLISHER_INSTALLED

## Writes
- state:risk_rate
- state:risk_rate:ts
- state:risk_rate:meta
- ram26:risk_rate:events

## Safety
No target/policy/mode/trading/fill writes.
EOF
echo "REPORT=$OUT_DIR/RISK_RATE_REPORT.md"
