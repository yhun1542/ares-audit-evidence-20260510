#!/usr/bin/env bash
###############################################################################
# verify_combined_ares_v42_final.sh — ARES 통합 검증 스크립트
# ============================================================================
# Layer 구조:
#   Layer 0: P1 v6 foundation  (이미 배포된 기반 안전층)
#   Track B: v2.1 bundle       (시그널 오케스트레이터 등)
#   Track A: position-hard-stop v4.2
#   Track C: P3 post-trade / OFG
#
# 검증 순서:
#   Phase 0: nodefix (Node.js 환경 정합성)
#   Phase 1: P1 v6 baseline (기반 안전층 5개 프로세스)
#   Phase 2: 핵심 운영 프로세스 (position-hard-stop, OFG 등)
#   Phase 3: Redis 키 상태 (Epoch 키, 모드, 게이트 등)
#   Phase 4: 상위 트랙 프로세스 (alert-rules 등)
#
# 사용법:
#   chmod +x verify_combined_ares_v42_final.sh
#   ./verify_combined_ares_v42_final.sh
###############################################################################
set -uo pipefail

# ─── 색상 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

# ─── 카운터 ──────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; WARN=0; TOTAL=0

check_pass() { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); echo -e "  ${GREEN}[PASS]${NC} $1"; }
check_fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); echo -e "  ${RED}[FAIL]${NC} $1"; }
check_warn() { WARN=$((WARN+1)); TOTAL=$((TOTAL+1)); echo -e "  ${YELLOW}[WARN]${NC} $1"; }

header() { echo -e "\n${CYAN}${BOLD}═══ $1 ═══${NC}"; }

# ─── Redis 접속 정보 ─────────────────────────────────────────────────────────
REDIS_URL="${REDIS_URL:-rediss://:Q5L7MzWVBt1hjFKlUTmCXqlssss5-fwT36Nz1L1P10U@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379}"

# Redis CLI 래퍼 (ioredis 기반)
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
# PHASE 0: NODEFIX — Node.js 환경 정합성
###############################################################################
header "Phase 0: Node.js Environment (nodefix)"

# Node.js 버전
NODE_VER=$(node -e "console.log(process.version)" 2>/dev/null || echo "MISSING")
if [[ "$NODE_VER" =~ ^v(20|22)\. ]]; then
  check_pass "Node.js version: $NODE_VER"
else
  check_fail "Node.js version: $NODE_VER (expected v20.x or v22.x)"
fi

# npm/pnpm 존재
if command -v npm &>/dev/null; then
  check_pass "npm available: $(npm --version 2>/dev/null)"
else
  check_warn "npm not found"
fi

# ioredis 모듈 존재
if node -e "require('ioredis')" 2>/dev/null; then
  check_pass "ioredis module available"
else
  check_fail "ioredis module MISSING"
fi

# PM2 존재
if command -v pm2 &>/dev/null; then
  PM2_VER=$(pm2 --version 2>/dev/null)
  check_pass "PM2 available: v${PM2_VER}"
else
  check_fail "PM2 not found"
fi

###############################################################################
# PHASE 1: P1 v6 BASELINE — 기반 안전층 5개 프로세스
###############################################################################
header "Phase 1: P1 v6 Foundation Layer (Layer 0)"

P1V6_PROCS=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6" "smg-dashboard-v6")
P1V6_FILES_DIR="/home/ubuntu/patches/v6"

# 1a. 파일 존재 확인
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

# 1b. PM2 프로세스 상태 확인
echo -e "  ${BOLD}[PM2 Processes]${NC}"
for proc in "${P1V6_PROCS[@]}"; do
  STATUS=$(pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc}');
        if(!p) { console.log('NOT_FOUND'); return; }
        console.log(p.pm2_env.status + '|' + p.pm2_env.restart_time + '|' + p.pm2_env.pm_uptime);
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null)

  if [[ "$STATUS" == "NOT_FOUND" ]]; then
    check_fail "PM2 process NOT FOUND: ${proc}"
  elif [[ "$STATUS" == "PARSE_ERROR" ]]; then
    check_fail "PM2 parse error for: ${proc}"
  elif [[ "$STATUS" =~ ^online ]]; then
    IFS='|' read -r st restarts uptime <<< "$STATUS"
    UPTIME_MIN=$(( ($(date +%s) * 1000 - uptime) / 60000 ))
    if [[ "$restarts" -gt 10 ]]; then
      check_warn "${proc}: online but ${restarts} restarts (uptime: ${UPTIME_MIN}m)"
    else
      check_pass "${proc}: online (restarts: ${restarts}, uptime: ${UPTIME_MIN}m)"
    fi
  else
    check_fail "${proc}: status=${STATUS}"
  fi
done

# 1c. Heartbeat 확인 (각 v6 프로세스가 Redis에 heartbeat 쓰는지)
echo -e "  ${BOLD}[Heartbeats]${NC}"
HB_KEYS=(
  "ares:eta:monitor:heartbeat"
  "ares:router_guard:heartbeat"
  "ofg:unifier:heartbeat"
  "ares:md_feeder:heartbeat"
)
HB_NAMES=("eta-v6" "router-guard-v6" "ofg-unifier-v6" "md-feeder-v6")

for i in "${!HB_KEYS[@]}"; do
  HB_TTL=$(redis_cmd ttl "${HB_KEYS[$i]}")
  if [[ "$HB_TTL" =~ ^[0-9]+$ ]] && [[ "$HB_TTL" -gt 0 ]]; then
    check_pass "${HB_NAMES[$i]} heartbeat alive (TTL: ${HB_TTL}s)"
  elif [[ "$HB_TTL" == "-2" ]]; then
    check_fail "${HB_NAMES[$i]} heartbeat MISSING"
  else
    check_warn "${HB_NAMES[$i]} heartbeat TTL=${HB_TTL}"
  fi
done

# 1d. Epoch 키 지속성 확인 (TTL 없어야 함)
echo -e "  ${BOLD}[Epoch Key Persistence]${NC}"
EPOCH_KEYS=(
  "ares:equity:fence_epoch"
  "ares:router_guard:epoch"
  "ofg:unifier:epoch"
  "ares:md_feeder:epoch"
)
EPOCH_NAMES=("ETA fence" "Router guard" "OFG unifier" "MD feeder")

for i in "${!EPOCH_KEYS[@]}"; do
  ETTL=$(redis_cmd ttl "${EPOCH_KEYS[$i]}")
  if [[ "$ETTL" == "-1" ]]; then
    EVAL=$(redis_cmd get "${EPOCH_KEYS[$i]}")
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
PHS_STATUS=$(pm2 jlist 2>/dev/null | node -e "
  let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
    try {
      const list=JSON.parse(d);
      const p=list.find(x=>x.name==='position-hard-stop');
      if(!p) { console.log('NOT_FOUND'); return; }
      console.log(p.pm2_env.status + '|' + p.pm2_env.restart_time);
    } catch { console.log('PARSE_ERROR'); }
  });
" 2>/dev/null)

if [[ "$PHS_STATUS" =~ ^online ]]; then
  IFS='|' read -r st restarts <<< "$PHS_STATUS"
  check_pass "position-hard-stop: online (restarts: ${restarts})"
else
  check_fail "position-hard-stop: ${PHS_STATUS}"
fi

# OFG 프로세스 — ofg-unifier-v6 우선, order-flow-governor 레거시 확인
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
" 2>/dev/null)

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
ALERT_STATUS=$(pm2 jlist 2>/dev/null | node -e "
  let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
    try {
      const list=JSON.parse(d);
      const p=list.find(x=>x.name==='alert-rules');
      console.log(p ? p.pm2_env.status : 'NOT_FOUND');
    } catch { console.log('PARSE_ERROR'); }
  });
" 2>/dev/null)

if [[ "$ALERT_STATUS" == "online" ]]; then
  check_pass "alert-rules: online"
else
  check_warn "alert-rules: ${ALERT_STATUS}"
fi

###############################################################################
# PHASE 3: Redis 키 상태 검증
###############################################################################
header "Phase 3: Redis Key State Verification"

# Equity truth
EQUITY_VAL=$(redis_cmd get "ares:equity:total")
if [[ -n "$EQUITY_VAL" ]] && [[ "$EQUITY_VAL" != "null" ]]; then
  check_pass "ares:equity:total = ${EQUITY_VAL}"
else
  check_fail "ares:equity:total MISSING or null"
fi

# Active fence
FENCE_VAL=$(redis_cmd get "ares:equity:active_fence")
if [[ -n "$FENCE_VAL" ]] && [[ "$FENCE_VAL" != "null" ]]; then
  check_pass "ares:equity:active_fence = ${FENCE_VAL}"
else
  check_warn "ares:equity:active_fence not set"
fi

# Router mode
ROUTER_DESIRED=$(redis_cmd get "ares:router:mode:desired")
ROUTER_EFFECTIVE=$(redis_cmd get "ares:router:mode:effective")
ROUTER_VER_TTL=$(redis_cmd ttl "ares:router:mode:version")

if [[ -n "$ROUTER_DESIRED" ]] && [[ "$ROUTER_DESIRED" != "null" ]]; then
  check_pass "Router desired mode: ${ROUTER_DESIRED}"
else
  check_warn "Router desired mode not set"
fi

if [[ -n "$ROUTER_EFFECTIVE" ]] && [[ "$ROUTER_EFFECTIVE" != "null" ]]; then
  check_pass "Router effective mode: ${ROUTER_EFFECTIVE}"
else
  check_warn "Router effective mode not set"
fi

if [[ "$ROUTER_VER_TTL" =~ ^[0-9]+$ ]] && [[ "$ROUTER_VER_TTL" -gt 0 ]]; then
  check_warn "Router mode version has TTL=${ROUTER_VER_TTL}s (set by guard lease cycle)"
fi

# OFG gate
OFG_GATE=$(redis_cmd get "ofg:gate:current")
if [[ -n "$OFG_GATE" ]] && [[ "$OFG_GATE" != "null" ]]; then
  OFG_STATE=$(echo "$OFG_GATE" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{console.log(JSON.parse(d).state)}catch{console.log('PARSE_ERR')}})" 2>/dev/null)
  check_pass "OFG gate state: ${OFG_STATE}"
else
  check_fail "ofg:gate:current MISSING"
fi

# MD feeder — 심볼 수 확인
MD_COUNT=$(redis_cmd eval "return #redis.call('keys','md:mid:*')" 0 2>/dev/null || echo "0")
if [[ "$MD_COUNT" =~ ^[0-9]+$ ]] && [[ "$MD_COUNT" -gt 0 ]]; then
  check_pass "Market data keys: ${MD_COUNT} symbols"
else
  check_warn "Market data keys: ${MD_COUNT} (may be outside market hours)"
fi

###############################################################################
# PHASE 4: 상위 트랙 프로세스 (선택적)
###############################################################################
header "Phase 4: Upper Track Processes (informational)"

TRACK_PROCS=("ares-runtime-guard" "ares-self-healing-guard" "circuit-breaker-daemon" "data-freshness-enforcer" "ares-invariant-guard" "equity-calculator")
for proc in "${TRACK_PROCS[@]}"; do
  ST=$(pm2 jlist 2>/dev/null | node -e "
    let d=''; process.stdin.on('data',c=>d+=c); process.stdin.on('end',()=>{
      try {
        const list=JSON.parse(d);
        const p=list.find(x=>x.name==='${proc}');
        console.log(p ? p.pm2_env.status + '|' + p.pm2_env.restart_time : 'NOT_FOUND');
      } catch { console.log('PARSE_ERROR'); }
    });
  " 2>/dev/null)

  if [[ "$ST" =~ ^online ]]; then
    IFS='|' read -r s r <<< "$ST"
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
# SUMMARY
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
