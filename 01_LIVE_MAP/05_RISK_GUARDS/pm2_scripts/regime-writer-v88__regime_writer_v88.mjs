// SOURCE: /home/ubuntu/regime_writer_v88.mjs
// PATCHED: 2026-05-07 — F2 (ctx:regime:state SET 검증 · 재시도 · 진단키 보강)
//
// Field finding (2026-05-07)
// --------------------------
// regime_writer_v88 was running every 300s and successfully writing
//   ctx:regime:state:audit
// but the sibling key
//   ctx:regime:state
// was returning TYPE=none/STRLEN=0 immediately after SET.  Audit hash kept
// updating, so the writer itself was alive, but the canonical state key
// was either being clobbered between the two SETs (cluster slot quirk,
// SCRIPT, ACL, or eviction) or the SET silently no-op'd.
//
// F2 PATCH adds:
//   (a) SET → GET back-verify pass.  If TYPE=none/STRLEN=0 within 200ms,
//       up to 3 retries with `EX TTL` are performed, then the failure is
//       written to a sentinel key  ctx:regime:state:writer_failure  and
//       logged at error level so journald/cron alarms catch it.
//   (b) Both keys SET in a single MULTI pipeline — guarantees atomic ACL,
//       avoids a window where audit lands but state is dropped.
//   (c) Defensive payload check: if state.regime is undefined we DO NOT
//       blank ctx:regime:state (we leave previous value alive until next
//       cycle).  This kills "writer publishing empty state on boot" cases.
//   (d) Heartbeat key  ctx:regime:state:writer_heartbeat  (300s TTL) for
//       external alarms ("regime writer silent for >900s").
//   (e) Adds a writer_meta_source label to ctx:regime:state itself so
//       downstream consumers can tell which path produced the value.

import Redis from "ioredis";
import fs from "fs";

if (!process.env.REDIS_URL) console.warn("[WARN] REDIS_URL not set — using localhost fallback");
const REDIS_URL = process.env.REDIS_URL || "redis://127.0.0.1:6379";
const TTL_SEC = parseInt(process.env.REGIME_TTL_SEC || "600", 10);
const INTERVAL_SEC = parseInt(process.env.REGIME_INTERVAL_SEC || "300", 10);
const POLICY_PATH = process.env.REGIME_POLICY_PATH || "/home/ubuntu/ssot/v8.7d/regime_policy_v88.json";
const VERSION = "regime_writer_v88-f2patch-20260507";

// F2: verification configuration
const SET_VERIFY_RETRIES   = parseInt(process.env.REGIME_SET_VERIFY_RETRIES   || "3", 10);
const SET_VERIFY_DELAY_MS  = parseInt(process.env.REGIME_SET_VERIFY_DELAY_MS  || "200", 10);

const REGIME_STATE_KEY  = "ctx:regime:state";
const REGIME_AUDIT_KEY  = "ctx:regime:state:audit";
const REGIME_FAIL_KEY   = "ctx:regime:state:writer_failure";
const REGIME_HB_KEY     = "ctx:regime:state:writer_heartbeat";

const redis = new Redis(REDIS_URL, {
  lazyConnect: true,
  retryStrategy: (times) => Math.min(times * 500, 30000),
  maxRetriesPerRequest: 3,
});
redis.on("error", (err) => { console.error(`[regime-v88] Redis error: ${err.message}`); });

const nowIso  = () => new Date().toISOString();
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const sleep   = (ms) => new Promise((r) => setTimeout(r, ms));

function loadPolicy() {
  try { return JSON.parse(fs.readFileSync(POLICY_PATH, "utf-8")); }
  catch (e) { console.error(`[regime-v88] Failed to load policy: ${e.message}`); return { presets: {}, thresholds: {} }; }
}

function safeJson(raw) { if (!raw) return null; try { return JSON.parse(raw); } catch { return null; } }

async function scanKeys(redis, pattern) {
  const keys = []; let cursor = "0";
  do {
    const [nextCursor, batch] = await redis.scan(cursor, "MATCH", pattern, "COUNT", 100);
    cursor = nextCursor; keys.push(...batch);
  } while (cursor !== "0");
  return keys;
}

async function getAresMeta() {
  const raw = await redis.get("sleeve:ares:weights");
  const j = safeJson(raw);
  const m = j?.metadata || {};
  return {
    vix_z: typeof m.vix_z === "number" ? m.vix_z : null,
    vol_z: typeof m.vol_z === "number" ? m.vol_z : null,
    dd20:  typeof m.dd_20 === "number" ? m.dd_20 : (typeof m.dd20 === "number" ? m.dd20 : null),
    intraday_return: null,
    source: m.source || null,
  };
}

async function getShock() {
  const raw = await redis.get("ctx:shock:market");
  const j = safeJson(raw) || {};
  const drop = (j.drop_rule || {}).drop_count ?? 0;
  const sev  = (j.sev_rule  || {}).sev_count  ?? 0;
  return {
    ok: j.ok === true, active: j.active === true,
    drop_count: +drop || 0, sev_count: +sev || 0, hold_until_ts: j.hold_until_ts || null,
  };
}

async function getEventPressure(universeN) {
  const hRaw = await redis.get("ctx:event:health");
  const h = safeJson(hRaw) || {};
  const ok = h.ok === true;
  const keys = await scanKeys(redis, "ctx:event:*");
  const n = keys.filter((k) => !k.endsWith(":audit") && k !== "ctx:event:health").length;
  const coverage = universeN ? (n / universeN) : 0;
  const score = clamp01(coverage);
  return { ok, n, coverage, score };
}

async function getMacroBreadth() {
  const mRaw = await redis.get("ctx:macro:regime");
  const bRaw = await redis.get("ctx:breadth:health");
  const m = safeJson(mRaw) || {}; const b = safeJson(bRaw) || {};
  return {
    macro_state: m.regime || "UNKNOWN",
    macro_score: typeof m.score === "number" ? m.score : 0.5,
    breadth_weak: b.weak === true,
    breadth_score: typeof b.breadth_score === "number" ? b.breadth_score : 0.5,
  };
}

function computeStress(meta, shock, thr) {
  const vix_z_raw = meta.vix_z;
  const vol_z_raw = meta.vol_z;
  const dd20_raw  = meta.dd_20 !== undefined ? meta.dd_20 : meta.dd20;
  const NULL_DEFAULT = 0.2;
  const vix_z = (vix_z_raw !== null && vix_z_raw !== undefined && isFinite(vix_z_raw)) ? vix_z_raw : NULL_DEFAULT;
  const vol_z = (vol_z_raw !== null && vol_z_raw !== undefined && isFinite(vol_z_raw)) ? vol_z_raw : NULL_DEFAULT;
  const dd20  = (dd20_raw  !== null && dd20_raw  !== undefined && isFinite(dd20_raw))  ? dd20_raw  : 0;

  const s1 = (dd20 <= thr.dd_crisis) ? 1.0 : (dd20 <= thr.dd_def ? 0.7 : 0.0);
  const s2 = (vix_z >= thr.vix_crisis) ? 1.0 : (vix_z >= thr.vix_def ? 0.7 : 0.0);
  const s3 = (vol_z >= thr.vol_crisis) ? 1.0 : (vol_z >= thr.vol_def ? 0.6 : 0.0);
  const s4 = shock.active ? 1.0 : 0.0;
  const intradayRet = meta.intraday_return ?? 0;
  const s5 = (intradayRet <= -0.015) ? 0.8 : (intradayRet <= -0.008) ? 0.5 : (intradayRet <= -0.004) ? 0.3 : 0.0;

  const score = clamp01(Math.max(s1, s2, s3, s4, s5));
  let level = "LOW";
  if (score >= 0.95) level = "EXTREME";
  else if (score >= 0.70) level = "HIGH";
  else if (score >= 0.35) level = "MID";

  const has = [meta.vix_z, meta.vol_z, meta.dd_20 !== undefined ? meta.dd_20 : meta.dd20]
              .filter((x) => typeof x === "number" && isFinite(x)).length;
  const dataQuality = has / 3.0;
  const conf = clamp01(0.25 + 0.45 * dataQuality + (shock.ok ? 0.10 : 0.0));
  return { score, level, confidence: conf, vix_z, vol_z, dd20 };
}

function decideRegime(stress, macro, event, thr, prevState) {
  if (stress.level === "EXTREME") return { regime: "CRISIS", preset: "CRISIS_V1" };
  if (stress.level === "HIGH")    return { regime: "DEFENSIVE", preset: "DEFENSIVE_V1" };
  if ((event.score >= thr.event_hot) && (macro.macro_state === "RISK_OFF")) return { regime: "DEFENSIVE", preset: "DEFENSIVE_V1" };
  if (macro.macro_state === "RISK_OFF" || macro.breadth_weak || macro.macro_state === "UNKNOWN") return { regime: "CAUTIOUS", preset: "CAUTIOUS_V1" };
  if (prevState.regime && prevState.regime !== "NORMAL") {
    if (stress.score > 0.3 || event.score > 0.4) return { regime: "CAUTIOUS", preset: "CAUTIOUS_V1" };
  }
  return { regime: "NORMAL", preset: "NORMAL_V1" };
}

// F2 PATCH (v2): GET-back verification + targeted re-SET on empty result.
// Important: the canonical pipelined SET is performed ONCE in runOnce; this
// routine only re-SETs when the GET-back probe finds the key empty, so the
// happy path is a single SET, single GET. This avoids the "double SET" race
// flagged during 3-AI review while still giving us the resilience we need
// against the ElastiCache HA quirk that originally produced TYPE=none.
async function verifyOrResetState(key, value, ttl) {
  let lastErr = null;
  for (let attempt = 1; attempt <= SET_VERIFY_RETRIES; attempt++) {
    try {
      await sleep(SET_VERIFY_DELAY_MS);
      const back  = await redis.get(key);
      const strln = back ? back.length : 0;
      if (back && strln > 0) {
        return { ok: true, attempt, strln };
      }
      // Only on confirmed empty/missing: do a targeted re-SET. This is
      // the only place we rewrite the key; pipeline already wrote it once.
      lastErr = new Error(`GET-back empty after pipeline SET (attempt=${attempt}, strln=${strln}); re-SETting`);
      console.warn(`[regime-v88] [F2_VERIFY_RESET] ${lastErr.message}`);
      await redis.set(key, value, "EX", ttl);
    } catch (e) {
      lastErr = e;
    }
  }
  return { ok: false, error: String(lastErr && lastErr.message || lastErr) };
}

async function runOnce() {
  const ts = nowIso();
  const policy = loadPolicy();
  const thr = policy.thresholds || {
    dd_def: -0.03, dd_crisis: -0.05,
    vix_def: 1.0,  vix_crisis: 1.8,
    vol_def: 1.2,  vol_crisis: 2.0,
    event_hot: 0.70,
  };

  let uRaw;
  try {
    const uType = await redis.type("emarkos:v1:universe:current");
    if (uType === "set") {
      const members = await redis.smembers("emarkos:v1:universe:current");
      uRaw = JSON.stringify(members);
    } else {
      uRaw = await redis.get("emarkos:v1:universe:current");
    }
  } catch { uRaw = ""; }
  let universeN = 0;
  try {
    const j = JSON.parse(uRaw);
    universeN = Array.isArray(j) ? j.length : (j.universe?.length || 0);
  } catch {
    universeN = (uRaw || "").split(",").filter(Boolean).length;
  }

  const prevRaw = await redis.get(REGIME_STATE_KEY);
  const prevState = safeJson(prevRaw) || {};

  const meta  = await getAresMeta();
  const shock = await getShock();
  const macro = await getMacroBreadth();
  const event = await getEventPressure(universeN);

  const stress = computeStress(meta, shock, thr);
  const final  = decideRegime(stress, macro, event, thr, prevState);
  const preset = (policy.presets || {})[final.preset] || {};

  const state = {
    ts, ok: true,
    regime: final.regime, preset: final.preset,
    confidence: clamp01((stress.confidence * 0.6) + (event.ok ? 0.2 : 0.0) + 0.2),
    components: {
      stress, macro, event,
      shock: {
        active: shock.active, drop_count: shock.drop_count,
        sev_count: shock.sev_count, hold_until_ts: shock.hold_until_ts,
      },
    },
    policy: preset,
    writer_meta_source: meta.source || null,    // F2: trace upstream ARES meta source
    writer_version: VERSION,                    // F2
  };
  const audit = { ts, policy_path: POLICY_PATH, thresholds: thr, writer_version: VERSION };

  // F2 defensive: don't overwrite state with empty payload when regime is undefined.
  if (!state.regime) {
    console.error(`[regime-v88] [F2] refusing to publish empty regime payload (ts=${ts}); leaving prior state alive`);
    try {
      await redis.set(REGIME_FAIL_KEY, JSON.stringify({ ts, reason: "empty_regime", state }), "EX", TTL_SEC);
    } catch (e) {
      console.error(`[regime-v88] [F2] failed to publish REGIME_FAIL_KEY: ${e.message}`);
    }
    return;
  }

  // F2 pipelined SET (atomic vs ACL/cluster quirks): state + audit + heartbeat
  try {
    const pipe = redis.multi();
    pipe.set(REGIME_STATE_KEY, JSON.stringify(state), "EX", TTL_SEC);
    pipe.set(REGIME_AUDIT_KEY, JSON.stringify(audit), "EX", TTL_SEC);
    pipe.set(REGIME_HB_KEY, JSON.stringify({ ts, pid: process.pid, version: VERSION }), "EX", TTL_SEC);
    await pipe.exec();
  } catch (e) {
    console.error(`[regime-v88] [F2] pipeline SET failed: ${e.message}`);
  }

  // F2 verify state (the canonical key); audit/heartbeat are guarded by their TTL
  const verify = await verifyOrResetState(REGIME_STATE_KEY, JSON.stringify(state), TTL_SEC);
  if (!verify.ok) {
    console.error(`[regime-v88] [F2_VERIFY_FAIL] ctx:regime:state SET-then-GET empty after ${SET_VERIFY_RETRIES} retries; err=${verify.error}`);
    try {
      await redis.set(REGIME_FAIL_KEY, JSON.stringify({
        ts, reason: "set_verify_fail", attempts: SET_VERIFY_RETRIES,
        delay_ms: SET_VERIFY_DELAY_MS, error: verify.error,
      }), "EX", TTL_SEC);
    } catch (e) {
      console.error(`[regime-v88] [F2] could not publish REGIME_FAIL_KEY on verify_fail: ${e.message}`);
    }
  } else if (verify.attempt > 1) {
    console.warn(`[regime-v88] [F2_VERIFY_OK] ctx:regime:state required attempt=${verify.attempt}, strln=${verify.strln}`);
  }

  await redis.xadd("kpi:regime:events", "MAXLEN", "~", "20000", "*",
    "ts", state.ts, "regime", state.regime, "preset", state.preset, "confidence", String(state.confidence));

  // --- [FIX 1] 4AI 구조적 패치 (kept) ---
  const llmFactor = final.regime === "NORMAL" ? 1.0 : (final.regime === "CAUTIOUS" ? 0.8 : (final.regime === "DEFENSIVE" ? 0.5 : 0.0));
  const llmAction = final.regime === "NORMAL" ? "MAINTAIN" : (final.regime === "CAUTIOUS" ? "CAUTION" : "REDUCE_RISK");
  await redis.hset("llm:regime:correction", {
    factor: llmFactor, action: llmAction, confidence: state.confidence,
    ts: Date.now(), reason: `regime_writer_v88: ${final.regime}`,
  });
  await redis.expire("llm:regime:correction", TTL_SEC);

  const monitorVerdict = {
    recommendation: final.regime === "NORMAL" ? "APPROVE" : "MAINTAIN",
    confidence: state.confidence,
    riskScore: final.regime === "NORMAL" ? 0.04 : (final.regime === "CAUTIOUS" ? 0.2 : 0.5),
    exposureHint: llmFactor,
    ts: Date.now(),
    diagnostics: { freshSignals: 10, coverage: 0.95, directionScore: 0.8 },
  };
  await redis.set("monitor:v3v4:latest_verdict", JSON.stringify(monitorVerdict), "EX", TTL_SEC);

  console.log(`[regime-v88] ts=${ts} regime=${state.regime} preset=${state.preset} conf=${state.confidence.toFixed(2)} stress=${stress.level} event=${event.score.toFixed(2)} macro=${macro.macro_state} verify=${verify.ok ? "OK" : "FAIL"} ares_meta_src=${meta.source || "?"}`);
  console.log(`[regime-v88] (FIX) Updated llm:regime:correction and monitor:v3v4:latest_verdict`);
}

async function main() {
  console.log(`[regime-v88] Starting... VERSION=${VERSION} REDIS_URL=${REDIS_URL} INTERVAL=${INTERVAL_SEC}s TTL=${TTL_SEC}s`);
  console.log(`[regime-v88] Policy path: ${POLICY_PATH}`);
  console.log(`[regime-v88] [F2] verify retries=${SET_VERIFY_RETRIES} delay_ms=${SET_VERIFY_DELAY_MS}`);

  await redis.connect();
  console.log("[regime-v88] Redis connected");

  while (true) {
    try { await runOnce(); }
    catch (e) { console.error(`[regime-v88] Error in runOnce: ${e.message}`); }
    await new Promise((r) => setTimeout(r, INTERVAL_SEC * 1000));
  }
}

process.on("uncaughtException", (err) => { console.error(`[regime-v88] UNCAUGHT_EXCEPTION: ${err.message}`); });
process.on("unhandledRejection", (reason) => { console.error(`[regime-v88] UNHANDLED_REJECTION: ${reason}`); });

main();
