/**
 * final-trade-gate.mjs — Single Authority Trade Gate
 * ===================================================
 * ARES Zero-Failure Open Plan — P0-2
 *
 * [PATCH-v3 2026-04-21] ANTI-FLAP STRUCTURAL ADDITIONS:
 *   [PATCH-v3-1] HYSTERESIS / DEBOUNCE on gate transitions.
 *     ROOT CAUSE OBSERVED: upstream writers (invariant-checker, OFG
 *     divergence-repair, etc.) produce sub-tick oscillations of
 *     `trade:halt` and `ofg:gate`. Without debouncing, FTG transmits
 *     the oscillation 1:1 to downstream consumers and floods telegram.
 *     FIX: require N consecutive evaluations agreeing on the new gate
 *     before emitting a transition + alert. Transitions INTO a more
 *     restrictive state (toward HALT) are still fast (1-tick) for
 *     safety; only relaxations (toward OPEN) require debounce.
 *   [PATCH-v3-2] ALERT COOLDOWN.
 *     Minimum 60s between Telegram alerts for the same {from,to} pair,
 *     to prevent alert storms even if debounce is defeated.
 *   [PATCH-v3-3] Legacy trade:halt read is kept, but we now also
 *     expose the observation in audit stream so the writer can be
 *     traced when halt-controller writer_audit shows mismatch.
 *
 * [PATCH-v2 2026-04-16] preserved:
 *   [PATCH-v2-1] Force sell check runs REGARDLESS of gate state.
 *   [PATCH-v2-2] Process-level uncaughtException/unhandledRejection handlers
 *   [PATCH-v2-3] Consecutive error tracking with heartbeat on error
 *   [PATCH-v2-4] Additional metadata suffix filters
 *
 * STATES:
 *   OPEN           — Normal trading
 *   DEGRADED       — Reduced trading (BUY caps, no new large positions)
 *   HALT           — No trading at all
 *   LIQUIDATE_ONLY — Only SELL/close/hedge allowed (overrides HALT for force_sells)
 *
 * @version 2.1.0-patch-v3
 */

import { getRedis, log } from '../lib/redis-connection.mjs';
import { sendAlert } from '../lib/telegram-alert.mjs';
import { readOfgStateDetailed } from '../lib/ofg-state-reader.mjs';
import {
  readInputsV47,
  evaluateRawGateV47,
  applyRelaxDebounceV47,
  assertReadSpecIntegrity,
  buildReadSpec,
} from './lib/ares_v47/gate_core_v47.mjs';

const EVAL_INTERVAL_MS = 15_000;
const GATE_TTL_SEC = 240; // [MUSK-FIX 2026-05-06] race-window eliminated; was 90s == watchdog threshold
const AUDIT_STREAM_MAXLEN = 1000;

// [PATCH-v3-1] Debounce thresholds (in evaluation ticks; 1 tick = 15s)
//   - Restrictive transitions (toward HALT/DEGRADED/LIQUIDATE_ONLY): 1 tick (fast)
//   - Relaxing transitions (toward OPEN): 2 ticks (30s) — prevents flapping
const RELAX_DEBOUNCE_TICKS = Number(process.env.FTG_RELAX_DEBOUNCE_TICKS || 2);

// [PATCH-v3-2] Alert cooldown per (from,to) pair
const ALERT_COOLDOWN_MS = Number(process.env.FTG_ALERT_COOLDOWN_MS || 60_000);

// [PATCH-v4 2026-04-26 Manus AI] Anti-flap structural reinforcement.
//   ROOT-CAUSE OBSERVED: pair-keyed cooldown (HALT->OPEN vs OPEN->HALT) lets
//   two opposite transitions alternate every 30s and bypass the cooldown
//   entirely. We add three orthogonal layers:
//     (a) GLOBAL_ALERT_MIN_INTERVAL_MS — minimum gap between ANY two
//         FINAL_TRADE_GATE_CHANGE telegram alerts, regardless of pair.
//     (b) FLAP_WINDOW_MS / FLAP_MAX_TRANSITIONS — if more than N transitions
//         occur within the window, enter quarantine mode and suppress all
//         alerts (still log+audit) for FLAP_QUARANTINE_MS, then send a
//         single summary alert when quarantine ends.
//     (c) When in quarantine, gate value still publishes to redis so
//         executors keep functioning correctly; only telegram is muted.
const GLOBAL_ALERT_MIN_INTERVAL_MS = Number(process.env.FTG_GLOBAL_ALERT_MIN_INTERVAL_MS || 120_000);
const FLAP_WINDOW_MS               = Number(process.env.FTG_FLAP_WINDOW_MS || 300_000);  // 5 min
const FLAP_MAX_TRANSITIONS         = Number(process.env.FTG_FLAP_MAX_TRANSITIONS || 4);
const FLAP_QUARANTINE_MS           = Number(process.env.FTG_FLAP_QUARANTINE_MS || 600_000); // 10 min

// [MUSK-RCA-20260430] Legacy trade:halt is not a durable source of truth.
// A truthy legacy key is honored only when it has either an expiry TTL or
// explicit metadata (reason/ts/details). A metadata-less, non-expiring key
// is an orphan artifact and must not self-perpetuate HALT.
const FTG_IGNORE_STALE_LEGACY_TRADE_HALT = String(process.env.FTG_IGNORE_STALE_LEGACY_TRADE_HALT || 'true').toLowerCase() !== 'false';
const OFG_STATUS_STALE_MS = Number(process.env.OFG_STATUS_STALE_MS || 2000);  // [3AI-FIX] OFG stale 임계값

// Ordering used to decide "restrictive" vs "relaxing"
// SSOT_CONTRACT_V1: HOLD_ONLY is a *partial* restriction (no new buys, sells/holds OK),
// less restrictive than LIQUIDATE_ONLY. Both are technically operational gates,
// not panic states. Only HALT is the truly user-facing alarm condition.
const GATE_RESTRICTIVENESS = { OPEN: 0, HOLD_ONLY: 1, DEGRADED: 2, LIQUIDATE_ONLY: 3, HALT: 4 };

// SSOT_CONTRACT_V1: separate "gate state" (machine-facing, used by executor)
// from "alert state" (human-facing, used for telegram). This prevents the
// noisy operational toggles (e.g. NOOP_REFRESH vs WATCHDOG_REFRESH) from
// reaching the operator while the trading system continues to function correctly.
const ALERT_WORTHY_STATES = new Set(['HALT']);

function isAlertWorthyTransition(from, to) {
  // We only telegram if entering or leaving the truly-alarming HALT state,
  // OR if entering FLAP quarantine (handled separately).
  if (from === to) return false;
  return ALERT_WORTHY_STATES.has(from) || ALERT_WORTHY_STATES.has(to);
}

const K = {

  // [ARES-V4] NextGen Intraday Alpha & LLM Correction Keys
  V4_LLM_CORRECTION: 'llm:regime:correction:latest',
  V4_NEWS_LATEST:    'alpha:news_sentiment:latest',
  V4_MONITOR_VERDICT:'monitor:v3v4:latest_verdict',

  GATE:         'ares:final_trade_gate',
  GATE_REASON:  'ares:final_trade_gate:reason',
  GATE_INPUTS:  'ares:final_trade_gate:inputs',
  GATE_EVAL_TS: 'ares:final_trade_gate:last_eval_ts',
  GATE_HEARTBEAT: 'ares:final_trade_gate:heartbeat',
  FTG_HEARTBEAT:  'ftg:heartbeat',
  AUDIT_STREAM: 'ares:final_trade_gate:audit',
  // [MANUS-STRUCTURAL-FIX 2026-04-30]
  // Legacy consumers and preflight still read `final:tradeable`.
  // The old shadow ares-final-trade-gate no longer owns canonical writes, so
  // stale durable values can falsely block order_gate_contract. Keep this key
  // TTL-bound and synchronized from the single canonical final-trade-gate.
  LEGACY_FINAL_TRADEABLE: 'final:tradeable',
  LEGACY_FINAL_TRADEABLE_REASON: 'final:tradeable:reason',
  LEGACY_FINAL_TRADEABLE_TS: 'final:tradeable:ts',
  // SSOT_CONTRACT_V1 additions (3AI synth: Claude 4-key topology)
  GATE_ACTION:      'ofg:gate:action',       // OPEN | CLOSED | HOLD          (TTL 15s, drives execution)
  GATE_ALERT_LEVEL: 'ofg:gate:alert_level',  // OK | WARN | CRIT              (TTL 120s, hysteresis on downgrade)
  GATE_ALERT_SINCE: 'ofg:gate:alert_since',  // ms when level last changed     (TTL 120s)
  GATE_REASONS_OFG: 'ofg:gate:reasons',      // JSON array of reason codes    (TTL 15s)
  GATE_EVAL_TS_OFG: 'ofg:gate:eval_ts',      // int ms                          (TTL 15s)
  GATE_STATE_USER:    'ares:final_trade_gate:user_facing_state',  // alert-facing legacy compat
  GATE_STATE_MACHINE: 'ares:final_trade_gate:machine_state',      // executor-facing legacy compat
  SSOT_CURRENT: 'ssot:target:v2:current',                          // for trade_mode/promotion read
  OFG_TRADE_MODE: 'ofg:trade_mode',                                // NORMAL | HOLD_ONLY | KILL (TTL 90s)
  OFG_TRADE_MODE_REASON: 'ofg:trade_mode:reason',                  // JSON, TTL 90s

  TRADING_ENABLED:            'trading:enabled',             // ← FTG 출력 (읽지 않음, deprecated as input)
  TRADING_ENABLED_OVERRIDE:   'trading:enabled:override',   // ← manual halt (kill) 만 체크
  TRADE_HALT:        'trade:halt',
  POLICY_TRADE_HALT: 'policy:trade:halt',
  ARES_HALT_ACTIVE:  'ares:halt:active',
  ARES_HALT_SEVERITY:'ares:halt:severity',
  INVARIANT_HALT:    'ares:invariant:halt',
  GUARDRAIL_STATUS:  'guardrail:status',
  OFG_HALT_STATE:    'ares:ofg:halt_state',
  OFG_GATE:          'ofg:gate',
  RECOVERY_RAMP:     'ares:guard:recovery_ramp:active',
  PREOPEN_CRITICAL:  'ares:preopen:critical_fail',
  NEXTGEN2_TRADE_HALT:'nextgen2:trade:halt',
  SAFE_LIVE_HARD_HALT:'policy:safe_live:hard_halt',  // [MUSK-SWP 2026-04-24] safe-live-guard own-lane
  FORCE_SELL_PREFIX:  'ares:guard:force_sell:',
  FORCE_SELL_BYPASS:  'ares:guard:force_sell:bypass',
  OPS_HALT_REQUEST:   'ops:halt:request',
};

const redis = getRedis();
let _prevGate = null;
let _consecutiveErrors = 0;
const MAX_CONSECUTIVE_ERRORS = 10;

// [PATCH-v3-1] Debounce state
let _pendingGate = null;
let _pendingStreak = 0;

// [PATCH-v3-2] Alert cooldown state: Map<`${from}->${to}`, lastAlertTs>
const _alertCooldownMap = new Map();

// [PATCH-v4] Global last-alert ts + transition history ring + quarantine state
let _lastGlobalAlertTs = 0;
const _transitionHistory = []; // array of {ts, from, to}
let _quarantineUntilTs = 0;
let _quarantineStartedAt = 0;
let _quarantineSuppressedCount = 0;
const _quarantineSeenPairs = new Map(); // pair -> count

async function readInputs() {
  const pipe = redis.pipeline();
  pipe.get(K.TRADING_ENABLED_OVERRIDE); // 0 (MUSK-RCA-A: self-ref removed)
  pipe.get(K.TRADE_HALT);             // 1
  pipe.ttl(K.TRADE_HALT);             // 1a legacy halt TTL
  pipe.get('trade:halt:reason');      // 1b legacy halt metadata
  pipe.get('trade:halt:ts');          // 1c legacy halt metadata
  pipe.get('trade:halt:details');     // 1d legacy halt metadata
  pipe.get(K.POLICY_TRADE_HALT);      // 2
  pipe.get(K.ARES_HALT_ACTIVE);       // 3
  pipe.get(K.ARES_HALT_SEVERITY);     // 4
  pipe.get(K.INVARIANT_HALT);         // 5
  pipe.get(K.GUARDRAIL_STATUS);       // 6
  pipe.get(K.OFG_HALT_STATE);         // 7
  pipe.get(K.OFG_GATE);               // 8
  pipe.get(K.RECOVERY_RAMP);          // 9
  pipe.get(K.PREOPEN_CRITICAL);       // 10
  pipe.get(K.NEXTGEN2_TRADE_HALT);    // 11
  pipe.get(K.SAFE_LIVE_HARD_HALT);    // 11b [MUSK-SWP 2026-04-24]
  pipe.get(K.FORCE_SELL_BYPASS);      // 13 (was 12)
  pipe.get(K.OPS_HALT_REQUEST);       // 14 structured halt intent
  pipe.get(K.SSOT_CURRENT);           // 15 SSOT_CONTRACT_V1

  pipe.hgetall(K.V4_LLM_CORRECTION);  // V4-1
  pipe.get(K.V4_NEWS_LATEST);         // V4-2
  pipe.get(K.V4_MONITOR_VERDICT);     // V4-3

  const results = await pipe.exec();

  // [MUSK_RCA_20260429] Read OFG owner state through the canonical stale-aware reader.
  // Do not treat FTG's own output keys as OFG input; stale/missing owner state fails closed.
  let ofgState = { state: 'HALT', source: 'reader:not_called', ts: null, ageMs: null, stale: true };
  try {
    ofgState = await readOfgStateDetailed(redis);
  } catch (err) {
    ofgState = { state: 'HALT', source: 'reader:error', error: String(err?.message || err).slice(0, 200), ts: null, ageMs: null, stale: true };
  }

  // SSOT_CONTRACT_V1: extract trade_mode and promotion.type from SSOT payload
  
  // [ARES-V4] Extract V4 inputs
  const v4_llm_correction = results[20] && results[20][1] ? results[20][1] : {};
  const v4_news_latest    = results[21] && results[21][1] ? results[21][1] : null;
  const v4_monitor        = results[22] && results[22][1] ? results[22][1] : null;
  
  let llmFactor = 0;
  let llmAction = 'MAINTAIN';
  if (v4_llm_correction && v4_llm_correction.correction_factor) {
    llmFactor = parseFloat(v4_llm_correction.correction_factor) || 0;
    llmAction = v4_llm_correction.recommended_action || 'MAINTAIN';
  }
  
  let monitorVerdict = null;
  try {
    if (v4_monitor) monitorVerdict = JSON.parse(v4_monitor);
  } catch(e) {}

  let ssot_trade_mode = null;
  let ssot_promotion_type = null;
  try {
    const ssotRaw = results[19]?.[1];
    if (ssotRaw) {
      const obj = JSON.parse(ssotRaw);
      ssot_trade_mode = obj?.targets?.meta?.trade_mode || obj?.meta?.trade_mode || null;
      ssot_promotion_type = obj?.promotion?.type || null;
    }
  } catch (_) { /* tolerate parse errors */ }

  return {
    trading_enabled_override: results[0]?.[1],  // (MUSK-RCA-A: self-ref removed)
    trade_halt:        results[1]?.[1],
    trade_halt_ttl:    results[2]?.[1],
    trade_halt_reason: results[3]?.[1],
    trade_halt_ts:     results[4]?.[1],
    trade_halt_details:results[5]?.[1],
    policy_trade_halt: results[6]?.[1],
    ares_halt_active:  results[7]?.[1],
    ares_halt_severity:results[8]?.[1],
    invariant_halt:    results[9]?.[1],
    guardrail_status:  results[10]?.[1],
    ofg_halt_state:    results[11]?.[1],
    ofg_gate:          ofgState.state || results[12]?.[1],
    ofg_gate_source:   ofgState.source || 'unknown',
    ofg_gate_ts:       ofgState.ts || null,
    ofg_gate_age_ms:   ofgState.ageMs ?? null,
    ofg_gate_stale:    ofgState.stale ?? null,
    recovery_ramp:     results[13]?.[1],
    preopen_critical:  results[14]?.[1],
    nextgen2_trade_halt: results[15]?.[1],
    safe_live_hard_halt: results[16]?.[1],
    force_sell_bypass: results[17]?.[1],
    ops_halt_request: results[18]?.[1],
    ssot_trade_mode,
    ssot_promotion_type,

    v4_llm_factor: llmFactor,
    v4_llm_action: llmAction,
    v4_monitor_verdict: monitorVerdict,

  };
}


function parseOpsHaltRequest(raw) {
  if (!raw || String(raw).trim() === '') return null;
  try {
    const obj = JSON.parse(raw);
    const exp = obj.expires_at || obj.expiresAt || obj.expiry;
    if (exp && Number.isFinite(Date.parse(exp)) && Date.now() > Date.parse(exp)) return null;
    return obj;
  } catch (_) {
    return { caller: 'unknown', reason: String(raw).slice(0, 80), severity: 'unknown', ts: null };
  }
}

function evaluateGate(inputs) {
  // [V4.7] Delegate to gate_core_v47 evaluateRawGateV47 for structured risk-lattice evaluation
  try {
    const v47Config = {
      ignoreStaleLegacyTradeHalt: FTG_IGNORE_STALE_LEGACY_TRADE_HALT,
      v4Required: String(process.env.FTG_V4_REQUIRED || 'false').toLowerCase() === 'true',
      v4RequiredFailureGate: process.env.FTG_V4_FAILURE_GATE || 'HOLD_ONLY',
      llmReduceThreshold: Number(process.env.FTG_V4_LLM_REDUCE_THRESHOLD || -0.30),
      llmHoldThreshold: Number(process.env.FTG_V4_LLM_HOLD_THRESHOLD || -0.65),
      llmMaxAgeMs: Number(process.env.FTG_V4_LLM_MAX_AGE_MS || 360000),
      monitorMaxAgeMs: Number(process.env.FTG_V4_MONITOR_MAX_AGE_MS || 180000),
      allowMonitorHardHalt: String(process.env.FTG_V4_MONITOR_ALLOW_HALT || 'false').toLowerCase() === 'true',
    };
    const v47Result = evaluateRawGateV47(inputs, v47Config);
    return { gate: v47Result.gate, reasons: v47Result.reasons };
  } catch (v47Err) {
    log('WARN', 'FTG_V47_EVAL_FALLBACK', { error: String(v47Err?.message || v47Err).slice(0, 200) });
  }
  // [V4.7 FALLBACK] Legacy evaluation below
function inspectLegacyTradeHalt(inputs) {
  const raw = String(inputs.trade_halt ?? '').trim().toLowerCase();
  const truthy = raw === 'true' || raw === '1';
  if (!truthy) return { honor: false, ignored: false, reason: null };

  const ttl = Number(inputs.trade_halt_ttl);
  const hasExpiry = Number.isFinite(ttl) && ttl > 0;
  const hasMetadata = Boolean(inputs.trade_halt_reason || inputs.trade_halt_ts || inputs.trade_halt_details);
  if (FTG_IGNORE_STALE_LEGACY_TRADE_HALT && !hasExpiry && !hasMetadata) {
    return {
      honor: false,
      ignored: true,
      reason: `stale_legacy_trade_halt_ignored raw=${raw} ttl=${Number.isFinite(ttl) ? ttl : 'unknown'} metadata=absent`,
    };
  }
  return {
    honor: true,
    ignored: false,
    reason: `trade:halt = true ttl=${Number.isFinite(ttl) ? ttl : 'unknown'} metadata=${hasMetadata ? 'present' : 'absent'}`,
  };
}

  const reasons = [];
  let gate = 'OPEN';

  // [MUSK-RCA-20260421-A] Self-reference removed.
  // Previously FTG read its own output (trading:enabled) as input, creating a
  // closed loop where any transient false state would self-perpetuate.
  // Now only manual kill-switch override (trading:enabled:override = 'kill')
  // can force HALT from this source. Other halts are covered by dedicated keys.
  const FTG_DISABLE_SELF_REF = process.env.FTG_DISABLE_SELF_REFERENCE !== 'false';
  if (FTG_DISABLE_SELF_REF) {
    if (inputs.trading_enabled_override === 'kill') {
      reasons.push('manual_override = kill');
      gate = 'HALT';
      return { gate, reasons };
    }
    // else: do NOT read trading:enabled anymore — FTG is authoritative output
  } else {
    // Legacy fallback (for rollback) — reads back trading:enabled (DEPRECATED)
    if (!inputs.trading_enabled_override || inputs.trading_enabled_override !== 'true') {
      // fallback kept for safety net if fused with legacy key still around
      // NO-OP — safer to not halt on missing override
    }
  }

  const opsHaltRequest = parseOpsHaltRequest(inputs.ops_halt_request);
  if (opsHaltRequest) {
    reasons.push(`ops:halt:request = ${opsHaltRequest.reason || 'requested'} (caller: ${opsHaltRequest.caller || 'unknown'})`);
    gate = 'HALT';
  }

  const legacyTradeHalt = inspectLegacyTradeHalt(inputs);
  if (legacyTradeHalt.honor) {
    reasons.push(legacyTradeHalt.reason || 'trade:halt = true');
    gate = 'HALT';
  } else if (legacyTradeHalt.ignored) {
    reasons.push(legacyTradeHalt.reason);
  }
  if (inputs.policy_trade_halt === 'true' || inputs.policy_trade_halt === '1') {
    reasons.push('policy:trade:halt = true');
    gate = 'HALT';
  }
  if (inputs.ares_halt_active === 'true' || inputs.ares_halt_active === '1') {
    reasons.push(`ares:halt:active = true (severity: ${inputs.ares_halt_severity || 'unknown'})`);
    gate = 'HALT';
  }
  if (inputs.safe_live_hard_halt === 'true' || inputs.safe_live_hard_halt === '1') {
    reasons.push('policy:safe_live:hard_halt = true');
    gate = 'HALT';
  }
  if (inputs.invariant_halt === '1' || inputs.invariant_halt === 'true') {
    reasons.push('ares:invariant:halt = 1');
    gate = 'HALT';
  }

  if (gate === 'HALT') return { gate, reasons };

  if (inputs.ofg_halt_state === 'HALT') {
    reasons.push('ares:ofg:halt_state = HALT');
    gate = 'HALT';
    return { gate, reasons };
  }
  if (inputs.ofg_gate === 'HALT') {
    reasons.push(`ofg_state = HALT (source=${inputs.ofg_gate_source || 'unknown'}, age_ms=${inputs.ofg_gate_age_ms ?? 'unknown'})`);
    gate = 'HALT';
    return { gate, reasons };
  }

  if (inputs.preopen_critical && inputs.preopen_critical !== '0' && inputs.preopen_critical !== 'false') {
    reasons.push(`ares:preopen:critical_fail = ${inputs.preopen_critical}`);
    gate = 'HALT';
    return { gate, reasons };
  }

  if (inputs.guardrail_status === 'CRITICAL') {
    reasons.push('guardrail:status = CRITICAL');
    if (gate !== 'HALT') gate = 'DEGRADED';
  }
  if (inputs.guardrail_status === 'WARNING') {
    reasons.push('guardrail:status = WARNING');
    if (gate === 'OPEN') gate = 'DEGRADED';
  }
  if (inputs.recovery_ramp === 'true') {
    reasons.push('recovery_ramp active');
    if (gate === 'OPEN') gate = 'DEGRADED';
  }
  if (inputs.ofg_gate === 'DEGRADED' || inputs.ofg_gate === 'WARN') {
    reasons.push(`ofg_state = ${inputs.ofg_gate} (source=${inputs.ofg_gate_source || 'unknown'})`);
    if (gate === 'OPEN') gate = 'DEGRADED';
  }
  if (inputs.nextgen2_trade_halt === 'true' || inputs.nextgen2_trade_halt === '1' || inputs.nextgen2_trade_halt === 'HALT') {
    reasons.push(`nextgen2:trade:halt = ${inputs.nextgen2_trade_halt}`);
    if (gate === 'OPEN') gate = 'DEGRADED';
  }

  
  // =========================================================================
  // [ARES-V4] Intraday Alpha & LLM Correction Integration
  // =========================================================================
  // LLM 보정 레이어에서 REDUCE_RISK가 강하게 떨어질 경우 DEGRADED 모드 진입
  if (inputs.v4_llm_action === 'REDUCE_RISK' && inputs.v4_llm_factor <= -0.3) {
    reasons.push(`v4:llm_correction: REDUCE_RISK (factor=${inputs.v4_llm_factor})`);
    if (gate === 'OPEN') gate = 'DEGRADED';
  }
  
  // 모니터링 스크립트의 실시간 품질 검증 결과 반영
  if (inputs.v4_monitor_verdict && inputs.v4_monitor_verdict.verdict) {
    const verdict = inputs.v4_monitor_verdict.verdict;
    if (verdict.recommendation === 'HOLD_PENDING_STABILITY' && gate === 'OPEN') {
      reasons.push('v4:monitor: HOLD_PENDING_STABILITY (regime/alpha unstable)');
      gate = 'HOLD_ONLY'; // 불안정할 경우 신규 매수 금지 (HOLD_ONLY)
    }
  }
  // =========================================================================

  // SSOT_CONTRACT_V1: HOLD_ONLY directive from SSOT (e.g. WATCHDOG_REFRESH).
  // It DOES NOT mean "halt" — sells and holds still execute. We therefore
  // never escalate to HALT for this; we only restrict OPEN/DEGRADED to HOLD_ONLY.
  if (inputs.ssot_trade_mode === 'HOLD_ONLY' && (gate === 'OPEN' || gate === 'DEGRADED')) {
    reasons.push(`ssot.trade_mode = HOLD_ONLY (promotion.type = ${inputs.ssot_promotion_type || 'unknown'})`);
    gate = 'HOLD_ONLY';
  }
  if (inputs.ssot_trade_mode === 'LIQUIDATE_ONLY' && gate !== 'HALT') {
    reasons.push(`ssot.trade_mode = LIQUIDATE_ONLY`);
    gate = 'LIQUIDATE_ONLY';
  }

  if (reasons.length === 0) reasons.push('all_clear');

  return { gate, reasons };
}

async function checkPendingForceSells() {
  const forceKeys = [];
  let cursor = '0';
  do {
    const [nextCursor, keys] = await redis.scan(cursor, 'MATCH', K.FORCE_SELL_PREFIX + '*', 'COUNT', 100);
    cursor = nextCursor;
    forceKeys.push(...keys);
  } while (cursor !== '0');

  const metadataSuffixes = [':executed', ':state', ':state_data', ':stream', ':bypass', ':retry_count', ':reissue_count'];
  const symbolKeys = forceKeys.filter(k => !metadataSuffixes.some(suffix => k.endsWith(suffix)));

  const trulyPending = [];
  const executedCount = [];
  for (const k of symbolKeys) {
    const executedKey = k + ':executed';
    const stateKey = k + ':state';
    const hasExecuted = forceKeys.includes(executedKey);
    if (hasExecuted) { executedCount.push(k); continue; }
    try {
      const state = await redis.get(stateKey);
      if (state === 'CONFIRMED' || state === 'BROKER_CONFIRMED') { executedCount.push(k); continue; }
    } catch { /* non-critical */ }
    trulyPending.push(k);
  }

  return { trulyPending, executedCount: executedCount.length, totalSymbolKeys: symbolKeys.length };
}

/**
 * [PATCH-v3-1] Apply hysteresis: the RAW evaluation produced `rawGate`, but
 * we only emit a transition to `effectiveGate` once the new value has been
 * seen for enough consecutive ticks. Moves toward a more-restrictive state
 * (higher GATE_RESTRICTIVENESS) are immediate; moves toward a less-restrictive
 * state require `RELAX_DEBOUNCE_TICKS` consecutive agreement.
 *
 * Returns the gate we should actually emit this tick.
 */
function applyHysteresis(rawGate) {
  if (_prevGate === null) {
    // First tick ever — emit as-is, no debounce possible.
    _pendingGate = null;
    _pendingStreak = 0;
    return rawGate;
  }
  if (rawGate === _prevGate) {
    _pendingGate = null;
    _pendingStreak = 0;
    return _prevGate;
  }
  const rawR  = GATE_RESTRICTIVENESS[rawGate]  ?? 0;
  const prevR = GATE_RESTRICTIVENESS[_prevGate] ?? 0;
  // More restrictive (rawR > prevR) → immediate (safety first)
  if (rawR > prevR) {
    _pendingGate = null;
    _pendingStreak = 0;
    return rawGate;
  }
  // Relaxation → require debounce
  if (_pendingGate === rawGate) {
    _pendingStreak += 1;
  } else {
    _pendingGate = rawGate;
    _pendingStreak = 1;
  }
  if (_pendingStreak >= RELAX_DEBOUNCE_TICKS) {
    _pendingGate = null;
    _pendingStreak = 0;
    return rawGate;
  }
  // Not yet — hold previous gate
  return _prevGate;
}

async function evaluate() {
  try {
    const now = Date.now();
    const inputs = await readInputs();
    const { gate: rawGate, reasons } = evaluateGate(inputs);

    // Force sell check is independent of gate (PATCH-v2-1)
    let effectiveGateAfterRules = rawGate;
    try {
      const { trulyPending, executedCount, totalSymbolKeys } = await checkPendingForceSells();
      const hasBypass = inputs.force_sell_bypass === 'true';

      if (trulyPending.length > 0) {
        reasons.push(`force_sell_pending: ${trulyPending.length} symbols`);
        if (effectiveGateAfterRules === 'HALT' && hasBypass) {
          effectiveGateAfterRules = 'LIQUIDATE_ONLY';
          reasons.push('HALT->LIQUIDATE_ONLY: force_sell bypass active');
          log('WARN', 'FTG_HALT_OVERRIDE_FOR_FORCE_SELL', {
            pending_count: trulyPending.length,
            bypass: hasBypass,
            original_gate: rawGate,
            effective_gate: 'LIQUIDATE_ONLY',
            symbols: trulyPending.map(k => k.replace(K.FORCE_SELL_PREFIX, '')),
          });
        } else if (effectiveGateAfterRules === 'OPEN' || effectiveGateAfterRules === 'DEGRADED') {
          effectiveGateAfterRules = 'LIQUIDATE_ONLY';
        } else if (effectiveGateAfterRules === 'HALT' && !hasBypass) {
          reasons.push(`WARN: ${trulyPending.length} force_sells BLOCKED by HALT (no bypass key)`);
          log('ERROR', 'FTG_FORCE_SELL_BLOCKED_BY_HALT', {
            pending_count: trulyPending.length,
            symbols: trulyPending.map(k => k.replace(K.FORCE_SELL_PREFIX, '')),
            action: 'Set ares:guard:force_sell:bypass=true to override HALT for force sells',
          });
        }
      } else if (totalSymbolKeys > 0) {
        reasons.push(`force_sell_executed: ${executedCount} symbols (all executed)`);
      }
    } catch (err) {
      log('ERROR', 'FTG_FORCE_SELL_CHECK_ERROR', { error: err.message });
      reasons.push(`force_sell_check_error: ${err.message}`);
    }

    // [PATCH-v3-1] Apply hysteresis to the gate value we actually publish.
    const emittedGate = applyHysteresis(effectiveGateAfterRules);

    // Annotate reasons if hysteresis is currently holding the previous gate
    if (emittedGate !== effectiveGateAfterRules) {
      reasons.push(`hysteresis_holding: raw=${effectiveGateAfterRules} emitted=${emittedGate} streak=${_pendingStreak}/${RELAX_DEBOUNCE_TICKS}`);
    }

    // SSOT_CONTRACT_V1: 4-key gate-state topology (3AI synth: Claude).
    //   ofg:gate:action       — drives execution (OPEN/CLOSED/HOLD), short TTL
    //   ofg:gate:alert_level  — drives PagerDuty (OK/WARN/CRIT),  hysteresis TTL
    //   ofg:gate:reasons      — array of reason codes
    //   ofg:gate:eval_ts      — last evaluation ts
    // The two dimensions are independent: action can flip at sub-min cadence
    // while alert_level is dampened (downgrade requires holding for 120s).
    function actionForGate(g) {
      if (g === 'HALT')           return 'CLOSED';
      if (g === 'HOLD_ONLY')      return 'HOLD';
      if (g === 'LIQUIDATE_ONLY') return 'LIQUIDATE_ONLY';
      if (g === 'DEGRADED')       return 'OPEN';
      return 'OPEN';
    }
    function alertLevelForGate(g, reasonsArr) {
      if (g === 'HALT') return 'CRIT';
      if (g === 'LIQUIDATE_ONLY') return 'WARN';
      if (g === 'DEGRADED' || g === 'HOLD_ONLY') return 'WARN';
      // HOLD_ONLY caused by watchdog freshness is informational, not a CRIT.
      return 'OK';
    }
    const newAction     = actionForGate(emittedGate);
    const desiredLevel  = alertLevelForGate(emittedGate, reasons);
    const userFacingState = (emittedGate === 'HALT') ? 'HALT' : 'RUNNING';

    // Hysteresis on alert_level: upgrades immediate, downgrades require
    // ALERT_HYSTERESIS_S elapsed since last change.
    const ALERT_HYSTERESIS_S = 120;
    const SEV = { OK: 0, WARN: 1, CRIT: 2 };
    const curLevel = (await redis.get(K.GATE_ALERT_LEVEL)) || 'OK';
    const curSince = parseInt((await redis.get(K.GATE_ALERT_SINCE)) || '0', 10);
    let nextLevel = curLevel;
    let nextSince = curSince || now;
    if ((SEV[desiredLevel] ?? 0) > (SEV[curLevel] ?? 0)) {
      nextLevel = desiredLevel;
      nextSince = now;
    } else if (desiredLevel !== curLevel && (now - curSince) > ALERT_HYSTERESIS_S * 1000) {
      nextLevel = desiredLevel;
      nextSince = now;
    }

    const pipe = redis.pipeline();
    pipe.set(K.GATE, emittedGate, 'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_STATE_MACHINE, emittedGate, 'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_STATE_USER,    userFacingState, 'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_ACTION,        newAction,      'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_ALERT_LEVEL,   nextLevel,      'EX', ALERT_HYSTERESIS_S);
    pipe.set(K.GATE_ALERT_SINCE,   String(nextSince), 'EX', ALERT_HYSTERESIS_S);
    pipe.set(K.GATE_EVAL_TS_OFG,   String(now),    'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_REASONS_OFG,   JSON.stringify(reasons), 'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_REASON, JSON.stringify(reasons), 'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_INPUTS, JSON.stringify(inputs), 'EX', GATE_TTL_SEC);
    pipe.set(K.GATE_EVAL_TS, new Date(now).toISOString(), 'EX', GATE_TTL_SEC);
    // [MANUS-STRUCTURAL-FIX 2026-04-30] Legacy order-gate contract compatibility.
    // OPEN is the only fully tradeable state. HALT/HOLD_ONLY/LIQUIDATE_ONLY/DEGRADED
    // must not be interpreted as unrestricted live trading by older consumers.
    pipe.set(K.LEGACY_FINAL_TRADEABLE, emittedGate === 'OPEN' ? 'true' : 'false', 'EX', GATE_TTL_SEC);
    pipe.set(K.LEGACY_FINAL_TRADEABLE_REASON, JSON.stringify(reasons), 'EX', GATE_TTL_SEC);
    pipe.set(K.LEGACY_FINAL_TRADEABLE_TS, new Date(now).toISOString(), 'EX', GATE_TTL_SEC);
    pipe.setex(K.GATE_HEARTBEAT, GATE_TTL_SEC, String(now));
    pipe.setex(K.FTG_HEARTBEAT, GATE_TTL_SEC, String(now));
    pipe.xadd(K.AUDIT_STREAM, 'MAXLEN', '~', String(AUDIT_STREAM_MAXLEN), '*',
      'gate', emittedGate,
      'raw_gate', effectiveGateAfterRules,
      'reasons', JSON.stringify(reasons),
      'ts', new Date(now).toISOString(),
    );
    await pipe.exec();

    _consecutiveErrors = 0;

    if (_prevGate !== null && _prevGate !== emittedGate) {
      log('WARN', 'FINAL_TRADE_GATE_CHANGED', {
        from: _prevGate, to: emittedGate, reasons,
      });

      // [PATCH-v4] Record transition into ring for flap detection (always),
      // even if we end up suppressing the alert.
      _transitionHistory.push({ ts: now, from: _prevGate, to: emittedGate });
      while (_transitionHistory.length && now - _transitionHistory[0].ts > FLAP_WINDOW_MS) {
        _transitionHistory.shift();
      }

      const pairKey = `${_prevGate}->${emittedGate}`;

      // SSOT_CONTRACT_V1: only telegram if the user-facing state changed.
      // Internal toggles (OPEN <-> HOLD_ONLY <-> DEGRADED) stay in audit only.
      if (!isAlertWorthyTransition(_prevGate, emittedGate)) {
        log('INFO', 'FTG_TRANSITION_NOT_USER_FACING', { pair: pairKey, reasons });
        _prevGate = emittedGate;
        return;
      }

      // [PATCH-v4-b] Already in quarantine? Just count and exit (mute telegram).
      if (now < _quarantineUntilTs) {
        _quarantineSuppressedCount += 1;
        _quarantineSeenPairs.set(pairKey, (_quarantineSeenPairs.get(pairKey) || 0) + 1);
        log('INFO', 'FTG_ALERT_SUPPRESSED_QUARANTINE', {
          pair: pairKey,
          quarantineRemainingMs: _quarantineUntilTs - now,
          suppressedSoFar: _quarantineSuppressedCount,
        });
      } else if (_transitionHistory.length > FLAP_MAX_TRANSITIONS) {
        // [PATCH-v4-b] Just crossed flap threshold — enter quarantine and
        // send one summary alert NOW; subsequent transitions will be muted.
        _quarantineUntilTs = now + FLAP_QUARANTINE_MS;
        _quarantineStartedAt = now;
        _quarantineSuppressedCount = 0;
        _quarantineSeenPairs.clear();
        _quarantineSeenPairs.set(pairKey, 1);
        _lastGlobalAlertTs = now;
        log('ERROR', 'FTG_FLAP_QUARANTINE_ENTERED', {
          transitionsInWindow: _transitionHistory.length,
          windowMs: FLAP_WINDOW_MS,
          quarantineMs: FLAP_QUARANTINE_MS,
          recentTransitions: _transitionHistory.slice(-8),
        });
        await sendAlert('FINAL_TRADE_GATE_FLAP_QUARANTINE', [
          `Final Trade Gate is FLAPPING — entering ${Math.round(FLAP_QUARANTINE_MS/1000)}s quarantine.`,
          `Detected ${_transitionHistory.length} transitions in ${Math.round(FLAP_WINDOW_MS/1000)}s.`,
          `Latest: ${_prevGate} -> ${emittedGate}`,
          `Reasons: ${reasons.join(', ')}`,
          `Telegram alerts for FINAL_TRADE_GATE_CHANGE will be suppressed during quarantine; gate value still publishes normally.`,
        ].join('\n'), { force: true });
      } else {
        // [PATCH-v3-2] pair-cooldown + [PATCH-v4-a] global rate limit
        const lastPairAlertTs = _alertCooldownMap.get(pairKey) || 0;
        const pairCooledDown   = (now - lastPairAlertTs)   >= ALERT_COOLDOWN_MS;
        const globalCooledDown = (now - _lastGlobalAlertTs) >= GLOBAL_ALERT_MIN_INTERVAL_MS;
        if (pairCooledDown && globalCooledDown) {
          _alertCooldownMap.set(pairKey, now);
          _lastGlobalAlertTs = now;
          await sendAlert('FINAL_TRADE_GATE_CHANGE', [
            `Final Trade Gate: ${_prevGate} -> ${emittedGate}`,
            `Reasons: ${reasons.join(', ')}`,
          ].join('\n'), { force: true });
        } else {
          log('INFO', 'FTG_ALERT_SUPPRESSED_COOLDOWN', {
            pair: pairKey,
            pairSinceMs: now - lastPairAlertTs,
            globalSinceMs: now - _lastGlobalAlertTs,
            pairCooldownMs: ALERT_COOLDOWN_MS,
            globalCooldownMs: GLOBAL_ALERT_MIN_INTERVAL_MS,
          });
        }
      }
    }

    // [PATCH-v4-c] Quarantine-end summary: if we just exited a quarantine,
    // emit one consolidated telegram so operators know the suppression ended
    // and how many transitions were swallowed.
    if (_quarantineUntilTs > 0 && now >= _quarantineUntilTs) {
      const summaryPairs = Array.from(_quarantineSeenPairs.entries())
        .map(([k, v]) => `${k} x${v}`).join(', ');
      log('WARN', 'FTG_FLAP_QUARANTINE_ENDED', {
        startedAt: _quarantineStartedAt,
        suppressed: _quarantineSuppressedCount,
        pairs: summaryPairs,
        currentGate: emittedGate,
      });
      try {
        await sendAlert('FINAL_TRADE_GATE_FLAP_QUARANTINE_END', [
          `Final Trade Gate flap quarantine ended.`,
          `Suppressed ${_quarantineSuppressedCount} transitions during ${Math.round((now-_quarantineStartedAt)/1000)}s window.`,
          `Pairs: ${summaryPairs || '(none)'}`,
          `Current gate: ${emittedGate}`,
        ].join('\n'), { force: true });
      } catch (_) { /* best-effort */ }
      _quarantineUntilTs = 0;
      _quarantineStartedAt = 0;
      _quarantineSuppressedCount = 0;
      _quarantineSeenPairs.clear();
      _lastGlobalAlertTs = now;
    }

    _prevGate = emittedGate;

    if (now % 60000 < EVAL_INTERVAL_MS) {
      log('INFO', 'FINAL_TRADE_GATE', { gate: emittedGate, reasons });
    }

  } catch (err) {
    _consecutiveErrors++;
    log('ERROR', 'FINAL_TRADE_GATE: eval error', {
      error: err.message,
      consecutive_errors: _consecutiveErrors,
      stack: err.stack?.slice(0, 300),
    });

    try {
      await redis.set(K.GATE, 'HALT', 'EX', GATE_TTL_SEC);
      await redis.set(K.GATE_REASON, JSON.stringify(['EVAL_ERROR: ' + err.message]), 'EX', GATE_TTL_SEC);
      await redis.set(K.LEGACY_FINAL_TRADEABLE, 'false', 'EX', GATE_TTL_SEC);
      await redis.set(K.LEGACY_FINAL_TRADEABLE_REASON, JSON.stringify(['EVAL_ERROR: ' + err.message]), 'EX', GATE_TTL_SEC);
      await redis.set(K.LEGACY_FINAL_TRADEABLE_TS, new Date(Date.now()).toISOString(), 'EX', GATE_TTL_SEC);
      await redis.setex(K.GATE_HEARTBEAT, GATE_TTL_SEC, String(Date.now()));
      await redis.setex(K.FTG_HEARTBEAT, GATE_TTL_SEC, String(Date.now()));
    } catch (innerErr) {
      log('ERROR', 'FINAL_TRADE_GATE: fail-closed write also failed', { error: innerErr.message });
    }

    if (_consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
      log('ERROR', 'FINAL_TRADE_GATE: too many consecutive errors', { consecutive_errors: _consecutiveErrors });
      _consecutiveErrors = 0;
    }
  }
}

process.on('uncaughtException', (err) => {
  log('ERROR', 'FTG_UNCAUGHT_EXCEPTION', { error: err.message, stack: err.stack?.slice(0, 500) });
});
process.on('unhandledRejection', (reason) => {
  log('ERROR', 'FTG_UNHANDLED_REJECTION', { reason: String(reason)?.slice(0, 500) });
});

log('INFO', '=== Final Trade Gate v2.1.0-patch-v3 Starting ===', {
  eval_interval_ms: EVAL_INTERVAL_MS,
  gate_ttl_sec: GATE_TTL_SEC,
  relax_debounce_ticks: RELAX_DEBOUNCE_TICKS,
  alert_cooldown_ms: ALERT_COOLDOWN_MS,
  patches: [
    'PATCH-v2-1: force_sell passthrough works during HALT',
    'PATCH-v2-2: process-level error handlers',
    'PATCH-v2-3: consecutive error tracking + heartbeat on error',
    'PATCH-v2-4: extended metadata suffix filter',
    'PATCH-v3-1: hysteresis (restrictive=fast, relax=debounced)',
    'PATCH-v3-2: alert cooldown per (from,to) pair',
    'PATCH-v3-3: audit stream records raw_gate alongside emitted gate',
    'PATCH-v4 (Manus 2026-04-26): global alert min-interval + flap quarantine',
  ],
  global_alert_min_interval_ms: GLOBAL_ALERT_MIN_INTERVAL_MS,
  flap_window_ms: FLAP_WINDOW_MS,
  flap_max_transitions: FLAP_MAX_TRANSITIONS,
  flap_quarantine_ms: FLAP_QUARANTINE_MS,
});

evaluate();
setInterval(evaluate, EVAL_INTERVAL_MS);

process.on('SIGINT', async () => {
  log('INFO', 'FINAL_TRADE_GATE: shutting down — gate will expire via TTL (fail-closed)');
  await redis.quit().catch(() => {});
  process.exit(0);
});
process.on('SIGTERM', async () => {
  await redis.quit().catch(() => {});
  process.exit(0);
});
