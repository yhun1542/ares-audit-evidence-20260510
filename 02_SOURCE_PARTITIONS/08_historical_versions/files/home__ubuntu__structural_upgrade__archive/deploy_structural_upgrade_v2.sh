#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ARES Structural Upgrade v3.0 — Deployment Script v2
# 
# CHANGES from v1 (per 4AI review):
#   - FULL-FILE REPLACEMENT only (NO regex/sed/python patching)
#   - Pre-built upgraded files are copied directly
#   - Canary validation between each stage
#   - Rolling restart with health checks
#   - Explicit post-deploy validation
#
# Files deployed:
#   1. lib/atomic-cas-lock.mjs          → replaces PATCH-001-position-state-cas.mjs
#   2. execution-reconciler-upgraded.mjs → replaces execution-reconciler.mjs
#   3. kill-switch-authority.mjs         → NEW service
#   4. lib/ortex-circuit-breaker.mjs     → NEW module
#   5. lib/risk-thresholds.mjs           → NEW module
#
# Execution: bash deploy_structural_upgrade_v2.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

STAMP=$(date +%Y%m%d_%H%M%S)
UPGRADE_DIR="/home/ubuntu/structural_upgrade"
AUB_DIR="/home/ubuntu/aub-trading-system"
BACKUP_DIR="/home/ubuntu/backups/structural_upgrade_${STAMP}"
LOG_FILE="/home/ubuntu/logs/structural_upgrade_${STAMP}.log"

# ── Logging ──────────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")" "$BACKUP_DIR"

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$msg" | tee -a "$LOG_FILE"
}

fail() {
  log "❌ FATAL: $*"
  log "Rolling back..."
  rollback
  exit 1
}

# ── Rollback function ────────────────────────────────────────────────────────
rollback() {
  log "=== ROLLBACK START ==="
  if [ -f "$BACKUP_DIR/execution-reconciler.mjs" ]; then
    cp "$BACKUP_DIR/execution-reconciler.mjs" "$AUB_DIR/execution-reconciler.mjs"
    log "Restored execution-reconciler.mjs"
  fi
  if [ -f "$BACKUP_DIR/PATCH-001-position-state-cas.mjs" ]; then
    cp "$BACKUP_DIR/PATCH-001-position-state-cas.mjs" "$AUB_DIR/lib/patches/PATCH-001-position-state-cas.mjs"
    log "Restored PATCH-001-position-state-cas.mjs"
  fi
  if [ -f "$BACKUP_DIR/ecosystem.master.cjs" ]; then
    cp "$BACKUP_DIR/ecosystem.master.cjs" /home/ubuntu/ecosystem.master.cjs
    log "Restored ecosystem.master.cjs"
  fi
  # Stop new kill-switch-authority if it was started
  pm2 delete kill-switch-authority 2>/dev/null || true
  # Restart affected services with old code
  pm2 restart execution-reconciler 2>/dev/null || true
  pm2 save
  log "=== ROLLBACK COMPLETE ==="
}

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 0: PRE-FLIGHT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 0: PRE-FLIGHT VALIDATION ═══"

# Verify all source files exist
for f in \
  "$UPGRADE_DIR/lib/atomic-cas-lock.mjs" \
  "$UPGRADE_DIR/execution-reconciler-upgraded.mjs" \
  "$UPGRADE_DIR/kill-switch-authority.mjs" \
  "$UPGRADE_DIR/lib/ortex-circuit-breaker.mjs" \
  "$UPGRADE_DIR/lib/risk-thresholds.mjs"; do
  [ -f "$f" ] || fail "Source file missing: $f"
  log "✓ Source exists: $f ($(wc -c < "$f") bytes)"
done

# Verify target directory exists
[ -d "$AUB_DIR" ] || fail "Target directory missing: $AUB_DIR"
[ -d "$AUB_DIR/lib" ] || fail "lib directory missing: $AUB_DIR/lib"

# Syntax validation of all JS files
log "--- Syntax validation ---"
for f in \
  "$UPGRADE_DIR/lib/atomic-cas-lock.mjs" \
  "$UPGRADE_DIR/execution-reconciler-upgraded.mjs" \
  "$UPGRADE_DIR/kill-switch-authority.mjs" \
  "$UPGRADE_DIR/lib/ortex-circuit-breaker.mjs" \
  "$UPGRADE_DIR/lib/risk-thresholds.mjs"; do
  node --check "$f" 2>&1 || fail "Syntax error in: $f"
  log "✓ Syntax OK: $(basename "$f")"
done

# PM2 pre-flight snapshot
log "--- PM2 snapshot ---"
pm2 jlist > "$BACKUP_DIR/pm2_snapshot_pre.json"
ONLINE_PRE=$(pm2 jlist | python3 -c "import sys,json; print(sum(1 for p in json.load(sys.stdin) if p.get('pm2_env',{}).get('status')=='online'))")
log "PM2 online count (pre): $ONLINE_PRE"

log "✅ PHASE 0 COMPLETE — All pre-flight checks passed"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: BACKUP
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 1: BACKUP ═══"

cp "$AUB_DIR/execution-reconciler.mjs" "$BACKUP_DIR/execution-reconciler.mjs"
cp "$AUB_DIR/lib/patches/PATCH-001-position-state-cas.mjs" "$BACKUP_DIR/PATCH-001-position-state-cas.mjs" 2>/dev/null || true
cp /home/ubuntu/ecosystem.master.cjs "$BACKUP_DIR/ecosystem.master.cjs"

# Backup lib directory
mkdir -p "$BACKUP_DIR/lib"
cp "$AUB_DIR/lib/"*.mjs "$BACKUP_DIR/lib/" 2>/dev/null || true

log "Backup saved to: $BACKUP_DIR"
log "✅ PHASE 1 COMPLETE"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: DEPLOY NEW MODULES (non-breaking — new files only)
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 2: DEPLOY NEW MODULES ═══"

# 2a. Deploy atomic-cas-lock.mjs to lib/
cp "$UPGRADE_DIR/lib/atomic-cas-lock.mjs" "$AUB_DIR/lib/atomic-cas-lock.mjs"
log "✓ Deployed: lib/atomic-cas-lock.mjs"

# 2b. Deploy ortex-circuit-breaker.mjs to lib/
cp "$UPGRADE_DIR/lib/ortex-circuit-breaker.mjs" "$AUB_DIR/lib/ortex-circuit-breaker.mjs"
log "✓ Deployed: lib/ortex-circuit-breaker.mjs"

# 2c. Deploy risk-thresholds.mjs to lib/
cp "$UPGRADE_DIR/lib/risk-thresholds.mjs" "$AUB_DIR/lib/risk-thresholds.mjs"
log "✓ Deployed: lib/risk-thresholds.mjs"

# 2d. Deploy kill-switch-authority.mjs (standalone service)
cp "$UPGRADE_DIR/kill-switch-authority.mjs" "$AUB_DIR/kill-switch-authority.mjs"
log "✓ Deployed: kill-switch-authority.mjs"

# Verify all deployed files with syntax check
for f in \
  "$AUB_DIR/lib/atomic-cas-lock.mjs" \
  "$AUB_DIR/lib/ortex-circuit-breaker.mjs" \
  "$AUB_DIR/lib/risk-thresholds.mjs" \
  "$AUB_DIR/kill-switch-authority.mjs"; do
  node --check "$f" 2>&1 || fail "Post-deploy syntax error: $f"
done

log "✅ PHASE 2 COMPLETE — New modules deployed"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: DEPLOY UPGRADED EXECUTION-RECONCILER (breaking change)
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 3: DEPLOY UPGRADED EXECUTION-RECONCILER ═══"

# Full-file replacement — NO regex patching
cp "$UPGRADE_DIR/execution-reconciler-upgraded.mjs" "$AUB_DIR/execution-reconciler.mjs"
log "✓ Replaced: execution-reconciler.mjs (full-file replacement)"

# Syntax validation of the deployed file
node --check "$AUB_DIR/execution-reconciler.mjs" 2>&1 || fail "Syntax error in deployed execution-reconciler.mjs"
log "✓ Syntax OK: execution-reconciler.mjs (deployed)"

log "✅ PHASE 3 COMPLETE"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: START KILL-SWITCH-AUTHORITY (NEW SERVICE)
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 4: START KILL-SWITCH-AUTHORITY ═══"

# Start as PM2 process (fail-safe: starts with trading:enabled=false)
pm2 start "$AUB_DIR/kill-switch-authority.mjs" \
  --name kill-switch-authority \
  --interpreter node \
  --node-args="--experimental-modules" \
  --max-restarts 10 \
  --restart-delay 3000 \
  2>&1 | tee -a "$LOG_FILE"

sleep 5

# Canary check: kill-switch-authority must be online
KSA_STATUS=$(pm2 jlist | python3 -c "
import sys, json
for p in json.load(sys.stdin):
    if p['name'] == 'kill-switch-authority':
        print(p.get('pm2_env',{}).get('status','unknown'))
        break
else:
    print('not_found')
")

if [ "$KSA_STATUS" != "online" ]; then
  fail "kill-switch-authority failed to start (status: $KSA_STATUS)"
fi
log "✓ kill-switch-authority is ONLINE"

# Verify it set trading:enabled=false (fail-safe default)
source /home/ubuntu/.env 2>/dev/null
REDIS_PW="${REDIS_PASSWORD:-}"
TRADING_ENABLED=$(redis-cli -a "$REDIS_PW" --no-auth-warning GET trading:enabled 2>/dev/null || echo "unknown")
log "trading:enabled = $TRADING_ENABLED (expected: false at startup)"

log "✅ PHASE 4 COMPLETE"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: INITIALIZE RISK THRESHOLDS IN REDIS
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 5: INITIALIZE RISK THRESHOLDS ═══"

redis-cli -a "$REDIS_PW" --no-auth-warning <<'REDIS_CMDS'
HSETNX risk:thresholds low 0.30
HSETNX risk:thresholds medium 0.50
HSETNX risk:thresholds high 0.75
HSETNX risk:thresholds critical 0.90
HSETNX risk:thresholds max_acceptable 0.85
HSETNX risk:thresholds version 1
HSETNX risk:thresholds lastUpdated 0
HSETNX risk:thresholds updatedBy risk-thresholds-init
REDIS_CMDS

log "✓ Risk thresholds seeded in Redis (HSETNX — won't overwrite existing)"
redis-cli -a "$REDIS_PW" --no-auth-warning HGETALL risk:thresholds 2>&1 | tee -a "$LOG_FILE"

log "✅ PHASE 5 COMPLETE"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: STAGED PM2 RESTART WITH HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 6: STAGED PM2 RESTART ═══"

# Stage 1: Restart execution-reconciler (primary CAS consumer)
log "--- Stage 1: execution-reconciler ---"
pm2 restart execution-reconciler --update-env 2>&1 | tee -a "$LOG_FILE"
sleep 8

# Health check: execution-reconciler must be online
ER_STATUS=$(pm2 jlist | python3 -c "
import sys, json
for p in json.load(sys.stdin):
    if p['name'] == 'execution-reconciler':
        env = p.get('pm2_env', {})
        print(f\"{env.get('status','?')} restarts={env.get('restart_time',0)}\")
        break
else:
    print('not_found')
")
log "execution-reconciler: $ER_STATUS"

if [[ "$ER_STATUS" != online* ]]; then
  fail "execution-reconciler failed after restart"
fi

# Verify CAS initialization in logs
sleep 3
CAS_INIT=$(pm2 logs execution-reconciler --lines 20 --nostream 2>&1 | grep -c "CAS_LOCK_INITIALIZED" || echo "0")
log "CAS_LOCK_INITIALIZED log count: $CAS_INIT"

if [ "$CAS_INIT" -eq 0 ]; then
  log "⚠️ WARNING: CAS_LOCK_INITIALIZED not found in recent logs (may need more time)"
fi

# Stage 2: Restart order-intent-executor (CAS consumer)
log "--- Stage 2: order-intent-executor ---"
pm2 restart order-intent-executor --update-env 2>&1 | tee -a "$LOG_FILE"
sleep 5

OIE_STATUS=$(pm2 jlist | python3 -c "
import sys, json
for p in json.load(sys.stdin):
    if p['name'] == 'order-intent-executor':
        print(p.get('pm2_env',{}).get('status','?'))
        break
else:
    print('not_found')
")
log "order-intent-executor: $OIE_STATUS"

log "✅ PHASE 6 COMPLETE"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: POST-DEPLOY VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
log "═══ PHASE 7: POST-DEPLOY VALIDATION ═══"

# 7a. PM2 status check
ONLINE_POST=$(pm2 jlist | python3 -c "import sys,json; print(sum(1 for p in json.load(sys.stdin) if p.get('pm2_env',{}).get('status')=='online'))")
ERRORED_POST=$(pm2 jlist | python3 -c "import sys,json; print(sum(1 for p in json.load(sys.stdin) if p.get('pm2_env',{}).get('status')=='errored'))")
log "PM2 online: $ONLINE_POST (was: $ONLINE_PRE), errored: $ERRORED_POST"

if [ "$ERRORED_POST" -gt 0 ]; then
  log "⚠️ WARNING: $ERRORED_POST errored processes detected"
fi

# 7b. File hash verification
log "--- File hash verification ---"
for f in \
  "$AUB_DIR/lib/atomic-cas-lock.mjs" \
  "$AUB_DIR/execution-reconciler.mjs" \
  "$AUB_DIR/kill-switch-authority.mjs" \
  "$AUB_DIR/lib/ortex-circuit-breaker.mjs" \
  "$AUB_DIR/lib/risk-thresholds.mjs"; do
  HASH=$(sha256sum "$f" | cut -d' ' -f1)
  log "  $(basename "$f"): $HASH"
done

# 7c. Redis key verification
log "--- Redis key verification ---"
redis-cli -a "$REDIS_PW" --no-auth-warning GET trading:enabled 2>&1 | tee -a "$LOG_FILE"
redis-cli -a "$REDIS_PW" --no-auth-warning HGETALL risk:thresholds 2>&1 | tee -a "$LOG_FILE"
redis-cli -a "$REDIS_PW" --no-auth-warning GET kill_switch 2>&1 | tee -a "$LOG_FILE"
redis-cli -a "$REDIS_PW" --no-auth-warning GET ares:ops:env_mode 2>&1 | tee -a "$LOG_FILE"

# 7d. Save PM2 state
pm2 save 2>&1 | tee -a "$LOG_FILE"

log "✅ PHASE 7 COMPLETE"

# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
log ""
log "═══════════════════════════════════════════════════════════════════"
log "  STRUCTURAL UPGRADE v3.0 DEPLOYMENT COMPLETE"
log "  Timestamp: $STAMP"
log "  Backup: $BACKUP_DIR"
log "  Log: $LOG_FILE"
log "  PM2 online: $ONLINE_POST (was: $ONLINE_PRE)"
log "  Errored: $ERRORED_POST"
log "═══════════════════════════════════════════════════════════════════"
log ""
log "NEXT STEPS:"
log "  1. Monitor CAS conflict rate: pm2 logs execution-reconciler | grep CAS"
log "  2. Monitor kill-switch: pm2 logs kill-switch-authority"
log "  3. Verify risk thresholds: redis-cli HGETALL risk:thresholds"
log "  4. Before market open: Set trading:enabled=true via kill-switch-authority"
