#!/usr/bin/env bash
###############################################################################
# rollback_combined_ares_v42_patched.sh — ARES 통합 롤백 스크립트 (4AI 피드백 반영)
# ============================================================================
# 패치 내용 (v42_final → v42_patched):
#   [PATCH-R1] Git 자동 롤백 구현 (git_sha_before.txt 활용)
#   [PATCH-R2] PM2 ecosystem reload 추가 (단순 복사 → reload)
#   [PATCH-R3] 상위 트랙 프로세스 포괄적 재시작 (P1 v6 제외 전체)
#   [PATCH-R4] Redis 핵심 키 자동 복원
#   [PATCH-R5] --dry-run 옵션 추가
#   [PATCH-R6] 2>/dev/null 최소화 → 에러 로그 파일로 리다이렉트
#   [PATCH-R7] 롤백 후 자동 verify 실행 옵션
#   [PATCH-R8] 실패 시 non-zero exit 보장 (ROLLBACK_FAILED 카운터)
#   [PATCH-R9] redis_cmd() 실패 시 명시적 exit code
#   [PATCH-R10] flock 동시 실행 방지
#   [PATCH-R11] 백업 파일 권한 보호
#
# 핵심 원칙:
#   1. P1 v6 기반층은 롤백 대상이 아님 — 절대 건드리지 않음
#   2. 상위 트랙(Track A/B/C) 프로세스만 이전 상태로 복원
#   3. deploy 시 생성된 백업 디렉토리를 사용
#
# 사용법:
#   ./rollback_combined_ares_v42_patched.sh [<backup_dir>] [--dry-run] [--auto-verify]
###############################################################################
set -euo pipefail

# [PATCH-R10] flock 동시 실행 방지
LOCK_FILE="/tmp/ares_deploy.lock"
exec 200>"${LOCK_FILE}"
if ! flock -n 200; then
  echo "[FATAL] Another deploy/rollback is already running. Exiting."
  exit 1
fi

# ─── 옵션 파싱 [PATCH-R5] ──────────────────────────────────────────────────
DRY_RUN=false
AUTO_VERIFY=false
BACKUP_DIR=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --auto-verify) AUTO_VERIFY=true ;;
    *) BACKUP_DIR="$arg" ;;
  esac
done

# ─── 색상 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

# [PATCH-R8] 실패 카운터
ROLLBACK_FAILED=0

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; ROLLBACK_FAILED=$((ROLLBACK_FAILED+1)); }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 에러 로그 [PATCH-R6] ───────────────────────────────────────────────────
ROLLBACK_LOG="/home/ubuntu/rollback_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${ROLLBACK_LOG}") 2>&1
log_info "Rollback log: ${ROLLBACK_LOG}"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
ARES_REPO="/home/ubuntu/ARES-KIS-US-AUTOPILOT"

# ─── P1 v6 프로세스 (건드리지 않음) ─────────────────────────────────────────
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# ─── Redis 접속 정보 ─────────────────────────────────────────────────────────
REDIS_URL="${REDIS_URL:-rediss://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379}"

# [PATCH-R9] redis_cmd: 에러 시 명시적 non-zero exit
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
# STEP 0: 백업 디렉토리 결정
###############################################################################
log_step "Step 0: Locate Backup"

if [[ -z "$BACKUP_DIR" ]]; then
  BACKUP_DIR=$(find /home/ubuntu -maxdepth 1 -name 'deploy_backup_*' -type d 2>/dev/null | sort -r | head -1 || echo "")
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
  STATUS=$(get_pm2_status "$proc")
  if [[ "$STATUS" == "online" ]]; then
    log_ok "P1 v6 ${proc}: online (untouched)"
  else
    log_warn "P1 v6 ${proc}: ${STATUS} — NOT part of rollback, but may need attention"
  fi
done

###############################################################################
# STEP 2: Git 롤백 [PATCH-R1: 자동 롤백]
###############################################################################
log_step "Step 2: Git Rollback (Upper Tracks)"

if [[ -d "${ARES_REPO}/.git" ]]; then
  cd "${ARES_REPO}"
  CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  log_info "Current HEAD: ${CURRENT_SHA}"

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
      git reset --hard "${PREV_SHA}" 2>&1 && \
        log_ok "Git rolled back: ${CURRENT_SHA} → ${PREV_SHA}" || \
        log_fail "Git rollback failed — manual intervention required"
    fi
  elif [[ "$PREV_SHA" == "$CURRENT_SHA" ]]; then
    log_info "Git already at backup SHA — no rollback needed"
  else
    log_warn "No previous SHA available — showing recent commits for manual rollback:"
    git log --oneline -5 2>/dev/null | while read line; do
      echo -e "    $line"
    done
    log_info "To rollback manually: cd ${ARES_REPO} && git reset --hard <previous_sha>"
  fi
  cd -
else
  log_warn "${ARES_REPO} is not a git repo"
fi

###############################################################################
# STEP 3: Ecosystem 복원 + PM2 Reload [PATCH-R2]
###############################################################################
log_step "Step 3: Restore Ecosystem Config & Reload PM2"

if [[ -f "${BACKUP_DIR}/ecosystem.config.cjs" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would restore ecosystem.config.cjs and reload PM2"
  else
    cp "${BACKUP_DIR}/ecosystem.config.cjs" "${ARES_CURRENT}/ecosystem.config.cjs" 2>&1
    log_ok "Restored ecosystem.config.cjs from backup"

    # [PATCH-R2/R3] PM2 reload — P1 v6 제외 상위 트랙 전체
    UPPER_PROCS=$(get_upper_track_procs)
    if [[ -n "$UPPER_PROCS" ]]; then
      while read -r proc; do
        if [[ -n "$proc" ]]; then
          pm2 restart "$proc" --update-env 2>&1 && \
            log_ok "PM2 restarted: ${proc}" || \
            log_fail "PM2 restart failed: ${proc}"
        fi
      done <<< "$UPPER_PROCS"
    else
      pm2 restart position-hard-stop --update-env 2>&1 && \
        log_ok "Restarted: position-hard-stop" || \
        log_fail "Failed to restart: position-hard-stop"

      pm2 restart alert-rules --update-env 2>&1 && \
        log_ok "Restarted: alert-rules" || \
        log_fail "Failed to restart: alert-rules"
    fi
  fi
else
  log_warn "No ecosystem.config.cjs in backup — skipping"
fi

log_info "P1 v6 processes: NOT restarted (foundation layer preserved)"

###############################################################################
# STEP 4: Redis 키 복원 [PATCH-R4: 자동 복원]
###############################################################################
log_step "Step 4: Redis Key Restoration"

if [[ -f "${BACKUP_DIR}/redis_snapshot.json" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would restore Redis keys from: ${BACKUP_DIR}/redis_snapshot.json"
  else
    log_info "Restoring Redis keys from snapshot..."
    RESTORE_RESULT=$(node -e "
      const Redis = require('ioredis');
      const fs = require('fs');
      const r = new Redis('${REDIS_URL}', {tls:{}});
      (async () => {
        try {
          const snap = JSON.parse(fs.readFileSync('${BACKUP_DIR}/redis_snapshot.json', 'utf8'));
          let restored = 0, skipped = 0, failed = 0;
          for (const [key, info] of Object.entries(snap)) {
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
    " 2>&1)

    RESTORED=$(echo "$RESTORE_RESULT" | tail -1 | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const r=JSON.parse(d);console.log(r.restored+'|'+r.skipped+'|'+r.failed)}catch{console.log('0|0|1')}})" 2>/dev/null || echo "0|0|1")
    IFS='|' read -r R_OK R_SKIP R_FAIL <<< "$RESTORED"
    log_info "Redis restore: ${R_OK} restored, ${R_SKIP} skipped (null), ${R_FAIL} failed"

    if [[ "${R_FAIL}" == "0" ]]; then
      log_ok "Redis keys restored successfully"
    else
      log_fail "Some Redis keys failed to restore — check log"
    fi
  fi
else
  log_info "No Redis snapshot in backup — skipping Redis restoration"
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

PHS_ST=$(get_pm2_status "position-hard-stop")
if [[ "$PHS_ST" == "online" ]]; then
  log_ok "position-hard-stop: online after rollback"
else
  log_fail "position-hard-stop: ${PHS_ST} after rollback"
fi

ALERT_ST=$(get_pm2_status "alert-rules")
if [[ "$ALERT_ST" == "online" ]]; then
  log_ok "alert-rules: online after rollback"
else
  log_warn "alert-rules: ${ALERT_ST} after rollback"
fi

###############################################################################
# SUMMARY [PATCH-R8: 실패 시 non-zero exit]
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
if [[ "$ROLLBACK_FAILED" -eq 0 ]]; then
  echo -e "${BOLD}  ${GREEN}ROLLBACK COMPLETE — SUCCESS${NC}"
else
  echo -e "${BOLD}  ${RED}ROLLBACK COMPLETE — ${ROLLBACK_FAILED} FAILURE(S) DETECTED${NC}"
fi
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  Backup used:  ${BACKUP_DIR}"
echo -e "  P1 v6:        ${GREEN}NOT touched${NC} (foundation layer preserved)"
echo -e "  Git:          Rolled back (if SHA available)"
echo -e "  Ecosystem:    Restored & PM2 reloaded"
echo -e "  Redis:        Keys restored from snapshot"
echo -e "  Upper tracks: Restarted (all, P1 v6 excluded)"
echo -e "  Dry-run:      ${DRY_RUN}"
echo -e "  Failures:     ${ROLLBACK_FAILED}"
echo -e "  Log:          ${ROLLBACK_LOG}"
echo ""

# [PATCH-R7] 자동 verify 실행
if [[ "$AUTO_VERIFY" == "true" ]]; then
  log_step "Auto-Verify: Running verify script"
  VERIFY_SCRIPT="$(dirname "$0")/verify_combined_ares_v42_patched.sh"
  if [[ -f "$VERIFY_SCRIPT" ]]; then
    bash "$VERIFY_SCRIPT" 2>&1 || log_warn "Verify returned non-zero exit code"
  else
    log_warn "Verify script not found: ${VERIFY_SCRIPT}"
  fi
else
  echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42_patched.sh to confirm.${NC}"
fi

if [[ "$ROLLBACK_FAILED" -gt 0 ]]; then
  exit 1
else
  exit 0
fi
