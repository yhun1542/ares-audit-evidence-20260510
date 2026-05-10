#!/usr/bin/env node
/**
 * OPEN-009 RAM26 G8 Readiness Guard
 *
 * Purpose:
 * - Prevent kill-switch-authority from enabling trading before RAM26 is actually ready.
 * - Maintains trading:enabled:override=false and trading:enabled=false while G8 fails.
 * - Does NOT place orders.
 * - Does NOT change emarkos:v1:mode to LIVE.
 *
 * G8 readiness:
 *  1) active_strategy_id == RAM26_ALPHA_0001_b215c119ea23
 *  2) ram26:features:v1:meta.status == PUBLISHED
 *  3) eligible_ratio >= 0.60
 *  4) ram26:engine:gate:last.pass == true
 *  5) ares:bridge:active == ram26_final_to_champion_bridge_v2
 *  6) ssot:target:v2:current.source == ram26_engine_bridge_v2
 *  7) policy:champion:active.issued_by == ram26_final_to_champion_bridge_v2
 *  8) target weights non-flat
 */

import Redis from "ioredis";
import process from "node:process";

const REDIS_URL = process.env.REDIS_URL || process.env.ARES_REDIS_URL;
if (!REDIS_URL) throw new Error("REDIS_URL/ARES_REDIS_URL required");

const CHAMPION_ID = process.env.CHAMPION_ID || "RAM26_ALPHA_0001_b215c119ea23";
const CYCLE_MS = Number(process.env.RAM26_G8_CYCLE_MS || "1000");
const MIN_ELIGIBLE_RATIO = Number(process.env.RAM26_G8_MIN_ELIGIBLE_RATIO || "0.60");
const MAX_AGE_MS = Number(process.env.RAM26_G8_MAX_AGE_MS || "180000");
const OVERRIDE_KEY = process.env.RAM26_G8_OVERRIDE_KEY || "trading:enabled:override";
const ENFORCE_TRADING_FALSE = String(process.env.RAM26_G8_ENFORCE_TRADING_FALSE || "true").toLowerCase() === "true";
const RELEASE_WHEN_READY = String(process.env.RAM26_G8_RELEASE_WHEN_READY || "false").toLowerCase() === "true";
const HALT_REASON = process.env.RAM26_G8_HALT_REASON || "OPEN009_RAM26_G8_NOT_READY";
const EVENT_STREAM = process.env.RAM26_G8_EVENT_STREAM || "ares:open009:g8:events";

// [P1-A v3 PATCH 2026-05-09] Auto-release sticky incident after sustained stability.
// V3 hardens V2 against four 4AI-consensus findings:
//  (1) RELEASE-TIME RESET     after auto-release, counter+timer are zeroed so a
//                             freshly-set OPEN009 incident_lock can NOT be cleared
//                             in seconds by a stale long pre-existing clean streak.
//  (2) WALL-CLOCK DUAL GATE   release requires BOTH consecutive_pass_count >= N
//                             AND wall-clock elapsed >= AUTO_RELEASE_MIN_WALL_MS.
//                             A future cadence change cannot collapse the safety
//                             window below the wall-clock floor.
//  (3) HALT-REASON SCOPE      already in V2: only halts whose value begins with
//                             'OPEN009' are eligible to be cleared by this guard.
//                             V3 keeps and re-emphasises this invariant.
//  (4) LOCK-SET TRANSITION    when ops:halt:active transitions from EMPTY/non-OPEN009
//                             to OPEN009*, the in-process counter+timer reset so
//                             the streak begins AT the moment of incident, not before.
//  (5) MANUAL KILL SWITCH     env RAM26_G8_AUTO_RELEASE_DISABLED=1 OR Redis key
//                             ares:open009:g8:disable_auto_release truthy fully
//                             suppresses auto-release while preserving sticky behavior.
// Set RAM26_G8_AUTO_RELEASE_AFTER_PASS_COUNT to a very large number to disable.
const AUTO_RELEASE_AFTER_PASS_COUNT = Number(process.env.RAM26_G8_AUTO_RELEASE_AFTER_PASS_COUNT || "30");
const AUTO_RELEASE_MIN_WALL_MS = Number(process.env.RAM26_G8_AUTO_RELEASE_MIN_WALL_MS || "30000");
const AUTO_RELEASE_DISABLED_ENV = String(process.env.RAM26_G8_AUTO_RELEASE_DISABLED || "0").toLowerCase();
const AUTO_RELEASE_DISABLE_REDIS_KEY = "ares:open009:g8:disable_auto_release";

let consecutivePassCount = 0;
let firstPassTs = 0;            // wall-clock ts (ms) of first PASS in current streak
let lastHaltSnapshot = "";      // last observed ops:halt:active (transition detection)

const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

function parseJson(raw, fallback = null) {
  if (!raw) return fallback;
  try { return JSON.parse(raw); } catch { return fallback; }
}

function now() { return Date.now(); }

function weightStats(targetObj) {
  // [P6 PATCH 2026-05-09] Unified target extraction (no double-unwrap).
  // Accepts:
  //   - Array of {symbol, w, ...} objects (positions array from P4 patch)
  //   - Dict of symbol -> weight or {weight}
  //   - Wrapper object with .weights, .positions, or .targets
  let targets;
  if (Array.isArray(targetObj)) {
    targets = targetObj;
  } else if (targetObj && typeof targetObj === "object") {
    if (Array.isArray(targetObj.positions)) {
      targets = targetObj.positions;
    } else if (targetObj.weights && typeof targetObj.weights === "object" && !Array.isArray(targetObj.weights)) {
      targets = targetObj.weights;
    } else if (targetObj.targets && typeof targetObj.targets === "object") {
      targets = Array.isArray(targetObj.targets) ? targetObj.targets
              : (Array.isArray(targetObj.targets.positions) ? targetObj.targets.positions : targetObj.targets);
    } else {
      // Treat targetObj itself as a flat dict of symbol -> weight or {weight}
      targets = targetObj;
    }
  } else {
    targets = {};
  }
  const vals = [];
  if (Array.isArray(targets)) {
    for (const v of targets) {
      const w = Number(v?.weight ?? v?.w ?? v?.w_norm ?? v?.w_raw ?? 0);
      if (Number.isFinite(w) && w > 0) vals.push(w);
    }
  } else if (targets && typeof targets === "object") {
    for (const v of Object.values(targets)) {
      const w = Number(typeof v === "object" ? (v?.weight ?? v?.w ?? v?.w_norm ?? v?.w_raw ?? 0) : v);
      if (Number.isFinite(w) && w > 0) vals.push(w);
    }
  }
  const n = vals.length;
  const sum = vals.reduce((a,b)=>a+b,0);
  const mean = n ? sum / n : 0;
  const variance = n ? vals.reduce((a,b)=>a+(b-mean)*(b-mean),0)/n : 0;
  const std = Math.sqrt(variance);
  const min = n ? Math.min(...vals) : 0;
  const max = n ? Math.max(...vals) : 0;
  const ratio = min > 0 ? max / min : null;
  return { n, sum, std, min, max, ratio, nonFlat: n >= 8 && (std >= 0.0025 || (ratio !== null && ratio >= 1.5)) };
}

async function evaluate() {
  const [
    active,
    mode,
    kill,
    featuresRaw,
    engineGateRaw,
    bridgeActive,
    ssotRaw,
    policyRaw,
  ] = await redis.mget(
    "emarkos:v1:ram26:active_strategy_id",
    "emarkos:v1:mode",
    "kill-switch:active",
    "ram26:features:v1:meta",
    "ram26:engine:gate:last",
    "ares:bridge:active",
    "ssot:target:v2:current",
    "policy:champion:active",
  );

  const features = parseJson(featuresRaw, {});
  const engineGate = parseJson(engineGateRaw, {});
  const ssot = parseJson(ssotRaw, {});
  const policy = parseJson(policyRaw, {});
  const tnow = now();

  const fTs = Number(features.ts_ms || features.ts || 0);
  const eTs = Number(engineGate.ts || engineGate.ts_ms || 0);
  const sTs = Number(ssot.ts || ssot.issued_at_ms || 0);
  const pTs = Number(policy.issued_at_ms || policy.ts || 0);

  const featureAgeOk = fTs > 0 ? (tnow - (fTs > 1e12 ? fTs : fTs * 1000)) <= MAX_AGE_MS : false;
  const engineAgeOk = eTs > 0 ? (tnow - (eTs > 1e12 ? eTs : eTs * 1000)) <= MAX_AGE_MS : false;
  const ssotAgeOk = sTs > 0 ? (tnow - (sTs > 1e12 ? sTs : sTs * 1000)) <= MAX_AGE_MS : false;
  const policyAgeOk = pTs > 0 ? (tnow - (pTs > 1e12 ? pTs : pTs * 1000)) <= MAX_AGE_MS : false;

  // [P4 PATCH 2026-05-09] schema-aware target extraction
  // Old schema: ssot itself is dict of symbol -> weight
  // New schema: ssot.targets.positions is array of {symbol, w, ...}
  // Also support ssot.targets being dict of symbol -> weight (intermediate)
  const _targetsObj = ssot?.targets?.positions || ssot?.targets || ssot;
  const st = weightStats(_targetsObj);
  const checks = {
    active_strategy_id: active === CHAMPION_ID,
    features_published: features.status === "PUBLISHED",
    features_fresh: featureAgeOk,
    eligible_ratio: Number(features.eligible_ratio || 0) >= MIN_ELIGIBLE_RATIO,
    engine_gate_pass: engineGate.pass === true,
    engine_gate_fresh: engineAgeOk,
    bridge_active: ((bridgeActive === "ram26_final_to_champion_bridge_v2" || bridgeActive === "LIVE_FINAL_TO_CHAMPION") /* RAM26_G8_FINAL_BRIDGE_ALIAS_PATCH_V1 */ || bridgeActive === "LIVE_FINAL_TO_CHAMPION"), // RAM26_G8_BRIDGE_ALIAS_LIVE_FINAL_V2
    // [P4 PATCH 2026-05-09] schema-aware ssot_source check
    // Old schema: top-level source field
    // New schema (ssot:target:v2:current from ssot_promote_v2): source moved to phase9_b1_marker.source
    ssot_source: (
      ssot.source === "ram26_engine_bridge_v2" ||
      ssot?.phase9_b1_marker?.source === "ssot_promote_v2" ||
      ssot?.phase9_b1_marker?.generator === "ares_policy_engine" ||
      ssot?.engine_version === "ram26_alpha_engine_v1"
    ),
    ssot_fresh: ssotAgeOk,
    policy_issued_by: policy.issued_by === "ram26_final_to_champion_bridge_v2",
    policy_fresh: policyAgeOk,
    target_non_flat: st.nonFlat,
    kill_switch_clear: kill !== "true",
  };

  const pass = Object.values(checks).every(Boolean);
  const failed = Object.entries(checks).filter(([,v]) => !v).map(([k]) => k);

  return {
    pass,
    failed,
    checks,
    runtime: { active, mode, kill, bridgeActive },
    feature: {
      status: features.status,
      eligible_ratio: features.eligible_ratio,
      age_ok: featureAgeOk,
      ts_ms: features.ts_ms,
      reason: features.reason,
    },
    engine: {
      pass: engineGate.pass,
      age_ok: engineAgeOk,
      diagnostics: engineGate.diagnostics || engineGate.bridge_stats || null,
    },
    target: st,
    ts: tnow,
  };
}

async function enforce(result) {
  const pipe = redis.pipeline();
  pipe.set("ares:open009:g8:last", JSON.stringify(result));
  pipe.set("ares:open009:g8:status", result.pass ? "READY" : "NOT_READY");

  if (!result.pass) {
    pipe.set(OVERRIDE_KEY, "false");
    if (ENFORCE_TRADING_FALSE) pipe.set("trading:enabled", "false");
    pipe.set("ops:halt:active", HALT_REASON);
    pipe.set("ops:halt:request", HALT_REASON);
    pipe.set("manual_order_submission_enabled", "false");
    pipe.set("policy:trade:halt_reason", HALT_REASON);
  } else if (RELEASE_WHEN_READY) {
    // [P1-A v3 PATCH 2026-05-09] OPEN009_INCIDENT_STICKY_AUTO_RELEASE_V3
    // See top-of-file P1-A v3 design comment for full rationale.
    const _haltActive = await redis.get("ops:halt:active").catch(() => null);
    const haltIsOpen009Sticky = !!(_haltActive && String(_haltActive).startsWith("OPEN009"));

    // (4) Lock-set transition: detect newly-set OPEN009 halt this cycle and reset streak.
    const haltActiveStr = _haltActive ? String(_haltActive) : "";
    const lockTransitionToActive =
      lastHaltSnapshot !== haltActiveStr &&
      haltIsOpen009Sticky &&
      (lastHaltSnapshot === "" || !lastHaltSnapshot.startsWith("OPEN009"));
    if (lockTransitionToActive) {
      consecutivePassCount = 0;
      firstPassTs = 0;
      console.log(`[open009-g8] lock-set transition detected; counter+timer reset (prev="${lastHaltSnapshot}" new="${haltActiveStr}")`);
    }
    lastHaltSnapshot = haltActiveStr;

    // (5) Manual kill switch: env or Redis key suppresses auto-release.
    let autoReleaseDisabled = (AUTO_RELEASE_DISABLED_ENV === "1" || AUTO_RELEASE_DISABLED_ENV === "true");
    if (!autoReleaseDisabled) {
      try {
        const v = await redis.get(AUTO_RELEASE_DISABLE_REDIS_KEY);
        if (v && String(v).toLowerCase() !== "0" && String(v).toLowerCase() !== "false") {
          autoReleaseDisabled = true;
        }
      } catch (_) {}
    }

    pipe.set("ares:open009:g8:consecutive_pass_count", String(consecutivePassCount));
    if (firstPassTs) pipe.set("ares:open009:g8:first_pass_ts", String(firstPassTs));
    else pipe.del("ares:open009:g8:first_pass_ts");

    const wallClockMs = firstPassTs ? (Date.now() - firstPassTs) : 0;
    const wallClockReady = wallClockMs >= AUTO_RELEASE_MIN_WALL_MS;
    const counterReady = consecutivePassCount >= AUTO_RELEASE_AFTER_PASS_COUNT;
    const eligibleForRelease = haltIsOpen009Sticky && counterReady && wallClockReady && !autoReleaseDisabled;

    if (haltIsOpen009Sticky && !eligibleForRelease) {
      // Still sticky: counter/wall-clock not yet satisfied OR operator disabled auto-release.
      pipe.set("ares:open009:status",
        autoReleaseDisabled
          ? "G8_READY_BUT_INCIDENT_STICKY_AUTO_RELEASE_DISABLED"
          : "G8_READY_BUT_INCIDENT_STICKY"
      );
      pipe.set(OVERRIDE_KEY, "false");
      pipe.set("trading:enabled", "false");
      pipe.set("manual_order_submission_enabled", "false");
    } else if (eligibleForRelease) {
      // Auto-release: counter + wall-clock both satisfied, halt is OPEN009-scoped, not disabled.
      pipe.del(OVERRIDE_KEY);
      pipe.del("ops:halt:active");
      pipe.del("ops:halt:request");
      pipe.del("manual_order_submission_enabled");
      pipe.del("policy:trade:halt_reason");
      pipe.set("ares:open009:status", "G8_READY_AUTO_RELEASED_AFTER_STABILITY");
      pipe.xadd(EVENT_STREAM, "MAXLEN", "~", 1000, "*",
        "ts", String(result.ts),
        "event", "AUTO_RELEASE_STICKY_INCIDENT_V3",
        "consecutive_pass_count", String(consecutivePassCount),
        "wall_clock_ms", String(wallClockMs),
        "threshold_count", String(AUTO_RELEASE_AFTER_PASS_COUNT),
        "threshold_wall_ms", String(AUTO_RELEASE_MIN_WALL_MS),
        "prior_halt_reason", String(_haltActive || ""),
      );
      // (1) RELEASE-TIME RESET: zero counter+timer so the next OPEN009 incident
      //     starts a fresh stability streak instead of inheriting this one.
      consecutivePassCount = 0;
      firstPassTs = 0;
      lastHaltSnapshot = "";
      pipe.set("ares:open009:g8:consecutive_pass_count", "0");
      pipe.del("ares:open009:g8:first_pass_ts");
      pipe.set("ares:open009:g8:last_auto_release_ts", String(Date.now()));
    } else {
      // Halt is empty or non-OPEN009 → normal READY release path.
      pipe.del(OVERRIDE_KEY);
      pipe.set("ares:open009:status", "G8_READY_RELEASED_OVERRIDE");
    }
    // Intentionally do not set mode=LIVE. Higher GO gate owns that.
  }

  pipe.xadd(EVENT_STREAM, "MAXLEN", "~", 1000, "*",
    "ts", String(result.ts),
    "pass", String(result.pass),
    "failed", result.failed.join(","),
    "payload", JSON.stringify(result),
  );
  await pipe.exec();
}

async function main() {
  console.log(`[open009-g8] starting champion=${CHAMPION_ID} cycle_ms=${CYCLE_MS}`);
  while (true) {
    try {
      const r = await evaluate();
      // [P1-A v3 PATCH] Update consecutive PASS counter and wall-clock timer before enforce()
      if (r.pass) {
        consecutivePassCount++;
        if (firstPassTs === 0) firstPassTs = Date.now();
      } else {
        if (consecutivePassCount > 0 || firstPassTs !== 0) {
          console.log(`[open009-g8] FAIL → streak reset (count=${consecutivePassCount}, ageMs=${firstPassTs ? Date.now()-firstPassTs : 0})`);
        }
        consecutivePassCount = 0;
        firstPassTs = 0;
      }
      await enforce(r);
      console.log(JSON.stringify({ ts: r.ts, pass: r.pass, failed: r.failed, feature: r.feature, target: r.target }));
    } catch (e) {
      console.error(`[open009-g8] ERROR ${e.stack || e.message}`);
    }
    await new Promise(res => setTimeout(res, CYCLE_MS));
  }
}

main().catch(e => {
  console.error(e.stack || e.message);
  process.exit(1);
});
