#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${RAM26_NATIVE_ROLLBACK_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_ROLLBACK_NATIVE_BRIDGE_FULL_LIVE" ]]; then
  echo "RAM26_NATIVE_ROLLBACK_CONFIRM must be YES_ROLLBACK_NATIVE_BRIDGE_FULL_LIVE"
  exit 2
fi
REDIS_ENV="${REDIS_ENV:-/etc/ares/redis.env}"
if [[ -f "$REDIS_ENV" ]]; then set -a; source "$REDIS_ENV"; set +a; fi
REDIS_CLI=(redis-cli)
if [[ -n "${REDIS_URL:-}" ]]; then
  if [[ "$REDIS_URL" == rediss://* ]]; then REDIS_CLI=(redis-cli -u "$REDIS_URL" --tls --insecure); else REDIS_CLI=(redis-cli -u "$REDIS_URL"); fi
fi
"${REDIS_CLI[@]}" SET trading:enabled false
"${REDIS_CLI[@]}" SET trading:enabled:override false
"${REDIS_CLI[@]}" SET manual_order_submission_enabled false
"${REDIS_CLI[@]}" SET emarkos:v1:mode SAFE
"${REDIS_CLI[@]}" SET ops:halt:active RAM26_NATIVE_BRIDGE_ROLLBACK
"${REDIS_CLI[@]}" SET ops:halt:request RAM26_NATIVE_BRIDGE_ROLLBACK
echo "RAM26 native bridge rollback applied."
