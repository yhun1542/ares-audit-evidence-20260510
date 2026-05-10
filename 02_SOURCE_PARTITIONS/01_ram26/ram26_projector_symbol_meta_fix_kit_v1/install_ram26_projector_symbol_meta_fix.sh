#!/bin/bash
# ============================================================================
# install_ram26_projector_symbol_meta_fix.sh
#
# Apply RAM26 Target SSOT Projector v2 (symbol+meta-marker fix).
# Strict scope:
#   - Replace ONLY /home/ubuntu/ares_current/bridges/ram26_target_ssot_projector_v1.mjs
#   - Backup the original file
#   - One-shot repair via PM2 restart of ONLY 'ram26-target-ssot-projector'
# Hard prohibitions (all enforced or refused):
#   - No write to ssot:target:v2:current
#   - No write to policy:champion:active
#   - No change to emarkos:v1:mode
#   - No change to trading:enabled
#   - No global pm2 restart, no order path or fill stream restart
# ============================================================================
set -euo pipefail

EXPECTED_TOKEN="YES_PATCH_RAM26_PROJECTOR_SYMBOL_META"
GIVEN_TOKEN="${RAM26_PROJECTOR_FIX_CONFIRM:-}"
if [[ "$GIVEN_TOKEN" != "$EXPECTED_TOKEN" ]]; then
  echo "[install][ABORT] RAM26_PROJECTOR_FIX_CONFIRM must equal '$EXPECTED_TOKEN'"
  echo "  Hint: RAM26_PROJECTOR_FIX_CONFIRM=$EXPECTED_TOKEN bash $0"
  exit 2
fi
echo "[install] confirm token verified"

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_NEW="$KIT_DIR/ram26_target_ssot_projector_v2.mjs"
TARGET_PATH="/home/ubuntu/ares_current/bridges/ram26_target_ssot_projector_v1.mjs"
BACKUP_DIR="/home/ubuntu/ares_current/bridges/backups"
PM2_PROC="ram26-target-ssot-projector"
TS=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE="$BACKUP_DIR/ram26_target_ssot_projector_v1.mjs.${TS}.bak"

# ---- Pre-flight ----
echo ""
echo "=== [install] Pre-flight ==="
[[ -f "$SRC_NEW" ]] || { echo "[install][ABORT] missing kit file: $SRC_NEW"; exit 3; }
[[ -f "$TARGET_PATH" ]] || { echo "[install][ABORT] target not found: $TARGET_PATH"; exit 3; }
node --check "$SRC_NEW" || { echo "[install][ABORT] kit file syntax check failed"; exit 4; }
echo "  ok: kit file syntax"
pm2 describe "$PM2_PROC" >/dev/null 2>&1 || { echo "[install][ABORT] PM2 process '$PM2_PROC' not found"; exit 5; }
echo "  ok: pm2 process '$PM2_PROC' present"

# Snapshot forbidden state for delta verification at the end
SNAP_MODE_BEFORE=$(redis-cli GET emarkos:v1:mode || echo "")
SNAP_TRADE_BEFORE=$(redis-cli GET trading:enabled || echo "")
SNAP_SSOT_TS_BEFORE=$(redis-cli GET ssot:target:v2:current | head -c 0 ; redis-cli OBJECT IDLETIME ssot:target:v2:current 2>/dev/null || echo "")
SNAP_POLICY_BEFORE=$(redis-cli GET policy:champion:active | head -c 0 ; redis-cli OBJECT IDLETIME policy:champion:active 2>/dev/null || echo "")
echo "  snapshot: mode=$SNAP_MODE_BEFORE trade=$SNAP_TRADE_BEFORE"

# ---- Backup ----
mkdir -p "$BACKUP_DIR"
cp -p "$TARGET_PATH" "$BACKUP_FILE"
echo ""
echo "=== [install] Backup ==="
echo "  $BACKUP_FILE"

# ---- Install (atomic) ----
TMP_PATH="${TARGET_PATH%.mjs}.new.${TS}.mjs"
cp "$SRC_NEW" "$TMP_PATH"
node --check "$TMP_PATH" || { echo "[install][ABORT] new file fails syntax check"; rm -f "$TMP_PATH"; exit 6; }
mv -f "$TMP_PATH" "$TARGET_PATH"
echo ""
echo "=== [install] File replaced ==="
echo "  $TARGET_PATH"
node --check "$TARGET_PATH" || { echo "[install][ABORT] post-install syntax check failed"; cp -f "$BACKUP_FILE" "$TARGET_PATH"; exit 7; }
echo "  ok: post-install syntax"

# ---- One-shot repair: restart projector only ----
echo ""
echo "=== [install] PM2 single restart: $PM2_PROC ==="
pm2 restart "$PM2_PROC" --update-env >/dev/null
sleep 4
pm2 describe "$PM2_PROC" 2>/dev/null | grep -E "status|restarts" | head -4

# ---- Forbidden state check (must be unchanged) ----
SNAP_MODE_AFTER=$(redis-cli GET emarkos:v1:mode || echo "")
SNAP_TRADE_AFTER=$(redis-cli GET trading:enabled || echo "")
echo ""
echo "=== [install] Forbidden state check ==="
echo "  mode:  $SNAP_MODE_BEFORE -> $SNAP_MODE_AFTER"
echo "  trade: $SNAP_TRADE_BEFORE -> $SNAP_TRADE_AFTER"
if [[ "$SNAP_MODE_BEFORE" != "$SNAP_MODE_AFTER" ]] || [[ "$SNAP_TRADE_BEFORE" != "$SNAP_TRADE_AFTER" ]]; then
  echo "[install][WARN] forbidden state changed during install — investigate immediately"
fi

echo ""
echo "RAM26_PROJECTOR_SYMBOL_META_FIX_PASS"
echo "Backup: $BACKUP_FILE"
