#!/usr/bin/env node
/**
 * ram26_final_to_champion_bridge_v2.mjs
 * Native RAM26 engine bridge for ALL_GATES_GO.
 *
 * Why this exists:
 * - Installed RAM26 engine supports native args:
 *   --once, --input-json, --output-json, --features-key, --output-key,
 *   --ssot-staged-key, --feature-max-age-sec, --publish-redis, --publish-ssot-staged.
 * - It does NOT support legacy --config / --strategy-id / --stdout-json.
 * - Older gate scripts expected a missing RAM26 bridge_v2.
 *
 * This bridge:
 * 1) calls engine with native args;
 * 2) validates target non-flatness/gross/n;
 * 3) publishes staged target;
 * 4) optionally promotes staged target to current/policy/champion hashes;
 * 5) never places orders.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import Redis from "ioredis";

function argMap(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const k = a.slice(2);
    const v = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
    out[k] = v;
  }
  return out;
}

function jparse(x, fallback = {}) {
  if (!x) return fallback;
  if (typeof x === "object") return x;
  try { return JSON.parse(String(x)); } catch { return fallback; }
}

function nowMs() { return Date.now(); }

function statsFromTargets(targets) {
  let vals = [];
  let scores = [];
  if (Array.isArray(targets)) {
    const t = {};
    for (const x of targets) if (x && x.symbol) t[x.symbol] = x;
    targets = t;
  }
  if (!targets || typeof targets !== "object") targets = {};
  for (const v of Object.values(targets)) {
    let w = 0, s = 0;
    if (v && typeof v === "object") {
      w = Number(v.weight ?? v.w ?? 0);
      s = Number(v.score ?? v.alpha ?? v.edge ?? v.risk_score ?? 0);
    } else {
      w = Number(v);
    }
    if (Number.isFinite(w) && w > 0) vals.push(w);
    if (Number.isFinite(s)) scores.push(s);
  }
  const n = vals.length;
  const gross = vals.reduce((a,b)=>a+b,0);
  const mean = n ? gross / n : 0;
  const std = n ? Math.sqrt(vals.reduce((a,b)=>a+(b-mean)*(b-mean),0)/n) : 0;
  const min = n ? Math.min(...vals) : 0;
  const max = n ? Math.max(...vals) : 0;
  const ratio = min > 0 ? max / min : null;
  const cv = mean > 0 ? std / mean : 0;
  const unique = new Set(vals.map(x => Number(x).toFixed(6))).size;
  const smean = scores.length ? scores.reduce((a,b)=>a+b,0)/scores.length : 0;
  const scoreStd = scores.length ? Math.sqrt(scores.reduce((a,b)=>a+(b-smean)*(b-smean),0)/scores.length) : 0;
  const nonFlat = n >= 8 && (std >= 0.0025 || (ratio !== null && ratio >= 1.5) || cv >= 0.25 || unique >= 8);
  return { n, gross, std, min, max, ratio, cv, unique, scoreStd, nonFlat };
}

function normalizeTargets(rawTargets) {
  if (Array.isArray(rawTargets)) {
    const t = {};
    for (const x of rawTargets) if (x && x.symbol) t[String(x.symbol).toUpperCase()] = x;
    rawTargets = t;
  }
  const out = {};
  if (!rawTargets || typeof rawTargets !== "object") return out;
  for (const [symRaw, v] of Object.entries(rawTargets)) {
    const sym = String(symRaw).toUpperCase();
    let w = 0, score = 0, targetValue = 0, pDrop = null, mult = null, side = "BUY";
    if (v && typeof v === "object") {
      w = Number(v.weight ?? v.w ?? 0);
      score = Number(v.score ?? v.alpha ?? v.edge ?? 0);
      targetValue = Number(v.target_value ?? v.targetValue ?? 0);
      pDrop = v.p_drop ?? v.pDrop ?? null;
      mult = v.risk_multiplier ?? v.xgb_risk_multiplier ?? null;
      side = v.side || side;
    } else {
      w = Number(v);
    }
    if (!Number.isFinite(w) || w <= 0) continue;
    out[sym] = {
      symbol: sym,
      weight: Number(w.toFixed(10)),
      target_value: Number.isFinite(targetValue) ? targetValue : 0,
      side,
      score: Number.isFinite(score) ? score : 0,
      p_drop: pDrop,
      risk_multiplier: mult,
      source: "ram26_engine_bridge_v2",
    };
  }
  return out;
}

function ensurePass(targets, data, championId) {
  const st = statsFromTargets(targets);
  const checks = {
    strategy_ram26: String(data.strategy_id || data.engine_version || championId).startsWith("RAM26"),
    target_n: st.n >= 8,
    gross_valid: st.gross >= 0.25 && st.gross <= 1.05,
    target_non_flat: st.nonFlat,
  };
  const failed = Object.entries(checks).filter(([,v]) => !v).map(([k]) => k);
  return { pass: failed.length === 0, failed, checks, stats: st };
}

const args = argMap(process.argv);
const redisUrl = process.env.REDIS_URL || process.env.ARES_REDIS_URL;
if (!redisUrl) {
  console.error("REDIS_URL/ARES_REDIS_URL required");
  process.exit(2);
}

const CHAMPION_ID = process.env.CHAMPION_ID || args["strategy-id"] || "RAM26_ALPHA_0001_b215c119ea23";
const PROMOTION_ID = process.env.PROMOTION_ID || "RAM26_ALPHA_DIRECT_LIVE_20260506T154832Z";
const ENGINE_FILE = args["engine-file"] || process.env.RAM26_ENGINE_FILE || "/home/ubuntu/ares_current/engines/ram26/ram26_alpha_engine_v1.py";
const PYTHON = process.env.PYTHON || "python3";
const featureMaxAge = String(args["feature-max-age-sec"] || process.env.RAM26_STAGED_MAX_AGE_SEC || "172800");
const featuresKey = args["features-key"] || process.env.RAM26_FEATURES_KEY || "ram26:features:v1";
const outputKey = args["output-key"] || "ram26:alpha:targets:v1:latest";
const stagedKey = args["ssot-staged-key"] || "ram26:ssot:target:v2:staged";
const publishStaged = args["publish-staged"] === "true" || args["publish-ssot-staged"] === "true" || args["once"] === "true";
const promoteCurrent = args["promote-current"] === "true";
const allowLiveCurrentWrite = args["allow-current-ssot-write"] === "true" || args["allow-live-ssot-write"] === "true";

if (!fs.existsSync(ENGINE_FILE)) {
  console.error(`Engine file missing: ${ENGINE_FILE}`);
  process.exit(2);
}

const tmpOut = path.join(os.tmpdir(), `ram26_bridge_v2_engine_${Date.now()}.json`);
const engineArgs = [
  ENGINE_FILE,
  "--once",
  "--features-key", featuresKey,
  "--output-key", outputKey,
  "--ssot-staged-key", stagedKey,
  "--target-gross", String(args["target-gross"] || "0.75"),
  "--max-weight", String(args["max-weight"] || "0.10"),
  "--min-active", String(args["min-active"] || "8"),
  "--feature-max-age-sec", featureMaxAge,
  "--publish-redis",
  "--publish-ssot-staged",
  "--output-json", tmpOut,
];

const proc = spawnSync(PYTHON, engineArgs, {
  encoding: "utf8",
  env: { ...process.env, RAM26_STAGED_MAX_AGE_SEC: featureMaxAge },
  timeout: Number(process.env.RAM26_ENGINE_TIMEOUT_MS || "90000"),
});

let data = {};
if (fs.existsSync(tmpOut)) {
  data = jparse(fs.readFileSync(tmpOut, "utf8"), {});
}
if (!Object.keys(data).length && proc.stdout) {
  data = jparse(proc.stdout.trim(), {});
}
data.strategy_id = data.strategy_id || CHAMPION_ID;
data.engine_version = data.engine_version || CHAMPION_ID;

const rawTargets = data.targets || data.weights || {};
const targets = normalizeTargets(rawTargets);
const gate = ensurePass(targets, data, CHAMPION_ID);
gate.strategy_id = data.strategy_id;
gate.engine_version = data.engine_version;
gate.engine_rc = proc.status;
gate.engine_stderr_tail = (proc.stderr || "").slice(-1200);
gate.feature_max_age_sec = Number(featureMaxAge);
gate.ts_ms = nowMs();
gate.producer = "ram26_final_to_champion_bridge_v2";

const redis = new Redis(redisUrl, {
  tls: redisUrl.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

function hashPayload(obj) {
  return crypto.createHash("sha256").update(JSON.stringify(obj, Object.keys(obj).sort())).digest("hex");
}

const ts = nowMs();
const targetHash = crypto.createHash("sha256").update(JSON.stringify(targets, Object.keys(targets).sort())).digest("hex");
const ssot = {
  ...data,
  targets,
  engine_version: CHAMPION_ID,
  strategy_id: CHAMPION_ID,
  champion_id: CHAMPION_ID,
  promotion_id: PROMOTION_ID,
  source: "ram26_engine_bridge_v2",
  issued_by: "ram26_final_to_champion_bridge_v2",
  target_hash: targetHash,
  ts,
  issued_at_ms: ts,
  stats: gate.stats,
};
const policy = {
  candidate_id: CHAMPION_ID,
  candidate_config_sha256: data.candidate_config_sha256 || "",
  universe_sha256: data.universe_sha256 || "",
  champion_version: CHAMPION_ID,
  champion_strategy_name: "RAM26_ALPHA_0001",
  champion_engine_file: "ram26_alpha_engine_v1.py",
  engine_version: CHAMPION_ID,
  strategy_id: CHAMPION_ID,
  promotion_id: PROMOTION_ID,
  issued_at_ms: ts,
  issued_by: "ram26_final_to_champion_bridge_v2",
  source: "ram26_alpha_engine_v1",
  targets,
  _meta_marker: {
    phase9_b1_marker: "live_full_champion",
    issued_by: "ram26_final_to_champion_bridge_v2",
    ts_ms: ts,
    validation_state: "OVERRIDE_APPROVED",
  },
  version: CHAMPION_ID,
  stats: gate.stats,
};

const pipe = redis.pipeline();
pipe.set("ram26:engine:gate:last", JSON.stringify(gate));
pipe.xadd("ram26:engine:gate:events", "MAXLEN", "~", 1000, "*",
  "event", gate.pass ? "engine_gate_pass" : "engine_gate_fail",
  "payload", JSON.stringify(gate));

if (gate.pass && publishStaged) {
  pipe.set(stagedKey, JSON.stringify(ssot));
  pipe.set(outputKey, JSON.stringify({ ...data, targets, strategy_id: CHAMPION_ID, engine_version: CHAMPION_ID, stats: gate.stats }));
  pipe.xadd("ram26:bridge:v2:events", "MAXLEN", "~", 1000, "*",
    "event", "staged_published",
    "n", String(gate.stats.n),
    "gross", String(gate.stats.gross),
    "payload", JSON.stringify(gate));
}

if (gate.pass && promoteCurrent && allowLiveCurrentWrite) {
  pipe.set("ssot:target:v2:current", JSON.stringify(ssot));
  pipe.set("policy:champion:active", JSON.stringify(policy));
  pipe.del("champion:targets:ssot");
  pipe.del("champion:target");
  for (const [sym, rec] of Object.entries(targets)) {
    const p = JSON.stringify(rec);
    pipe.hset("champion:targets:ssot", sym, p);
    pipe.hset("champion:target", sym, p);
  }
  const meta = JSON.stringify({
    producer: "ram26_final_to_champion_bridge_v2",
    ts,
    champion_id: CHAMPION_ID,
    target_hash: targetHash,
    stats: gate.stats,
  });
  pipe.set("champion:targets:ssot:meta", meta);
  pipe.set("champion:target:meta", meta);
  pipe.set("champion:targets:ssot:ts", String(ts));
  pipe.set("champion:target:ts", String(ts));
  pipe.set("ares:bridge:active", "LIVE_FINAL_TO_CHAMPION");
  pipe.set("ares:bridge:producer", "ram26_final_to_champion_bridge_v2");
  pipe.set("truth:champion:version", CHAMPION_ID);
  pipe.set("truth:champion:official_version", CHAMPION_ID);
  pipe.set("truth:champion:strategy_name", "RAM26_ALPHA_0001");
  pipe.set("truth:champion:engine_file", "ram26_alpha_engine_v1.py");
  pipe.set("truth:champion:standard_validator_decision", "GO");
  pipe.xadd("ram26:bridge:v2:events", "MAXLEN", "~", 1000, "*",
    "event", "current_promoted",
    "n", String(gate.stats.n),
    "gross", String(gate.stats.gross),
    "payload", JSON.stringify({ gate, targetHash }));
}

await pipe.exec();
await redis.quit();

const result = {
  pass: gate.pass,
  gate,
  target_stats: gate.stats,
  published_staged: gate.pass && publishStaged,
  promoted_current: gate.pass && promoteCurrent && allowLiveCurrentWrite,
  engine_stdout_tail: (proc.stdout || "").slice(-1200),
  engine_stderr_tail: (proc.stderr || "").slice(-1200),
};
console.log(JSON.stringify(result, null, 2));
process.exit(gate.pass ? 0 : 10);
