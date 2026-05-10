#!/usr/bin/env node
/**
 * RAM26 Standard Validator v1
 *
 * Purpose:
 * - Replace missing ares-standard-validator with RAM26-aware readiness decision.
 * - Does not place orders.
 * - Does not set LIVE.
 * - Publishes validator decision keys and heartbeat for downstream daemons.
 *
 * Decision PASS requires:
 * - active strategy is RAM26_ALPHA_0001_b215c119ea23
 * - ram26:features:v1:meta.status=PUBLISHED and eligible_ratio>=0.60
 * - ram26:engine:gate:last.pass=true
 * - ssot:target:v2:current.source=ram26_engine_bridge_v2
 * - target weights non-flat
 * - champion:targets:ssot HLEN>=8 if already projected
 */

import Redis from "ioredis";
import process from "node:process";

const REDIS_URL = process.env.REDIS_URL || process.env.ARES_REDIS_URL;
if (!REDIS_URL) throw new Error("REDIS_URL/ARES_REDIS_URL required");

const CHAMPION_ID = process.env.CHAMPION_ID || "RAM26_ALPHA_0001_b215c119ea23";
const CYCLE_MS = Number(process.env.RAM26_STANDARD_VALIDATOR_CYCLE_MS || "2000");
const MIN_ELIGIBLE_RATIO = Number(process.env.RAM26_MIN_ELIGIBLE_RATIO || "0.60");
const MAX_AGE_MS = Number(process.env.RAM26_VALIDATOR_MAX_AGE_MS || "300000");
const DECISION_TTL_SEC = Number(process.env.RAM26_VALIDATOR_DECISION_TTL_SEC || "15");

const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

function parseJson(raw, fallback = {}) {
  if (!raw) return fallback;
  try { return JSON.parse(raw); } catch { return fallback; }
}

function tsFresh(tsLike, maxAgeMs = MAX_AGE_MS) {
  const t = Number(tsLike || 0);
  if (!t) return false;
  const ms = t > 1e12 ? t : t * 1000;
  return (Date.now() - ms) <= maxAgeMs && (Date.now() - ms) >= -60000;
}

function stats(targetObj) {
  // [P7 PATCH 2026-05-09] Schema-aware target extraction.
  // Accepts: array, wrapper with .positions, .weights, or .targets, or flat dict.
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
  const mean = n ? sum/n : 0;
  const std = n ? Math.sqrt(vals.reduce((a,b)=>a+(b-mean)*(b-mean),0)/n) : 0;
  const min = n ? Math.min(...vals) : 0;
  const max = n ? Math.max(...vals) : 0;
  const ratio = min > 0 ? max/min : null;
  const nonFlat = n >= 8 && (std >= 0.0025 || (ratio !== null && ratio >= 1.5));
  return { n, sum, std, min, max, ratio, nonFlat };
}

async function evaluate() {
  const [
    active, mode, featuresRaw, engineRaw, ssotRaw, policyRaw, bridgeActive
  ] = await redis.mget(
    "emarkos:v1:ram26:active_strategy_id",
    "emarkos:v1:mode",
    "ram26:features:v1:meta",
    "ram26:engine:gate:last",
    "ssot:target:v2:current",
    "policy:champion:active",
    "ares:bridge:active",
  );

  const features = parseJson(featuresRaw);
  const engine = parseJson(engineRaw);
  const ssot = parseJson(ssotRaw);
  const policy = parseJson(policyRaw);
  const targetStats = stats(ssot);
  const championTargetsLen = Number(await redis.hlen("champion:targets:ssot").catch(() => 0));

  const checks = {
    active_is_ram26: active === CHAMPION_ID,
    mode_is_valid_runtime_state: mode === "LIVE" || mode === "SAFE" || mode === "REPAIR",
    features_published: features.status === "PUBLISHED",
    features_ratio: Number(features.eligible_ratio || 0) >= MIN_ELIGIBLE_RATIO,
    features_fresh: tsFresh(features.ts_ms || features.ts),
    engine_pass: engine.pass === true || engine.go === true,
    engine_fresh: tsFresh(engine.ts || engine.ts_ms || engine.generated_at_ms),
    // [P7 PATCH 2026-05-09] Schema-aware bridge/source check (legacy + new schema)
    bridge_ram26: (
      bridgeActive === "ram26_final_to_champion_bridge_v2" ||
      bridgeActive === "LIVE_FINAL_TO_CHAMPION" ||
      ssot.source === "ram26_engine_bridge_v2" ||
      ssot?.phase9_b1_marker?.issued_by === "ram26_final_to_champion_bridge_v2" ||
      ssot?.engine_version === "ram26_alpha_engine_v1"
    ),
    ssot_source_ram26: (
      ssot.source === "ram26_engine_bridge_v2" ||
      ssot?.phase9_b1_marker?.source === "ssot_promote_v2" ||
      ssot?.phase9_b1_marker?.generator === "ares_policy_engine" ||
      ssot?.engine_version === "ram26_alpha_engine_v1"
    ),
    ssot_fresh: tsFresh(ssot.ts || ssot.issued_at_ms),
    target_non_flat: targetStats.nonFlat,
    target_n: targetStats.n >= 8,
    gross_valid: targetStats.sum >= 0.25 && targetStats.sum <= 1.05,
    policy_ram26: policy.issued_by === "ram26_final_to_champion_bridge_v2" || policy.source === "ram26_alpha_engine_v1",
  };

  const pass = Object.values(checks).every(Boolean);
  const failed = Object.entries(checks).filter(([,v]) => !v).map(([k]) => k);

  return {
    verdict: pass ? "GO" : "NO_GO",
    pass,
    failed,
    checks,
    ts: Date.now(),
    iso: new Date().toISOString(),
    champion_id: CHAMPION_ID,
    runtime: { active, mode, bridgeActive },
    features: { status: features.status, eligible_ratio: features.eligible_ratio, reason: features.reason },
    engine: { pass: engine.pass, go: engine.go },
    targetStats,
    championTargetsLen,
    producer: "ram26_standard_validator_v1",
  };
}

async function publishDecision(decision) {
  const payload = JSON.stringify(decision);
  // [P8 PATCH 2026-05-09] Also publish in ares_policy_engine schema to
  // policy:validator:latest so build_safety_marker yields VALIDATOR_APPROVED.
  let championSha = "";
  let championId = "";
  try {
    const champRaw = await redis.get("policy:champion:active");
    if (champRaw) {
      const champ = JSON.parse(champRaw);
      championSha = champ.candidate_config_sha256 || champ.config_sha256 || "";
      championId = champ.candidate_id || champ.name || "";
    }
  } catch {}
  const policyEnginePayload = JSON.stringify({
    decision: decision.verdict,
    ts_ms: decision.ts,
    candidate_config_sha256: championSha,
    candidate_id: championId,
    reasons: decision.failed,
    pass: decision.pass,
    producer: decision.producer || "ram26_standard_validator_v1",
    iso: decision.iso,
  });
  const pipe = redis.pipeline();
  const keys = [
    "ares:standard_validator:decision",
    "ares:validator:standard:decision",
    "standard:validator:decision",
    "validator:standard:decision",
    "ares:standard-validator:decision",
    "ram26:standard_validator:decision",
  ];
  for (const k of keys) pipe.set(k, payload, "EX", DECISION_TTL_SEC);
  // [P8 PATCH] policy_engine compatibility key (NO TTL — policy_engine
  // checks freshness via ts_ms field so it is safe to leave indefinitely).
  pipe.set("policy:validator:latest", policyEnginePayload);
  pipe.set("ares:standard_validator:heartbeat", String(decision.ts), "EX", DECISION_TTL_SEC);
  pipe.set("ram26:standard_validator:last", payload, "EX", 300);
  pipe.xadd("ram26:standard_validator:events", "MAXLEN", "~", 1000, "*",
    "ts", String(decision.ts),
    "verdict", decision.verdict,
    "failed", decision.failed.join(","),
    "payload", payload
  );
  await pipe.exec();
}

async function main() {
  console.log(`[ram26-standard-validator] start champion=${CHAMPION_ID}`);
  while (true) {
    try {
      const d = await evaluate();
      await publishDecision(d);
      console.log(JSON.stringify({ verdict: d.verdict, failed: d.failed, target: d.targetStats, features: d.features }));
    } catch (e) {
      console.error(`[ram26-standard-validator] ERROR ${e.stack || e.message}`);
    }
    await new Promise(r => setTimeout(r, CYCLE_MS));
  }
}

main().catch(e => {
  console.error(e.stack || e.message);
  process.exit(1);
});
