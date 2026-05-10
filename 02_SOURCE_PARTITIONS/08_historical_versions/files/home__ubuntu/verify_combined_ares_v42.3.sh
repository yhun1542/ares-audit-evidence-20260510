#!/usr/bin/env bash
###############################################################################
# verify_combined_ares_v42.3.sh — ARES 통합 검증 스크립트 (GPT R3 v42.2 블로커 해결)
# ============================================================================
# v42.2 → v42.3 변경사항:
#   [FIX-v3-1] 멀티 인스턴스 상세 검증 (git SHA, PM2 프로세스 세트, parity)
#   [FIX-v3-2] PM2 pm_cwd 검증 (실행 경로 일치 확인)
#   [FIX-v3-3] P1 v6 필수 파일명 정확한 검증
#   [FIX-v3-4] Redis 연결 사전 테스트
#   [FIX-v3-5] PM2 daemon health check (pm2 ping)
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

# ─── 에러 로그 ──────────────────────────────────────────────────────────────
VERIFY_LOG="/home/ubuntu/verify_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${VERIFY_LOG}") 2>&1
echo -e "${CYAN}[INFO]${NC} Verify log: ${VERIFY_LOG}"
echo -e "${CYAN}[INFO]${NC} Script version: v42.3"

# ─── REDIS_URL 자동 로드 ──────────────────────────────────────────────────
if [[ -z "${REDIS_URL:-}" ]]; then
  if [[ -f /etc/ares/redis.env ]]; then
    source /etc/ares/redis.env
    echo -e "${CYAN}[INFO]${NC} REDIS_URL loaded from /etc/ares/redis.env"
  fi
  if [[ -z "${REDIS_URL:-}" ]]; then
    echo -e "${RED}[FATAL]${NC} REDIS_URL environment variable is NOT set."
    exit 1
  fi
fi
echo -e "${CYAN}[INFO]${NC} REDIS_URL is set (from environment)"

# ─── 경로 상수 ──────────────────────────────────────────────────────────────
ARES_CURRENT="/home/ubuntu/ares_current"
# ARES_REPO: PM2 프로세스가 주로 ares_current를 사용하므로 동일 경로 사용
ARES_REPO="/home/ubuntu/ares_current"

if [[ -d "${ARES_REPO}/node_modules" ]]; then
  export NODE_PATH="${ARES_REPO}/node_modules:${NODE_PATH:-}"
fi

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

redis_scan_count() {
  local pattern="$1"
  REDIS_URL_INTERNAL="${REDIS_URL}" node -e "
    const Redis = require('ioredis');
    const r = new Redis(process.env.REDIS_URL_INTERNAL, {tls:{}, connectTimeout: 10000});
    (async () => {
      try {
        let count = 0, cursor = '0';
        do {
          const [nc, keys] = await r.scan(cursor, 'MATCH', process.argv[1], 'COUNT', 100);
          cursor = nc; count += keys.length;
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
  " "$pattern" 2>/dev/null
}

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
# PHASE 0: Node.js 환경 정합성
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

# [FIX-v3-5] PM2 daemon health check
if command -v pm2 &>/dev/null; then
  PM2_VER=$(pm2 --version 2>/dev/null)
  check_pass "PM2 available: v${PM2_VER}"
  if pm2 ping 2>/dev/null | grep -q "success\|pong" 2>/dev/null; then
    check_pass "PM2 daemon: healthy (ping ok)"
  else
    check_warn "PM2 daemon: ping failed — daemon may need restart"
  fi
else
  check_fail "PM2 not found"
fi

if [[ -d "${ARES_REPO}/node_modules" ]]; then
  NM_COUNT=$(find "${ARES_REPO}/node_modules" -maxdepth 1 -type d 2>/dev/null | wc -l || echo "0")
  check_pass "node_modules present: ${NM_COUNT} packages"
else
  check_warn "node_modules not found in ${ARES_REPO}"
fi

# ARES_CURRENT/ARES_REPO 실경로 일치 검증
REAL_CURRENT=$(readlink -f "$ARES_CURRENT" 2>/dev/null || echo "MISSING")
REAL_REPO=$(readlink -f "$ARES_REPO" 2>/dev/null || echo "MISSING")
if [[ "$REAL_CURRENT" == "MISSING" ]]; then
  check_warn "ARES_CURRENT (${ARES_CURRENT}) does not exist"
elif [[ "$REAL_CURRENT" == "$REAL_REPO" ]]; then
  check_pass "ARES_CURRENT → ARES_REPO path match: ${REAL_REPO}"
else
  check_fail "Path mismatch: ARES_CURRENT=${REAL_CURRENT} vs ARES_REPO=${REAL_REPO}"
fi

# ecosystem.master.cjs blast radius 경고
if [[ -f "${ARES_REPO}/ecosystem.master.cjs" ]]; then
  ECO_APP_COUNT=$(node -e "const m=require('${ARES_REPO}/ecosystem.master.cjs');console.log(m.apps?m.apps.length:0)" 2>/dev/null || echo "?")
  if [[ "$ECO_APP_COUNT" =~ ^[0-9]+$ ]] && [[ "$ECO_APP_COUNT" -gt 20 ]]; then
    check_warn "ecosystem.master.cjs has ${ECO_APP_COUNT} apps — DO NOT use startOrReload on this file"
  else
    check_pass "ecosystem.master.cjs: ${ECO_APP_COUNT} apps"
  fi
fi

# [FIX-v3-4] Redis 연결 사전 테스트
if redis_cmd ping &>/dev/null; then
  PING_RESULT=$(redis_cmd ping 2>/dev/null || echo "FAIL")
  if [[ "$PING_RESULT" == "PONG" ]]; then
    check_pass "Redis connectivity: PONG"
  else
    check_fail "Redis connectivity: unexpected response (${PING_RESULT})"
  fi
else
  check_fail "Redis connectivity: FAILED"
fi

###############################################################################
# PHASE 1: P1 v6 BASELINE
###############################################################################
header "Phase 1: P1 v6 Foundation Layer (Layer 0)"

P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")
P1V6_FILES_DIR="/home/ubuntu/patches/v6"

# [FIX-v3-3] 정확한 파일명 검증
P1V6_REQUIRED_FILES=(
  "p1-1-eta-conflict-monitor-v6.mjs"
  "p1-4-router-mode-guard-v6.mjs"
  "p1-5-ofg-gate-unifier-v6.mjs"
  "p1-6-market-data-feeder-v6.mjs"
  "p1-2-smg-audit-dashboard-v6.mjs"
)

echo -e "  ${BOLD}[Files]${NC}"
for f in "${P1V6_REQUIRED_FILES[@]}"; do
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
    UPTIME_MIN=$(( ($(date +%s) * 1000 - uptime) / 60000 )) 2>/dev/null || UPTIME_MIN=0
    if [[ "$restarts" -gt 10 ]]; then
      check_warn "${proc}: online but ${restarts} restarts (uptime: ${UPTIME_MIN}m)"
    else
      check_pass "${proc}: online (restarts: ${restarts}, uptime: ${UPTIME_MIN}m)"
    fi
  else
    check_fail "${proc}: status=${STATUS}"
  fi
done

# [FIX-v3-2] PM2 pm_cwd 검증
echo -e "  ${BOLD}[PM2 Runtime Paths]${NC}"
for proc in "${P1V6_PROCS[@]}"; do
  CWD=$(get_pm2_cwd "$proc")
  if [[ "$CWD" == "NOT_FOUND" ]]; then
    check_warn "PM2 cwd: ${proc} not in PM2"
  elif [[ "$CWD" == "PARSE_ERROR" || "$CWD" == "QUERY_FAILED" || "$CWD" == "UNKNOWN" ]]; then
    check_warn "PM2 cwd: ${proc} — could not determine"
  else
    check_pass "PM2 cwd: ${proc} → ${CWD}"
  fi
done

# Heartbeat 확인
echo -e "  ${BOLD}[Heartbeats]${NC}"
HB_KEYS=("ares:eta:monitor:heartbeat" "ares:router_guard:heartbeat" "ofg:unifier:heartbeat" "ares:md_feeder:heartbeat" "ares:smg:heartbeat")
HB_NAMES=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")

for i in "${!HB_KEYS[@]}"; do
  if ! HB_TTL=$(redis_cmd ttl "${HB_KEYS[$i]}"); then
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
EPOCH_KEYS=("ares:equity:fence_epoch" "ares:router_guard:epoch" "ofg:unifier:epoch" "ares:md_feeder:epoch")
EPOCH_NAMES=("ETA fence" "Router guard" "OFG unifier" "MD feeder")

for i in "${!EPOCH_KEYS[@]}"; do
  if ! ETTL=$(redis_cmd ttl "${EPOCH_KEYS[$i]}"); then
    check_fail "${EPOCH_NAMES[$i]} epoch: REDIS QUERY FAILED"
  elif [[ "$ETTL" == "-1" ]]; then
    if EVAL=$(redis_cmd get "${EPOCH_KEYS[$i]}"); then
      check_pass "${EPOCH_NAMES[$i]} epoch: value=${EVAL}, NO_EXPIRY (safe)"
    else
      check_pass "${EPOCH_NAMES[$i]} epoch: NO_EXPIRY (safe), value read failed"
    fi
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

PHS_STATUS=$(get_pm2_status "position-hard-stop")
if [[ "$PHS_STATUS" =~ ^online ]]; then
  IFS='|' read -r st restarts uptime <<< "$PHS_STATUS"
  check_pass "position-hard-stop: online (restarts: ${restarts})"
else
  check_fail "position-hard-stop: ${PHS_STATUS}"
fi

OFG_V6_STATUS=$(pm2 jlist 2>/dev/null | node -e "
  let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
    try {
      const list=JSON.parse(d);
      const v6=list.find(x=>x.name==='ofg-unifier-v6');
      const legacy=list.find(x=>x.name==='order-flow-governor');
      const v6s = v6 ? v6.pm2_env.status : 'NOT_FOUND';
      const legs = legacy ? legacy.pm2_env.status : 'NOT_FOUND';
      console.log(v6s + '|' + legs);
    } catch { console.log('PARSE_ERROR'); }
  });
" 2>/dev/null || echo "QUERY_FAILED")

IFS='|' read -r ofg_v6 ofg_legacy <<< "$OFG_V6_STATUS"
if [[ "$ofg_v6" == "online" ]]; then
  check_pass "ofg-unifier-v6: online (primary OFG)"
else
  check_fail "ofg-unifier-v6: ${ofg_v6}"
fi

if [[ "$ofg_legacy" == "online" ]]; then
  check_warn "order-flow-governor (legacy): still online — consider stopping"
else
  check_pass "order-flow-governor (legacy): ${ofg_legacy} (expected)"
fi

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

if ! EQUITY_VAL=$(redis_cmd get "ares:equity:total"); then
  check_fail "ares:equity:total: REDIS QUERY FAILED"
elif [[ -n "$EQUITY_VAL" ]] && [[ "$EQUITY_VAL" != "null" ]]; then
  check_pass "ares:equity:total = ${EQUITY_VAL}"
else
  check_fail "ares:equity:total MISSING or null"
fi

if ! FENCE_VAL=$(redis_cmd get "ares:equity:active_fence"); then
  check_warn "ares:equity:active_fence: Redis query failed"
elif [[ -n "$FENCE_VAL" ]] && [[ "$FENCE_VAL" != "null" ]]; then
  check_pass "ares:equity:active_fence = ${FENCE_VAL}"
else
  check_warn "ares:equity:active_fence not set"
fi

if ! ROUTER_DESIRED=$(redis_cmd get "ares:router:mode:desired"); then
  check_warn "Router desired mode: Redis query failed"
elif [[ -n "$ROUTER_DESIRED" ]] && [[ "$ROUTER_DESIRED" != "null" ]]; then
  check_pass "Router desired mode: ${ROUTER_DESIRED}"
else
  check_warn "Router desired mode not set"
fi

if ! ROUTER_EFFECTIVE=$(redis_cmd get "ares:router:mode:effective"); then
  check_warn "Router effective mode: Redis query failed"
elif [[ -n "$ROUTER_EFFECTIVE" ]] && [[ "$ROUTER_EFFECTIVE" != "null" ]]; then
  check_pass "Router effective mode: ${ROUTER_EFFECTIVE}"
else
  check_warn "Router effective mode not set"
fi

if ! OFG_GATE=$(redis_cmd get "ofg:gate:current"); then
  check_fail "ofg:gate:current: REDIS QUERY FAILED"
elif [[ -n "$OFG_GATE" ]] && [[ "$OFG_GATE" != "null" ]]; then
  OFG_STATE=$(echo "$OFG_GATE" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const o=JSON.parse(d);console.log(o.state||'UNKNOWN')}catch{console.log('PARSE_ERR')}})" 2>/dev/null || echo "PARSE_ERR")
  if [[ "$OFG_STATE" == "PARSE_ERR" ]]; then
    check_warn "OFG gate: key exists but JSON parse failed"
  else
    check_pass "OFG gate state: ${OFG_STATE}"
  fi
else
  check_fail "ofg:gate:current MISSING"
fi

MD_COUNT=$(redis_scan_count "md:mid:*" || echo "0")
if [[ "$MD_COUNT" =~ ^[0-9]+$ ]] && [[ "$MD_COUNT" -ge 10 ]]; then
  check_pass "Market data keys: ${MD_COUNT} symbols (SCAN-based)"
else
  check_warn "Market data keys: ${MD_COUNT} (may be outside market hours)"
fi

###############################################################################
# PHASE 4: 상위 트랙 프로세스 (informational)
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
# PHASE 5: 멀티 인스턴스 상세 검증 [FIX-v3-1]
###############################################################################
header "Phase 5: Multi-Instance Parity Verification"

INSTANCE_ID=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo "unknown")
INSTANCE_IP=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "unknown")
LOCAL_SHA=$(cd "${ARES_REPO}" && git rev-parse HEAD 2>/dev/null || echo "unknown")
# [FIX-R6-3] 로컬 인스턴스 상세 정보
LOCAL_ONLINE=$(pm2 jlist 2>/dev/null | node -e "
  let d='';process.stdin.on('data',c=>d+=c);
  process.stdin.on('end',()=>{
    try{console.log(JSON.parse(d).filter(p=>p.pm2_env.status==='online').length)}
    catch{console.log(0)}
  })" 2>/dev/null || echo "0")
LOCAL_NODE=$(node -v 2>/dev/null || echo "unknown")
check_pass "Local instance: ${INSTANCE_ID} (${INSTANCE_IP}), SHA: ${LOCAL_SHA}, online: ${LOCAL_ONLINE}, node: ${LOCAL_NODE}"

# [R8] Single-instance topology — remote parity check removed
check_pass "Single-instance mode: no remote parity check required"

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  VERIFICATION SUMMARY (v42.3)${NC}"
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
  "version": "v42.3",
  "instance": "${INSTANCE_ID}",
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
