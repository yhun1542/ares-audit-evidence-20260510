#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

log() { printf '[ROLLBACK] %s\n' "$*"; }
warn() { printf '[ROLLBACK][WARN] %s\n' "$*" >&2; }
fail() { printf '[ROLLBACK][ERROR] %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

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
[[ -n "${ECOSYSTEM_CONFIG:-}" ]] || fail "manifest missing ECOSYSTEM_CONFIG"
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

pm2 restart "$ECOSYSTEM_CONFIG"
if [[ -n "${PM2_PHS_APP:-}" ]]; then
  pm2 restart "$PM2_PHS_APP" --update-env || warn "failed to restart PM2 PHS app: $PM2_PHS_APP"
fi
OFG_APP_TO_RESTART="$(pm2_first_available_app "${PM2_OFG_APP_FALLBACKS:-${PM2_OFG_APP:-ofg-unifier-v5},order-flow-governor}" || true)"
if [[ -n "$OFG_APP_TO_RESTART" ]]; then
  pm2 restart "$OFG_APP_TO_RESTART" --update-env || warn "failed to restart PM2 OFG app: $OFG_APP_TO_RESTART"
fi
pm2 save

log "rollback complete"
log "recommended next step: run verify_combined_ares_v42.sh"
