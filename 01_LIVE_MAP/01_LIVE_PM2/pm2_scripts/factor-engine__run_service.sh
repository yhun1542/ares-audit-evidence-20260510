#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "usage: $0 <service-name> [args...]" >&2
  exit 2
fi
SERVICE_NAME="$1"
shift || true
ENV_FILE="${ARES_ENV_FILE:-/etc/ares/ares.env}"
REGISTRY_FILE="${ARES_REGISTRY_FILE:-/etc/ares/services.registry.yaml}"
[[ -f "$ENV_FILE" ]] || { echo "FATAL: missing env file: $ENV_FILE" >&2; exit 101; }
[[ -f "$REGISTRY_FILE" ]] || { echo "FATAL: missing registry file: $REGISTRY_FILE" >&2; exit 102; }
set -a
source "$ENV_FILE"
# [STRUCT-FIX-REDIS-SSOT-2026-05-04] Redis credentials are single-source-of-truth values.
SSOT_REDIS_ENV="${ARES_REDIS_SSOT_FILE:-/etc/ares/redis.env}"
if [[ -f "$SSOT_REDIS_ENV" ]]; then
  source "$SSOT_REDIS_ENV"
fi
set +a
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_JSON="$($PYTHON_BIN - <<'PY' "$REGISTRY_FILE" "$SERVICE_NAME"
import json, sys, yaml
reg = yaml.safe_load(open(sys.argv[1]))
name = sys.argv[2]
svc = reg.get('services', {}).get(name)
if not svc:
    raise SystemExit(f'unknown service: {name}')
print(json.dumps(svc))
PY
)"
export SERVICE_NAME
export REGISTRY_FILE
export ARES_ENV_FILE="$ENV_FILE"
export SERVICE_JSON

# Extract CWD first and cd before preflight
CWD="$($PYTHON_BIN - <<'PY' "$SERVICE_JSON"
import json, sys
print(json.loads(sys.argv[1])["cwd"])
PY
)"
cd "$CWD"

# Now run preflight (cwd is correct)
"$PYTHON_BIN" /etc/ares/bin/preflight_env_guard.py

EXEC_PATH="$($PYTHON_BIN - <<'PY' "$SERVICE_JSON"
import json, sys
print(json.loads(sys.argv[1])["exec"])
PY
)"
INTERPRETER="$($PYTHON_BIN - <<'PY' "$SERVICE_JSON"
import json, sys
print(json.loads(sys.argv[1]).get("interpreter", "python3"))
PY
)"
exec "$INTERPRETER" "$EXEC_PATH" "$@"
