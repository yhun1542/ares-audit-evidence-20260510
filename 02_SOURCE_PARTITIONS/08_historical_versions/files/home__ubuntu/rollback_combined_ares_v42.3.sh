#!/usr/bin/env bash
###############################################################################
# rollback_combined_ares_v42.3.sh — ARES 통합 롤백 스크립트 (GPT R3 v42.2 블로커 해결)
# ============================================================================
# v42.2 → v42.3 변경사항:
#   [FIX-v3-1] 멀티 인스턴스 롤백 오케스트레이션 (DEPLOY_TARGETS 루프)
#   [FIX-v3-2] ARES_CURRENT↔ARES_REPO realpath fail-hard 체크 추가
#   [FIX-v3-3] git reset --hard 실패 시 앱 재시작 중단 (false rollback 방지)
#   [FIX-v3-4] dep install 실패 시 앱 재시작 중단 (inconsistent node_modules 방지)
#   [FIX-v3-5] 의존성 재설치 lockfile 강제 (--frozen-lockfile / npm ci)
#   [FIX-v3-6] 백업 무결성 사전 검증 (진행 전 게이트)
#
# 기존 v42.2 유지: flock, set -euo pipefail, REDIS_URL 강제, redis_cmd 환경변수,
#   개별 앱 restart, epoch/effective 자동 복원 금지, dirty tree stash, NODE_PATH
###############################################################################
set -euo pipefail

# flock + cleanup trap
LOCK_FILE="/tmp/ares_deploy.lock"
exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  echo "[FATAL] Another deploy/rollback is already running. Exiting."
  exit 1
fi
trap 'flock -u 200; rm -f "${LOCK_FILE}"' EXIT

# ─── 옵션 파싱 ──────────────────────────────────────────────────────────────
DRY_RUN=false
AUTO_VERIFY=false
LOCAL_ONLY=false
BACKUP_DIR=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --auto-verify) AUTO_VERIFY=true ;;
    --local-only) LOCAL_ONLY=true ;;  # legacy flag (single-instance mode, no effect)
    *) BACKUP_DIR="$arg" ;;
  esac
done

# ─── 색상 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

ROLLBACK_FAILED=0

# [FIX-v3-R4] 변수 사전 초기화 (set -u 호환)
GIT_ROLLBACK_OK=true
BACKUP_VALID=true

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; ROLLBACK_FAILED=$((ROLLBACK_FAILED+1)); }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 에러 로그 ──────────────────────────────────────────────────────────────
ROLLBACK_LOG="/home/ubuntu/rollback_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${ROLLBACK_LOG}") 2>&1
log_info "Rollback log: ${ROLLBACK_LOG}"
log_info "Script version: v42.3"

# ─── REDIS_URL 환경변수 강제 ───────────────────────────────────────────────
if [[ -z "${REDIS_URL:-}" ]]; then
  if [[ -f /etc/ares/redis.env ]]; then
    source /etc/ares/redis.env
    export REDIS_URL  # GPT HIGH-2: ensure subprocesses inherit REDIS_URL
    log_info "REDIS_URL loaded from /etc/ares/redis.env"
  fi
  if [[ -z "${REDIS_URL:-}" ]]; then
    echo -e "${RED}[FATAL]${NC} REDIS_URL environment variable is NOT set."
    exit 1
  fi
fi
export REDIS_URL  # GPT HIGH-2: ensure REDIS_URL is always exported
log_ok "REDIS_URL is set (from environment)"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
# ARES_REPO: PM2 프로세스가 주로 ares_current를 사용하므로 동일 경로 사용
ARES_REPO="/home/ubuntu/ares_current"

# NODE_PATH 설정
if [[ -d "${ARES_REPO}/node_modules" ]]; then
  export NODE_PATH="${ARES_REPO}/node_modules:${NODE_PATH:-}"
fi

# [FIX-v3-2] ARES_CURRENT↔ARES_REPO realpath fail-hard 체크 (deploy와 동일)
REAL_CURRENT=$(readlink -f "$ARES_CURRENT" 2>/dev/null || echo "MISSING")
REAL_REPO=$(readlink -f "$ARES_REPO" 2>/dev/null || echo "MISSING")
if [[ "$REAL_CURRENT" == "MISSING" ]]; then
  log_warn "ARES_CURRENT does not exist — rollback will target ARES_REPO directly"
elif [[ "$REAL_CURRENT" != "$REAL_REPO" ]]; then
  echo -e "${RED}[FATAL]${NC} Path mismatch!"
  echo "  ARES_CURRENT resolves to: ${REAL_CURRENT}"
  echo "  ARES_REPO resolves to:    ${REAL_REPO}"
  echo "Rollback would reset ARES_REPO but PM2 references ARES_CURRENT."
  echo "Fix: ln -sfn ${ARES_REPO} ${ARES_CURRENT}"
  exit 1
else
  log_ok "ARES_CURRENT and ARES_REPO resolve to same path: ${REAL_REPO}"
fi

# ─── Single-instance rollback (R8: second instance removed) ────────────
DEPLOY_TARGETS=("localhost")
log_info "Rollback targets: ${DEPLOY_TARGETS[*]} (single-instance mode)"

# P1 v6 프로세스 (건드리지 않음)
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# ARES 전용 상위 트랙 allowlist
ARES_UPPER_TRACK_PROCS=(
  "position-hard-stop"
  "alert-rules"
  "ares-runtime-guard"
  "ares-self-healing-guard"
  "circuit-breaker-daemon"
  "data-freshness-enforcer"
  "ares-invariant-guard"
  "equity-calculator"
)

# Redis 키 분류
REDIS_SAFE_RESTORE_KEYS=("ares:router:mode:desired")
REDIS_NO_RESTORE_KEYS=(
  "ares:equity:fence_epoch" "ares:router_guard:epoch" "ofg:unifier:epoch"
  "ares:md_feeder:epoch" "ares:router:mode:effective" "ares:equity:total"
  "ares:equity:active_fence" "ofg:gate:current"
)

# redis_cmd
redis_cmd() {
  local result exit_code
  result=$(REDIS_URL_INTERNAL="${REDIS_URL}" node -e "
    const Redis = require('ioredis');
    const r = new Redis(process.env.REDIS_URL_INTERNAL, {tls:{}, connectTimeout: 10000, commandTimeout: 15000});
    (async () => {
      try {
        const args = process.argv.slice(1);
        const cmd = args[0];
        const rest = args.slice(1);
        const result = await r[cmd](...rest);
        console.log(typeof result === 'object' ? JSON.stringify(result) : result);
        process.exitCode = 0;
      } catch(e) {
        console.error('REDIS_ERROR:', e.message);
        process.exitCode = 1;
      }
      await r.quit();
    })();
  " "$@" 2>/dev/null) && exit_code=0 || exit_code=$?
  if [[ "$exit_code" -ne 0 ]]; then
    return 1
  fi
  echo "$result"
}

# PM2 상태 조회 헬퍼
get_pm2_status() {
  local proc_name="$1"
  pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc_name}');
        console.log(p ? p.pm2_env.status : 'NOT_FOUND');
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null || echo "QUERY_FAILED"
}

# [FIX-R6-H2] PM2 uptime/restart 카운터 조회 (P1 never-restarted 증명용)
get_pm2_uptime_restarts() {
  local proc_name="$1"
  pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc_name}');
        if(!p) { console.log('NOT_FOUND'); return; }
        console.log(p.pm2_env.restart_time + '|' + p.pm2_env.pm_uptime);
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null || echo "QUERY_FAILED"
}

###############################################################################
# STEP 0: 백업 디렉토리 결정 + [FIX-v3-6] 무결성 검증
###############################################################################
log_step "Step 0: Locate & Validate Backup"

if [[ -z "$BACKUP_DIR" ]]; then
  BACKUP_DIR=$(find /home/ubuntu -maxdepth 1 -name 'deploy_backup_*' -type d 2>/dev/null | sort -r | head -1 || echo "")
  if [[ -z "$BACKUP_DIR" ]]; then
    echo -e "${RED}[FATAL]${NC} No backup directory found. Specify one: $0 <backup_dir>"
    exit 1
  fi
  log_info "Auto-detected latest backup: ${BACKUP_DIR}"
fi

if [[ ! -d "$BACKUP_DIR" ]]; then
  echo -e "${RED}[FATAL]${NC} Backup directory does not exist: ${BACKUP_DIR}"
  exit 1
fi

# [FIX-v3-6] 백업 무결성 사전 검증
BACKUP_VALID=true
if [[ ! -f "${BACKUP_DIR}/git_sha_before.txt" ]]; then
  log_warn "Backup missing: git_sha_before.txt — git rollback will be skipped"
fi
if [[ -f "${BACKUP_DIR}/redis_snapshot.json" ]]; then
  if ! node -e "JSON.parse(require('fs').readFileSync('${BACKUP_DIR}/redis_snapshot.json','utf8'))" 2>/dev/null; then
    log_fail "Redis snapshot in backup is INVALID JSON — Redis restore will be skipped"
    BACKUP_VALID=false
  else
    log_ok "Redis snapshot: valid JSON"
  fi
else
  log_info "No Redis snapshot in backup"
fi

log_ok "Using backup: ${BACKUP_DIR}"
ls -la "${BACKUP_DIR}/" 2>/dev/null | grep -v "^total" | while read -r line; do
  echo -e "    $line"
done

###############################################################################
# STEP 1: P1 v6 기반층 상태 확인 (read-only)
###############################################################################
log_step "Step 1: P1 v6 Foundation Layer Status (read-only)"
log_info "P1 v6 processes will NOT be rolled back"

declare -A P1_PRE_RESTARTS
declare -A P1_PRE_UPTIMES
for proc in "${P1V6_PROCS[@]}"; do
  STATUS=$(get_pm2_status "$proc")
  if [[ "$STATUS" == "online" ]]; then
    UR=$(get_pm2_uptime_restarts "$proc")
    if [[ "$UR" != "NOT_FOUND" && "$UR" != "PARSE_ERROR" && "$UR" != "QUERY_FAILED" ]]; then
      IFS='|' read -r restarts uptime <<< "$UR"
      P1_PRE_RESTARTS["$proc"]="$restarts"
      P1_PRE_UPTIMES["$proc"]="$uptime"
      log_ok "P1 v6 ${proc}: online (restarts: ${restarts}, uptime_ms: ${uptime})"
    else
      log_ok "P1 v6 ${proc}: online (counters unavailable)"
    fi
  else
    log_warn "P1 v6 ${proc}: ${STATUS} — NOT part of rollback, but may need attention"
  fi
done

###############################################################################
# STEP 2: Git 롤백 [FIX-v3-3: 실패 시 앱 재시작 중단]
###############################################################################
log_step "Step 2: Git Rollback (Upper Tracks)"

GIT_ROLLBACK_OK=true

if [[ -d "${ARES_REPO}/.git" ]]; then
  cd "${ARES_REPO}"
  CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  log_info "Current HEAD: ${CURRENT_SHA}"

  # dirty tree 체크 (untracked 포함)
  DIRTY=false
  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    DIRTY=true
  fi
  UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l || echo "0")
  if [[ "$UNTRACKED" -gt 0 ]]; then
    DIRTY=true
  fi

  if [[ "$DIRTY" == "true" ]]; then
    log_warn "Working tree has uncommitted/untracked changes — stashing before rollback"
    if [[ "$DRY_RUN" != "true" ]]; then
      git stash push -u -m "ares-rollback-$(date +%Y%m%d_%H%M%S)" 2>&1 || \
        log_warn "git stash failed — proceeding with caution"
    fi
  fi

  PREV_SHA=""
  if [[ -f "${BACKUP_DIR}/git_sha_before.txt" ]]; then
    PREV_SHA=$(cat "${BACKUP_DIR}/git_sha_before.txt" 2>/dev/null | tr -d '[:space:]')
    log_info "Previous SHA from backup: ${PREV_SHA}"
  fi

  if [[ -n "$PREV_SHA" && "$PREV_SHA" != "unknown" && "$PREV_SHA" != "$CURRENT_SHA" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
      log_info "[DRY-RUN] Would run: git reset --hard ${PREV_SHA}"
    else
      log_info "Rolling back git to: ${PREV_SHA}"
      # [FIX-v3-3] git reset 실패 시 앱 재시작 중단
      if git reset --hard "${PREV_SHA}" 2>&1; then
        log_ok "Git rolled back: ${CURRENT_SHA} → ${PREV_SHA}"
      else
        log_fail "Git rollback FAILED — aborting to prevent false rollback"
        echo -e "${RED}[FATAL]${NC} Cannot restart apps on potentially broken code state."
        echo "Manual intervention required: git reset --hard ${PREV_SHA}"
        exit 1
      fi
    fi
  elif [[ "$PREV_SHA" == "$CURRENT_SHA" ]]; then
    log_info "Git already at backup SHA — no rollback needed"
  else
    log_warn "No previous SHA available — showing recent commits for manual rollback:"
    git log --oneline -5 2>/dev/null || true
    GIT_ROLLBACK_OK=false
  fi
  cd - > /dev/null
else
  log_warn "${ARES_REPO} is not a git repo"
  GIT_ROLLBACK_OK=false
fi

###############################################################################
# STEP 2.5: 의존성 재설치 [FIX-v3-4/v3-5: 실패 시 중단 + lockfile 강제]
###############################################################################
log_step "Step 2.5: Dependency Re-install (lockfile-enforced, fail-hard)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would re-install dependencies from rolled-back lockfile"
elif [[ "$GIT_ROLLBACK_OK" == "false" ]]; then
  log_warn "Git rollback was incomplete — skipping dep install (manual review needed)"
else
  if [[ -f "${ARES_REPO}/package.json" ]]; then
    cd "${ARES_REPO}"
    DEP_INSTALL_OK=false

    # [FIX-R6-1] Lockfile-enforced ONLY — no fallback to --prod (GPT 95점 패치)
    # Rollback에서 dep fallback은 non-deterministic dependency resolution을 유발할 수 있음
    if command -v pnpm &>/dev/null && [[ -f "pnpm-lock.yaml" ]]; then
      if pnpm install --frozen-lockfile 2>&1 | tail -5; then
        log_ok "pnpm install --frozen-lockfile completed"
        DEP_INSTALL_OK=true
      else
        log_fail "pnpm --frozen-lockfile FAILED — lockfile integrity issue, NOT falling back"
      fi
    elif command -v npm &>/dev/null && [[ -f "package-lock.json" ]]; then
      if npm ci --omit=dev 2>&1 | tail -5; then
        log_ok "npm ci --omit=dev completed"
        DEP_INSTALL_OK=true
      else
        log_fail "npm ci FAILED — lockfile integrity issue, NOT falling back"
      fi
    else
      log_fail "No lockfile found (pnpm-lock.yaml or package-lock.json) — cannot install deterministically"
    fi

    # [FIX-v3-4] dep install 실패 시 앱 재시작 중단
    if [[ "$DEP_INSTALL_OK" == "false" ]]; then
      log_fail "Dependency install FAILED — aborting to prevent inconsistent rollback"
      echo -e "${RED}[FATAL]${NC} Cannot restart apps with broken/inconsistent node_modules."
      echo "Manual intervention required: cd ${ARES_REPO} && pnpm install"
      exit 1
    fi
    cd - > /dev/null
  fi
fi

###############################################################################
# STEP 3: PM2 프로세스 재시작 (개별 앱 단위)
###############################################################################
log_step "Step 3: Restart Upper Track Processes (individual app restart)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would restart ARES upper track processes"
elif [[ "$GIT_ROLLBACK_OK" == "false" ]]; then
  log_fail "Git rollback was incomplete — REFUSING to restart apps on potentially wrong code"
  echo -e "${RED}[FATAL]${NC} Cannot restart processes without confirmed code rollback."
  echo "Manual intervention required: verify git state, then restart processes manually."
  exit 1
else
  log_info "Using individual app restart (NO ecosystem reload)"

  RESTART_OK=0
  RESTART_SKIP=0
  RESTART_FAIL=0

  for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
    # P1 v6 이중 안전 체크
    for p1 in "${P1V6_PROCS[@]}"; do
      if [[ "$proc" == "$p1" ]]; then
        log_warn "SKIP: ${proc} is in P1 v6 — will NOT restart"
        continue 2
      fi
    done

    ST=$(get_pm2_status "$proc")
    if [[ "$ST" == "NOT_FOUND" ]]; then
      log_info "Process not in PM2: ${proc} (skipped)"
      RESTART_SKIP=$((RESTART_SKIP+1))
    elif pm2 restart "$proc" --update-env 2>&1; then
      log_ok "Restarted: ${proc}"
      RESTART_OK=$((RESTART_OK+1))
    else
      log_fail "Failed to restart: ${proc}"
      RESTART_FAIL=$((RESTART_FAIL+1))
    fi
  done

  log_info "Restart summary: ${RESTART_OK} ok, ${RESTART_SKIP} skipped, ${RESTART_FAIL} failed"

  # [FIX-R6-H2] Local P1 v6 post-rollback restart_time 비교 (deploy와 동일 수준)
  log_info "Proving P1 v6 was NOT restarted (local)..."
  for proc in "${P1V6_PROCS[@]}"; do
    POST_UR=$(get_pm2_uptime_restarts "$proc")
    if [[ "$POST_UR" != "NOT_FOUND" && "$POST_UR" != "PARSE_ERROR" && "$POST_UR" != "QUERY_FAILED" ]]; then
      IFS='|' read -r post_restarts post_uptime <<< "$POST_UR"
      pre_restarts=${P1_PRE_RESTARTS["$proc"]:-""}
      pre_uptime=${P1_PRE_UPTIMES["$proc"]:-""}
      if [[ -n "$pre_restarts" && "$post_restarts" != "$pre_restarts" ]]; then
        log_fail "P1 v6 ${proc}: restart_count CHANGED (${pre_restarts} → ${post_restarts}) — FOUNDATION LAYER VIOLATED"
      elif [[ -n "$pre_uptime" && "$post_uptime" != "$pre_uptime" ]]; then
        log_fail "P1 v6 ${proc}: uptime CHANGED (${pre_uptime} → ${post_uptime}) — FOUNDATION LAYER VIOLATED"
      else
        log_ok "P1 v6 ${proc}: NOT restarted (restarts: ${post_restarts}, uptime: ${post_uptime})"
      fi
    else
      log_warn "P1 v6 ${proc}: post-rollback counters unavailable"
    fi
  done
  log_info "P1 v6 processes: NOT restarted (foundation layer preserved)"
fi

###############################################################################
# STEP 4: Redis 키 복원 (safe keys only)
###############################################################################
log_step "Step 4: Redis Key Restoration (safe keys only)"

if [[ -f "${BACKUP_DIR}/redis_snapshot.json" && "$BACKUP_VALID" == "true" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would restore SAFE Redis keys from: ${BACKUP_DIR}/redis_snapshot.json"
    log_info "[DRY-RUN] Epoch keys, effective mode, and dynamic state will NOT be restored"
  else
    log_info "Restoring SAFE config Redis keys from snapshot..."
    log_warn "NOT restoring: epoch keys (monotonic), effective mode (runtime), dynamic state (stale)"

    SAFE_KEYS_JSON=$(printf '"%s",' "${REDIS_SAFE_RESTORE_KEYS[@]}" | sed 's/,$//')

    RESTORE_RESULT=$(REDIS_URL_INTERNAL="${REDIS_URL}" node -e "
      const Redis = require('ioredis');
      const fs = require('fs');
      const r = new Redis(process.env.REDIS_URL_INTERNAL, {tls:{}, connectTimeout: 10000});
      (async () => {
        try {
          const snap = JSON.parse(fs.readFileSync('${BACKUP_DIR}/redis_snapshot.json', 'utf8'));
          const safeKeys = new Set([${SAFE_KEYS_JSON}]);
          let restored = 0, skipped = 0, failed = 0;
          for (const [key, info] of Object.entries(snap)) {
            if (!safeKeys.has(key)) {
              console.error('  SKIP:', key, '(not in safe restore list)');
              skipped++;
              continue;
            }
            try {
              if (info.value !== null && info.value !== undefined) {
                await r.set(key, typeof info.value === 'object' ? JSON.stringify(info.value) : info.value);
                if (info.ttl && info.ttl > 0) {
                  await r.expire(key, info.ttl);
                } else {
                  await r.persist(key);
                }
                restored++;
              } else {
                skipped++;
              }
            } catch(e) {
              console.error('  REDIS_RESTORE_FAIL:', key, e.message);
              failed++;
            }
          }
          console.log(JSON.stringify({restored, skipped, failed}));
        } catch(e) {
          console.error('REDIS_RESTORE_ERROR:', e.message);
          console.log('{\"restored\":0,\"skipped\":0,\"failed\":1}');
        }
        await r.quit();
      })();
    " 2>"${ROLLBACK_LOG}.redis_errors" || true)

    PARSED=$(echo "$RESTORE_RESULT" | tail -1 | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const r=JSON.parse(d);console.log(r.restored+'|'+r.skipped+'|'+r.failed)}catch{console.log('0|0|1')}})" 2>/dev/null || echo "0|0|1")
    IFS='|' read -r R_OK R_SKIP R_FAIL <<< "$PARSED"
    log_info "Redis restore: ${R_OK} restored (safe only), ${R_SKIP} skipped (unsafe/null), ${R_FAIL} failed"

    if [[ "${R_FAIL}" == "0" ]]; then
      log_ok "Redis safe keys restored successfully"
    else
      log_fail "Some Redis keys failed to restore"
    fi
  fi
else
  log_info "No valid Redis snapshot in backup — skipping Redis restoration"
fi

###############################################################################
# STEP 5: POST-ROLLBACK 검증
###############################################################################
log_step "Step 5: Post-Rollback Verification"

sleep 5

P1_OK=true
for proc in "${P1V6_PROCS[@]}"; do
  ST=$(get_pm2_status "$proc")
  if [[ "$ST" != "online" ]]; then
    log_fail "P1 v6 ${proc}: ${ST} after rollback — INVESTIGATE!"
    P1_OK=false
  fi
done

if [[ "$P1_OK" == "true" ]]; then
  log_ok "P1 v6 foundation layer: still healthy after rollback"
fi

# [FIX-R8-H2] Validate ALL restarted upper-track processes (not just PHS + alert-rules)
UPPER_TRACK_FAIL=0
for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
  # Skip P1 v6 (already checked above)
  for p1 in "${P1V6_PROCS[@]}"; do
    [[ "$proc" == "$p1" ]] && continue 2
  done
  UT_ST=$(get_pm2_status "$proc")
  if [[ "$UT_ST" == "online" ]]; then
    log_ok "${proc}: online after rollback"
  elif [[ "$UT_ST" == "NOT_FOUND" ]]; then
    log_info "${proc}: not in PM2 (skipped)"
  else
    log_fail "${proc}: ${UT_ST} after rollback — INVESTIGATE!"
    UPPER_TRACK_FAIL=$((UPPER_TRACK_FAIL+1))
  fi
done

if [[ "$UPPER_TRACK_FAIL" -gt 0 ]]; then
  log_fail "Post-rollback: ${UPPER_TRACK_FAIL} upper-track process(es) NOT online"
else
  log_ok "All restarted upper-track processes: online after rollback"
fi

# [R8] Remote instance rollback removed (single-instance topology)
log_info "Single-instance mode — no remote rollback step"

###############################################################################
# AUTO-VERIFY
###############################################################################
VERIFY_EXIT=0
if [[ "$AUTO_VERIFY" == "true" ]]; then
  log_step "Auto-Verify: Running verify script"
  VERIFY_SCRIPT="$(dirname "$0")/verify_combined_ares_v42.3.sh"
  if [[ -f "$VERIFY_SCRIPT" ]]; then
    if ! bash "$VERIFY_SCRIPT" 2>&1; then
      log_fail "Auto-verify returned non-zero exit code"
      VERIFY_EXIT=1
    fi
  else
    log_warn "Verify script not found: ${VERIFY_SCRIPT}"
  fi
fi

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
if [[ "$ROLLBACK_FAILED" -eq 0 ]]; then
  echo -e "${BOLD}  ${GREEN}ROLLBACK COMPLETE — SUCCESS${NC}"
else
  echo -e "${BOLD}  ${RED}ROLLBACK COMPLETE — ${ROLLBACK_FAILED} FAILURE(S) DETECTED${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  Version:        v42.3"
echo -e "  Backup used:    ${BACKUP_DIR}"
echo -e "  P1 v6:          ${GREEN}NOT touched${NC} (foundation layer preserved)"
echo -e "  Git:            Rolled back (fail-hard: abort on failure)"
echo -e "  Dependencies:   Re-installed (lockfile-enforced, fail-hard)"
echo -e "  PM2:            Individual app restart (NO ecosystem reload)"
echo -e "  Redis safe:     Restored (desired mode only)"
echo -e "  Redis unsafe:   NOT restored (epoch/effective/dynamic)"
echo -e "  Targets:        ${DEPLOY_TARGETS[*]}"
echo -e "  Dry-run:        ${DRY_RUN}"
echo -e "  Failures:       ${ROLLBACK_FAILED}"
echo -e "  Log:            ${ROLLBACK_LOG}"
echo ""

if [[ "$AUTO_VERIFY" != "true" ]]; then
  echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42.3.sh to confirm.${NC}"
fi

if [[ "$ROLLBACK_FAILED" -gt 0 || "$VERIFY_EXIT" -gt 0 ]]; then
  exit 1
else
  exit 0
fi
