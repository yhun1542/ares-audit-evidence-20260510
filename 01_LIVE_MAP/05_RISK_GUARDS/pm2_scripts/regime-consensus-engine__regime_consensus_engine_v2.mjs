// SOURCE: /home/ubuntu/ares_current/external/regime_consensus_engine_v2.mjs
// SIZE: 21742 chars

/**
 * ARES Regime Consensus Engine v2.0
 * ==================================
 * All regime keys are HASH type. Uses HGETALL to read them.
 *
 * Sources (all HASH):
 *   1. regime:current          → {regime, score, volScale, hedgeRatio, ...}
 *   2. regime:final:current    → {ms_regime, r15_regime, final_regime, gpu_regime, ...}
 *   3. regime:gate             → {state, cautionStreak, crisisStreak, clearStreak, ...}
 *   4. regime:ms:current       → HASH (multi-source)
 *   5. regime:r15:current      → HASH (R15 bridge)
 *   6. regime:hmm:current      → HASH (R11-S + DTCH HMM publisher; weight=0.10; opt-in via REGIME_HMM_ENABLED)
 *
 * Consensus: Weighted voting with cautious tiebreak.
 */

import Redis from "ioredis";

function resolveRequiredRedisUrl() {
  const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || "";
  if (!url) throw new Error("REDIS_URL or ARES_REDIS_URL is required");
  if (/localhost|127\.0\.0\.1/.test(url)) {
    throw new Error("Local Redis fallback is forbidden in production");
  }
  return url;
}

function recommendationToMultiplier(rec) {
  switch (String(rec || "").toUpperCase()) {
    case "FULL_EXPOSURE":
    case "NORMAL_EXPOSURE":
      return 1.0;
    case "REDUCED_EXPOSURE":
      return 0.75;
    case "DEFENSIVE":
      return 0.50;
    case "MINIMAL_EXPOSURE":
      return 0.25;
    case "REDUCE_ALL":
      return 0.0;
    default:
      return 0.5;
  }
}


const REDIS_URL = resolveRequiredRedisUrl();
const POLL_MS = parseInt(process.env.REGIME_POLL_MS || "10000");
const CONF_THRESHOLD = parseFloat(process.env.REGIME_CONFIDENCE_THRESHOLD || "0.6");
const HISTORY_MAX = parseInt(process.env.REGIME_HISTORY_MAXLEN || "10000");
const DRY_RUN = (process.env.REGIME_CONSENSUS_DRY_RUN || "false") === "true";

const REGIME_RISK = {
  BULLISH: 0, NORMAL: 1, NEUTRAL: 2, TRANSITION: 3,
  CAUTION: 4, BEARISH: 5, CRISIS: 6, HALTED: 7, UNKNOWN: 3
};

function envBool(name, defaultValue = false) {
  const v = process.env[name];
  if (v === undefined || v === null || v === "") return defaultValue;
  return ["1", "true", "yes", "on"].includes(String(v).trim().toLowerCase());
}

// HMM source feature flag (v1.2.3):
// - `REGIME_HMM_ENABLED=true`  => consensus reads regime:hmm:current as an extra source.
// - `REGIME_HMM_FULL_LIVE=true` => HMM is given 100% consensus weight ONLY when it is fresh AND meaningful.
//
// v1.2.3 hardening (post-incident from v1.2.2 cutover):
//   Gate 1 (Emergency-Off):    Redis key `regime:hmm:live_emergency_off`. SET => instant legacy v2.0 weights.
//   Gate 2 (Fallback-Detect):  HMM source whose source_tag/input_source is `fallback*` => HMM down-weighted to fallbackScaledHmmWeights() (FIXED 10/90 split, env-INDEPENDENT).
//   Gate 3 (CRISIS short-circuit): if >=2 non-HMM sources vote CRISIS/BEAR/BEARISH/HALTED but HMM votes NORMAL,
//                                  the engine ignores HMM's 100% weight and reverts to legacy v2.0 weights.
//   v1.2.3 MUST-1 fix: Gate 2 was using scaledHmmWeights() which depends on REGIME_HMM_WEIGHT env (=1.0 in FULL_LIVE),
//                      causing weights[hmm]=1.0 even when mode='SCALED_HMM_10_LEGACY_90'. Now uses fallbackScaledHmmWeights() (always 10/90).
//   v1.2.3 NICE-1: payload invariant self-check added — if mode==SCALED but hmmW!=0.10 OR mode==FULL_LIVE_HMM_100 but hmmW!=1.0 etc., logs INVARIANT VIOLATION.
// All gates are evaluated PER CYCLE; emergency-off is also polled by a 5s hot-reader so ops can flip it without restart.
const REGIME_HMM_ENABLED = envBool("REGIME_HMM_ENABLED", false);
const REGIME_HMM_FULL_LIVE = envBool("REGIME_HMM_FULL_LIVE", false);
const REGIME_HMM_WEIGHT = (() => {
  const v = parseFloat(process.env.REGIME_HMM_WEIGHT || (REGIME_HMM_FULL_LIVE ? "1.0" : "0.10"));
  return Number.isFinite(v) && v >= 0 && v <= 1.0 ? v : (REGIME_HMM_FULL_LIVE ? 1.0 : 0.10);
})();
const REGIME_HMM_FRESH_MS = (() => {
  const min = parseFloat(process.env.REGIME_HMM_FRESH_MIN || "30");
  return (Number.isFinite(min) && min > 0 ? min : 30) * 60 * 1000;
})();
// v1.2.3: emergency-off polling interval (defaults to 5s; configurable via REGIME_HMM_EMERGENCY_POLL_SEC).
const REGIME_HMM_EMERGENCY_POLL_SEC = (() => {
  const v = parseFloat(process.env.REGIME_HMM_EMERGENCY_POLL_SEC || "5");
  return Number.isFinite(v) && v >= 1 && v <= 60 ? v : 5;
})();
// v1.2.3: shared mutable flag updated by the hot-reader. Default OFF (=> HMM enabled).
let EMERGENCY_OFF_ACTIVE = false;
let EMERGENCY_OFF_VALUE = "";

const WEIGHTS_BASE_V20 = {
  // [R-005] 가중치 재조정: multi-source 상향, self-reference 축소
  "regime:current": 0.25,
  "regime:final:current": 0.20,
  "regime:gate": 0.10,
  "regime:ms:current": 0.25,
  "regime:r15:current": 0.10,
  "regime:final:sub:ms": 0.05,
  "regime:final:sub:r15": 0.05,
  "regime:final:sub:gpu": 0.00
};

function scaledHmmWeights() {
  if (!REGIME_HMM_ENABLED) return { ...WEIGHTS_BASE_V20 };
  const remaining = Math.max(0, 1.0 - REGIME_HMM_WEIGHT);
  const out = {};
  for (const [k, v] of Object.entries(WEIGHTS_BASE_V20)) out[k] = v * remaining;
  out["regime:hmm:current"] = REGIME_HMM_WEIGHT;
  return out;
}

// v1.2.3 MUST-1: fallback-only weights. ALWAYS 10/90 split, IGNORES REGIME_HMM_WEIGHT env.
// Critical bug fix from v1.2.2 incident: in FULL_LIVE mode REGIME_HMM_WEIGHT=1.0, which made
// scaledHmmWeights() return HMM=1.0 even when Gate-2 (fallback) detected non-meaningful HMM signal.
// This caused mode='SCALED_HMM_10_LEGACY_90' but weights[hmm]=1.0 -- the opposite of intent.
const FALLBACK_HMM_WEIGHT = 0.10;
function fallbackScaledHmmWeights() {
  if (!REGIME_HMM_ENABLED) return { ...WEIGHTS_BASE_V20 };
  const remaining = 1.0 - FALLBACK_HMM_WEIGHT; // = 0.90, env-independent
  const out = {};
  for (const [k, v] of Object.entries(WEIGHTS_BASE_V20)) out[k] = v * remaining;
  out["regime:hmm:current"] = FALLBACK_HMM_WEIGHT;
  return out;
}

// v1.2.3: returns one of three modes via { weights, mode, gateReason? }:
//   FULL_LIVE_HMM_100      - HMM is fresh, meaningful, no CRISIS divergence => HMM weight=1.0
//   SCALED_HMM_10_LEGACY_90 - HMM enabled but down-weighted (default 0.10) due to fallback or v1.1 mode
//   LEGACY_V20_PURE        - HMM disabled by config, emergency-off, stale, or CRISIS short-circuit
function effectiveWeightsForSources(sources) {
  if (!REGIME_HMM_ENABLED) {
    return { weights: { ...WEIGHTS_BASE_V20 }, mode: "LEGACY_V20_PURE", gateReason: "REGIME_HMM_ENABLED=false" };
  }
  // Gate 1: emergency-off (Redis hot-reader)
  if (EMERGENCY_OFF_ACTIVE) {
    return { weights: { ...WEIGHTS_BASE_V20 }, mode: "LEGACY_V20_PURE", gateReason: `EMERGENCY_OFF=${EMERGENCY_OFF_VALUE || "1"}` };
  }
  const hmm = sources["regime:hmm:current"];
  if (!hmm) {
    // stale or missing => legacy v2.0 (existing v1.2 behavior preserved)
    return { weights: { ...WEIGHTS_BASE_V20 }, mode: "LEGACY_V20_PURE", gateReason: "hmm_stale_or_missing" };
  }
  // Gate 2: fallback detection by source_tag / input_source on the raw hash
  const raw = hmm.raw || {};
  const tag = String(raw.source_tag || raw.input_source || "").toLowerCase();
  const isFallback = tag === "" ? false : (tag.startsWith("fallback") || tag === "fallback_default" || tag === "fallback_features");
  if (REGIME_HMM_FULL_LIVE && !isFallback) {
    // Gate 3: CRISIS short-circuit
    let crisisVotes = 0;
    const stressLabels = new Set(["CRISIS", "BEAR", "BEARISH", "HALTED"]);
    for (const [k, d] of Object.entries(sources)) {
      if (k === "regime:hmm:current") continue;
      if (stressLabels.has(d.regime)) crisisVotes++;
    }
    if (crisisVotes >= 2 && hmm.regime === "NORMAL") {
      return { weights: { ...WEIGHTS_BASE_V20 }, mode: "LEGACY_V20_PURE", gateReason: `crisis_short_circuit_votes=${crisisVotes}` };
    }
    const out = {};
    for (const k of Object.keys(WEIGHTS_BASE_V20)) out[k] = 0.0;
    out["regime:hmm:current"] = 1.0;
    return { weights: out, mode: "FULL_LIVE_HMM_100", gateReason: "" };
  }
  // v1.2.3: FULL_LIVE off OR HMM is fallback => fallbackScaledHmmWeights() (FIXED 10/90 split, env-independent)
  return { weights: fallbackScaledHmmWeights(), mode: "SCALED_HMM_10_LEGACY_90", gateReason: isFallback ? `hmm_fallback(tag=${tag || "unknown"})` : "full_live_disabled" };
}

const WEIGHTS = scaledHmmWeights();

const redis = new Redis(REDIS_URL, {
  retryStrategy: (t) => Math.min(t * 200, 5000),
  maxRetriesPerRequest: 3,
  tls: REDIS_URL.startsWith("rediss://") ? {} : undefined
});

redis.on("error", (e) => console.error("[REDIS]", e.message));
redis.on("connect", () => console.log("[INFO] Redis connected"));

function norm(raw) {
  if (!raw) return "UNKNOWN";
  const u = String(raw).trim().toUpperCase();
  if (u.includes("BULL")) return "BULLISH";
  if (u.includes("BEAR")) return "BEARISH";
  if (u.includes("CRISIS")) return "CRISIS";
  if (u.includes("HALT")) return "HALTED";
  if (u.includes("CAUTION")) return "CAUTION";
  if (u.includes("TRANSITION")) return "TRANSITION";
  if (u.includes("NEUTRAL")) return "NEUTRAL";
  if (u.includes("NORMAL")) return "NORMAL";
  if (u === "NO_GATE") return "NORMAL";
  if (u === "GATE_ACTIVE") return "CAUTION";
  return "UNKNOWN";
}

async function hgetSafe(key) {
  try {
    const t = await redis.type(key);
    if (t === "hash") return await redis.hgetall(key);
    if (t === "string") {
      const v = await redis.get(key);
      try { return JSON.parse(v); } catch { return { value: v }; }
    }
    if (t === "set") {
      const members = await redis.smembers(key);
      return { members };
    }
    return null;
  } catch (e) {
    console.warn(`[WARN] ${key}: ${e.message}`);
    return null;
  }
}

async function readSources() {
  const sources = {};

  // 1. regime:current
  const rc = await hgetSafe("regime:current");
  if (rc && rc.regime) {
    sources["regime:current"] = {
      regime: norm(rc.regime),
      score: parseFloat(rc.score || 0),
      raw: rc
    };
  }

  // 2. regime:final:current (has sub-regimes)
  const rf = await hgetSafe("regime:final:current");
  if (rf) {
    if (rf.final_regime) {
      sources["regime:final:current"] = {
        regime: norm(rf.final_regime),
        score: parseFloat(rf.final_mult || 1),
        raw: rf
      };
    }
    // Sub-sources from final
    if (rf.ms_regime) {
      sources["regime:final:sub:ms"] = {
        regime: norm(rf.ms_regime),
        score: parseFloat(rf.ms_score || 0),
        raw: { ms_regime: rf.ms_regime, ms_score: rf.ms_score }
      };
    }
    if (rf.r15_regime) {
      sources["regime:final:sub:r15"] = {
        regime: norm(rf.r15_regime),
        score: parseFloat(rf.r15_mult || 1),
        raw: { r15_regime: rf.r15_regime }
      };
    }
    if (rf.gpu_regime) {
      sources["regime:final:sub:gpu"] = {
        regime: norm(rf.gpu_regime),
        score: parseFloat(rf.gpu_confidence || 0),
        raw: { gpu_regime: rf.gpu_regime, gpu_oos_auc: rf.gpu_oos_auc }
      };
    }
  }

  // 3. regime:gate
  const rg = await hgetSafe("regime:gate");
  if (rg && rg.state) {
    sources["regime:gate"] = {
      regime: norm(rg.state),
      score: rg.state === "NO_GATE" ? 0.8 : 0.2,
      raw: rg
    };
  }

  // 4. regime:ms:current
  const rms = await hgetSafe("regime:ms:current");
  if (rms) {
    const regime = rms.regime || rms.label || rms.value || Object.values(rms)[0];
    if (regime) {
      sources["regime:ms:current"] = {
        regime: norm(regime),
        score: parseFloat(rms.score || rms.confidence || 0.5),
        raw: rms
      };
    }
  }

  // 5. regime:r15:current
  const rr = await hgetSafe("regime:r15:current");
  if (rr) {
    const regime = rr.regime || rr.label || rr.value || Object.values(rr)[0];
    if (regime) {
      sources["regime:r15:current"] = {
        regime: norm(regime),
        score: parseFloat(rr.score || rr.confidence || 0.5),
        raw: rr
      };
    }
  }

  // 6. regime:hmm:current (R11-S + DTCH HMM publisher; opt-in)
  if (REGIME_HMM_ENABLED) {
    const rhmm = await hgetSafe("regime:hmm:current");
    if (rhmm) {
      const lastUpdate = await redis.get("regime:hmm:last_update").catch(() => null);
      const parsedLastUpdate = lastUpdate ? Date.parse(lastUpdate) : NaN;
      const ageMs = Date.now() - parsedLastUpdate;
      const stale = (
        !lastUpdate ||
        !Number.isFinite(parsedLastUpdate) ||
        !Number.isFinite(ageMs) ||
        ageMs < 0 ||
        ageMs > REGIME_HMM_FRESH_MS
      );

      if (stale) {
        console.warn("[hmm] stale/invalid/missing regime:hmm:last_update; HMM source skipped this cycle");
      } else {
        // Conservative safety: if DTCH brake is active, the vote must become CRISIS.
        // The consensus engine votes by regime label; it does not consume the score as a scalar.
        const brakeOn = String(rhmm.dtch_brake || "0") === "1";
        const regime = brakeOn ? "CRISIS" : (rhmm.regime || rhmm.label || rhmm.value);
        const normalized = norm(regime);
        const score = parseFloat(rhmm.score || rhmm.confidence || 0.5);
        if (normalized !== "UNKNOWN") {
          // v1.2.3: preserve raw fields so effectiveWeightsForSources() can read source_tag / input_source.
          sources["regime:hmm:current"] = {
            regime: normalized,
            score: Number.isFinite(score) ? score : 0.5,
            raw: rhmm,
            source_tag: rhmm.source_tag || "",
            input_source: rhmm.input_source || ""
          };
        }
      }
    }
  }

  return sources;
}

function computeConsensus(sources) {
  const eff = effectiveWeightsForSources(sources);
  const weights = eff.weights;
  const votes = {};
  let totalW = 0;
  let anyHalted = false;
  const details = [];

  for (const [key, data] of Object.entries(sources)) {
    const w = Object.prototype.hasOwnProperty.call(weights, key) ? weights[key] : 0.05;
    const r = data.regime;
    if (r === "HALTED") anyHalted = true;
    if (r !== "UNKNOWN") {
      votes[r] = (votes[r] || 0) + w;
      totalW += w;
    }
    details.push({ source: key, regime: r, weight: w, score: data.score || 0 });
  }

  if (anyHalted) {
    return { consensus: "HALTED", confidence: 1.0, method: "HALTED_OVERRIDE",
             sources: details, votes, recommendation: "REDUCE_ALL", riskLevel: 7 };
  }

  if (totalW === 0) {
    return { consensus: "UNKNOWN", confidence: 0, method: "NO_DATA",
             sources: details, votes, recommendation: "HOLD", riskLevel: 3 };
  }

  const normVotes = {};
  for (const [r, v] of Object.entries(votes)) normVotes[r] = v / totalW;

  const sorted = Object.entries(normVotes).sort((a, b) => b[1] - a[1]);
  const topR = sorted[0][0], topC = sorted[0][1];

  let consensus, method;
  if (topC >= CONF_THRESHOLD) {
    consensus = topR; method = "MAJORITY_VOTE";
  } else if (sorted.length >= 2) {
    const [r1, r2] = [sorted[0][0], sorted[1][0]];
    consensus = (REGIME_RISK[r1] ?? 3) >= (REGIME_RISK[r2] ?? 3) ? r1 : r2;
    method = "CAUTIOUS_TIEBREAK";
  } else {
    consensus = "CAUTION"; method = "DEFAULT_CAUTION";
  }

  const risk = REGIME_RISK[consensus] ?? 3;
  const rec = risk <= 1 ? "FULL_EXPOSURE" : risk <= 2 ? "NORMAL_EXPOSURE" :
              risk <= 3 ? "REDUCED_EXPOSURE" : risk <= 4 ? "DEFENSIVE" :
              risk <= 5 ? "MINIMAL_EXPOSURE" : "REDUCE_ALL";

  // v1.2.3 NICE-1: payload invariant self-check (mode <-> weights consistency).
  // If a violation is detected, log it loudly so monitor/alerts can catch it. Does NOT break the cycle.
  const hmmW = weights["regime:hmm:current"] || 0;
  const tolerance = 0.01;
  let invariantViolation = "";
  if (eff.mode === "FULL_LIVE_HMM_100" && Math.abs(hmmW - 1.0) > tolerance) {
    invariantViolation = `mode=FULL_LIVE_HMM_100 but hmmW=${hmmW} (expected ~1.0)`;
  } else if (eff.mode === "SCALED_HMM_10_LEGACY_90" && Math.abs(hmmW - FALLBACK_HMM_WEIGHT) > tolerance) {
    invariantViolation = `mode=SCALED_HMM_10_LEGACY_90 but hmmW=${hmmW} (expected ~${FALLBACK_HMM_WEIGHT})`;
  } else if (eff.mode === "LEGACY_V20_PURE" && hmmW !== 0) {
    invariantViolation = `mode=LEGACY_V20_PURE but hmmW=${hmmW} (expected 0)`;
  }
  if (invariantViolation) {
    console.error(`[INVARIANT VIOLATION] ${invariantViolation} | gateReason=${eff.gateReason}`);
  }

  return { consensus, confidence: topC, method, sources: details,
           votes: normVotes, riskLevel: risk, recommendation: rec,
           hmmFullLive: REGIME_HMM_FULL_LIVE, effectiveWeights: weights,
           effectiveMode: eff.mode, effectiveGateReason: eff.gateReason || "",
           invariantViolation };
}

async function publish(result) {
  const timestamp = new Date().toISOString();
  const payload = { ...result, timestamp, engine: "regime-consensus-v2.1" };
  const json = JSON.stringify(payload);

  if (DRY_RUN) {
    console.log("[DRY_RUN]", json.substring(0, 300));
    return;
  }

  const key = "regime:consensus:current";
  const keyType = await redis.type(key);
  if (keyType !== "none" && keyType !== "hash") {
    const legacyRaw = await redis.get(key).catch(() => null);
    if (legacyRaw !== null) {
      await redis.set(`${key}:legacy:${Date.now()}`, legacyRaw, "EX", 86400).catch(() => {});
    }
    await redis.del(key);
  }

  const mult = recommendationToMultiplier(result.recommendation);
  const fields = {
    consensus: result.consensus,
    confidence: String(result.confidence),
    method: result.method,
    recommendation: result.recommendation,
    riskLevel: String(result.riskLevel),
    timestamp,
    engine: "regime-consensus-v2.1",
    payload_json: json,
    votes_json: JSON.stringify(result.votes || {}),
    sources_json: JSON.stringify(result.sources || []),
    final_regime: result.consensus,
    ms_regime: result.consensus,
    final_mult: String(mult),
    consensus_mult: String(mult),
  };
  const flat = [];
  for (const [k, v] of Object.entries(fields)) flat.push(k, String(v ?? ""));
  await redis.hset(key, ...flat);
  await redis.expire(key, 3600);
  await redis.set("regime:consensus:current:json", json, "EX", 3600);
  await redis.xadd("regime:consensus:history", "MAXLEN", "~", String(HISTORY_MAX), "*",
    "consensus", result.consensus, "confidence", String(result.confidence.toFixed(3)),
    "method", result.method, "recommendation", result.recommendation, "payload", json);
  await redis.set("feature:regime_consensus:enabled", "true");
  await redis.set("regime:consensus:last_update", timestamp);
  // [RAM26-COMPAT] regime:current 호환 HASH 쓰기 (schema-validator 요구사항)
  // regime:current는 legacy 소비자(order-flow-governor-v2, live-trading-kis)가 읽는 키
  try {
    const compatFields = {
      regime: result.consensus,
      consensus_regime: result.consensus,
      confidence: String(result.confidence),
      recommendation: result.recommendation,
      ts: timestamp,
      source: "regime-consensus-v2.1",
    };
    const compatFlat = [];
    for (const [k, v] of Object.entries(compatFields)) compatFlat.push(k, String(v ?? ""));
    await redis.hset("regime:current", ...compatFlat);
    await redis.expire("regime:current", 600);  // 10분 TTL (schema max=600s)
  } catch (e) {
    console.warn("[WARN] regime:current compat write failed:", e.message);
  }
}

let cycle = 0, lastC = null;

async function run() {
  cycle++;
  try {
    const src = await readSources();
    const n = Object.keys(src).length;
    if (n === 0) { console.log(`[CYCLE ${cycle}] No sources`); return; }

    const res = computeConsensus(src);
    const changed = lastC !== res.consensus;
    if (changed || cycle % 30 === 0) {
      console.log(`[CYCLE ${cycle}] consensus=${res.consensus} conf=${res.confidence.toFixed(2)} ` +
        `method=${res.method} rec=${res.recommendation} sources=${n}`);
      if (changed) {
        console.log(`[CHANGE] ${lastC || "INIT"} -> ${res.consensus}`);
        for (const s of res.sources) console.log(`  ${s.source}: ${s.regime} (w=${s.weight})`);
      }
    }
    await publish(res);
    lastC = res.consensus;
  } catch (e) { console.error(`[CYCLE ${cycle}] Error:`, e.message); }
}

// v1.2.3: emergency-off hot-reader. Polls Redis every REGIME_HMM_EMERGENCY_POLL_SEC seconds.
// Setting `regime:hmm:live_emergency_off` in Redis (any non-empty string) instantly forces consensus to LEGACY_V20_PURE.
// DEL of the key restores the previous mode without restarting consensus.
async function emergencyOffHotReader() {
  try {
    const v = await redis.get("regime:hmm:live_emergency_off");
    const wasOn = EMERGENCY_OFF_ACTIVE;
    if (v && String(v).trim() !== "") {
      EMERGENCY_OFF_ACTIVE = true;
      EMERGENCY_OFF_VALUE = String(v);
      if (!wasOn) console.warn(`[hmm:emergency_off] ACTIVATED value="${EMERGENCY_OFF_VALUE}" — consensus reverts to LEGACY_V20_PURE`);
    } else {
      EMERGENCY_OFF_ACTIVE = false;
      EMERGENCY_OFF_VALUE = "";
      if (wasOn) console.warn(`[hmm:emergency_off] CLEARED — consensus may resume HMM-aware mode on next cycle`);
    }
  } catch (e) {
    // Hot-reader failure should NEVER throw. Keep current EMERGENCY_OFF_ACTIVE state.
    console.error(`[hmm:emergency_off] poll error: ${e && e.message || e}`);
  }
}

console.log("=".repeat(60));
console.log("  ARES Regime Consensus Engine v2.1 (v1.2.3 hardened: 3 gates + emergency-off + invariant + fallback-fix)");
console.log(`  Poll: ${POLL_MS}ms | Threshold: ${CONF_THRESHOLD} | DryRun: ${DRY_RUN}`);
console.log(`  HMM enabled: ${REGIME_HMM_ENABLED} | HMM full-live: ${REGIME_HMM_FULL_LIVE} | HMM weight: ${REGIME_HMM_WEIGHT.toFixed(3)} | HMM fresh min: ${(REGIME_HMM_FRESH_MS / 60000).toFixed(1)}`);
console.log(`  v1.2.3 gates: emergency-off (poll=${REGIME_HMM_EMERGENCY_POLL_SEC}s) + fallback-detect (HMM=${FALLBACK_HMM_WEIGHT} fixed) + crisis-short-circuit`);
console.log(`  v1.2.3 invariant: payload mode<->weights consistency self-check enabled`);
console.log(`  Config weights (v1.1 mode default): ${JSON.stringify(WEIGHTS)}`);
console.log("=".repeat(60));
console.log(`[hmm:emergency_off] hot-reader starting, poll=${REGIME_HMM_EMERGENCY_POLL_SEC}s, key=regime:hmm:live_emergency_off`);

// v1.2.3: prime the emergency-off flag synchronously before the first run() so the first cycle already respects it.
await emergencyOffHotReader();
setInterval(emergencyOffHotReader, REGIME_HMM_EMERGENCY_POLL_SEC * 1000);

await run();
setInterval(run, POLL_MS);

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, async () => {
    console.log(`\n[SHUTDOWN] ${sig}`);
    try { await redis.set("regime:consensus:status", "STOPPED"); await redis.quit(); } catch {}
    /* process.exit(0) removed for PM2 */ return;
  });
}
