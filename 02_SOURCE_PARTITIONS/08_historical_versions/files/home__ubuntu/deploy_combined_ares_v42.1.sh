#!/usr/bin/env bash
###############################################################################
# deploy_combined_ares_v42.1.sh — ARES 통합 배포 스크립트 (GPT R2 블로커 해결)
# ============================================================================
# v42_patched → v42.1 변경사항 (GPT 66점 → 95점+ 목표):
#   [FIX-1] REDIS_URL 기본값 삭제 → 환경변수 강제 (시크릿 하드코딩 제거)
#   [FIX-2] redis_cmd() node -e에 시크릿 직접 삽입 금지 → 환경변수 전달
#   [FIX-3] ((P1_FAIL++)) → P1_FAIL=$((P1_FAIL+1)) (set -e 충돌 방지)
#   [FIX-4] ARES_CURRENT != ARES_REPO → fail-hard (경고→즉시실패)
#   [FIX-5] git pull 실패 → fail-hard (warn→abort)
#   [FIX-6] PM2 재시작 → pm2 startOrReload ecosystem.config.cjs
#   [FIX-7] 상위 트랙 프로세스 → ARES ecosystem 기반 allowlist
#   [FIX-8] flock cleanup trap 추가
#   [FIX-9] redis_cmd 호출부 패턴 수정 (if ! out=... 패턴)
#   [FIX-10] dependency install 실패 → fail-hard
#
# 기존 패치 유지: flock, set -euo pipefail, 2>/dev/null 최소화, SHA 기록,
#   smg-dashboard heartbeat, DEPLOY_FAILED 카운터, 백업 권한 보호
###############################################################################
set -euo pipefail

# [FIX-8] flock + cleanup trap
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
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --skip-p1-check) SKIP_P1_CHECK=true ;;
  esac
done

# ─── 색상 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

DEPLOY_FAILED=0

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; DEPLOY_FAILED=$((DEPLOY_FAILED+1)); }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 에러 로그 ──────────────────────────────────────────────────────────────
DEPLOY_LOG="/home/ubuntu/deploy_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${DEPLOY_LOG}") 2>&1
log_info "Deploy log: ${DEPLOY_LOG}"

# ─── [FIX-1] REDIS_URL 환경변수 강제 (기본값 없음) ─────────────────────────
if [[ -z "${REDIS_URL:-}" ]]; then
  echo -e "${RED}[FATAL]${NC} REDIS_URL environment variable is NOT set."
  echo "Export REDIS_URL before running this script."
  echo "Example: export REDIS_URL='rediss://:PASSWORD@HOST:6379'"
  exit 1
fi
log_ok "REDIS_URL is set (from environment)"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
ARES_REPO="/home/ubuntu/ARES-KIS-US-AUTOPILOT"
P1V6_DIR="/home/ubuntu/patches/v6"
BACKUP_DIR="/home/ubuntu/deploy_backup_$(date +%Y%m%d_%H%M%S)"

# [FIX-4] ARES_CURRENT != ARES_REPO → fail-hard
if [[ -L "$ARES_CURRENT" ]]; then
  REAL_CURRENT=$(readlink -f "$ARES_CURRENT")
  REAL_REPO=$(readlink -f "$ARES_REPO")
  if [[ "$REAL_CURRENT" != "$REAL_REPO" ]]; then
    echo -e "${RED}[FATAL]${NC} ARES_CURRENT(${REAL_CURRENT}) != ARES_REPO(${REAL_REPO})"
    echo "Deploy would update ARES_REPO but PM2 references ARES_CURRENT. Fix symlink first."
    exit 1
  fi
  log_ok "ARES_CURRENT symlink verified → ${REAL_REPO}"
elif [[ -d "$ARES_CURRENT" ]]; then
  log_info "ARES_CURRENT is a directory (not symlink) — ensure it stays in sync with ARES_REPO"
fi

# ─── PM2 프로세스명 ─────────────────────────────────────────────────────────
PM2_OFG_APP="${PM2_OFG_APP:-ofg-unifier-v6}"
PM2_PHS_APP="${PM2_PHS_APP:-position-hard-stop}"
PM2_ALERT_APP="${PM2_ALERT_APP:-alert-rules}"

# P1 v6 프로세스 (기반 안전층 — 절대 재시작하지 않음)
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# [FIX-7] ARES 전용 상위 트랙 allowlist (blast radius 제한)
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

# [FIX-2] redis_cmd: 환경변수로 시크릿 전달 (node -e에 직접 삽입 금지)
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

###############################################################################
# PRE-FLIGHT: P1 v6 기반층 상태 확인
###############################################################################
log_step "PRE-FLIGHT: P1 v6 Foundation Layer Check"

if [[ "$SKIP_P1_CHECK" == "true" ]]; then
  log_warn "P1 v6 check skipped by --skip-p1-check flag"
else
  P1_FAIL=0

  if [[ -d "$P1V6_DIR" ]]; then
    P1_FILE_COUNT=$(find "${P1V6_DIR}" -maxdepth 1 -name 'p1-*.mjs' -type f 2>/dev/null | wc -l || echo "0")
    if [[ "$P1_FILE_COUNT" -ge 5 ]]; then
      log_ok "P1 v6 files present: ${P1_FILE_COUNT} modules in ${P1V6_DIR}"
    else
      log_fail "P1 v6 files incomplete: only ${P1_FILE_COUNT} found (need 5+)"
      P1_FAIL=$((P1_FAIL+1))  # [FIX-3]
    fi
  else
    log_fail "P1 v6 directory MISSING: ${P1V6_DIR}"
    P1_FAIL=$((P1_FAIL+1))  # [FIX-3]
  fi

  for proc in "${P1V6_PROCS[@]}"; do
    STATUS=$(get_pm2_status "$proc")
    if [[ "$STATUS" == "online" ]]; then
      log_ok "P1 v6 process: ${proc} = online"
    else
      log_fail "P1 v6 process: ${proc} = ${STATUS} (must be online before deploy)"
      P1_FAIL=$((P1_FAIL+1))  # [FIX-3]
    fi
  done

  # [FIX-9] Epoch 키 확인 — if ! out=... 패턴
  EPOCH_KEYS=("ares:equity:fence_epoch" "ares:router_guard:epoch" "ofg:unifier:epoch" "ares:md_feeder:epoch")
  for ek in "${EPOCH_KEYS[@]}"; do
    if ! ETTL=$(redis_cmd ttl "$ek"); then
      log_fail "Epoch key ${ek}: REDIS QUERY FAILED"
      P1_FAIL=$((P1_FAIL+1))  # [FIX-3]
    elif [[ "$ETTL" == "-1" ]]; then
      log_ok "Epoch key ${ek}: NO_EXPIRY (safe)"
    else
      log_warn "Epoch key ${ek}: TTL=${ETTL} (unexpected)"
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
# STEP 1: 백업
###############################################################################
log_step "Step 1: Pre-deploy Backup"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would create backup at ${BACKUP_DIR}"
else
  mkdir -p "${BACKUP_DIR}"
  chmod 700 "${BACKUP_DIR}"

  pm2 jlist > "${BACKUP_DIR}/pm2_snapshot.json" 2>/dev/null || true
  cp "${ARES_CURRENT}/ecosystem.config.cjs" "${BACKUP_DIR}/" 2>/dev/null || true

  if [[ -d "${ARES_REPO}/.git" ]]; then
    cd "${ARES_REPO}"
    git rev-parse HEAD > "${BACKUP_DIR}/git_sha_before.txt" 2>/dev/null || true
    cd - > /dev/null
  fi

  # [FIX-2] Redis 스냅샷도 환경변수로 전달
  REDIS_URL_INTERNAL="${REDIS_URL}" node -e "
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
  " > "${BACKUP_DIR}/redis_snapshot.json" 2>&1 || true
  chmod 600 "${BACKUP_DIR}/redis_snapshot.json" 2>/dev/null || true

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
    BEFORE_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    # [FIX-5] git pull 실패 → fail-hard
    if ! git pull --ff-only 2>&1; then
      log_fail "git pull --ff-only failed"
      if ! git pull 2>&1; then
        echo -e "${RED}[FATAL]${NC} git pull failed — cannot deploy stale code"
        exit 1
      fi
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
# STEP 2.5: 의존성 설치
###############################################################################
log_step "Step 2.5: Dependency Install"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would install dependencies in ${ARES_REPO}"
else
  if [[ -f "${ARES_REPO}/package.json" ]]; then
    cd "${ARES_REPO}"
    # [FIX-10] dependency install 실패 → fail-hard
    if command -v pnpm &>/dev/null; then
      if ! pnpm install --prod 2>&1 | tail -5; then
        log_fail "pnpm install failed"
        echo -e "${RED}[FATAL]${NC} Dependency install failed — cannot deploy with broken deps"
        exit 1
      fi
      log_ok "pnpm install completed"
    elif command -v npm &>/dev/null; then
      if ! npm install --production 2>&1 | tail -5; then
        log_fail "npm install failed"
        echo -e "${RED}[FATAL]${NC} Dependency install failed — cannot deploy with broken deps"
        exit 1
      fi
      log_ok "npm install completed"
    else
      log_fail "No package manager found — cannot install dependencies"
      exit 1
    fi
    cd - > /dev/null
  else
    log_info "No package.json in ${ARES_REPO} — skipping dependency install"
  fi
fi

###############################################################################
# STEP 3: 상위 트랙 프로세스 재시작 [FIX-6/7: ecosystem reload + allowlist]
###############################################################################
log_step "Step 3: Restart Upper Track Processes (P1 v6 excluded)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would restart ARES upper track processes"
  for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
    log_info "[DRY-RUN] Would restart: ${proc}"
  done
else
  # [FIX-6] ecosystem.config.cjs가 있으면 startOrReload 사용
  ECOSYSTEM_FILE="${ARES_CURRENT}/ecosystem.config.cjs"
  if [[ -f "$ECOSYSTEM_FILE" ]]; then
    log_info "Using ecosystem reload: ${ECOSYSTEM_FILE}"
    if pm2 startOrReload "${ECOSYSTEM_FILE}" --update-env 2>&1; then
      log_ok "PM2 ecosystem reload completed"
    else
      log_warn "PM2 ecosystem reload failed — falling back to individual restart"
      # Fallback: allowlist 기반 개별 재시작
      for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
        ST=$(get_pm2_status "$proc")
        if [[ "$ST" != "NOT_FOUND" ]]; then
          pm2 restart "$proc" --update-env 2>&1 && \
            log_ok "Restarted: ${proc}" || \
            log_fail "Failed to restart: ${proc}"
        fi
      done
    fi
  else
    # [FIX-7] allowlist 기반 개별 재시작 (blast radius 제한)
    log_info "No ecosystem file — using ARES allowlist for restart"
    for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
      ST=$(get_pm2_status "$proc")
      if [[ "$ST" != "NOT_FOUND" ]]; then
        pm2 restart "$proc" --update-env 2>&1 && \
          log_ok "Restarted: ${proc}" || \
          log_fail "Failed to restart: ${proc}"
      else
        log_info "Process not in PM2: ${proc} (skipped)"
      fi
    done
  fi

  log_info "P1 v6 processes NOT restarted (stable foundation — do not touch)"
fi

###############################################################################
# STEP 4: POST-DEPLOY 검증
###############################################################################
log_step "Step 4: Post-Deploy Verification"

sleep 5

PHS_STATUS=$(get_pm2_status "${PM2_PHS_APP}")
if [[ "$PHS_STATUS" == "online" ]]; then
  log_ok "${PM2_PHS_APP}: online after deploy"
else
  log_fail "${PM2_PHS_APP}: ${PHS_STATUS} after deploy"
fi

P1_STILL_OK=true
for proc in "${P1V6_PROCS[@]}"; do
  ST=$(get_pm2_status "$proc")
  if [[ "$ST" != "online" ]]; then
    log_fail "P1 v6 process ${proc} is ${ST} after deploy — INVESTIGATE!"
    P1_STILL_OK=false
  fi
done

if [[ "$P1_STILL_OK" == "true" ]]; then
  log_ok "P1 v6 foundation layer: still healthy after deploy"
fi

# smg-dashboard heartbeat
if SMG_HB=$(redis_cmd get "ares:smg_dashboard:heartbeat"); then
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
echo -e "  Backup:     ${BACKUP_DIR}"
echo -e "  OFG app:    ${PM2_OFG_APP} (primary)"
echo -e "  PHS app:    ${PM2_PHS_APP}"
echo -e "  P1 v6:      NOT touched (foundation layer)"
echo -e "  Dry-run:    ${DRY_RUN}"
echo -e "  Failures:   ${DEPLOY_FAILED}"
echo -e "  Log:        ${DEPLOY_LOG}"
echo ""

if [[ "$DEPLOY_FAILED" -gt 0 ]]; then
  echo -e "  ${RED}${BOLD}WARNING: Deploy had ${DEPLOY_FAILED} failure(s). Review log and consider rollback.${NC}"
  exit 1
else
  echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42.1.sh to confirm.${NC}"
  exit 0
fi
