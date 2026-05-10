#!/usr/bin/env bash
###############################################################################
# deploy_combined_ares_v42_final.sh — ARES 통합 배포 스크립트
# ============================================================================
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
#   chmod +x deploy_combined_ares_v42_final.sh
#   ./deploy_combined_ares_v42_final.sh [--dry-run] [--skip-p1-check]
###############################################################################
set -euo pipefail

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

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
ARES_REPO="/home/ubuntu/ARES-KIS-US-AUTOPILOT"
P1V6_DIR="/home/ubuntu/patches/v6"
BACKUP_DIR="/home/ubuntu/deploy_backup_$(date +%Y%m%d_%H%M%S)"

# ─── PM2 프로세스명 기본값 ──────────────────────────────────────────────────
# v6 업데이트: OFG는 ofg-unifier-v6가 최우선 기본값
PM2_OFG_APP="${PM2_OFG_APP:-ofg-unifier-v6}"
PM2_PHS_APP="${PM2_PHS_APP:-position-hard-stop}"
PM2_ALERT_APP="${PM2_ALERT_APP:-alert-rules}"

# ─── P1 v6 프로세스 목록 (기반 안전층) ──────────────────────────────────────
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# ─── Redis 접속 정보 ─────────────────────────────────────────────────────────
REDIS_URL="${REDIS_URL:-rediss://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379}"

redis_cmd() {
  node -e "
    const Redis = require('ioredis');
    const r = new Redis('${REDIS_URL}', {tls:{}});
    (async () => {
      try {
        const args = process.argv.slice(1);
        const cmd = args[0];
        const rest = args.slice(1);
        const result = await r[cmd](...rest);
        console.log(typeof result === 'object' ? JSON.stringify(result) : result);
      } catch(e) { console.error('REDIS_ERROR:', e.message); }
      await r.quit();
    })();
  " "$@" 2>/dev/null
}

###############################################################################
# PRE-FLIGHT: P1 v6 기반층 상태 확인 (배포 전 필수)
###############################################################################
log_step "PRE-FLIGHT: P1 v6 Foundation Layer Check"

if [[ "$SKIP_P1_CHECK" == "true" ]]; then
  log_warn "P1 v6 check skipped by --skip-p1-check flag"
else
  P1_FAIL=0

  # 파일 존재 확인
  if [[ -d "$P1V6_DIR" ]]; then
    P1_FILE_COUNT=$(ls "${P1V6_DIR}"/p1-*.mjs 2>/dev/null | wc -l)
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
    STATUS=$(pm2 jlist 2>/dev/null | node -e "
      let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
        try {
          const list=JSON.parse(d);
          const p=list.find(x=>x.name==='${proc}');
          console.log(p ? p.pm2_env.status : 'NOT_FOUND');
        } catch { console.log('PARSE_ERROR'); }
      });
    " 2>/dev/null)

    if [[ "$STATUS" == "online" ]]; then
      log_ok "P1 v6 process: ${proc} = online"
    else
      log_fail "P1 v6 process: ${proc} = ${STATUS} (must be online before deploy)"
      ((P1_FAIL++))
    fi
  done

  # Epoch 키 확인
  EPOCH_KEYS=("ares:equity:fence_epoch" "ares:router_guard:epoch" "ofg:unifier:epoch" "ares:md_feeder:epoch")
  for ek in "${EPOCH_KEYS[@]}"; do
    ETTL=$(redis_cmd ttl "$ek")
    if [[ "$ETTL" == "-1" ]]; then
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

  # PM2 프로세스 목록 스냅샷
  pm2 jlist > "${BACKUP_DIR}/pm2_snapshot.json" 2>/dev/null || true

  # 현재 ecosystem 백업
  cp "${ARES_CURRENT}/ecosystem.config.cjs" "${BACKUP_DIR}/" 2>/dev/null || true

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
  " > "${BACKUP_DIR}/redis_snapshot.json" 2>/dev/null || true

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
    git pull --ff-only 2>/dev/null || {
      log_warn "git pull --ff-only failed, trying git pull"
      git pull 2>/dev/null || log_warn "git pull failed — continuing with current code"
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
# STEP 3: 상위 트랙 프로세스 재시작
###############################################################################
log_step "Step 3: Restart Upper Track Processes"

# position-hard-stop (Track A)
if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would restart: ${PM2_PHS_APP}"
else
  pm2 restart "${PM2_PHS_APP}" --update-env 2>/dev/null && \
    log_ok "Restarted: ${PM2_PHS_APP}" || \
    log_warn "Failed to restart: ${PM2_PHS_APP}"
fi

# alert-rules
if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would restart: ${PM2_ALERT_APP}"
else
  pm2 restart "${PM2_ALERT_APP}" --update-env 2>/dev/null && \
    log_ok "Restarted: ${PM2_ALERT_APP}" || \
    log_warn "Failed to restart: ${PM2_ALERT_APP}"
fi

# NOTE: P1 v6 프로세스는 재시작하지 않음 — 이미 안정적으로 구동 중
log_info "P1 v6 processes NOT restarted (stable foundation — do not touch)"

###############################################################################
# STEP 4: POST-DEPLOY 검증
###############################################################################
log_step "Step 4: Post-Deploy Verification"

sleep 5  # 프로세스 안정화 대기

# position-hard-stop 확인
PHS_OK=$(pm2 jlist 2>/dev/null | node -e "
  let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
    try {
      const list=JSON.parse(d);
      const p=list.find(x=>x.name==='position-hard-stop');
      console.log(p && p.pm2_env.status === 'online' ? 'OK' : 'FAIL');
    } catch { console.log('FAIL'); }
  });
" 2>/dev/null)

if [[ "$PHS_OK" == "OK" ]]; then
  log_ok "position-hard-stop: online after deploy"
else
  log_fail "position-hard-stop: NOT online after deploy"
fi

# P1 v6 프로세스 재확인 (배포가 깨뜨리지 않았는지)
P1_STILL_OK=true
for proc in "${P1V6_PROCS[@]}"; do
  ST=$(pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc}');
        console.log(p ? p.pm2_env.status : 'NOT_FOUND');
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null)
  if [[ "$ST" != "online" ]]; then
    log_fail "P1 v6 process ${proc} is ${ST} after deploy — INVESTIGATE!"
    P1_STILL_OK=false
  fi
done

if [[ "$P1_STILL_OK" == "true" ]]; then
  log_ok "P1 v6 foundation layer: still healthy after deploy"
fi

# OFG 상태 확인
OFG_GATE=$(redis_cmd get "ofg:gate:current" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{console.log(JSON.parse(d).state)}catch{console.log('UNKNOWN')}})" 2>/dev/null)
log_info "OFG gate state: ${OFG_GATE}"

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  DEPLOY COMPLETE${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  Backup:     ${BACKUP_DIR}"
echo -e "  OFG app:    ${PM2_OFG_APP} (primary)"
echo -e "  PHS app:    ${PM2_PHS_APP}"
echo -e "  P1 v6:      NOT touched (foundation layer)"
echo -e "  Dry-run:    ${DRY_RUN}"
echo ""
echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42_final.sh to confirm.${NC}"
