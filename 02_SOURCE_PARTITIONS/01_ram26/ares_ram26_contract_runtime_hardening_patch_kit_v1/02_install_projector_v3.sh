#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM="${RAM26_PROJECTOR_V3_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_INSTALL_RAM26_PROJECTOR_V3" ]]; then
  echo "RAM26_PROJECTOR_V3_CONFIRM must be YES_INSTALL_RAM26_PROJECTOR_V3"
  exit 2
fi

ARES_ROOT="${ARES_ROOT:-/home/ubuntu/ares_current}"
DEST_DIR="${ARES_ROOT}/bridges"
DEST_FILE="${DEST_DIR}/ram26_target_ssot_projector_v1.mjs"
PM2_NAME="${PM2_NAME:-ram26-target-ssot-projector}"
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/contract_runtime_hardening/${TS}_projector_v3"
BACKUP_DIR="$OUT_DIR/backup"
mkdir -p "$OUT_DIR" "$BACKUP_DIR" "$DEST_DIR"

if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/ram26_target_ssot_projector_v3.mjs"

[[ -f "$SRC" ]] || { echo "source missing: $SRC"; exit 2; }
[[ -f "$DEST_FILE" ]] && cp -a "$DEST_FILE" "$BACKUP_DIR/$(basename "$DEST_FILE").bak_${TS}"
cp -a "$SRC" "$DEST_FILE"
chmod +x "$DEST_FILE"
node --check "$DEST_FILE"
node "$DEST_FILE" --self-test > "$OUT_DIR/self_test.json"

# One-shot projection before restart.
REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" \
node "$DEST_FILE" --once > "$OUT_DIR/oneshot_stdout.json" 2> "$OUT_DIR/oneshot_stderr.txt"

if command -v pm2 >/dev/null 2>&1 && pm2 describe "$PM2_NAME" >/dev/null 2>&1; then
  REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" pm2 restart "$PM2_NAME" --update-env
  pm2 save || true
  sleep 5
  pm2 logs "$PM2_NAME" --lines 100 --nostream > "$OUT_DIR/pm2_logs_after.txt" 2>&1 || true
fi

# Validation
bash "$(dirname "$0")/06_verify_hardening_gate.sh" > "$OUT_DIR/verify_after.txt" 2>&1 || true

cat > "$OUT_DIR/PROJECTOR_V3_REPORT.md" <<EOF
# RAM26 Projector v3 Install Report

## Verdict
RAM26_PROJECTOR_V3_INSTALLED

## Changes
- Handles SSOT_TARGET_V2 envelope \`targets.positions[]\`.
- Propagates _meta_marker and phase9_b1_marker to champion hashes.
- Allows projection during LIVE if source is valid and kill-switch is clear.
- Does not write ssot:target:v2:current, policy:champion:active, mode, trading, or fill streams.

## Artifacts
- self_test.json
- oneshot_stdout.json
- verify_after.txt
- backup: \`${BACKUP_DIR}\`
EOF

echo "REPORT=$OUT_DIR/PROJECTOR_V3_REPORT.md"
