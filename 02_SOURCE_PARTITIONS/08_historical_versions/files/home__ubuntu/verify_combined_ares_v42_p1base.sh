#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

pass() { printf '[VERIFY][PASS] %s\n' "$*"; }
warn() { printf '[VERIFY][WARN] %s\n' "$*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail_msg() { printf '[VERIFY][FAIL] %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 1; }; }

first_existing_dir() {
  local p
  for p in "$@"; do
    [[ -d "$p" ]] && { printf '%s\n' "$p"; return 0; }
  done
  return 1
}

first_existing_file() {
  local p
  for p in "$@"; do
    [[ -f "$p" ]] && { printf '%s\n' "$p"; return 0; }
  done
  return 1
}

pm2_status() {
  local app="$1"
  pm2 jlist | node -e '
    const fs = require("fs");
    const app = process.argv[1];
    const data = JSON.parse(fs.readFileSync(0, "utf8"));
    const hit = data.find(x => x.name === app);
    process.stdout.write(hit?.pm2_env?.status || "missing");
  ' "$app"
}

pm2_env_var() {
  local app="$1"
  local var="$2"
  pm2 jlist | node -e '
    const fs = require("fs");
    const app = process.argv[1];
    const key = process.argv[2];
    const data = JSON.parse(fs.readFileSync(0, "utf8"));
    const hit = data.find(x => x.name === app);
    const val = hit?.pm2_env?.env?.[key] ?? hit?.pm2_env?.[key] ?? "";
    process.stdout.write(String(val || ""));
  ' "$app" "$var"
}

check_file() {
  local path="$1"
  [[ -f "$path" ]] && pass "file exists: $path" || fail_msg "file missing: $path"
}

check_pm2_online() {
  local app="$1"
  local required="${2:-1}"
  if [[ -z "$app" ]]; then
    return 0
  fi
  local status
  status="$(pm2_status "$app")"
  if [[ "$status" == "online" ]]; then
    pass "PM2 app online: $app"
  elif [[ "$required" == "1" ]]; then
    fail_msg "PM2 app not online: $app (status=$status)"
  else
    warn "PM2 app not online/absent: $app (status=$status)"
  fi
}

check_pm2_any_online() {
  local apps_csv="$1"
  local required="${2:-0}"
  local IFS=','
  local apps=($apps_csv)
  local app status found="0"
  for app in "${apps[@]}"; do
    [[ -z "$app" ]] && continue
    status="$(pm2_status "$app")"
    if [[ "$status" == "online" ]]; then
      pass "PM2 app online: $app"
      found="1"
      break
    fi
  done
  if [[ "$found" == "0" ]]; then
    if [[ "$required" == "1" ]]; then
      fail_msg "none of PM2 apps are online: ${apps_csv}"
    else
      warn "none of PM2 apps are online: ${apps_csv}"
    fi
  fi
}

redis_probe_json() {
  local outfile="$1"
  (
    cd "$REPO_DIR"
    REDIS_CONN="$REDIS_CONN" VERIFY_SYMBOL="$VERIFY_SYMBOL" OFG_SESSION_RATE_KEY="$OFG_SESSION_RATE_KEY" node <<'NODE' > "$outfile"
const fs = require('fs');
const path = require('path');
const repoDir = process.cwd();
const conn = process.env.REDIS_CONN || '';
const verifySymbol = process.env.VERIFY_SYMBOL || '';
const ofgKey = process.env.OFG_SESSION_RATE_KEY || '';

function loadRedis() {
  try {
    const resolved = require.resolve('ioredis', { paths: [repoDir, process.cwd()] });
    return require(resolved);
  } catch (e) {
    console.error(`ioredis_resolve_failed:${e.message}`);
    process.exit(2);
  }
}

const Redis = loadRedis();

(async () => {
  const out = { ok: false, ping: false, keys: {}, parse: {}, symbol: {}, errors: [] };
  if (!conn) {
    out.errors.push('REDIS_CONN_EMPTY');
    console.log(JSON.stringify(out));
    return;
  }

  const redis = new Redis(conn, {
    tls: conn.startsWith('rediss://') ? { rejectUnauthorized: false } : undefined,
    maxRetriesPerRequest: 2,
    retryStrategy: () => null,
    lazyConnect: false,
  });

  try {
    const keys = [
      'ares:equity:health',
      'ares:smg:health',
      'ares:positions:promote_health',
      'ares:emergency:health',
      'ares:final_trade_gate',
      'ftg:heartbeat',
      'ares:guard:hard_stop:heartbeat',
      'ares:guard:hard_stop:reconcile',
      'ares:halt:active',
      'ares:guard:hard_stop:violations',
      'ares:invariant:halt',
    ];
    if (ofgKey) keys.push(ofgKey);
    if (verifySymbol) {
      keys.push(`ares:guard:force_sell:${verifySymbol}:state`);
      keys.push(`ares:guard:force_sell:${verifySymbol}:executed`);
      keys.push(`ares:guard:force_sell:${verifySymbol}:intent_ts`);
    }

    const uniqueKeys = [...new Set(keys)];
    const ping = await redis.ping();
    out.ping = ping === 'PONG';

    const values = await redis.mget(uniqueKeys);
    uniqueKeys.forEach((k, i) => { out.keys[k] = values[i]; });

    for (const field of ['ares:guard:hard_stop:heartbeat', 'ares:guard:hard_stop:violations', 'ares:guard:hard_stop:reconcile']) {
      const raw = out.keys[field];
      if (!raw) continue;
      try { out.parse[field] = JSON.parse(raw); }
      catch (e) { out.errors.push(`JSON_PARSE_FAIL:${field}:${e.message}`); }
    }

    if (verifySymbol) {
      out.symbol = {
        state: out.keys[`ares:guard:force_sell:${verifySymbol}:state`] || null,
        executed: out.keys[`ares:guard:force_sell:${verifySymbol}:executed`] || null,
        intent_ts: out.keys[`ares:guard:force_sell:${verifySymbol}:intent_ts`] || null,
      };
    }

    out.ok = true;
    console.log(JSON.stringify(out));
  } catch (e) {
    out.errors.push(`REDIS_PROBE_FAIL:${e.message}`);
    console.log(JSON.stringify(out));
    process.exitCode = 1;
  } finally {
    try { await redis.quit(); } catch {}
  }
})();
NODE
  )
}

json_get() {
  local file="$1"
  local expr="$2"
  node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const expr = process.argv[2];
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    let val;
    try {
      val = Function("data", `return (${expr});`)(data);
    } catch {
      val = undefined;
    }
    if (val === undefined || val === null) process.stdout.write("");
    else if (typeof val === "object") process.stdout.write(JSON.stringify(val));
    else process.stdout.write(String(val));
  ' "$file" "$expr"
}

WARN_COUNT=0
FAIL_COUNT=0

DEFAULT_REPO_DIR="$(first_existing_dir \
  /home/ubuntu/ARES-KIS-US-AUTOPILOT \
  /home/ubuntu/ares-trading-system \
  /home/ubuntu/aub-trading-system || true)"
REPO_DIR="${REPO_DIR:-$DEFAULT_REPO_DIR}"
DEFAULT_PHS_TARGET="$(first_existing_file \
  /home/ubuntu/ARES-KIS-US-AUTOPILOT/guards/position-hard-stop.mjs \
  /home/ubuntu/ares-trading-system/services/guardrails/position-hard-stop.mjs || true)"
PHS_TARGET="${PHS_TARGET:-$DEFAULT_PHS_TARGET}"
PM2_PHS_APP="${PM2_PHS_APP:-position-hard-stop}"
PM2_OFG_APP="${PM2_OFG_APP:-ofg-unifier-v6}"
PM2_OFG_APP_FALLBACKS="${PM2_OFG_APP_FALLBACKS:-$PM2_OFG_APP,ofg-unifier-v5,order-flow-governor}"
P1_V6_DIR="${P1_V6_DIR:-/home/ubuntu/patches/v6}"
P1_V6_BASELINE_APPS="${P1_V6_BASELINE_APPS:-eta-v6,router-guard-v6,ofg-unifier-v6,md-feeder-v6,smg-dashboard-v6}"
POST_TRADE_ANALYZER="${POST_TRADE_ANALYZER:-$(first_existing_file \
  "$REPO_DIR/ares_post_trade_analyzer.py" \
  /home/ubuntu/ares_post_trade_analyzer.py || true)}"
OFG_V2_PATH="${OFG_V2_PATH:-$(first_existing_file \
  "$REPO_DIR/order_flow_governor_v2.py" \
  /home/ubuntu/ares_work/order_flow_governor_v2.py || true)}"
REDIS_CONN="${REDIS_URL:-${ARES_REDIS_URL:-}}"
VERIFY_SYMBOL="${VERIFY_SYMBOL:-}"
CRON_MARKER="${CRON_MARKER:-# ARES_POST_TRADE_ANALYZER}"
OFG_SESSION_RATE_KEY="${OFG_SESSION_RATE_KEY:-}"
EXPECTED_OFG_SESSION_RATE="${EXPECTED_OFG_SESSION_RATE:-12000}"
POST_TRADE_DRY_RUN_CMD="${POST_TRADE_DRY_RUN_CMD:-}"

need_cmd pm2
need_cmd node
[[ -n "$REPO_DIR" ]] || fail_msg "REPO_DIR auto-detect failed"
[[ -n "$PHS_TARGET" ]] || fail_msg "PHS_TARGET auto-detect failed"

if [[ -z "$REDIS_CONN" ]]; then
  REDIS_CONN="$(pm2_env_var "$PM2_PHS_APP" REDIS_URL)"
fi
if [[ -z "$REDIS_CONN" ]]; then
  REDIS_CONN="$(pm2_env_var "$PM2_PHS_APP" ARES_REDIS_URL)"
fi
if [[ -z "$REDIS_CONN" ]]; then
  REDIS_CONN="$(pm2_env_var final-trade-gate REDIS_URL)"
fi
if [[ -z "$REDIS_CONN" ]]; then
  REDIS_CONN="$(pm2_env_var final-trade-gate ARES_REDIS_URL)"
fi
if [[ -n "$REDIS_CONN" ]]; then
  pass "REDIS_CONN detected"
else
  warn "REDIS_CONN not detected from env or PM2"
fi

check_file "$PHS_TARGET"
check_file "$POST_TRADE_ANALYZER"
check_file "$OFG_V2_PATH"

if grep -nE '@version 4\.2\.0-patch-v3-ai-verified|PATCH-v3-AI-VERIFIED' "$PHS_TARGET" >/dev/null 2>&1; then
  pass "PHS file contains v4.2 AI-verified markers"
else
  fail_msg "PHS file does not contain expected v4.2 AI-verified markers"
fi

if grep -n 'Position Hard-Stop v4\.1 starting' "$PHS_TARGET" >/dev/null 2>&1; then
  warn "PHS runtime banner still says v4.1 although file is v4.2 (cosmetic mismatch documented in report)"
fi

if grep -n 'reconcile_latency_ms' "$PHS_TARGET" >/dev/null 2>&1; then
  pass "PHS file contains reconcile_latency_ms"
else
  fail_msg "PHS file missing reconcile_latency_ms marker"
fi

check_pm2_online final-trade-gate 1
check_pm2_online halt-controller 1
check_pm2_online ares-invariant-checker 1
check_pm2_online "$PM2_PHS_APP" 1
check_pm2_online peak-safety-clamp 1
check_pm2_any_online "$PM2_OFG_APP_FALLBACKS" 0

# P1 v6 baseline
if [[ -d "$P1_V6_DIR" ]]; then
  pass "P1 v6 directory present: $P1_V6_DIR"
else
  warn "P1 v6 directory missing: $P1_V6_DIR (docs say deployed path is /home/ubuntu/patches/v6)"
fi

IFS=',' read -r -a _p1_apps <<< "$P1_V6_BASELINE_APPS"
for _app in "${_p1_apps[@]}"; do
  _app="$(printf '%s' "$_app" | xargs)"
  [[ -z "$_app" ]] && continue
  check_pm2_online "$_app" 1
done

cron_all="$(crontab -l 2>/dev/null || true)"
if grep -Fq "$CRON_MARKER" <<< "$cron_all"; then
  pass "post-trade cron marker present"
else
  fail_msg "post-trade cron marker missing"
fi

cron_analyzer_lines="$(printf '%s\n' "$cron_all" | grep -E 'ares_post_trade_analyzer\.py' || true)"
cron_count="$(printf '%s\n' "$cron_analyzer_lines" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$cron_count" -gt 1 ]]; then
  warn "multiple post-trade analyzer cron entries detected ($cron_count)"
fi
if grep -Fq '0 23 * * 1-5' <<< "$cron_analyzer_lines" && grep -Fq '0 23 * * *' <<< "$cron_analyzer_lines"; then
  warn "weekday and daily post-trade cron entries coexist; remove one to avoid duplicate runs"
fi

if [[ -n "$POST_TRADE_DRY_RUN_CMD" ]]; then
  if bash -lc "$POST_TRADE_DRY_RUN_CMD" >/tmp/ares_post_trade_dry_run.out 2>/tmp/ares_post_trade_dry_run.err; then
    pass "post-trade dry-run command succeeded"
  else
    fail_msg "post-trade dry-run command failed"
    sed 's/^/[VERIFY][DRYRUN][STDERR] /' /tmp/ares_post_trade_dry_run.err >&2 || true
  fi
else
  warn "POST_TRADE_DRY_RUN_CMD not set; dry-run skipped"
fi

if [[ -n "$REDIS_CONN" ]]; then
  PROBE_JSON="/tmp/ares_verify_probe.$$.json"
  if redis_probe_json "$PROBE_JSON"; then
    pass "Node ioredis probe executed"
  else
    fail_msg "Node ioredis probe execution failed"
  fi

  probe_ok="$(json_get "$PROBE_JSON" 'data.ok')"
  probe_ping="$(json_get "$PROBE_JSON" 'data.ping')"
  probe_errors="$(json_get "$PROBE_JSON" 'data.errors')"

  [[ "$probe_ok" == "true" ]] && pass "Redis probe JSON OK" || fail_msg "Redis probe JSON not OK"
  [[ "$probe_ping" == "true" ]] && pass "Redis ping OK via ioredis" || fail_msg "Redis ping failed via ioredis"
  [[ -n "$probe_errors" && "$probe_errors" != "[]" ]] && warn "Redis probe reported issues: $probe_errors"

  for key in \
    ares:equity:health \
    ares:smg:health \
    ares:positions:promote_health \
    ares:emergency:health \
    ares:final_trade_gate \
    ftg:heartbeat \
    ares:guard:hard_stop:heartbeat \
    ares:guard:hard_stop:reconcile \
    ares:halt:active; do
    val="$(json_get "$PROBE_JSON" "data.keys[${key@Q}]")"
    if [[ -n "$val" ]]; then
      pass "Redis key present: $key"
    else
      fail_msg "Redis key missing/empty: $key"
    fi
  done

  gate_val="$(json_get "$PROBE_JSON" 'data.keys["ares:final_trade_gate"]')"
  if [[ "$gate_val" == "OPEN" || "$gate_val" == "DEGRADED" || "$gate_val" == "LIQUIDATE_ONLY" || "$gate_val" == "HALT" ]]; then
    pass "FTG gate state readable: $gate_val"
  else
    fail_msg "unexpected FTG gate state: ${gate_val:-<empty>}"
  fi

  halt_val="$(json_get "$PROBE_JSON" 'data.keys["ares:halt:active"]')"
  if [[ "$halt_val" == "false" || "$halt_val" == "0" || "$halt_val" == "true" || "$halt_val" == "1" ]]; then
    pass "halt active state readable: $halt_val"
  else
    fail_msg "unexpected halt active state: ${halt_val:-<empty>}"
  fi

  hb_version="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"]?.version')"
  [[ "$hb_version" == "4.2" ]] && pass "hard-stop heartbeat version is 4.2" || warn "hard-stop heartbeat version expected 4.2 got '${hb_version:-<empty>}'"

  hb_checked="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"]?.checked')"
  [[ -n "$hb_checked" ]] && pass "hard-stop heartbeat checked count readable: $hb_checked" || warn "hard-stop heartbeat checked count missing"

  hb_violations="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"]?.violations')"
  [[ -n "$hb_violations" ]] && pass "hard-stop heartbeat violations readable: $hb_violations" || warn "hard-stop heartbeat violations missing"

  hb_mode="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"]?.mode')"
  [[ -n "$hb_mode" ]] && pass "hard-stop heartbeat mode readable: $hb_mode" || warn "hard-stop heartbeat mode missing"

  viol_version="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:violations"]?.version')"
  [[ "$viol_version" == "4.2" ]] && pass "hard-stop violations version is 4.2" || warn "hard-stop violations version expected 4.2 got '${viol_version:-<empty>}'"

  viol_count="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:violations"]?.violations?.length')"
  if [[ -n "$viol_count" ]]; then
    pass "hard-stop violations count readable: $viol_count"
  else
    warn "hard-stop violations count missing"
  fi

  rec_ts="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:reconcile"]?.ts')"
  [[ -n "$rec_ts" ]] && pass "reconcile timestamp readable: $rec_ts" || warn "reconcile timestamp missing"

  broker_count="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:reconcile"]?.broker_symbols?.length')"
  [[ -n "$broker_count" ]] && pass "reconcile broker symbol count readable: $broker_count" || warn "reconcile broker symbol count missing"

  invariant_halt="$(json_get "$PROBE_JSON" 'data.keys["ares:invariant:halt"]')"
  if [[ -z "$invariant_halt" || "$invariant_halt" == "null" ]]; then
    pass "invariant halt not active"
  else
    warn "invariant halt present: $invariant_halt"
  fi

  if [[ -n "$OFG_SESSION_RATE_KEY" ]]; then
    ofg_val="$(json_get "$PROBE_JSON" "data.keys[${OFG_SESSION_RATE_KEY@Q}]")"
    if [[ "$ofg_val" == "$EXPECTED_OFG_SESSION_RATE" ]]; then
      pass "OFG session rate key $OFG_SESSION_RATE_KEY == $EXPECTED_OFG_SESSION_RATE"
    else
      fail_msg "OFG session rate key $OFG_SESSION_RATE_KEY expected '$EXPECTED_OFG_SESSION_RATE' got '${ofg_val:-<empty>}'"
    fi
  else
    warn "OFG_SESSION_RATE_KEY not set; runtime OFG session-rate check skipped"
  fi

  if [[ -n "$VERIFY_SYMBOL" ]]; then
    val_state="$(json_get "$PROBE_JSON" 'data.symbol.state')"
    [[ -n "$val_state" ]] && pass "symbol state present for $VERIFY_SYMBOL: $val_state" || warn "symbol state absent for $VERIFY_SYMBOL (normal if no recent force-sell)"
    val_executed="$(json_get "$PROBE_JSON" 'data.symbol.executed')"
    [[ -n "$val_executed" ]] && pass "symbol executed present for $VERIFY_SYMBOL" || warn "symbol executed absent for $VERIFY_SYMBOL"
    val_intent="$(json_get "$PROBE_JSON" 'data.symbol.intent_ts')"
    [[ -n "$val_intent" ]] && pass "symbol intent_ts present for $VERIFY_SYMBOL" || warn "symbol intent_ts absent for $VERIFY_SYMBOL"
  fi
else
  warn "REDIS_CONN not set; Redis verification skipped"
fi

printf '[VERIFY] warnings=%d failures=%d\n' "$WARN_COUNT" "$FAIL_COUNT"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi
