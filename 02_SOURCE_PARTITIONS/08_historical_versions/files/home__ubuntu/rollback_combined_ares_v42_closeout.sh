#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

# FIX-R3: Concurrent execution lock via flock (shares lock with deploy)
LOCK_FILE="/tmp/ares_deploy.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[ROLLBACK][ERROR] Another deploy/rollback is already running (lock: $LOCK_FILE)" >&2
  exit 1
fi

log() { printf '[ROLLBACK] %s\n' "$*"; }
warn() { printf '[ROLLBACK][WARN] %s\n' "$*" >&2; }
fail() { printf '[ROLLBACK][ERROR] %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

# FIX-R4: Signal trap for cleanup on interruption
cleanup() {
  local rc=$?
  if (( rc != 0 )); then
    warn "rollback interrupted (exit code $rc); system may be in partial state"
    warn "manual intervention may be required"
  fi
  flock -u 200 2>/dev/null || true
}
trap cleanup EXIT

# FIX-R5: Market-hours safety guard (US market 09:30-16:00 ET)
FORCE_ROLLBACK="${FORCE_ROLLBACK:-0}"
if [[ "$FORCE_ROLLBACK" != "1" ]]; then
  current_hour_et=$(TZ='America/New_York' date +%H)
  current_min_et=$(TZ='America/New_York' date +%M)
  current_dow=$(TZ='America/New_York' date +%u)
  total_min=$(( current_hour_et * 60 + current_min_et ))
  market_open=570
  market_close=960
  if (( current_dow <= 5 && total_min >= market_open && total_min <= market_close )); then
    fail "rollback blocked: US market hours ($(TZ='America/New_York' date '+%H:%M ET %A')). Use FORCE_ROLLBACK=1 to override."
  fi
fi

pm2_has_app() {
  local app="$1"
  pm2 pid "$app" 2>/dev/null | awk 'NF{print}' | grep -vq '^0$'
}

pm2_first_available_app() {
  local csv="$1"
  local app
  IFS=',' read -r -a _apps <<< "$csv"
  for app in "${_apps[@]}"; do
    app="$(printf '%s' "$app" | xargs)"
    [[ -z "$app" ]] && continue
    if pm2_has_app "$app"; then
      printf '%s\n' "$app"
      return 0
    fi
  done
  return 1
}

# FIX-R1: Add restart_pm2_app helper (mirrors deploy)
restart_pm2_app() {
  local app="$1"
  local required="${2:-1}"
  if [[ -z "$app" ]]; then
    return 0
  fi
  if pm2_has_app "$app"; then
    log "restarting PM2 app: $app"
    pm2 restart "$app" --update-env
  elif [[ "$required" == "1" ]]; then
    fail "required PM2 app not found: $app"
  else
    warn "optional PM2 app not found: $app"
  fi
}

# FIX-R1: Add restart_pm2_apps_csv helper (mirrors deploy)
restart_pm2_apps_csv() {
  local csv="$1"
  local required="${2:-1}"
  local app
  IFS=',' read -r -a _apps <<< "$csv"
  for app in "${_apps[@]}"; do
    app="$(printf '%s' "$app" | xargs)"
    [[ -z "$app" ]] && continue
    restart_pm2_app "$app" "$required"
  done
}

remove_cron_marker() {
  local marker="$1"
  local tmp
  tmp=$(mktemp)
  crontab -l > "$tmp" 2>/dev/null || true
  if grep -Fq "$marker" "$tmp"; then
    grep -Fv "$marker" "$tmp" > "$tmp.new" || true
    mv "$tmp.new" "$tmp"
    crontab "$tmp"
    rm -f "$tmp"
    return 0
  fi
  rm -f "$tmp"
  return 1
}

need_cmd git
need_cmd pm2
need_cmd node
need_cmd crontab

MANIFEST_INPUT="${1:-${STATE_MANIFEST:-/tmp/ares_combined_latest/manifest.env}}"
[[ -f "$MANIFEST_INPUT" ]] || fail "manifest not found: $MANIFEST_INPUT"
# shellcheck disable=SC1090
source "$MANIFEST_INPUT"

[[ -n "${REPO_DIR:-}" ]] || fail "manifest missing REPO_DIR"
[[ -n "${PRE_GIT_COMMIT:-}" ]] || fail "manifest missing PRE_GIT_COMMIT"
[[ -n "${PHS_TARGET:-}" ]] || fail "manifest missing PHS_TARGET"
[[ -n "${PHS_BACKUP:-}" ]] || fail "manifest missing PHS_BACKUP"

cd "$REPO_DIR"

log "rollback using manifest: $MANIFEST_INPUT"
log "restoring git commit: $PRE_GIT_COMMIT"

if [[ -n "${PRE_GIT_BRANCH:-}" && "$PRE_GIT_BRANCH" != "HEAD" ]]; then
  git checkout -f "$PRE_GIT_BRANCH" || warn "git checkout branch failed: $PRE_GIT_BRANCH"
fi
git reset --hard "$PRE_GIT_COMMIT"

if [[ -f "$PHS_BACKUP" ]]; then
  sudo cp "$PHS_BACKUP" "$PHS_TARGET"
  node --check "$PHS_TARGET"
  log "restored PHS file from backup: $PHS_BACKUP"
else
  warn "PHS backup not found: $PHS_BACKUP"
fi

if [[ "${POST_TRADE_CRON_ADDED:-0}" == "1" ]]; then
  if remove_cron_marker "${CRON_MARKER:-# ARES_POST_TRADE_ANALYZER}"; then
    log "removed post-trade cron marker"
  else
    warn "post-trade cron marker not present"
  fi
else
  log "manifest says cron was not added by this deployment; leaving crontab unchanged"
fi

# FIX-R1: Replace ecosystem-wide restart with individual per-app restarts
# (ecosystem restart is dangerous due to stale PM2 entries — confirmed in v3.6.5 deployment incident)
TRACK_B_PM2_APPS="${TRACK_B_PM2_APPS:-final-trade-gate,halt-controller,ares-invariant-checker,peak-safety-clamp}"
log "restarting Track B core PM2 apps individually"
restart_pm2_apps_csv "$TRACK_B_PM2_APPS" 0

# Restart PHS
if [[ -n "${PM2_PHS_APP:-}" ]]; then
  restart_pm2_app "$PM2_PHS_APP" 0
fi

# Restart OFG from fallback set
OFG_APP_TO_RESTART="$(pm2_first_available_app "${PM2_OFG_APP_FALLBACKS:-${PM2_OFG_APP:-ofg-unifier-v6},ofg-unifier-v5,order-flow-governor}" || true)"
if [[ -n "$OFG_APP_TO_RESTART" ]]; then
  restart_pm2_app "$OFG_APP_TO_RESTART" 0
else
  warn "PM2 OFG app not found in fallback set"
fi

# FIX-R2: Restart equity-calculator from fallback set (mirrors deploy behavior)
if [[ "${RESTART_EQUITY_CALCULATOR:-1}" == "1" ]]; then
  EQ_APP_TO_RESTART="$(pm2_first_available_app "${PM2_EQ_APP_FALLBACKS:-${PM2_EQ_APP:-equity-calculator}}" || true)"
  if [[ -n "$EQ_APP_TO_RESTART" ]]; then
    restart_pm2_app "$EQ_APP_TO_RESTART" 0
  else
    warn "PM2 equity-calculator app not found in fallback set"
  fi
fi

pm2 save

log "rollback complete"
log "IMPORTANT: run verify_combined_ares_v42_closeout.sh to confirm rollback health"

# FIX-R6: Auto-invoke verify if script is found alongside rollback
VERIFY_SCRIPT="$(dirname "$(readlink -f "$0")")/verify_combined_ares_v42_closeout.sh"
if [[ -f "$VERIFY_SCRIPT" && "${AUTO_VERIFY:-0}" == "1" ]]; then
  log "AUTO_VERIFY=1: running post-rollback verification"
  bash "$VERIFY_SCRIPT" || warn "post-rollback verify reported failures"
fi
