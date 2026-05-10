#!/usr/bin/env node
// ares-heartbeat-sentinel (2026-04-22, ROOT-CAUSE-FIX-P1) ---------------------
//
// Sidecar process that observes PM2 process states and writes a standard
// Redis heartbeat key per monitored PM2 process. Also writes the LEGACY
// `ares:sentinel:heartbeat` key (raw ISO-8601 string) expected by
// ops/alert-rules.mjs -> R-HEARTBEAT-SENTINEL-DOWN. This closes the broken
// contract that produced perpetual false CRITICAL alerts.
//
// Design goals:
//   1. Zero modifications to live-trading process source code.
//   2. Never cause a false NEGATIVE (i.e., never write a heartbeat when the
//      target process is not actually online).
//   3. Cheap: a single `pm2 jlist` every CHECK_INTERVAL_MS seconds.
//   4. Non-fatal on any error: own exceptions must never take trading down.
//
// Key conventions (all with TTL = HEARTBEAT_TTL_SEC, default 60s):
//   ares:hb:<pm2-name>              JSON { ts, ts_ms, status, pid, ... }
//   ares:hb:sentinel:status         JSON { ts, ok, observed, missing, unhealthy }
//   ares:sentinel:heartbeat         Raw ISO-8601 string (LEGACY CONTRACT)
//                                    ^ consumed by alert-rules.mjs
//
// To disable a target (e.g., for maintenance), set env
//   HEARTBEAT_SENTINEL_DISABLE="name1,name2"

import Redis from "ioredis";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const REDIS_URL = process.env.ARES_REDIS_URL || process.env.REDIS_URL;
if (!REDIS_URL) {
  console.error(JSON.stringify({ ts: new Date().toISOString(), level: "ERROR", proc: "heartbeat-sentinel", msg: "No ARES_REDIS_URL/REDIS_URL env" }));
  process.exit(2);
}

const CHECK_INTERVAL_MS = Number(process.env.HEARTBEAT_SENTINEL_INTERVAL_MS || 5000);
const HEARTBEAT_TTL_SEC = Number(process.env.HEARTBEAT_TTL_SEC || 60);
const KEY_PREFIX = process.env.HEARTBEAT_KEY_PREFIX || "ares:hb:";
const STATUS_KEY = process.env.HEARTBEAT_SENTINEL_STATUS_KEY || "ares:hb:sentinel:status";
// ── P1 ROOT-CAUSE FIX ────────────────────────────────────────────────
// This is the key that ops/alert-rules.mjs -> R-HEARTBEAT-SENTINEL-DOWN
// reads. It expects a RAW ISO-8601 string (not JSON). The previous
// sentinel never wrote it, so the rule fired indefinitely.
const LEGACY_HB_KEY = process.env.HEARTBEAT_LEGACY_KEY || "ares:sentinel:heartbeat";
// ─────────────────────────────────────────────────────────────────────

// Default target set. Can be overridden with HEARTBEAT_SENTINEL_TARGETS env
// (comma separated).
const DEFAULT_TARGETS = [
  "ssot-pipeline-guard",
  "ssot-writer-v2",
  "ssot-promote-v2",
  "nextgen2-live",
  "final-to-champion-bridge",
  "realtime-data-feed-v3",
  // "realtime-data-feed",  // [RETIRED 20260506] replaced by realtime-data-feed-v3
  "order-intent-executor",
  "kis-balance-sync",
  "watchdog-monitor",
];

const TARGETS = (process.env.HEARTBEAT_SENTINEL_TARGETS
  ? process.env.HEARTBEAT_SENTINEL_TARGETS.split(",").map(s => s.trim()).filter(Boolean)
  : DEFAULT_TARGETS);

const DISABLED = new Set((process.env.HEARTBEAT_SENTINEL_DISABLE || "")
  .split(",").map(s => s.trim()).filter(Boolean));

function log(level, msg, ctx = {}) {
  console.log(JSON.stringify({
    ts: new Date().toISOString(),
    level,
    proc: "heartbeat-sentinel",
    msg,
    ...ctx,
  }));
}

// Parse a rediss:// or redis:// URL. We piggyback on ioredis defaults but need
// to force TLS for rediss.
const redis = new Redis(REDIS_URL, {
  tls: REDIS_URL.startsWith("rediss://") ? {} : undefined,
  maxRetriesPerRequest: 2,
  enableOfflineQueue: false,
  lazyConnect: false,
  connectTimeout: 5000,
});

redis.on("error", (e) => {
  // Don't crash on transient connection errors; log and let ioredis reconnect.
  log("WARN", "redis_error", { error: String(e?.message || e) });
});

async function fetchPm2List() {
  // We prefer `pm2 jlist` rather than importing pm2 as a library, because
  // library mode would try to spawn its own god daemon under a separate UID.
  const { stdout } = await execFileAsync("pm2", ["jlist"], {
    maxBuffer: 32 * 1024 * 1024,
    timeout: 10_000,
    env: { ...process.env, PM2_SILENT: "true" },
  });
  return JSON.parse(stdout);
}

function isHealthy(entry) {
  if (!entry || !entry.pm2_env) return false;
  if (entry.pm2_env.status !== "online") return false;
  // Consider waiting_restart / stopped / errored / stopping as unhealthy.
  // Unstable_restarts > 5 within short window is also unhealthy.
  const unstable = Number(entry.pm2_env.unstable_restarts || 0);
  if (unstable > 10) return false;
  // PM2 sets pm_uptime=0 for short periods during start; that's fine.
  return true;
}

async function writeLegacyHeartbeat() {
  // ROOT-CAUSE-FIX-P1: Write the legacy key that alert-rules.mjs reads.
  // The sentinel itself is a healthy process if this function was reached,
  // so emitting this key is safe regardless of target states.
  try {
    await redis.set(LEGACY_HB_KEY, new Date().toISOString(), "EX", HEARTBEAT_TTL_SEC);
  } catch (e) {
    log("WARN", "legacy_hb_write_failed", { key: LEGACY_HB_KEY, error: String(e?.message || e) });
  }
}

async function tick() {
  // P1 FIX: Write legacy heartbeat FIRST, so even a transient pm2 jlist
  // failure does not falsely suggest the sentinel is down.
  await writeLegacyHeartbeat();

  let list;
  try {
    list = await fetchPm2List();
  } catch (e) {
    log("WARN", "pm2_jlist_failed", { error: String(e?.message || e) });
    // Update sentinel status but don't touch heartbeat keys so that stale
    // keys naturally expire (TTL 60s).
    try {
      await redis.set(STATUS_KEY, JSON.stringify({
        ts: new Date().toISOString(),
        ok: false,
        error: String(e?.message || e),
      }), "EX", HEARTBEAT_TTL_SEC);
    } catch {}
    return;
  }
  const byName = Object.create(null);
  for (const x of list) {
    if (x && x.name) byName[x.name] = x;
  }
  const nowMs = Date.now();
  const nowSec = Math.floor(nowMs / 1000);
  const observed = [];
  const missing = [];
  const unhealthy = [];
  for (const name of TARGETS) {
    if (DISABLED.has(name)) continue;
    const entry = byName[name];
    if (!entry) { missing.push(name); continue; }
    if (!isHealthy(entry)) { unhealthy.push({ name, status: entry.pm2_env?.status }); continue; }
    const doc = {
      ts: nowSec,
      ts_ms: nowMs,
      status: entry.pm2_env.status,
      pid: entry.pid || null,
      uptime_ms: entry.pm2_env.pm_uptime ? (nowMs - entry.pm2_env.pm_uptime) : 0,
      restarts: entry.pm2_env.restart_time ?? 0,
      unstable_restarts: entry.pm2_env.unstable_restarts ?? 0,
      sentinel_version: "1.1.0-p1",
    };
    observed.push(name);
    try {
      await redis.set(`${KEY_PREFIX}${name}`, JSON.stringify(doc), "EX", HEARTBEAT_TTL_SEC);
    } catch (e) {
      log("WARN", "hb_write_failed", { name, error: String(e?.message || e) });
    }
  }
  try {
    await redis.set(STATUS_KEY, JSON.stringify({
      ts: new Date().toISOString(),
      ok: true,
      observed_count: observed.length,
      missing_count: missing.length,
      unhealthy_count: unhealthy.length,
      observed,
      missing,
      unhealthy,
    }), "EX", HEARTBEAT_TTL_SEC);
  } catch {}
  // Periodic info log for ops visibility (every ~60s).
  if (!tick._lastInfoAt || (nowMs - tick._lastInfoAt) > 60_000) {
    log("INFO", "tick", {
      observed: observed.length,
      missing: missing.length,
      unhealthy: unhealthy.length,
    });
    tick._lastInfoAt = nowMs;
  }
}

log("INFO", "starting", {
  version: "1.1.0-p1",
  targets: TARGETS,
  disabled: Array.from(DISABLED),
  interval_ms: CHECK_INTERVAL_MS,
  ttl_sec: HEARTBEAT_TTL_SEC,
  legacy_hb_key: LEGACY_HB_KEY,
});

// Kick off an immediate tick, then on interval.
tick().catch(e => log("WARN", "tick_failed", { error: String(e?.message || e) }));
setInterval(() => { tick().catch(e => log("WARN", "tick_failed", { error: String(e?.message || e) })); }, CHECK_INTERVAL_MS);

const shutdown = async (sig) => {
  log("INFO", "shutdown", { sig });
  try { await redis.quit(); } catch {}
  process.exit(0);
};
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
