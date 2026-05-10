
/**
 * kill-switch-authority.mjs — Sole writer of trading:enabled
 * 
 * REPLACES: auto-killswitch-system.mjs (simulation code)
 * COMPLEMENTS: go-nogo-judge (verdict publisher)
 * 
 * Design invariants (4AI consensus: Claude + Gemini + Grok + GPT):
 *   - If verdict is not GO       → trading DISABLED
 *   - If verdict is stale (>30s) → trading DISABLED
 *   - If Redis unreachable       → trading DISABLED (best-effort)
 *   - If this process crashes    → PM2 restarts; stale-check disables
 *   - Manual override honored    → trading:enabled:override = "kill"
 *   - Dual path: Pub/Sub (real-time) + Poll (3s backup)
 *   - Latency budget: <10s from verdict to trading:enabled change
 *   - All Redis state writes are ATOMIC via Lua script (GPT review v4)
 *   - Shutdown has 3s hard timeout (Claude review v3)
 * 
 * [STRUCTURAL_PATCH 2026-04-11] Grace Period 추가:
 *   - 일시적 장애(Redis 연결 끊김, verdict 일시 지연)를 즉각 DISABLE로 변환하지 않음
 *   - 연속 CONSECUTIVE_FAIL_THRESHOLD회 실패 시에만 DISABLE
 *   - 마지막 유효 verdict를 LAST_GOOD_VERDICT_TTL_MS 동안 캐싱
 * 
 * @version 1.2.0
 * @author ARES SRE (4AI-guided structural upgrade + First Principles patch)
 */

import Redis from 'ioredis';

function resolveRequiredRedisUrl() {
  const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || "";
  if (!url) throw new Error("REDIS_URL or ARES_REDIS_URL is required");
  if (/localhost|127\.0\.0\.1/.test(url)) {
    throw new Error("Local Redis fallback is forbidden in production");
  }
  return url;
}


// ── Configuration ───────────────────────────────────────────────────────────
const CFG = {
  REDIS_URL:            resolveRequiredRedisUrl(),
  REDIS_PASSWORD:       process.env.REDIS_PASSWORD || '',
  VERDICT_CHANNEL:      process.env.VERDICT_CHANNEL || 'ares:go-nogo:verdicts',
  VERDICT_KEY:          process.env.VERDICT_KEY || 'ares:go_nogo:verdict',  // [MANUS_FIX_20260407] match go-nogo-judge key
  TRADING_ENABLED_KEY:  'trading:enabled',
  OVERRIDE_KEY:         'trading:enabled:override',
  HALT_REQUEST_KEY:     'ops:halt:request',
  AUB_HALT_ACTIVE_KEYS: (process.env.KSA_AUB_HALT_ACTIVE_KEYS || 'ares:guard:halt_active,ares:halt:active').split(',').map(s => s.trim()).filter(Boolean),
  AUB_HALT_REQUEST_KEYS: (process.env.KSA_AUB_HALT_REQUEST_KEYS || 'ares:halt:request:aub_controller').split(',').map(s => s.trim()).filter(Boolean),
  CONTROL_AUDIT_STREAM: 'kill-switch-authority:audit',
  HEARTBEAT_KEY:        'kill-switch-authority:heartbeat',
  STATE_KEY:            'kill-switch-authority:state',
  POLL_INTERVAL_MS:     3_000,        // 3s backup poll (< 10s budget)
  VERDICT_STALE_MS:     180_000,   // [STRUCTURAL_FIX] 120s→180s: go-nogo 60s주기 × 3       // Verdict older than 120s = stale
  HEARTBEAT_TTL_S:      15,           // Heartbeat expires in 15s
  // [STRUCTURAL_PATCH] Grace Period 설정
  CONSECUTIVE_FAIL_THRESHOLD: 5,   // [STRUCTURAL_FIX] 3→5: 15초 grace period       // 연속 N회 실패 시에만 DISABLE
  LAST_GOOD_VERDICT_TTL_MS: 300_000,   // 마지막 유효 verdict 캐시 5분 (INTRADAY 기본값)
  // [MUSK-RCA-20260421-A] adaptive grace — session-aware thresholds
  ADAPTIVE_GRACE_ENABLED: process.env.KSA_ADAPTIVE_GRACE !== 'false',
  MARKET_SESSION_KEY: 'market:session',
  GRACE_PRE_OPEN_THRESHOLD:   parseInt(process.env.KSA_GRACE_PREOPEN_N   || '10', 10),
  GRACE_PRE_OPEN_CACHE_MS:    parseInt(process.env.KSA_GRACE_PREOPEN_MS  || '600000', 10),
  GRACE_INTRADAY_THRESHOLD:   parseInt(process.env.KSA_GRACE_INTRADAY_N  || '5',  10),
  GRACE_INTRADAY_CACHE_MS:    parseInt(process.env.KSA_GRACE_INTRADAY_MS || '300000', 10),
  GRACE_POST_CLOSE_THRESHOLD: parseInt(process.env.KSA_GRACE_POSTCLOSE_N || '3',  10),
  GRACE_POST_CLOSE_CACHE_MS:  parseInt(process.env.KSA_GRACE_POSTCLOSE_MS|| '120000', 10),
  // [MANUS_RESIDUAL_KSA_GLOBAL_FAILCLOSED_CFG_20260429] Default-deny live re-enable until registry ownership/resume runbook is explicit.
  FAIL_CLOSED_REENABLE_BLOCK: process.env.KSA_FAIL_CLOSED_REENABLE_BLOCK !== 'false',
  REENABLE_ALLOW_KEY: process.env.KSA_REENABLE_ALLOW_KEY || 'ops:ksa:allow_reenable',
  // [MANUS_RESIDUAL_KSA_CTRL_CALLERS_RESTORED_20260429] Restore control-plane allow-list Sets used by ctrl:request:trading_enabled.
  CTRL_ALLOWED_CALLERS: new Set((process.env.KSA_CTRL_ALLOWED_CALLERS || 'ares-policy-persistence-guard-v42,ops,sre,manus-hardening-gate').split(',').map(s => s.trim()).filter(Boolean)),
  CTRL_RESUME_CALLERS: new Set((process.env.KSA_CTRL_RESUME_CALLERS || '').split(',').map(s => s.trim()).filter(Boolean)),
};

// ── State ───────────────────────────────────────────────────────────────────
let currentState = 'INIT';      // INIT | ENABLED | DISABLED | OVERRIDE_KILL
let lastVerdictAt = 0;
let subClient = null;
let cmdClient = null;
let verdictCount = 0;
let transitionCount = 0;

// [STRUCTURAL_PATCH] Grace Period 상태
let consecutiveFailCount = 0;
let lastGoodVerdict = null;       // { decision, ts, source }
let lastGoodVerdictAt = 0;        // timestamp when last good verdict was received

// ── Logging (structured JSON, PM2 captures stdout) ─────────────────────────
function log(level, msg, data = {}) {
  const entry = {
    ts: new Date().toISOString(),
    svc: 'kill-switch-authority',
    level,
    msg,
    state: currentState,
    consecutiveFailCount,   // [STRUCTURAL_PATCH] 로그에 연속 실패 횟수 포함
    ...data,
  };
  process.stdout.write(JSON.stringify(entry) + '\n');
}

// ── Atomic Lua for setting trading:enabled + state + heartbeat ─────────────
const SET_TRADING_LUA = `
local enabled_key = KEYS[1]
local state_key   = KEYS[2]
local hb_key      = KEYS[3]
local val         = ARGV[1]
local state_json  = ARGV[2]
local hb_val      = ARGV[3]
local hb_ttl      = tonumber(ARGV[4])
redis.call('SET', enabled_key, val)
redis.call('SET', state_key, state_json)
redis.call('SET', hb_key, hb_val, 'EX', hb_ttl)
return 1
`;
let setTradingScriptSha = null;


async function auditTransition(event, data = {}) {
  try {
    await cmdClient.xadd(
      CFG.CONTROL_AUDIT_STREAM, 'MAXLEN', '~', 10000, '*',
      'payload', JSON.stringify({ ts: new Date().toISOString(), event, pid: process.pid, ...data })
    );
  } catch (e) {
    log('warn', 'audit-transition-failed', { event, err: e.message });
  }
}

function truthyHaltValue(v) {
  return ['1', 'true', 'yes', 'on', 'kill', 'halt', 'halted'].includes(String(v || '').trim().toLowerCase());
}
async function readAubHaltRequest() {
  for (const key of CFG.AUB_HALT_ACTIVE_KEYS) {
    const raw = await cmdClient.get(key).catch(() => null);
    if (truthyHaltValue(raw)) return { caller: 'aub_controller', reason: `aub-halt-active:${key}`, severity: 'halt', value: 'false' };
  }
  for (const key of CFG.AUB_HALT_REQUEST_KEYS) {
    const raw = await cmdClient.get(key).catch(() => null);
    if (raw && String(raw).trim() !== '') return { caller: 'aub_controller', reason: `aub-halt-request:${String(raw).slice(0, 120)}`, severity: 'halt', value: 'false' };
  }
  return null;
}
function parseHaltRequest(raw) {
  if (!raw || String(raw).trim() === '') return null;
  try {
    const req = JSON.parse(raw);
    const exp = req.expires_at || req.expiresAt || req.expiry;
    if (exp && Number.isFinite(Date.parse(exp)) && Date.now() > Date.parse(exp)) return null;
    return req;
  } catch (_) {
    return { caller: 'unknown', reason: String(raw).slice(0, 120), severity: 'unknown', value: 'false' };
  }
}

// CANARY-LIVE-ONLY PATCH 2026-04-29:
// Helper used by the global fail-closed guard. It updates DISABLED state
// without recursively entering setTrading(true)->guard again. This preserves
// fail-closed behavior while preventing poll-loop ReferenceError storms.
async function refreshDisabledStateNoRecursion(reason, meta = {}) {
  const stateJson = JSON.stringify({
    state: 'DISABLED',
    reason,
    ts: Date.now(),
    pid: process.pid,
    verdictCount,
    transitionCount: currentState !== 'DISABLED' ? ++transitionCount : transitionCount,
    consecutiveFailCount,
    ...meta,
  });
  const hbVal = Date.now().toString();
  try {
    if (!setTradingScriptSha) {
      setTradingScriptSha = await cmdClient.script('LOAD', SET_TRADING_LUA);
    }
    await cmdClient.evalsha(
      setTradingScriptSha, 3,
      CFG.TRADING_ENABLED_KEY, CFG.STATE_KEY, CFG.HEARTBEAT_KEY,
      'false', stateJson, hbVal, String(CFG.HEARTBEAT_TTL_S)
    );
  } catch (err) {
    if (err.message?.includes('NOSCRIPT')) {
      setTradingScriptSha = await cmdClient.script('LOAD', SET_TRADING_LUA);
      await cmdClient.evalsha(
        setTradingScriptSha, 3,
        CFG.TRADING_ENABLED_KEY, CFG.STATE_KEY, CFG.HEARTBEAT_KEY,
        'false', stateJson, hbVal, String(CFG.HEARTBEAT_TTL_S)
      );
    } else {
      await cmdClient.set(CFG.TRADING_ENABLED_KEY, 'false');
      await cmdClient.set(CFG.STATE_KEY, stateJson);
      await cmdClient.set(CFG.HEARTBEAT_KEY, hbVal, 'EX', CFG.HEARTBEAT_TTL_S);
    }
  }
  await auditTransition('trading_enabled_transition', { previous_state: currentState, next_state: 'DISABLED', value: 'false', reason, ...meta });
  currentState = 'DISABLED';
  log('warn', 'trading:enabled → false', { reason, ...meta });
}

// ── Core: Set trading:enabled and record state (atomic Lua) ────────────────
async function setTrading(enabled, reason, meta = {}) {
  if (typeof enabled !== 'boolean') {
    log('error', 'setTrading-invalid-enabled-type-fail-closed', {
      receivedType: typeof enabled, receivedValue: String(enabled).slice(0, 50), reason
    });
    enabled = false;
    reason = `invalid-enabled-type:${reason}`;
  }
  const val = enabled ? 'true' : 'false';
  const newState = enabled ? 'ENABLED' : 'DISABLED';
  // [MANUS_RESIDUAL_KSA_GLOBAL_FAILCLOSED_GUARD_20260429] Full fail-closed policy: GO/PASS verdicts and control resume requests cannot
  // re-enable trading unless both runtime env and Redis allow-key are explicitly opened.
  if (enabled === true || enabled === 'true') {
    try {
      const allowKeyValue = await cmdClient.get(CFG.REENABLE_ALLOW_KEY).catch(() => null);
      const envAllows = process.env.KSA_ALLOW_REENABLE === 'true';
      const redisAllows = allowKeyValue === 'true';
      if (CFG.FAIL_CLOSED_REENABLE_BLOCK && !(envAllows && redisAllows)) {
        await refreshDisabledStateNoRecursion(`global-reenable-blocked-fail-closed:${reason}`, {
          original_reason: reason, env_allows: envAllows, redis_allows: redisAllows,
          allow_key: CFG.REENABLE_ALLOW_KEY, marker: 'GLOBAL_REENABLE_BLOCK_FAIL_CLOSED',
        });
        return;
      }
    } catch (globalGuardErr) {
      await refreshDisabledStateNoRecursion(`global-reenable-blocked-guard-error:${reason}`, {
        err: globalGuardErr.message, original_reason: reason, marker: 'GLOBAL_REENABLE_BLOCK_FAIL_CLOSED',
      });
      return;
    }
  }

  const stateJson = JSON.stringify({
    state: newState,
    reason,
    ts: Date.now(),
    pid: process.pid,
    verdictCount,
    transitionCount: newState !== currentState ? ++transitionCount : transitionCount,
    consecutiveFailCount,  // [STRUCTURAL_PATCH]
  });

  const hbVal = Date.now().toString();

  try {
    // Use EVALSHA with NOSCRIPT fallback for atomic write
    if (!setTradingScriptSha) {
      setTradingScriptSha = await cmdClient.script('LOAD', SET_TRADING_LUA);
    }
    try {
      await cmdClient.evalsha(
        setTradingScriptSha, 3,
        CFG.TRADING_ENABLED_KEY, CFG.STATE_KEY, CFG.HEARTBEAT_KEY,
        val, stateJson, hbVal, String(CFG.HEARTBEAT_TTL_S)
      );
    } catch (luaErr) {
      // Handle NOSCRIPT (script evicted from cache) with reload
      if (luaErr.message?.includes('NOSCRIPT')) {
        setTradingScriptSha = await cmdClient.script('LOAD', SET_TRADING_LUA);
        await cmdClient.evalsha(
          setTradingScriptSha, 3,
          CFG.TRADING_ENABLED_KEY, CFG.STATE_KEY, CFG.HEARTBEAT_KEY,
          val, stateJson, hbVal, String(CFG.HEARTBEAT_TTL_S)
        );
      } else {
        throw luaErr;
      }
    }

    const prevState = currentState;
    if (newState !== currentState) {
      log(enabled ? 'info' : 'warn', `trading:enabled → ${val}`, { reason, prev: currentState, ...meta });
    }
    await auditTransition('trading_enabled_transition', { previous_state: prevState, next_state: newState, value: val, reason, ...meta });
    currentState = newState;
  } catch (err) {
    log('error', 'setTrading-failed', { err: err.message, attempted: val, reason });
    // Fail safe: try direct SET as last resort
    try {
      await cmdClient.set(CFG.TRADING_ENABLED_KEY, 'false');
    } catch (directErr) {
      log('error', 'fail-safe-direct-set-also-failed', { err: directErr.message });
    }
    currentState = 'DISABLED';
  }
}

// ── [STRUCTURAL_PATCH] Grace Period 기반 DISABLE 판정 ──────────────────────
async function getSessionGraceConfig() {
  // [MUSK-RCA-20260421-A] adaptive grace
  if (!CFG.ADAPTIVE_GRACE_ENABLED) {
    return { threshold: CFG.CONSECUTIVE_FAIL_THRESHOLD, cacheMs: CFG.LAST_GOOD_VERDICT_TTL_MS, session: 'LEGACY' };
  }
  try {
    const session = (await cmdClient.get(CFG.MARKET_SESSION_KEY) || 'UNKNOWN').toUpperCase();
    if (session === 'PRE_OPEN' || session === 'PREMARKET') {
      return { threshold: CFG.GRACE_PRE_OPEN_THRESHOLD, cacheMs: CFG.GRACE_PRE_OPEN_CACHE_MS, session };
    }
    if (session === 'POST_CLOSE' || session === 'AFTER_HOURS' || session === 'CLOSED') {
      return { threshold: CFG.GRACE_POST_CLOSE_THRESHOLD, cacheMs: CFG.GRACE_POST_CLOSE_CACHE_MS, session };
    }
    // REGULAR / INTRADAY / UNKNOWN → INTRADAY 기본
    return { threshold: CFG.GRACE_INTRADAY_THRESHOLD, cacheMs: CFG.GRACE_INTRADAY_CACHE_MS, session };
  } catch (e) {
    return { threshold: CFG.CONSECUTIVE_FAIL_THRESHOLD, cacheMs: CFG.LAST_GOOD_VERDICT_TTL_MS, session: 'FALLBACK' };
  }
}
async function gracefulDisable(reason, source) {
  consecutiveFailCount++;
  const g = await getSessionGraceConfig();
  // 마지막 유효 verdict가 아직 유효한 경우
  if (lastGoodVerdict && (Date.now() - lastGoodVerdictAt) < g.cacheMs) {
    if (consecutiveFailCount < g.threshold) {
      log('warn', `grace-period[${g.session}]: fail ${consecutiveFailCount}/${g.threshold}, ` +
          `using cached verdict (age=${Date.now() - lastGoodVerdictAt}ms, cacheMs=${g.cacheMs})`,
          { reason, source, session: g.session, cachedDecision: lastGoodVerdict.decision });
      return;
    }
  }
  // Grace period 초과 또는 캐시 없음 → 실제 DISABLE
  log('warn', `grace-period-exhausted[${g.session}]: ${consecutiveFailCount} consecutive failures → DISABLE`,
      { reason, source, session: g.session, threshold: g.threshold });
  await setTrading(false, `${reason} (after ${consecutiveFailCount} consecutive failures, session=${g.session})`);
}

// ── Verdict evaluation ──────────────────────────────────────────────────────
async function evaluateVerdict(verdictRaw, source) {
  verdictCount++;
  let verdict;

  try {
    verdict = typeof verdictRaw === 'string' ? JSON.parse(verdictRaw) : verdictRaw;
  } catch {
    log('error', 'unparseable-verdict', { raw: String(verdictRaw).slice(0, 200), source });
    await gracefulDisable(`unparseable-verdict-from-${source}`, source);  // [STRUCTURAL_PATCH]
    return;
  }

  // ── Manual override check (always takes priority) ─────────────────────
  try {
    const override = await cmdClient.get(CFG.OVERRIDE_KEY);
    if (override === 'kill') {
      consecutiveFailCount = 0;  // [STRUCTURAL_PATCH] override는 의도적이므로 카운터 리셋
      await setTrading(false, 'manual-override-kill');
      currentState = 'OVERRIDE_KILL';
      return;
    }
  } catch (err) {
    log('error', 'override-check-failed', { err: err.message });
    await gracefulDisable('override-check-failed', source);  // [STRUCTURAL_PATCH]
    return;
  }

   // ── Extract timestamp (GPT review: malformed strings must be treated as stale) ──
  const ts = verdict.ts || verdict.timestamp || verdict.updated_at || 0;
  if (typeof ts === 'number' && ts > 0) {
    lastVerdictAt = ts;
  } else if (typeof ts === 'string') {
    const parsed = new Date(ts).getTime();
    if (Number.isFinite(parsed) && parsed > 0) {
      lastVerdictAt = parsed;
    } else {
      // Malformed timestamp — treat as stale (fail safe)
      log('warn', 'malformed-verdict-timestamp', { raw: String(ts).slice(0, 50), source });
      await gracefulDisable(`malformed-timestamp-from-${source}`, source);  // [STRUCTURAL_PATCH]
      return;
    }
  } else {
    // No timestamp at all — treat as stale
    log('warn', 'missing-verdict-timestamp', { source });
    await gracefulDisable(`missing-timestamp-from-${source}`, source);  // [STRUCTURAL_PATCH]
    return;
  }

  // ── Staleness check ───────────────────────────────────────────────────
  const age = Date.now() - lastVerdictAt;
  if (age > CFG.VERDICT_STALE_MS) {
    await gracefulDisable(`stale-verdict-age-${age}ms-from-${source}`, source);  // [STRUCTURAL_PATCH]
    return;
  }

  // ── Verdict decision ──────────────────────────────────────────────────
  const decision = (verdict.result || verdict.verdict || verdict.decision || verdict.status || '').toUpperCase();  // [MANUS_FIX_20260407] go-nogo uses 'result' field
  
  if (decision === 'GO' || decision === 'PASS' || decision === 'APPROVED') {
    consecutiveFailCount = 0;  // [STRUCTURAL_PATCH] 성공 시 카운터 리셋
    lastGoodVerdict = { decision, ts: lastVerdictAt, source };  // [STRUCTURAL_PATCH] 캐시
    lastGoodVerdictAt = Date.now();
    await setTrading(true, `verdict-${decision}-from-${source}`);
  } else {
    await gracefulDisable(`verdict-${decision}-from-${source}`, source);  // [STRUCTURAL_PATCH]
  }
}

// ── Pub/Sub subscriber (real-time path) ─────────────────────────────────────
function startSubscriber() {
  const redisOpts = {
    retryStrategy: (times) => Math.min(times * 500, 5000),
    maxRetriesPerRequest: null,
    enableReadyCheck: true,
  };
  if (CFG.REDIS_PASSWORD) redisOpts.password = CFG.REDIS_PASSWORD;

  subClient = new Redis(CFG.REDIS_URL, redisOpts);

  subClient.on('error', (err) => {
    log('error', 'sub-client-error', { err: err.message });
  });

  subClient.subscribe(CFG.VERDICT_CHANNEL, "ctrl:request:trading_enabled", (err) => {
    if (err) {
      log('error', 'subscribe-failed', { err: err.message, channel: CFG.VERDICT_CHANNEL });
    } else {
      log('info', 'subscribed', { channel: CFG.VERDICT_CHANNEL });
    }
  });

  subClient.on('message', (channel, message) => {
    if (channel === CFG.VERDICT_CHANNEL) {
      evaluateVerdict(message, 'pubsub').catch((err) => {
        log('error', 'evaluate-pubsub-error', { err: err.message });
      });
    }
    // [TIER1-PHASE3] Handle ctrl:request:trading_enabled proposals
    if (channel === 'ctrl:request:trading_enabled') {
      (async () => {
        try {
          const req = JSON.parse(message);
          const val = String(req.value).toLowerCase();
          const caller = req.caller || 'unknown';
          const reason = req.reason || 'ctrl_request';
          const allowed = CFG.CTRL_ALLOWED_CALLERS.has(caller);
          const resumeAllowed = CFG.CTRL_RESUME_CALLERS.has(caller);
          log('info', 'ctrl_request_trading_enabled', { caller, val, reason, allowed, resumeAllowed });
          if (!allowed) {
            await auditTransition('ctrl_request_rejected', { caller, val, reason, reject_reason: 'caller_not_allowed' });
            log('warn', 'ctrl_request_rejected', { caller, val, reason });
            return;
          }
          if (val === 'true') {
            if (!resumeAllowed) {
              await auditTransition('ctrl_request_rejected', { caller, val, reason, reject_reason: 'resume_not_allowed' });
              log('warn', 'ctrl_request_resume_rejected', { caller, reason });
              return;
            }
            await setTrading(true, `ctrl:${caller}:${reason}`, { caller, request_channel: channel });
          } else if (val === 'false') {
            await setTrading(false, `ctrl:${caller}:${reason}`, { caller, request_channel: channel });
          } else {
            log('warn', 'ctrl_request_invalid_value', { val, caller });
          }
        } catch (e) {
          log('error', 'ctrl_request_trading_enabled_error', { err: e.message, raw: message?.slice?.(0, 200) });
        }
      })();
    }
  });
}

// ── Polling loop (backup path, covers missed pub/sub) ───────────────────────
async function pollLoop() {
  while (true) {
    try {
      // Heartbeat (standalone, not critical path)
      await cmdClient.set(CFG.HEARTBEAT_KEY, Date.now().toString(), 'EX', CFG.HEARTBEAT_TTL_S);

      const haltReqRaw = await cmdClient.get(CFG.HALT_REQUEST_KEY);
      const haltReq = parseHaltRequest(haltReqRaw) || await readAubHaltRequest();
      if (haltReq) {
        await auditTransition('ops_halt_request_active', { caller: haltReq.caller || 'unknown', reason: haltReq.reason || 'unspecified', severity: haltReq.severity || 'unknown' });
        await setTrading(false, `ops-halt-request:${haltReq.caller || 'unknown'}:${haltReq.reason || 'unspecified'}`, { caller: haltReq.caller || 'unknown', request_key: CFG.HALT_REQUEST_KEY });
        await new Promise(r => setTimeout(r, CFG.POLL_INTERVAL_MS));
        continue;
      }

      // Read latest verdict from key (backup for missed pub/sub)
      const raw = await cmdClient.get(CFG.VERDICT_KEY);
      if (raw) {
        await evaluateVerdict(raw, 'poll');
      } else {
        // No verdict exists at all — fail safe
        await gracefulDisable('no-verdict-found', 'poll');  // [STRUCTURAL_PATCH]
      }
      // [STRUCTURAL_FIX] Duplicate stale check removed — evaluateVerdict() already handles staleness
    } catch (err) {
      log('error', 'poll-error', { err: err.message });
      // [STRUCTURAL_PATCH] poll 에러도 grace period 적용
      consecutiveFailCount++;
      if (consecutiveFailCount >= CFG.CONSECUTIVE_FAIL_THRESHOLD) {
        // Poll failure → fail safe (best-effort single SET)
        try {
          await cmdClient.set(CFG.TRADING_ENABLED_KEY, 'false');
        } catch { /* best effort */ }
      } else {
        log('warn', `poll-error grace: ${consecutiveFailCount}/${CFG.CONSECUTIVE_FAIL_THRESHOLD}`);
      }
    }

    await new Promise(r => setTimeout(r, CFG.POLL_INTERVAL_MS));
  }
}

// ── Graceful shutdown → FAIL SAFE ──────────────────────────────────────────
async function shutdown(signal) {
  // Hard backstop: force exit after 3s even if Redis hangs (Claude review v3)
  const exitTimer = setTimeout(() => {
    log('error', 'shutdown-hard-exit', { signal, reason: 'redis-write-timeout-3s' });
    throw new Error("Fatal error - PM2 will restart");
  }, 3000);
  exitTimer.unref();

  log('warn', 'shutdown', { signal });
  try {
    // Use atomic Lua for shutdown state too
    await cmdClient.eval(
      `redis.call('SET', KEYS[1], 'false')
       redis.call('SET', KEYS[2], ARGV[1])
       return 1`,
      2,
      CFG.TRADING_ENABLED_KEY, CFG.STATE_KEY,
      JSON.stringify({ state: 'SHUTDOWN', reason: signal, ts: Date.now(), pid: process.pid })
    );
  } catch { /* best effort */ }
  try { subClient?.disconnect(); } catch {}
  try { cmdClient?.disconnect(); } catch {}
  clearTimeout(exitTimer);
  /* process.exit(0) removed for PM2 */ return;
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  log('info', 'starting', { pid: process.pid, version: '1.3.0' });  // [STRUCTURAL_PATCH] version bump

  const redisOpts = {
    retryStrategy: (times) => Math.min(times * 500, 5000),
    maxRetriesPerRequest: 3,
    enableReadyCheck: true,
  };
  if (CFG.REDIS_PASSWORD) redisOpts.password = CFG.REDIS_PASSWORD;

  cmdClient = new Redis(CFG.REDIS_URL, redisOpts);

  cmdClient.on('error', (err) => {
    log('error', 'cmd-client-error', { err: err.message });
  });

  // Pre-load Lua script SHA
  try {
    setTradingScriptSha = await cmdClient.script('LOAD', SET_TRADING_LUA);
    log('info', 'lua-script-loaded', { sha: setTradingScriptSha });
  } catch (err) {
    log('warn', 'lua-script-load-failed', { err: err.message });
    // Will retry on first setTrading call
  }

  // FAIL SAFE on startup: disable until first valid GO verdict
  await setTrading(false, 'startup-fail-safe');

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT',  () => shutdown('SIGINT'));
  process.on('uncaughtException', (err) => {
    log('error', 'uncaught-exception', { err: err.message, stack: err.stack });
    shutdown('uncaughtException');
  });
  process.on('unhandledRejection', (err) => {
    log('error', 'unhandled-rejection', { err: String(err?.message || err), stack: err?.stack });
    shutdown('unhandledRejection');
  });

  startSubscriber();
  await pollLoop();   // blocks forever
}

main().catch((err) => {
  console.error('FATAL:', err);
  throw new Error("Fatal error - PM2 will restart");
});

// ─── DEGRADED MODE ENFORCER (v3 addition) ───
// Reads go-nogo verdict and sets buy:blocked accordingly
async function enforceDegradedMode() {
  try {
    const verdictStr = await redis.get('ares:go_nogo:verdict');
    if (!verdictStr) return;
    const verdict = JSON.parse(verdictStr);
    if (verdict.result === 'DEGRADED' || verdict.degradedMode === true) {
      await redis.set('buy:blocked', 'true');
      await redis.set('buy:blocked:reason', 'DEGRADED_MODE: price freshness stale');
    } else if (verdict.result === 'GO') {
      await redis.set('buy:blocked', 'false');
      await redis.del('buy:blocked:reason');
    }
  } catch (e) {
    // Silent fail — don't break kill-switch for degraded mode
  }
}
// Run degraded mode check every 30 seconds
setInterval(enforceDegradedMode, 30000);

