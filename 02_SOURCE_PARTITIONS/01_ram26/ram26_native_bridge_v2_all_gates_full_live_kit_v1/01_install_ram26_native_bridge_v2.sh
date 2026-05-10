#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM="${RAM26_BRIDGE_V2_INSTALL_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_INSTALL_RAM26_NATIVE_BRIDGE_V2" ]]; then
  echo "RAM26_BRIDGE_V2_INSTALL_CONFIRM must be YES_INSTALL_RAM26_NATIVE_BRIDGE_V2"
  exit 2
fi

ARES_ROOT="${ARES_ROOT:-/home/ubuntu/ares_current}"
DEST_DIR="${ARES_ROOT}/ops"
DEST_FILE="${DEST_DIR}/ram26_final_to_champion_bridge_v2.mjs"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/native_bridge_v2/${TS}_install"
BACKUP_DIR="${OUT_DIR}/backup"
mkdir -p "$OUT_DIR" "$BACKUP_DIR" "$DEST_DIR"

LOG="$OUT_DIR/run.log"
exec > >(tee -a "$LOG") 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$SCRIPT_DIR/ram26_final_to_champion_bridge_v2.mjs" ]] || { echo "bridge source missing"; exit 2; }

[[ -f "$DEST_FILE" ]] && cp -a "$DEST_FILE" "$BACKUP_DIR/$(basename "$DEST_FILE").bak_${TS}"
cp -a "$SCRIPT_DIR/ram26_final_to_champion_bridge_v2.mjs" "$DEST_FILE"
chmod +x "$DEST_FILE"
node --check "$DEST_FILE"

cat > "$OUT_DIR/RAM26_BRIDGE_V2_INSTALL_REPORT.md" <<EOF
# RAM26 Native Bridge v2 Install Report

## Verdict
RAM26_NATIVE_BRIDGE_V2_INSTALLED

## Installed
- \`${DEST_FILE}\`

## Safety
- No Redis mutation except file install.
- No LIVE.
- No trading enable.
EOF

echo "REPORT=$OUT_DIR/RAM26_BRIDGE_V2_INSTALL_REPORT.md"
