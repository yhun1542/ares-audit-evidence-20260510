#!/usr/bin/env bash
set -Eeuo pipefail
CONFIRM="${ARES_HARDEN_ROLLBACK_CONFIRM:-}"
if [[ "$CONFIRM" != "YES_ROLLBACK_ARES_HARDENING_PROCESSES" ]]; then
  echo "ARES_HARDEN_ROLLBACK_CONFIRM must be YES_ROLLBACK_ARES_HARDENING_PROCESSES"
  exit 2
fi
for app in ram26-risk-rate-publisher kis-ws-status-synthesizer; do
  if command -v pm2 >/dev/null 2>&1 && pm2 describe "$app" >/dev/null 2>&1; then
    pm2 delete "$app" || true
  fi
done
pm2 save || true
echo "Deleted sidecar processes: ram26-risk-rate-publisher, kis-ws-status-synthesizer"
echo "Projector file rollback must be performed from backup directory in the install report."
