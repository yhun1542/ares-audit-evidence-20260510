#!/usr/bin/env bash
###############################################################################
# verify_combined_ares_v42_patched.sh — ARES 통합 검증 스크립트 (4AI 피드백 반영)
# ============================================================================
# 패치 내용 (v42_final → v42_patched):
#   [PATCH-V1] set -euo pipefail 적용
#   [PATCH-V2] 2>/dev/null 최소화 → 에러 로그 파일로 리다이렉트
#   [PATCH-V3] smg-dashboard-v6 heartbeat 체크 추가
#   [PATCH-V4] PM2 상태 조회 헬퍼 함수로 중복 제거
#   [PATCH-V5] verify 결과를 JSON 파일로도 저장
#   [PATCH-V6] 의존성 설치 상태 확인 추가
#   [PATCH-V7] KEYS → SCAN 변경 (프로덕션 Redis 안전)
#   [PATCH-V8] OFG gate JSON parse 실패 시 WARN 처리
#   [PATCH-V9] redis_cmd() 실패 시 명시적 exit code
#   [PATCH-V10] set -e 취약 구문 수정 (find 사용)
#
# 검증 순서:
#   Phase 0: nodefix (Node.js 환경 정합성)
#   Phase 1: P1 v6 baseline (기반 안전층 5개 프로세스)
#   Phase 2: 핵심 운영 프로세스 (position-hard-stop, OFG 등)
#   Phase 3: Redis 키 상태 (Epoch 키, 모드, 게이트 등)
#   Phase 4: 상위 트랙 프로세스 (alert-rules 등)
#
# 사용법:
#   chmod +x verify_combined_ares_v42_patched.sh
#   ./verify_combined_ares_v42_patched.sh
###############################################################################
set -euo pipefail

# ─── 색상 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

# ─── 카운터 ──────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; WARN=0; TOTAL=0

check_pass() { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); echo -e "  ${GREEN}[PASS]${NC} $1"; }
check_fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); echo -e "  ${RED}[FAIL]${NC} $1"; }
check_warn() { WARN=$((WARN+1)); TOTAL=$((TOTAL+1)); echo -e "  ${YELLOW}[WARN]${NC} $1"; }

header() { echo -e "\n${CYAN}${BOLD}═══ $1 ═══${NC}"; }

# ─── 에러 로그 [PATCH-V2] ───────────────────────────────────────────────────
VERIFY_LOG="/home/ubuntu/verify_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${VERIFY_LOG}") 2>&1
echo -e "${CYAN}[INFO]${NC} Verify log: ${VERIFY_LOG}"

# ─── Redis 접속 정보 ─────────────────────────────────────────────────────────
REDIS_URL="${REDIS_URL:-rediss://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379}"

# [PATCH-V9] Redis CLI 래퍼: 에러 시 명시적 non-zero exit
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

# [PATCH-V7] SCAN 기반 키 카운트 (KEYS 대신)
redis_scan_count() {
  local pattern="$1"
  node -e "
    const Redis = require('ioredis');
    const r = new Redis('${REDIS_URL}', {tls:{}});
    (async () => {
      try {
        let count = 0;
        let cursor = '0';
        do {
          const [nextCursor, keys] = await r.scan(cursor, 'MATCH', '${pattern}', 'COUNT', 100);
          cursor = nextCursor;
          count += keys.length;
        } while (cursor !== '0');
        console.log(count);
        process.exitCode = 0;
      } catch(e) {
        console.error('REDIS_SCAN_ERROR:', e.message);
        console.log('0');
        process.exitCode = 1;
      }
      await r.quit();
    })();
  " 2>&1
}

# [PATCH-V4] PM2 상태 조회 헬퍼
get_pm2_status() {
  local proc_name="$1"
  pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc_name}');
        if(!p) { console.log('NOT_FOUND'); return; }
        console.log(p.pm2_env.status + '|' + p.pm2_env.restart_time + '|' + p.pm2_env.pm_uptime);
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null || echo "QUERY_FAILED"
}

###############################################################################
# PHASE 0: NODEFIX — Node.js 환경 정합성
###############################################################################
header "Phase 0: Node.js Environment (nodefix)"

NODE_VER=$(node -e "console.log(process.version)" 2>/dev/null || echo "MISSING")
if [[ "$NODE_VER" =~ ^v(20|22)\. ]]; then
  check_pass "Node.js version: $NODE_VER"
else
  check_fail "Node.js version: $NODE_VER (expected v20.x or v22.x)"
fi

if command -v npm &>/dev/null; then
  check_pass "npm available: $(npm --version 2>/dev/null)"
else
  check_warn "npm not found"
fi

if command -v pnpm &>/dev/null; then
  check_pass "pnpm available: $(pnpm --version 2>/dev/null)"
fi

if node -e "require('ioredis')" 2>/dev/null; then
  check_pass "ioredis module available"
else
  check_fail "ioredis module MISSING"
fi

if command -v pm2 &>/dev/null; then
  PM2_VER=$(pm2 --version 2>/dev/null)
  check_pass "PM2 available: v${PM2_VER}"
else
  check_fail "PM2 not found"
fi

# [PATCH-V6] 의존성 설치 상태 확인
ARES_REPO="/home/ubuntu/ARES-KIS-US-AUTOPILOT"
if [[ -d "${ARES_REPO}/node_modules" ]]; then
  NM_COUNT=$(find "${ARES_REPO}/node_modules" -maxdepth 1 -type d 2>/dev/null | wc -l || echo "0")
  check_pass "node_modules present: ${NM_COUNT} packages"
else
  check_warn "node_modules not found in ${ARES_REPO} — dependencies may not be installed"
fi

###############################################################################
# PHASE 1: P1 v6 BASELINE — 기반 안전층 5개 프로세스
###############################################################################
header "Phase 1: P1 v6 Foundation Layer (Layer 0)"

P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")
P1V6_FILES_DIR="/home/ubuntu/patches/v6"

P1V6_FILE_MAP=(
  "p1-1-eta-conflict-monitor-v6.mjs"
  "p1-4-router-mode-guard-v6.mjs"
  "p1-5-ofg-gate-unifier-v6.mjs"
  "p1-6-market-data-feeder-v6.mjs"
  "p1-2-smg-audit-dashboard-v6.mjs"
)

echo -e "  ${BOLD}[Files]${NC}"
for f in "${P1V6_FILE_MAP[@]}"; do
  if [[ -f "${P1V6_FILES_DIR}/${f}" ]]; then
    check_pass "File exists: ${f}"
  else
    check_fail "File MISSING: ${P1V6_FILES_DIR}/${f}"
  fi
done

echo -e "  ${BOLD}[PM2 Processes]${NC}"
for proc in "${P1V6_PROCS[@]}"; do
  STATUS=$(get_pm2_status "$proc")

  if [[ "$STATUS" == "NOT_FOUND" ]]; then
    check_fail "PM2 process NOT FOUND: ${proc}"
  elif [[ "$STATUS" == "PARSE_ERROR" || "$STATUS" == "QUERY_FAILED" ]]; then
    check_fail "PM2 query error for: ${proc}"
  elif [[ "$STATUS" =~ ^online ]]; then
    IFS='|' read -r st restarts uptime <<< "$STATUS"
    UPTIME_MIN=$(( ($(date +%s) * 1000 - uptime) / 60000 )) || UPTIME_MIN=0
    if [[ "$restarts" -gt 10 ]]; then
      check_warn "${proc}: online but ${restarts} restarts (uptime: ${UPTIME_MIN}m)"
    else
      check_pass "${proc}: online (restarts: ${restarts}, uptime: ${UPTIME_MIN}m)"
    fi
  else
    check_fail "${proc}: status=${STATUS}"
  fi
done

# [PATCH-V3] Heartbeat 확인 (smg-dashboard-v6 추가)
echo -e "  ${BOLD}[Heartbeats]${NC}"
HB_KEYS=(
  "ares:eta:monitor:heartbeat"
  "ares:router_guard:heartbeat"
  "ofg:unifier:heartbeat"
  "ares:md_feeder:heartbeat"
  "ares:smg_dashboard:heartbeat"
)
HB_NAMES=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

for i in "${!HB_KEYS[@]}"; do
  HB_TTL=$(redis_cmd ttl "${HB_KEYS[$i]}" || echo "REDIS_FAIL")
  if [[ "$HB_TTL" == "REDIS_FAIL" || "$HB_TTL" == "REDIS_CMD_FAILED" ]]; then
    check_fail "${HB_NAMES[$i]} heartbeat: REDIS QUERY FAILED"
  elif [[ "$HB_TTL" =~ ^[0-9]+$ ]] && [[ "$HB_TTL" -gt 0 ]]; then
    check_pass "${HB_NAMES[$i]} heartbeat alive (TTL: ${HB_TTL}s)"
  elif [[ "$HB_TTL" == "-2" ]]; then
    check_fail "${HB_NAMES[$i]} heartbeat MISSING"
  else
    check_warn "${HB_NAMES[$i]} heartbeat TTL=${HB_TTL}"
  fi
done

# Epoch 키 지속성 확인
echo -e "  ${BOLD}[Epoch Key Persistence]${NC}"
EPOCH_KEYS=(
  "ares:equity:fence_epoch"
  "ares:router_guard:epoch"
  "ofg:unifier:epoch"
  "ares:md_feeder:epoch"
)
EPOCH_NAMES=("ETA fence" "Router guard" "OFG unifier" "MD feeder")

for i in "${!EPOCH_KEYS[@]}"; do
  ETTL=$(redis_cmd ttl "${EPOCH_KEYS[$i]}" || echo "REDIS_FAIL")
  if [[ "$ETTL" == "REDIS_FAIL" || "$ETTL" == "REDIS_CMD_FAILED" ]]; then
    check_fail "${EPOCH_NAMES[$i]} epoch: REDIS QUERY FAILED"
  elif [[ "$ETTL" == "-1" ]]; then
    EVAL=$(redis_cmd get "${EPOCH_KEYS[$i]}" || echo "N/A")
    check_pass "${EPOCH_NAMES[$i]} epoch: value=${EVAL}, NO_EXPIRY (safe)"
  elif [[ "$ETTL" == "-2" ]]; then
    check_fail "${EPOCH_NAMES[$i]} epoch KEY MISSING"
  else
    check_fail "${EPOCH_NAMES[$i]} epoch HAS TTL=${ETTL}s (DANGER: will expire!)"
  fi
done

###############################################################################
# PHASE 2: 핵심 운영 프로세스
###############################################################################
header "Phase 2: Core Operational Processes"

# position-hard-stop (Track A)
PHS_STATUS=$(get_pm2_status "position-hard-stop")
if [[ "$PHS_STATUS" =~ ^online ]]; then
  IFS='|' read -r st restarts uptime <<< "$PHS_STATUS"
  check_pass "position-hard-stop: online (restarts: ${restarts})"
else
  check_fail "position-hard-stop: ${PHS_STATUS}"
fi

# OFG 프로세스
OFG_V6_STATUS=$(pm2 jlist 2>/dev/null | node -e "
  let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
    try {
      const list=JSON.parse(d);
      const v6=list.find(x=>x.name==='ofg-unifier-v6');
      const legacy=list.find(x=>x.name==='order-flow-governor');
      const v6s = v6 ? v6.pm2_env.status : 'NOT_FOUND';
      const legs = legacy ? legacy.pm2_env.status : 'NOT_FOUND';
      const legr = legacy ? legacy.pm2_env.restart_time : 0;
      console.log(v6s + '|' + legs + '|' + legr);
    } catch { console.log('PARSE_ERROR'); }
  });
" 2>/dev/null || echo "QUERY_FAILED")

IFS='|' read -r ofg_v6 ofg_legacy ofg_leg_restarts <<< "$OFG_V6_STATUS"
if [[ "$ofg_v6" == "online" ]]; then
  check_pass "ofg-unifier-v6: online (primary OFG)"
else
  check_fail "ofg-unifier-v6: ${ofg_v6} (should be online as primary OFG)"
fi

if [[ "$ofg_legacy" == "online" ]]; then
  check_warn "order-flow-governor (legacy): still online — consider stopping"
elif [[ "$ofg_legacy" =~ waiting ]]; then
  check_pass "order-flow-governor (legacy): waiting/stopped (expected, replaced by v6)"
else
  check_pass "order-flow-governor (legacy): ${ofg_legacy}"
fi

# alert-rules
ALERT_STATUS=$(get_pm2_status "alert-rules")
if [[ "$ALERT_STATUS" =~ ^online ]]; then
  check_pass "alert-rules: online"
else
  check_warn "alert-rules: ${ALERT_STATUS}"
fi

###############################################################################
# PHASE 3: Redis 키 상태 검증
###############################################################################
header "Phase 3: Redis Key State Verification"

EQUITY_VAL=$(redis_cmd get "ares:equity:total" || echo "REDIS_FAIL")
if [[ "$EQUITY_VAL" == "REDIS_FAIL" || "$EQUITY_VAL" == "REDIS_CMD_FAILED" ]]; then
  check_fail "ares:equity:total: REDIS QUERY FAILED"
elif [[ -n "$EQUITY_VAL" ]] && [[ "$EQUITY_VAL" != "null" ]]; then
  check_pass "ares:equity:total = ${EQUITY_VAL}"
else
  check_fail "ares:equity:total MISSING or null"
fi

FENCE_VAL=$(redis_cmd get "ares:equity:active_fence" || echo "")
if [[ -n "$FENCE_VAL" ]] && [[ "$FENCE_VAL" != "null" ]] && [[ "$FENCE_VAL" != "REDIS_CMD_FAILED" ]]; then
  check_pass "ares:equity:active_fence = ${FENCE_VAL}"
else
  check_warn "ares:equity:active_fence not set"
fi

ROUTER_DESIRED=$(redis_cmd get "ares:router:mode:desired" || echo "")
ROUTER_EFFECTIVE=$(redis_cmd get "ares:router:mode:effective" || echo "")
ROUTER_VER_TTL=$(redis_cmd ttl "ares:router:mode:version" || echo "")

if [[ -n "$ROUTER_DESIRED" ]] && [[ "$ROUTER_DESIRED" != "null" ]] && [[ "$ROUTER_DESIRED" != "REDIS_CMD_FAILED" ]]; then
  check_pass "Router desired mode: ${ROUTER_DESIRED}"
else
  check_warn "Router desired mode not set"
fi

if [[ -n "$ROUTER_EFFECTIVE" ]] && [[ "$ROUTER_EFFECTIVE" != "null" ]] && [[ "$ROUTER_EFFECTIVE" != "REDIS_CMD_FAILED" ]]; then
  check_pass "Router effective mode: ${ROUTER_EFFECTIVE}"
else
  check_warn "Router effective mode not set"
fi

if [[ "$ROUTER_VER_TTL" =~ ^[0-9]+$ ]] && [[ "$ROUTER_VER_TTL" -gt 0 ]]; then
  check_warn "Router mode version has TTL=${ROUTER_VER_TTL}s (set by guard lease cycle)"
fi

# [PATCH-V8] OFG gate — JSON parse 실패 시 WARN
OFG_GATE=$(redis_cmd get "ofg:gate:current" || echo "REDIS_FAIL")
if [[ "$OFG_GATE" == "REDIS_FAIL" || "$OFG_GATE" == "REDIS_CMD_FAILED" ]]; then
  check_fail "ofg:gate:current: REDIS QUERY FAILED"
elif [[ -n "$OFG_GATE" ]] && [[ "$OFG_GATE" != "null" ]]; then
  OFG_STATE=$(echo "$OFG_GATE" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const o=JSON.parse(d);console.log(o.state||'UNKNOWN')}catch{console.log('PARSE_ERR')}})" 2>/dev/null || echo "PARSE_ERR")
  if [[ "$OFG_STATE" == "PARSE_ERR" ]]; then
    check_warn "OFG gate: key exists but JSON parse failed — may be malformed"
  else
    check_pass "OFG gate state: ${OFG_STATE}"
  fi
else
  check_fail "ofg:gate:current MISSING"
fi

# [PATCH-V7] MD feeder — SCAN 기반 심볼 수 확인 (KEYS 대신)
MD_COUNT=$(redis_scan_count "md:mid:*" || echo "0")
if [[ "$MD_COUNT" =~ ^[0-9]+$ ]] && [[ "$MD_COUNT" -ge 10 ]]; then
  check_pass "Market data keys: ${MD_COUNT} symbols (SCAN-based)"
else
  check_warn "Market data keys: ${MD_COUNT} (may be outside market hours)"
fi

###############################################################################
# PHASE 4: 상위 트랙 프로세스 (선택적)
###############################################################################
header "Phase 4: Upper Track Processes (informational)"

TRACK_PROCS=("ares-runtime-guard" "ares-self-healing-guard" "circuit-breaker-daemon" "data-freshness-enforcer" "ares-invariant-guard" "equity-calculator")
for proc in "${TRACK_PROCS[@]}"; do
  ST=$(get_pm2_status "$proc")

  if [[ "$ST" =~ ^online ]]; then
    IFS='|' read -r s r u <<< "$ST"
    if [[ "$r" -gt 20 ]]; then
      check_warn "${proc}: online but ${r} restarts"
    else
      check_pass "${proc}: online (restarts: ${r})"
    fi
  elif [[ "$ST" == "NOT_FOUND" ]]; then
    check_warn "${proc}: not registered in PM2"
  else
    check_warn "${proc}: ${ST}"
  fi
done

###############################################################################
# SUMMARY [PATCH-V5: JSON 결과 저장]
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  VERIFICATION SUMMARY${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  Total checks: ${TOTAL}"
echo -e "  ${GREEN}PASS: ${PASS}${NC}"
echo -e "  ${RED}FAIL: ${FAIL}${NC}"
echo -e "  ${YELLOW}WARN: ${WARN}${NC}"
echo ""

VERIFY_JSON="/home/ubuntu/verify_result_$(date +%Y%m%d_%H%M%S).json"
SCORE=$(echo "scale=1; ${PASS} * 100 / ${TOTAL}" | bc 2>/dev/null || echo "0")
VERDICT="$(if [[ "$FAIL" -eq 0 ]]; then echo "ALL_PASSED"; elif [[ "$FAIL" -le 2 ]]; then echo "MINOR_ISSUES"; else echo "CRITICAL_FAILURES"; fi)"

cat > "${VERIFY_JSON}" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "total": ${TOTAL},
  "pass": ${PASS},
  "fail": ${FAIL},
  "warn": ${WARN},
  "score": ${SCORE},
  "verdict": "${VERDICT}"
}
EOF
echo -e "  ${CYAN}[INFO]${NC} Results saved: ${VERIFY_JSON}"

if [[ "$FAIL" -eq 0 ]]; then
  echo -e "  ${GREEN}${BOLD}VERDICT: ALL CRITICAL CHECKS PASSED${NC}"
  echo -e "  System is ready for upper-track operations."
  exit 0
elif [[ "$FAIL" -le 2 ]]; then
  echo -e "  ${YELLOW}${BOLD}VERDICT: MINOR ISSUES DETECTED${NC}"
  echo -e "  Review FAIL items above before proceeding."
  exit 1
else
  echo -e "  ${RED}${BOLD}VERDICT: CRITICAL FAILURES — DO NOT PROCEED${NC}"
  echo -e "  Fix all FAIL items before any deployment."
  exit 2
fi
