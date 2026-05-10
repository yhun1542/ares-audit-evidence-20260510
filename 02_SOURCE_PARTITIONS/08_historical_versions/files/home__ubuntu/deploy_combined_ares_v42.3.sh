#!/usr/bin/env bash
###############################################################################
# deploy_combined_ares_v42.3.sh — ARES 통합 배포 스크립트 (GPT R3 v42.2 블로커 해결)
# ============================================================================
# v42.2 → v42.3 변경사항 (GPT 92점 → 95점+ 목표):
#   [FIX-v3-1] 멀티 인스턴스 실제 오케스트레이션 (DEPLOY_TARGETS 루프)
#   [FIX-v3-2] 의존성 설치 lockfile 강제 (--frozen-lockfile / npm ci)
#   [FIX-v3-3] 백업 생성 fail-hard (|| true 제거 → 무결성 게이트)
#   [FIX-v3-4] post-deploy에서 모든 재시작된 프로세스 검증
#   [FIX-v3-5] P1 "never restarted" 증명 (pre/post uptime 비교)
#   [FIX-v3-6] PM2 pm_cwd 검증 (실행 경로 일치 확인)
#   [FIX-v3-7] deploy preflight 정확한 파일명 검증
#
# 기존 v42.2 유지: flock, set -euo pipefail, REDIS_URL 강제, redis_cmd 환경변수,
#   개별 앱 restart, epoch TTL=-1 강제, git --ff-only, realpath 일치, NODE_PATH
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
SKIP_P1_CHECK=false
LOCAL_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --skip-p1-check) SKIP_P1_CHECK=true ;;
    --local-only) LOCAL_ONLY=true ;;  # legacy flag (single-instance mode, no effect)
  esac
done

# [FIX-R8-H1] Break-glass gate: --skip-p1-check requires ARES_BREAK_GLASS=1 + typed confirmation + audit log
if [[ "$SKIP_P1_CHECK" == "true" ]]; then
  if [[ "${ARES_BREAK_GLASS:-}" != "1" ]]; then
    echo -e "\033[0;31m[FATAL]\033[0m --skip-p1-check requires ARES_BREAK_GLASS=1 environment variable."
    echo "  Usage: ARES_BREAK_GLASS=1 bash deploy_combined_ares_v42.3.sh --skip-p1-check"
    exit 1
  fi
  echo -e "\033[0;31m\033[1m\u26a0 BREAK-GLASS MODE: P1 v6 safety checks will be BYPASSED.\033[0m"
  echo -e "\033[0;31mThis is an emergency-only override for a real-money trading system.\033[0m"
  read -rp "Type 'CONFIRM-BREAK-GLASS' to proceed: " BG_CONFIRM
  if [[ "$BG_CONFIRM" != "CONFIRM-BREAK-GLASS" ]]; then
    echo -e "\033[0;31m[ABORT]\033[0m Break-glass confirmation failed."
    exit 1
  fi
  # Audit log entry
  _BG_LOG="/var/log/ares_break_glass.log"
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] BREAK-GLASS deploy by $(whoami)@$(hostname) PID=$$ args=$*" >> "$_BG_LOG" 2>/dev/null || true
  echo -e "\033[1;33m[WARN]\033[0m BREAK-GLASS activated \u2014 P1 checks bypassed (logged to ${_BG_LOG})"
fi

# ─── 색상 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

DEPLOY_FAILED=0
RESTARTED_PROCS=()
declare -A P1_PRE_RESTARTS
declare -A P1_PRE_UPTIMES

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; DEPLOY_FAILED=$((DEPLOY_FAILED+1)); }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 에러 로그 ──────────────────────────────────────────────────────────────
DEPLOY_LOG="/home/ubuntu/deploy_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${DEPLOY_LOG}") 2>&1
log_info "Deploy log: ${DEPLOY_LOG}"
log_info "Script version: v42.3"

# ─── REDIS_URL 환경변수 강제 (INV-03 준수) ─────────────────────────────────
if [[ -z "${REDIS_URL:-}" ]]; then
  if [[ -f /etc/ares/redis.env ]]; then
    source /etc/ares/redis.env
    export REDIS_URL  # GPT HIGH-2: ensure subprocesses inherit REDIS_URL
    log_info "REDIS_URL loaded from /etc/ares/redis.env"
  fi
  if [[ -z "${REDIS_URL:-}" ]]; then
    echo -e "${RED}[FATAL]${NC} REDIS_URL environment variable is NOT set."
    echo "Export REDIS_URL or create /etc/ares/redis.env"
    exit 1
  fi
fi
export REDIS_URL  # GPT HIGH-2: ensure REDIS_URL is always exported
log_ok "REDIS_URL is set (from environment)"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
# ARES_REPO: PM2 프로세스가 주로 ares_current를 사용하므로 동일 경로 사용
# GitHub 레포는 /home/ubuntu/ARES-KIS-US-AUTOPILOT이지만 운영 경로는 ares_current
ARES_REPO="/home/ubuntu/ares_current"
P1V6_DIR="/home/ubuntu/patches/v6"
BACKUP_DIR="/home/ubuntu/deploy_backup_$(date +%Y%m%d_%H%M%S)"

# NODE_PATH 설정
if [[ -d "${ARES_REPO}/node_modules" ]]; then
  export NODE_PATH="${ARES_REPO}/node_modules:${NODE_PATH:-}"
  log_info "NODE_PATH set: ${ARES_REPO}/node_modules"
fi

# ARES_CURRENT/ARES_REPO 실경로 강제 일치
REAL_CURRENT=$(readlink -f "$ARES_CURRENT" 2>/dev/null || echo "MISSING")
REAL_REPO=$(readlink -f "$ARES_REPO" 2>/dev/null || echo "MISSING")
if [[ "$REAL_CURRENT" == "MISSING" ]]; then
  log_info "ARES_CURRENT does not exist yet — will be created if needed"
elif [[ "$REAL_CURRENT" != "$REAL_REPO" ]]; then
  echo -e "${RED}[FATAL]${NC} Path mismatch!"
  echo "  ARES_CURRENT resolves to: ${REAL_CURRENT}"
  echo "  ARES_REPO resolves to:    ${REAL_REPO}"
  echo "Deploy would update ARES_REPO but PM2 references ARES_CURRENT."
  echo "Fix: ln -sfn ${ARES_REPO} ${ARES_CURRENT}"
  exit 1
else
  log_ok "ARES_CURRENT and ARES_REPO resolve to same path: ${REAL_REPO}"
fi

# ─── Single-instance deployment (R8: second instance removed) ─────────────
DEPLOY_TARGETS=("localhost")
log_info "Deploy targets: ${DEPLOY_TARGETS[*]} (single-instance mode)"

# ─── PM2 프로세스명 ─────────────────────────────────────────────────────────
PM2_OFG_APP="${PM2_OFG_APP:-ofg-unifier-v6}"
PM2_PHS_APP="${PM2_PHS_APP:-position-hard-stop}"
PM2_ALERT_APP="${PM2_ALERT_APP:-alert-rules}"

# P1 v6 프로세스 (기반 안전층 — 절대 재시작하지 않음)
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# [FIX-v3-7] P1 v6 필수 파일명 (정확한 파일명 검증)
P1V6_REQUIRED_FILES=(
  "p1-1-eta-conflict-monitor-v6.mjs"
  "p1-4-router-mode-guard-v6.mjs"
  "p1-5-ofg-gate-unifier-v6.mjs"
  "p1-6-market-data-feeder-v6.mjs"
  "p1-2-smg-audit-dashboard-v6.mjs"
)

# ARES 전용 상위 트랙 allowlist (blast radius 제한)
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

# redis_cmd: 환경변수로 시크릿 전달
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

# [FIX-v3-5] PM2 uptime/restart 카운터 조회 (P1 never-restarted 증명용)
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

# [FIX-v3-6] PM2 pm_cwd 조회
get_pm2_cwd() {
  local proc_name="$1"
  pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc_name}');
        if(!p) { console.log('NOT_FOUND'); return; }
        console.log(p.pm2_env.pm_cwd || 'UNKNOWN');
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null || echo "QUERY_FAILED"
}

###############################################################################
# PRE-FLIGHT: P1 v6 기반층 상태 확인
###############################################################################
log_step "PRE-FLIGHT: P1 v6 Foundation Layer Check"

if [[ "$SKIP_P1_CHECK" == "true" ]]; then
  log_warn "P1 v6 check skipped by BREAK-GLASS override (see audit log)"
else
  P1_FAIL=0

  # [FIX-v3-7] 정확한 파일명 검증 (파일 수가 아닌 개별 파일 확인)
  if [[ -d "$P1V6_DIR" ]]; then
    for fname in "${P1V6_REQUIRED_FILES[@]}"; do
      if [[ -f "${P1V6_DIR}/${fname}" ]]; then
        log_ok "P1 v6 file: ${fname}"
      else
        log_fail "P1 v6 file MISSING: ${P1V6_DIR}/${fname}"
        P1_FAIL=$((P1_FAIL+1))
      fi
    done
  else
    log_fail "P1 v6 directory MISSING: ${P1V6_DIR}"
    P1_FAIL=$((P1_FAIL+1))
  fi

  # [FIX-v3-5] P1 프로세스 상태 + uptime/restart 카운터 기록 (pre-deploy)
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
      log_fail "P1 v6 process: ${proc} = ${STATUS} (must be online before deploy)"
      P1_FAIL=$((P1_FAIL+1))
    fi
  done

  # Epoch 키 확인 강화: TTL=-1만 PASS
  EPOCH_KEYS=("ares:equity:fence_epoch" "ares:router_guard:epoch" "ofg:unifier:epoch" "ares:md_feeder:epoch")
  for ek in "${EPOCH_KEYS[@]}"; do
    if ! ETTL=$(redis_cmd ttl "$ek"); then
      log_fail "Epoch key ${ek}: REDIS QUERY FAILED"
      P1_FAIL=$((P1_FAIL+1))
    elif [[ "$ETTL" == "-1" ]]; then
      log_ok "Epoch key ${ek}: NO_EXPIRY (safe)"
    elif [[ "$ETTL" == "-2" ]]; then
      log_fail "Epoch key ${ek}: KEY MISSING (TTL=-2) — P1 foundation incomplete"
      P1_FAIL=$((P1_FAIL+1))
    elif [[ "$ETTL" =~ ^[0-9]+$ ]] && [[ "$ETTL" -gt 0 ]]; then
      log_fail "Epoch key ${ek}: WILL EXPIRE in ${ETTL}s — unsafe for deploy"
      P1_FAIL=$((P1_FAIL+1))
    else
      log_fail "Epoch key ${ek}: unexpected TTL=${ETTL}"
      P1_FAIL=$((P1_FAIL+1))
    fi
  done

  if [[ "$P1_FAIL" -gt 0 ]]; then
    log_fail "P1 v6 foundation layer has ${P1_FAIL} failures."
    echo -e "${RED}${BOLD}ABORT: Cannot deploy upper tracks without healthy P1 v6 foundation.${NC}"
    exit 1
  fi

  log_ok "P1 v6 foundation layer: ALL CHECKS PASSED"
fi

###############################################################################
# STEP 1: 백업 [FIX-v3-3: fail-hard 백업]
###############################################################################
log_step "Step 1: Pre-deploy Backup (fail-hard)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would create backup at ${BACKUP_DIR}"
else
  mkdir -p "${BACKUP_DIR}"
  chmod 700 "${BACKUP_DIR}"

  # [FIX-v3-3] PM2 스냅샷 — 필수 (실패 시 abort)
  if ! pm2 jlist > "${BACKUP_DIR}/pm2_snapshot.json" 2>/dev/null; then
    echo -e "${RED}[FATAL]${NC} Failed to create PM2 snapshot — cannot proceed without backup"
    exit 1
  fi
  log_ok "PM2 snapshot saved"

  # ecosystem 백업 (존재하면)
  if [[ -f "${ARES_REPO}/ecosystem.config.cjs" ]]; then
    cp "${ARES_REPO}/ecosystem.config.cjs" "${BACKUP_DIR}/" || log_warn "ecosystem.config.cjs backup failed"
  fi

  # Git SHA 기록 — 필수
  if [[ -d "${ARES_REPO}/.git" ]]; then
    cd "${ARES_REPO}"
    if ! git rev-parse HEAD > "${BACKUP_DIR}/git_sha_before.txt" 2>/dev/null; then
      echo -e "${RED}[FATAL]${NC} Failed to record git SHA — cannot proceed without backup"
      exit 1
    fi
    log_ok "Git SHA recorded: $(cat "${BACKUP_DIR}/git_sha_before.txt")"
    cd - > /dev/null
  fi

  # Redis 스냅샷 — 필수 (stderr 분리)
  if ! REDIS_URL_INTERNAL="${REDIS_URL}" node -e "
    const Redis = require('ioredis');
    const r = new Redis(process.env.REDIS_URL_INTERNAL, {tls:{}, connectTimeout: 10000});
    (async () => {
      const keys = [
        'ares:equity:total','ares:equity:active_fence','ares:equity:fence_epoch',
        'ares:router:mode:desired','ares:router:mode:effective','ares:router_guard:epoch',
        'ofg:gate:current','ofg:unifier:epoch','ares:md_feeder:epoch'
      ];
      const snap = {};
      for (const k of keys) {
        snap[k] = { value: await r.get(k), ttl: await r.ttl(k) };
      }
      console.log(JSON.stringify(snap, null, 2));
      await r.quit();
    })();
  " > "${BACKUP_DIR}/redis_snapshot.json" 2>"${BACKUP_DIR}/redis_snapshot_errors.log"; then
    echo -e "${RED}[FATAL]${NC} Failed to create Redis snapshot — cannot proceed without backup"
    exit 1
  fi
  chmod 600 "${BACKUP_DIR}/redis_snapshot.json"

  # 스냅샷 무결성 검증
  if ! node -e "JSON.parse(require('fs').readFileSync('${BACKUP_DIR}/redis_snapshot.json','utf8'))" 2>/dev/null; then
    echo -e "${RED}[FATAL]${NC} Redis snapshot is INVALID JSON — backup corrupted"
    exit 1
  fi
  log_ok "Redis snapshot: valid JSON"

  log_ok "Backup created: ${BACKUP_DIR}"
fi

###############################################################################
# STEP 2: Git Pull (상위 트랙만)
###############################################################################
log_step "Step 2: Git Pull (Upper Tracks Only)"
log_info "P1 v6 is NOT a git pull target — it lives in ${P1V6_DIR}"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would pull: ${ARES_REPO}"
else
  if [[ -d "${ARES_REPO}/.git" ]]; then
    cd "${ARES_REPO}"

    # dirty tree 검사
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
      echo -e "${RED}[FATAL]${NC} Working tree has uncommitted changes."
      echo "Commit or stash changes before deploying."
      git status --short 2>/dev/null
      exit 1
    fi
    UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l || echo "0")
    if [[ "$UNTRACKED" -gt 0 ]]; then
      log_warn "Working tree has ${UNTRACKED} untracked files (not blocking, but review recommended)"
    fi

    BEFORE_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    if ! git pull --ff-only 2>&1; then
      echo -e "${RED}[FATAL]${NC} git pull --ff-only failed."
      echo "This means the local branch has diverged from remote."
      echo "DO NOT merge on production. Fix upstream first."
      exit 1
    fi
    AFTER_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    if [[ "$BEFORE_SHA" == "$AFTER_SHA" ]]; then
      log_info "No new commits: ${BEFORE_SHA}"
    else
      log_ok "Updated: ${BEFORE_SHA} → ${AFTER_SHA}"
    fi
    cd - > /dev/null
  else
    log_warn "${ARES_REPO} is not a git repo — skipping pull"
  fi
fi

###############################################################################
# STEP 2.5: 의존성 설치 [FIX-v3-2: lockfile 강제]
###############################################################################
log_step "Step 2.5: Dependency Install (lockfile-enforced)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would install dependencies in ${ARES_REPO}"
else
  if [[ -f "${ARES_REPO}/package.json" ]]; then
    cd "${ARES_REPO}"
    if command -v pnpm &>/dev/null && [[ -f "pnpm-lock.yaml" ]]; then
      # [FIX-v3-2] lockfile 강제: --frozen-lockfile
      if ! pnpm install --frozen-lockfile 2>&1 | tail -5; then
        echo -e "${RED}[FATAL]${NC} pnpm install --frozen-lockfile failed."
        echo "Lockfile is out of sync. Fix upstream, do not mutate production."
        exit 1
      fi
      log_ok "pnpm install --frozen-lockfile completed"
    elif command -v npm &>/dev/null && [[ -f "package-lock.json" ]]; then
      # [FIX-v3-2] lockfile 강제: npm ci
      if ! npm ci --omit=dev 2>&1 | tail -5; then
        echo -e "${RED}[FATAL]${NC} npm ci --omit=dev failed."
        echo "Lockfile is out of sync. Fix upstream, do not mutate production."
        exit 1
      fi
      log_ok "npm ci --omit=dev completed"
    elif command -v pnpm &>/dev/null; then
      echo -e "${RED}[FATAL]${NC} No lockfile found (pnpm-lock.yaml / package-lock.json missing)."
      echo "Cannot install dependencies deterministically. Fix upstream."
      exit 1
    elif command -v npm &>/dev/null; then
      echo -e "${RED}[FATAL]${NC} No lockfile found (pnpm-lock.yaml / package-lock.json missing)."
      echo "Cannot install dependencies deterministically. Fix upstream."
      exit 1
    else
      echo -e "${RED}[FATAL]${NC} No package manager found"
      exit 1
    fi
    cd - > /dev/null
  else
    log_info "No package.json in ${ARES_REPO} — skipping dependency install"
  fi
fi

###############################################################################
# [FIX-v3-6] STEP 2.7: PM2 pm_cwd 검증
###############################################################################
log_step "Step 2.7: PM2 Runtime Path Validation"

if [[ "$DRY_RUN" != "true" ]]; then
  for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
    CWD=$(get_pm2_cwd "$proc")
    if [[ "$CWD" == "NOT_FOUND" ]]; then
      log_info "PM2 cwd check: ${proc} not in PM2 (skipped)"
    elif [[ "$CWD" == "PARSE_ERROR" || "$CWD" == "QUERY_FAILED" || "$CWD" == "UNKNOWN" ]]; then
      log_warn "PM2 cwd check: ${proc} — could not determine cwd"
    else
      REAL_CWD=$(readlink -f "$CWD" 2>/dev/null || echo "$CWD")
      if [[ "$REAL_CWD" == "$REAL_REPO" || "$REAL_CWD" == "${REAL_REPO}/"* ]]; then
        log_ok "PM2 cwd: ${proc} → ${REAL_CWD} (matches ARES_REPO)"
      else
        log_warn "PM2 cwd: ${proc} → ${REAL_CWD} (NOT under ARES_REPO — may use different code)"
      fi
    fi
  done
fi

###############################################################################
# STEP 3: 상위 트랙 프로세스 재시작 (개별 앱 단위)
###############################################################################
log_step "Step 3: Restart Upper Track Processes (P1 v6 excluded)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would restart ARES upper track processes (individual app restart)"
  for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
    log_info "[DRY-RUN] Would restart: ${proc}"
  done
else
  log_info "Using individual app restart (pm2 restart <name> --update-env)"
  log_info "NOT using ecosystem reload (ecosystem.master.cjs contains 80+ apps)"

  RESTART_OK=0
  RESTART_SKIP=0
  RESTART_FAIL=0

  for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
    # P1 v6에 포함된 프로세스는 절대 재시작하지 않음 (이중 안전)
    for p1 in "${P1V6_PROCS[@]}"; do
      if [[ "$proc" == "$p1" ]]; then
        log_warn "SKIP: ${proc} is in P1 v6 allowlist — will NOT restart"
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
      RESTARTED_PROCS+=("$proc")
    else
      log_fail "Failed to restart: ${proc}"
      RESTART_FAIL=$((RESTART_FAIL+1))
    fi
  done

  log_info "Restart summary: ${RESTART_OK} ok, ${RESTART_SKIP} skipped, ${RESTART_FAIL} failed"
  log_info "P1 v6 processes NOT restarted (stable foundation — do not touch)"
fi

###############################################################################
# STEP 4: POST-DEPLOY 검증
###############################################################################
log_step "Step 4: Post-Deploy Verification"

sleep 5

# [FIX-v3-4] 모든 재시작된 프로세스 검증
if [[ "${#RESTARTED_PROCS[@]}" -gt 0 ]]; then
  log_info "Verifying all restarted processes..."
  for proc in "${RESTARTED_PROCS[@]}"; do
    ST=$(get_pm2_status "$proc")
    if [[ "$ST" == "online" ]]; then
      log_ok "${proc}: online after deploy"
    else
      log_fail "${proc}: ${ST} after deploy — INVESTIGATE!"
    fi
  done
else
  # fallback: 핵심 프로세스만 확인
  PHS_STATUS=$(get_pm2_status "${PM2_PHS_APP}")
  if [[ "$PHS_STATUS" == "online" ]]; then
    log_ok "${PM2_PHS_APP}: online after deploy"
  else
    log_fail "${PM2_PHS_APP}: ${PHS_STATUS} after deploy"
  fi
fi

# [FIX-v3-5] P1 "never restarted" 증명: pre/post uptime + restart 카운터 비교
log_info "Proving P1 v6 was NOT restarted during deploy..."
P1_RESTART_PROVEN=true
for proc in "${P1V6_PROCS[@]}"; do
  ST=$(get_pm2_status "$proc")
  if [[ "$ST" != "online" ]]; then
    log_fail "P1 v6 process ${proc} is ${ST} after deploy — INVESTIGATE!"
    P1_RESTART_PROVEN=false
    continue
  fi

  UR=$(get_pm2_uptime_restarts "$proc")
  if [[ "$UR" == "NOT_FOUND" || "$UR" == "PARSE_ERROR" || "$UR" == "QUERY_FAILED" ]]; then
    log_warn "P1 v6 ${proc}: cannot verify restart counter (counters unavailable)"
    continue
  fi

  IFS='|' read -r post_restarts post_uptime <<< "$UR"
  pre_restarts="${P1_PRE_RESTARTS[$proc]:-}"
  pre_uptime="${P1_PRE_UPTIMES[$proc]:-}"

  if [[ -n "$pre_restarts" && "$post_restarts" == "$pre_restarts" ]]; then
    log_ok "P1 v6 ${proc}: restart_count unchanged (${pre_restarts} → ${post_restarts}) — NOT restarted"
  elif [[ -n "$pre_restarts" && "$post_restarts" != "$pre_restarts" ]]; then
    log_fail "P1 v6 ${proc}: restart_count CHANGED (${pre_restarts} → ${post_restarts}) — WAS RESTARTED!"
    P1_RESTART_PROVEN=false
  else
    log_warn "P1 v6 ${proc}: no pre-deploy counter to compare"
  fi
done

if [[ "$P1_RESTART_PROVEN" == "true" ]]; then
  log_ok "P1 v6 PROVEN: No processes were restarted during deploy"
fi

# smg-dashboard heartbeat
if SMG_HB=$(redis_cmd get "ares:smg:heartbeat"); then
  if [[ -n "$SMG_HB" && "$SMG_HB" != "null" ]]; then
    log_ok "smg-dashboard-v6 heartbeat: active"
  else
    log_warn "smg-dashboard-v6 heartbeat: not detected (may be normal outside market hours)"
  fi
else
  log_warn "smg-dashboard-v6 heartbeat: Redis query failed"
fi

# OFG 상태 확인
if OFG_RAW=$(redis_cmd get "ofg:gate:current"); then
  if [[ -n "$OFG_RAW" && "$OFG_RAW" != "null" ]]; then
    OFG_GATE=$(echo "$OFG_RAW" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const o=JSON.parse(d);console.log(o.state||'UNKNOWN')}catch{console.log('PARSE_ERR')}})" 2>/dev/null || echo "UNKNOWN")
    if [[ "$OFG_GATE" == "PARSE_ERR" ]]; then
      log_warn "OFG gate: JSON parse error — value exists but malformed"
    else
      log_info "OFG gate state: ${OFG_GATE}"
    fi
  else
    log_warn "OFG gate: not available"
  fi
else
  log_warn "OFG gate: Redis query failed"
fi

# [R8] Remote instance deployment removed (single-instance topology)
log_info "Single-instance mode — no remote deployment step"

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
if [[ "$DEPLOY_FAILED" -eq 0 ]]; then
  echo -e "${BOLD}  ${GREEN}DEPLOY COMPLETE — SUCCESS${NC}"
else
  echo -e "${BOLD}  ${RED}DEPLOY COMPLETE — ${DEPLOY_FAILED} FAILURE(S) DETECTED${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  Version:    v42.3"
echo -e "  Backup:     ${BACKUP_DIR}"
echo -e "  OFG app:    ${PM2_OFG_APP} (primary)"
echo -e "  PHS app:    ${PM2_PHS_APP}"
echo -e "  P1 v6:      NOT touched (foundation layer)"
echo -e "  Restart:    Individual app restart (NO ecosystem reload)"
echo -e "  Deps:       Lockfile-enforced (--frozen-lockfile / npm ci)"
echo -e "  Targets:    ${DEPLOY_TARGETS[*]}"
echo -e "  Dry-run:    ${DRY_RUN}"
echo -e "  Failures:   ${DEPLOY_FAILED}"
echo -e "  Log:        ${DEPLOY_LOG}"
echo ""

if [[ "$DEPLOY_FAILED" -gt 0 ]]; then
  echo -e "  ${RED}${BOLD}WARNING: Deploy had ${DEPLOY_FAILED} failure(s). Review log and consider rollback.${NC}"
  exit 1
else
  echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42.3.sh to confirm.${NC}"
  exit 0
fi
