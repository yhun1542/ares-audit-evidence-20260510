#!/usr/bin/env bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════
# ARES Structural Upgrade Deployment Script
# Deploys: atomic-cas-lock, kill-switch-authority, ortex-circuit-breaker,
#          risk-thresholds, patches consolidation
# ═══════════════════════════════════════════════════════════════════════════

STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/home/ubuntu/backups/structural_upgrade_${STAMP}"
AUB="/home/ubuntu/aub-trading-system"
LIB="${AUB}/lib"
PATCHES="${LIB}/patches"
LOG="/home/ubuntu/structural_upgrade_deploy_${STAMP}.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ── STEP 0: Pre-flight checks ───────────────────────────────────────────
log "=== STEP 0: Pre-flight checks ==="
log "PM2 status before deployment:"
pm2 jlist 2>/dev/null | python3 -c "
import sys, json
procs = json.load(sys.stdin)
online = sum(1 for p in procs if p.get('pm2_env',{}).get('status') == 'online')
errored = sum(1 for p in procs if p.get('pm2_env',{}).get('status') == 'errored')
print(f'  online={online} errored={errored} total={len(procs)}')
" | tee -a "$LOG"

# ── STEP 1: Backup ──────────────────────────────────────────────────────
log "=== STEP 1: Backup current files ==="
mkdir -p "$BACKUP_DIR"

# Backup CAS module
cp -p "${PATCHES}/PATCH-001-position-state-cas.mjs" "$BACKUP_DIR/" 2>/dev/null || true

# Backup execution-reconciler (CAS wrapper section)
cp -p "${AUB}/execution-reconciler.mjs" "$BACKUP_DIR/"

# Backup ecosystem
cp -p /home/ubuntu/ecosystem.master.cjs "$BACKUP_DIR/"

# Backup auto-killswitch if exists
cp -p "${AUB}/auto-killswitch-system.mjs" "$BACKUP_DIR/" 2>/dev/null || true

log "Backup saved to: $BACKUP_DIR"
ls -la "$BACKUP_DIR/" | tee -a "$LOG"

# ── STEP 2: Deploy atomic-cas-lock.mjs ──────────────────────────────────
log "=== STEP 2: Deploy atomic-cas-lock.mjs ==="
cp /home/ubuntu/structural_upgrade/lib/atomic-cas-lock.mjs "${LIB}/atomic-cas-lock.mjs"
chmod 644 "${LIB}/atomic-cas-lock.mjs"

# Syntax check
node --check "${LIB}/atomic-cas-lock.mjs" && log "  ✅ atomic-cas-lock.mjs syntax OK" || {
  log "  ❌ SYNTAX ERROR — rolling back"
  cp "$BACKUP_DIR/PATCH-001-position-state-cas.mjs" "${PATCHES}/" 2>/dev/null || true
  exit 1
}

# ── STEP 3: Update execution-reconciler.mjs — replace CAS wrapper ──────
log "=== STEP 3: Update execution-reconciler.mjs CAS integration ==="

# Replace the import line (line 1)
python3 << 'PYEOF'
import re

filepath = "/home/ubuntu/aub-trading-system/execution-reconciler.mjs"
with open(filepath, 'r') as f:
    content = f.read()

# 1. Replace the old PATCH-001 import with new atomic-cas-lock import
old_import = r'import \{ updateTargetPositions as casUpdatePositions.*?\} from ["\'].*?PATCH-001.*?["\'];?'
new_import = 'import { initCASLock, updateTargetPositions as atomicCASUpdate, updateTargetPositionsBulk as atomicCASBulk, getCASHealth } from "./lib/atomic-cas-lock.mjs";'
content = re.sub(old_import, new_import, content, count=1)

# 2. Replace the dual-path updateTargetPositions function with single-path
old_func_pattern = r'async function updateTargetPositions\(symbol, updater\) \{[\s\S]*?// \[PATCH-001\] CAS lock via Lua script.*?[\s\S]*?return \{ ok: true, next: nextPositions\[symbol\] \|\| \{\} \};\s*\}\s*\}'
new_func = '''async function updateTargetPositions(symbol, updater) {
  // [STRUCTURAL-UPGRADE] Single-path atomic CAS — NO WATCH-MULTI fallback
  // Uses Lua script with version fencing for true atomicity
  return atomicCASUpdate(symbol, updater);
}'''
content = re.sub(old_func_pattern, new_func, content, count=1)

# 3. Add CAS initialization in the startup section (after Redis client creation)
# Look for the Redis client creation pattern and add initCASLock after it
if 'initCASLock' not in content:
    # Find where redis client is created/connected and add init after
    redis_init_pattern = r'(const redis = new Redis\([^)]*\);)'
    if re.search(redis_init_pattern, content):
        content = re.sub(
            redis_init_pattern,
            r'\1\n// [STRUCTURAL-UPGRADE] Initialize atomic CAS lock\nawait initCASLock(redis, { callerId: "execution-reconciler:" + process.pid });',
            content,
            count=1
        )

with open(filepath, 'w') as f:
    f.write(content)

print("execution-reconciler.mjs updated successfully")
PYEOF

# Syntax check
node --check "${AUB}/execution-reconciler.mjs" && log "  ✅ execution-reconciler.mjs syntax OK" || {
  log "  ❌ SYNTAX ERROR — rolling back"
  cp "$BACKUP_DIR/execution-reconciler.mjs" "${AUB}/"
  exit 1
}

# ── STEP 4: Deploy kill-switch-authority.mjs ─────────────────────────────
log "=== STEP 4: Deploy kill-switch-authority.mjs ==="
cp /home/ubuntu/structural_upgrade/kill-switch-authority.mjs "${AUB}/kill-switch-authority.mjs"
chmod 644 "${AUB}/kill-switch-authority.mjs"

node --check "${AUB}/kill-switch-authority.mjs" && log "  ✅ kill-switch-authority.mjs syntax OK" || {
  log "  ❌ SYNTAX ERROR"
  exit 1
}

# ── STEP 5: Deploy ortex-circuit-breaker.mjs ─────────────────────────────
log "=== STEP 5: Deploy ortex-circuit-breaker.mjs ==="
cp /home/ubuntu/structural_upgrade/lib/ortex-circuit-breaker.mjs "${LIB}/ortex-circuit-breaker.mjs"
chmod 644 "${LIB}/ortex-circuit-breaker.mjs"

node --check "${LIB}/ortex-circuit-breaker.mjs" && log "  ✅ ortex-circuit-breaker.mjs syntax OK" || {
  log "  ❌ SYNTAX ERROR"
  exit 1
}

# ── STEP 6: Deploy risk-thresholds.mjs ───────────────────────────────────
log "=== STEP 6: Deploy risk-thresholds.mjs ==="
cp /home/ubuntu/structural_upgrade/lib/risk-thresholds.mjs "${LIB}/risk-thresholds.mjs"
chmod 644 "${LIB}/risk-thresholds.mjs"

node --check "${LIB}/risk-thresholds.mjs" && log "  ✅ risk-thresholds.mjs syntax OK" || {
  log "  ❌ SYNTAX ERROR"
  exit 1
}

# ── STEP 7: Initialize Redis risk:thresholds HASH ────────────────────────
log "=== STEP 7: Initialize Redis risk:thresholds ==="
source /home/ubuntu/.env 2>/dev/null || true
REDIS_PW="${REDIS_PASSWORD:-}"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds low "0.30"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds medium "0.50"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds high "0.75"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds critical "0.90"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds max_acceptable "0.85"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds version "1"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds lastUpdated "$(date +%s)000"
redis-cli -a "$REDIS_PW" --no-auth-warning HSETNX risk:thresholds updatedBy "structural-upgrade-init"
log "  ✅ risk:thresholds HASH initialized"
redis-cli -a "$REDIS_PW" --no-auth-warning HGETALL risk:thresholds | tee -a "$LOG"

# ── STEP 8: Add kill-switch-authority to ecosystem.master.cjs ────────────
log "=== STEP 8: Update ecosystem.master.cjs ==="
python3 << 'PYEOF'
import re

filepath = "/home/ubuntu/ecosystem.master.cjs"
with open(filepath, 'r') as f:
    content = f.read()

# Check if kill-switch-authority already exists
if 'kill-switch-authority' in content:
    print("kill-switch-authority already in ecosystem.master.cjs — skipping")
else:
    # Find the last app entry and add kill-switch-authority before the closing bracket
    ksa_entry = '''
    {
      name:               'kill-switch-authority',
      script:             '/home/ubuntu/aub-trading-system/kill-switch-authority.mjs',
      interpreter:        'node',
      instances:          1,
      exec_mode:          'fork',
      autorestart:        true,
      max_restarts:       100,
      min_uptime:         '5s',
      restart_delay:      1000,
      watch:              false,
      env: {
        ...COMMON,
        NODE_ENV:         'production',
      },
    },'''
    
    # Insert before the last closing bracket of the apps array
    # Find the pattern of the last app entry's closing brace followed by ]
    content = content.replace(
        '  ],  // end apps',
        ksa_entry + '\n  ],  // end apps'
    )
    # If that pattern doesn't exist, try a more generic approach
    if 'kill-switch-authority' not in content:
        # Find last }, before ] in the apps array
        last_app_end = content.rfind('},')
        if last_app_end > 0:
            insert_pos = last_app_end + 2
            content = content[:insert_pos] + ksa_entry + content[insert_pos:]
    
    with open(filepath, 'w') as f:
        f.write(content)
    print("kill-switch-authority added to ecosystem.master.cjs")
PYEOF

# Validate ecosystem syntax
node -e "require('/home/ubuntu/ecosystem.master.cjs')" && log "  ✅ ecosystem.master.cjs syntax OK" || {
  log "  ❌ SYNTAX ERROR — rolling back"
  cp "$BACKUP_DIR/ecosystem.master.cjs" /home/ubuntu/
  exit 1
}

# ── STEP 9: PM2 Restart ─────────────────────────────────────────────────
log "=== STEP 9: PM2 Restart ==="

# Start kill-switch-authority (new service)
pm2 start /home/ubuntu/ecosystem.master.cjs --only kill-switch-authority --update-env 2>/dev/null || \
  pm2 start "${AUB}/kill-switch-authority.mjs" --name kill-switch-authority --update-env

sleep 3
log "kill-switch-authority status:"
pm2 show kill-switch-authority 2>/dev/null | grep -E 'status|uptime|restarts' | tee -a "$LOG"

# Restart execution-reconciler (CAS upgrade)
pm2 restart execution-reconciler --update-env
sleep 3
log "execution-reconciler status:"
pm2 show execution-reconciler 2>/dev/null | grep -E 'status|uptime|restarts' | tee -a "$LOG"

# Save PM2 state
pm2 save

log "=== Deployment complete ==="
log "Backup: $BACKUP_DIR"
log "Log: $LOG"
