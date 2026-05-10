#!/usr/bin/env bash
set -Eeuo pipefail

CONFIRM="${RAM26_BRIDGE_V2_STAGE_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_RUN_RAM26_BRIDGE_V2_STAGE" ]]; then
  echo "RAM26_BRIDGE_V2_STAGE_CONFIRM must be YES_RUN_RAM26_BRIDGE_V2_STAGE"
  exit 2
fi

REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
BRIDGE_FILE="${BRIDGE_FILE:-/home/ubuntu/ares_current/ops/ram26_final_to_champion_bridge_v2.mjs}"
PROMOTION_ROOT="${PROMOTION_ROOT:-/home/ubuntu/ares_promotions/RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${PROMOTION_ROOT}/native_bridge_v2/${TS}_stage"
mkdir -p "$OUT_DIR"

LOG="$OUT_DIR/run.log"
exec > >(tee -a "$LOG") 2>&1

[[ -f "$BRIDGE_FILE" ]] || { echo "BRIDGE_FILE missing: $BRIDGE_FILE"; exit 2; }

if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi

set +e
REDIS_URL="${REDIS_URL:-}" ARES_REDIS_URL="${REDIS_URL:-}" REDIS_PASSWORD="${REDIS_PASSWORD:-}" \
RAM26_STAGED_MAX_AGE_SEC="${RAM26_STAGED_MAX_AGE_SEC:-172800}" \
node "$BRIDGE_FILE" --once true --publish-staged true --feature-max-age-sec "${RAM26_STAGED_MAX_AGE_SEC:-172800}" \
  > "$OUT_DIR/bridge_stdout.json" 2> "$OUT_DIR/bridge_stderr.txt"
RC="$?"
set -e
echo "$RC" > "$OUT_DIR/bridge_rc.txt"

python3 - "$OUT_DIR/bridge_stdout.json" "$OUT_DIR/stage_gate.json" <<'PY'
import json,sys
from pathlib import Path
try:
    d=json.load(open(sys.argv[1]))
except Exception as e:
    d={"pass":False,"parse_error":str(e)}
Path(sys.argv[2]).write_text(json.dumps(d,indent=2,ensure_ascii=False))
print(json.dumps(d,indent=2,ensure_ascii=False))
PY

if [[ "$RC" -ne 0 ]]; then
  echo "Bridge stage failed."
  cat "$OUT_DIR/bridge_stderr.txt"
  exit "$RC"
fi

cat > "$OUT_DIR/RAM26_BRIDGE_V2_STAGE_REPORT.md" <<EOF
# RAM26 Bridge v2 Stage Report

## Verdict
RAM26_BRIDGE_V2_STAGE_PASS

## Artifacts
- stdout: \`${OUT_DIR}/bridge_stdout.json\`
- stderr: \`${OUT_DIR}/bridge_stderr.txt\`
- gate: \`${OUT_DIR}/stage_gate.json\`
EOF

echo "REPORT=$OUT_DIR/RAM26_BRIDGE_V2_STAGE_REPORT.md"
