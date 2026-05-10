#!/usr/bin/env node
/**
 * RAM26 Target SSOT Projector v3 — SSOT_TARGET_V2 envelope + marker propagation fix
 *
 * Fixes current production drift:
 * - ssot:target:v2:current.targets may be an envelope:
 *     { gross, net, meta, positions:[{symbol,w,target_value,...}] }
 *   Previous projector treated {gross,net} as two target symbols -> n=2/gross=1.5.
 * - projector refused to publish during LIVE via mode_live_refuse_projection.
 *   In Full Live this is wrong: projector must publish if source+G8 are valid.
 * - _meta_marker/phase9_b1_marker must be propagated to both champion hashes.
 *
 * Does NOT:
 * - set ssot:target:v2:current
 * - set policy:champion:active
 * - change mode/trading
 * - write fill streams
 */

import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || process.env.ARES_REDIS_URL;
if (!REDIS_URL) throw new Error("REDIS_URL/ARES_REDIS_URL required");

const CHAMPION_ID = process.env.CHAMPION_ID || "RAM26_ALPHA_0001_b215c119ea23";
const SOURCE_KEY = process.env.RAM26_PROJECTOR_SOURCE_KEY || "ssot:target:v2:current";
const POLICY_KEY = process.env.RAM26_PROJECTOR_POLICY_KEY || "policy:champion:active";
const DEST_HASH = process.env.RAM26_PROJECTOR_DEST_HASH || "champion:targets:ssot";
const LEGACY_HASH = process.env.RAM26_PROJECTOR_LEGACY_DEST_HASH || "champion:target";
const WRITE_LEGACY = String(process.env.RAM26_PROJECTOR_WRITE_CHAMPION_TARGET || "true").toLowerCase() === "true";
const CYCLE_MS = Number(process.env.RAM26_TARGET_PROJECTOR_CYCLE_MS || "2000");
const TTL_SEC = Number(process.env.RAM26_TARGET_SSOT_TTL_SEC || "900");
const REQUIRE_RAM26 = String(process.env.RAM26_PROJECTOR_REQUIRE_RAM26_SOURCE || "true").toLowerCase() === "true";
const REQUIRE_MARKER = String(process.env.RAM26_PROJECTOR_REQUIRE_META_MARKER || "true").toLowerCase() === "true";
const EVENT_STREAM = process.env.RAM26_PROJECTOR_EVENT_STREAM || "ram26:target_ssot_projector:events";

const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

function parse(raw, fallback = {}) {
  if (!raw) return fallback;
  if (typeof raw === "object") return raw;
  try { return JSON.parse(String(raw)); } catch { return fallback; }
}
function n(x, d = 0) {
  const v = Number(x);
  return Number.isFinite(v) ? v : d;
}
function symbolOf(x) {
  const s = String(x || "").trim().toUpperCase();
  return /^[A-Z][A-Z0-9.\-]{0,9}$/.test(s) ? s : "";
}
function weightOf(v) {
  if (typeof v === "number") return v;
  if (!v || typeof v !== "object") return 0;
  return n(v.weight ?? v.w ?? v.w_norm ?? v.target_weight ?? v.targetWeight ?? v.allocation, 0);
}
function record(sym, v) {
  const s = symbolOf(sym);
  if (!s) return null;
  const obj = v && typeof v === "object" ? v : {};
  const w = weightOf(v);
  if (!Number.isFinite(w) || w <= 0) return null;
  return {
    symbol: s,
    weight: Number(w.toFixed(10)),
    w: Number(w.toFixed(10)),
    target_value: n(obj.target_value ?? obj.targetValue ?? obj.notional ?? 0, 0),
    side: obj.side || "BUY",
    score: n(obj.score ?? obj.alpha ?? obj.edge ?? obj.risk_score ?? 0, 0),
    p_drop: obj.p_drop ?? obj.pDrop ?? null,
    risk_multiplier: obj.risk_multiplier ?? obj.xgb_risk_multiplier ?? obj.multiplier ?? null,
    source: "ram26_target_ssot_projector_v3",
    upstream_source: SOURCE_KEY,
    champion_id: CHAMPION_ID,
    ts: Date.now(),
  };
}
function add(out, sym, v) {
  const r = record(sym, v);
  if (r) out[r.symbol] = r;
}

function extractPositionsArray(ssot) {
  if (Array.isArray(ssot?.positions)) return ssot.positions;
  if (Array.isArray(ssot?.targets?.positions)) return ssot.targets.positions;
  if (Array.isArray(ssot?.targets?.meta?.positions)) return ssot.targets.meta.positions;
  return null;
}

function extractTargetMap(ssot) {
  const out = {};

  // 1) SSOT_TARGET_V2 envelope: targets.positions is the canonical executable list.
  const pos = extractPositionsArray(ssot);
  if (Array.isArray(pos)) {
    for (const p of pos) {
      if (!p || typeof p !== "object") continue;
      const w = p.weight ?? p.w ?? p.w_norm ?? p.target_weight;
      add(out, p.symbol ?? p.ticker ?? p.pdno ?? p.code, { ...p, weight: w });
    }
  }

  // 2) RAM26 engine native target array.
  if (Object.keys(out).length === 0 && Array.isArray(ssot?.targets)) {
    for (const e of ssot.targets) {
      if (!e || typeof e !== "object") continue;
      add(out, e.symbol ?? e.ticker ?? e.pdno ?? e.code, e);
    }
  }

  // 3) Symbol keyed target object. Ignore SSOT envelope keys.
  if (Object.keys(out).length === 0 && ssot?.targets && typeof ssot.targets === "object" && !Array.isArray(ssot.targets)) {
    for (const [k, v] of Object.entries(ssot.targets)) {
      if (["gross", "net", "meta", "positions"].includes(k)) continue;
      if (v && typeof v === "object") add(out, v.symbol ?? v.ticker ?? v.pdno ?? v.code ?? k, v);
      else add(out, k, { symbol: k, weight: v });
    }
  }

  // 4) weights dict fallback.
  if (Object.keys(out).length === 0 && ssot?.weights && typeof ssot.weights === "object") {
    for (const [k, v] of Object.entries(ssot.weights)) {
      if (v && typeof v === "object") add(out, v.symbol ?? k, v);
      else add(out, k, { symbol: k, weight: v });
    }
  }

  return out;
}

function extractMarker(ssot, policy) {
  const candidates = [
    ssot?._meta_marker,
    ssot?.phase9_b1_marker,
    ssot?.targets?.meta?._meta_marker,
    ssot?.targets?.meta?.phase9_b1_marker,
    policy?._meta_marker,
    policy?.phase9_b1_marker,
  ];
  for (let c of candidates) {
    if (!c) continue;
    if (typeof c === "string") c = { phase9_b1_marker: c };
    if (typeof c === "object") {
      const upstream = c.upstream_marker && typeof c.upstream_marker === "object" ? c.upstream_marker : {};
      return {
        ...upstream,
        ...c,
        phase9_b1_marker: c.phase9_b1_marker || upstream.phase9_b1_marker || "phase9_b1_marker_safe_v2",
        issued_by: c.issued_by || upstream.issued_by || "ram26_target_ssot_projector_v3",
        ts_ms: n(c.ts_ms ?? c.generated_at_ms ?? c.issued_at_ms ?? upstream.ts_ms ?? Date.now(), Date.now()),
        champion_version: c.champion_version || upstream.champion_version || CHAMPION_ID,
        champion_strategy_name: c.champion_strategy_name || upstream.champion_strategy_name || "RAM26_ALPHA_0001",
        champion_engine_file: c.champion_engine_file || upstream.champion_engine_file || "ram26_alpha_engine_v1.py",
      };
    }
  }
  return null;
}

function ram26SourceOk(ssot, policy) {
  if (!REQUIRE_RAM26) return true;
  const vals = [
    ssot?.engine_version,
    ssot?.strategy_id,
    ssot?.champion_id,
    ssot?.version,
    ssot?.phase9_b1_marker?.champion_version,
    ssot?.targets?.meta?.champion_version,
    ssot?.targets?.meta?.champion_strategy_name,
    ssot?.targets?.meta?.phase9_b1_marker?.champion_version,
    policy?.champion_version,
    policy?.champion_strategy_name,
    policy?.candidate_id,
    policy?.version,
  ].filter(Boolean).map(String);
  return vals.some(v => v.includes("RAM26"));
}

function stats(map) {
  const vals = Object.values(map).map(x => n(x.weight, 0)).filter(x => x > 0);
  const count = vals.length;
  const gross = vals.reduce((a,b)=>a+b,0);
  const mean = count ? gross / count : 0;
  const std = count ? Math.sqrt(vals.reduce((a,b)=>a+(b-mean)*(b-mean),0)/count) : 0;
  const min = count ? Math.min(...vals) : 0;
  const max = count ? Math.max(...vals) : 0;
  const ratio = min > 0 ? max/min : null;
  const cv = mean > 0 ? std/mean : 0;
  const unique = new Set(vals.map(x => x.toFixed(6))).size;
  const nonFlat = count >= 8 && (std >= 0.0025 || (ratio !== null && ratio >= 1.5) || cv >= 0.25 || unique >= 8);
  return { n: count, gross, std, min, max, ratio, cv, unique, nonFlat };
}

async function projectOnce() {
  const [active, mode, kill, ssotRaw, policyRaw] = await redis.mget(
    "emarkos:v1:ram26:active_strategy_id",
    "emarkos:v1:mode",
    "kill-switch:active",
    SOURCE_KEY,
    POLICY_KEY
  );
  const ssot = parse(ssotRaw, {});
  const policy = parse(policyRaw, {});
  const targets = extractTargetMap(ssot);
  const marker = extractMarker(ssot, policy);
  const st = stats(targets);
  const reasons = [];

  if (active && active !== CHAMPION_ID) reasons.push(`active_mismatch:${active}`);
  if (kill === "true") reasons.push("kill_switch_active");
  if (!ram26SourceOk(ssot, policy)) reasons.push("source_not_ram26");
  if (st.n < 8) reasons.push(`n_too_small:${st.n}`);
  if (st.gross < 0.25 || st.gross > 1.05) reasons.push(`gross_invalid:${st.gross}`);
  if (!st.nonFlat) reasons.push("target_flat_or_too_small");
  if (REQUIRE_MARKER && !marker) reasons.push("phase9_meta_marker_missing");

  const decision = {
    ts: Date.now(),
    iso: new Date().toISOString(),
    pass: reasons.length === 0,
    reasons,
    mode,
    champion_id: CHAMPION_ID,
    stats: st,
    target_symbols_sample: Object.keys(targets).slice(0, 20),
    has_meta_marker: Boolean(marker),
    meta_marker_source: marker?.issued_by || null,
    producer: "ram26_target_ssot_projector_v3",
  };

  const pipe = redis.pipeline();

  if (!decision.pass) {
    pipe.set("ram26:target_ssot_projector:last", JSON.stringify(decision), "EX", 300);
    pipe.xadd(EVENT_STREAM, "MAXLEN", "~", 1000, "*", "status", "NO_PUBLISH", "payload", JSON.stringify(decision));
    await pipe.exec();
    console.log(JSON.stringify({ status: "NO_PUBLISH", ...decision }));
    return decision;
  }

  pipe.del(DEST_HASH);
  if (WRITE_LEGACY) pipe.del(LEGACY_HASH);
  for (const [sym, rec] of Object.entries(targets)) {
    const payload = JSON.stringify(rec);
    pipe.hset(DEST_HASH, sym, payload);
    if (WRITE_LEGACY) pipe.hset(LEGACY_HASH, sym, payload);
  }

  const markerPayload = JSON.stringify(marker);
  pipe.hset(DEST_HASH, "_meta_marker", markerPayload);
  pipe.hset(DEST_HASH, "phase9_b1_marker", markerPayload);
  if (WRITE_LEGACY) {
    pipe.hset(LEGACY_HASH, "_meta_marker", markerPayload);
    pipe.hset(LEGACY_HASH, "phase9_b1_marker", markerPayload);
  }

  pipe.expire(DEST_HASH, TTL_SEC);
  if (WRITE_LEGACY) pipe.expire(LEGACY_HASH, TTL_SEC);

  const meta = { ...decision, marker, hlen: st.n + 2 };
  pipe.set(`${DEST_HASH}:meta`, JSON.stringify(meta), "EX", TTL_SEC);
  pipe.set(`${DEST_HASH}:ts`, String(decision.ts), "EX", TTL_SEC);
  if (WRITE_LEGACY) {
    pipe.set(`${LEGACY_HASH}:meta`, JSON.stringify(meta), "EX", TTL_SEC);
    pipe.set(`${LEGACY_HASH}:ts`, String(decision.ts), "EX", TTL_SEC);
  }
  pipe.set("ram26:target_ssot_projector:last", JSON.stringify(meta), "EX", 300);
  pipe.xadd(EVENT_STREAM, "MAXLEN", "~", 1000, "*", "status", "PUBLISHED", "n", String(st.n), "gross", String(st.gross), "payload", JSON.stringify(meta));
  await pipe.exec();

  console.log(JSON.stringify({ status: "PUBLISHED", n: st.n, gross: st.gross, hlen: st.n + 2, marker: true, sample: Object.keys(targets).slice(0, 8) }));
  return meta;
}

async function selfTest() {
  const ssot = {
    engine_version: "v7.0_LOCKED_20260130",
    targets: {
      gross: 0.75,
      net: 0.75,
      meta: { phase9_b1_marker: { champion_version: CHAMPION_ID, phase9_b1_marker: "phase9_b1_marker_safe_v2" } },
      positions: [{ symbol: "AAPL", w: 0.4 }, { symbol: "MSFT", w: 0.35 }, { symbol: "NVDA", w: 0.01 }, { symbol: "AMD", w: 0.01 }, { symbol: "MU", w: 0.01 }, { symbol: "INTC", w: 0.01 }, { symbol: "QCOM", w: 0.01 }, { symbol: "AMZN", w: 0.01 }]
    }
  };
  const m = extractTargetMap(ssot);
  const marker = extractMarker(ssot, {});
  const ok = m.AAPL && m.MSFT && marker?.champion_version === CHAMPION_ID;
  console.log(JSON.stringify({ SELFTEST: ok ? "PASS" : "FAIL", symbols: Object.keys(m), marker }));
  process.exit(ok ? 0 : 2);
}

async function main() {
  if (process.argv.includes("--self-test")) await selfTest();
  const once = process.argv.includes("--once");
  console.log(`[ram26-projector-v3] start once=${once} source=${SOURCE_KEY} dest=${DEST_HASH}`);
  while (true) {
    try { await projectOnce(); }
    catch (e) { console.error(`[ram26-projector-v3] ERROR ${e.stack || e.message}`); }
    if (once) break;
    await new Promise(r => setTimeout(r, CYCLE_MS));
  }
  await redis.quit();
}

main().catch(e => { console.error(e.stack || e.message); process.exit(1); });
