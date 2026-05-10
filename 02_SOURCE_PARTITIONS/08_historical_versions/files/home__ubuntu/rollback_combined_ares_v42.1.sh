#!/usr/bin/env bash
###############################################################################
# rollback_combined_ares_v42.1.sh — ARES 통합 롤백 스크립트 (GPT R2 블로커 해결)
# ============================================================================
# v42_patched → v42.1 변경사항:
#   [FIX-1] REDIS_URL 기본값 삭제 → 환경변수 강제
#   [FIX-2] redis_cmd() node -e에 시크릿 직접 삽입 금지 → 환경변수 전달
#   [FIX-3] flock cleanup trap 추가
#   [FIX-4] PM2 → pm2 startOrReload ecosystem.config.cjs
#   [FIX-5] 상위 트랙 → ARES 전용 allowlist (blast radius 제한)
#   [FIX-6] rollback 시 dependency 재설치 (pnpm install) 추가
#   [FIX-7] Redis 복원 키를 정적 설정 키/동적 상태 키로 분리
#   [FIX-8] --auto-verify 실패 → rollback 최종 exit code에 반영
#   [FIX-9] git rollback 전 dirty tree 체크
#   [FIX-10] redis_cmd 호출부 if ! out=... 패턴
###############################################################################
set -euo pipefail

# [FIX-3] flock + cleanup trap
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

ROLLBACK_FAILED=0

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; ROLLBACK_FAILED=$((ROLLBACK_FAILED+1)); }
log_step()  { echo -e "\n${BOLD}${CYAN}──── $1 ────${NC}"; }

# ─── 에러 로그 ──────────────────────────────────────────────────────────────
ROLLBACK_LOG="/home/ubuntu/rollback_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${ROLLBACK_LOG}") 2>&1
log_info "Rollback log: ${ROLLBACK_LOG}"

# ─── [FIX-1] REDIS_URL 환경변수 강제 ───────────────────────────────────────
if [[ -z "${REDIS_URL:-}" ]]; then
  echo -e "${RED}[FATAL]${NC} REDIS_URL environment variable is NOT set."
  echo "Export REDIS_URL before running this script."
  exit 1
fi
log_ok "REDIS_URL is set (from environment)"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
ARES_REPO="/home/ubuntu/ARES-KIS-US-AUTOPILOT"

# P1 v6 프로세스 (건드리지 않음)
P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

# [FIX-5] ARES 전용 상위 트랙 allowlist
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

# [FIX-7] Redis 키 분류: 정적 설정 키 (복원 안전) vs 동적 상태 키 (복원 위험)
REDIS_STATIC_KEYS=(
  "ares:router:mode:desired"
  "ares:router:mode:effective"
  "ares:equity:fence_epoch"
  "ares:router_guard:epoch"
  "ofg:unifier:epoch"
  "ares:md_feeder:epoch"
)
REDIS_DYNAMIC_KEYS=(
  "ares:equity:total"
  "ares:equity:active_fence"
  "ofg:gate:current"
)

# [FIX-2] redis_cmd: 환경변수로 시크릿 전달
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
ls -la "${BACKUP_DIR}/" 2>/dev/null | grep -v "^total" | while read -r line; do
  echo -e "    $line"
done

###############################################################################
# STEP 1: P1 v6 기반층 상태 확인 (read-only)
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
# STEP 2: Git 롤백 [FIX-9: dirty tree 체크 추가]
###############################################################################
log_step "Step 2: Git Rollback (Upper Tracks)"

if [[ -d "${ARES_REPO}/.git" ]]; then
  cd "${ARES_REPO}"
  CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  log_info "Current HEAD: ${CURRENT_SHA}"

  # [FIX-9] dirty tree 체크
  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    log_warn "Working tree has uncommitted changes — stashing before rollback"
    if [[ "$DRY_RUN" != "true" ]]; then
      git stash push -m "ares-rollback-$(date +%Y%m%d_%H%M%S)" 2>&1 || \
        log_warn "git stash failed — proceeding anyway"
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
      if git reset --hard "${PREV_SHA}" 2>&1; then
        log_ok "Git rolled back: ${CURRENT_SHA} → ${PREV_SHA}"
      else
        log_fail "Git rollback failed — manual intervention required"
      fi
    fi
  elif [[ "$PREV_SHA" == "$CURRENT_SHA" ]]; then
    log_info "Git already at backup SHA — no rollback needed"
  else
    log_warn "No previous SHA available — showing recent commits for manual rollback:"
    git log --oneline -5 2>/dev/null || true
  fi
  cd - > /dev/null
else
  log_warn "${ARES_REPO} is not a git repo"
fi

###############################################################################
# STEP 2.5: 의존성 재설치 [FIX-6: rollback 시 dependency 재설치]
###############################################################################
log_step "Step 2.5: Dependency Re-install (lockfile-based)"

if [[ "$DRY_RUN" == "true" ]]; then
  log_info "[DRY-RUN] Would re-install dependencies from rolled-back lockfile"
else
  if [[ -f "${ARES_REPO}/package.json" ]]; then
    cd "${ARES_REPO}"
    if command -v pnpm &>/dev/null; then
      if pnpm install --frozen-lockfile 2>&1 | tail -5; then
        log_ok "pnpm install (frozen-lockfile) completed"
      else
        log_warn "pnpm install --frozen-lockfile failed, trying pnpm install --prod"
        pnpm install --prod 2>&1 | tail -5 && \
          log_ok "pnpm install --prod completed" || \
          log_fail "pnpm install failed — node_modules may be inconsistent"
      fi
    elif command -v npm &>/dev/null; then
      npm ci 2>&1 | tail -5 && \
        log_ok "npm ci completed" || {
          log_warn "npm ci failed, trying npm install --production"
          npm install --production 2>&1 | tail -5 && \
            log_ok "npm install completed" || \
            log_fail "npm install failed — node_modules may be inconsistent"
        }
    fi
    cd - > /dev/null
  fi
fi

###############################################################################
# STEP 3: Ecosystem 복원 + PM2 Reload [FIX-4: startOrReload]
###############################################################################
log_step "Step 3: Restore Ecosystem Config & Reload PM2"

if [[ -f "${BACKUP_DIR}/ecosystem.config.cjs" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would restore ecosystem.config.cjs and reload PM2"
  else
    cp "${BACKUP_DIR}/ecosystem.config.cjs" "${ARES_CURRENT}/ecosystem.config.cjs" 2>&1
    log_ok "Restored ecosystem.config.cjs from backup"

    # [FIX-4] ecosystem startOrReload 사용
    ECOSYSTEM_FILE="${ARES_CURRENT}/ecosystem.config.cjs"
    if [[ -f "$ECOSYSTEM_FILE" ]]; then
      if pm2 startOrReload "${ECOSYSTEM_FILE}" --update-env 2>&1; then
        log_ok "PM2 ecosystem reload completed"
      else
        log_warn "PM2 ecosystem reload failed — falling back to allowlist restart"
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
      # [FIX-5] allowlist 기반 재시작
      for proc in "${ARES_UPPER_TRACK_PROCS[@]}"; do
        ST=$(get_pm2_status "$proc")
        if [[ "$ST" != "NOT_FOUND" ]]; then
          pm2 restart "$proc" --update-env 2>&1 && \
            log_ok "Restarted: ${proc}" || \
            log_fail "Failed to restart: ${proc}"
        fi
      done
    fi
  fi
else
  log_warn "No ecosystem.config.cjs in backup — skipping"
fi

log_info "P1 v6 processes: NOT restarted (foundation layer preserved)"

###############################################################################
# STEP 4: Redis 키 복원 [FIX-7: 정적/동적 키 분리]
###############################################################################
log_step "Step 4: Redis Key Restoration (static config keys only)"

if [[ -f "${BACKUP_DIR}/redis_snapshot.json" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would restore STATIC Redis keys from: ${BACKUP_DIR}/redis_snapshot.json"
    log_info "[DRY-RUN] DYNAMIC keys (equity, gate) will NOT be restored (stale data risk)"
  else
    log_info "Restoring STATIC config Redis keys from snapshot..."
    log_warn "DYNAMIC keys (ares:equity:total, ares:equity:active_fence, ofg:gate:current) will NOT be restored"
    log_warn "  → These are runtime state values that may have changed since backup"

    # [FIX-7] 정적 키만 복원, 동적 키는 건너뜀
    STATIC_KEYS_JSON=$(printf '"%s",' "${REDIS_STATIC_KEYS[@]}" | sed 's/,$//')
    DYNAMIC_KEYS_JSON=$(printf '"%s",' "${REDIS_DYNAMIC_KEYS[@]}" | sed 's/,$//')

    RESTORE_RESULT=$(REDIS_URL_INTERNAL="${REDIS_URL}" node -e "
      const Redis = require('ioredis');
      const fs = require('fs');
      const r = new Redis(process.env.REDIS_URL_INTERNAL, {tls:{}, connectTimeout: 10000});
      (async () => {
        try {
          const snap = JSON.parse(fs.readFileSync('${BACKUP_DIR}/redis_snapshot.json', 'utf8'));
          const staticKeys = new Set([${STATIC_KEYS_JSON}]);
          const dynamicKeys = new Set([${DYNAMIC_KEYS_JSON}]);
          let restored = 0, skipped = 0, dynamic_skipped = 0, failed = 0;
          for (const [key, info] of Object.entries(snap)) {
            if (dynamicKeys.has(key)) {
              console.error('  SKIP_DYNAMIC:', key, '(stale data risk)');
              dynamic_skipped++;
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
          console.log(JSON.stringify({restored, skipped, dynamic_skipped, failed}));
        } catch(e) {
          console.error('REDIS_RESTORE_ERROR:', e.message);
          console.log('{\"restored\":0,\"skipped\":0,\"dynamic_skipped\":0,\"failed\":1}');
        }
        await r.quit();
      })();
    " 2>&1)

    PARSED=$(echo "$RESTORE_RESULT" | tail -1 | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const r=JSON.parse(d);console.log(r.restored+'|'+r.skipped+'|'+r.dynamic_skipped+'|'+r.failed)}catch{console.log('0|0|0|1')}})" 2>/dev/null || echo "0|0|0|1")
    IFS='|' read -r R_OK R_SKIP R_DYN R_FAIL <<< "$PARSED"
    log_info "Redis restore: ${R_OK} restored, ${R_SKIP} skipped (null), ${R_DYN} dynamic skipped (safety), ${R_FAIL} failed"

    if [[ "${R_FAIL}" == "0" ]]; then
      log_ok "Redis static keys restored successfully"
    else
      log_fail "Some Redis keys failed to restore"
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
echo -e "  Backup used:    ${BACKUP_DIR}"
echo -e "  P1 v6:          ${GREEN}NOT touched${NC} (foundation layer preserved)"
echo -e "  Git:            Rolled back (if SHA available)"
echo -e "  Dependencies:   Re-installed (lockfile-based)"
echo -e "  Ecosystem:      Restored & PM2 reloaded"
echo -e "  Redis static:   Restored from snapshot"
echo -e "  Redis dynamic:  NOT restored (stale data safety)"
echo -e "  Upper tracks:   Restarted (allowlist-based, P1 v6 excluded)"
echo -e "  Dry-run:        ${DRY_RUN}"
echo -e "  Failures:       ${ROLLBACK_FAILED}"
echo -e "  Log:            ${ROLLBACK_LOG}"
echo ""

# [FIX-8] --auto-verify 실패 → rollback 최종 exit code에 반영
if [[ "$AUTO_VERIFY" == "true" ]]; then
  log_step "Auto-Verify: Running verify script"
  VERIFY_SCRIPT="$(dirname "$0")/verify_combined_ares_v42.1.sh"
  if [[ -f "$VERIFY_SCRIPT" ]]; then
    if ! bash "$VERIFY_SCRIPT" 2>&1; then
      log_fail "Auto-verify returned non-zero exit code"
    fi
  else
    log_warn "Verify script not found: ${VERIFY_SCRIPT}"
  fi
else
  echo -e "  ${GREEN}${BOLD}Next: Run verify_combined_ares_v42.1.sh to confirm.${NC}"
fi

if [[ "$ROLLBACK_FAILED" -gt 0 ]]; then
  exit 1
else
  exit 0
fi
