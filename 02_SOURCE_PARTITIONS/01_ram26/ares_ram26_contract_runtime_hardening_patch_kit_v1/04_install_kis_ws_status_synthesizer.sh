#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${KIS_WS_STATUS_SYNTH_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_INSTALL_KIS_WS_STATUS_SYNTH" ]]; then
  echo "KIS_WS_STATUS_SYNTH_CONFIRM must be YES_INSTALL_KIS_WS_STATUS_SYNTH"
  exit 2
fi
ARES_ROOT="${ARES_ROOT:-/home/ubuntu/ares_current}"
DEST_DIR="${ARES_ROOT}/external"
DEST_FILE="${DEST_DIR}/kis_ws_status_synthesizer_v1.py"
PM2_NAME="${PM2_NAME:-kis-ws-status-synthesizer}"
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/contract_runtime_hardening/${TS}_kis_ws_status"
mkdir -p "$OUT_DIR" "$DEST_DIR"
if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -a "$SCRIPT_DIR/kis_ws_status_synthesizer_v1.py" "$DEST_FILE"
chmod +x "$DEST_FILE"
python3 -m py_compile "$DEST_FILE"
REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" python3 "$DEST_FILE" --once > "$OUT_DIR/oneshot.json"
if command -v pm2 >/dev/null 2>&1; then
  if pm2 describe "$PM2_NAME" >/dev/null 2>&1; then pm2 delete "$PM2_NAME" || true; fi
  REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" pm2 start "$DEST_FILE" --name "$PM2_NAME" --interpreter python3 --update-env
  pm2 save || true
fi
cat > "$OUT_DIR/KIS_WS_STATUS_SYNTH_REPORT.md" <<EOF
# KIS WS Status Synthesizer Install Report

## Verdict
KIS_WS_STATUS_SYNTH_INSTALLED

## Writes
- kis:ws:status:current
- kis:ws:heartbeat
- kis:fills:last_ts
- kis:ws:status:synth:events

## Safety
No KIS API calls, no orders, no restarts except own PM2 process.
EOF
echo "REPORT=$OUT_DIR/KIS_WS_STATUS_SYNTH_REPORT.md"
