#!/usr/bin/env bash
###############################################################################
# deploy_combined_ares_v42_patched.sh — ARES 통합 배포 스크립트 (4AI 피드백 반영)
# ============================================================================
# 패치 내용 (v42_final → v42_patched):
#   [PATCH-1] 의존성 설치 단계 추가 (pnpm install)
#   [PATCH-2] 상위 트랙 프로세스 포괄적 재시작 (P1 v6 제외 전체)
#   [PATCH-3] 2>/dev/null 최소화 → 에러 로그 파일로 리다이렉트
#   [PATCH-4] ARES_CURRENT 심볼릭 링크/동기화 명확화
#   [PATCH-5] deploy 시 이전 SHA를 백업에 기록 (rollback용)
#   [PATCH-6] smg-dashboard-v6 heartbeat 체크 추가
#   [PATCH-7] 실패 시 non-zero exit 보장 (DEPLOY_FAILED 카운터)
#   [PATCH-8] redis_cmd() 실패 시 명시적 exit code
#   [PATCH-9] flock 동시 실행 방지
#   [PATCH-10] set -e 취약 구문 수정 (find 사용)
#   [PATCH-11] 백업 파일 권한 보호 (chmod 700)
#   [PATCH-12] ARES_REPO/ARES_CURRENT 동일성 검증
#
# Layer 구조:
#   Layer 0: P1 v6 foundation  → 사전 존재/상태 확인 대상 (배포 대상 아님)
#   Track B: v2.1 bundle       → git pull 대상
#   Track A: position-hard-stop v4.2 → git pull 대상
#   Track C: P3 post-trade / OFG → git pull 대상
#
# 핵심 원칙:
#   1. P1 v6은 /home/ubuntu/patches/v6/에 이미 배포된 기반층
#      → repo pull 대상이 아니라 사전 존재/상태 확인 대상
#   2. OFG 기본값은 ofg-unifier-v6 (order-flow-governor 아님)
#   3. deploy는 P1 v6 기반층이 살아있는지 먼저 확인한 뒤 상위 트랙 진행
#
# 사용법:
#   chmod +x deploy_combined_ares_v42_patched.sh
#   ./deploy_combined_ares_v42_patched.sh [--dry-run] [--skip-p1-check]
###############################################################################
set -euo pipefail

# [PATCH-9] flock 동시 실행 방지
LOCK_FILE="/tmp/ares_deploy.lock"
exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  echo "[FATAL] Another deploy/rollback is already running. Exiting."
  exit 1
fi

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

# [PATCH-7] 실패 카운터 — 핵심 실패 시 non-zero exit 보장
DEPLOY_FAILED=0

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; DEPLOY_FAILED=$((DEPLOY_FAILED+1)); }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 에러 로그 [PATCH-3] ────────────────────────────────────────────────────
DEPLOY_LOG="/home/ubuntu/deploy_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${DEPLOY_LOG}") 2>&1
log_info "Deploy log: ${DEPLOY_LOG}"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
# [PATCH-4] ARES_CURRENT는 ARES_REPO의 심볼릭 링크 또는 동일 경로
# ARES_REPO: git 관리 소스 코드 디렉토리
# ARES_CURRENT: PM2가 참조하는 런타임 디렉토리 (보통 ARES_REPO와 동일하거나 심볼릭 링크)
ARES_CURRENT="/home/ubuntu/ares_current"
ARES_REPO="/home/ubuntu/ARES-KIS-US-AUTOPILOT"
P1V6_DIR="/home/ubuntu/patches/v6"
BACKUP_DIR="/home/ubuntu/deploy_backup_$(date +%Y%m%d_%H%M%S)"

# [PATCH-12] ARES_REPO/ARES_CURRENT 동일성 검증
if [[ -L "$ARES_CURRENT" ]]; then
  REAL_CURRENT=$(readlink -f "$ARES_CURRENT")
  REAL_REPO=$(readlink -f "$ARES_REPO")
  if [[ "$REAL_CURRENT" != "$REAL_REPO" ]]; then
    log_warn "ARES_CURRENT(${REAL_CURRENT}) != ARES_REPO(${REAL_REPO}) — ecosystem may not reflect latest code"
  else
    log_ok "ARES_CURRENT symlink verified → ${REAL_REPO}"
  fi
elif [[ -d "$ARES_CURRENT" ]]; then
  log_info "ARES_CURRENT is a directory (not symlink) — ensure it stays in sync with ARES_REPO"
fi

# ─── PM2 프로세스명 기본값 ──────────────────────────────────────────────────
PM2_OFG_APP="${PM2_OFG_APP:-ofg-unifier-v6}"
PM2_PHS_APP="${PM2_PHS_APP:-position-hard-stop}"
PM2_ALERT_APP="${PM2_ALERT_APP:-alert-rules}"

# ─── P1 v6 프로세스 목록 (기반 안전층 — 절대 재시작하지 않음) ──────────────
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# ─── Redis 접속 정보 ─────────────────────────────────────────────────────────
# REDIS_URL은 환경변수로 주입 권장. 아래 기본값은 fallback용.
REDIS_URL="${REDIS_URL:-rediss://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379}"

# [PATCH-8] redis_cmd: 에러 시 명시적 non-zero exit + stderr 출력
redis_cmd() {
  local result
  result=$(node -e "
    const Redis = require('ioredis');
    const r = new Redis('${REDIS_URL}', {tls:{}});
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
  " "$@" 2>&1) || { echo "REDIS_CMD_FAILED"; return 1; }
  echo "$result"
}

# PM2 상태 조회 헬퍼 (중복 제거)
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

# P1 v6 제외한 상위 트랙 프로세스 목록 조회
get_upper_track_procs() {
  pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p1v6 = new Set(['eta-v6','router-guard-v6','ofg-unifier-v6','md-feeder-v6','smg-dashboard-v6']);
        const upper = list.filter(x => !p1v6.has(x.name)).map(x => x.name);
        console.log(upper.join('\\n'));
      } catch { console.log(''); }
    });
  " 2>/dev/null
}

###############################################################################
# PRE-FLIGHT: P1 v6 기반층 상태 확인 (배포 전 필수)
###############################################################################
log_step "PRE-FLIGHT: P1 v6 Foundation Layer Check"

if [[ "$SKIP_P1_CHECK" == "true" ]]; then
  log_warn "P1 v6 check skipped by --skip-p1-check flag"
else
  P1_FAIL=0

  # [PATCH-10] 파일 존재 확인 — find 사용 (set -e safe)
  if [[ -d "$P1V6_DIR" ]]; then
    P1_FILE_COUNT=$(find "${P1V6_DIR}" -maxdepth 1 -name 'p1-*.mjs' -type f 2>/dev/null | wc -l || echo "0")
    if [[ "$P1_FILE_COUNT" -ge 5 ]]; then
      log_ok "P1 v6 files present: ${P1_FILE_COUNT} modules in ${P1V6_DIR}"
    else
      log_fail "P1 v6 files incomplete: only ${P1_FILE_COUNT} found (need 5+)"
      ((P1_FAIL++))
    fi
  else
    log_fail "P1 v6 directory MISSING: ${P1V6_DIR}"
    ((P1_FAIL++))
  fi

  # PM2 프로세스 상태 확인
  for proc in "${P1V6_PROCS[@]}"; do
    STATUS=$(get_pm2_status "$proc")
    if [[ "$STATUS" == "online" ]]; then
      log_ok "P1 v6 process: ${proc} = online"
    else
      log_fail "P1 v6 process: ${proc} = ${STATUS} (must be online before deploy)"
      ((P1_FAIL++))
    fi
  done

  # Epoch 키 확인 [PATCH-8: Redis 실패 시 fail-close]
  EPOCH_KEYS=("ares:equity:fence_epoch" "ares:router_guard:epoch" "ofg:unifier:epoch" "ares:md_feeder:epoch")
  for ek in "${EPOCH_KEYS[@]}"; do
    ETTL=$(redis_cmd ttl "$ek" || echo "REDIS_FAIL")
    if [[ "$ETTL" == "REDIS_FAIL" || "$ETTL" == "REDIS_CMD_FAILED" ]]; then
      log_fail "Epoch key ${ek}: REDIS QUERY FAILED"
      ((P1_FAIL++))
    elif [[ "$ETTL" == "-1" ]]; then
      log_ok "Epoch key ${ek}: NO_EXPIRY (safe)"
    else
      log_warn "Epoch key ${ek}: TTL=${ETTL} (unexpected)"
    fi
  done

  if [[ "$P1_FAIL" -gt 0 ]]; then
    log_fail "P1 v6 foundation layer has ${P1_FAIL} failures."
    echo -e "${RED}${BOLD}ABORT: Cannot deploy upper tracks without healthy P1 v6 foundation.${NC}"
    echo -e "Fix P1 v6 issues first, or use --skip-p1-check to override (NOT recommended)."
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
  # [PATCH-11] 백업 디렉토리 권한 보호
  chmod 700 "${BACKUP_DIR}"

  # PM2 프로세스 목록 스냅샷
  pm2 jlist > "${BACKUP_DIR}/pm2_snapshot.json" 2>/dev/null || true

  # 현재 ecosystem 백업
  cp "${ARES_CURRENT}/ecosystem.config.cjs" "${BACKUP_DIR}/" 2>/dev/null || true

  # [PATCH-5] 현재 Git SHA를 백업에 기록 (rollback용)
  if [[ -d "${ARES_REPO}/.git" ]]; then
    cd "${ARES_REPO}"
    git rev-parse HEAD > "${BACKUP_DIR}/git_sha_before.txt" 2>/dev/null || true
    cd -
  fi

  # Redis 핵심 키 스냅샷
  node -e "
    const Redis = require('ioredis');
    const r = new Redis('${REDIS_URL}', {tls:{}});
    (async () => {
      const keys = [
        'ares:equity:total','ares:equity:active_fence','ares:equity:fence_epoch',
        'ares:router:mode:desired','ares:router:mode:effective','ares:router_guard:epoch',
        'ofg:gate:current','ofg:unifier:epoch',
        'ares:md_feeder:epoch'
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
# STEP 2: Git Pull (상위 트랙만 — P1 v6은 대상 아님)
###############################################################################
log_step "Step 2: Git Pull (Upper Tracks Only)"
log_info "P1 v6 is NOT a git pull target — it lives in ${P1V6_DIR}"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would pull: ${ARES_REPO}"
else
  if [[ -d "${ARES_REPO}/.git" ]]; then
    cd "${ARES_REPO}"
    BEFORE_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    git pull --ff-only 2>&1 || {
      log_warn "git pull --ff-only failed, trying git pull"
      git pull 2>&1 || log_warn "git pull failed — continuing with current code"
    }
    AFTER_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    if [[ "$BEFORE_SHA" == "$AFTER_SHA" ]]; then
      log_info "No new commits: ${BEFORE_SHA}"
    else
      log_ok "Updated: ${BEFORE_SHA} → ${AFTER_SHA}"
    fi
    cd -
  else
    log_warn "${ARES_REPO} is not a git repo — skipping pull"
  fi
fi

###############################################################################
# STEP 2.5: 의존성 설치 [PATCH-1]
###############################################################################
log_step "Step 2.5: Install Dependencies"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would install dependencies in ${ARES_REPO}"
else
  if [[ -f "${ARES_REPO}/package.json" ]]; then
    cd "${ARES_REPO}"
    if command -v pnpm &>/dev/null; then
      pnpm install --prod 2>&1 | tail -5 && log_ok "pnpm install completed" || log_warn "pnpm install had warnings"
    elif command -v npm &>/dev/null; then
      npm install --production 2>&1 | tail -5 && log_ok "npm install completed" || log_warn "npm install had warnings"
    else
      log_warn "No package manager found — skipping dependency install"
    fi
    cd -
  else
    log_info "No package.json in ${ARES_REPO} — skipping dependency install"
  fi
fi

###############################################################################
# STEP 3: 상위 트랙 프로세스 재시작 [PATCH-2: 포괄적 재시작]
###############################################################################
log_step "Step 3: Restart Upper Track Processes (P1 v6 excluded)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would restart all upper track processes"
  UPPER_PROCS=$(get_upper_track_procs)
  if [[ -n "$UPPER_PROCS" ]]; then
    echo "$UPPER_PROCS" | while read -r proc; do
      [[ -n "$proc" ]] && log_info "[DRY-RUN] Would restart: ${proc}"
    done
  fi
else
  UPPER_PROCS=$(get_upper_track_procs)

  if [[ -n "$UPPER_PROCS" ]]; then
    while read -r proc; do
      if [[ -n "$proc" ]]; then
        pm2 restart "$proc" --update-env 2>&1 && \
          log_ok "Restarted: ${proc}" || \
          log_fail "Failed to restart: ${proc}"
      fi
    done <<< "$UPPER_PROCS"
  else
    # 최소한 핵심 프로세스는 재시작
    pm2 restart "${PM2_PHS_APP}" --update-env 2>&1 && \
      log_ok "Restarted: ${PM2_PHS_APP}" || \
      log_fail "Failed to restart: ${PM2_PHS_APP}"

    pm2 restart "${PM2_ALERT_APP}" --update-env 2>&1 && \
      log_ok "Restarted: ${PM2_ALERT_APP}" || \
      log_fail "Failed to restart: ${PM2_ALERT_APP}"
  fi

  log_info "P1 v6 processes NOT restarted (stable foundation — do not touch)"
fi

###############################################################################
# STEP 4: POST-DEPLOY 검증
###############################################################################
log_step "Step 4: Post-Deploy Verification"

sleep 5  # 프로세스 안정화 대기

# position-hard-stop 확인 — 변수명 사용 [PATCH-2]
PHS_STATUS=$(get_pm2_status "${PM2_PHS_APP}")
if [[ "$PHS_STATUS" == "online" ]]; then
  log_ok "${PM2_PHS_APP}: online after deploy"
else
  log_fail "${PM2_PHS_APP}: ${PHS_STATUS} after deploy"
fi

# P1 v6 프로세스 재확인 (배포가 깨뜨리지 않았는지)
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

# [PATCH-6] smg-dashboard-v6 heartbeat 체크
SMG_HB=$(redis_cmd get "ares:smg_dashboard:heartbeat" 2>/dev/null || echo "")
if [[ -n "$SMG_HB" && "$SMG_HB" != "null" && "$SMG_HB" != "REDIS_CMD_FAILED" ]]; then
  log_ok "smg-dashboard-v6 heartbeat: active"
else
  log_warn "smg-dashboard-v6 heartbeat: not detected (may be normal outside market hours)"
fi

# OFG 상태 확인
OFG_RAW=$(redis_cmd get "ofg:gate:current" 2>/dev/null || echo "")
if [[ -n "$OFG_RAW" && "$OFG_RAW" != "null" && "$OFG_RAW" != "REDIS_CMD_FAILED" ]]; then
  OFG_GATE=$(echo "$OFG_RAW" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const o=JSON.parse(d);console.log(o.state||'UNKNOWN')}catch{console.log('PARSE_ERR')}})" 2>/dev/null || echo "UNKNOWN")
  if [[ "$OFG_GATE" == "PARSE_ERR" ]]; then
    log_warn "OFG gate: JSON parse error — value exists but malformed"
  else
    log_info "OFG gate state: ${OFG_GATE}"
  fi
else
  log_warn "OFG gate: not available"
fi

###############################################################################
# SUMMARY [PATCH-7: 실패 시 non-zero exit]
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
  echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42_patched.sh to confirm.${NC}"
  exit 0
fi
