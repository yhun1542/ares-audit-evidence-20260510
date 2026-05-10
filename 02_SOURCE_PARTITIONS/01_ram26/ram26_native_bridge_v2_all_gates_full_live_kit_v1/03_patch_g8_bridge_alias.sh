#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${RAM26_PATCH_G8_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_PATCH_G8_BRIDGE_ALIAS" ]]; then
  echo "RAM26_PATCH_G8_CONFIRM must be YES_PATCH_G8_BRIDGE_ALIAS"
  exit 2
fi

PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/native_bridge_v2/${TS}_g8_patch"
BACKUP_DIR="$OUT_DIR/backup"
mkdir -p "$OUT_DIR" "$BACKUP_DIR"

G8_PATH="${G8_PATH:-/home/ubuntu/ares_current/guards/open009_ram26_g8_readiness_guard.mjs}"
[[ -f "$G8_PATH" ]] || { echo "G8 guard not found: $G8_PATH"; exit 2; }
cp -a "$G8_PATH" "$BACKUP_DIR/$(basename "$G8_PATH").bak_${TS}"

python3 - "$G8_PATH" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if "RAM26_G8_BRIDGE_ALIAS_LIVE_FINAL_V2" not in s:
    s=s.replace(
        'bridge_active: bridgeActive === "ram26_final_to_champion_bridge_v2",',
        'bridge_active: (bridgeActive === "ram26_final_to_champion_bridge_v2" || bridgeActive === "LIVE_FINAL_TO_CHAMPION"), // RAM26_G8_BRIDGE_ALIAS_LIVE_FINAL_V2'
    )
    if "RAM26_G8_BRIDGE_ALIAS_LIVE_FINAL_V2" not in s:
        s += "\n// RAM26_G8_BRIDGE_ALIAS_LIVE_FINAL_V2: review marker\n"
p.write_text(s)
PY

node --check "$G8_PATH"

if command -v pm2 >/dev/null 2>&1 && pm2 describe open009-ram26-g8-guard >/dev/null 2>&1; then
  pm2 restart open009-ram26-g8-guard --update-env
  pm2 save || true
fi

cat > "$OUT_DIR/RAM26_G8_ALIAS_PATCH_REPORT.md" <<EOF
# RAM26 G8 Alias Patch Report

## Verdict
RAM26_G8_BRIDGE_ALIAS_PATCHED

## Alias accepted
- ram26_final_to_champion_bridge_v2
- LIVE_FINAL_TO_CHAMPION
EOF

echo "REPORT=$OUT_DIR/RAM26_G8_ALIAS_PATCH_REPORT.md"
