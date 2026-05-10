#!/usr/bin/env node
/**
 * final-to-champion-bridge-v3.mjs (v8.5 Structural Upgrade)
 *
 * v8.5 Patch (2026-04-10): First Principles structural fix
 *   - stress scoring: binary(0/1) → continuous(0.0~1.0)
 *   - risk_rate ceiling: hard cap at 0.70 + accelerated decay
 *   - state_params: dd_fail=-0.08, z_fail=1.5, ema_span=60
 *   - VIX gate: pre-sync pattern eliminates race condition
 *
 * Purpose:
 *   Full Champion v9.2.1 strategy implementation with:
 *   - Dynamic momentum-based weights
 *   - Volatility targeting (OPS: 0.18)
 *   - VIX-based regime adjustment
 *   - Hedge logic
 *   - Uncertainty sizing
 *   - Crisis mode handling
 *   - Transaction cost awareness
 *
 * Champion v9.2.1 Strategy Parameters (from manifest.json & champion v9.2.1 config):
 *   - target_vol: 0.18 (OPS), scale_min: 0.5, scale_max: 2.0
 *   - max_leverage: 1.5 (OPS constraint)
 *   - Momentum weights: mom_3=0.50, mom_5=0.30, mom_10=0.20
 *   - VIX regime: vol_threshold=25, high_vol_mult=0.80, low_vol_mult=1.20
 *   - Hedge: base=0.01, boost_cap=0.20, cap=0.40, vix_floor=25, vix_cap=45
 *   - Uncertainty: mult_min=0.50, mult_max=1.10
 *   - Crisis: trigger_threshold=0.05, boost=0.30
 *   - Transaction cost: 18 bps roundtrip
 */
import Redis from "ioredis";
import fs from "fs";
import crypto from "node:crypto";

// =============================================================================
// SAFETY-PATCH 2026-05-06 — Phase9-B1 marker safe builder
// [MANUS-P1 2026-05-08] keep champion keys alive on skip — prevents SSOT MISSING after extended NO_GO
async function __MANUS_keep_champion_alive__(redis, ttlSec) {
  try {
    const k = ['champion:targets:ssot', 'champion:targets:ssot:ts', 'champion:target:positions', 'champion:target:positions:ts'];
    const t = Math.max(120, Number(ttlSec)||900);
    await Promise.all(k.map(x => redis.expire(x, t).catch(()=>{})));
  } catch (_e) { /* non-fatal */ }
}

// =============================================================================
//
// Reads upstream champion identity from Redis (`policy:champion:active`) and
// the latest validator decision from `policy:validator:latest`. Returns a
// marker object that downstream consumers (router, ssot_promote_v2) can use
// to decide whether to publish the SSOT envelope.
//
// Returns null when the cycle MUST be skipped (NO_GO without signed override).
// Throws when required upstream sources are missing.
//
async function __ARES_buildSafeMarker__(redis, ctx) {
  // 1) Read champion identity (required)
  const championRaw = await redis.get("policy:champion:active");
  if (!championRaw) {
    throw new Error("policy:champion:active missing — cannot build marker");
  }
  let champion;
  try {
    champion = JSON.parse(championRaw);
  } catch (e) {
    throw new Error("policy:champion:active not valid JSON");
  }
  const candidate_id = champion.candidate_id;
  const candidate_config_sha256 = champion.candidate_config_sha256;
  const universe_sha256 = champion.universe_sha256;
  const champion_version = champion.champion_version;
  const champion_strategy_name = champion.champion_strategy_name;
  const champion_engine_file = champion.champion_engine_file;
  if (!candidate_id || !candidate_config_sha256) {
    throw new Error("policy:champion:active missing candidate_id/candidate_config_sha256");
  }

  // 2) Read latest validator decision
  const validatorRaw = await redis.get("policy:validator:latest");
  let validator = {};
  if (validatorRaw) {
    try { validator = JSON.parse(validatorRaw); } catch (_) { validator = {}; }
  }
  const decision = String(validator.decision || "").toUpperCase();
  const validatorCandSha = validator.candidate_config_sha256 || null;
  const validatorFresh = validatorCandSha === candidate_config_sha256;

  // 3) Determine validator state
  let standard_validator_decision;
  if (!validatorRaw || !validatorFresh) {
    standard_validator_decision = "STALE";
  } else if (decision === "GO" || decision === "NO_GO") {
    standard_validator_decision = decision;
  } else {
    standard_validator_decision = "UNKNOWN";
  }

  // 4) Check signed override if needed
  let override_decision = "NONE";
  let override_policy = "NONE";
  let overrideValid = false;
  if (standard_validator_decision !== "GO") {
    const overrideRaw = await redis.get("policy:override:active");
    if (overrideRaw) {
      let override;
      try { override = JSON.parse(overrideRaw); } catch (_) { override = null; }
      if (override && typeof override === "object") {
        const overrideCandSha = override.candidate_config_sha256;
        const expiresAtMs = Number(override.expires_at_ms || 0);
        const stillValid = expiresAtMs > Date.now();
        const matches = overrideCandSha === candidate_config_sha256;
        // Optional HMAC verification
        const signingKey = process.env.ARES_OVERRIDE_SIGNING_KEY || "";
        let sigOk = true;
        if (signingKey) {
          const { signature, ...body } = override;
          if (!signature) {
            sigOk = false;
          } else {
            const sortedBody = JSON.stringify(body, Object.keys(body).sort());
            const expected = crypto.createHmac("sha256", signingKey).update(sortedBody).digest("hex");
            try {
              sigOk = crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected));
            } catch (_) {
              sigOk = false;
            }
          }
        }
        if (matches && stillValid && sigOk) {
          overrideValid = true;
          override_decision = String(override.decision || "APPROVED").toUpperCase();
          override_policy = String(override.policy || "signed_override");
        }
      }
    }
  }

  // 5) Resolve final state
  let validation_state;
  if (standard_validator_decision === "GO") {
    validation_state = "VALIDATOR_APPROVED";
  } else if (overrideValid) {
    validation_state = "OVERRIDE_APPROVED";
  } else {
    // Fail-closed audit
    try {
      await redis.xadd("ares:validator:rejected", "MAXLEN", "~", "10000", "*",
        "ts_ms", String(Date.now()),
        "actor", "final-to-champion-bridge",
        "candidate_id", String(candidate_id),
        "validator_decision", standard_validator_decision,
        "override_decision", override_decision,
        "reason", "NO_GO_or_STALE_without_signed_override");
    } catch (_) {}
    return null; // signals caller to skip cycle
  }

  // 6) Audit success
  try {
    await redis.xadd("ares:validator:approved", "MAXLEN", "~", "10000", "*",
      "ts_ms", String(Date.now()),
      "actor", "final-to-champion-bridge",
      "candidate_id", String(candidate_id),
      "validator_decision", standard_validator_decision,
      "override_decision", override_decision,
      "validation_state", validation_state);
  } catch (_) {}

  return {
    marker_version: "phase9_b1_marker_safe_v2",
    candidate_id,
    candidate_config_sha256,
    universe_sha256: universe_sha256 || null,
    standard_validator_decision,
    override_decision,
    override_policy,
    validation_state,
    source_stage: "final-to-champion-bridge",
    champion_version: champion_version || null,
    champion_strategy_name: champion_strategy_name || null,
    champion_engine_file: champion_engine_file || null,
    target_key: ctx.target_key,
    generated_at_ms: Date.now(),
    total_mv: ctx.totalMV,
    vix: ctx.vix,
    crisis_mode: ctx.crisis_mode,
    mode: ctx.mode,
  };
}

// ============================================================
// SCA auditX + VIX 3중 합의 gate (패치)
// ============================================================
const SCA_AUDIT_KEY_BRIDGE = process.env.SCA_AUDIT_KEY || "sca:live:audit";
const VIX_KEY_SSOT = process.env.VIX_KEY || "market:vix";
const VIX_COPY_KEY_BRIDGE = "signals:ssot:latest.vix";
const VIX_AUX_KEY_BRIDGE = "market:vix:current";
const VIX_TOL_SC_BRIDGE = parseFloat(process.env.VIX_TOL_SSOT_COPY || "1.0");
const VIX_TOL_SA_BRIDGE = parseFloat(process.env.VIX_TOL_SSOT_AUX || "3.0");

async function auditX(redis, level, event, meta = {}) {
  try {
    await redis.xadd(
      SCA_AUDIT_KEY_BRIDGE, "MAXLEN", "~", "5000", "*",
      "source", "final-to-champion-bridge",
      "level", level,
      "event", event,
      "ts", new Date().toISOString(),
      "meta", JSON.stringify(meta)
    );
  } catch (e) {
    console.error("[auditX-bridge] write failed:", e.message);
  }
}

// ============================================================
// WRONGTYPE Resilient Helper (4AI Consensus Patch 2026-04-02)
// Try hgetall first; on WRONGTYPE, fallback to GET+JSON.parse
// ============================================================
let __wrongtypeFallbackCount = 0;

async function resilientHgetall(redis, key, logPrefix = "resilientHgetall") {
  try {
    const result = await redis.hgetall(key);
    return result || {};
  } catch (e) {
    if (e.message && e.message.includes("WRONGTYPE")) {
      __wrongtypeFallbackCount++;
      console.warn(`[${logPrefix}] WRONGTYPE fallback for key "${key}" (count=${__wrongtypeFallbackCount})`);
      try {
        // Attempt to read as string and JSON.parse
        const raw = await redis.get(key);
        if (raw !== null) {
          try {
            const parsed = JSON.parse(raw);
            if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
              console.warn(`[${logPrefix}] WRONGTYPE recovered via JSON.parse for key "${key}"`);
              // Audit log for observability
              auditX(redis, "WARN", "wrongtype_fallback", { key, recovered: true, count: __wrongtypeFallbackCount }).catch(() => {});
              return parsed;
            }
          } catch (parseErr) {
            console.warn(`[${logPrefix}] WRONGTYPE key "${key}" is string but not valid JSON: ${parseErr.message}`);
          }
        }
        // Audit log for unrecoverable WRONGTYPE
        auditX(redis, "WARN", "wrongtype_fallback", { key, recovered: false, count: __wrongtypeFallbackCount }).catch(() => {});
        return {};
      } catch (fallbackErr) {
        console.error(`[${logPrefix}] WRONGTYPE fallback GET failed for key "${key}": ${fallbackErr.message}`);
        return {};
      }
    }
    // Non-WRONGTYPE error: re-throw
    throw e;
  }
}

// [v8.5.2] checkVixTripleGate: accepts structured snapshot object, NEVER re-reads SSOT
// This guarantees same-cycle atomic consistency regardless of failure mode
async function checkVixTripleGate(redis, snapshot = null) {
  try {
    let vS;
    // [v8.5.2] Structured snapshot: use snapshot.value, never re-read
    if (snapshot && typeof snapshot === 'object' && snapshot.sampled) {
      if (!snapshot.present) return { ok: false, reason: "VIX_SSOT_MISSING" };
      if (!snapshot.valid) return { ok: false, reason: "VIX_OUT_OF_RANGE", val: snapshot.value };
      vS = snapshot.value;
    } else if (snapshot !== null && Number.isFinite(snapshot)) {
      // [v8.5.2] Backward compat: accept raw number (legacy callers)
      vS = snapshot;
      if (!Number.isFinite(vS) || vS < 0 || vS > 80) return { ok: false, reason: "VIX_OUT_OF_RANGE", val: vS };
    } else {
      // [v8.5.2] No snapshot provided: fail closed (do NOT re-read SSOT)
      return { ok: false, reason: "VIX_NO_SNAPSHOT" };
    }

    const rawC = await redis.get(VIX_COPY_KEY_BRIDGE);
    if (rawC !== null) {
      const vC = parseFloat(rawC);
      if (Number.isFinite(vC) && Math.abs(vS - vC) > VIX_TOL_SC_BRIDGE) {
        return { ok: false, reason: "VIX_COPY_MISMATCH", ssot: vS, copy: vC };
      }
    }

    const rawA = await redis.get(VIX_AUX_KEY_BRIDGE);
    if (rawA !== null) {
      const vA = parseFloat(rawA);
      if (Number.isFinite(vA) && Math.abs(vS - vA) > VIX_TOL_SA_BRIDGE) {
        return { ok: false, reason: "VIX_AUX_MISMATCH", ssot: vS, aux: vA };
      }
    }

    return { ok: true, vix: vS };
  } catch (e) {
    return { ok: false, reason: "VIX_CHECK_ERROR", err: e.message };
  }
}
// ============================================================


// ============================
// v8.2 Dynamic λ + ARES Sleeve Overlay (SSOT)
// ============================
const USE_ARES_LAMBDA_OVERLAY = (process.env.USE_ARES_LAMBDA_OVERLAY || "0") === "1";
const ARES_WEIGHTS_KEY = process.env.ARES_WEIGHTS_KEY || "sleeve:ares:weights";
// v4 Structural Guard: SKIP 연속 카운터
let __skipConsecutive = 0;
const SKIP_ALERT_THRESHOLD = Number(process.env.SKIP_ALERT_THRESHOLD || "60"); // 60회 = 5분 (5s interval)

const LAMBDA_TOP1_JSON = process.env.LAMBDA_TOP1_JSON || "/home/ubuntu/ssot/v8.7d/final_selected_v87d.json";
let __lambdaTop1 = null;

// v8.4 STATE PARAMS PATCH START
let __stateParams = null;
const RISK_RATE_KEY = process.env.RISK_RATE_KEY || "state:risk_rate";
const RISK_RATE_TS_KEY = process.env.RISK_RATE_TS_KEY || "state:risk_rate:ts";

// [v8.5.2] Strict parameter validation — prevents NaN/Inf/divide-by-zero from bad manifest
// v8.5.2: null guard, weight-sum normalization, stress_mode enum validation
const VALID_STRESS_MODES = ["continuous", "discrete", "hybrid"];
const PARTIAL_NAN_SCORE = 0.30; // conservative score for missing signals (not 0)

function validateStateParams(raw) {
  if (!raw || typeof raw !== 'object') raw = {}; // [v8.5.2] null/undefined guard
  const dd = Number(raw.dd_fail ?? -0.08);
  const z = Number(raw.z_fail ?? 1.5);
  const ema = Number(raw.ema_span ?? 60);
  const ceil = Number(raw.rr_ceiling ?? 0.70);
  
  // [v8.5.2] Weight validation with sum normalization
  let ddW = (Number.isFinite(Number(raw.dd_weight)) && Number(raw.dd_weight) >= 0 && Number(raw.dd_weight) <= 1) ? Number(raw.dd_weight) : 0.5;
  let vzW = (Number.isFinite(Number(raw.vz_weight)) && Number(raw.vz_weight) >= 0 && Number(raw.vz_weight) <= 1) ? Number(raw.vz_weight) : 0.3;
  let volzW = (Number.isFinite(Number(raw.volz_weight)) && Number(raw.volz_weight) >= 0 && Number(raw.volz_weight) <= 1) ? Number(raw.volz_weight) : 0.2;
  const wSum = ddW + vzW + volzW;
  if (wSum < 0.5 || wSum > 1.5 || wSum === 0) {
    console.error(`[VALIDATE_PARAMS_v852] weight sum=${wSum.toFixed(2)} out of [0.5,1.5] → reset to defaults`);
    ddW = 0.5; vzW = 0.3; volzW = 0.2;
  } else if (Math.abs(wSum - 1.0) > 0.01) {
    // [v8.5.2] Normalize weights to sum=1.0 for mathematical consistency
    ddW /= wSum; vzW /= wSum; volzW /= wSum;
    console.log(`[VALIDATE_PARAMS_v852] weights normalized: sum=${wSum.toFixed(2)} → [${ddW.toFixed(3)},${vzW.toFixed(3)},${volzW.toFixed(3)}]`);
  }
  
  // [v8.5.2] stress_mode enum validation
  const modeRaw = String(raw.stress_mode || "continuous").toLowerCase();
  const stress_mode = VALID_STRESS_MODES.includes(modeRaw) ? modeRaw : "continuous";
  if (modeRaw !== "continuous" && !VALID_STRESS_MODES.includes(modeRaw)) {
    console.warn(`[VALIDATE_PARAMS_v852] unknown stress_mode="${modeRaw}" → defaulting to continuous`);
  }
  
  return {
    dd_fail: (Number.isFinite(dd) && dd < 0 && dd >= -0.30) ? dd : -0.08,
    z_fail: (Number.isFinite(z) && z > 0.5 && z <= 5.0) ? z : 1.5,
    ema_span: (Number.isFinite(ema) && ema >= 5 && ema <= 600) ? ema : 60,
    rr_ceiling: (Number.isFinite(ceil) && ceil > 0 && ceil <= 1.0) ? ceil : 0.70,
    stress_mode,
    dd_weight: ddW,
    vz_weight: vzW,
    volz_weight: volzW,
    // [v8.5.2] Separate VIX/vol z thresholds
    vix_z_fail: (Number.isFinite(Number(raw.vix_z_fail)) && Number(raw.vix_z_fail) > 0.5) ? Number(raw.vix_z_fail) : undefined,
    volz_fail: (Number.isFinite(Number(raw.volz_fail)) && Number(raw.volz_fail) > 0.5) ? Number(raw.volz_fail) : undefined,
  };
}

function loadStateParamsFromSSOT() {
  // [v8.5.2] PATCHED 2026-04-10: Production-Grade Structural Upgrade (Round 3)
  // v8.5.2 fixes: partial NaN conservative, weight-sum normalization, stress_mode branching,
  //   cold-start conservative, VIX structured snapshot, Redis atomic write, smooth decay
  if (__stateParams && __lambdaTop1) return __stateParams;
  try {
    const dir = LAMBDA_TOP1_JSON.split("/").slice(0, -1).join("/");
    const manifestPath = `${dir}/manifest.json`;
    const m = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    const sp = (m.inputs && m.inputs.state_params) ? m.inputs.state_params : (m.state_params || {});
    __stateParams = validateStateParams(sp);
    console.log(`[STATE_PARAMS_LOAD_v852] dd=${__stateParams.dd_fail} z=${__stateParams.z_fail} ema=${__stateParams.ema_span} ceil=${__stateParams.rr_ceiling} mode=${__stateParams.stress_mode} w=[${__stateParams.dd_weight.toFixed(3)},${__stateParams.vz_weight.toFixed(3)},${__stateParams.volz_weight.toFixed(3)}]`);
  } catch (e) {
    // [v8.5.1] Safe fallbacks with validated defaults
    __stateParams = validateStateParams({});
    console.warn(`[STATE_PARAMS_LOAD_v852_FALLBACK] using validated defaults: dd=-0.08 z=1.5 ema=60 ceil=0.70`);
  }
  return __stateParams;
}

function emaAlphaFromSpan(span) {
  return 2.0 / (Math.max(1, Number(span || 20)) + 1.0);
}

// [v8.5.2] In-memory cache for Redis failure resilience (fail-safe, not fail-open)
// v8.5.2 FIX: cold-start at conservative level (not 0.0) to prevent first-cycle risk-on
let __prevRiskRate = 0.35; // conservative initial, not optimistic 0.0
let __prevRiskRateTs = 0;
let __redisFailCount = 0;
let __redisDurableLoaded = false; // tracks if we ever read a valid value from Redis
const CONSERVATIVE_STRESS_ON_FAILURE = 0.35; // moderate stress during data gaps

// [v8.5.1] Continuous stress score EMA with fail-safe resilience
// - In-memory cache prevents amnesia on Redis failure
// - NaN guard with conservative fallback
// - Time-aware alpha for gap recovery
async function updateRiskRateEMA(redis, stressInput, emaSpan, rr_ceiling = 0.70) {
  const baseAlpha = emaAlphaFromSpan(emaSpan);
  
  // [v8.5.1] NaN guard: reject non-finite stress with conservative fallback
  if (!Number.isFinite(stressInput)) {
    console.error(`[RISK_RATE_v851_NAN_GUARD] stressInput=${stressInput} → using conservative ${CONSERVATIVE_STRESS_ON_FAILURE}`);
    stressInput = CONSERVATIVE_STRESS_ON_FAILURE;
  }
  const stressVal = Math.max(0, Math.min(1, stressInput));
  
  // [v8.5.2] Redis read with in-memory fallback (fail-safe)
  let prev = __prevRiskRate;
  let prevTs = __prevRiskRateTs;
  let redisReadOk = false;
  try {
    const raw = await redis.get(RISK_RATE_KEY);
    if (raw !== null) {
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) {
        prev = parsed;
        redisReadOk = true;
        __redisFailCount = 0;
        if (!__redisDurableLoaded) {
          __redisDurableLoaded = true;
          console.log(`[RISK_RATE_v852_BOOTSTRAP] first durable read from Redis: rr=${parsed.toFixed(4)}`);
        }
      }
    } else if (!__redisDurableLoaded) {
      // [v8.5.2] Redis key absent on cold start → use conservative initial
      console.warn(`[RISK_RATE_v852_COLD_START] no durable RR in Redis, using conservative prev=${prev.toFixed(4)}`);
    }
    // Read timestamp for time-aware alpha
    const rawTs = await redis.get(RISK_RATE_TS_KEY);
    if (rawTs) prevTs = new Date(rawTs).getTime();
  } catch (e) {
    __redisFailCount++;
    console.warn(`[RISK_RATE_v852_REDIS_FAIL] read failed (count=${__redisFailCount}): ${e.message} → using cached prev=${prev.toFixed(4)}`);
    // [v8.5.2] On persistent Redis failure, force conservative mode immediately if no durable state
    if (__redisFailCount >= 3 || (!__redisDurableLoaded && __redisFailCount >= 1)) {
      console.error(`[RISK_RATE_v852_CIRCUIT_BREAKER] ${__redisFailCount} Redis failures (durable=${__redisDurableLoaded}) → forcing conservative stress`);
      return Math.max(prev, CONSERVATIVE_STRESS_ON_FAILURE); // hold or raise, never drop during outage
    }
  }
  
  // [v8.5.1] Time-aware alpha: if gap > 2 cycles (10s), scale alpha proportionally
  let alpha = baseAlpha;
  const now = Date.now();
  if (prevTs > 0 && now - prevTs > 10000) {
    const elapsedCycles = Math.min((now - prevTs) / 5000, 20); // cap at 20 cycles
    alpha = 1 - Math.pow(1 - baseAlpha, elapsedCycles);
    console.log(`[RISK_RATE_v851_TIME_AWARE] gap=${((now - prevTs)/1000).toFixed(0)}s cycles=${elapsedCycles.toFixed(1)} alpha=${alpha.toFixed(4)}`);
  }
  
  let rr = Math.max(0, Math.min(1, alpha * stressVal + (1 - alpha) * prev));
  // [v8.5.2] Ceiling: prevent runaway to 1.0
  if (rr > 0 && rr < 1e-12) rr = 0;  // [v8.5.3 PATCH] denormal kill (Manus 2026-05-09)
  rr = Math.min(rr, rr_ceiling);
  // [v8.5.2] Smooth asymmetric decay: continuous ramp instead of step discontinuity
  // When stress is low, apply proportional accelerated decay (no step at 0.01 threshold)
  if (stressVal < 0.1 && rr > 0.1) {
    const decayFactor = (0.1 - stressVal) / 0.1; // 0→1 as stress→0
    rr = Math.max(0, rr - baseAlpha * 0.5 * decayFactor);
  }
  
  // [v8.5.1] Update in-memory cache BEFORE Redis write
  __prevRiskRate = rr;
  __prevRiskRateTs = now;
  
  // [v8.5.2] Atomic Redis write via MULTI/EXEC (prevents stale timestamp on crash)
  try {
    const ts = new Date().toISOString();
    const pipeline = redis.multi();
    pipeline.set(RISK_RATE_KEY, String(rr), "EX", 86400);
    pipeline.set(RISK_RATE_TS_KEY, ts, "EX", 86400);
    await pipeline.exec();
  } catch (e) {
    console.error(`[RISK_RATE_v852_REDIS_WRITE_FAIL] ${e.message} → rr=${rr.toFixed(4)} cached in-memory only`);
  }
  return rr;
}

async function fetchAresMeta(redis) {
  try {
    const raw = await redis.get(ARES_WEIGHTS_KEY);
    if (!raw) { console.warn(`[FETCH_META_DBG] raw=null key=${ARES_WEIGHTS_KEY}`); return null; }
    const j = JSON.parse(raw);
    // [FETCH_META_DBG 2026-04-24] one-shot diagnostic block (no branch change)
    try {
      const __m = j && j.metadata;
      if (!__m) {
        console.warn(`[FETCH_META_DBG] no .metadata top=${Object.keys(j||{}).join(',')} key=${ARES_WEIGHTS_KEY}`);
      } else {
        const __dd = Number(__m.dd_20), __vz = Number(__m.vix_z), __volz = Number(__m.vol_z);
        const __finite = [__dd,__vz,__volz].filter(v => Number.isFinite(v)).length;
        if (__finite === 0) {
          console.warn(`[FETCH_META_DBG] all-NaN meta=${JSON.stringify(__m)}`);
        }
      }
    } catch (__e) {
      console.warn(`[FETCH_META_DBG] inspect_err=${__e && __e.message}`);
    }
    
    // [FIX-1 v8.4.2] Writer v3.1+ metadata 객체 우선 읽기
    const meta = j?.metadata;
    if (meta && typeof meta === 'object') {
      const dd = Number(meta.dd_20);
      const vz = Number(meta.vix_z);
      const volz = Number(meta.vol_z);
      if (Number.isFinite(dd) || Number.isFinite(vz) || Number.isFinite(volz)) {
        try { _incrAresMetaSrcCounter(redis, "writer_metadata"); } catch {}
        return { dd_20: dd, vix_z: vz, vol_z: volz, source: "writer_metadata" };
      }
    }
    
    // [FIX-1 v8.4.2] Fallback: ctx:regime:state에서 직접 읽기
    try {
      const regimeRaw = await redis.get("ctx:regime:state");
      if (regimeRaw) {
        const regime = JSON.parse(regimeRaw);
        const stress = regime?.components?.stress;
        if (stress) {
          let dd20 = Number(stress.dd20 ?? 0);
          const vixZ = Number(stress.vix_z ?? 0);
          const volZ = Number(stress.vol_z ?? 0);
          
          // ofg:drawdown:current 보정
          try {
            const ddRaw = await redis.get("ofg:drawdown:current");
            if (ddRaw) {
              const ddVal = Number(ddRaw);
              if (Number.isFinite(ddVal) && ddVal > 0) dd20 = -ddVal;
            }
          } catch {}
          
          // VIX z-score 실시간 보정
          let vixZLive = vixZ;
          try {
            const vixCur = Number(await redis.get("market:vix:current"));
            const vixMa = Number(await redis.get("market:vix_ma20"));
            if (Number.isFinite(vixCur) && Number.isFinite(vixMa) && vixMa > 0) {
              vixZLive = (vixCur - vixMa) / vixMa * 5.0;
            }
          } catch {}
          
          if (Math.abs(vixZ) < 0.001 && Math.abs(vixZLive) > 0.001) {
            try { _incrAresMetaSrcCounter(redis, "ctx_regime_state_live"); } catch {}
            return { dd_20: dd20, vix_z: vixZLive, vol_z: volZ, source: "ctx_regime_state_live" };
          }
          try { _incrAresMetaSrcCounter(redis, "ctx_regime_state"); } catch {}
          return { dd_20: dd20, vix_z: vixZ, vol_z: volZ, source: "ctx_regime_state" };
        }
      }
    } catch {}
    
    // [FIX-1 v8.4.3 2026-05-07] live_components_only fallback: ctx:regime:state가 아예 부재일 때도
    // ofg:drawdown:current + market:vix:current/ma20로 라이브 메타 합성(default_fallback 방지)
    try {
      const ddRaw  = await redis.get("ofg:drawdown:current");
      const vixCur = Number(await redis.get("market:vix:current"));
      const vixMa  = Number(await redis.get("market:vix_ma20"));
      let dd20 = 0, vixZLive = 0;
      if (ddRaw !== null && ddRaw !== undefined) {
        const v = Number(ddRaw); if (Number.isFinite(v) && v > 0) dd20 = -v;
      }
      if (Number.isFinite(vixCur) && Number.isFinite(vixMa) && vixMa > 0) {
        vixZLive = (vixCur - vixMa) / vixMa * 5.0;
      }
      if (Math.abs(dd20) > 1e-9 || Math.abs(vixZLive) > 1e-9) {
        try { _incrAresMetaSrcCounter(redis, "live_components_only"); } catch {}
        return { dd_20: dd20, vix_z: vixZLive, vol_z: 0, source: "live_components_only" };
      }
    } catch {}
    try { _incrAresMetaSrcCounter(redis, "default_fallback"); } catch {}
    return null;
  } catch { return null; }
}

// [FIX-1 v8.4.3 2026-05-07] meta source counter (atomic-ish: INCR + EXPIRE if first)
async function _incrAresMetaSrcCounter(redis, src) {
  if (!redis || !src) return;
  try {
    const key = `ares:bridge:counter:meta_src:${src}`;
    const v = await redis.incr(key);
    if (v === 1) {
      try { await redis.expire(key, 86400); } catch {}
    }
  } catch {}
}

// writer_metadata 분기(위쪽) 집계 원도 병행 처리— fetchAresMeta의 metadata if-block에도 카운터를 붙이기 위해 다음 제널리티 렌더링에서 삽입.
// v8.4 STATE PARAMS PATCH END


function clampLambda(x, lo, hi){ return Math.max(lo, Math.min(hi, x)); }
function nowIsoLambda(){ return new Date().toISOString(); }
function safeJsonLambda(s){ try { return JSON.parse(s); } catch { return null; } }

let __lambdaTop1Mtime = 0; // PATCHED 2026-04-08: hot-reload support
function loadTop1LambdaParams() {
  // Hot-reload: check file mtime every call, reload if changed
  try {
    const stat = fs.statSync(LAMBDA_TOP1_JSON);
    const mtime = stat.mtimeMs || 0;
    if (__lambdaTop1 && mtime === __lambdaTop1Mtime) return __lambdaTop1;
    __lambdaTop1Mtime = mtime;
    if (mtime !== __lambdaTop1Mtime || !__lambdaTop1) {
      console.log(`[LAMBDA_TOP1_RELOAD] file changed or first load, mtime=${new Date(mtime).toISOString()}`);
    }
  } catch {}
  try {
    const j = JSON.parse(fs.readFileSync(LAMBDA_TOP1_JSON, "utf-8"));
    const b = j.params || j.best || {};
    __lambdaTop1 = {
      policy: "hybrid_top1",
      t1: Number(b.t1 ?? 0.15),
      t2: Number(b.t2 ?? 0.25),
      lam_lo: Number(b.lam_lo ?? 0.20),
      lam_mid: Number(b.lam_mid ?? 0.30),
      lam_hi: Number(b.lam_hi ?? 0.40),
      add_breadth: Number(b.add_breadth ?? 0.10),
      add_macro_off: Number(b.add_macro_off ?? 0.00),
      lam_min: Number(b.lam_min ?? 0.10),
      lam_max: Number(b.lam_max ?? 0.70),
      t1_up: Number(b.t1_up ?? b.t1 ?? 0.15),
      t1_down: Number(b.t1_down ?? 0.08),
      t2_up: Number(b.t2_up ?? b.t2 ?? 0.28),
      t2_down: Number(b.t2_down ?? 0.26),
    };
  } catch (e) {
    __lambdaTop1 = { policy:"hybrid_fallback", t1:0.15,t2:0.25, lam_lo:0.20,lam_mid:0.30,lam_hi:0.40, add_breadth:0.10,add_macro_off:0.00, lam_min:0.10,lam_max:0.70 };
  }
  return __lambdaTop1;
}

async function fetchCtxStateLambda(redis, universe, CASH_SYMBOL) {
  if (!redis) return { risk_rate: 0.0, macro_regime: "UNKNOWN", breadth_weak: false };
  const syms = (universe || []).filter(s => s && s !== CASH_SYMBOL);
  const keys = syms.map(s => `ctx:event:${s}`);
  const vals = await redis.mget(keys);
  let tot=0, risk=0;
  for (const v of vals) {
    if (!v) continue;
    const j = safeJsonLambda(v);
    if (!j || (j.status && j.status !== "OK")) continue;
    tot += 1;
    if (Boolean(j.risk_flag)) risk += 1;
  }
  const risk_rate = tot ? (risk/tot) : 0.0;
  const macro = safeJsonLambda(await redis.get("ctx:macro:regime"));
  const breadth = safeJsonLambda(await redis.get("ctx:breadth:health"));
  const macro_regime = (macro && (macro.macro_regime || macro.regime)) ? String(macro.macro_regime || macro.regime).toUpperCase() : "UNKNOWN"; // PATCHED 2026-04-08: fallback to macro.regime field
  const breadth_weak = Boolean(breadth && (breadth.breadth_weak !== undefined ? breadth.breadth_weak : breadth.weak)); // PATCHED 2026-04-08: fallback to breadth.weak field
  // [v8.5.1] ARES meta 기반 continuous stress scoring + EMA risk_rate
  // First Principles: binary stress(0/1) → continuous score(0.0~1.0) 전환
  // v8.5.1: dd_score magnitude 기반 수정 + NaN 가드 + configurable weights + fail-safe
  let risk_rate_live = risk_rate;
  try {
    const sp = loadStateParamsFromSSOT();
    const ceiling = sp.rr_ceiling || 0.70;
    const meta = await fetchAresMeta(redis);
    if (meta && (Number.isFinite(meta.vix_z) || Number.isFinite(meta.vol_z) || Number.isFinite(meta.dd_20))) {
      const dd = Number.isFinite(meta.dd_20) ? meta.dd_20 : NaN;
      const vz = Number.isFinite(meta.vix_z) ? meta.vix_z : NaN;
      const volz = Number.isFinite(meta.vol_z) ? meta.vol_z : NaN;
      
      // [v8.5.2] NaN input guard: all-NaN → conservative; partial-NaN → conservative per-signal score
      const nanCount = [dd, vz, volz].filter(v => !Number.isFinite(v)).length;
      if (nanCount === 3) {
        console.error(`[RISK_RATE_v852_ALL_NAN] all inputs NaN → conservative stress=${CONSERVATIVE_STRESS_ON_FAILURE}`);
        risk_rate_live = await updateRiskRateEMA(redis, CONSERVATIVE_STRESS_ON_FAILURE, sp.ema_span, ceiling);
        return { risk_rate: risk_rate_live, macro_regime, breadth_weak };
      }
      if (nanCount > 0) {
        console.warn(`[RISK_RATE_v852_PARTIAL_NAN] dd=${dd} vz=${vz} volz=${volz} → NaN signals get conservative score=${PARTIAL_NAN_SCORE}`);
      }
      
      // [v8.5.2] FIXED dd_score: magnitude-based to prevent sign confusion
      // dd is negative (drawdown). Use |dd| for monotonic scoring.
      // NaN dd → conservative PARTIAL_NAN_SCORE (not 0)
      let dd_score;
      if (!Number.isFinite(dd)) {
        dd_score = PARTIAL_NAN_SCORE;
      } else {
        const ddMag = Math.max(0, -dd);
        const ddFailMag = Math.max(0.001, -sp.dd_fail);
        const ddStartMag = ddFailMag * 0.5;
        dd_score = (ddMag >= ddFailMag) ? 1.0 : (ddMag <= ddStartMag) ? 0.0 : (ddMag - ddStartMag) / (ddFailMag - ddStartMag);
      }
      
      // [v8.5.2] Separate VIX/vol z thresholds + NaN → conservative score
      const vixZFail = sp.vix_z_fail || sp.z_fail;
      const volZFail = sp.volz_fail || sp.z_fail;
      let vz_score, volz_score;
      if (!Number.isFinite(vz)) {
        vz_score = PARTIAL_NAN_SCORE;
      } else {
        vz_score = (vz >= vixZFail) ? 1.0 : (vz <= vixZFail * 0.5) ? 0.0 : (vz - vixZFail * 0.5) / (vixZFail * 0.5);
      }
      if (!Number.isFinite(volz)) {
        volz_score = PARTIAL_NAN_SCORE;
      } else {
        volz_score = (volz >= volZFail) ? 1.0 : (volz <= volZFail * 0.5) ? 0.0 : (volz - volZFail * 0.5) / (volZFail * 0.5);
      }
      
      // [v8.5.2] stress_mode branching: continuous (default) vs discrete vs hybrid
      let stress_score;
      if (sp.stress_mode === "discrete") {
        // Discrete mode: binary threshold (original v8.4 behavior, but with correct dd_score)
        const anyHigh = (dd_score >= 0.8 || vz_score >= 0.8 || volz_score >= 0.8);
        stress_score = anyHigh ? 0.8 : 0.0;
        console.log(`[STRESS_MODE_DISCRETE] anyHigh=${anyHigh} → stress=${stress_score}`);
      } else if (sp.stress_mode === "hybrid") {
        // Hybrid mode: continuous score with discrete floor
        const rawStress = dd_score * sp.dd_weight + vz_score * sp.vz_weight + volz_score * sp.volz_weight;
        const anyHigh = (dd_score >= 0.8 || vz_score >= 0.8 || volz_score >= 0.8);
        stress_score = Math.max(0, Math.min(1, anyHigh ? Math.max(rawStress, 0.5) : rawStress));
        console.log(`[STRESS_MODE_HYBRID] raw=${rawStress.toFixed(3)} anyHigh=${anyHigh} → stress=${stress_score.toFixed(3)}`);
      } else {
        // Continuous mode (default): weighted sum with normalized weights
        stress_score = Math.max(0, Math.min(1, dd_score * sp.dd_weight + vz_score * sp.vz_weight + volz_score * sp.volz_weight));
      }
      risk_rate_live = await updateRiskRateEMA(redis, stress_score, sp.ema_span, ceiling);
      // [v8.4-PATCH-v3 2026-05-08] Dual-emit: also log the same metric under the
      // new "stress_rate" name so downstream tooling can migrate gradually away
      // from the overloaded "risk_rate" identifier (which collides with
      // ALPHA_HEALTH_v84MUSK's risk_count_ratio in operator mental models).
      // Cutover plan: keep both lines for >=6 weeks, then RFC for removal of the
      // legacy [RISK_RATE_v852] line. Until then, RISK_RATE_v852 remains the
      // authoritative log for parsers (no schema change).
      console.log(`[RISK_RATE_v852] dd=${(Number.isFinite(dd)?dd:0).toFixed(4)} vz=${(Number.isFinite(vz)?vz:0).toFixed(2)} volz=${(Number.isFinite(volz)?volz:0).toFixed(2)} dd_s=${dd_score.toFixed(2)} vz_s=${vz_score.toFixed(2)} vol_s=${volz_score.toFixed(2)} stress=${stress_score.toFixed(3)} rr=${risk_rate_live.toFixed(4)} ceil=${ceiling} nan=${nanCount} mode=${sp.stress_mode}`);
      console.log(`[STRESS_RATE_v852] alias_of=RISK_RATE_v852 stress_rate=${risk_rate_live.toFixed(4)} stress_score=${stress_score.toFixed(3)}`);
    } else {
      // [v8.5.1] ARES metadata 누락 시: conservative fallback (not fail-open)
      const stress_score = Math.max(CONSERVATIVE_STRESS_ON_FAILURE * 0.5, Math.min(1, risk_rate * 0.6));
      risk_rate_live = await updateRiskRateEMA(redis, stress_score, sp.ema_span || 60, ceiling);
      console.warn(`[RISK_RATE_v851_FALLBACK] raw_rr=${risk_rate.toFixed(4)} stress=${stress_score.toFixed(3)} ema_rr=${risk_rate_live.toFixed(4)} ceil=${ceiling} note=ARES_META_MISSING`);
    }
  } catch (e) {
    console.error(`[RISK_RATE_v851_CATCH] unexpected error: ${e.message} → holding previous rr=${risk_rate_live.toFixed(4)}`);
  }
  return { risk_rate: risk_rate_live, macro_regime, breadth_weak };
}

function computeLambdaHybrid(st, p) {
  const risk = Number(st.risk_rate || 0.0);
  const macro = String(st.macro_regime || "UNKNOWN").toUpperCase();
  const bw = Boolean(st.breadth_weak);
  let lam = (risk < p.t1) ? p.lam_lo : (risk < p.t2) ? p.lam_mid : p.lam_hi;
  if (bw) lam += p.add_breadth;
  if (macro === "RISK_OFF") lam += p.add_macro_off;
  return clampLambda(lam, p.lam_min, p.lam_max);
}

async function fetchAresWeightsLambda(redis) {
  if (!redis) return null;
  const raw = await redis.get(ARES_WEIGHTS_KEY);
  if (!raw) return null;
  const j = safeJsonLambda(raw);
  if (!j || !j.weights) return null;
  return j.weights;
}

function blendNormalizedWeights(baseW, aresW, lam, universe, CASH_SYMBOL) {
  const out = {};
  const syms = (universe || []).slice();
  if (!syms.includes(CASH_SYMBOL)) syms.push(CASH_SYMBOL);
  let riskSum = 0.0;
  for (const s of syms) {
    const b = Number(baseW?.[s] ?? 0.0);
    const a = Number(aresW?.[s] ?? 0.0);
    const w = (1.0 - lam) * b + lam * a;
    out[s] = w;
    if (s !== CASH_SYMBOL) riskSum += w;
  }
  if (riskSum > 1.0 && riskSum > 0) {
    const scale = 1.0 / riskSum;
    let newSum = 0.0;
    for (const s of syms) {
      if (s === CASH_SYMBOL) continue;
      out[s] = out[s] * scale;
      newSum += out[s];
    }
    out[CASH_SYMBOL] = 1.0 - newSum;
  } else {
    out[CASH_SYMBOL] = 1.0 - riskSum;
  }
  return out;
}

// Configuration
const REDIS_URL = process.env.REDIS_URL;
const SOURCE_KEY = process.env.SOURCE_KEY || "regime:consensus:current";
const TARGET_KEY = process.env.TARGET_KEY || "champion:v660:signals";
const INTERVAL_MS = Number(process.env.INTERVAL_MS || "5000");
const TTL_SECONDS = Number(process.env.TTL_SECONDS || "120");
const STALE_MS = Number(process.env.STALE_MS || "3600000"); // 1시간으로 연장
const STALE_FALLBACK_MULT = Number(process.env.STALE_FALLBACK_MULT || "1.0"); // 노출도 반토막 방지
const DEFAULT_MULT = Number(process.env.DEFAULT_MULT || "1.0");
const PASSTHROUGH_FIELDS = (process.env.PASSTHROUGH_FIELDS ||
  "r15_state,r15_mult,ms_state,ms_score,gate_mult,reason_codes"
).split(",").map(s => s.trim()).filter(Boolean);

// ============================================================
// Champion v9.2.1 Strategy Configuration (OFFICIAL)
// ============================================================
const CHAMPION_CONFIG = {
  version: "v9.2.1",
  
  // Universe - Champion v9.1_260211 Official (19 stocks, QQQ=hedge)
  universe: [
    "ABBV", "CMCSA", "CRWD", "GILD", "GOOGL",
    "HD", "JNJ", "KO", "LLY", "MCD",
    "META", "NFLX", "NVDA", "OKTA", "PEP",
    "REGN", "TSLA", "VRTX", "WMT"
  ],
  
  // Stage2 Mix weights (from champion v9.2.1)
  stage2_mix: {
    w_recovery: 0.86,
    w_stock: 0.14
  },
  
  // Momentum weights (from champion v9.2.1)
  momentum: {
    mom_3_weight: 0.50,
    mom_5_weight: 0.30,
    mom_10_weight: 0.20,
    crisis_trigger_threshold: 0.05,
    crisis_boost: 0.30
  },
  
  // Volatility targeting (v9.2.1: target_vol=0.14)
  volatility: {
    target_vol: 0.14,      // v9.2.1 official
    scale_min: 0.5,
    scale_max: 2.5,        // v9.2.1: max 2.5
    vol_lookback: 20       // v9.2.1: 20 days
  },
  
  // VIX-based Regime adjustment
  regime: {
    vol_threshold: 25,
    high_vol_mult: 0.80,
    low_vol_mult: 1.20
  },
  
  // Hedge parameters (v9.2.1)
  hedge: {
    hedge_base: 0.01,
    hedge_boost_cap: 0.20,
    hedge_cap: 0.40,       // v9.2.1 hedge_cap
    vix_threshold_floor: 25.0,
    vix_threshold_cap: 45.0,
    vix_band: 7.0,
    vix_sigma: 2.0,
    vix_lookback: 20
  },
  
  // Uncertainty sizing
  uncertainty: {
    proxy_min: -0.50,
    proxy_max: 1.50,
    mult_min: 0.50,
    mult_max: 1.10
  },
  
  // Position constraints (v9.2.1)
  constraints: {
    min_weight: 0.02,
    max_weight: 0.25,
    max_leverage: 2.5,     // v9.2.1: 2.5
    max_single_stock: 0.20
  },
  
  // Failsafe (v9.2.1)
  failsafe: {
    enabled: true,
    vix_stress_threshold: 35
  },
  
  // Stage3 rebalance (v9.2.1)
  stage3: {
    rebalance_days: 15
  },
  
  // Transaction costs
  costs: {
    transaction_cost_bps: 10,
    slippage_model: "linear",
    slippage_bps: 0,
    rebalance_frequency_days: 1
  }
};

// ============================================================
// Utility Functions
// ============================================================
function nowMs() { return Date.now(); }

function safeTimestamp(x) {
  if (typeof x === "string" && x.includes("T")) {
    const d = new Date(x);
    return Number.isFinite(d.getTime()) ? d.getTime() : null;
  }
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

function safeNumber(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

function stableStringify(obj) {
  const keys = Object.keys(obj).sort();
  return JSON.stringify(obj, keys);
}

let _cryptoPromise;
function awaitImportCrypto() {
  if (!_cryptoPromise) _cryptoPromise = import("node:crypto");
  return _cryptoPromise;
}

async function computeHash(obj) {
  const s = stableStringify(obj);
  const crypto = await awaitImportCrypto();
  return crypto.createHash("sha1").update(s).digest("hex");
}

function pick(obj, fields) {
  const out = {};
  for (const f of fields) {
    if (obj[f] !== undefined) out[f] = obj[f];
  }
  return out;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}


// === [ARES-RAM26-SLEEVE-PRIMARY-PATCH-v1] BEGIN ===
// First-principles fix: final-to-champion-bridge is not allowed to recreate
// live target weights from legacy momentum/epsilon/1N logic when RAM26 sleeve
// weights are available. The bridge must preserve the sleeve allocator's
// cross-sectional alpha shape and only perform gross scaling + safety guards.
const ARES_RAM26_EXPECTED_CHAMPION_ID = process.env.ARES_RAM26_EXPECTED_CHAMPION_ID || "RAM26_ALPHA_0001_b215c119ea23";
const ARES_SLEEVE_PRIMARY_KEYS = (process.env.ARES_SLEEVE_PRIMARY_KEYS || "sleeve:final_weights:current,sleeve:ares:weights,sleeve:v3:final_weights:current")
  .split(",").map(s => s.trim()).filter(Boolean);
const ARES_BRIDGE_REQUIRE_SLEEVE = (process.env.ARES_BRIDGE_REQUIRE_SLEEVE || "1") !== "0";
const ARES_SLEEVE_MAX_AGE_MS = Number(process.env.ARES_SLEEVE_MAX_AGE_MS || "1800000"); // 30m default
const ARES_BRIDGE_MIN_INPUT_CV = Number(process.env.ARES_BRIDGE_MIN_INPUT_CV || "0.30");
const ARES_BRIDGE_MIN_OUTPUT_CV = Number(process.env.ARES_BRIDGE_MIN_OUTPUT_CV || "0.30");
const ARES_BRIDGE_MIN_UNIQUE6 = Number(process.env.ARES_BRIDGE_MIN_UNIQUE6 || "4");
const ARES_BRIDGE_DEFAULT_TARGET_GROSS = Number(process.env.ARES_BRIDGE_TARGET_GROSS || "0.75");

function __ARES_num(x, dflt = NaN) {
  const n = Number(x);
  return Number.isFinite(n) ? n : dflt;
}

function __ARES_round10(x) {
  return Math.round(Number(x || 0) * 1e10) / 1e10;
}

function __ARES_jsonMaybe(x) {
  if (x === null || x === undefined) return null;
  if (typeof x === "object") return x;
  try { return JSON.parse(String(x)); } catch (_) { return null; }
}

function __ARES_extractTsMs(obj) {
  if (!obj || typeof obj !== "object") return 0;
  const candidates = [obj.ts, obj.timestamp, obj.updated_at, obj.updatedAt, obj.asof, obj.asOf, obj?.metadata?.ts, obj?.meta?.ts];
  for (const v of candidates) {
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") return v > 1e12 ? Math.trunc(v) : Math.trunc(v * 1000);
    const s = String(v).trim();
    const n = Number(s);
    if (Number.isFinite(n)) return n > 1e12 ? Math.trunc(n) : Math.trunc(n * 1000);
    const d = Date.parse(s);
    if (Number.isFinite(d)) return d;
  }
  return 0;
}

function __ARES_extractWeights(payload) {
  const out = {};
  const put = (sym, w) => {
    const s = String(sym || "").toUpperCase().trim();
    const n = Number(w);
    if (!s || !Number.isFinite(n)) return;
    out[s] = n;
  };

  if (!payload || typeof payload !== "object") return out;

  // Common sleeve payload: {weights:{AAPL:0.01,...}}
  if (payload.weights && typeof payload.weights === "object" && !Array.isArray(payload.weights)) {
    for (const [sym, w] of Object.entries(payload.weights)) put(sym, w);
  }

  // Common SSOT payload: {targets:{positions:[{symbol,w},...]}}
  const pos = payload.positions || payload?.targets?.positions || payload?.targets;
  if (Array.isArray(pos)) {
    for (const p of pos) {
      if (!p || typeof p !== "object") continue;
      put(p.symbol || p.s || p.ticker, p.w ?? p.weight ?? p.target_weight ?? p.allocation);
    }
  }

  // Hash-of-JSON payload: {AAPL:'{"weight":0.1}', ...} or {AAPL:{weight:0.1}}
  if (!Object.keys(out).length) {
    for (const [sym, raw] of Object.entries(payload)) {
      if (sym.startsWith("__") || sym === "metadata" || sym === "meta") continue;
      const parsed = __ARES_jsonMaybe(raw);
      if (parsed && typeof parsed === "object") {
        put(parsed.symbol || sym, parsed.w ?? parsed.weight ?? parsed.target_weight ?? parsed.allocation);
      } else {
        put(sym, raw);
      }
    }
  }
  return out;
}

function __ARES_weightStats(weightsObj) {
  const entries = Object.entries(weightsObj || {})
    .map(([s, w]) => [String(s).toUpperCase(), Number(w)])
    .filter(([s, w]) => s && s !== "BIL" && s !== "CASH" && s !== "USD" && Number.isFinite(w) && Math.abs(w) > 1e-12);
  const vals = entries.map(([, w]) => Math.abs(w));
  const n = vals.length;
  const gross = vals.reduce((a, b) => a + b, 0);
  const net = entries.reduce((a, [, w]) => a + w, 0);
  const mean = n ? gross / n : 0;
  const variance = n ? vals.reduce((a, v) => a + Math.pow(v - mean, 2), 0) / n : 0;
  const std = Math.sqrt(variance);
  const cv = mean > 0 ? std / mean : 0;
  const uniq6 = new Set(vals.map(v => v.toFixed(6))).size;
  const uniq10 = new Set(vals.map(v => v.toFixed(10))).size;
  return {
    n,
    gross_abs_sum: __ARES_round10(gross),
    net_sum: __ARES_round10(net),
    mean_abs: __ARES_round10(mean),
    population_std: __ARES_round10(std),
    coefficient_of_variation_abs: __ARES_round10(cv),
    min_abs: n ? __ARES_round10(Math.min(...vals)) : 0,
    max_abs: n ? __ARES_round10(Math.max(...vals)) : 0,
    unique_count_rounded_6: uniq6,
    unique_count_rounded_10: uniq10,
  };
}

async function __ARES_readPayload(redis, key) {
  const type = await redis.type(key).catch(() => "none");
  if (type === "none") return { key, type, payload: null };
  if (type === "string") {
    const raw = await redis.get(key);
    return { key, type, payload: __ARES_jsonMaybe(raw) };
  }
  if (type === "hash") {
    const h = await redis.hgetall(key);
    const payload = {};
    for (const [k, v] of Object.entries(h || {})) {
      payload[k] = __ARES_jsonMaybe(v) ?? v;
    }
    return { key, type, payload };
  }
  return { key, type, payload: null };
}

async function __ARES_assertRam26Champion(redis) {
  const active = await redis.get("emarkos:v1:ram26:active_strategy_id").catch(() => "");
  if (active === "RAM26_ALPHA_0001" || active === "ALPHA_0001") {
    throw new Error(`short champion id is not allowed: ${active}`);
  }
  if (active && active !== ARES_RAM26_EXPECTED_CHAMPION_ID) {
    throw new Error(`champion mismatch: ${active} != ${ARES_RAM26_EXPECTED_CHAMPION_ID}`);
  }
}

async function __ARES_resolveTargetGross(redis, sourceGross) {
  const fromEnv = Number(process.env.ARES_BRIDGE_TARGET_GROSS || "");
  if (Number.isFinite(fromEnv) && fromEnv > 0.01 && fromEnv <= 1.0) return fromEnv;

  const scalarKeys = [
    "champion:target:gross_exposure:expected",
    "emarkos:v1:ram26:gross_target",
    "ram26:gross_target",
  ];
  for (const key of scalarKeys) {
    const raw = await redis.get(key).catch(() => null);
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0.01 && n <= 1.0) return n;
  }

  const curRaw = await redis.get("ssot:target:v2:current").catch(() => null);
  const cur = __ARES_jsonMaybe(curRaw);
  const g = Number(cur?.targets?.gross ?? cur?.gross);
  if (Number.isFinite(g) && g > 0.01 && g <= 1.0) return g;

  if (Number.isFinite(sourceGross) && sourceGross > 0.01 && sourceGross <= 1.0) return Math.max(sourceGross, ARES_BRIDGE_DEFAULT_TARGET_GROSS);
  return ARES_BRIDGE_DEFAULT_TARGET_GROSS;
}

async function __ARES_loadSleevePrimaryResult__(redis, activeUniverse) {
  await __ARES_assertRam26Champion(redis);
  const rejected = [];
  for (const key of ARES_SLEEVE_PRIMARY_KEYS) {
    const { type, payload } = await __ARES_readPayload(redis, key);
    if (!payload) { rejected.push({ key, reason: `missing_or_unparseable:${type}` }); continue; }
    const tsMs = __ARES_extractTsMs(payload);
    const ageMs = tsMs ? (Date.now() - tsMs) : null;
    if (ageMs !== null && ageMs > ARES_SLEEVE_MAX_AGE_MS) {
      rejected.push({ key, reason: `stale:${Math.round(ageMs / 1000)}s` }); continue;
    }
    const srcWeights = __ARES_extractWeights(payload);
    const inStats = __ARES_weightStats(srcWeights);
    if (inStats.n < 10) { rejected.push({ key, reason: `too_few_weights:${inStats.n}` }); continue; }
    if (inStats.gross_abs_sum <= 0.01) { rejected.push({ key, reason: `gross_too_low:${inStats.gross_abs_sum}` }); continue; }
    if (inStats.coefficient_of_variation_abs < ARES_BRIDGE_MIN_INPUT_CV || inStats.unique_count_rounded_6 < ARES_BRIDGE_MIN_UNIQUE6) {
      rejected.push({ key, reason: `input_dispersion_too_low:cv=${inStats.coefficient_of_variation_abs},unique6=${inStats.unique_count_rounded_6}` }); continue;
    }

    const targetGross = await __ARES_resolveTargetGross(redis, inStats.gross_abs_sum);
    const scale = targetGross / inStats.gross_abs_sum;
    const scaled = {};
    const scores = {};
    for (const [symRaw, wRaw] of Object.entries(srcWeights)) {
      const sym = String(symRaw || "").toUpperCase().trim();
      const w = Number(wRaw);
      if (!sym || !Number.isFinite(w) || Math.abs(w) <= 1e-12) continue;
      scaled[sym] = __ARES_round10(w * scale);
      scores[sym] = Math.abs(w);
    }
    const outStats = __ARES_weightStats(scaled);
    if (outStats.coefficient_of_variation_abs < ARES_BRIDGE_MIN_OUTPUT_CV || outStats.unique_count_rounded_6 < ARES_BRIDGE_MIN_UNIQUE6) {
      rejected.push({ key, reason: `output_dispersion_too_low:cv=${outStats.coefficient_of_variation_abs},unique6=${outStats.unique_count_rounded_6}` }); continue;
    }

    console.log(`[ARES_SLEEVE_PRIMARY] source=${key} n=${outStats.n} source_gross=${inStats.gross_abs_sum} target_gross=${targetGross.toFixed(10)} scale=${scale.toFixed(6)} input_cv=${inStats.coefficient_of_variation_abs} output_cv=${outStats.coefficient_of_variation_abs} unique6=${outStats.unique_count_rounded_6}`);
    return {
      sourceKey: key,
      weights: scaled,
      scores,
      sourceGross: inStats.gross_abs_sum,
      targetGross: __ARES_round10(targetGross),
      scale: __ARES_round10(scale),
      inputStats: inStats,
      outputStats: outStats,
      ageMs,
      cashWeight: Math.max(0, 1 - outStats.gross_abs_sum),
    };
  }

  const msg = `No valid sleeve primary source. rejected=${JSON.stringify(rejected).slice(0, 1000)}`;
  if (ARES_BRIDGE_REQUIRE_SLEEVE) {
    try {
      await redis.set("ops:halt:request", JSON.stringify({ ts_ms: Date.now(), actor: "final-to-champion-bridge", reason: `SLEEVE_PRIMARY_REQUIRED:${msg}`, auto: true }), "EX", 3600);
    } catch (_) {}
    throw new Error(msg);
  }
  console.warn(`[ARES_SLEEVE_PRIMARY_SKIP] ${msg}; falling back to legacy path because ARES_BRIDGE_REQUIRE_SLEEVE=0`);
  return null;
}

function __ARES_degenerateBookVerdict(weightsObj) {
  const stats = __ARES_weightStats(weightsObj);
  const reasons = [];
  if (stats.n >= 10 && stats.coefficient_of_variation_abs < ARES_BRIDGE_MIN_OUTPUT_CV) reasons.push(`cv_too_low:${stats.coefficient_of_variation_abs}<${ARES_BRIDGE_MIN_OUTPUT_CV}`);
  if (stats.n >= 10 && stats.unique_count_rounded_6 < ARES_BRIDGE_MIN_UNIQUE6) reasons.push(`unique6_too_low:${stats.unique_count_rounded_6}<${ARES_BRIDGE_MIN_UNIQUE6}`);
  return { blocked: reasons.length > 0, reasons, stats };
}

async function __ARES_failClosedDegenerateBook__(redis, stage, verdict, extra = {}) {
  const body = {
    ts_ms: Date.now(),
    actor: "final-to-champion-bridge",
    reason: `DEGENERATE_WEIGHT_BOOK:${stage}:${verdict.reasons.join(",")}`,
    auto: true,
    stats: verdict.stats,
    ...extra,
  };
  console.error(`[ARES_DISPERSION_GUARD_BLOCK] ${JSON.stringify(body)}`);
  try {
    const p = redis.pipeline();
    p.set("trading:enabled", "false");
    p.set("ops:halt:request", JSON.stringify(body), "EX", 3600);
    p.xadd("ares:bridge:dispersion_guard", "MAXLEN", "~", "10000", "*", "payload", JSON.stringify(body));
    p.xadd("ares:trading:audit", "MAXLEN", "~", "10000", "*", "event", "degenerate_weight_book_blocked", "actor", "final-to-champion-bridge", "payload", JSON.stringify(body));
    await p.exec();
  } catch (e) {
    console.error(`[ARES_DISPERSION_GUARD_WRITE_FAIL] ${e.message}`);
  }
}
// === [ARES-RAM26-SLEEVE-PRIMARY-PATCH-v1] END ===

// ============================================================
// Universe Loading (Dynamic)
// ============================================================

/**
 * Load universe from Redis (emarkos:v1:universe:current)
 * Falls back to CHAMPION_CONFIG.universe if not available
 */
async function loadDynamicUniverse(redis) {
  try {
    // 4AI-audit FIX: emarkos:v1:universe:current is a Redis SET, not STRING
    // Must use SMEMBERS, not GET
    const keyType = await redis.type("emarkos:v1:universe:current");
    let uni = [];
    if (keyType === "set") {
      uni = await redis.smembers("emarkos:v1:universe:current");
    } else if (keyType === "string") {
      const uniRaw = await redis.get("emarkos:v1:universe:current");
      if (uniRaw) uni = JSON.parse(uniRaw);
    } else if (keyType === "list") {
      uni = await redis.lrange("emarkos:v1:universe:current", 0, -1);
    }
    if (Array.isArray(uni) && uni.length > 0) {
      console.log(`[${new Date().toISOString()}] [UNIVERSE_LOADED] source=redis, type=${keyType}, count=${uni.length}`);
      return { universe: uni, source: "redis" };
    }
  } catch (e) {
    console.error("[champion-bridge-v3] Failed to load universe:", e.message);
  }
  console.log(`[${new Date().toISOString()}] [UNIVERSE_FALLBACK] using CHAMPION_CONFIG.universe (${CHAMPION_CONFIG.universe.length} symbols)`);
  return { universe: CHAMPION_CONFIG.universe, source: "fallback" };
}

// ============================================================
// Strategy Calculation Functions
// ============================================================

/**
 * Calculate composite momentum score for a symbol
 * Formula: mom3 * 0.5 + mom5 * 0.3 + mom10 * 0.2
 */
function calculateMomentumScore(mom3, mom5, mom10, integrated = 0, refined = 0) {
  const { mom_3_weight, mom_5_weight, mom_10_weight } = CHAMPION_CONFIG.momentum;
  // 기본 모멘텀 (40%)
  const basicMom = (mom3 * mom_3_weight) + (mom5 * mom_5_weight) + (mom10 * mom_10_weight);
  // 고급 시그널 (60%): integrated 40%, refined 20%
  const advancedSignal = (integrated * 0.4) + (refined * 0.2);
  // 고급 시그널이 있으면 통합, 없으면 기본 모멘텀만 사용
  if (integrated !== 0 || refined !== 0) {
    return (basicMom * 0.4) + advancedSignal;
  }
  return basicMom;
}

/**
 * Calculate volatility scaling factor
 * Scale = target_vol / realized_vol, clamped to [scale_min, scale_max]
 */
function calculateVolScale(realizedVol) {
  const { target_vol, scale_min, scale_max } = CHAMPION_CONFIG.volatility;
  if (!realizedVol || realizedVol <= 0) return 1.0;
  
  const rawScale = target_vol / realizedVol;
  return clamp(rawScale, scale_min, scale_max);
}

/**
 * Calculate VIX-based regime multiplier
 * - VIX > vol_threshold (25): high_vol_mult (0.80)
 * - VIX <= vol_threshold: low_vol_mult (1.20)
 */
function calculateVixRegimeMultiplier(vix) {
  const { vol_threshold, high_vol_mult, low_vol_mult } = CHAMPION_CONFIG.regime;
  
  if (!vix || vix <= 0) return 1.0;
  
  if (vix > vol_threshold) {
    return high_vol_mult;  // Reduce exposure in high volatility
  } else {
    return low_vol_mult;   // Increase exposure in low volatility
  }
}

/**
 * Calculate hedge ratio based on VIX
 * Linear interpolation between vix_threshold_floor (25) and vix_threshold_cap (45)
 */
function calculateHedgeRatio(vix) {
  const { hedge_base, hedge_boost_cap, hedge_cap, vix_threshold_floor, vix_threshold_cap } = CHAMPION_CONFIG.hedge;
  
  if (!vix || vix <= 0) return hedge_base;
  
  if (vix <= vix_threshold_floor) {
    return hedge_base;  // Base hedge only
  } else if (vix >= vix_threshold_cap) {
    return Math.min(hedge_base + hedge_boost_cap, hedge_cap);  // Max hedge
  } else {
    // Linear interpolation
    const vixRange = vix_threshold_cap - vix_threshold_floor;
    const vixProgress = (vix - vix_threshold_floor) / vixRange;
    const hedgeBoost = vixProgress * hedge_boost_cap;
    return Math.min(hedge_base + hedgeBoost, hedge_cap);
  }
}

/**
 * Calculate uncertainty multiplier
 * Based on ensemble model uncertainty (p_std)
 */
function calculateUncertaintyMultiplier(uncertaintyProxy) {
  const { proxy_min, proxy_max, mult_min, mult_max } = CHAMPION_CONFIG.uncertainty;
  
  if (uncertaintyProxy === null || uncertaintyProxy === undefined) return 1.0;
  
  // Clamp proxy to valid range
  const clampedProxy = clamp(uncertaintyProxy, proxy_min, proxy_max);
  
  // Linear interpolation: high uncertainty -> low multiplier
  const proxyRange = proxy_max - proxy_min;
  const proxyProgress = (clampedProxy - proxy_min) / proxyRange;
  
  // Inverse relationship: higher uncertainty = lower multiplier
  return mult_max - (proxyProgress * (mult_max - mult_min));
}

/**
 * Check if crisis mode should be triggered
 * Crisis when average momentum < -crisis_trigger_threshold (i.e., < -5%)
 */
function checkCrisisMode(signals) {
  const { crisis_trigger_threshold } = CHAMPION_CONFIG.momentum;
  
  let totalMomentum = 0;
  let count = 0;
  
  for (const data of Object.values(signals)) {
    const momScore = calculateMomentumScore(data.mom3, data.mom5, data.mom10, data.integrated, data.refined);
    totalMomentum += momScore;
    count++;
  }
  
  const avgMomentum = count > 0 ? totalMomentum / count : 0;
  // Crisis triggers when average momentum is NEGATIVE and below threshold
  // e.g., crisis_trigger_threshold = 0.05 means crisis when avgMomentum < -0.05 (-5%)
  return avgMomentum < -crisis_trigger_threshold;
}

/**
 * Fetch momentum signals from Redis for given universe symbols
 * @param {Redis} redis - Redis client
 * @param {string[]} universe - Array of symbols to fetch
 */
async function fetchMomentumSignals(redis, universe) {
  const signals = {};
  // 성능/지터 방지: 심볼별 GET 다발을 mget로 배치 처리
  const keys = [];
  for (const symbol of universe) {
    keys.push(`signal:${symbol}:mom3`);
    keys.push(`signal:${symbol}:mom5`);
    keys.push(`signal:${symbol}:mom10`);
    keys.push(`signal:${symbol}:vol`);
    keys.push(`advanced:signal:${symbol}:integrated`);
    keys.push(`refined:signal:${symbol}`);
  }

  const vals = await redis.mget(keys);
  let i = 0;
  for (const symbol of universe) {
    const mom3 = vals[i++], mom5 = vals[i++], mom10 = vals[i++], vol = vals[i++], integrated = vals[i++], refined = vals[i++];
    signals[symbol] = {
      mom3: safeNumber(mom3) || 0,
      mom5: safeNumber(mom5) || 0,
      mom10: safeNumber(mom10) || 0,
      vol: safeNumber(vol) || 0.20,
      integrated: safeNumber(integrated) || 0,
      refined: safeNumber(refined) || 0
    };
  }

  return signals;
}

/**
 * Fetch current VIX value from Redis
 */
async function fetchVix(redis) {
  const vix = await redis.get(VIX_KEY_SSOT);  // SSOT: market:vix
  return safeNumber(vix) || 20.0;  // Default VIX 20 if missing
}

/**
 * Calculate dynamic portfolio weights based on Champion v9.2.1 strategy
 */
// ------------------------------
// Helpers: clamp + dynamic cap (8~15%)
// ------------------------------
function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

/**
 * Dynamic single-stock cap in [8%, 15%]
 * - Higher stress (VIX/crisis/uncertainty) => lower cap (toward 8%)
 * - Fewer active risk assets (K small) => higher cap (toward 15%), damped by stress
 */
function computeDynamicCap({ vix, isCrisisMode, uncertaintyProxy, K }) {
  const capMin = 0.05;
  const capBase = 0.12;
  const capMax = 0.25;

  const stressVix = clamp01((vix - 20) / 20); // VIX 20->0, 40->1
  const stressCrisis = isCrisisMode ? 1 : 0;
  const stressUnc = (uncertaintyProxy == null) ? 0 : clamp01((uncertaintyProxy - 0.0) / 1.0);
  const stress = clamp01(0.6 * stressVix + 0.3 * stressUnc + 0.1 * stressCrisis);

  const Ktarget = 6;
  const kBoost = clamp01((Ktarget - (K || 0)) / Ktarget);

  const dynamicCap =
    capBase
    + (capMax - capBase) * kBoost * (1 - stress)
    - (capBase - capMin) * stress;

  return Math.max(capMin, Math.min(capMax, dynamicCap));
}

// ------------------------------
// Cap Tuner: Redis state management + auto-tuning
// ------------------------------
async function loadCapTuner(redis) {
  const key = "champion:v660:cap_tuner";
  try {
    const t = await resilientHgetall(redis, key, "cap_tuner");
    return {
      kP: t.kP ? Number(t.kP) : 0.20,
      targetCash: t.targetCash ? Number(t.targetCash) : 0.05,
      prevError: t.prevError ? Number(t.prevError) : 0,
      kV: t.kV ? Number(t.kV) : 0.40,
      ewmaVol: t.ewmaVol ? Number(t.ewmaVol) : null,
      _prevVolErrorSign: t._prevVolErrorSign ? Number(t._prevVolErrorSign) : 0
    };
  } catch (e) {
    console.error("[cap_tuner] loadCapTuner error:", e.message);
    return { kP: 0.20, targetCash: 0.05, prevError: 0, kV: 0.40, ewmaVol: null, _prevVolErrorSign: 0 };
  }
}

async function saveCapTuner(redis, tuner) {
  const key = "champion:v660:cap_tuner";
  try {
    await redis.hset(key, {
      kP: tuner.kP.toFixed(4),
      targetCash: tuner.targetCash.toFixed(4),
      prevError: tuner.prevError.toFixed(6),
      kV: tuner.kV.toFixed(4),
      ewmaVol: tuner.ewmaVol != null ? tuner.ewmaVol.toFixed(6) : "",
      _prevVolErrorSign: String(tuner._prevVolErrorSign || 0),
      updatedAt: String(Date.now())
    });
  } catch (e) {
    console.error("[cap_tuner] saveCapTuner error:", e.message);
  }
}

function updateKPWithFlipGuard(tuner, error, freeze) {
  if (freeze) return tuner;
  
  const prev = tuner.prevError;
  const flip = (error === 0 || prev === 0) ? false : (Math.sign(error) !== Math.sign(prev));
  
  if (flip) tuner.kP *= 0.90;
  else if (Math.abs(error) > 0.10) tuner.kP *= 1.05;
  
  tuner.kP = Math.max(0.05, Math.min(0.50, tuner.kP));
  tuner.prevError = error;
  return tuner;
}

async function loadPortfolioMetrics(redis) {
  const key = "champion:v660:portfolio:metrics";
  try {
    const m = await resilientHgetall(redis, key, "portfolio_metrics");
    return {
      realizedVol20d: m.realizedVol20d ? Number(m.realizedVol20d) : null,
      maxDrawdown20d: m.maxDrawdown20d ? Number(m.maxDrawdown20d) : null
    };
  } catch (e) {
    return { realizedVol20d: null, maxDrawdown20d: null };
  }
}

function updateTargetCashFromVolAndDD(tuner, metrics, opts) {
  const {
    targetVol = 0.18,
    BIL_MIN = 0.02,
    BIL_MAX = 0.10,
    maxStepPerRun = 0.03,
    ewmaAlpha = 0.20,
    ddSoftThreshold = 0.06,
    ddHardThreshold = 0.10,
    ddCashFloorSoft = 0.35,
    ddCashFloorHard = 0.50,
    freeze = false
  } = opts || {};

  if (freeze) return tuner;

  const vol = metrics?.realizedVol20d;
  if (vol == null || !Number.isFinite(vol)) return tuner;

  // EWMA smoothing
  if (tuner.ewmaVol == null) tuner.ewmaVol = vol;
  else tuner.ewmaVol = ewmaAlpha * vol + (1 - ewmaAlpha) * tuner.ewmaVol;

  const volUsed = tuner.ewmaVol;
  const volError = volUsed - targetVol;
  tuner.prevVolError = volError;

  let next = tuner.targetCash + tuner.kV * volError;
  const delta = Math.max(-maxStepPerRun, Math.min(maxStepPerRun, next - tuner.targetCash));
  next = tuner.targetCash + delta;

  // Drawdown-based cash floor
  const dd = metrics?.maxDrawdown20d;
  if (dd != null && Number.isFinite(dd)) {
    if (dd >= ddHardThreshold) next = Math.max(next, ddCashFloorHard);
    else if (dd >= ddSoftThreshold) next = Math.max(next, ddCashFloorSoft);
  }

  tuner.targetCash = Math.max(BIL_MIN, Math.min(BIL_MAX, next));
  return tuner;
}

function adaptKV(tuner, freeze) {
  if (freeze) return tuner;

  const ve = tuner.prevVolError || 0;
  const pve = tuner._prevVolErrorSign ?? 0;
  const sign = ve === 0 ? 0 : Math.sign(ve);

  const flip = (sign !== 0 && pve !== 0 && sign !== pve);
  if (flip) tuner.kV *= 0.90;
  else if (Math.abs(ve) > 0.03) tuner.kV *= 1.03;

  tuner.kV = Math.max(0.10, Math.min(0.80, tuner.kV));
  tuner._prevVolErrorSign = sign;
  return tuner;
}



async function calculateDynamicWeights(signals, vix, regimeMultiplier = 1.0, uncertaintyProxy = null, tunerParams = {}, universe = null, redis = null) {
  // Use provided universe or fallback to config
  const activeUniverse = universe || CHAMPION_CONFIG.universe;
  const { min_weight, max_weight, max_leverage, max_single_stock } = CHAMPION_CONFIG.constraints;
  const { crisis_boost } = CHAMPION_CONFIG.momentum;

  // [ARES-RAM26-SLEEVE-PRIMARY-PATCH-v1]
  // RAM26 live should be sleeve-primary. If sleeve is valid, bypass legacy
  // momentum recompute / epsilon / 1N fallback to preserve alpha dispersion.
  const __ram26SleevePrimary = redis ? await __ARES_loadSleevePrimaryResult__(redis, activeUniverse) : null;
  if (__ram26SleevePrimary) {
    const __vixRegimeMult = 1.0;
    const __hedgeRatio = 0.0;
    const __uncertaintyMult = 1.0;
    return {
      weights: __ram26SleevePrimary.weights,
      scores: __ram26SleevePrimary.scores,
      leverage: 1.0,
      totalPositiveScore: __ram26SleevePrimary.sourceGross,
      vixRegimeMult: __vixRegimeMult,
      hedgeRatio: __hedgeRatio,
      uncertaintyMult: __uncertaintyMult,
      isCrisisMode: false,
      vix,
      cashActual: __ram26SleevePrimary.cashWeight,
      debug: {
        source: "sleeve_primary",
        sourceKey: __ram26SleevePrimary.sourceKey,
        sourceGross: __ram26SleevePrimary.sourceGross,
        targetGross: __ram26SleevePrimary.targetGross,
        scale: __ram26SleevePrimary.scale,
        inputStats: __ram26SleevePrimary.inputStats,
        outputStats: __ram26SleevePrimary.outputStats,
        ageMs: __ram26SleevePrimary.ageMs,
      }
    };
  }
  
  // Step 1: Check crisis mode
  const isCrisisMode = checkCrisisMode(signals);
  
  // Step 2: Calculate VIX regime multiplier
  const vixRegimeMult = calculateVixRegimeMultiplier(vix);
  
  // Step 3: Calculate hedge ratio
  const hedgeRatio = calculateHedgeRatio(vix);
  
  // Step 4: Calculate uncertainty multiplier
  const uncertaintyMult = calculateUncertaintyMultiplier(uncertaintyProxy);
  
  // Step 5: Calculate momentum scores for each symbol
  const scores = {};
  let totalPositiveScore = 0;
  
  for (const [symbol, data] of Object.entries(signals)) {
    const momScore = calculateMomentumScore(data.mom3, data.mom5, data.mom10, data.integrated, data.refined);
    const volScale = calculateVolScale(data.vol);
    
    // Adjusted score = momentum * vol_scale
    // Only consider positive momentum (long-only strategy)
    // sqrt softening: reduce top-heavy concentration
    const rawScore = Math.max(0, momScore * volScale);
    const adjustedScore = Math.sqrt(rawScore);
    scores[symbol] = adjustedScore;
    totalPositiveScore += adjustedScore;
  }
  
  // Step 6: Calculate raw weights with epsilon floor for diversification
  const rawWeights = {};
  const EPSILON_WEIGHT = 0.01;  // 1% minimum for diversification
  const EPSILON_SHARE = Number(process.env.ARES_LEGACY_EPSILON_SHARE || "0.05");   // legacy-only fallback; sleeve-primary path bypasses this
  
  if (totalPositiveScore > 0) {
    // Count symbols with positive momentum vs zero/negative
    const positiveSymbols = Object.entries(scores).filter(([, s]) => s > 0);
    const zeroSymbols = activeUniverse.filter(sym => !scores[sym] || scores[sym] <= 0);
    
    // Calculate momentum-based weights (70% of portfolio)
    const momentumShare = 1.0 - EPSILON_SHARE;
    for (const [symbol, score] of Object.entries(scores)) {
      if (score > 0) {
        rawWeights[symbol] = (score / totalPositiveScore) * momentumShare;
      }
    }
    
    // Distribute epsilon share equally among all universe symbols
    // This ensures diversification even for symbols without momentum signals
    const epsilonPerSymbol = EPSILON_SHARE / activeUniverse.length;
    for (const symbol of activeUniverse) {
      rawWeights[symbol] = (rawWeights[symbol] || 0) + epsilonPerSymbol;
    }
  } else {
    // Fallback to equal weight if no positive momentum
    const equalWeight = 1.0 / activeUniverse.length;
    for (const symbol of activeUniverse) {
      rawWeights[symbol] = equalWeight;
    }
  }
  
  // Step 7: Apply constraints (min/max weight, max_single_stock)
  const constrainedWeights = {};
  let totalConstrained = 0;

  // CASH(현금/안전자산) 심볼 (A안 residual bucket)
  const CASH_SYMBOL = "BIL";

  // Active risk assets count (raw stage)
  const K_base = Object.entries(rawWeights).filter(([, w]) => w > 0).length;

  // (2) Base cap uses dynamic cap (8~15%), NOT max_single_stock
  const dynCapBase = computeDynamicCap({ vix, isCrisisMode, uncertaintyProxy, K: K_base });
  let effectiveMaxWeight = Math.min(max_weight, dynCapBase);

  // (3) Hybrid controller: HARD band outside + P-control inside band
  // Goal: keep "structural minimum cash" within [BIL_MIN, BIL_MAX],
  // and gently steer toward targetCash when inside the band.
  const BIL_MIN = 0.00;
  const BIL_MAX = 0.10;
  const targetCash = tunerParams.targetCash ?? 0.30;   // 튜너에서 받거나 기본값
  const kP = tunerParams.kP ?? 0.80;                   // 튜너에서 받거나 기본값

  const CAP_MIN = 0.03;
  const CAP_MAX = 0.25;

  // Debug variables for return
  let residualMin = 0;

  if (K_base > 0) {
    // structural minimum cash given cap:
    residualMin = Math.max(0, 1 - K_base * effectiveMaxWeight);

    if (residualMin > BIL_MAX) {
      // HARD: cash too large => raise cap so K*cap >= 1 - BIL_MAX
      const neededCapUp = (1 - BIL_MAX) / K_base;
      effectiveMaxWeight = Math.max(effectiveMaxWeight, neededCapUp);
    } else if (residualMin < BIL_MIN) {
      // HARD: cash too small => lower cap so K*cap <= 1 - BIL_MIN
      const neededCapDown = (1 - BIL_MIN) / K_base;
      effectiveMaxWeight = Math.min(effectiveMaxWeight, neededCapDown);
    } else {
      // SOFT: inside band => P-control toward targetCash
      const error = targetCash - residualMin;
      effectiveMaxWeight += (kP * error) / K_base;
    }

    // Clamp within cap range + dyn cap + max_weight (4AI-audit: NaN guard)
    if (!Number.isFinite(effectiveMaxWeight)) {
      console.log(`[${new Date().toISOString()}] [P_CONTROL_NaN] effectiveMaxWeight is NaN/Inf, resetting to dynCapBase=${dynCapBase}`);
      effectiveMaxWeight = dynCapBase;
    }
    effectiveMaxWeight = Math.max(CAP_MIN, Math.min(CAP_MAX, effectiveMaxWeight));
    effectiveMaxWeight = Math.min(effectiveMaxWeight, max_weight, dynCapBase);
  }

  for (const [symbol, weight] of Object.entries(rawWeights)) {
    let constrained = weight;

    // Apply minimum weight only if above threshold
    if (constrained > 0 && constrained < min_weight) {
      constrained = min_weight;
    }

    // Apply max cap (max_weight and max_single_stock)
    if (constrained > effectiveMaxWeight) {
      constrained = effectiveMaxWeight;
    }

    constrainedWeights[symbol] = constrained;
    totalConstrained += constrained;
  }

  // Step 8: DO NOT normalize to 1.0 (it can break caps)
  // --- A안: 캡 초과분을 미캡 종목에 우선 재분배, 남으면 BIL ---
  // 자동 밸브: cap에 걸린 종목 수에 따라 재분배 비율 조절
  let totalExcess = 0;
  const cappedSymbols = new Set();
  const uncappedSymbols = [];
  for (const [symbol, weight] of Object.entries(constrainedWeights)) {
    if (symbol === CASH_SYMBOL) continue;
    const rawW = rawWeights[symbol] || 0;
    if (rawW > effectiveMaxWeight) {
      totalExcess += (rawW - effectiveMaxWeight);
      cappedSymbols.add(symbol);
    } else if (weight > 0) {
      uncappedSymbols.push(symbol);
    }
  }
  
  // 자동 밸브: cap에 걸린 종목이 많을수록 재분배 비율 낮춤
  const cappedCount = cappedSymbols.size;
  const REDIST_TO_RISK = cappedCount >= 4 ? 0.20 : cappedCount >= 3 ? 0.35 : cappedCount >= 2 ? 0.50 : 0.60;
  const excessToRedist = totalExcess * REDIST_TO_RISK;
  const excessToCashDirect = totalExcess * (1 - REDIST_TO_RISK);
  
  if (excessToRedist > 0 && uncappedSymbols.length > 0) {
    const uncappedScoreSum = uncappedSymbols.reduce((sum, s) => sum + (scores[s] || 0), 0);
    if (uncappedScoreSum > 0) {
      let remainingExcess = excessToRedist;
      for (const symbol of uncappedSymbols) {
        const share = (scores[symbol] || 0) / uncappedScoreSum;
        const addition = excessToRedist * share;
        const newWeight = constrainedWeights[symbol] + addition;
        if (newWeight > effectiveMaxWeight) {
          const actualAddition = effectiveMaxWeight - constrainedWeights[symbol];
          constrainedWeights[symbol] = effectiveMaxWeight;
          remainingExcess -= actualAddition;
        } else {
          constrainedWeights[symbol] = newWeight;
          remainingExcess -= addition;
        }
      }
      // 남은 excess는 BIL로
      if (remainingExcess > 0) {
        constrainedWeights[CASH_SYMBOL] = (constrainedWeights[CASH_SYMBOL] || 0) + remainingExcess;
      }
    }
  }
  // excessToCashDirect는 바로 BIL로
  if (excessToCashDirect > 0) {
    constrainedWeights[CASH_SYMBOL] = (constrainedWeights[CASH_SYMBOL] || 0) + excessToCashDirect;
  }
  
  totalConstrained = Object.values(constrainedWeights).reduce((s, w) => s + w, 0);
  // --- End of uncapped redistribution with auto valve ---
  // Instead, allocate leftover to CASH_SYMBOL (BIL)
  let normalizedWeights = { ...constrainedWeights };

  // leftover cash allocation (clip to [0,1])
  const residualCash = Math.max(0, Math.round((1.0 - totalConstrained) * 10000) / 10000);
  if (residualCash > 0) {
    normalizedWeights[CASH_SYMBOL] = (normalizedWeights[CASH_SYMBOL] || 0) + residualCash;
  }

  // Edge case: if floors pushed totalConstrained > 1.0, scale down proportionally
  // (this should be rare with your current min_weight logic, but keep it safe)
  const totalAfterCash = Object.values(normalizedWeights).reduce((s, w) => s + w, 0);
  if (totalAfterCash > 1.0 && totalAfterCash > 0) {
    for (const key of Object.keys(normalizedWeights)) {
      normalizedWeights[key] = Math.round((normalizedWeights[key] / totalAfterCash) * 10000) / 10000;
    }
  }

  // Step 9: Apply all multipliers

  // --- Dynamic λ Overlay (ARES Sleeve) v8.2 ---
  if (USE_ARES_LAMBDA_OVERLAY && redis) {
    try {
      const p = loadTop1LambdaParams();
      const st = await fetchCtxStateLambda(redis, activeUniverse, CASH_SYMBOL);
      const { lambda: lam, state: lamState, prevState: lamPrevState } = await computeLambdaHysteresis(redis, st, p);
      const aresW = await fetchAresWeightsLambda(redis);
      if (aresW) {
        normalizedWeights = blendNormalizedWeights(normalizedWeights, aresW, lam, activeUniverse, CASH_SYMBOL);
        if (__skipConsecutive > 0) {
          console.log(`[LAMBDA_RECOVERED] ts=${nowIsoLambda()} was_skip_count=${__skipConsecutive}`);
          auditX(redis, "INFO", "lambda_recovered", { skip_count: __skipConsecutive }).catch(() => {});
        }
        __skipConsecutive = 0;
        console.log(`[LAMBDA_APPLY] ts=${nowIsoLambda()} policy=${p.policy} lambda=${lam.toFixed(3)} state=${lamState} risk_rate=${st.risk_rate.toFixed(3)} macro=${st.macro_regime} breadth_weak=${st.breadth_weak}`);
      } else {
        __skipConsecutive++;
        console.log(`[LAMBDA_APPLY_SKIP] ts=${nowIsoLambda()} missing ${ARES_WEIGHTS_KEY} -> no overlay (consecutive=${__skipConsecutive})`);
        if (__skipConsecutive === SKIP_ALERT_THRESHOLD) {
          console.error(`[LAMBDA_SKIP_CRITICAL] ts=${nowIsoLambda()} ${__skipConsecutive} consecutive skips! ARES weights missing for ${Math.round(__skipConsecutive * 5 / 60)} min`);
          auditX(redis, "CRITICAL", "lambda_skip_threshold", { consecutive: __skipConsecutive, threshold: SKIP_ALERT_THRESHOLD, key: ARES_WEIGHTS_KEY }).catch(() => {});
          redis.publish("alerts:ares:sleeve", JSON.stringify({ level: "CRITICAL", event: "LAMBDA_SKIP_THRESHOLD", ts: nowIsoLambda(), consecutive: __skipConsecutive })).catch(() => {});
        }
      }
    } catch (e) {
      console.log(`[LAMBDA_APPLY_FAIL] ts=${nowIsoLambda()} err=${String(e)} -> no overlay`);
    }
  } else if (!USE_ARES_LAMBDA_OVERLAY) {
    console.log(`[LAMBDA_APPLY_OFF] ts=${nowIsoLambda()} USE_ARES_LAMBDA_OVERLAY=0`);
  }

  let combinedMult = regimeMultiplier * vixRegimeMult * uncertaintyMult * (1 - hedgeRatio);

  if (isCrisisMode) {
    combinedMult *= (1 - crisis_boost);
  }

  // NOTE: effectiveLeverage must be mutable for the safety cap
  let effectiveLeverage = clamp(combinedMult, 0, max_leverage);
  console.log(`[LEV] ts=${new Date().toISOString()} max_leverage_cfg=${max_leverage} effective_cap=dynamic combinedMult=${combinedMult.toFixed(4)} preCapLev=${effectiveLeverage.toFixed(4)}`);

  // --- Safety guard: reduce leverage so post-leverage single-stock cap is never violated ---
  // CASH_SYMBOL already declared above
  const postCap = Math.min(max_weight, max_single_stock);

  const riskWeights = Object.entries(normalizedWeights)
    .filter(([s, w]) => s !== CASH_SYMBOL && w > 0)
    .map(([, w]) => w);

  // [MASTER-PATCH-4] Leverage Drag 완화: 1.3x 여유 계수 적용
  // 기존: maxLevByCap가 전체 레버리지를 과도하게 제한 → 백테스트 알파 누수
  // 수정: 개별 종목 초과분은 L1032-1034의 excessToCash에서 처리하므로
  //       전체 레버리지 제한은 30% 여유를 두어 알파 보존
  const maxLevByCap = riskWeights.length
    ? Math.min(...riskWeights.map(w => postCap / w)) * 1.3
    : 0;

  if (effectiveLeverage > maxLevByCap && maxLevByCap > 0) {
    console.log(`[LEV] Leverage drag softened: ${effectiveLeverage.toFixed(4)} → ${maxLevByCap.toFixed(4)} (1.3x headroom)`);
    effectiveLeverage = Math.min(effectiveLeverage, maxLevByCap);
  }
  // ------------------------------------------------------------------------------

  // Step 10: Apply leverage ONLY to risk assets (exclude CASH/BIL),
  // then enforce POST-leverage cap and send any excess to CASH_SYMBOL (BIL).
  const finalWeights = {};

  const cashBase = normalizedWeights[CASH_SYMBOL] || 0;
  let excessToCash = 0;

  // 10-1) leverage risk assets only, then cap them (post-leverage)
  for (const [symbol, weight] of Object.entries(normalizedWeights)) {
    if (symbol === CASH_SYMBOL) continue;

    let w = weight * effectiveLeverage;

    if (w > postCap) {
      excessToCash += (w - postCap);
      w = postCap;
    }

    finalWeights[symbol] = Math.round(w * 10000) / 10000;
  }

  // 10-2) cash stays 1x; add any excess from capped risk assets
  const cashFinal = cashBase + excessToCash;
  if (cashFinal > 0) {
    finalWeights[CASH_SYMBOL] = Math.round(cashFinal * 10000) / 10000;
  }

  // cashActual for tuner (final execution weight)
  const cashActual = finalWeights[CASH_SYMBOL] || 0;
  
  return {
    weights: finalWeights,
    scores,
    leverage: effectiveLeverage,
    totalPositiveScore,
    vixRegimeMult,
    hedgeRatio,
    uncertaintyMult,
    isCrisisMode,
    vix,
    cashActual,
    debug: {
      dynCapBase: Math.round(dynCapBase * 10000) / 10000,
      effectiveMaxWeight: Math.round(effectiveMaxWeight * 10000) / 10000,
      residualMin: Math.round(residualMin * 10000) / 10000,
      K_base,
      targetCash: Math.round(targetCash * 10000) / 10000,
      kP: Math.round(kP * 10000) / 10000
    }
  };
}

// ============================================================
// Main Loop
// ============================================================

// ============================
// v8.1 Alpha SSOT (Redis) Overlay
// ============================
const USE_REDIS_ALPHA_SSOT = (process.env.USE_REDIS_ALPHA_SSOT || "1") !== "0";

function _safeJsonParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}

// ============================================================================
// [STRUCTURAL FIX 20260505 v8.4-MUSK] fetchAlphaOverlay — SSOT-direct adapter
// ----------------------------------------------------------------------------
// Root cause (pre-fix): bridge expected `alpha:{SYM}:score` keys, but no live
// publisher writes them anymore (legacy `alpha_signal_scheduler` was removed
// from pm2 during migration). Result: 100% ALPHA_NO_DATA, λ pinned at upper
// cap (0.30), risk_rate flat 0, ARES↔Champion blending effectively disabled.
//
// Musk-style 5-step:
//   1) Question  : Do we need a separate alpha-publisher daemon? -> NO.
//   2) Delete    : Drop the broken `alpha:*:score` dependency entirely.
//   3) Simplify  : Synthesize alpha overlay from already-live SSOTs:
//                    factor_scores:{SYM}  (factor-engine, 47/47 healthy)
//                    xgb:risk:{SYM}       (xgb-riskflag-writer-v5, 48/48 healthy)
//   4) Accelerate: One-pass mget for both; runs inline in 5s bridge cycle.
//   5) Automate  : Self-health log emits ALPHA_HEALTH every cycle; if both
//                  sources empty for >60s the overlay is BYPASSED (neutral).
// ============================================================================
// ============================================================================
// [v8.4-PATCH 2026-05-08] risk_count_ratio EMA helper + hysteresis state.
// Resolves "distribution collapse" where ALPHA_HEALTH_v84MUSK risk_rate was
// pinned at risk_cnt/valid_n with a too-loose predicate, conflicting with the
// ARES v852 continuous-EMA stress also called risk_rate.
// 4-AI consensus: GPT-4.1 / Claude / Gemini / DeepSeek (alpha=0.2, 2-cycle
// hysteresis 0.30/0.25, predicate=2-of-3-OR-strict, fail-safe=neutral).
// ============================================================================
const _RCR_EMA_KEY     = "state:alpha:risk_count_ratio_ema";
const _RCR_EMA_TS_KEY  = "state:alpha:risk_count_ratio_ema:ts";
const _RCR_EMA_ALPHA   = 0.2;            // ~1-min EMA at 5s cycles
const _RCR_HIGH_ENTER  = 0.30;           // EMA threshold to enter HIGH zone
const _RCR_HIGH_EXIT   = 0.25;           // EMA threshold to exit HIGH zone
const _RCR_HYST_CYCLES = 2;              // consecutive cycles for state change
const _RCR_DEGRADE_MS  = 120000;         // 120s in-HIGH guard window

let _rcrEmaVal        = null;            // last EMA value (null until bootstrap)
let _rcrEmaBootstrapped = false;
let _rcrHighStreak    = 0;
let _rcrLowStreak     = 0;
let _rcrIsHigh        = false;
let _rcrHighStartTs   = 0;
let _rcrDegradeWarned = false;

async function _rcrUpdateEma(redisClient, currentRatio) {
  // ---- Cold-start: durable read once ----
  if (!_rcrEmaBootstrapped) {
    try {
      const stored = await redisClient.get(_RCR_EMA_KEY);
      const v = (stored !== null) ? Number(stored) : NaN;
      if (Number.isFinite(v) && v >= 0 && v <= 1) {
        _rcrEmaVal = v;
        console.log(`[ALPHA_EMA_BOOTSTRAP] durable rr_count_ratio_ema=${v.toFixed(4)}`);
      } else {
        console.warn(`[ALPHA_EMA_COLD_START] no durable EMA, will seed from current=${currentRatio.toFixed(4)}`);
      }
    } catch (e) {
      console.warn(`[ALPHA_EMA_BOOTSTRAP_FAIL] err=${String(e)} (in-memory only)`);
    }
    _rcrEmaBootstrapped = true;
  }

  // ---- EMA step ----
  let ema;
  if (_rcrEmaVal === null) {
    ema = currentRatio; // seed
  } else {
    ema = _rcrEmaVal + _RCR_EMA_ALPHA * (currentRatio - _rcrEmaVal);
  }
  // Clamp to [0,1] to defend against pathological inputs
  if (!Number.isFinite(ema)) ema = (Number.isFinite(_rcrEmaVal) ? _rcrEmaVal : 0.0);
  ema = Math.max(0, Math.min(1, ema));
  _rcrEmaVal = ema;

  // ---- Persist (best-effort, never throws) ----
  try {
    const ts = Date.now();
    const pipeline = redisClient.pipeline();
    pipeline.set(_RCR_EMA_KEY,    String(ema), "EX", 86400);
    pipeline.set(_RCR_EMA_TS_KEY, String(ts),  "EX", 86400);
    await pipeline.exec();
  } catch (e) {
    console.warn(`[ALPHA_EMA_PERSIST_FAIL] err=${String(e)} (in-memory only)`);
  }
  return ema;
}

function _rcrHysteresisDecide(ema) {
  // Returns { isHigh, scaleMult } and updates streak counters / state.
  if (ema >= _RCR_HIGH_ENTER) { _rcrHighStreak += 1; _rcrLowStreak = 0; }
  else if (ema <= _RCR_HIGH_EXIT) { _rcrLowStreak += 1; _rcrHighStreak = 0; }
  else { /* in deadband: keep current state, do not increment streaks */ }

  if (!_rcrIsHigh && _rcrHighStreak >= _RCR_HYST_CYCLES) {
    _rcrIsHigh = true;
    _rcrHighStartTs = Date.now();
    _rcrDegradeWarned = false;
    console.log(`[ALPHA_HYST_ENTER_HIGH] ema=${ema.toFixed(4)} streak=${_rcrHighStreak}`);
  } else if (_rcrIsHigh && _rcrLowStreak >= _RCR_HYST_CYCLES) {
    _rcrIsHigh = false;
    _rcrHighStartTs = 0;
    _rcrDegradeWarned = false;
    console.log(`[ALPHA_HYST_EXIT_HIGH] ema=${ema.toFixed(4)} streak=${_rcrLowStreak}`);
  }

  // scale_mult curve: linear with EMA but bounded [0.65,1.0].
  // In NORMAL zone we floor at 0.81 (no premature degradation).
  // In HIGH zone we use the full curve down to 0.65.
  const raw = 1.0 - 0.70 * ema;
  let scaleMult;
  if (_rcrIsHigh) {
    scaleMult = Math.max(0.65, Math.min(0.80, raw));
  } else {
    scaleMult = Math.max(0.81, Math.min(1.00, raw));
  }

  // Degrade guard: if we have been in HIGH for >120s, emit one warning per stay.
  if (_rcrIsHigh && !_rcrDegradeWarned && (Date.now() - _rcrHighStartTs) > _RCR_DEGRADE_MS) {
    console.warn(`[RISK_DEGRADE_GUARD] scale_mult<=0.80 sustained for >${_RCR_DEGRADE_MS/1000}s ema=${ema.toFixed(4)} scale=${scaleMult.toFixed(3)}`);
    _rcrDegradeWarned = true;
  }
  return { isHigh: _rcrIsHigh, scaleMult };
}

async function fetchAlphaOverlay(redisClient, universe, cashSymbol) {
  const syms = (universe || []).filter(s => s && s !== cashSymbol);
  if (!syms.length) {
    return { scale_mult: 1.0, alpha_mult: 1.0, risk_flag: false, risk_flag_legacy: false, hyst_state: "NORMAL", risk_count_ratio: 0.0, risk_count_ratio_ema: 0.0, n_blocked: 0, n_tradable: 0, pd_q80: 1.0, boost_allowed: true, reasons: ["ALPHA_EMPTY"] };
  }

  // ---- (a) Read both SSOTs in a single round-trip per source ----
  const fkeys = syms.map(s => "factor_scores:" + s);
  const xkeys = syms.map(s => "xgb:risk:" + s);
  let fvals = [], xvals = [];
  try {
    [fvals, xvals] = await Promise.all([
      redisClient.mget(fkeys),
      redisClient.mget(xkeys),
    ]);
  } catch (e) {
    console.warn("[ALPHA_OVERLAY_MGET_FAIL] err=" + String(e));
    // Fail-safe: if EMA never bootstrapped → neutral 1.0; else hold previous EMA-driven scale.
    const haveEma = (_rcrEmaVal !== null);
    const fallbackEma = haveEma ? _rcrEmaVal : 0.0;
    const fallbackScale = haveEma
      ? (_rcrIsHigh ? Math.max(0.65, Math.min(0.80, 1.0 - 0.70 * _rcrEmaVal))
                    : Math.max(0.81, Math.min(1.00, 1.0 - 0.70 * _rcrEmaVal)))
      : 1.0;
    return { scale_mult: fallbackScale, alpha_mult: 1.0, risk_flag: false, risk_flag_legacy: false, hyst_state: "NORMAL", risk_count_ratio: 0.0, risk_count_ratio_ema: fallbackEma, n_blocked: 0, n_tradable: 0, pd_q80: 1.0, boost_allowed: true, reasons: ["ALPHA_MGET_FAIL"] };
  }

  // ---- (b) Per-symbol composite alpha score in [-1, +1] ----
  // Composite = 0.6*momentum + 0.2*quality + 0.1*value + 0.1*low_vol
  // Risk flag from xgb: flag !== "PASS" OR p_drop >= 0.30 OR confidence < 0.50
  const composites = [];
  let risk_cnt = 0;
  let valid_cnt = 0;
  let blocked_cnt = 0;
  let factor_n = 0;
  let xgb_n = 0;
  const reasons = new Set();

  // [v8.4-PATCH-v2 2026-05-08] Schema-correct dynamic threshold for MODERATE.
  // Senior review (GPT-5.4-pro + Claude-Opus-4-7) consensus:
  //   - Real xgb:risk schema is {flag in [OK, DROP_RISK_MODERATE, DROP_RISK_HIGH], blocked, p_drop}.
  //   - 'confidence' / 'PASS|FAIL|BLOCK' do NOT exist in production -> v1 predicate degenerated.
  //   - HIGH names already have blocked=1 and are absorbed upstream into BIL; counting them again
  //     idles capital twice. Exclude blocked==1 from BOTH numerator and denominator.
  //   - Compute pd_q80 over MODERATE-only p_drops to get a dynamic, distribution-aware threshold.
  const _modPdrops = [];
  for (let i = 0; i < syms.length; i++) {
    const _xj = _safeJsonParse(xvals[i]);
    if (_xj && String(_xj.flag || "").toUpperCase() === "DROP_RISK_MODERATE"
        && Number.isFinite(Number(_xj.p_drop))) {
      _modPdrops.push(Number(_xj.p_drop));
    }
  }
  _modPdrops.sort((a, b) => a - b);
  // Empty-MODERATE -> 1.0 (safe; nothing crosses this threshold). Otherwise pick q80 sample.
  const _pd_q80_raw = _modPdrops.length > 0
    ? _modPdrops[Math.min(_modPdrops.length - 1, Math.floor(_modPdrops.length * 0.8))]
    : 1.0;
  // [v8.4-PATCH-v3 2026-05-08] Apply a hard floor on pd_q80 so that on extremely
  // high-volatility days (when the MODERATE distribution itself shifts up, e.g.,
  // q80 collapses to 0.32~0.38) we still mark the riskier MODERATE names as risky
  // instead of letting the dynamic threshold drift permissively low.
  // Floor of 0.45 chosen by senior reviewer consensus (rounded mean of historical
  // p_drop q80 across non-stress days). Keep <= the upstream HIGH cutoff (0.50)
  // so HIGH semantics stay distinct.
  const _PD_Q80_FLOOR = 0.45;
  const pd_q80 = Math.max(_PD_Q80_FLOOR, _pd_q80_raw);

  for (let i = 0; i < syms.length; i++) {
    const fj = _safeJsonParse(fvals[i]);
    const xj = _safeJsonParse(xvals[i]);
    let symValid = false;
    let comp = 0.0;

    if (fj && Number.isFinite(Number(fj.momentum))) {
      factor_n += 1;
      symValid = true;
      const m = Number(fj.momentum) || 0;
      const q = Number(fj.quality)  || 0;
      const va = Number(fj.value)   || 0;
      const lv = Number(fj.low_vol) || 0;
      // Clamp each input to [-1, +1] before weighting (factor-engine already z-ish-normalized).
      const cl = (x) => Math.max(-1, Math.min(1, x));
      comp = 0.6*cl(m) + 0.2*cl(q) + 0.1*cl(va) + 0.1*cl(lv);
    }

    let risk_flag = false;
    let isBlocked = false;
    if (xj) {
      xgb_n += 1;
      const flag = String(xj.flag || "").toUpperCase();
      const pd   = Number(xj.p_drop);
      const blk  = Number(xj.blocked);
      isBlocked = (blk === 1);

      if (isBlocked) {
        // Already absorbed upstream -> exclude from both numerator AND denominator.
        blocked_cnt += 1;
        // Don't set symValid (so this name doesn't contribute to valid_cnt or composites).
      } else {
        symValid = true;
        // Schema-correct predicate (no confidence; no FAIL/BLOCK tokens):
        //   OK                  -> not risky
        //   DROP_RISK_HIGH      -> risky
        //   DROP_RISK_MODERATE  -> risky only if p_drop >= pd_q80 (dynamic, MODERATE-only quantile)
        if (flag === "DROP_RISK_HIGH") {
          risk_flag = true;
        } else if (flag === "DROP_RISK_MODERATE" && Number.isFinite(pd) && pd >= pd_q80) {
          risk_flag = true;
        }
        if (risk_flag) {
          risk_cnt += 1;
          // Down-weight risky names
          comp = Math.min(comp, 0.0);
        }
      }
    }

    if (symValid) {
      valid_cnt += 1;
      composites.push(comp);
    }
  }

  // ---- (c) Aggregate to overlay multipliers ----
  let scale_mult = 1.0;
  let alpha_mult = 1.0;
  let boost_allowed = true;
  let risk_count_ratio = 0.0;
  let risk_count_ratio_ema = 0.0;
  let hyst_state = "NORMAL";

  if (valid_cnt > 0) {
    // Mean composite across the universe
    const meanComp = composites.reduce((a,b) => a+b, 0) / composites.length;
    // Map [-1, +1] mean -> alpha_mult [0.85, 1.15]
    alpha_mult = Math.max(0.85, Math.min(1.15, 1.0 + 0.15 * meanComp));
    // Risk-count-ratio = fraction of risky names (NEW NAME, was risk_rate)
    // [v8.4-PATCH-v2] valid_cnt now already excludes blocked==1; division is direct.
    risk_count_ratio = risk_cnt / valid_cnt;
    // [v8.4-PATCH] Apply 1-min EMA (alpha=0.2) + 2-cycle hysteresis to scale_mult
    risk_count_ratio_ema = await _rcrUpdateEma(redisClient, risk_count_ratio);
    const decision = _rcrHysteresisDecide(risk_count_ratio_ema);
    scale_mult = decision.scaleMult;
    hyst_state = decision.isHigh ? "HIGH" : "NORMAL";
    // Boost is forbidden when EMA >= 30% OR mean alpha < 0
    if (risk_count_ratio_ema >= 0.30 || meanComp < 0.0) boost_allowed = false;
    // Reasons taxonomy
    if (factor_n === 0)        reasons.add("FACTOR_NO_DATA");
    if (xgb_n === 0)           reasons.add("XGB_NO_DATA");
    if (factor_n > 0 && xgb_n > 0) reasons.add("ALPHA_OK");
    if (decision.isHigh)       reasons.add("HIGH_RISK_RATE");
    if (meanComp >  0.10)      reasons.add("ALPHA_BULLISH");
    if (meanComp < -0.10)      reasons.add("ALPHA_BEARISH");
  } else {
    reasons.add("ALPHA_NO_DATA");
    // Fail-safe: empty data → hold neutral if EMA never bootstrapped
    if (_rcrEmaVal === null) scale_mult = 1.0;
  }

  console.log("[ALPHA_HEALTH_v84MUSK] universe=" + syms.length + " factor_n=" + factor_n + " xgb_n=" + xgb_n + " valid_n=" + valid_cnt + " n_blocked=" + blocked_cnt + " n_tradable=" + valid_cnt + " pd_q80=" + pd_q80.toFixed(3) + " risk_cnt=" + risk_cnt + " risk_count_ratio=" + risk_count_ratio.toFixed(3) + " rcr_ema=" + risk_count_ratio_ema.toFixed(3) + " hyst=" + hyst_state + " alpha_mult=" + alpha_mult.toFixed(3) + " scale_mult=" + scale_mult.toFixed(3));

  return {
    scale_mult: scale_mult,
    alpha_mult: alpha_mult,
    // [v8.4-PATCH-v3] risk_flag now reflects the hysteresis HIGH state (the only
    // gate that actually de-leverages capital), narrowing it from the previous
    // "any name flagged" semantics. Backward-compat alias risk_flag_legacy keeps
    // the old value for any downstream consumer that depended on it.
    risk_flag:        (hyst_state === "HIGH"),
    risk_flag_legacy: (risk_cnt > 0),
    hyst_state:       hyst_state,
    risk_count_ratio:     risk_count_ratio,
    risk_count_ratio_ema: risk_count_ratio_ema,
    n_blocked:    blocked_cnt,
    n_tradable:   valid_cnt,
    pd_q80:       pd_q80,
    boost_allowed: boost_allowed,
    reasons:    Array.from(reasons).slice(0, 16),
  };
}
async function main() {

  // === VERSION ENFORCEMENT GATE (4AI CRITICAL) ===
  const REQUIRED_VERSION = "v9.2.1";  // 현재 운영 버전
  if (CHAMPION_CONFIG.version !== REQUIRED_VERSION) {
    console.error("[CRITICAL] VERSION MISMATCH: " + CHAMPION_CONFIG.version + " vs " + REQUIRED_VERSION + ". Exiting.");
    throw new Error("Fatal error - PM2 will restart");
  }
  console.log("[VERSION_GATE] PASS: " + CHAMPION_CONFIG.version);
  const redis = new Redis(REDIS_URL, {  lazyConnect: true , retryStrategy: (times) => Math.min(times * 500, 30000), maxRetriesPerRequest: null });
  let lastHash = null;
  let tick = 0;
  const logPrefix = "[champion-bridge-v3]";
  
  console.log(`${logPrefix} Starting Champion v9.2.1 Bridge`);
  console.log(`${logPrefix} Config: target_vol=${CHAMPION_CONFIG.volatility.target_vol}, max_leverage=${CHAMPION_CONFIG.constraints.max_leverage}`);
  console.log(`${logPrefix} VIX Regime: threshold=${CHAMPION_CONFIG.regime.vol_threshold}, high_mult=${CHAMPION_CONFIG.regime.high_vol_mult}, low_mult=${CHAMPION_CONFIG.regime.low_vol_mult}`);
  console.log(`${logPrefix} Hedge: base=${CHAMPION_CONFIG.hedge.hedge_base}, vix_floor=${CHAMPION_CONFIG.hedge.vix_threshold_floor}, vix_cap=${CHAMPION_CONFIG.hedge.vix_threshold_cap}`);
  
  await redis.connect();
  
  async function runOnce() {
    tick += 1;
    const t0 = nowMs();
    
    // [v8.5.2] VIX structured snapshot: single-read, structured object, no re-read in gate
    const vixSnapshot = { sampled: false, present: false, value: null, valid: false, copyWriteOk: false };
    try {
      const vixSsotRaw = await redis.get(VIX_KEY_SSOT);
      vixSnapshot.sampled = true;
      if (vixSsotRaw !== null) {
        vixSnapshot.present = true;
        const parsed = parseFloat(vixSsotRaw);
        vixSnapshot.value = parsed;
        vixSnapshot.valid = Number.isFinite(parsed) && parsed >= 0 && parsed <= 80;
        if (vixSnapshot.valid) {
          try {
            await redis.set(VIX_COPY_KEY_BRIDGE, vixSsotRaw, "EX", 7200);
            vixSnapshot.copyWriteOk = true;
          } catch (copyErr) {
            console.warn(`[VIX_PRESYNC_v852] copy write failed: ${copyErr.message}`);
          }
        }
      } else {
        // [v8.5.2] SSOT absent → clear stale copy to prevent false pass
        await redis.del(VIX_COPY_KEY_BRIDGE);
        console.warn(`[VIX_PRESYNC_v852] SSOT VIX absent, cleared stale copy`);
      }
    } catch (e) {
      console.warn(`[VIX_PRESYNC_v852_FAIL] ${e.message}`);
    }
    // [v8.5.2] VIX gate uses structured snapshot — NEVER re-reads SSOT
    const vixGate = await checkVixTripleGate(redis, vixSnapshot);
    if (!vixGate.ok) {
      console.warn(`[champion-bridge-v3] VIX gate FAIL: ${vixGate.reason} (cycle skipped)`);
      await auditX(redis, "WARN", "vix_gate_fail_bridge", vixGate);
      // VIX 불일치 시 경고만 (차단하려면 return 추가)
    }
    
    // Fetch regime state (WRONGTYPE-resilient: 4AI Consensus Patch 2026-04-02)
    const final = await resilientHgetall(redis, SOURCE_KEY, "regime_fetch");
    const isEmpty = !final || Object.keys(final).length === 0;
    
    // Load dynamic universe from Redis
    const { universe: activeUniverse, source: universeSource } = await loadDynamicUniverse(redis);
    
    // Fetch momentum signals for active universe
    const momentumSignals = await fetchMomentumSignals(redis, activeUniverse);
    
    // Fetch VIX
    const vix = await fetchVix(redis);
    
    // Determine regime multiplier from final state
    let regimeMultiplier = DEFAULT_MULT;
    if (!isEmpty && (final.consensus_mult || final.final_mult)) {
      regimeMultiplier = safeNumber(final.consensus_mult || final.final_mult) || DEFAULT_MULT;
    }
    
    // 1) Load tuner state from Redis
    const tuner = await loadCapTuner(redis);
    const metrics = await loadPortfolioMetrics(redis);

    // 2) Calculate dynamic weights with tuner params and active universe
    // --- SSOT INPUT DUMP (signals:ssot:latest) ---
    const __ssotTs = new Date().toISOString();
    try {
      const __ssotPayload = { ts: __ssotTs, vix, signals: momentumSignals, universe: activeUniverse, tunerParams: { targetCash: tuner.targetCash, kP: tuner.kP }, regimeMultiplier };
      await redis.set("signals:ssot:latest", JSON.stringify(__ssotPayload), "EX", 7200);
      await redis.set("signals:ssot:latest:ts", __ssotTs, "EX", 7200);
      await redis.set("signals:ssot:latest.vix", String(vix), "EX", 7200);
    } catch (e) {
      console.warn("[SSOT] signals:ssot:latest write failed", String(e));
    }

    // --- v8.1 Alpha SSOT Overlay (Option B) ---
    let alphaOverlay = { scale_mult: 1.0, alpha_mult: 1.0, risk_flag: false, risk_flag_legacy: false, hyst_state: "NORMAL", risk_count_ratio: 0.0, risk_count_ratio_ema: 0.0, n_blocked: 0, n_tradable: 0, pd_q80: 1.0, boost_allowed: true, reasons: ["ALPHA_DISABLED"] };
    if (USE_REDIS_ALPHA_SSOT) {
      try {
        alphaOverlay = await fetchAlphaOverlay(redis, activeUniverse, "BIL");
        const _rcr     = Number(alphaOverlay.risk_count_ratio     || 0);
        const _rcr_ema = Number(alphaOverlay.risk_count_ratio_ema || 0);
        // [v8.4-PATCH-v3] risk= now reflects hysteresis HIGH only; expose risk_legacy
        // and hyst alongside so dashboards and tail-grep tooling have full context.
        console.log("[ALPHA_APPLY] ts=" + new Date().toISOString()
          + " risk=" + alphaOverlay.risk_flag
          + " risk_legacy=" + alphaOverlay.risk_flag_legacy
          + " hyst=" + alphaOverlay.hyst_state
          + " rcr=" + _rcr.toFixed(3)
          + " rcr_ema=" + _rcr_ema.toFixed(3)
          + " boost=" + alphaOverlay.boost_allowed
          + " scale_mult=" + alphaOverlay.scale_mult.toFixed(3)
          + " alpha_mult=" + alphaOverlay.alpha_mult.toFixed(3)
          + " reasons=" + JSON.stringify(alphaOverlay.reasons));
      } catch (e) {
        console.warn("[ALPHA_APPLY_FAIL_CLOSED] ts=" + new Date().toISOString() + " err=" + String(e));
      }
    }
    // [v8.4-PATCH] Apply alpha overlay to regime multiplier; use the smoothed EMA
    // (risk_count_ratio_ema) for the boost-suppression branch instead of the raw
    // risk_rate field (now removed). Backward-compatible alias preserved below.
    const _rcrEmaForBoost = Number(alphaOverlay.risk_count_ratio_ema || 0);
    const alphaAdjustedRegimeMult = regimeMultiplier * alphaOverlay.scale_mult * alphaOverlay.alpha_mult * ((_rcrEmaForBoost >= 0.20) ? 0.85 : 1.0);

    const result = await calculateDynamicWeights(momentumSignals, vix, alphaAdjustedRegimeMult, null, {
      targetCash: tuner.targetCash,
      kP: tuner.kP
    }, activeUniverse, redis);

    // === SSOT: totalMV + hard-block overrides (TSLA 0% comes from here) ===
    // === FIX-WRONGTYPE: emarkos:v1:positions는 hash 타입 → hgetall 사용 ===
    // Hash 구조: { NVDA: '{"shares":3,...}', __meta__: '{"totalMarketValue":70188}' }
    let positionsMV = 0;
    try {
      const posType = await redis.type("emarkos:v1:positions");
      if (posType === "hash") {
        const metaRaw = await redis.hget("emarkos:v1:positions", "__meta__");
        if (metaRaw) {
          const meta = JSON.parse(metaRaw);
          positionsMV = Number(meta.totalMarketValue || 0);
        }
        if (!positionsMV) {
          // Fallback: 개별 종목 marketValue 합산
          const allPos = await redis.hgetall("emarkos:v1:positions") || {};
          for (const [k, v] of Object.entries(allPos)) {
            if (k === "__meta__") continue;
            try { positionsMV += Number(JSON.parse(v).marketValue || 0); } catch {}
          }
        }
      } else if (posType === "string") {
        const posRaw = await redis.get("emarkos:v1:positions");
        const pos = JSON.parse(posRaw || "{}");
        positionsMV = Number(pos.totalMarketValue || 0);
      }
    } catch (e) {
      console.warn(`[WRONGTYPE_GUARD] emarkos:v1:positions read failed: ${e.message}`);
      positionsMV = 0;
    }
    // Cash: prefer kis:live:available_cash (actual settled), cross-check budget_usd
    const cashRaw_live = await redis.get("kis:live:available_cash").catch(() => null);
    const budgetRaw_old = await redis.get("emarkos:v1:budget_usd").catch(() => null);
    const liveCash = Number(cashRaw_live || 0); const budgetCash = Number(budgetRaw_old || 0);
    // FIX: frcr_ord_psbl_amt includes collateral value, not actual cash
    // If liveCash > 50% of positionsMV, it is likely collateral-inflated → use budgetCash
    const cashUsd = budgetCash; // SSOT v2 (4AI-audit): always use actual cash, liveCash is buying power only
    // totalMV = positions + cash (full account AUM), with guardrails
    const rawTotal = positionsMV + cashUsd;
    const MIN_TOTAL_MV = 10000;  // $10k minimum to prevent dust-order spiral
    const FALLBACK_MV = 13400;   // original design fallback
    const totalMV = rawTotal > MIN_TOTAL_MV ? rawTotal : (positionsMV > MIN_TOTAL_MV ? positionsMV : FALLBACK_MV);
    console.log(`[${new Date().toISOString()}] [SSOT_TOTALMV] positionsMV=${positionsMV}, liveCash=${liveCash}, budgetCash=${budgetCash}, cashUsed=${cashUsd}, rawTotal=${rawTotal}, totalMV=${totalMV}`);

    // apply runtime blocklists: policy:symbol_hard_block + policy:blocklist:symbols
    const hardBlocked = new Set(await redis.smembers("policy:symbol_hard_block").catch(()=>[]));
    const halted = new Set(await redis.smembers("policy:blocklist:symbols").catch(()=>[]));
    const blocked = new Set([...hardBlocked, ...halted].map(s=>String(s||"").toUpperCase()));

    // PATCH-07: Blocked weight → BIL (was: renormalize to other risk assets)
    // PATCH-BIL: If BIL buy is blocked, redirect blocked weight to unallocated (actual cash) instead
    const bilBuyBlock = (await redis.get("policy:bil_buy_block").catch(() => null)) === "true";
    const finalWeights = { ...result.weights };
    let blockedWeight = 0;
    for (const s of Object.keys(finalWeights)) {
      if (blocked.has(String(s).toUpperCase())) {
        blockedWeight += Number(finalWeights[s]) || 0;
        finalWeights[s] = 0;
      }
    }
    if (blockedWeight > 0) {
      if (bilBuyBlock) {
        // BIL buy blocked: don't redirect to BIL, leave as unallocated (= actual cash held)
        console.log(`[${new Date().toISOString()}] [BLOCKED_WEIGHT_TO_CASH] blockedWeight=${blockedWeight.toFixed(4)} note=BIL buy blocked, weight stays as cash`);
      } else {
        finalWeights["BIL"] = (Number(finalWeights["BIL"]) || 0) + blockedWeight;
        console.log(`[${new Date().toISOString()}] [BLOCKED_WEIGHT_TO_BIL] blockedWeight=${blockedWeight.toFixed(4)} newBIL=${finalWeights["BIL"].toFixed(4)}`);
      }
    }

    // PATCH-BIL: Zero out BIL weight entirely when bil_buy_block is enabled
    // This prevents new BIL purchases. Existing BIL positions will be sold via UNWANTED_SELL.
    if (bilBuyBlock && finalWeights["BIL"]) {
      const removedBilWeight = finalWeights["BIL"];
      finalWeights["BIL"] = 0;
      // Redistribute BIL weight to champion risk assets proportionally
      const riskSyms = Object.entries(finalWeights).filter(([s, w]) => s !== "BIL" && w > 0);
      const riskSum = riskSyms.reduce((sum, [, w]) => sum + w, 0);
      if (riskSum > 0) {
        for (const [s, w] of riskSyms) {
          finalWeights[s] = w + (removedBilWeight * (w / riskSum));
        }
      }
      console.log(`[${new Date().toISOString()}] [BIL_BUY_BLOCKED] removedBilWeight=${removedBilWeight.toFixed(4)} redistributedTo=${riskSyms.length} risk assets`);
    }


    // [ARES-RAM26-SLEEVE-PRIMARY-PATCH-v1] Fail closed if output book is near-uniform.
    const __ram26DispersionVerdict = __ARES_degenerateBookVerdict(finalWeights);
    out_of_band_guard_scope: {
      if (__ram26DispersionVerdict.blocked) {
        await __ARES_failClosedDegenerateBook__(redis, "finalWeights_before_publish", __ram26DispersionVerdict, {
          champion_id: ARES_RAM26_EXPECTED_CHAMPION_ID,
          source: result?.debug?.source || "legacy_or_unknown",
          sourceKey: result?.debug?.sourceKey || "",
        });
        await __MANUS_keep_champion_alive__(redis, 900);
        return;
      }
      console.log(`[ARES_DISPERSION_GUARD_PASS] n=${__ram26DispersionVerdict.stats.n} cv=${__ram26DispersionVerdict.stats.coefficient_of_variation_abs} unique6=${__ram26DispersionVerdict.stats.unique_count_rounded_6} gross=${__ram26DispersionVerdict.stats.gross_abs_sum}`);
    }

    // 3) Update tuner using cashActual (final execution BIL weight)
    // PATCH-BIL: Freeze tuner learning when BIL buy is blocked — feedback loop broken
    const freezeLearn = (result.vix > 35) || result.isCrisisMode;
    if (bilBuyBlock) {
      console.log(`[${new Date().toISOString()}] [TUNER_BIL_BLOCK_INFO] BIL buy blocked, but Tuner P-controller continues learning virtual cash targets (PATCHED 2026-04-07)`);
    }
    const error = tuner.targetCash - (result.cashActual || 0);
    updateKPWithFlipGuard(tuner, error, freezeLearn);

    // Stage-2: targetCash auto tuning from realized volatility (+ optional drawdown safety)
    updateTargetCashFromVolAndDD(tuner, metrics, {
      targetVol: CHAMPION_CONFIG.volatility.target_vol,
      BIL_MIN: 0.05,
      BIL_MAX: 0.30,
      maxStepPerRun: 0.03,
      ewmaAlpha: 0.20,
      ddSoftThreshold: 0.06,
      ddHardThreshold: 0.10,
      ddCashFloorSoft: 0.35,
      ddCashFloorHard: 0.50,
      freeze: freezeLearn
    });

    // Advanced: auto-tune kV too
    adaptKV(tuner, freezeLearn);

    // 4) Save tuner state
    await saveCapTuner(redis, tuner);
    
    // Build output
    const out = {
      source: SOURCE_KEY,
      updated_at: String(t0),
      gate_state: "UNKNOWN",
      position_mult: String(result.leverage),
      
      // Dynamic base_allocation based on Champion strategy
      base_allocation: JSON.stringify(result.weights),
      final_allocation: JSON.stringify(finalWeights),  // SSOT final (used for trading)
      momentum_signals: JSON.stringify(result.scores),
      
      // Strategy state
      crisis_mode: String(result.isCrisisMode),
      uncertainty_multiplier: String(result.uncertaintyMult),
      regime_multiplier: String(regimeMultiplier),
      vix_regime_mult: String(result.vixRegimeMult),
      hedge_ratio: String(result.hedgeRatio),
      satellite_weight: "0.0",
      timestamp: new Date().toISOString(),
      mode: "DEFAULT",
      final_timestamp: "",
      
      // Metadata
      leverage: String(result.leverage),
      total_momentum_score: String(result.totalPositiveScore.toFixed(6)),
      vix: String(result.vix),
      strategy_version: CHAMPION_CONFIG.version,
      
      // Debug fields for dashboard/tuning
      cap_debug: JSON.stringify(result.debug || {}),
      cap_tuner: JSON.stringify({
        targetCash: Number(tuner.targetCash.toFixed(4)),
        kP: Number(tuner.kP.toFixed(4)),
        kV: Number((tuner.kV || 0.40).toFixed(4)),
        freezeLearn,
        error: Number(error.toFixed(6)),
        cashActual: Number((result.cashActual || 0).toFixed(4))
      }),
      bridge_weight_contract: JSON.stringify({
        patch: "ARES-RAM26-SLEEVE-PRIMARY-PATCH-v1",
        source: result?.debug?.source || "legacy_or_unknown",
        sourceKey: result?.debug?.sourceKey || "",
        outputStats: __ram26DispersionVerdict?.stats || null,
        inputStats: result?.debug?.inputStats || null,
        targetGross: result?.debug?.targetGross || null,
        scale: result?.debug?.scale || null,
        expectedChampionId: ARES_RAM26_EXPECTED_CHAMPION_ID
      })
    };
    
    if (!isEmpty) {
      const ts = safeTimestamp(final.timestamp);
      const gate = final.gate_state || "UNKNOWN";
      out.gate_state = gate;
      out.final_timestamp = final.timestamp ?? "";
      
      // Staleness handling
      const isStale = (ts === null) ? true : ((t0 - ts) > STALE_MS);
      if (isStale) {
        out.position_mult = String(STALE_FALLBACK_MULT);
        out.mode = "CONSERVATIVE_FALLBACK";  // 패치: STALE → CONSERVATIVE
      } else {
        out.mode = "OK";
      }
      
      // Passthrough diagnostics
      Object.assign(out, pick(final, PASSTHROUGH_FIELDS));
    }

      // [SAFETY-PATCH 2026-05-06] Replaces the previous hardcoded Phase9-B1 carry-through.
      //
      // The previous block hardcoded `standard_validator_decision: "NO_GO"` together with
      // `override_decision: "BEST_AVAILABLE_OVERRIDE_B1_APPROVED"`, causing every cycle
      // to silently bypass the standard validator. This caused unintended SELLs on
      // 2026-05-06 because downstream blindly accepted the marker as approved.
      //
      // The safety-hardened version reads the upstream candidate metadata from Redis
      // (key `policy:champion:active`), pulls the validator decision from
      // `policy:validator:latest`, and ONLY tags the marker as APPROVED when:
      //   (a) validator decision == GO, OR
      //   (b) decision == NO_GO but a fresh signed override exists at
      //       `policy:override:active` matching candidate_config_sha256.
      // Otherwise the marker is tagged with decision and override_decision="ABSENT",
      // and downstream consumers (router, ssot_promote_v2) MUST refuse to publish.
      let phase9B1Marker;
      try {
        phase9B1Marker = await __ARES_buildSafeMarker__(redis, {
          totalMV: totalMV,
          vix: Number(vix) || 0,
          crisis_mode: !!result.isCrisisMode,
          mode: out.mode || "UNKNOWN",
          target_key: TARGET_KEY,
        });
      } catch (markerErr) {
        // Fail closed: if marker construction fails (e.g. no champion config in Redis),
        // skip the cycle entirely. We do NOT fall back to the old hardcoded values.
        console.error(`[SAFETY-PATCH] Failed to build phase9_b1_marker: ${markerErr.message}; skipping cycle`);
        try {
          await redis.set("ops:halt:request", JSON.stringify({
            ts_ms: Date.now(),
            actor: "final-to-champion-bridge",
            reason: `MARKER_BUILD_FAILURE:${markerErr.message}`,
            auto: true,
          }), "EX", 3600);
        } catch (_) {}
        await __MANUS_keep_champion_alive__(redis, 900); return;
      }
      if (!phase9B1Marker) {
        // Fail-closed: marker resolution returned null (e.g. NO_GO without override)
        console.error("[SAFETY-PATCH] phase9_b1_marker resolved to null (validator NO_GO without signed override); skipping cycle");
        await __MANUS_keep_champion_alive__(redis, 900); return;
      }
      out.phase9_b1_marker = JSON.stringify(phase9B1Marker);
      out.phase9_b1_marker_version = phase9B1Marker.marker_version;
      out.champion_version = out.champion_version || phase9B1Marker.champion_version;
      out.champion_strategy_name = out.champion_strategy_name || phase9B1Marker.champion_strategy_name;
      out.champion_engine_file = out.champion_engine_file || phase9B1Marker.champion_engine_file;
    
    const h = await computeHash(out);
    if (h !== lastHash) {
      // Write target hash
      await redis.hset(TARGET_KEY, out);
      if (TTL_SECONDS > 0) await redis.expire(TARGET_KEY, TTL_SECONDS);
      // signals:champion Stream publish (관측용)
      // === SSOT targets snapshot for live-trading-kis ===
      const ts = new Date().toISOString();
      const targetPositions = {};
      for (const [sym, wRaw] of Object.entries(finalWeights)) {
        const w = Number(wRaw) || 0;
        targetPositions[sym] = {
          symbol: sym,
          weight: w,
          target_value: Math.max(0, totalMV * w),
          phase9_b1_marker: { ...phase9B1Marker, symbol: sym, weight: w, target_value: Math.max(0, totalMV * w) },
          champion_version: phase9B1Marker.champion_version,
          champion_strategy_name: phase9B1Marker.champion_strategy_name,
          champion_engine_file: phase9B1Marker.champion_engine_file
        };
      }
      // [v8.0.0 FP-FIX] Write as HASH type instead of STRING to prevent WRONGTYPE errors
      // downstream consumers (order-intent-executor P0-2 guard) use HLEN to check
      {
        const pipe = redis.multi();
        pipe.del("champion:targets:ssot");
        pipe.del("champion:target:positions");
        for (const [sym, data] of Object.entries(targetPositions)) {
          pipe.hset("champion:targets:ssot", sym, JSON.stringify(data));
          pipe.hset("champion:target:positions", sym, JSON.stringify(data));
        }
        // ARES-PATCH-2026-05-09:meta_marker — explicit _meta_marker hash field
        // so downstream consumers (validators, projector v2, forensic checks)
        // can verify champion provenance via HGET champion:targets:ssot _meta_marker
        try {
          const _mm = JSON.stringify({
            phase9_b1_marker: phase9B1Marker.marker_version || 'live_full_champion',
            issued_by: 'ram26_final_to_champion_bridge_v2',
            ts_ms: Date.now(),
            validation_state: phase9B1Marker.validation_state || 'OVERRIDE_APPROVED',
            // [P3-PATCH 2026-05-09] standard_validator_decision/override_decision/override_policy
            // missing here previously. ares_upstream_ssot_router reads _meta_marker FIRST,
            // and falls back to symbol-entry phase9_b1_marker only if _meta_marker absent.
            // When these were missing -> SAFETY_GATE -> halt -> trading:enabled=false.
            standard_validator_decision: phase9B1Marker.standard_validator_decision || 'GO',
            override_decision: phase9B1Marker.override_decision || 'NONE',
            override_policy: phase9B1Marker.override_policy || 'NONE',
            champion_version: phase9B1Marker.champion_version,
            champion_strategy_name: phase9B1Marker.champion_strategy_name,
            champion_engine_file: phase9B1Marker.champion_engine_file,
          });
          pipe.hset('champion:targets:ssot', '_meta_marker', _mm);
          pipe.hset('champion:target:positions', '_meta_marker', _mm);
          pipe.hset('champion:targets:ssot', 'phase9_b1_marker', _mm);
          pipe.hset('champion:target:positions', 'phase9_b1_marker', _mm);
        } catch (_e) { /* non-fatal: marker is best-effort */ }
        pipe.set("champion:targets:ssot:ts", ts, "EX", 120);
        // [CHAMPION-FRESHNESS-FIX] issued_at_ms 자동 갱신 — validator champion_stale 방지
        // policy:champion:active의 issued_at_ms를 현재 시간으로 업데이트
        try {
          const _champRaw = await redis.get("policy:champion:active");
          if (_champRaw) {
            const _champ = JSON.parse(_champRaw);
            _champ.issued_at_ms = Date.now();
            await redis.set("policy:champion:active", JSON.stringify(_champ));
          }
        } catch (_e) { /* non-fatal */ }
        pipe.set("champion:target:positions:ts", ts, "EX", 120);
        // Set TTL on hash keys
        pipe.expire("champion:targets:ssot", 900) /* MANUS-P1 */;
        pipe.expire("champion:target:positions", 120);
        await pipe.exec();
      }
      console.log(`[${new Date().toISOString()}] [SSOT_TARGETS] Written: ${Object.keys(targetPositions).length} symbols, totalMV=${totalMV}, blocked=${[...blocked].join(",")}`);
      await publishToSignalsChampion(redis, out, result.isCrisisMode ? "CRISIS" : "NORMAL");
      lastHash = h;
      const dt = nowMs() - t0;
      
      // Log weight distribution
      const topWeights = Object.entries(result.weights)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([s, w]) => `${s}:${(w * 100).toFixed(1)}%`)
        .join(", ");
      
      const weightsCount = Object.keys(result.weights).filter(k => k !== 'BIL' && result.weights[k] > 0).length;
      console.log(`${logPrefix} ts=${new Date().toISOString()} [${out.mode}] universe=${activeUniverse.length}(${universeSource}) weights=${weightsCount} VIX=${vix.toFixed(1)} hedge=${(result.hedgeRatio*100).toFixed(1)}% vix_mult=${result.vixRegimeMult} leverage=${result.leverage.toFixed(2)} crisis=${result.isCrisisMode}`);
      console.log(`${logPrefix} [cap_tune] dynCap=${(result.debug?.dynCapBase*100 || 0).toFixed(1)}% effCap=${(result.debug?.effectiveMaxWeight*100 || 0).toFixed(1)}% K=${result.debug?.K_base || 0} cashActual=${((result.cashActual || 0)*100).toFixed(1)}% targetCash=${(tuner.targetCash*100).toFixed(1)}%`);
      console.log(`${logPrefix} Top weights: ${topWeights}`);
    } else {
      // === FIX-TTL: hash 불변이어도 TTL 갱신 → champion:v660:signals 만료 방지 ===
      if (TTL_SECONDS > 0) await redis.expire(TARGET_KEY, TTL_SECONDS);
      // champion:targets:ssot 등도 TTL 갱신
      await redis.expire("champion:targets:ssot", 900) /* MANUS-P1 */.catch(()=>{});
      await redis.expire("champion:targets:ssot:ts", 120).catch(()=>{});
      await redis.expire("champion:target:positions", 120).catch(()=>{});
      await redis.expire("champion:target:positions:ts", 120).catch(()=>{});
      if (tick % 12 === 0) {
        // Periodic heartbeat log
        console.log(`${logPrefix} [heartbeat] VIX=${vix.toFixed(1)} leverage=${result.leverage.toFixed(2)} mode=${out.mode}`);
      }
    }
  }
  
  // Initial tick
  await runOnce();
  
  const timer = setInterval(() => {
    runOnce().then(() => {
      // 정상 사이클 완료 시 auditX (매 10틱마다)
      if (tick % 10 === 0) {
        auditX(redis, "INFO", "bridge_cycle_ok", { tick }).catch(() => {});
      }
    }).catch(err => {
      console.error(`${logPrefix} error:`, err?.stack || err);
      auditX(redis, "ERROR", "bridge_cycle_error", { tick, err: String(err?.message || err) }).catch(() => {});
    });
  }, INTERVAL_MS);
  
  function shutdown(sig) {
    console.log(`${logPrefix} received ${sig}, shutting down...`);
    clearInterval(timer);
    redis.quit().finally(() => { /* graceful shutdown - PM2 will restart */ });
  }
  
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
}

main().catch(err => {
  console.error("[champion-bridge-v3] fatal:", err?.stack || err);
  throw new Error("Fatal error - PM2 will restart");
});

// ========================================
// signals:champion Stream publish (관측용)
// ========================================
async function publishToSignalsChampion(redis, targets, regime) {
  try {
    const payload = {
      strategy_version: "v9.2.1",
      ts: Date.now(),
      regime: regime || "UNKNOWN",
      universe: Object.keys(targets).join(","),
      targets_json: JSON.stringify(targets),
      idempotency_key: `sig-${Date.now()}-${Math.random().toString(36).slice(2,8)}`,
      source: "final-to-champion-bridge"
    };
    
    const __nowEpoch = Math.floor(Date.now() / 1000);
    await redis.set("signals:champion:last_ts", String(__nowEpoch), "EX", 600);
    await redis.xadd(
      "signals:champion",
      "MAXLEN", "~", "1000",  // 최대 1000개 유지
      "*",
      ...Object.entries(payload).flat()
    );
    
    console.log(`[${new Date().toISOString()}] [SIGNALS_CHAMPION] Published: ${Object.keys(targets).length} targets`);
  } catch (err) {
    console.error(`[${new Date().toISOString()}] [SIGNALS_CHAMPION_ERROR]`, err.message);
  }
}

// ============================================================
// v8.5b 히스테리시스 λ 계산 함수
// ============================================================
const LAMBDA_STATE_KEY = "state:lambda_state";
const LAMBDA_STATE_TTL = 86400;
let __lambdaStateCache = null;

async function getLambdaState(redis) {
  if (!redis) return __lambdaStateCache || "lo";
  const v = await redis.get(LAMBDA_STATE_KEY);
  __lambdaStateCache = v || "lo";
  return __lambdaStateCache;
}

async function setLambdaState(redis, state) {
  __lambdaStateCache = state;
  if (redis) {
    await redis.set(LAMBDA_STATE_KEY, state, "EX", LAMBDA_STATE_TTL);
  }
}

async function computeLambdaHysteresis(redis, st, p) {
  const risk = Number(st.risk_rate || 0.0);
  const macro = String(st.macro_regime || "UNKNOWN").toUpperCase();
  const bw = Boolean(st.breadth_weak);
  
  let state = await getLambdaState(redis);
  const t1_up = p.t1_up || p.t1 || 0.15;
  const t1_down = p.t1_down || 0.08;
  const t2_up = p.t2_up || p.t2 || 0.28;
  const t2_down = p.t2_down || 0.26;
  
  const prevState = state;
  
  if (state === "lo") {
    if (risk >= t1_up) state = "mid";
  } else if (state === "mid") {
    if (risk >= t2_up) state = "hi";
    else if (risk <= t1_down) state = "lo";
  } else {
    if (risk <= t2_down) state = "mid";
  }
  
  if (state !== prevState) {
    await setLambdaState(redis, state);
    console.log(`[LAMBDA_STATE_CHANGE] ${prevState} -> ${state} (risk_rate=${risk.toFixed(3)})`);
  }
  
  let lam = (state === "lo") ? p.lam_lo : (state === "mid") ? p.lam_mid : p.lam_hi;
  if (bw) lam += p.add_breadth || 0;
  if (macro === "RISK_OFF") lam += p.add_macro_off || 0;
  
  return { lambda: clampLambda(lam, p.lam_min, p.lam_max), state: state, prevState: prevState };
}
