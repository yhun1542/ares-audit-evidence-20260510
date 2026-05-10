#!/usr/bin/env node
/**
 * redis_env_sentinel.mjs — MUSK RCA-v7 (STRUCTURAL FIX 2026-05-08)
 * ================================================================
 *
 * 근본 원인 (1원리 분석):
 *   - 기존 v6 sentinel 은 /proc/<pid>/environ 으로 REDIS_URL 검사
 *   - 하지만 일부 PM2 자식은 spawn 직후 /proc/<pid>/environ 이
 *     비어있는 짧은 race window 가 있음 (kernel commit 지연)
 *   - 이 때 readEnvFor() 는 빈 객체 반환 → REDIS_URL_MISSING 오인
 *   - 자기 자신(self) 도 spawn 직후엔 같은 문제 발생 → 자기-위반 → autofix
 *     로직이 자신을 재시작 → 또 자기-위반 → 무한 루프
 *
 * 구조적 수정:
 *   1) readEnvFor() race-safe: empty/short read 시 1회 retry (200ms 후)
 *   2) Self-exclusion 강화: SELF_NAME 뿐 아니라 SELF_PID 도 명시 제외
 *   3) Hysteresis 강화: VIOLATION_THRESHOLD 3 → 5 (더 보수적)
 *   4) Fresh-spawn grace: PM2 entry 의 pm_uptime < 30s 이면 검사 스킵
 *   5) Autofix loop guard: 동일 process 를 5분 이내 2회 이상 재시작 금지
 *
 * 모든 변경은 backward-compatible: 기존 ENV는 그대로 동작.
 */
"use strict";
import Redis from "ioredis";
import fs from "node:fs";
import { execSync } from "node:child_process";

// ── MUSK RCA-v7 hysteresis (was 3) ──
const VIOLATION_THRESHOLD = Number(process.env.VIOLATION_THRESHOLD || 5);
const FRESH_SPAWN_GRACE_MS = Number(process.env.FRESH_SPAWN_GRACE_MS || 30_000);
const AUTOFIX_DEDUP_MS = Number(process.env.AUTOFIX_DEDUP_MS || 5 * 60 * 1000);

const _consecutiveViolations = new Map();
const _lastAutofixAt = new Map(); // name -> ts ms

function _checkHysteresis(pid, isViolation) {
  const key = String(pid);
  if (!isViolation) {
    _consecutiveViolations.delete(key);
    return false;
  }
  const count = (_consecutiveViolations.get(key) || 0) + 1;
  _consecutiveViolations.set(key, count);
  return count >= VIOLATION_THRESHOLD;
}

const SSOT_FILE = process.env.ARES_REDIS_SSOT || "/etc/ares/redis.env";
const AUTOFIX = process.env.PM2_SENTINEL_AUTOFIX === "1";
const INTERVAL = Number(process.env.PM2_SENTINEL_INTERVAL_MS || 60_000);
const DEDUP_MS = Number(process.env.PM2_SENTINEL_DEDUP_MS || 10 * 60 * 1000);

if (!fs.existsSync(SSOT_FILE)) {
  console.error(`[sentinel-v7.1] SSOT missing: ${SSOT_FILE}`);
  process.exit(78);
}

const ssotRaw = fs.readFileSync(SSOT_FILE, "utf8");
const ssot = Object.fromEntries(
  ssotRaw
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && l.includes("="))
    .map((l) => {
      const i = l.indexOf("=");
      return [l.slice(0, i), l.slice(i + 1).replace(/^['"]|['"]$/g, "")];
    })
);
const SSOT_REDIS_URL = ssot.REDIS_URL || ssot.ARES_REDIS_URL;
const SSOT_REDIS_HOST = ssot.ARES_REDIS_HOST || ssot.REDIS_HOST;
if (!SSOT_REDIS_URL || !SSOT_REDIS_HOST) {
  console.error("[sentinel-v7.1] SSOT malformed — REDIS_URL/ARES_REDIS_HOST missing");
  process.exit(78);
}

const redis = new Redis(SSOT_REDIS_URL, {
  tls: SSOT_REDIS_URL.startsWith("rediss://") ? {} : undefined,
  maxRetriesPerRequest: 3,
  connectTimeout: 10_000,
  lazyConnect: false,
});
redis.on("error", (e) => console.error("[sentinel-v7.1] redis err:", e?.message || e));

const lastAlert = new Map();
let pm2List = [];

const SELF_NAME = "redis-env-sentinel";
const SELF_PID = process.pid;

// ── MUSK RCA-v7: race-safe /proc/<pid>/environ reader ──
function readEnvForOnce(pid) {
  try {
    const buf = fs.readFileSync(`/proc/${pid}/environ`);
    if (!buf || buf.length < 16) return null; // race window: kernel hasn't committed yet
    const out = {};
    for (const kv of buf.toString("utf8").split("\0")) {
      if (!kv) continue;
      const i = kv.indexOf("=");
      if (i > 0) out[kv.slice(0, i)] = kv.slice(i + 1);
    }
    // Sanity: a valid PM2 child should have at least PWD and PATH.
    if (Object.keys(out).length < 5) return null;
    return out;
  } catch {
    return null;
  }
}

async function readEnvFor(pid) {
  const first = readEnvForOnce(pid);
  if (first) return first;
  // race-safe single retry after short delay
  await new Promise((r) => setTimeout(r, 200));
  return readEnvForOnce(pid);
}

function validateRedisUrlShape(url) {
  if (!url) return "REDIS_URL_MISSING";
  if (url.includes("localhost") || url.includes("127.0.0.1")) return "REDIS_URL_LOCALHOST";
  let u;
  try {
    u = new URL(url);
  } catch {
    return "REDIS_URL_PARSE_ERROR";
  }
  if (u.protocol !== "rediss:") return "REDIS_URL_NOT_TLS";
  if ((u.hostname || "").toLowerCase() !== String(SSOT_REDIS_HOST).toLowerCase())
    return "REDIS_URL_MISMATCH";
  if (u.hostname.includes("amazonaws.com") && (!u.username || !u.password)) {
    return "REDIS_URL_CREDENTIAL_SHAPE";
  }
  if (url !== SSOT_REDIS_URL) return "REDIS_URL_NOT_SSOT_EXACT";
  return null;
}

function scanAndMaybeSanitizePm2Dump() {
  const dumpFile = process.env.PM2_DUMP_FILE || "/home/ubuntu/.pm2/dump.pm2";
  const out = { file: dumpFile, checked: 0, violations: [], sanitized: 0 };
  if (!fs.existsSync(dumpFile)) return out;
  let arr;
  try {
    arr = JSON.parse(fs.readFileSync(dumpFile, "utf8"));
  } catch (e) {
    console.error("[sentinel-v7.1] dump.pm2 parse error:", e?.message || e);
    return out;
  }
  if (!Array.isArray(arr)) return out;
  let modified = false;
  for (const entry of arr) {
    if (!entry?.env) continue;
    out.checked++;
    const u = entry.env.REDIS_URL;
    if (u && validateRedisUrlShape(u)) {
      out.violations.push({
        name: entry.name,
        before: u.slice(0, 60) + "...",
      });
      if (AUTOFIX) {
        entry.env.REDIS_URL = SSOT_REDIS_URL;
        modified = true;
        out.sanitized++;
      }
    }
  }
  if (modified) {
    try {
      fs.writeFileSync(dumpFile, JSON.stringify(arr, null, 2));
    } catch (e) {
      console.error("[sentinel-v7.1] dump.pm2 write error:", e?.message || e);
    }
  }
  return out;
}

async function tgAlert(code, body) {
  const now = Date.now();
  const last = lastAlert.get(code) || 0;
  if (now - last < DEDUP_MS) return;
  lastAlert.set(code, now);
  const TOKEN = process.env.TG_TOKEN;
  const CHAT = process.env.TG_CHAT;
  if (!TOKEN || !CHAT) return;
  try {
    const text = `🛡️ REDIS-ENV-SENTINEL ${code}\n${body}`;
    const url = `https://api.telegram.org/bot${TOKEN}/sendMessage`;
    await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: CHAT, text, parse_mode: "Markdown" }),
    });
  } catch (e) {
    console.error("[sentinel-v7.1] tg send error:", e?.message || e);
  }
}

async function fetchPm2List() {
  try {
    const out = execSync("pm2 jlist", {
      maxBuffer: 64 * 1024 * 1024,
      timeout: 15_000,
      env: { ...process.env, PM2_SILENT: "true" },
    });
    return JSON.parse(out.toString());
  } catch (e) {
    console.error("[sentinel-v7.1] pm2 jlist failed:", e?.message || e);
    return [];
  }
}

async function tick() {
  pm2List = await fetchPm2List();
  if (!pm2List.length) return;

  const audited = [];
  const violations = [];
  const namingViolations = [];
  const seenNames = new Map();
  const nowMs = Date.now();

  for (const p of pm2List) {
    const pid = p.pid;
    const name = p.name;
    const status = p.pm2_env?.status;
    if (!pid || status !== "online") continue;
    seenNames.set(name, (seenNames.get(name) || 0) + 1);

    // ── MUSK RCA-v7: SELF exclusion (name + pid) ──
    if (name === SELF_NAME || pid === SELF_PID) {
      audited.push({ name, pid, status, code: "OK_SELF" });
      continue;
    }

    // ── MUSK RCA-v7: fresh-spawn grace window ──
    const pmUptime = p.pm2_env?.pm_uptime || 0;
    const procAgeMs = pmUptime ? (nowMs - pmUptime) : Infinity;
    if (procAgeMs < FRESH_SPAWN_GRACE_MS) {
      audited.push({ name, pid, status, code: "OK_FRESH_SPAWN", age_ms: procAgeMs });
      continue;
    }

    const env = await readEnvFor(pid);
    if (!env) {
      // Could not read /proc — DO NOT flag as violation; just record probe failure.
      audited.push({ name, pid, status, code: "PROBE_FAILED" });
      continue;
    }

    const url = env.REDIS_URL || null;
    let code = null;
    if (!url) {
      code = "REDIS_URL_MISSING";
    } else {
      try {
        const parsed = new URL(url);
        const host = (parsed.hostname || "").toLowerCase();
        if (parsed.protocol !== "rediss:") code = "REDIS_URL_NOT_TLS";
        else if (host === "localhost" || host === "127.0.0.1") code = "REDIS_URL_LOCALHOST";
        else if (host !== String(SSOT_REDIS_HOST).toLowerCase()) code = "REDIS_URL_MISMATCH";
        else if (!parsed.username) code = "REDIS_URL_USERNAME_MISSING";
        else if (!parsed.password) code = "REDIS_URL_PASSWORD_MISSING";
        else if (url !== SSOT_REDIS_URL) code = "REDIS_URL_NOT_SSOT_EXACT";
      } catch {
        code = "REDIS_URL_PARSE_ERROR";
      }
    }

    audited.push({ name, pid, status, code: code || "OK" });

    const isEcosystemFileBased = /^ecosystem\..+/.test(name);
    if (isEcosystemFileBased) {
      if (code) namingViolations.push({ name, pid, code: `PM2_NAMING_VIOLATION:${code}` });
      continue;
    }

    if (code) {
      // Hysteresis: only flag if N consecutive ticks
      const confirmed = _checkHysteresis(pid, true);
      if (confirmed) {
        violations.push({ name, pid, code, url: url || "(none)" });
      }
    } else {
      _checkHysteresis(pid, false);
    }
  }

  const duplicates = [...seenNames.entries()]
    .filter(([_, n]) => n > 1)
    .map(([name, n]) => ({ name, count: n }));
  const dumpScan = scanAndMaybeSanitizePm2Dump();
  const dumpViolations = dumpScan.violations || [];

  const snapshot = {
    ts_ms: Date.now(),
    sentinel_version: "v7.1-structural-fallback-20260508",
    ssot_host: SSOT_REDIS_HOST,
    self_pid: SELF_PID,
    total: audited.length,
    violations: violations.length,
    naming_violations: namingViolations.length,
    duplicates,
    dump_checked: dumpScan.checked,
    dump_violations: dumpViolations.length,
    dump_sanitized: dumpScan.sanitized,
    items: audited,
  };

  if (namingViolations.length || duplicates.length) {
    const lines = [
      ...namingViolations.map(
        (v) => `• *${v.name}* (pid=${v.pid}) → PM2_NAMING_VIOLATION (file-based registration)`
      ),
      ...duplicates.map((d) => `• *${d.name}* registered ${d.count} times → DUPLICATE`),
    ].join("\n");
    console.log(
      JSON.stringify({
        ts: new Date().toISOString(),
        level: "WARN",
        proc: "redis-env-sentinel-v7.1",
        message: "naming/duplicate violations",
        namingViolations,
        duplicates,
      })
    );
    await tgAlert(
      "PM2_NAMING_VIOLATION",
      `Structural PM2 registration anomaly detected (NOT auto-fixed):\n${lines}\n\n` +
        "Manual cleanup required: `pm2 delete ecosystem.<...>` then `pm2 start /path/to/ecosystem.cjs`."
    );
  }

  try {
    await redis.setex("ops:redis_env_audit:last", 300, JSON.stringify(snapshot));
  } catch (e) {
    console.error("[sentinel-v7.1] snapshot write failed:", e?.message || e);
  }

  if (dumpViolations.length > 0) {
    console.log(
      JSON.stringify({
        ts: new Date().toISOString(),
        level: AUTOFIX ? "WARN" : "ERROR",
        proc: "redis-env-sentinel-v7.1",
        message: AUTOFIX ? "pm2 dump sanitized" : "pm2 dump violations",
        checked: dumpScan.checked,
        sanitized: dumpScan.sanitized,
        violations: dumpViolations,
      })
    );
    await tgAlert(
      "PM2_DUMP_REDIS_URL_VIOLATION",
      `${dumpViolations.length} PM2 dump Redis URL field(s) violate SSOT policy. ` +
        (AUTOFIX
          ? `Sanitized ${dumpScan.sanitized} field(s) in dump.pm2.`
          : "Manual dump sanitization required.")
    );
  }

  if (violations.length > 0) {
    const lines = violations.map((v) => `• *${v.name}* (pid=${v.pid}) → ${v.code}`).join("\n");
    console.log(
      JSON.stringify({
        ts: new Date().toISOString(),
        level: "ERROR",
        proc: "redis-env-sentinel-v7.1",
        message: "violations",
        violations,
      })
    );
    await tgAlert(
      "REDIS_URL_VIOLATION",
      `${violations.length} PM2 process(es) violate SSOT policy:\n${lines}\n\n` +
        (AUTOFIX
          ? "Auto-fix enabled: restarting with --update-env."
          : "Auto-fix disabled; manual intervention required.")
    );
    if (AUTOFIX) {
      for (const v of violations) {
        // ── MUSK RCA-v7: autofix loop guard ──
        const lastAt = _lastAutofixAt.get(v.name) || 0;
        if (nowMs - lastAt < AUTOFIX_DEDUP_MS) {
          console.log(
            JSON.stringify({
              ts: new Date().toISOString(),
              level: "WARN",
              proc: "redis-env-sentinel-v7.1",
              message: "autofix_skipped_dedup",
              name: v.name,
              age_ms: nowMs - lastAt,
            })
          );
          continue;
        }
        _lastAutofixAt.set(v.name, nowMs);
        try {
          execSync(
            `set -a; source ${SSOT_FILE}; set +a; pm2 restart ${v.name} --update-env`,
            { shell: "/bin/bash", stdio: "inherit", maxBuffer: 10 * 1024 * 1024 }
          );
        } catch (e) {
          console.error(`[sentinel-v7.1] autofix failed for ${v.name}:`, e?.message || e);
        }
      }
    }
  } else {
    console.log(
      JSON.stringify({
        ts: new Date().toISOString(),
        level: "INFO",
        proc: "redis-env-sentinel-v7.1",
        message: `OK — ${audited.length} PM2 processes pass SSOT policy`,
      })
    );
  }
}

async function main() {
  console.log(
    `[sentinel-v7.1] starting interval=${INTERVAL}ms autofix=${AUTOFIX} ssot_host=${SSOT_REDIS_HOST} self_pid=${SELF_PID} hysteresis=${VIOLATION_THRESHOLD} grace=${FRESH_SPAWN_GRACE_MS}ms`
  );
  await new Promise((resolve) => {
    if (redis.status === "ready") return resolve();
    redis.once("ready", resolve);
  });
  console.log("[sentinel-v7.1] redis ready");
  await tick();
  setInterval(tick, INTERVAL);
}

process.on("SIGTERM", () => process.exit(0));
process.on("SIGINT", () => process.exit(0));

main().catch((e) => {
  console.error("[sentinel-v7.1] fatal:", e);
  process.exit(1);
});
