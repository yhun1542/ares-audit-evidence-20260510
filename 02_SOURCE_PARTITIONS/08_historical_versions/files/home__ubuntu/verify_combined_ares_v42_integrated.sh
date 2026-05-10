#!/usr/bin/env bash
# verify_combined_ares_v42_integrated.sh
# Unified verification: equity-calculator v3.6.5 + P1 v6 hardening + PHS v4.2 + core infra
# FIX-I1: trap cleanup, TLS cert validation, ioredis timeouts
# FIX-I2: safe json_get (no Function/eval), heartbeat freshness checks
# FIX-I3: pm2 jlist caching, cron duplicate → fail, STRICT_MODE flag
# FIX-I4: version constants at top, POST_TRADE_DRY_RUN_CMD sanitization
set -Eeuo pipefail
umask 022

# ── Configurable version constants ──────────────────────────────────────────
EXPECTED_EC_VERSION="${EXPECTED_EC_VERSION:-3.6.5}"
EXPECTED_PHS_VERSION="${EXPECTED_PHS_VERSION:-4.2}"
HEARTBEAT_STALE_SEC="${HEARTBEAT_STALE_SEC:-300}"  # 5 min default
STRICT_MODE="${STRICT_MODE:-0}"  # 1=treat v6 heartbeat absence as FAIL during trading hours

# ── Output helpers ──────────────────────────────────────────────────────────
pass() { printf '[VERIFY][PASS] %s\n' "$*"; }
warn() { printf '[VERIFY][WARN] %s\n' "$*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail_msg() { printf '[VERIFY][FAIL] %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 1; }; }

WARN_COUNT=0
FAIL_COUNT=0

# FIX-I1: Cleanup trap for temp files
_TMPFILES=()
cleanup() {
  for f in "${_TMPFILES[@]}"; do rm -f "$f"; done
}
trap cleanup EXIT

# ── Utility helpers ─────────────────────────────────────────────────────────
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

# FIX-I3: Cache pm2 jlist output to avoid repeated calls
_PM2_JLIST_CACHE=""
_pm2_jlist() {
  if [[ -z "$_PM2_JLIST_CACHE" ]]; then
    _PM2_JLIST_CACHE="$(pm2 jlist 2>/dev/null || echo '[]')"
  fi
  printf '%s' "$_PM2_JLIST_CACHE"
}

pm2_status() {
  local app="$1"
  _pm2_jlist | node -e '
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
  _pm2_jlist | node -e '
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
  local csv="$1"
  local required="${2:-1}"
  local app status
  IFS=',' read -r -a _apps <<< "$csv"
  for app in "${_apps[@]}"; do
    app="$(printf '%s' "$app" | xargs)"
    [[ -z "$app" ]] && continue
    status="$(pm2_status "$app")"
    if [[ "$status" == "online" ]]; then
      pass "PM2 fallback app online: $app"
      return 0
    fi
  done
  if [[ "$required" == "1" ]]; then
    fail_msg "No PM2 app online in fallback set: $csv"
  else
    warn "No PM2 app online in fallback set: $csv"
  fi
  return 1
}

# ── Redis probe (FIX-I1: TLS validation + timeouts) ────────────────────────
redis_probe_json() {
  local outfile="$1"
  node - "$outfile" "$REDIS_CONN" "$OFG_SESSION_RATE_KEY" "$VERIFY_SYMBOL" <<'NODE'
const fs = require('fs');
const outfile = process.argv[2];
const conn = process.argv[3] || '';
const ofgKey = process.argv[4] || '';
const verifySymbol = process.argv[5] || '';
function loadRedis() {
  try { return require('ioredis'); } catch (e) {
    console.error(`redis_require_failed:${e.message}`);
    process.exit(2);
  }
}
const Redis = loadRedis();
(async () => {
  const out = { ok: false, ping: false, keys: {}, parse: {}, symbol: {}, errors: [], ts_now: Date.now() };
  if (!conn) {
    out.errors.push('REDIS_CONN_EMPTY');
    fs.writeFileSync(outfile, JSON.stringify(out));
    return;
  }
  // FIX-I1: TLS cert validation enabled by default; set REDIS_TLS_REJECT_UNAUTHORIZED=0 only for self-signed
  const tlsOpts = conn.startsWith('rediss://') ? {
    rejectUnauthorized: process.env.REDIS_TLS_REJECT_UNAUTHORIZED !== '0'
  } : undefined;
  const redis = new Redis(conn, {
    tls: tlsOpts,
    maxRetriesPerRequest: 2,
    retryStrategy: () => null,
    connectTimeout: 10000,   // FIX-I1: 10s connection timeout
    commandTimeout: 15000,   // FIX-I1: 15s command timeout
    lazyConnect: false,
  });
  try {
    const keys = [
      // Core infra
      'ares:final_trade_gate',
      'ftg:heartbeat',
      'ares:guard:hard_stop:heartbeat',
      'ares:guard:hard_stop:reconcile',
      'ares:guard:hard_stop:violations',
      'ares:halt:active',
      'ares:invariant:halt',
      // equity-calculator v3.6.5 baseline
      'equity-calculator:heartbeat',
      'equity-calculator:heartbeat:ts',
      'ares:equity:total',
      'ares:equity:snapshot',
      'truth:equity:broker_total_usd',
      'truth:ts:broker_snapshot_ms',
      'ares:equity:health',
      // P1 v6 heartbeat migration targets
      'ares:eta:monitor:heartbeat',
      'ares:smg:dashboard:heartbeat',
      'ares:emergency:shadow:heartbeat',
      'ares:router_guard:heartbeat',
      'ares:epoch:watchdog:heartbeat',
      // P1 v6 epoch keys
      'ares:equity:fence_epoch',
      'ares:router_guard:epoch',
      'ofg:unifier:epoch',
      'ares:md_feeder:epoch',
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
    for (const field of [
      'ares:guard:hard_stop:heartbeat',
      'ares:guard:hard_stop:violations',
      'ares:guard:hard_stop:reconcile',
      'equity-calculator:heartbeat',
      'ares:equity:snapshot'
    ]) {
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
    fs.writeFileSync(outfile, JSON.stringify(out));
  } catch (e) {
    out.errors.push(`REDIS_PROBE_FAIL:${e.message}`);
    fs.writeFileSync(outfile, JSON.stringify(out));
    process.exitCode = 1;
  } finally {
    try { await redis.quit(); } catch {}
  }
})();
NODE
}

# FIX-I2: Safe json_get — uses property path traversal instead of Function()/eval
json_get() {
  local file="$1"
  local expr="$2"
  node -e '
    const fs = require("fs");
    const file = process.argv[1];
    const expr = process.argv[2];
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    // Safe property traversal: supports data.x.y.z, data.x["key"], data.x?.y
    function safeGet(obj, path) {
      // Remove leading "data." or "data"
      path = path.replace(/^data\.?/, "");
      if (!path) return obj;
      const tokens = [];
      // Tokenize: handle dot notation and bracket notation
      const re = /(?:\["([^"]+)"\]|\['\''([^'\'']+)'\''\]|\.?([a-zA-Z_$][\w$]*)|\?\.([a-zA-Z_$][\w$]*))/g;
      let m;
      while ((m = re.exec(path)) !== null) {
        tokens.push(m[1] || m[2] || m[3] || m[4] || "");
      }
      let cur = obj;
      for (const t of tokens) {
        if (cur == null) return undefined;
        cur = cur[t];
      }
      return cur;
    }
    let val;
    try { val = safeGet(data, expr); } catch { val = undefined; }
    if (val === undefined || val === null) process.stdout.write("");
    else if (typeof val === "object") process.stdout.write(JSON.stringify(val));
    else process.stdout.write(String(val));
  ' "$file" "$expr"
}

# FIX-I2: Heartbeat freshness checker
check_heartbeat_freshness() {
  local label="$1"
  local ts_value="$2"
  local stale_sec="${3:-$HEARTBEAT_STALE_SEC}"
  if [[ -z "$ts_value" ]]; then
    warn "$label: timestamp unavailable for freshness check"
    return
  fi
  local now_ms ts_ms age_sec
  now_ms="$(date +%s%3N 2>/dev/null || date +%s)000"
  # Handle both epoch-ms and ISO strings
  if [[ "$ts_value" =~ ^[0-9]+$ ]]; then
    ts_ms="$ts_value"
  else
    # Try to parse ISO date
    ts_ms="$(node -e "process.stdout.write(String(new Date('$ts_value').getTime()))" 2>/dev/null || echo "0")"
  fi
  if [[ "$ts_ms" == "0" || "$ts_ms" == "NaN" ]]; then
    warn "$label: could not parse timestamp '$ts_value'"
    return
  fi
  age_sec=$(( (${now_ms%???} - ${ts_ms%???}) ))
  if (( age_sec < 0 )); then age_sec=$(( -age_sec )); fi
  if (( age_sec > stale_sec )); then
    warn "$label: stale (age=${age_sec}s > ${stale_sec}s threshold)"
  else
    pass "$label: fresh (age=${age_sec}s)"
  fi
}

# ── Configuration ───────────────────────────────────────────────────────────
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
PM2_EQ_APP="${PM2_EQ_APP:-equity-calculator}"
PM2_EQ_APP_FALLBACKS="${PM2_EQ_APP_FALLBACKS:-$PM2_EQ_APP}"
P1_V6_DIR="${P1_V6_DIR:-/home/ubuntu/patches/v6}"
P1_V6_BASELINE_APPS="${P1_V6_BASELINE_APPS:-eta-v6,router-guard-v6,ofg-unifier-v6,md-feeder-v6,smg-dashboard-v6,epoch-guardian-v6,alert-subscriber-v6}"
P1_V6_CLI_TOOL="${P1_V6_CLI_TOOL:-$P1_V6_DIR/p1-8-override-cli.mjs}"
POST_TRADE_ANALYZER="${POST_TRADE_ANALYZER:-$(first_existing_file "$REPO_DIR/ares_post_trade_analyzer.py" /home/ubuntu/ares_post_trade_analyzer.py || true)}"
OFG_V2_PATH="${OFG_V2_PATH:-$(first_existing_file "$REPO_DIR/order_flow_governor_v2.py" /home/ubuntu/ares_work/order_flow_governor_v2.py || true)}"
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

# ── File & marker checks ───────────────────────────────────────────────────
check_file "$PHS_TARGET"
check_file "$POST_TRADE_ANALYZER"
check_file "$OFG_V2_PATH"

if grep -nE '@version 4\.2\.0-patch-v3-ai-verified|PATCH-v3-AI-VERIFIED' "$PHS_TARGET" >/dev/null 2>&1; then
  pass "PHS file contains v4.2 AI-verified markers"
else
  fail_msg "PHS file does not contain expected v4.2 AI-verified markers"
fi

if grep -n 'Position Hard-Stop v4\.1 starting' "$PHS_TARGET" >/dev/null 2>&1; then
  warn "PHS runtime banner still says v4.1 although file is v4.2 (cosmetic mismatch documented)"
fi

if grep -n 'reconcile_latency_ms' "$PHS_TARGET" >/dev/null 2>&1; then
  pass "PHS file contains reconcile_latency_ms"
else
  fail_msg "PHS file missing reconcile_latency_ms marker"
fi

if grep -n "emarkos:v1:positions:normalized" "$PHS_TARGET" >/dev/null 2>&1; then
  pass "PHS file uses emarkos:v1:positions:normalized"
else
  fail_msg "PHS file missing emarkos:v1:positions:normalized hotfix"
fi

# ── PM2 process checks ─────────────────────────────────────────────────────
check_pm2_online final-trade-gate 1
check_pm2_online halt-controller 1
check_pm2_online ares-invariant-checker 1
check_pm2_online "$PM2_PHS_APP" 1
check_pm2_online peak-safety-clamp 1
check_pm2_any_online "$PM2_OFG_APP_FALLBACKS" 1
check_pm2_any_online "$PM2_EQ_APP_FALLBACKS" 0

# ── P1 v6 baseline checks ──────────────────────────────────────────────────
if [[ -d "$P1_V6_DIR" ]]; then
  pass "P1 v6 directory present: $P1_V6_DIR"
else
  warn "P1 v6 directory missing: $P1_V6_DIR"
fi

IFS=',' read -r -a _p1_apps <<< "$P1_V6_BASELINE_APPS"
for _app in "${_p1_apps[@]}"; do
  _app="$(printf '%s' "$_app" | xargs)"
  [[ -z "$_app" ]] && continue
  check_pm2_online "$_app" 1
done

if [[ -f "$P1_V6_CLI_TOOL" ]]; then
  pass "P1 v6 CLI tool present: $P1_V6_CLI_TOOL"
else
  warn "P1 v6 CLI tool missing: $P1_V6_CLI_TOOL"
fi

# ── Cron checks ─────────────────────────────────────────────────────────────
cron_all="$(crontab -l 2>/dev/null || true)"
if grep -Fq "$CRON_MARKER" <<< "$cron_all"; then
  pass "post-trade cron marker present"
else
  fail_msg "post-trade cron marker missing"
fi
cron_analyzer_lines="$(printf '%s\n' "$cron_all" | grep -E 'ares_post_trade_analyzer\.py' || true)"
cron_count="$(printf '%s\n' "$cron_analyzer_lines" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$cron_count" -gt 1 ]]; then
  # FIX-I3: Multiple cron entries can cause duplicate runs — escalate to FAIL
  fail_msg "multiple post-trade analyzer cron entries detected ($cron_count) — risk of duplicate execution"
fi
if grep -Fq '0 23 * * 1-5' <<< "$cron_analyzer_lines" && grep -Fq '0 23 * * *' <<< "$cron_analyzer_lines"; then
  fail_msg "weekday and daily post-trade cron entries coexist; remove one to avoid duplicate runs"
fi

# FIX-I4: POST_TRADE_DRY_RUN_CMD sanitization — only allow if it looks safe
if [[ -n "$POST_TRADE_DRY_RUN_CMD" ]]; then
  # FIX-I5: Block shell metacharacters more comprehensively
    if [[ "$POST_TRADE_DRY_RUN_CMD" =~ [\;\|\&\`\$\(\)\<\>\{\}\!] ]]; then
    fail_msg "POST_TRADE_DRY_RUN_CMD contains suspicious characters; skipping for safety"
  else
    _dry_out="/tmp/ares_post_trade_dry_run.$$.out"
    _dry_err="/tmp/ares_post_trade_dry_run.$$.err"
    _TMPFILES+=("$_dry_out" "$_dry_err")
    if bash -lc "$POST_TRADE_DRY_RUN_CMD" >"$_dry_out" 2>"$_dry_err"; then
      pass "post-trade dry-run command succeeded"
    else
      fail_msg "post-trade dry-run command failed"
      sed 's/^/[VERIFY][DRYRUN][STDERR] /' "$_dry_err" >&2 || true
    fi
  fi
else
  warn "POST_TRADE_DRY_RUN_CMD not set; dry-run skipped"
fi

# ── Redis verification ──────────────────────────────────────────────────────
if [[ -n "$REDIS_CONN" ]]; then
  PROBE_JSON="/tmp/ares_verify_probe.$$.json"
  _TMPFILES+=("$PROBE_JSON")
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

  # P1 v6 heartbeat migration targets
  for key in \
    ares:eta:monitor:heartbeat \
    ares:smg:dashboard:heartbeat \
    ares:emergency:shadow:heartbeat \
    ares:router_guard:heartbeat; do
    val="$(json_get "$PROBE_JSON" "data.keys[\"${key}\"]")"
    if [[ -n "$val" ]]; then
      pass "Redis key present (v6 heartbeat): $key"
    else
      if [[ "$STRICT_MODE" == "1" ]]; then
        fail_msg "Redis key missing (v6 heartbeat, STRICT_MODE): $key"
      else
        warn "Redis key missing/empty (v6 heartbeat): $key (may be absent outside trading hours)"
      fi
    fi
  done

  # P1 v6 epoch keys — must always exist
  for key in \
    ares:equity:fence_epoch \
    ares:router_guard:epoch \
    ofg:unifier:epoch \
    ares:md_feeder:epoch; do
    val="$(json_get "$PROBE_JSON" "data.keys[\"${key}\"]")"
    if [[ -n "$val" ]]; then
      pass "Redis key present (v6 epoch): $key=$val"
    else
      fail_msg "Redis key missing/empty (v6 epoch): $key"
    fi
  done

  wdog_val="$(json_get "$PROBE_JSON" 'data.keys["ares:epoch:watchdog:heartbeat"]')"
  if [[ -n "$wdog_val" ]]; then
    pass "Epoch guardian watchdog heartbeat present: $wdog_val"
  else
    # FIX-I5: In STRICT_MODE, watchdog heartbeat is also critical
    if [[ "$STRICT_MODE" == "1" ]]; then
      fail_msg "Epoch guardian watchdog heartbeat missing (STRICT_MODE)"
    else
      warn "Epoch guardian watchdog heartbeat missing (epoch-guardian-v6 may not have run yet)"
    fi
  fi

  # Core infrastructure keys
  for key in \
    ares:final_trade_gate \
    ftg:heartbeat \
    ares:guard:hard_stop:heartbeat \
    ares:guard:hard_stop:reconcile \
    ares:halt:active \
    equity-calculator:heartbeat \
    equity-calculator:heartbeat:ts \
    ares:equity:total \
    ares:equity:snapshot; do
    val="$(json_get "$PROBE_JSON" "data.keys[\"${key}\"]")"
    if [[ -n "$val" ]]; then
      pass "Redis key present: $key"
    else
      fail_msg "Redis key missing/empty: $key"
    fi
  done

  # Optional truth keys
  for key in \
    truth:equity:broker_total_usd \
    truth:ts:broker_snapshot_ms; do
    val="$(json_get "$PROBE_JSON" "data.keys[\"${key}\"]")"
    if [[ -n "$val" ]]; then
      pass "optional Redis key present: $key"
    else
      warn "optional Redis key absent: $key"
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

  # equity-calculator heartbeat deep checks
  ec_hb_version="$(json_get "$PROBE_JSON" 'data.parse["equity-calculator:heartbeat"].version')"
  [[ "$ec_hb_version" == "$EXPECTED_EC_VERSION" ]] && pass "equity-calculator heartbeat version: $EXPECTED_EC_VERSION" || warn "equity-calculator heartbeat version expected $EXPECTED_EC_VERSION got '${ec_hb_version:-<empty>}'"

  ec_hb_tier="$(json_get "$PROBE_JSON" 'data.parse["equity-calculator:heartbeat"].tier')"
  [[ -n "$ec_hb_tier" ]] && pass "equity-calculator heartbeat tier: $ec_hb_tier" || warn "equity-calculator heartbeat tier missing"

  ec_hb_total="$(json_get "$PROBE_JSON" 'data.parse["equity-calculator:heartbeat"].total')"
  [[ -n "$ec_hb_total" ]] && pass "equity-calculator heartbeat total: $ec_hb_total" || warn "equity-calculator heartbeat total missing"

  ec_hb_redis="$(json_get "$PROBE_JSON" 'data.parse["equity-calculator:heartbeat"].redis_healthy')"
  [[ "$ec_hb_redis" == "true" ]] && pass "equity-calculator heartbeat redis_healthy=true" || warn "equity-calculator heartbeat redis_healthy expected true got '${ec_hb_redis:-<empty>}'"

  # FIX-I2: Heartbeat freshness checks
  ec_hb_ts="$(json_get "$PROBE_JSON" 'data.keys["equity-calculator:heartbeat:ts"]')"
  check_heartbeat_freshness "equity-calculator:heartbeat:ts" "$ec_hb_ts"

  ftg_hb="$(json_get "$PROBE_JSON" 'data.keys["ftg:heartbeat"]')"
  check_heartbeat_freshness "ftg:heartbeat" "$ftg_hb"

  eq_total="$(json_get "$PROBE_JSON" 'data.keys["ares:equity:total"]')"
  [[ -n "$eq_total" ]] && pass "ares:equity:total readable: $eq_total" || fail_msg "ares:equity:total unreadable"

  eq_schema="$(json_get "$PROBE_JSON" 'data.parse["ares:equity:snapshot"].schema_version')"
  [[ "$eq_schema" == "$EXPECTED_EC_VERSION" ]] && pass "ares:equity:snapshot schema_version: $EXPECTED_EC_VERSION" || warn "ares:equity:snapshot schema_version expected $EXPECTED_EC_VERSION got '${eq_schema:-<empty>}'"

  hb_version="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"].version')"
  [[ "$hb_version" == "$EXPECTED_PHS_VERSION" ]] && pass "hard-stop heartbeat version: $EXPECTED_PHS_VERSION" || warn "hard-stop heartbeat version expected $EXPECTED_PHS_VERSION got '${hb_version:-<empty>}'"

  hb_checked="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"].checked')"
  [[ -n "$hb_checked" ]] && pass "hard-stop heartbeat checked: $hb_checked" || warn "hard-stop heartbeat checked count missing"

  hb_violations="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"].violations')"
  [[ -n "$hb_violations" ]] && pass "hard-stop heartbeat violations: $hb_violations" || warn "hard-stop heartbeat violations missing"

  hb_mode="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:heartbeat"].mode')"
  [[ -n "$hb_mode" ]] && pass "hard-stop heartbeat mode: $hb_mode" || warn "hard-stop heartbeat mode missing"

  viol_version="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:violations"].version')"
  [[ "$viol_version" == "$EXPECTED_PHS_VERSION" ]] && pass "hard-stop violations version: $EXPECTED_PHS_VERSION" || warn "hard-stop violations version expected $EXPECTED_PHS_VERSION got '${viol_version:-<empty>}'"

  viol_count="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:violations"].violations.length')"
  [[ -n "$viol_count" ]] && pass "hard-stop violations count: $viol_count" || warn "hard-stop violations count missing"

  rec_ts="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:reconcile"].ts')"
  [[ -n "$rec_ts" ]] && pass "reconcile timestamp readable: $rec_ts" || warn "reconcile timestamp missing"
  # FIX-I2: Check reconcile freshness
  check_heartbeat_freshness "hard-stop:reconcile:ts" "$rec_ts"

  broker_count="$(json_get "$PROBE_JSON" 'data.parse["ares:guard:hard_stop:reconcile"].broker_symbols.length')"
  [[ -n "$broker_count" ]] && pass "reconcile broker count: $broker_count" || warn "reconcile broker symbol count missing"

  deprecated_health="$(json_get "$PROBE_JSON" 'data.keys["ares:equity:health"]')"
  if [[ -z "$deprecated_health" || "$deprecated_health" == "null" ]]; then
    pass "deprecated ares:equity:health key correctly absent"
  else
    warn "deprecated ares:equity:health key still present — should be removed"
  fi

  invariant_halt="$(json_get "$PROBE_JSON" 'data.keys["ares:invariant:halt"]')"
  if [[ -z "$invariant_halt" || "$invariant_halt" == "null" ]]; then
    pass "invariant halt not active"
  else
    warn "invariant halt present: $invariant_halt"
  fi

  if [[ -n "$OFG_SESSION_RATE_KEY" ]]; then
    ofg_val="$(json_get "$PROBE_JSON" "data.keys[\"${OFG_SESSION_RATE_KEY}\"]")"
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
    [[ -n "$val_state" ]] && pass "symbol state present for $VERIFY_SYMBOL: $val_state" || warn "symbol state absent for $VERIFY_SYMBOL"
    val_executed="$(json_get "$PROBE_JSON" 'data.symbol.executed')"
    [[ -n "$val_executed" ]] && pass "symbol executed present for $VERIFY_SYMBOL" || warn "symbol executed absent for $VERIFY_SYMBOL"
    val_intent="$(json_get "$PROBE_JSON" 'data.symbol.intent_ts')"
    [[ -n "$val_intent" ]] && pass "symbol intent_ts present for $VERIFY_SYMBOL" || warn "symbol intent_ts absent for $VERIFY_SYMBOL"
  fi
else
  warn "REDIS_CONN not set; Redis verification skipped"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
printf '\n[VERIFY] ═══════════════════════════════════════════════════════════\n'
printf '[VERIFY] SUMMARY: warnings=%d failures=%d  (at %s)\n' "$WARN_COUNT" "$FAIL_COUNT" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
if (( FAIL_COUNT > 0 )); then
  printf '[VERIFY] RESULT: FAILED\n'
  printf '[VERIFY] ═══════════════════════════════════════════════════════════\n'
  exit 1
else
  printf '[VERIFY] RESULT: PASSED\n'
  printf '[VERIFY] ═══════════════════════════════════════════════════════════\n'
fi
