#!/usr/bin/env node
/**
 * RAM26 Target SSOT Projector v2
 * ----------------------------------------------------------------------------
 * Purpose:
 * - Project RAM26 ssot:target:v2:current into champion:targets:ssot hash AND
 *   legacy champion:target hash, using ticker symbols as keys (never numeric
 *   indices) and propagating the upstream phase9_b1_marker / _meta_marker.
 * - Fix v1 BUGs:
 *   (1) Object.entries(array) produced numeric keys "0".."N-1" — now uses
 *       symbol field from each entry (or the upstream weights dict).
 *   (2) phase9_b1_marker / _meta_marker was never propagated to
 *       champion:targets:ssot, causing ares-upstream-ssot-router to FailClosed.
 *   (3) legacy champion:target was never refreshed alongside, breaking SLO
 *       guards that monitor champion:target HASH emptiness.
 *
 * Strictness:
 * - RAM26_PROJECTOR_REQUIRE_META_MARKER (default true): refuse to publish if
 *   no marker is found from any of the 4 upstream sources.
 * - RAM26_PROJECTOR_ALLOW_SYNTHETIC_MARKER (default false): never synthesize
 *   a marker locally; only propagate genuine upstream-issued markers.
 *
 * Forbidden by design:
 * - Does NOT write ssot:target:v2:current.
 * - Does NOT write policy:champion:active.
 * - Does NOT change emarkos:v1:mode or trading:enabled.
 * - Does NOT submit any order or fill event.
 *
 * Backward compat:
 * - Keeps the same PM2 process name "ram26-target-ssot-projector".
 * - Keeps the same status keys ram26:target_ssot_projector:last and
 *   stream ram26:target_ssot_projector:events.
 */
import Redis from "ioredis";
import process from "node:process";

const REDIS_URL = process.env.REDIS_URL || process.env.ARES_REDIS_URL;
if (!REDIS_URL) throw new Error("REDIS_URL/ARES_REDIS_URL required");

const CHAMPION_ID = process.env.CHAMPION_ID || "RAM26_ALPHA_0001_b215c119ea23";
const CYCLE_MS = Number(process.env.RAM26_TARGET_PROJECTOR_CYCLE_MS || "2000");
const TARGET_TTL_SEC = Number(process.env.RAM26_TARGET_SSOT_TTL_SEC || "300");
const REQUIRE_SAFE_MODE = String(process.env.RAM26_TARGET_REQUIRE_SAFE_MODE || "true").toLowerCase() === "true";
const REQUIRE_META_MARKER = String(process.env.RAM26_PROJECTOR_REQUIRE_META_MARKER || "true").toLowerCase() === "true";
const ALLOW_SYNTHETIC_MARKER = String(process.env.RAM26_PROJECTOR_ALLOW_SYNTHETIC_MARKER || "false").toLowerCase() === "true";
const WRITE_LEGACY_CHAMPION_TARGET = String(process.env.RAM26_PROJECTOR_WRITE_LEGACY_CHAMPION_TARGET || "true").toLowerCase() === "true";

const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

function parseJson(raw, fallback = {}) {
  if (!raw) return fallback;
  try { return JSON.parse(raw); } catch { return fallback; }
}

// ---------------------------------------------------------------------------
// Symbol map extraction (FIX 1+2): handles array, object-with-symbol-field,
// object keyed by ticker, object keyed by numeric string + value.symbol, and
// the upstream weights dict as ultimate fallback.
// ---------------------------------------------------------------------------
function extractSymbolMap(ssot) {
  const out = {};
  const tarr = ssot.targets;
  // Case A: array form — most common
  if (Array.isArray(tarr)) {
    for (const e of tarr) {
      if (!e || typeof e !== "object") continue;
      const sym = String(e.symbol || e.ticker || "").trim();
      if (!sym || /^\d+$/.test(sym)) continue; // skip numeric pseudo-symbols
      out[sym] = e;
    }
  } else if (tarr && typeof tarr === "object") {
    // Case B: dict form
    for (const [k, v] of Object.entries(tarr)) {
      if (!v) continue;
      // B1: numeric-keyed but value carries .symbol
      if (/^\d+$/.test(String(k))) {
        const sym = String(v?.symbol || v?.ticker || "").trim();
        if (sym && !/^\d+$/.test(sym)) {
          out[sym] = (typeof v === "object") ? v : { symbol: sym, weight: Number(v) };
        }
        continue;
      }
      // B2: ticker-keyed
      out[k] = (typeof v === "object") ? v : { symbol: k, weight: Number(v) };
    }
  }
  // Fallback C: weights dict if extraction yielded nothing
  if (Object.keys(out).length === 0 && ssot.weights && typeof ssot.weights === "object") {
    for (const [sym, w] of Object.entries(ssot.weights)) {
      if (!sym || /^\d+$/.test(sym)) continue;
      const wn = Number(w);
      if (!Number.isFinite(wn) || wn <= 0) continue;
      out[sym] = { symbol: sym, weight: wn, side: "BUY", target_value: 0 };
    }
  }
  return out;
}

function targetStats(symMap) {
  const vals = [];
  for (const v of Object.values(symMap || {})) {
    const w = Number(v?.weight ?? v?.w ?? v?.w_norm ?? v?.w_raw ?? 0);
    if (Number.isFinite(w) && w > 0) vals.push(w);
  }
  const n = vals.length;
  const sum = vals.reduce((a, b) => a + b, 0);
  const mean = n ? sum / n : 0;
  const std = n ? Math.sqrt(vals.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n) : 0;
  const min = n ? Math.min(...vals) : 0;
  const max = n ? Math.max(...vals) : 0;
  const ratio = min > 0 ? max / min : null;
  return { n, sum, std, min, max, ratio, nonFlat: n >= 8 && (std >= 0.0025 || (ratio !== null && ratio >= 1.5)) };
}

// ---------------------------------------------------------------------------
// Marker resolution (FIX 3): 4-step priority. Returns {marker, source} or null.
// ---------------------------------------------------------------------------
async function resolveMetaMarker(ssot) {
  if (ssot && ssot._meta_marker && typeof ssot._meta_marker === "object") {
    return { marker: ssot._meta_marker, source: "ssot._meta_marker" };
  }
  if (ssot && ssot.phase9_b1_marker && typeof ssot.phase9_b1_marker === "object") {
    return { marker: ssot.phase9_b1_marker, source: "ssot.phase9_b1_marker" };
  }
  const pcaRaw = await redis.get("policy:champion:active");
  const pca = parseJson(pcaRaw, null);
  if (pca && typeof pca === "object") {
    if (pca._meta_marker && typeof pca._meta_marker === "object") {
      return { marker: pca._meta_marker, source: "policy:champion:active._meta_marker" };
    }
    if (pca.phase9_b1_marker && typeof pca.phase9_b1_marker === "object") {
      return { marker: pca.phase9_b1_marker, source: "policy:champion:active.phase9_b1_marker" };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Main projection cycle
// ---------------------------------------------------------------------------
async function runOnce() {
  const [active, mode, raw] = await redis.mget(
    "emarkos:v1:ram26:active_strategy_id",
    "emarkos:v1:mode",
    "ssot:target:v2:current",
  );
  const ssot = parseJson(raw);
  const symMap = extractSymbolMap(ssot);
  const st = targetStats(symMap);
  const reasons = [];

  if (active !== CHAMPION_ID) reasons.push(`active_mismatch:${active}`);
  if (REQUIRE_SAFE_MODE && mode === "LIVE") reasons.push("mode_live_refuse_projection");
  if (ssot.source !== "ram26_engine_bridge_v2") reasons.push(`bad_source:${ssot.source}`);
  if (!st.nonFlat) reasons.push("target_flat_or_too_small");
  if (st.sum < 0.25 || st.sum > 1.05) reasons.push(`gross_invalid:${st.sum}`);
  if (st.n < 8) reasons.push(`n_too_small:${st.n}`);

  let metaResolved = null;
  if (reasons.length === 0) {
    metaResolved = await resolveMetaMarker(ssot);
    if (!metaResolved && REQUIRE_META_MARKER && !ALLOW_SYNTHETIC_MARKER) {
      reasons.push("meta_marker_missing_strict");
    }
  }

  const decision = {
    ts: Date.now(),
    iso: new Date().toISOString(),
    pass: reasons.length === 0,
    reasons,
    champion_id: CHAMPION_ID,
    stats: st,
    ssot_source: ssot.source,
    producer: "ram26_target_ssot_projector_v2",
    meta_marker_source: metaResolved ? metaResolved.source : null,
  };

  if (!decision.pass) {
    await redis.set("ram26:target_ssot_projector:last", JSON.stringify(decision), "EX", 120);
    await redis.xadd("ram26:target_ssot_projector:events", "MAXLEN", "~", 1000, "*",
      "ts", String(decision.ts), "status", "NO_PUBLISH", "reasons", reasons.join(","), "payload", JSON.stringify(decision));
    console.log(JSON.stringify({ status: "NO_PUBLISH", reasons, stats: st, n: Object.keys(symMap).length }));
    return decision;
  }

  // Build payload mapping keyed by ticker
  const mapping = {};
  for (const [sym, v] of Object.entries(symMap)) {
    const w = Number(v?.weight ?? v?.w ?? v?.w_norm ?? v?.w_raw ?? 0);
    if (!Number.isFinite(w) || w <= 0) continue;
    mapping[sym] = JSON.stringify({
      symbol: sym,
      weight: w,
      target_value: Number(v?.target_value ?? 0),
      side: v?.side || "BUY",
      source: "ram26_target_ssot_projector_v2",
      upstream_source: "ssot:target:v2:current",
      champion_id: CHAMPION_ID,
      ts: decision.ts,
    });
  }

  const markerJson = metaResolved ? JSON.stringify(metaResolved.marker) : null;
  const decisionWithHlen = { ...decision, hlen: Object.keys(mapping).length };

  const pipe = redis.pipeline();

  // --- champion:targets:ssot (canonical, FIX target) ---
  pipe.del("champion:targets:ssot");
  for (const [sym, payload] of Object.entries(mapping)) {
    pipe.hset("champion:targets:ssot", sym, payload);
  }
  if (markerJson) pipe.hset("champion:targets:ssot", "_meta_marker", markerJson);
  pipe.expire("champion:targets:ssot", TARGET_TTL_SEC);
  pipe.set("champion:targets:ssot:meta", JSON.stringify(decisionWithHlen), "EX", TARGET_TTL_SEC);

  // --- legacy champion:target (FIX 4) ---
  if (WRITE_LEGACY_CHAMPION_TARGET) {
    pipe.del("champion:target");
    for (const [sym, payload] of Object.entries(mapping)) {
      pipe.hset("champion:target", sym, payload);
    }
    if (markerJson) pipe.hset("champion:target", "_meta_marker", markerJson);
    pipe.expire("champion:target", TARGET_TTL_SEC);
  }

  // --- status & events ---
  pipe.set("ram26:target_ssot_projector:last", JSON.stringify(decisionWithHlen), "EX", 300);
  pipe.xadd("ram26:target_ssot_projector:events", "MAXLEN", "~", 1000, "*",
    "ts", String(decision.ts), "status", "PUBLISHED",
    "n", String(Object.keys(mapping).length),
    "marker_source", String(decision.meta_marker_source || "none"),
    "payload", JSON.stringify(decisionWithHlen));

  await pipe.exec();
  console.log(JSON.stringify({
    status: "PUBLISHED",
    n: Object.keys(mapping).length,
    marker_source: decision.meta_marker_source,
    stats: st,
  }));
  return decisionWithHlen;
}

async function main() {
  const once = process.argv.includes("--once");
  console.log(`[ram26-target-projector v2] start once=${once} require_meta_marker=${REQUIRE_META_MARKER} allow_synthetic=${ALLOW_SYNTHETIC_MARKER} write_legacy=${WRITE_LEGACY_CHAMPION_TARGET}`);
  while (true) {
    try { await runOnce(); }
    catch (e) { console.error(`[ram26-target-projector v2] ERROR ${e.stack || e.message}`); }
    if (once) break;
    await new Promise(r => setTimeout(r, CYCLE_MS));
  }
  await redis.quit();
}

main().catch(e => {
  console.error(e.stack || e.message);
  process.exit(1);
});
