#!/usr/bin/env node
/**
 * RAM26 Risk Rate Publisher v1
 *
 * Purpose:
 * - Restore durable `state:risk_rate` and `state:risk_rate:ts` telemetry when
 *   final-to-champion-bridge does not publish it.
 * - Observation/risk telemetry only. Does not write targets, mode, trading.
 */

import Redis from "ioredis";

const REDIS_URL = process.env.REDIS_URL || process.env.ARES_REDIS_URL;
if (!REDIS_URL) throw new Error("REDIS_URL/ARES_REDIS_URL required");

const CYCLE_MS = Number(process.env.RAM26_RISK_RATE_CYCLE_MS || "5000");
const EMA_SPAN = Number(process.env.RAM26_RISK_RATE_EMA_SPAN || "20");
const CEILING = Number(process.env.RAM26_RISK_RATE_CEILING || "0.70");
const EVENT_STREAM = "ram26:risk_rate:events";

const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

function parse(raw, fallback = {}) {
  if (!raw) return fallback;
  try { return JSON.parse(String(raw)); } catch { return fallback; }
}
function n(x, d = 0) {
  const v = Number(x);
  return Number.isFinite(v) ? v : d;
}
function clamp(x, lo = 0, hi = 1) {
  return Math.max(lo, Math.min(hi, x));
}
function stressFromRegime(obj) {
  const stress = n(obj?.stress?.score ?? obj?.stress_score ?? obj?.stress ?? 0, 0);
  const vixZ = n(obj?.vix_z ?? obj?.vixZ ?? obj?.features?.vix_z ?? 0, 0);
  const dd20 = Math.abs(n(obj?.dd20 ?? obj?.dd_20 ?? obj?.drawdown_20d ?? obj?.features?.dd20 ?? 0, 0));
  const regime = String(obj?.regime ?? obj?.state ?? obj?.final_regime ?? "").toUpperCase();
  const regimeStress = {
    CRASH: 0.70, CRISIS: 0.60, RISK_OFF: 0.45, BEAR: 0.35, CAUTION: 0.20, CAUTIOUS: 0.20,
    TRANSITION: 0.12, NORMAL: 0.03, NEUTRAL: 0.03, BULL: 0.02, CALM: 0.02
  }[regime] ?? 0;
  const vixStress = clamp((vixZ - 1.0) / 4.0, 0, 0.5);
  const ddStress = clamp(dd20 * 8.0, 0, 0.5);
  return clamp(Math.max(stress, vixStress, ddStress, regimeStress), 0, CEILING);
}
async function readRegime() {
  for (const key of ["ctx:regime:state", "regime:final:current", "regime:consensus:current", "regime:current"]) {
    const type = await redis.type(key).catch(() => "none");
    if (type === "string") {
      const obj = parse(await redis.get(key), null);
      if (obj) return { key, obj };
    } else if (type === "hash") {
      const h = await redis.hgetall(key);
      if (h && Object.keys(h).length) return { key, obj: h };
    }
  }
  return { key: null, obj: {} };
}
async function cycle() {
  const { key, obj } = await readRegime();
  const input = stressFromRegime(obj);
  const prev = n(await redis.get("state:risk_rate"), 0);
  const alpha = 2 / (EMA_SPAN + 1);
  let rr = clamp(alpha * input + (1 - alpha) * prev, 0, CEILING);
  if (rr > 0 && rr < 1e-12) rr = 0;
  const iso = new Date().toISOString();
  const meta = { producer: "ram26_risk_rate_publisher_v1", source_key: key, input_stress: input, prev, rr, ema_span: EMA_SPAN, ts: iso };
  await redis
    .multi()
    .set("state:risk_rate", String(rr))
    .set("state:risk_rate:ts", iso)
    .set("state:risk_rate:meta", JSON.stringify(meta), "EX", 60)
    .xadd(EVENT_STREAM, "MAXLEN", "~", 1000, "*", "rr", String(rr), "input", String(input), "source_key", String(key || ""), "payload", JSON.stringify(meta))
    .exec();
  console.log(JSON.stringify(meta));
}
async function main() {
  const once = process.argv.includes("--once");
  while (true) {
    try { await cycle(); }
    catch (e) { console.error(`[ram26-risk-rate] ${e.stack || e.message}`); }
    if (once) break;
    await new Promise(r => setTimeout(r, CYCLE_MS));
  }
  await redis.quit();
}
main().catch(e => { console.error(e); process.exit(1); });
