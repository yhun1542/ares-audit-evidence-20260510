#!/usr/bin/env bash
# integrity-loop : runs ares-verify every 300s, alerts and (optionally) auto-repairs on breach.
#
# Install via PM2:
#   pm2 start /home/ubuntu/ares_scripts/integrity_loop.sh \
#     --name integrity-loop \
#     --time \
#     -o /home/ubuntu/.pm2/logs/integrity-loop-out.log \
#     -e /home/ubuntu/.pm2/logs/integrity-loop-err.log
#   pm2 save
#
# Environment:
#   ARES_MANIFEST_PATH   path to manifest.json (default /home/ubuntu/ares_scripts/manifest.json)
#   ARES_VERIFY_PATH     path to ares_verify.py
#   ARES_REPAIR_PATH     path to post_live_integrity_repair.sh
#   AUTO_REPAIR_ON_BREACH true|false (default false; alert-only)
#   LOOP_INTERVAL_SEC    default 300

set -Eeuo pipefail

MANIFEST="${ARES_MANIFEST_PATH:-/home/ubuntu/ares_scripts/manifest.json}"
VERIFY="${ARES_VERIFY_PATH:-/home/ubuntu/ares_scripts/ares_verify.py}"
REPAIR="${ARES_REPAIR_PATH:-/home/ubuntu/ares_scripts/post_live_integrity_repair.sh}"
AUTO_REPAIR="${AUTO_REPAIR_ON_BREACH:-false}"
INTERVAL="${LOOP_INTERVAL_SEC:-300}"

if [[ -f /etc/ares/redis.env ]]; then
  set -a; source /etc/ares/redis.env; set +a
fi

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

while true; do
  echo "[$(ts)] verify start manifest=${MANIFEST}"
  if RESULT_JSON=$(python3 "${VERIFY}" "${MANIFEST}" 2>&1); then
    echo "[$(ts)] verify ALL_PASS"
  else
    EXIT=$?
    echo "[$(ts)] verify BREACH exit=${EXIT}"
    echo "${RESULT_JSON}" | head -200

    if [[ "${EXIT}" -ge 2 && "${AUTO_REPAIR}" == "true" ]]; then
      echo "[$(ts)] critical breach detected, invoking auto-repair"
      POST_LIVE_REPAIR_CONFIRM=YES_POST_LIVE_INTEGRITY_REPAIR \
        bash "${REPAIR}" || echo "[$(ts)] auto-repair failed"
    else
      echo "[$(ts)] alert-only mode (AUTO_REPAIR_ON_BREACH=${AUTO_REPAIR})"
    fi
  fi

  sleep "${INTERVAL}"
done
