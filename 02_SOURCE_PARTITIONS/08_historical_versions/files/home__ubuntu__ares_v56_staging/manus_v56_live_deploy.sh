#!/usr/bin/env bash
set -euo pipefail

# V5.6 live deploy script
# Deploys:
#   - ares_v55_live_autopilot.py (primary decision service)
#   - nexus_bridge_v56_compat.py (Claude-compatible Redis bridge)
#   - config + PM2 ecosystem + preflight

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/ares_v56_live}"
DB_PATH="${DB_PATH:-/home/ubuntu/ares_work/ares_x_v11_0.db}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
PM2_PREFIX="${PM2_PREFIX:-ares-v56}"
START_SERVICES="${START_SERVICES:-1}"
KILL_OLD="${KILL_OLD:-0}"

required_files=(
  "ares_v55_live_autopilot.py"
  "v55_live_autopilot_config.json"
  "engine_v54_aggressive_realistic.py"
  "v54a_aggressive_realistic_config.json"
  "nexus_bridge_v56_compat.py"
  "manus_v56_preflight.py"
)

for f in "${required_files[@]}"; do
  if [[ ! -f "$SCRIPT_DIR/$f" ]]; then
    echo "[FATAL] missing file: $SCRIPT_DIR/$f" >&2
    exit 2
  fi
done

mkdir -p "$DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR/releases"
STAMP="$(date +%Y%m%d_%H%M%S)"
RELEASE_DIR="$DEPLOY_DIR/releases/$STAMP"
mkdir -p "$RELEASE_DIR"

# backup existing live symlink target if exists
if [[ -L "$DEPLOY_DIR/current" || -d "$DEPLOY_DIR/current" ]]; then
  PREV_TARGET="$(readlink -f "$DEPLOY_DIR/current" || true)"
  if [[ -n "${PREV_TARGET:-}" && -d "$PREV_TARGET" ]]; then
    echo "[INFO] previous release: $PREV_TARGET"
  fi
fi

cp "$SCRIPT_DIR/ares_v55_live_autopilot.py" "$RELEASE_DIR/"
cp "$SCRIPT_DIR/v55_live_autopilot_config.json" "$RELEASE_DIR/"
cp "$SCRIPT_DIR/engine_v54_aggressive_realistic.py" "$RELEASE_DIR/"
cp "$SCRIPT_DIR/v54a_aggressive_realistic_config.json" "$RELEASE_DIR/"
cp "$SCRIPT_DIR/nexus_bridge_v56_compat.py" "$RELEASE_DIR/"
cp "$SCRIPT_DIR/manus_v56_preflight.py" "$RELEASE_DIR/"

python3 - <<PY
import json, pathlib
release = pathlib.Path(r"$RELEASE_DIR")
cfg = json.loads((release / 'v55_live_autopilot_config.json').read_text())
cfg['execution']['db_path'] = r"$DB_PATH"
cfg['execution']['redis_url'] = r"$REDIS_URL"
cfg['execution']['engine_path'] = str((release / 'engine_v54_aggressive_realistic.py').resolve())
cfg['execution']['alpha_config_path'] = str((release / 'v54a_aggressive_realistic_config.json').resolve())
cfg['execution']['state_path'] = str((release / 'v55_live_state.pkl').resolve())
cfg['audit_dir'] = str((release / 'audit_v55').resolve())
(release / 'v55_live_autopilot_config.json').write_text(json.dumps(cfg, indent=2))
PY

cat > "$RELEASE_DIR/ecosystem.v56.config.js" <<EOF
module.exports = {
  apps: [
    {
      name: '$PM2_PREFIX-live',
      cwd: '$RELEASE_DIR',
      script: 'python3',
      args: 'ares_v55_live_autopilot.py --config v55_live_autopilot_config.json',
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      kill_timeout: 10000,
      out_file: '$RELEASE_DIR/logs/live.out.log',
      error_file: '$RELEASE_DIR/logs/live.err.log',
      merge_logs: true,
      env: { PYTHONUNBUFFERED: '1' }
    },
    {
      name: '$PM2_PREFIX-bridge',
      cwd: '$RELEASE_DIR',
      script: 'python3',
      args: 'nexus_bridge_v56_compat.py --redis-url $REDIS_URL',
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      kill_timeout: 10000,
      out_file: '$RELEASE_DIR/logs/bridge.out.log',
      error_file: '$RELEASE_DIR/logs/bridge.err.log',
      merge_logs: true,
      env: { PYTHONUNBUFFERED: '1' }
    }
  ]
}
EOF
mkdir -p "$RELEASE_DIR/logs"

echo "[INFO] running preflight"
python3 "$RELEASE_DIR/manus_v56_preflight.py" \
  --config "$RELEASE_DIR/v55_live_autopilot_config.json" \
  --output "$RELEASE_DIR/preflight_report.json"

ln -sfn "$RELEASE_DIR" "$DEPLOY_DIR/current"

if [[ "$START_SERVICES" == "1" ]]; then
  if ! command -v pm2 >/dev/null 2>&1; then
    echo "[FATAL] pm2 not found. install pm2 first." >&2
    exit 3
  fi
  pm2 startOrReload "$RELEASE_DIR/ecosystem.v56.config.js"
  pm2 save || true
fi

if [[ "$KILL_OLD" == "1" ]]; then
  for app in ares-kernel-daemon ares-feedback-daemon ares-nexus-alpha ares-nexus-gate; do
    pm2 delete "$app" >/dev/null 2>&1 || true
  done
fi

echo "[OK] deployed release: $RELEASE_DIR"
echo "[NEXT] pm2 status: pm2 ls"
echo "[NEXT] tail logs: pm2 logs $PM2_PREFIX-live --lines 50"
echo "[NEXT] bridge logs: pm2 logs $PM2_PREFIX-bridge --lines 50"
echo "[NEXT] readiness: redis-cli GET ares:v55:live:readiness"
echo "[NEXT] target: redis-cli GET nexus:target:latest"
