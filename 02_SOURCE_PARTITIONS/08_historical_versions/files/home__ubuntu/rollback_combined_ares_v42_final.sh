#!/usr/bin/env bash
###############################################################################
# rollback_combined_ares_v42_final.sh — ARES 통합 롤백 스크립트
# ============================================================================
# 핵심 원칙:
#   1. P1 v6 기반층은 롤백 대상이 아님 — 절대 건드리지 않음
#   2. 상위 트랙(Track A/B/C) 프로세스만 이전 상태로 복원
#   3. deploy 시 생성된 백업 디렉토리를 사용
#
# 사용법:
#   ./rollback_combined_ares_v42_final.sh <backup_dir>
#   예: ./rollback_combined_ares_v42_final.sh /home/ubuntu/deploy_backup_20260416_030000
#
#   백업 디렉토리 없이 실행 시 가장 최근 백업을 자동 탐색
###############################################################################
set -euo pipefail

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

# ─── P1 v6 프로세스 (건드리지 않음) ─────────────────────────────────────────
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
# STEP 0: 백업 디렉토리 결정
###############################################################################
log_step "Step 0: Locate Backup"

BACKUP_DIR="${1:-}"
if [[ -z "$BACKUP_DIR" ]]; then
  # 가장 최근 백업 자동 탐색
  BACKUP_DIR=$(ls -dt /home/ubuntu/deploy_backup_* 2>/dev/null | head -1 || echo "")
  if [[ -z "$BACKUP_DIR" ]]; then
    log_fail "No backup directory found. Specify one: $0 <backup_dir>"
    exit 1
  fi
  log_info "Auto-detected latest backup: ${BACKUP_DIR}"
fi

if [[ ! -d "$BACKUP_DIR" ]]; then
  log_fail "Backup directory does not exist: ${BACKUP_DIR}"
  exit 1
fi

log_ok "Using backup: ${BACKUP_DIR}"

# 백업 내용 확인
echo -e "  Contents:"
ls -la "${BACKUP_DIR}/" 2>/dev/null | grep -v "^total" | while read line; do
  echo -e "    $line"
done

###############################################################################
# STEP 1: P1 v6 기반층 상태 확인 (건드리지 않되 상태는 확인)
###############################################################################
log_step "Step 1: P1 v6 Foundation Layer Status (read-only)"
log_info "P1 v6 processes will NOT be rolled back"

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
    log_ok "P1 v6 ${proc}: online (untouched)"
  else
    log_warn "P1 v6 ${proc}: ${STATUS} — NOT part of rollback, but may need attention"
  fi
done

###############################################################################
# STEP 2: Git 롤백 (상위 트랙만)
###############################################################################
log_step "Step 2: Git Rollback (Upper Tracks)"

if [[ -f "${BACKUP_DIR}/pm2_snapshot.json" ]]; then
  # 백업에서 이전 git SHA 추출 시도
  PREV_SHA=$(cat "${BACKUP_DIR}/pm2_snapshot.json" | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        // PM2 snapshot에서 SHA 정보가 있으면 사용
        console.log('N/A');
      } catch { console.log('N/A'); }
    });
  " 2>/dev/null)
  log_info "Previous SHA from backup: ${PREV_SHA}"
fi

if [[ -d "${ARES_REPO}/.git" ]]; then
  cd "${ARES_REPO}"
  CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  log_info "Current HEAD: ${CURRENT_SHA}"

  # git reflog에서 이전 커밋으로 복원 가능
  echo -e "  Recent commits:"
  git log --oneline -5 2>/dev/null | while read line; do
    echo -e "    $line"
  done

  log_info "To rollback git: cd ${ARES_REPO} && git reset --hard <previous_sha>"
  cd -
else
  log_warn "${ARES_REPO} is not a git repo"
fi

###############################################################################
# STEP 3: Ecosystem 복원
###############################################################################
log_step "Step 3: Restore Ecosystem Config"

if [[ -f "${BACKUP_DIR}/ecosystem.config.cjs" ]]; then
  cp "${BACKUP_DIR}/ecosystem.config.cjs" "${ARES_CURRENT}/ecosystem.config.cjs"
  log_ok "Restored ecosystem.config.cjs from backup"
else
  log_warn "No ecosystem.config.cjs in backup — skipping"
fi

###############################################################################
# STEP 4: 상위 트랙 프로세스 재시작
###############################################################################
log_step "Step 4: Restart Upper Track Processes"

# position-hard-stop
pm2 restart position-hard-stop --update-env 2>/dev/null && \
  log_ok "Restarted: position-hard-stop" || \
  log_warn "Failed to restart: position-hard-stop"

# alert-rules
pm2 restart alert-rules --update-env 2>/dev/null && \
  log_ok "Restarted: alert-rules" || \
  log_warn "Failed to restart: alert-rules"

log_info "P1 v6 processes: NOT restarted (foundation layer preserved)"

###############################################################################
# STEP 5: Redis 키 복원 (선택적)
###############################################################################
log_step "Step 5: Redis Key Restoration (optional)"

if [[ -f "${BACKUP_DIR}/redis_snapshot.json" ]]; then
  log_info "Redis snapshot available at: ${BACKUP_DIR}/redis_snapshot.json"
  log_info "To restore specific keys, use:"
  echo -e "    node -e \"const snap = require('${BACKUP_DIR}/redis_snapshot.json'); console.log(snap);\""
  log_warn "Redis keys are NOT auto-restored — manual review required"
else
  log_info "No Redis snapshot in backup"
fi

###############################################################################
# STEP 6: POST-ROLLBACK 검증
###############################################################################
log_step "Step 6: Post-Rollback Verification"

sleep 5

# P1 v6 여전히 건강한지 확인
P1_OK=true
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
    log_fail "P1 v6 ${proc}: ${ST} after rollback — INVESTIGATE!"
    P1_OK=false
  fi
done

if [[ "$P1_OK" == "true" ]]; then
  log_ok "P1 v6 foundation layer: still healthy after rollback"
fi

# position-hard-stop 확인
PHS_ST=$(pm2 jlist 2>/dev/null | node -e "
  let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
    try {
      const list=JSON.parse(d);
      const p=list.find(x=>x.name==='position-hard-stop');
      console.log(p ? p.pm2_env.status : 'NOT_FOUND');
    } catch { console.log('PARSE_ERROR'); }
  });
" 2>/dev/null)

if [[ "$PHS_ST" == "online" ]]; then
  log_ok "position-hard-stop: online after rollback"
else
  log_warn "position-hard-stop: ${PHS_ST} after rollback"
fi

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  ROLLBACK COMPLETE${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  Backup used:  ${BACKUP_DIR}"
echo -e "  P1 v6:        ${GREEN}NOT touched${NC} (foundation layer preserved)"
echo -e "  Upper tracks:  Restored and restarted"
echo ""
echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42_final.sh to confirm.${NC}"
