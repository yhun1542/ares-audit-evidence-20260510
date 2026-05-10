#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

log() { printf '[DEPLOY] %s\n' "$*"; }
warn() { printf '[DEPLOY][WARN] %s\n' "$*" >&2; }
fail() { printf '[DEPLOY][ERROR] %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

first_existing_dir() {
  local p
  for p in "$@"; do
    [[ -d "$p" ]] && { printf '%s\n' "$p"; return 0; }
  done
  return 1
}

first_existing_file() {
  local p
  for p in "$@"; do
    [[ -f "$p" ]] && { printf '%s\n' "$p"; return 0; }
  done
  return 1
}

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

redis_get() {
  local key="$1"
  redis-cli -u "$REDIS_CONN" --raw GET "$key" 2>/dev/null || true
}

wait_for_redis_key() {
  local key="$1"
  local timeout_sec="$2"
  local start now value
  start=$(date +%s)
  while true; do
    value=$(redis_get "$key")
    if [[ -n "$value" && "$value" != "(nil)" ]]; then
      return 0
    fi
    now=$(date +%s)
    if (( now - start >= timeout_sec )); then
      return 1
    fi
    sleep 2
  done
}

wait_for_all_redis_keys() {
  local timeout_sec="$1"
  shift
  local key
  for key in "$@"; do
    log "waiting for Redis key: $key"
    wait_for_redis_key "$key" "$timeout_sec" || return 1
  done
}

resolve_ecosystem_config() {
  if [[ -n "${ECOSYSTEM_CONFIG_OVERRIDE:-}" ]]; then
    printf '%s\n' "$ECOSYSTEM_CONFIG_OVERRIDE"
    return 0
  fi
  first_existing_file \
    "$REPO_DIR/ecosystem.config.cjs" \
    "$REPO_DIR/docs/patches_v2/ecosystem.config.cjs"
}

install_or_update_cron() {
  local marker="$1"
  local line="$2"
  local tmp changed=0
  tmp=$(mktemp)
  crontab -l > "$tmp" 2>/dev/null || true
  if grep -Fq "$marker" "$tmp"; then
    if ! grep -Fq "$line" "$tmp"; then
      grep -Fv "$marker" "$tmp" > "$tmp.new" || true
      mv "$tmp.new" "$tmp"
      printf '%s\n' "$line" >> "$tmp"
      changed=1
    fi
  else
    printf '%s\n' "$line" >> "$tmp"
    changed=1
  fi
  crontab "$tmp"
  rm -f "$tmp"
  return "$changed"
}

DEFAULT_REPO_DIR="$(first_existing_dir \
  /home/ubuntu/ARES-KIS-US-AUTOPILOT \
  /home/ubuntu/ares-trading-system \
  /home/ubuntu/aub-trading-system || true)"
REPO_DIR="${REPO_DIR:-$DEFAULT_REPO_DIR}"
[[ -n "$REPO_DIR" ]] || fail "could not auto-detect REPO_DIR; export REPO_DIR=/path/to/repo"

DEFAULT_PHS_TARGET="$(first_existing_file \
  /home/ubuntu/ARES-KIS-US-AUTOPILOT/guards/position-hard-stop.mjs \
  /home/ubuntu/ares-trading-system/services/guardrails/position-hard-stop.mjs || true)"
PHS_TARGET="${PHS_TARGET:-$DEFAULT_PHS_TARGET}"
[[ -n "$PHS_TARGET" ]] || fail "could not auto-detect PHS_TARGET; export PHS_TARGET=/path/to/position-hard-stop.mjs"

GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-main}"
PHS_NEW="${PHS_NEW:-/tmp/position-hard-stop.mjs.new}"
PM2_PHS_APP="${PM2_PHS_APP:-position-hard-stop}"
PM2_OFG_APP="${PM2_OFG_APP:-ofg-unifier-v6}"
PM2_OFG_APP_FALLBACKS="${PM2_OFG_APP_FALLBACKS:-$PM2_OFG_APP,ofg-unifier-v5,order-flow-governor}"
REQUIRE_P3_FILES="${REQUIRE_P3_FILES:-1}"
INSTALL_POST_TRADE_CRON="${INSTALL_POST_TRADE_CRON:-1}"
POST_TRADE_SCHEDULE="${POST_TRADE_SCHEDULE:-0 23 * * *}"
POST_TRADE_ANALYZER="${POST_TRADE_ANALYZER:-$REPO_DIR/ares_post_trade_analyzer.py}"
OFG_V2_PATH="${OFG_V2_PATH:-$REPO_DIR/order_flow_governor_v2.py}"
POST_TRADE_LOG="${POST_TRADE_LOG:-/var/log/ares_post_trade_analyzer.log}"
CRON_MARKER="${CRON_MARKER:-# ARES_POST_TRADE_ANALYZER}"
TRACK_B_HEALTH_TIMEOUT_SEC="${TRACK_B_HEALTH_TIMEOUT_SEC:-120}"
TRACK_A_HEALTH_TIMEOUT_SEC="${TRACK_A_HEALTH_TIMEOUT_SEC:-120}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
REDIS_CONN="${REDIS_URL:-${ARES_REDIS_URL:-}}"

HARD_STOP_CHECK_INTERVAL_MS="${HARD_STOP_CHECK_INTERVAL_MS:-60000}"
HARD_STOP_REISSUE_AFTER_MS="${HARD_STOP_REISSUE_AFTER_MS:-90000}"
HARD_STOP_REISSUE_COOLDOWN_MS="${HARD_STOP_REISSUE_COOLDOWN_MS:-90000}"
FORCE_SELL_BATCH_SIZE="${FORCE_SELL_BATCH_SIZE:-5}"
FORCE_SELL_BATCH_DELAY_MS="${FORCE_SELL_BATCH_DELAY_MS:-500}"
ENFORCE_MODE="${ENFORCE_MODE:-true}"

need_cmd git
need_cmd pm2
need_cmd node
need_cmd python3
need_cmd install
[[ -n "$REDIS_CONN" ]] && need_cmd redis-cli
[[ "$INSTALL_POST_TRADE_CRON" == "1" ]] && need_cmd crontab

[[ -d "$REPO_DIR" ]] || fail "repo dir not found: $REPO_DIR"
[[ -f "$PHS_NEW" ]] || fail "missing custom PHS file: $PHS_NEW"
[[ -f "$PHS_TARGET" ]] || warn "target PHS file not found yet: $PHS_TARGET"

ECOSYSTEM_CONFIG="$(resolve_ecosystem_config)" || fail "ecosystem config not found under $REPO_DIR"

if [[ "$REQUIRE_P3_FILES" == "1" ]]; then
  [[ -f "$POST_TRADE_ANALYZER" ]] || fail "missing post-trade analyzer: $POST_TRADE_ANALYZER"
  [[ -f "$OFG_V2_PATH" ]] || fail "missing OFG v2 file: $OFG_V2_PATH"
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
STATE_DIR="/tmp/ares_combined_deploy_${TIMESTAMP}"
LATEST_LINK="/tmp/ares_combined_latest"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/manifest.env"

cd "$REPO_DIR"

if [[ "$ALLOW_DIRTY" != "1" ]] && [[ -n "$(git status --porcelain)" ]]; then
  fail "git working tree is dirty; set ALLOW_DIRTY=1 only if intentional"
fi

PRE_GIT_COMMIT="$(git rev-parse HEAD)"
PRE_GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
PRE_GIT_TAG="pre_combined_runbook_${TIMESTAMP}"
PHS_BACKUP="$PHS_TARGET.bak.$TIMESTAMP"

cat > "$STATE_FILE" <<MANIFEST
TIMESTAMP='$TIMESTAMP'
REPO_DIR='$REPO_DIR'
GIT_REMOTE='$GIT_REMOTE'
GIT_BRANCH='$GIT_BRANCH'
PRE_GIT_COMMIT='$PRE_GIT_COMMIT'
PRE_GIT_BRANCH='$PRE_GIT_BRANCH'
PRE_GIT_TAG='$PRE_GIT_TAG'
ECOSYSTEM_CONFIG='$ECOSYSTEM_CONFIG'
PHS_TARGET='$PHS_TARGET'
PHS_NEW='$PHS_NEW'
PHS_BACKUP='$PHS_BACKUP'
PM2_PHS_APP='$PM2_PHS_APP'
PM2_OFG_APP='$PM2_OFG_APP'
PM2_OFG_APP_FALLBACKS='$PM2_OFG_APP_FALLBACKS'
POST_TRADE_ANALYZER='$POST_TRADE_ANALYZER'
OFG_V2_PATH='$OFG_V2_PATH'
POST_TRADE_SCHEDULE='$POST_TRADE_SCHEDULE'
POST_TRADE_LOG='$POST_TRADE_LOG'
CRON_MARKER='$CRON_MARKER'
POST_TRADE_CRON_ADDED='0'
REDIS_CONN='${REDIS_CONN}'
MANIFEST

log "state dir: $STATE_DIR"
log "repo dir: $REPO_DIR"
log "PHS target: $PHS_TARGET"
log "pre git commit: $PRE_GIT_COMMIT"

if [[ -n "$REDIS_CONN" ]]; then
  log "checking Redis connectivity"
  redis-cli -u "$REDIS_CONN" PING >/dev/null || fail "Redis ping failed"
  log "precheck gate=$(redis_get ares:final_trade_gate) halt=$(redis_get ares:halt:active) invariant=$(redis_get ares:invariant:halt)"
else
  warn "REDIS_CONN not set; Redis health checks will be skipped"
fi

log "creating backups"
git tag "$PRE_GIT_TAG" "$PRE_GIT_COMMIT"
if [[ -f "$PHS_TARGET" ]]; then
  sudo cp "$PHS_TARGET" "$PHS_BACKUP"
fi
crontab -l > "$STATE_DIR/pre.crontab" 2>/dev/null || true

log "Track B: deploy v2.1 bundle"
git fetch "$GIT_REMOTE" "$GIT_BRANCH"
git checkout "$GIT_BRANCH"
git pull --ff-only "$GIT_REMOTE" "$GIT_BRANCH"

if [[ -d "$REPO_DIR/ares_patches_v2" ]]; then
  while IFS= read -r -d '' file; do
    log "syntax check: $file"
    node --check "$file"
  done < <(find "$REPO_DIR/ares_patches_v2" -maxdepth 1 -type f \( -name '*.mjs' -o -name '*.js' \) -print0)
else
  warn "ares_patches_v2 directory not found after pull; skipping bundle syntax sweep"
fi

pm2 restart "$ECOSYSTEM_CONFIG"
pm2 save

if [[ -n "$REDIS_CONN" ]]; then
  wait_for_all_redis_keys "$TRACK_B_HEALTH_TIMEOUT_SEC" \
    ares:equity:health \
    ares:smg:health \
    ares:positions:promote_health \
    ares:emergency:health || fail "Track B health keys did not appear in time"
fi

log "Track A: deploy custom position-hard-stop v4.2"
sudo install -m 0644 -o ubuntu -g ubuntu "$PHS_NEW" "$PHS_TARGET"
node --check "$PHS_TARGET"

grep -nE '@version 4\.2\.0-patch-v3-ai-verified|PATCH-v3-AI-VERIFIED|HARD_STOP_CHECK_INTERVAL_MS|HARD_STOP_REISSUE_AFTER_MS|REISSUE_COOLDOWN_MS|FORCE_SELL_BATCH_SIZE|FORCE_SELL_BATCH_DELAY_MS|reconcile_latency_ms' "$PHS_TARGET" >/dev/null || \
  warn "expected v4.2 markers not fully found in $PHS_TARGET"

auto_runtime_warn=0
if grep -n 'Position Hard-Stop v4\.1 starting' "$PHS_TARGET" >/dev/null 2>&1; then
  warn "v4.2 file still logs runtime banner as v4.1; report confirms this is expected but cosmetic"
  auto_runtime_warn=1
fi

export HARD_STOP_CHECK_INTERVAL_MS
export HARD_STOP_REISSUE_AFTER_MS
export HARD_STOP_REISSUE_COOLDOWN_MS
export FORCE_SELL_BATCH_SIZE
export FORCE_SELL_BATCH_DELAY_MS
export ENFORCE_MODE

pm2 restart "$PM2_PHS_APP" --update-env
pm2 save

if [[ -n "$REDIS_CONN" ]]; then
  wait_for_all_redis_keys "$TRACK_A_HEALTH_TIMEOUT_SEC" \
    ares:guard:hard_stop:heartbeat \
    ares:guard:hard_stop:reconcile || fail "Track A hard-stop keys did not appear in time"
fi

log "Track C: deploy/verify P3 artifacts"
if [[ -f "$POST_TRADE_ANALYZER" ]]; then
  python3 -m py_compile "$POST_TRADE_ANALYZER"
fi
if [[ -f "$OFG_V2_PATH" ]]; then
  python3 -m py_compile "$OFG_V2_PATH"
fi

OFG_APP_TO_RESTART="$(pm2_first_available_app "$PM2_OFG_APP_FALLBACKS" || true)"
if [[ -n "$OFG_APP_TO_RESTART" ]]; then
  pm2 restart "$OFG_APP_TO_RESTART" --update-env
  pm2 save
else
  warn "PM2 OFG app not found in fallback set: $PM2_OFG_APP_FALLBACKS (override PM2_OFG_APP or PM2_OFG_APP_FALLBACKS if needed)"
fi

if [[ "$INSTALL_POST_TRADE_CRON" == "1" ]]; then
  mkdir -p "$(dirname "$POST_TRADE_LOG")"
  CRON_LINE="$POST_TRADE_SCHEDULE cd '$REPO_DIR' && /usr/bin/env python3 '$POST_TRADE_ANALYZER' >> '$POST_TRADE_LOG' 2>&1 $CRON_MARKER"
  if install_or_update_cron "$CRON_MARKER" "$CRON_LINE"; then
    sed -i "s/POST_TRADE_CRON_ADDED='0'/POST_TRADE_CRON_ADDED='1'/" "$STATE_FILE"
    log "post-trade cron installed/updated"
  else
    log "post-trade cron already up-to-date"
  fi
fi

ln -sfn "$STATE_DIR" "$LATEST_LINK"

log "deployment complete"
log "manifest: $STATE_FILE"
log "latest:   $LATEST_LINK"
log "next step: run verify_combined_ares_v42_p1base.sh"
