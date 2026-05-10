#!/usr/bin/env node
/*
 * ares-bootstrap-override-daemon.mjs (STRUCT-FIX-2026-05-08)
 * --------------------------------------------------------------------
 * Long-running PM2-managed daemon that watches for the validator NO_GO
 * deadlock pattern and issues a single signed bootstrap override when
 * the pattern persists for at least DEADLOCK_HOLD_S seconds.
 *
 * The daemon REUSES the issuance logic from issue_bootstrap_override.mjs
 * but with all guard checks tightened so it is safe to run continuously.
 *
 * Required env: REDIS_URL, ARES_OVERRIDE_SIGNING_KEY
 *
 * Tunables (env):
 *   POLL_INTERVAL_MS        default 10000   (10s)
 *   DEADLOCK_HOLD_S         default 30      (require deadlock for >=30s)
 *   ISSUE_COOLDOWN_S        default 600     (don't issue more often than 10min)
 *   BOOTSTRAP_OVERRIDE_TTL  default 180     (override TTL seconds)
 *
 * Safety guards (all must pass before issuance):
 *   1) validator decision == NO_GO
 *   2) reasons contain BOTH "targets_count_too_low" AND "cash_buffer_too_low"
 *   3) HLEN champion:targets:ssot == 0
 *   4) policy:override:active does not exist
 *   5) deadlock has been observed for at least DEADLOCK_HOLD_S
 *   6) at least ISSUE_COOLDOWN_S since last successful issuance
 *
 * State is persisted in Redis at `ares:bootstrap_override:state`.
 * Audit trail at stream `ares:override:audit`.
 */

import Redis from "ioredis";
import crypto from "node:crypto";

const POLL_MS         = parseInt(process.env.POLL_INTERVAL_MS || "10000", 10);
const HOLD_S          = parseInt(process.env.DEADLOCK_HOLD_S || "30", 10);
const COOLDOWN_S      = parseInt(process.env.ISSUE_COOLDOWN_S || "600", 10);
const TTL_S           = parseInt(process.env.BOOTSTRAP_OVERRIDE_TTL || "180", 10);
const STATE_KEY       = "ares:bootstrap_override:state";

const REDIS_URL = process.env.REDIS_URL;
const SIGNING_KEY = process.env.ARES_OVERRIDE_SIGNING_KEY || "";

if (!REDIS_URL || !SIGNING_KEY) {
  console.error("[FATAL] REDIS_URL or ARES_OVERRIDE_SIGNING_KEY missing");
  process.exit(2);
}

const redis = new Redis(REDIS_URL, {
  lazyConnect: false,
  maxRetriesPerRequest: 5,
  retryStrategy: (times) => Math.min(1000 * times, 10000),
});

let shuttingDown = false;
function gracefulShutdown(sig) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[INFO] received ${sig}, shutting down gracefully...`);
  setTimeout(async () => {
    try { await redis.quit(); } catch (_) {}
    process.exit(0);
  }, 500);
}
process.on("SIGINT", () => gracefulShutdown("SIGINT"));
process.on("SIGTERM", () => gracefulShutdown("SIGTERM"));

function hmacSign(bodyObj, key) {
  const sortedBody = JSON.stringify(bodyObj, Object.keys(bodyObj).sort());
  return crypto.createHmac("sha256", key).update(sortedBody).digest("hex");
}

async function loadState() {
  const raw = await redis.get(STATE_KEY).catch(() => null);
  if (!raw) return { deadlock_first_seen_ms: 0, last_issued_ms: 0, issued_count: 0 };
  try { return JSON.parse(raw); }
  catch (_) { return { deadlock_first_seen_ms: 0, last_issued_ms: 0, issued_count: 0 }; }
}
async function saveState(s) {
  await redis.set(STATE_KEY, JSON.stringify(s), "EX", 86400).catch(() => {});
}

async function checkDeadlock() {
  const validatorRaw = await redis.get("policy:validator:latest");
  if (!validatorRaw) return { deadlock: false, reason: "no_validator" };
  let validator;
  try { validator = JSON.parse(validatorRaw); } catch (_) { return { deadlock: false, reason: "validator_not_json" }; }
  const decision = String(validator.decision || "").toUpperCase();
  const reasons = Array.isArray(validator.reasons) ? validator.reasons : [];
  if (decision !== "NO_GO") return { deadlock: false, reason: `decision=${decision}`, validator };
  const hasTargets = reasons.some(r => /targets_count_too_low/.test(r));
  const hasCash    = reasons.some(r => /cash_buffer_too_low/.test(r));
  if (!(hasTargets && hasCash)) return { deadlock: false, reason: "reasons_not_match", validator };
  const hlen = await redis.hlen("champion:targets:ssot").catch(() => -1);
  if (hlen !== 0) return { deadlock: false, reason: `hlen=${hlen}`, validator };
  const overrideExists = await redis.exists("policy:override:active").catch(() => 0);
  if (overrideExists) return { deadlock: false, reason: "override_already_active", validator };
  return { deadlock: true, validator };
}

async function issueOverride(validator) {
  const championRaw = await redis.get("policy:champion:active");
  if (!championRaw) throw new Error("policy:champion:active missing");
  const champion = JSON.parse(championRaw);
  const candidateSha = champion.candidate_config_sha256;
  const candidateId = champion.candidate_id;
  if (!candidateSha) throw new Error("champion has no candidate_config_sha256");

  // sanity: validator should match current candidate_config_sha
  const validatorSha = validator.candidate_config_sha256;
  if (validatorSha && validatorSha !== candidateSha) {
    throw new Error(`validator_sha mismatch (validator=${validatorSha} champion=${candidateSha})`);
  }

  const nowMs = Date.now();
  const expiresAtMs = nowMs + TTL_S * 1000;

  const body = {
    candidate_config_sha256: candidateSha,
    candidate_id: candidateId,
    decision: "APPROVED",
    policy: "bootstrap_deadlock_break_v1",
    reason: "bootstrap-daemon: deadlock pattern persisted; allowing one-cycle publish",
    issued_at_ms: nowMs,
    expires_at_ms: expiresAtMs,
    issued_by: "ares_bootstrap_override_daemon",
  };
  const signature = hmacSign(body, SIGNING_KEY);
  const override = { ...body, signature };

  await redis.set("policy:override:active", JSON.stringify(override), "PX", (TTL_S + 30) * 1000);
  await redis.xadd("ares:override:audit", "MAXLEN", "~", "2000", "*",
    "ts_ms", String(nowMs),
    "actor", "ares_bootstrap_override_daemon",
    "candidate_id", String(candidateId),
    "candidate_config_sha256", candidateSha,
    "policy", body.policy,
    "ttl_s", String(TTL_S),
    "reason", body.reason);
  return { candidateId, candidateSha, expiresAtMs };
}

async function tick() {
  const state = await loadState();
  const now = Date.now();
  const result = await checkDeadlock();

  if (!result.deadlock) {
    if (state.deadlock_first_seen_ms !== 0) {
      console.log(`[INFO] deadlock cleared (reason=${result.reason})`);
      state.deadlock_first_seen_ms = 0;
      await saveState(state);
    }
    return;
  }

  // deadlock detected
  if (state.deadlock_first_seen_ms === 0) {
    state.deadlock_first_seen_ms = now;
    console.log(`[INFO] deadlock pattern first seen at ${new Date(now).toISOString()}`);
    await saveState(state);
  }

  const heldS = (now - state.deadlock_first_seen_ms) / 1000;
  if (heldS < HOLD_S) {
    console.log(`[INFO] deadlock held=${heldS.toFixed(0)}s (need >=${HOLD_S}s); waiting`);
    return;
  }

  // cooldown check
  const sinceLastIssueS = (now - state.last_issued_ms) / 1000;
  if (state.last_issued_ms > 0 && sinceLastIssueS < COOLDOWN_S) {
    console.log(`[INFO] in cooldown: ${sinceLastIssueS.toFixed(0)}s since last issuance (need >=${COOLDOWN_S}s)`);
    return;
  }

  try {
    const out = await issueOverride(result.validator);
    state.last_issued_ms = now;
    state.issued_count = (state.issued_count || 0) + 1;
    state.deadlock_first_seen_ms = 0; // reset; will reappear if it really persists
    await saveState(state);
    console.log(JSON.stringify({
      kind: "OVERRIDE_ISSUED_BY_DAEMON",
      candidate_id: out.candidateId,
      candidate_config_sha256: out.candidateSha,
      expires_at_ms: out.expiresAtMs,
      ttl_s: TTL_S,
      issued_count_total: state.issued_count,
    }));
  } catch (e) {
    console.error("[ERROR] issueOverride failed:", e?.message || e);
  }
}

async function main() {
  console.log(JSON.stringify({
    kind: "DAEMON_STARTING",
    version: "ares_bootstrap_override_daemon_v1_struct_fix_2026_05_08",
    poll_ms: POLL_MS, hold_s: HOLD_S, cooldown_s: COOLDOWN_S, ttl_s: TTL_S,
  }));

  while (!shuttingDown) {
    try { await tick(); }
    catch (e) { console.error("[ERROR] tick:", e?.message || e); }
    await new Promise(r => setTimeout(r, POLL_MS));
  }
}

main().catch(e => {
  console.error("[FATAL]", e?.message || e);
  process.exit(1);
});
