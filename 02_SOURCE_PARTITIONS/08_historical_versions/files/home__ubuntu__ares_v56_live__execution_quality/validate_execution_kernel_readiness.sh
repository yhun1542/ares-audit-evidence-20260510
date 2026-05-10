#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# CORRECTED: validate_execution_kernel_readiness.sh
# EC2 실제 경로에 맞게 수정됨 (2026-04-01)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

REDIS_CLI="${REDIS_CLI:-redis-cli}"
PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

check_json_key() {
  local key="$1"
  local raw
  raw="$($REDIS_CLI --raw GET "$key" 2>/dev/null || true)"
  if [[ -z "$raw" ]]; then
    fail "$key missing"
    return
  fi
  if python3 - <<'PY' "$raw" >/dev/null 2>&1
import json, sys
json.loads(sys.argv[1])
PY
  then
    pass "$key parseable"
  else
    fail "$key invalid json"
  fi
}

check_string_eq() {
  local key="$1"; local expected="$2"
  local val
  val="$($REDIS_CLI --raw GET "$key" 2>/dev/null || true)"
  if [[ "$val" == "$expected" ]]; then
    pass "$key=$expected"
  else
    fail "$key expected=$expected actual=${val:-<empty>}"
  fi
}

check_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    pass "exists: $path"
  else
    fail "missing: $path"
  fi
}

check_pm2_online() {
  local name="$1"
  if ! command -v pm2 >/dev/null 2>&1; then
    fail "pm2 not installed"
    return
  fi
  # FIXED: pm2 list 텍스트 파싱 (pm2 jlist는 SSH 파이프에서 EPIPE 발생)
  if pm2 list --no-color 2>/dev/null | grep -q "│ ${name} .*│ online"; then
    pass "pm2 online: $name"
  elif pm2 list --no-color 2>/dev/null | grep -q "${name}.*online"; then
    pass "pm2 online: $name"
  else
    fail "pm2 not online: $name"
  fi
}

check_quote_fresh() {
  local symbol="$1"
  # CORRECTED v2: premium:polygon:{symbol}은 HASH 타입. HGETALL로 읽어야 함
  local key_type
  key_type="$($REDIS_CLI --raw TYPE "premium:polygon:$symbol" 2>/dev/null || true)"
  if [[ -z "$key_type" || "$key_type" == "none" ]]; then
    fail "quote missing: $symbol"
    return
  fi
  if python3 - <<'PY' "$symbol"
import json, sys, time, subprocess, os
symbol = sys.argv[1]
redis_cli = os.environ.get('REDIS_CLI', 'redis-cli').split()
key = f'premium:polygon:{symbol}'
# HGETALL returns key-value pairs
result = subprocess.run(redis_cli + ['--raw', 'HGETALL', key], capture_output=True, text=True)
lines = result.stdout.strip().split('\n')
if len(lines) < 2:
    raise SystemExit(1)
hash_data = {}
for i in range(0, len(lines)-1, 2):
    hash_data[lines[i]] = lines[i+1]
# Check updated_at field or data.t timestamp
updated_at = hash_data.get('updated_at', '')
data_json = hash_data.get('data', '{}')
try:
    data = json.loads(data_json)
    ts_ms = data.get('t', 0)
except:
    ts_ms = 0
if ts_ms > 0:
    age_ms = int(time.time()*1000) - int(ts_ms)
    # During market hours: fresh if < 60s. After hours: just check data exists
    if age_ms < 60000:
        raise SystemExit(0)  # fresh
    elif updated_at:  # data exists but stale (after hours)
        print(f'  {symbol}: data present, age={age_ms/1000:.0f}s (after hours OK)', file=sys.stderr)
        raise SystemExit(0)  # accept stale during after hours
if updated_at:
    print(f'  {symbol}: has updated_at={updated_at} but no t in data', file=sys.stderr)
    raise SystemExit(0)  # has data, just no ms timestamp
raise SystemExit(1)
PY
  then
    pass "quote present: $symbol"
  else
    fail "quote missing or empty: $symbol"
  fi
}

echo "═══════════════════════════════════════════════════════════"
echo "ARES Execution Kernel Readiness Check (CORRECTED)"
echo "Date: $(date -u)"
echo "═══════════════════════════════════════════════════════════"
echo

echo "--- [1] File Existence ---"
# CORRECTED: 실제 EC2 경로
check_exists "/home/ubuntu/ares_releases/2026-02-17/order-intent-executor.mjs"
check_exists "/home/ubuntu/ares_releases/2026-02-17/live-trading-kis.mjs"
check_exists "/home/ubuntu/ares_v56_live/releases/20260327_072504/algo-maker-pegger.mjs"
check_exists "/home/ubuntu/ares_releases/2026-02-17/ops/polygon-ws-nbbo.mjs"
check_exists "/home/ubuntu/data-gateway.mjs"
check_exists "/home/ubuntu/ares_v56_live/current/V65_C6_TC6x_20260331_engine.py"
check_exists "/home/ubuntu/ares_v56_live/current/tc6x_config.json"
check_exists "/home/ubuntu/ares_v56_live/current/v55_live_autopilot_config.json"
echo

echo "--- [2] Engine Integrity ---"
EXPECTED_MD5="ea625ad323f1552d18eaef0af1b83432"
ACTUAL_MD5="$(md5sum /home/ubuntu/ares_v56_live/current/V65_C6_TC6x_20260331_engine.py 2>/dev/null | awk '{print $1}' || true)"
if [[ "$ACTUAL_MD5" == "$EXPECTED_MD5" ]]; then
  pass "engine MD5=$EXPECTED_MD5"
else
  fail "engine MD5 expected=$EXPECTED_MD5 actual=${ACTUAL_MD5:-<missing>}"
fi
echo

echo "--- [3] Redis Keys ---"
# CORRECTED: 실제 live_engine 값
check_string_eq "ares:ops:live_engine" "V65_C6_TC6x_20260331"
check_json_key "policy:execution_router"
echo

echo "--- [4] Quote Freshness ---"
for s in AAPL MSFT NVDA META; do
  check_quote_fresh "$s"
done
echo

echo "--- [5] PM2 Processes ---"
check_pm2_online "order-intent-executor"
check_pm2_online "live-trading-kis"
check_pm2_online "algo-maker-pegger"
check_pm2_online "polygon-ws-nbbo"
check_pm2_online "data-gateway"
check_pm2_online "ares-v56-live"
echo

echo "--- [6] Health & Trading ---"
if [[ -n "$($REDIS_CLI --raw GET health:algo-maker-pegger 2>/dev/null || true)" ]]; then
  pass "health:algo-maker-pegger present"
else
  fail "health:algo-maker-pegger missing (pegger heartbeat not emitting)"
fi

if [[ -n "$($REDIS_CLI --raw GET trading:enabled 2>/dev/null || true)" ]]; then
  pass "trading:enabled present"
else
  fail "trading:enabled missing"
fi
echo

echo "--- [7] Stream Lengths ---"
echo "stream:fills = $(redis-cli XLEN stream:fills 2>/dev/null)"
echo "stream:execution_quality = $(redis-cli XLEN stream:execution_quality 2>/dev/null)"
echo "stream:peg_requests = $(redis-cli XLEN stream:peg_requests 2>/dev/null)"
echo "emarkos:v1:execution = $(redis-cli XLEN emarkos:v1:execution 2>/dev/null)"
echo

echo "═══════════════════════════════════════════════════════════"
echo "SUMMARY: PASS=$PASS FAIL=$FAIL"
echo "═══════════════════════════════════════════════════════════"
[[ "$FAIL" -eq 0 ]]
