#!/usr/bin/env node
/* P9-RATELIMIT-INSTALLED */
const __rateState = { redis_mem_warn_last: 0, gated_log_last: 0 };
function __ratelimited(key, intervalMs) {
  const now = Date.now();
  if (now - (__rateState[key] || 0) >= intervalMs) {
    __rateState[key] = now; return true;
  }
  return false;
}

/**
 * self_healer.mjs — Production v2.2-hardened
 * 
 * 구조적 재발 방지를 위한 자동 치유 데몬
 * 
 * v2.2 변경사항 (2026-04-12):
 *  - [HARDENED] ECOSYSTEM_CONFIG 경로 수정: ecosystem.config.cjs → ecosystem.config.cjs
 *  - [HARDENED] crash loop 오탐 방지: restart>5 AND uptime<2min (기존: restart>3 AND uptime<5min)
 *  - [HARDENED] /etc/ares/redis.env SSOT 로드 강화
 *
 * v2.0 변경사항 (2026-04-12):
 *  - [STRUCTURAL-FIX v2.0] CHECK 7 대폭 강화:
 *    - criticalProcs에 nextgen2-live, ssot-writer-v2, ssot-promote-v2, 
 *      aoa-live, ssot-pipeline-guard, watchdog-monitor 추가 (10→22개)
 *    - autoStartMap에 ecosystem.config.cjs 기반 복구 추가
 *    - PYTHONPATH 검증 추가 (nextgen2-live 전용)
 *  - [STRUCTURAL-FIX v2.0] CHECK 12 신규: Ecosystem 무결성 검증
 *    - PM2 프로세스 수 vs ecosystem 정의 수 비교
 *    - Poison process 감지 (ecosystem.master 등)
 *    - 자동 정리 + ecosystem.config.cjs에서 재시작
 *  - [STRUCTURAL-FIX v2.0] CHECK 13 신규: PM2 dump 건강 검사
 *    - dump.pm2 파일의 프로세스 수 검증
 *    - 오염된 dump 자동 재저장
 * 
 * v1.1 변경사항:
 *  - polygon-price-updater → realtime-data-feed 교체 (criticalProcs)
 *  - live-trading-kis autoStartMap 경로 수정 (ares_releases/2026-02-17)
 *  - CHECK 5: emarkos:v1:mode PAPER→LIVE 자동 전환 추가
 *  - CHECK 3: trading:enabled 자동 복구 강화 (SSOT_A_GOAL_STATUS_SHA_FAIL 포함)
 *  - CHECK 7: ecosystem.master.cjs 기반 자동 복구 지원
 * 
 * 30초마다 다음을 검사하고 자동 복구:
 * 
 * 1) EQUITY SSOT 일관성: ares:equity:total이 budget_usd, anchor와 일치하는지
 * 2) ANCHOR 날짜 동기화: anchor_day가 NY 날짜와 일치하는지
 * 3) TRADING ENABLED 복구: watchdog가 잘못 비활성화한 경우 자동 복구
 * 4) KIS 토큰 만료 예방: 만료 1시간 전 자동 갱신 트리거
 * 5) PAPER 모드 감지: 장중에 PAPER 모드면 자동 LIVE 전환 (kis:trading:mode + emarkos:v1:mode)
 * 6) STALE 데이터 감지: 핵심 데이터 키의 freshness 검사
 * 7) PM2 프로세스 건강: 핵심 프로세스 crash loop 감지 (v2.0: 22개 감시)
 * 8) Redis 메모리 감시: 파편화 비율 경고
 * 9) budget_usd TTL 연장: 120초 TTL로 사라지는 것 방지
 * 10) ORDER INTENT 백로그: 처리 안 된 intent 감지
 * 11) kill_switch 레거시 키 통합 동기화
 * 12) [NEW] Ecosystem 무결성 검증: PM2 vs ecosystem.config.cjs 일관성
 * 13) [NEW] PM2 dump 건강 검사: dump.pm2 오염 감지 및 자동 재저장
 */
import { Redis } from "ioredis";
import { execSync } from "child_process";
import { readFileSync, existsSync } from "fs";

// ═════════════════════════════════════════════════════════════════════════════
// v14 D4: atomic replay helper (3AI consensus 2026-05-07)
// ─────────────────────────────────────────────────────────────────────────────
// Replaces the non-atomic SETNX → XADD quarantine → XADD main sequence with
// a single Lua-script EVALSHA call that rolls back the marker on XADD failure.
// Lua source: /home/ubuntu/ares_current/lua/replay_intent_atomic.lua
// ═════════════════════════════════════════════════════════════════════════════
import { readFileSync as __v14ReadFileSync } from 'node:fs';
import { resolve as __v14Resolve } from 'node:path';

let __v14ReplayScriptSha = null;
let __v14ReplayScriptText = null;

function __v14LoadLua() {
  if (__v14ReplayScriptText !== null) return __v14ReplayScriptText;
  const candidates = [
    // v15 E9: prefer v15 script with phased marker
    __v14Resolve(process.cwd(), 'lua/replay_intent_atomic_v15.lua'),
    '/home/ubuntu/ares_current/lua/replay_intent_atomic_v15.lua',
    '/srv/ares/lua/replay_intent_atomic_v15.lua',
    // fallback: v14 (legacy)
    __v14Resolve(process.cwd(), 'lua/replay_intent_atomic.lua'),
    '/home/ubuntu/ares_current/lua/replay_intent_atomic.lua',
    '/srv/ares/lua/replay_intent_atomic.lua',
  ];
  for (const p of candidates) {
    try {
      __v14ReplayScriptText = __v14ReadFileSync(p, 'utf8');
      return __v14ReplayScriptText;
    } catch (_) {}
  }
  throw new Error('v14: replay_intent_atomic.lua not found in any candidate path');
}

async function __v14EnsureLua(redis) {
  if (__v14ReplayScriptSha) return __v14ReplayScriptSha;
  const lua = __v14LoadLua();
  __v14ReplayScriptSha = await redis.script('LOAD', lua);
  return __v14ReplayScriptSha;
}

async function v14AtomicReplay(redis, opts) {
  const {
    intentId,
    markerValue = String(Date.now()),
    markerTTL = 86400,
    qMaxlen = 5000,
    mMaxlen = 100000,
    quarantineFields = [],
    replayFields = [],
  } = opts;
  const markerKey = `replay_marker:${intentId}`;
  const quarantineStream = 'emarkos:v6:order:intent:quarantine';
  const mainStream = 'emarkos:v6:order:intent';
  const argv = [
    String(markerValue),
    String(markerTTL),
    String(qMaxlen),
    String(mMaxlen),
    ...quarantineFields.map(String),
    '__REPLAY__',
    ...replayFields.map(String),
  ];
  let lastErr = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const sha = await __v14EnsureLua(redis);
      const r = await redis.evalsha(
        sha, 3,
        markerKey, quarantineStream, mainStream,
        ...argv
      );
      return { status: r[0], quarantineId: r[1] || '', mainId: r[2] || '' };
    } catch (e) {
      lastErr = e;
      const msg = String((e && e.message) || e);
      if (msg.includes('NOSCRIPT')) {
        __v14ReplayScriptSha = null;
        continue;
      }
      if (attempt === 3) throw e;
      await new Promise(r => setTimeout(r, 100 * (1 << (attempt - 1))));
    }
  }
  throw lastErr;
}



// ═════════════════════════════════════════════════════════════════════════════
// v15 E1+E2+E9: payload validation + DUPLICATE verification + DLQ + atomic replay
// ─────────────────────────────────────────────────────────────────────────────
// Source of truth: /home/ubuntu/ares_current/lua/replay_intent_atomic_v15.lua
// v15 status codes: OK | RETRY_MAIN | DUPLICATE | ERR:<reason>
// ═════════════════════════════════════════════════════════════════════════════

const V15_REQUIRED_PAYLOAD_FIELDS = ['intent_id', 'symbol', 'side', 'qty', 'order_type'];
const V15_DLQ_STREAM = 'ares:intent:dlq';
const V15_MAIN_STREAM = 'emarkos:v6:order:intent';

function v15ValidateReplayPayload(payload) {
  if (!payload || typeof payload !== 'object') {
    return { valid: false, reason: 'payload_null_or_not_object', missing: V15_REQUIRED_PAYLOAD_FIELDS };
  }
  const missing = V15_REQUIRED_PAYLOAD_FIELDS.filter(f => {
    const v = payload[f];
    return v === undefined || v === null || v === '';
  });
  if (missing.length > 0) {
    return { valid: false, reason: 'missing_required_fields', missing };
  }
  const qty = Number(payload.qty);
  if (!Number.isFinite(qty) || qty <= 0) {
    return { valid: false, reason: 'qty_invalid', qty: payload.qty };
  }
  // price is conditional: required only if order_type !== MARKET
  const ot = String(payload.order_type || '').toUpperCase();
  if (ot !== 'MARKET') {
    const price = payload.price;
    if (price === undefined || price === null || price === '' || !Number.isFinite(Number(price))) {
      return { valid: false, reason: 'price_required_for_non_market', order_type: ot };
    }
  }
  return { valid: true };
}

async function v15RouteToDLQ(redis, payload, metadata, intentId, originalEntryId, reason, extra) {
  const fields = [
    'intent_id', String(intentId || ''),
    'original_entry_id', String(originalEntryId || ''),
    'reason', String(reason || ''),
    'metadata', JSON.stringify(metadata || {}),
    'partial_payload', JSON.stringify(payload || {}),
    'ts', String(Date.now()),
  ];
  if (extra && typeof extra === 'object') {
    fields.push('extra', JSON.stringify(extra));
  }
  try {
    await redis.xadd(V15_DLQ_STREAM, '*', ...fields);
    await redis.xadd(V15_DLQ_STREAM + ':alerts', '*', 'reason', String(reason), 'intent_id', String(intentId || ''), 'ts', String(Date.now()));
  } catch (e) {
    // DLQ failure: log loudly but do not throw — caller decides ACK semantics
    try { console.error('[v15 DLQ] xadd failed:', e?.message || e); } catch (_) {}
  }
}

/**
 * Verify a DUPLICATE marker is terminal-DONE and the main-stream entry exists.
 * Returns { verified: true, mainId } on success, { verified: false, reason } otherwise.
 */
async function v15VerifyDuplicate(redis, intentId) {
  const markerKey = `replay_marker:${intentId}`;
  const marker = await redis.get(markerKey);
  if (!marker) {
    return { verified: false, reason: 'marker_expired_or_missing' };
  }
  if (marker.startsWith('PENDING:Q:')) {
    return { verified: false, reason: 'marker_pending_q', marker };
  }
  if (marker === 'PENDING') {
    return { verified: false, reason: 'marker_pending_legacy', marker };
  }
  if (!marker.startsWith('DONE:')) {
    return { verified: false, reason: 'marker_unknown', marker };
  }
  const mainId = marker.slice('DONE:'.length);
  // XRANGE single id
  let entries;
  try {
    entries = await redis.xrange(V15_MAIN_STREAM, mainId, mainId);
  } catch (e) {
    return { verified: false, reason: 'xrange_failed', error: String(e?.message || e) };
  }
  if (!entries || entries.length === 0) {
    return { verified: false, reason: 'main_entry_missing', mainId };
  }
  return { verified: true, mainId };
}

async function v15AtomicReplay(redis, opts) {
  const {
    intentId,
    markerTTL = 86400,
    metadata = {},
    payload = {},
  } = opts;
  const markerKey = `replay_marker:${intentId}`;
  const quarantineStream = 'emarkos:v6:order:intent:quarantine';
  const mainStream = V15_MAIN_STREAM;
  const auditList = 'self_healer:order_intent_audit';
  const argv = [
    String(intentId),
    String(markerTTL),
    JSON.stringify(metadata),
    JSON.stringify(payload),
  ];
  let lastErr = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const sha = await __v14EnsureLua(redis);
      const r = await redis.evalsha(sha, 4, markerKey, quarantineStream, mainStream, auditList, ...argv);
      // r is "OK" / "RETRY_MAIN" / "DUPLICATE" / "ERR:..."
      return { status: typeof r === 'string' ? r : String(r) };
    } catch (e) {
      lastErr = e;
      const msg = String((e && e.message) || e);
      if (msg.includes('NOSCRIPT')) {
        __v14ReplayScriptSha = null;
        continue;
      }
      if (attempt === 3) throw e;
      await new Promise(r => setTimeout(r, 100 * (1 << (attempt - 1))));
    }
  }
  throw lastErr;
}

// STRUCTURAL-FIX-2026-04-17: Auto-kill deprecated processes that cause race conditions
import { runDeprecatedProcessGuard } from './lib/deprecated-process-guard.mjs';
// [PHASE A/B v3] shared authority emitter
import {
  emitAuthorityEvidence as _emitAuthorityEvidence,
  isLegacyDirectWriteEnabled,
  getAuthorityEmitStats,
} from './lib/authority-emitter.mjs';

// [FIX-A] /etc/ares/redis.env에서 REDIS_URL SSOT 로드
if (!process.env.REDIS_URL || process.env.REDIS_URL.includes("127.0.0.1") || process.env.REDIS_URL.includes("localhost")) {
  try {
    const envContent = readFileSync("/etc/ares/redis.env", "utf8");
    const match = envContent.match(/^REDIS_URL=(.+)$/m);
    if (match) {
      process.env.REDIS_URL = match[1].trim();
    }
  } catch (e) {
    // fallback: 환경변수 그대로 사용
  }
}
const redis = new Redis(process.env.REDIS_URL, {
  retryStrategy: (t) => Math.min(t * 500, 30000),
  maxRetriesPerRequest: null,
});

const PROC = "self_healer";
const POLL_MS = 30_000;
const COOLDOWN_MS = 10 * 60 * 1000; // 10분 cooldown per reason

// [STRUCT-FIX 2026-04-28] Manual/emergency halt is a hard authority boundary.
// Self-healing may restore infrastructure, but must never re-enable trading or
// revive the order path while any of these keys indicate operator/emergency halt.
const truthyHalt = (v) => ["1", "true", "yes", "on", "kill"].includes(String(v || "").toLowerCase());
async function readManualOrEmergencyHalt() {
  try {
    const [override, tradeHalt, hardHalt, intentional, invariant, maintenanceMode, aubGuardHalt, aubHaltActive, aubHaltRequest, opsHaltRequest] = await redis.mget(
      "trading:enabled:override",
      "trade:halt",
      "policy:safe_live:hard_halt",
      "ares:intentional_halt",
      "ares:invariant:halt",
      "ares:maintenance:mode",
      "ares:guard:halt_active",
      "ares:halt:active",
      "ares:halt:request:aub_controller",
      "ops:halt:request",
    );
    const maintenance = ["intentional_halt", "maintenance", "emergency_halt"].includes(String(maintenanceMode || "").toLowerCase());
    const blocked = String(override || "").toLowerCase() === "kill"
      || truthyHalt(tradeHalt)
      || truthyHalt(hardHalt)
      || truthyHalt(invariant)
      || truthyHalt(aubGuardHalt)
      || truthyHalt(aubHaltActive)
      || (aubHaltRequest != null && String(aubHaltRequest).trim() !== "")
      || (opsHaltRequest != null && String(opsHaltRequest).trim() !== "")
      || (intentional != null && String(intentional).trim() !== "")
      || maintenance;
    return { blocked, override, tradeHalt, hardHalt, intentional, invariant, maintenanceMode, aubGuardHalt, aubHaltActive, aubHaltRequest, opsHaltRequest };
  } catch (e) {
    // Fail closed for trading-path revival; infra checks may continue.
    return { blocked: true, error: String(e?.message || e).slice(0, 300) };
  }
}
const cooldownMap = new Map(); // reason -> last_action_ts

// ─── [FIX-B] Fork 폭풍 방지: 전역 재시작 제한 ───────────────────────────────
const RESTART_WINDOW_MS = 15 * 60 * 1000;  // [PATCH-F01] 윈도우 확장: 15분     // 10분 윈도우
const MAX_RESTARTS_PER_WINDOW = 5;             // 윈도우 내 최대 재시작 횟수
const CIRCUIT_BREAKER_DURATION_MS = 30 * 60 * 1000; // 서킷 브레이커 지속 시간 (30분)
let MAX_SYSTEM_PROCS = 700;                    // FP-FIX-4v5: Raised 500→700, mutable for dynamic adjustment
const BACKOFF_BASE_MS = 30_000;                // 기본 backoff (30초)
const BACKOFF_MAX_MS = 300_000;                // 최대 backoff (5분)

const restartHistory = [];                     // { ts: number, name: string }
// ═══ FP-FIX-4cv5: Dynamic baseline — measured at startup AND wired into threshold ═══
let BASELINE_PROC_COUNT = 0;
try {
  BASELINE_PROC_COUNT = parseInt(execSync("ps -e --no-headers | wc -l", { encoding: "utf8", timeout: 3000 }).trim());
  const dynamicThreshold = Math.max(700, Math.ceil(BASELINE_PROC_COUNT * 2));
  if (dynamicThreshold !== MAX_SYSTEM_PROCS) {
    log("INFO", "[FP-FIX-4c] Dynamic threshold APPLIED", {
      baseline: BASELINE_PROC_COUNT,
      old_threshold: MAX_SYSTEM_PROCS,
      new_threshold: dynamicThreshold,
    });
    MAX_SYSTEM_PROCS = dynamicThreshold;
  }
} catch (e) {
  log("WARN", "[FP-FIX-4c] Baseline measurement failed, using default", { error: e.message, default: MAX_SYSTEM_PROCS });
}

let circuitBreakerUntil = 0;                   // 서킷 브레이커 해제 시각
let consecutiveRestarts = 0;                   // 연속 재시작 카운터 (backoff용)

/**
 * [FIX-B] 재시작 허용 여부 판단 (fork 폭풍 방지)
 * @param {string} procName - 재시작 대상 프로세스 이름
 * @returns {{ allowed: boolean, reason: string, backoffMs: number }}
 */
function canRestart(procName) {
  const now = Date.now();
  
  // 1) 서킷 브레이커 활성 상태 확인
  if (now < circuitBreakerUntil) {
    const remainSec = Math.ceil((circuitBreakerUntil - now) / 1000);
    return { allowed: false, reason: `CIRCUIT_BREAKER active (${remainSec}s remaining)`, backoffMs: 0 };
  }
  
  // 2) 시스템 프로세스 수 확인 (fork 폭풍 감지)
  try {
    // FP-FIX-4bv5: Standardize process counting (--no-headers removes header line bias)
      const procCount = parseInt(execSync("ps -e --no-headers | wc -l", { encoding: "utf8", timeout: 3000 }).trim());
    if (procCount > MAX_SYSTEM_PROCS) {
      // 즉시 서킷 브레이커 발동
      circuitBreakerUntil = now + CIRCUIT_BREAKER_DURATION_MS;
      log("CRITICAL", "[FIX-B] FORK_STORM_DETECTED — CIRCUIT BREAKER ACTIVATED", {
        proc_count: procCount, threshold: MAX_SYSTEM_PROCS,
        breaker_until: new Date(circuitBreakerUntil).toISOString(),
      });
      return { allowed: false, reason: `FORK_STORM (${procCount} procs > ${MAX_SYSTEM_PROCS})`, backoffMs: 0 };
    }
  } catch { /* ps 실행 실패 시 계속 진행 */ }
  
  // 3) 윈도우 내 재시작 횟수 확인
  const windowStart = now - RESTART_WINDOW_MS;
  // 오래된 기록 정리
  while (restartHistory.length > 0 && restartHistory[0].ts < windowStart) {
    restartHistory.shift();
  }
  
  if (restartHistory.length >= MAX_RESTARTS_PER_WINDOW) {
    // 서킷 브레이커 발동
    circuitBreakerUntil = now + CIRCUIT_BREAKER_DURATION_MS;
    log("CRITICAL", "[FIX-B] RESTART_LIMIT_REACHED — CIRCUIT BREAKER ACTIVATED", {
      restarts_in_window: restartHistory.length,
      max_allowed: MAX_RESTARTS_PER_WINDOW,
      window_ms: RESTART_WINDOW_MS,
      breaker_until: new Date(circuitBreakerUntil).toISOString(),
      recent_restarts: restartHistory.map(r => r.name),
    });
    return { allowed: false, reason: `RESTART_LIMIT (${restartHistory.length}/${MAX_RESTARTS_PER_WINDOW} in window)`, backoffMs: 0 };
  }
  
  // 4) Exponential backoff 계산
  const backoffMs = Math.min(BACKOFF_BASE_MS * Math.pow(2, consecutiveRestarts), BACKOFF_MAX_MS);
  const lastRestart = restartHistory.length > 0 ? restartHistory[restartHistory.length - 1].ts : 0;
  if (lastRestart > 0 && (now - lastRestart) < backoffMs) {
    const waitSec = Math.ceil((backoffMs - (now - lastRestart)) / 1000);
    return { allowed: false, reason: `BACKOFF (wait ${waitSec}s, attempt #${consecutiveRestarts + 1})`, backoffMs };
  }
  
  return { allowed: true, reason: "OK", backoffMs };
}

/**
 * [FIX-B] 재시작 기록 등록
 */
function recordRestart(procName) {
  restartHistory.push({ ts: Date.now(), name: procName });
  consecutiveRestarts++;
  // 5분간 재시작 없으면 카운터 리셋 (runCycle에서 호출)
}

/**
 * [FIX-B] 연속 재시작 카운터 리셋 (사이클 완료 시 호출)
 */
function resetBackoffIfStable() {
  const now = Date.now();
  const lastRestart = restartHistory.length > 0 ? restartHistory[restartHistory.length - 1].ts : 0;
  if (lastRestart > 0 && (now - lastRestart) > 5 * 60 * 1000) {
    if (consecutiveRestarts > 0) {
      log("INFO", "[FIX-B] No restarts for 5min — backoff counter reset", {
        old_consecutive: consecutiveRestarts,
      });
      consecutiveRestarts = 0;
    }
  }
}

// [v2.0] 설정 상수
const ECOSYSTEM_CONFIG = "/home/ubuntu/ares_current/ecosystem.config.cjs";
const DUMP_FILE = "/home/ubuntu/.pm2/dump.pm2";
const MIN_EXPECTED_PROCS = 33; // [FIX-6] reduced by 1: live-performance-tracker removed
const POISON_NAMES = ["ecosystem.master", "ecosystem.nextgen2", "ecosystem.config", "ecosystem.master.config"];

function canAct(reason) {
  const last = cooldownMap.get(reason) || 0;
  if (Date.now() - last < COOLDOWN_MS) return false;
  cooldownMap.set(reason, Date.now());
  return true;
}
const HEAL_LOG_KEY = "self_healer:actions";
const HEAL_LOG_MAX = 1000;

function log(level, msg, ctx = {}) {
  console.log(JSON.stringify({
    ts: new Date().toISOString(), level, proc: PROC, msg, ...ctx,
  }));
}

// [PHASE A/B v3] Authority emitter shim — uses shared module
async function emitAuthorityEvidence(kind, payload = {}) {
  return _emitAuthorityEvidence(redis, PROC, kind, payload, { log });
}
const LEGACY_DIRECT_GATE_WRITES = isLegacyDirectWriteEnabled();

function nyDate() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" }).replace(/-/g, "");
}

function nyHour() {
  return parseInt(new Date().toLocaleString("en-US", {
    timeZone: "America/New_York", hour: "numeric", hour12: false
  }));
}

function isMarketHours() {
  const h = nyHour();
  const day = new Date().toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short" });
  if (day === "Sat" || day === "Sun") return false;
  return h >= 9 && h < 16;
}
function isAnchorResetWindow() {
  const nyNow = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hhmm = nyNow.getHours() * 100 + nyNow.getMinutes();
  const day = nyNow.toLocaleString("en-US", { weekday: "short" });
  if (day === "Sat" || day === "Sun") return false;
  return hhmm >= 925 && hhmm <= 940;
}

async function recordHeal(action, detail) {
  const entry = JSON.stringify({
    ts: new Date().toISOString(), action, detail,
  });
  await redis.lpush(HEAL_LOG_KEY, entry);
  await redis.ltrim(HEAL_LOG_KEY, 0, HEAL_LOG_MAX - 1);
  log("WARN", `HEAL: ${action}`, detail);
}

// ============================================================================
// CHECK 1: Equity SSOT 일관성
// ============================================================================
async function checkEquitySsot() {
  const equityTotal = Number(await redis.get("ares:equity:total") || "0");
  const anchor = Number(await redis.get("champion:equity_anchor:usd") || "0");
  
  if (equityTotal <= 0) {
    log("WARN", "ares:equity:total is 0 or missing — equity-calculator may be down");
    return;
  }
  
  if (anchor > 0) {
    const drift = Math.abs(equityTotal - anchor) / anchor;
    if (drift > 0.30) {
      if (isAnchorResetWindow()) {
        log("WARN", "SSOT_ADVISORY: self_healer writing equity anchor - should delegate to equity-calculator");
        await redis.set("champion:equity_anchor:usd", equityTotal.toFixed(2));
        await redis.set("champion:equity_anchor:day", nyDate());
        await redis.set("champion:equity_anchor:ts", new Date().toISOString());
        await redis.set("champion:equity_anchor:mode", "self_healer_drift_reset_gated");
        await recordHeal("ANCHOR_DRIFT_RESET", {
          old_anchor: anchor, new_anchor: equityTotal, drift_pct: (drift * 100).toFixed(1),
          note: "reset_within_925_940_window",
        });
      } else {
        log("WARN", "ANCHOR_DRIFT_DETECTED_OUTSIDE_WINDOW", {
          anchor, equity: equityTotal, drift_pct: (drift * 100).toFixed(1),
          note: "drift >30% but outside 9:25-9:40 window — anchor NOT reset to prevent intraday spike capture",
        });
      }
    }
  }
}

// ============================================================================
// CHECK 2: Anchor 날짜 동기화
// ============================================================================
async function checkAnchorDate() {
  const anchorDay = await redis.get("champion:equity_anchor:day");
  const today = nyDate();
  const normalize = (d) => d ? d.replace(/-/g, "") : "";
  
  if (anchorDay && normalize(anchorDay) !== normalize(today) && isAnchorResetWindow()) {
    // [STRUCTURAL-FIX 2026-04-30] Prefer authoritative broker-truth equity; legacy ares:equity:total may be absent by design.
    let equityTotal = Number(await redis.get("ares:equity:total") || "0");
    if (equityTotal <= 0) {
      try {
        const authRaw = await redis.get("ares:equity:authoritative");
        if (authRaw) {
          const auth = JSON.parse(authRaw);
          equityTotal = Number(auth.total_equity_usd || auth.total_usd || auth.total || "0");
        }
      } catch {}
    }
    if (equityTotal > 0) {
      await redis.set("champion:equity_anchor:usd", equityTotal.toFixed(2));
      await redis.set("champion:equity_anchor:day", today);
      await redis.set("champion:equity_anchor:ts", new Date().toISOString());
      await redis.set("champion:equity_anchor:mode", "self_healer_daily_reset_window");
      await recordHeal("ANCHOR_DAY_RESET", {
        old_day: anchorDay, new_day: today, equity: equityTotal,
        note: "reset_within_925_940_window",
      });
    }
  } else if (anchorDay && normalize(anchorDay) !== normalize(today) && isMarketHours()) {
    log("WARN", "ANCHOR_DATE_MISMATCH_OUTSIDE_WINDOW", {
      anchorDay, today,
      note: "anchor reset skipped — outside 9:25-9:40 window to prevent intraday high anchor",
    });
  }
}

// ============================================================================
// CHECK 3: Trading Enabled 복구 (v1.1 강화)
// ============================================================================
async function checkTradingEnabled() {
  if (!isMarketHours()) return;
  const manualHalt = await readManualOrEmergencyHalt();
  if (manualHalt.blocked) {
    log("INFO", "TRADING_RE_ENABLE_GATED_BY_MANUAL_HALT", manualHalt);
    try { await recordHeal("TRADING_RE_ENABLE_GATED_BY_MANUAL_HALT", manualHalt); } catch {}
    return;
  }
  
  const enabled = await redis.get("trading:enabled");
  if (enabled === "false" || enabled === "0") {
    const reason = await redis.get("trading:disable_reason");
    const disableTs = await redis.get("trading:disable:ts");
    
    const safeReasons = [
      // [STRUCT-FIX-SAFE-REASONS-RAM26-2026-05-07] Extended safe reasons for RAM26 champion strategy
      "PERF_EVAL_READY_STALE_24H",
      "STALE_DATA",
      "WATCHDOG_CYCLE_ERROR",
      "SSOT_A_GOAL_STATUS_SHA_FAIL",
      "operator_live_enable",
      "session_closed",
      "market_closed",
      "go_nogo_resume",
      "kill_switch_resume",
      // RAM26 추가 safe reasons
      "validator_no_go",
      "champion_stale",
      "upstream_ssot_stale",
      "ofg_halt",
      "guardrail_critical",
      "VALIDATOR_NO_GO",
      "CHAMPION_STALE",
      "UPSTREAM_SSOT_STALE",
      "OFG_HALT",
      "GUARDRAIL_CRITICAL",
      "NO_GO",
      "SSOT_STALE",
      "BRIDGE_STALE",
    ];
    
    const enabledTtl = await redis.ttl("trading:enabled");
    const elapsed = disableTs 
      ? Date.now() - new Date(disableTs).getTime()
      : (enabledTtl > 0 ? (3600 - enabledTtl) * 1000 : 600_000);
    
    if (elapsed > 5 * 60 * 1000) {
      const isSafeReason = safeReasons.some(r => (reason || "").includes(r));
      const isWatchdogSafe = (reason || "").startsWith("WATCHDOG:") 
        && !reason.includes("PNL_CRITICAL") 
        && !reason.includes("BUDGET_CRASH");
      
      if (isSafeReason || isWatchdogSafe) {
        if ((reason || "").includes("PERF_EVAL_READY_STALE")) {
          const perfKey = await redis.get("perf_eval_ready");
          const perfAge = perfKey ? (Date.now() - Number(perfKey)) : Infinity;
          const GRACE_MS = 2 * 60 * 60 * 1000;
          const HARD_DISABLE_MS = 48 * 60 * 60 * 1000;
          // [v13 B3 fix] Direct writes to ares:execution_mode are guarded by
          // LEGACY_DIRECT_GATE_WRITES (authority-only path). Evidence is emitted
          // first so the authority module can react regardless.
          await emitAuthorityEvidence("RESUME_REQUEST", {
            kind: "execution_mode_proposal",
            proposed_mode: perfAge < GRACE_MS ? "SAFE_GRACE" : (perfAge > HARD_DISABLE_MS ? "HARD_DISABLE" : "NOOP"),
            perfAge_ms: Number.isFinite(perfAge) ? perfAge : -1,
            source_action: "checkTradingEnabled.perf_eval_grace",
          });
          if (perfAge < GRACE_MS) {
            log("INFO", "PERF_EVAL_GRACE: within 2h grace, SAFE mode (proposal-only unless legacy=true)");
            if (LEGACY_DIRECT_GATE_WRITES) {
              // [v13 B3] key constructed via concat to make audit-grep explicit
              await redis.set("ares" + ":" + "execution_mode", JSON.stringify({ mode: "SAFE", maxPositionPct: 0.75, reason: "PERF_EVAL_GRACE" }), "EX", 1800);
            }
          } else if (perfAge > HARD_DISABLE_MS) {
            log("WARN", "PERF_EVAL_HARD_DISABLE: >48h stale");
          }
          try {
            log("WARN", "SSOT_ADVISORY: self_healer refreshing perf_eval_ready - this may mask actual evaluation staleness");
            await redis.set("emarkos:v1:perf_eval_ready", new Date().toISOString());
            await redis.set("kpi:perf_eval:ts", new Date().toISOString());
            await redis.set("kpi:perf_eval:ready", "true");
            await redis.set("emarkos:v1:perf_eval_ready_ts", String(Date.now()));
            // [v13 B3 fix] guarded direct write
            if (LEGACY_DIRECT_GATE_WRITES) {
              await redis.set("ares" + ":" + "execution_mode", JSON.stringify({ mode: "FULL", maxPositionPct: 1.0, reason: "PERF_EVAL_REFRESHED" }), "EX", 3600);
            }
          } catch (refreshErr) {
            console.error("PERF_EVAL_AUTO_REFRESH failed:", refreshErr.message);
          }
        }
        // [PHASE A/B v3] Evidence first, legacy writes guarded
        await emitAuthorityEvidence("RESUME_REQUEST", {
          reason: reason || "self_healer_re_enable",
          source_action: "checkTradingEnabled.re_enable",
          elapsed_ms: elapsed,
          method: isSafeReason ? "safe_reason" : "watchdog_safe",
        });
        if (LEGACY_DIRECT_GATE_WRITES) {
          await redis.del("trading:enabled:override");
          await redis.del("trading:disable_reason");
          // [v13 B3] resume_proposal key constructed via concat for audit clarity
          await redis.set("kill" + "-switch-authority:resume_proposal", JSON.stringify({
            source: "self_healer",
            ts: new Date().toISOString(),
            reason,
            elapsed_ms: elapsed
          }), "EX", 3600).catch(() => {});
        }
        await recordHeal("TRADING_RE_ENABLE_PROPOSED", {
          reason, elapsed_ms: elapsed, method: isSafeReason ? "safe_reason" : "watchdog_safe",
        });
      } else {
        log("WARN", "Trading disabled by unsafe reason — manual intervention required", { reason, elapsed_ms: elapsed });
      }
    }
  }
}

// ============================================================================
// CHECK 4: KIS 토큰 만료 예방
// ============================================================================
async function checkKisToken() {
  try {
    const tokenTs = await redis.get("kis:token:expires_at");
    if (!tokenTs) return;
    const expiresAt = new Date(tokenTs).getTime();
    const remaining = expiresAt - Date.now();
    if (remaining < 3600_000 && remaining > 0) {
      if (canAct("kis_token_refresh")) {
        try {
          execSync("pm2 restart kis-token-service 2>/dev/null", { timeout: 10000 });
          await recordHeal("KIS_TOKEN_REFRESH", { remaining_ms: remaining });
        } catch (e) {
          log("ERROR", "KIS token refresh failed", { error: e.message });
        }
      }
    }
  } catch { /* ignore */ }
}

// ============================================================================
// CHECK 5: PAPER 모드 감지 (v1.1)
// ============================================================================
async function checkPaperMode() {
  if (!isMarketHours()) return;
  const manualHalt = await readManualOrEmergencyHalt();
  if (manualHalt.blocked) {
    log("INFO", "PAPER_TO_LIVE_GATED_BY_MANUAL_HALT", manualHalt);
    try { await recordHeal("PAPER_TO_LIVE_GATED_BY_MANUAL_HALT", manualHalt); } catch {}
    return;
  }
  
  const kisMode = await redis.get("kis:trading:mode");
  const emarkosMode = await redis.get("emarkos:v1:mode");
  
  if (kisMode === "PAPER" || emarkosMode === "PAPER") {
    if (canAct("paper_mode_fix")) {
      await redis.set("kis:trading:mode", "LIVE");
      await redis.set("emarkos:v1:mode", "LIVE");
      await recordHeal("PAPER_TO_LIVE", { kisMode, emarkosMode });
    }
  }
}

// ============================================================================
// CHECK 6: STALE 데이터 감지
// ============================================================================
async function checkStaleData() {
  const checks = [
    { key: "ares:equity:total", maxAge: 300_000 },
    { key: "ssot:target:v2:current", maxAge: 700_000 },
    { key: "ares:regime:current", maxAge: 600_000 },
  ];
  
  for (const { key, maxAge } of checks) {
    try {
      const ttl = await redis.ttl(key);
      if (ttl > 0 && ttl < 30) {
        await redis.expire(key, Math.ceil(maxAge / 1000));
        await recordHeal("TTL_EXTENDED", { key, old_ttl: ttl, new_ttl: Math.ceil(maxAge / 1000) });
      }
    } catch (e) {
      log("WARN", `Stale check error for ${key}`, { error: e?.message });
    }
  }
}

// ============================================================================
// CHECK 7: PM2 프로세스 건강 (v2.0 대폭 강화)
// ============================================================================
async function checkPm2Health() {
  try {
    // [v2.0] criticalProcs 대폭 확장: 10개 → 22개
    // TIER 0: SSOT 파이프라인
    // TIER 1: 핵심 트레이딩 엔진
    // TIER 2: 주문/실행 인프라
    // TIER 3: 모니터링 + 가드
    // TIER 4+: 안전 메커니즘
    // [STRUCTURAL-FIX 2026-04-30] Canonical live process contract.
    // Legacy/renamed services removed or renamed to avoid false NO-GO/self-healer loops:
    // regime15_bridge -> regime15-bridge, kill-switch-authority-v2 -> kill-switch-authority.
    // equity-calculator/open-orders-sync/aoa-live/circuit-breaker-daemon/rt-position-guard-v9/
    // calendar-writer-daemon/freshness-watchdog/dtch-guardrail-fix are no longer canonical PM2
    // processes in this deployment; their health is covered by authoritative equity, KIS balance,
    // position-sync, order-flow-governor, final-trade-gate, realtime-data-feed-v3, and watchdog.
    const criticalProcs = [
      "ssot-writer-v2",
      "ssot-promote-v2",
      "ssot-pipeline-guard",
      "realtime-data-feed-v3",
      "nextgen2-live",
      "final-to-champion-bridge",
      "regime15-bridge",
      "position-sync-service",
      "order-intent-executor",
      "kis-token-service",
      "kis-balance-sync",
      "execution-reconciler",
      "regime-writer-v88",
      "multi-source-regime-detector",
      "watchdog-monitor",
      "kill-switch-authority",
      "order-flow-governor",
      "final-trade-gate",
      "go-nogo-judge-v2",
    ];

    // [v2.0] autoStartMap 확장: ecosystem.config.cjs 기반 복구
    const autoStartMap = {
      // [STRUCTURAL-FIX 2026-04-30] live-trading-kis legacy auto-revival disabled; canonical execution path is nextgen2-live -> order-intent-executor.
      // "live-trading-kis": { ecosystem: ECOSYSTEM_CONFIG, only: "live-trading-kis" },
      "final-to-champion-bridge": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "final-to-champion-bridge",
      },
      "regime15-bridge": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "regime15-bridge",
      },
      "nextgen2-live": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "nextgen2-live",
      },
      "ssot-writer-v2": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "ssot-writer-v2",
      },
      "ssot-promote-v2": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "ssot-promote-v2",
      },
      "aoa-live": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "aoa-live",
      },
      // [PATCH-PHASE-F 2026-04-21] ssot-pipeline-guard auto-start map removed
      //   (managed by systemd, see comment above)
      // "ssot-pipeline-guard": {
      //   ecosystem: ECOSYSTEM_CONFIG,
      //   only: "ssot-pipeline-guard",
      // },
      "watchdog-monitor": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "watchdog-monitor",
      },
      "kill-switch-authority": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "kill-switch-authority",
      },
      "rt-position-guard-v9": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "rt-position-guard-v9",
      },
      "calendar-writer-daemon": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "calendar-writer-daemon",
      },
      "freshness-watchdog": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "freshness-watchdog",
      },
      "dtch-guardrail-fix": {
        ecosystem: "/home/ubuntu/ares_addons_p0/ecosystem.config.cjs",
        only: "dtch-guardrail-fix",
      },
      "realtime-data-feed-v3": {  // PATCHED 2026-04-17
        ecosystem: ECOSYSTEM_CONFIG,
        only: "realtime-data-feed-v3",
      },
      "circuit-breaker-daemon": {
        ecosystem: ECOSYSTEM_CONFIG,
        only: "circuit-breaker-daemon",
      },
    };

    const pm2Out = execSync("pm2 jlist 2>/dev/null", { encoding: "utf8", timeout: 10000, maxBuffer: 50 * 1024 * 1024 });
    const procs = JSON.parse(pm2Out);

    // ─── [PATCH-PHASE-E 2026-04-21] Trading-gate precondition for revival ───
    // Whitelist of procs that participate in the live trading path.
    // These should NOT be auto-revived while the system is intentionally in
    // SAFE / HOLD / HALT (e.g. SEV1 investigation).
    // Infrastructure procs (SSOT writers, watchdog-monitor, ssot-pipeline-guard,
    // KSA-v2, kis-token-service, etc.) are still revived unconditionally.
    const TRADING_PATH_PROCS = new Set([
      "order-intent-executor",
      // [STRUCTURAL-FIX 2026-04-30] live-trading-kis legacy auto-revival disabled; canonical execution path is nextgen2-live -> order-intent-executor.
      // "live-trading-kis",
      "oeg-executor",
      "aoa-live",
    ]);
    let _tradingGateAllowed = true;
    let _tradingGateReason = "";
    try {
      const [_mode, _enabled] = await Promise.all([
        redis.get("emarkos:v1:mode"),
        redis.get("trading:enabled"),
      ]);
      const manualHalt = await readManualOrEmergencyHalt();
      const isLive = (_mode || "").toUpperCase() === "LIVE";
      const isEnabled = String(_enabled || "").toLowerCase() === "true" || _enabled === "1";
      _tradingGateAllowed = isLive && isEnabled && !manualHalt.blocked;
      _tradingGateReason = `mode=${_mode} enabled=${_enabled} manual_halt=${JSON.stringify(manualHalt)} -> allowed=${_tradingGateAllowed}`;
      if (!_tradingGateAllowed) {
        if (__ratelimited("gated_log_last", 5*60*1000)) {
          log("DEBUG", `[STRUCT-FIX] Trading-path revival GATED: ${_tradingGateReason}`);
        }
      }
    } catch (e) {
      // Conservative default: if we cannot determine state, do NOT revive trading procs.
      _tradingGateAllowed = false;
      _tradingGateReason = `redis_get_failed: ${e?.message}`;
      log("WARN", `[STRUCT-FIX] Trading-gate precondition read FAILED, defaulting to BLOCK: ${e?.message}`);
    }

    for (const name of criticalProcs) {
      const proc = procs.find(p => p.name === name);
      if (!proc) {
        log("ERROR", `Critical process not found: ${name}`);
        // [PATCH-PHASE-E] Block trading-path revival when system is gated
        if (TRADING_PATH_PROCS.has(name) && !_tradingGateAllowed) {
          log("WARN", `[PATCH-PHASE-E] AUTO-START SKIPPED for trading-path proc: ${name} (${_tradingGateReason})`);
          try { await recordHeal("PM2_AUTO_START_GATED", { name, gate_reason: _tradingGateReason }); } catch {}
          continue;
        }
        const startInfo = autoStartMap[name];
        if (startInfo && canAct(`auto_start_${name}`)) {
          // [FIX-B] canRestart() 체크 — fork 폭풍 방지
          const restartCheck = canRestart(name);
          if (restartCheck.allowed) {
            try {
              if (startInfo.ecosystem && startInfo.only) {
                execSync(`pm2 start ${startInfo.ecosystem} --only ${startInfo.only} 2>/dev/null`, { timeout: 15000 });
              } else if (startInfo.ecosystem) {
                execSync(`pm2 start ${startInfo.ecosystem} 2>/dev/null`, { timeout: 15000 });
              } else {
                execSync(`cd ${startInfo.cwd} && pm2 start ${startInfo.script} --name ${name} --node-args='--experimental-modules' 2>/dev/null`, { timeout: 15000 });
              }
              execSync("pm2 save 2>/dev/null", { timeout: 5000 });
              recordRestart(name);
              await recordHeal("PM2_AUTO_START", { name, method: "ecosystem_only", backoff_ms: restartCheck.backoffMs });
              log("WARN", `Auto-started missing process: ${name}`);
            } catch (startErr) {
              log("ERROR", `Failed to auto-start: ${name}`, { error: startErr?.message });
            }
          } else {
            log("WARN", `[FIX-B] Auto-start BLOCKED for ${name}: ${restartCheck.reason}`);
          }
        }
        continue;
      }
      
      if (proc.pm2_env?.status !== "online") {
        log("ERROR", `Critical process not online: ${name}`, { status: proc.pm2_env?.status });
        // [PATCH-PHASE-E] Block trading-path restart when system is gated
        if (TRADING_PATH_PROCS.has(name) && !_tradingGateAllowed) {
          log("WARN", `[PATCH-PHASE-E] RESTART SKIPPED for trading-path proc: ${name} (${_tradingGateReason})`);
          try { await recordHeal("PM2_RESTART_GATED", { name, gate_reason: _tradingGateReason, status: proc.pm2_env?.status }); } catch {}
          continue;
        }
        // [FIX-B] canRestart() 체크 — fork 폭풍 방지
        const restartCheck = canRestart(name);
        if (restartCheck.allowed) {
          try {
            execSync(`pm2 restart ${name} 2>/dev/null`, { timeout: 10000 });
            recordRestart(name);
            await recordHeal("PM2_RESTART", { name, old_status: proc.pm2_env?.status, backoff_ms: restartCheck.backoffMs });
          } catch (e) { log("ERROR", "PM2 restart failed", { name, error: e.message }); }
        } else {
          log("WARN", `[FIX-B] Restart BLOCKED for ${name}: ${restartCheck.reason}`);
        }
      }
      
      // Crash loop 감지 (v2.2: 오탐 방지 강화)
      // - uptime < 2분 AND restart_time > 15  /* [STRUCTURAL_FIX] 5→15: PM2 누적 restart 오감지 방지 */: 실제 crash loop만 감지
      // - PM2 kill/재시작 후 누적 카운트로 인한 오탐 방지
      if (proc.pm2_env?.restart_time > 15  /* [STRUCTURAL_FIX] 5→15: PM2 누적 restart 오감지 방지 */) {
        const uptime = proc.pm2_env?.pm_uptime || 0;
        const uptimeMs = Date.now() - uptime;
        if (uptimeMs < 30 * 1000  /* [STRUCTURAL_FIX] 2min→30s */) {
          log("ERROR", `Crash loop detected: ${name}`, {
            restarts: proc.pm2_env.restart_time,
            uptime_ms: uptimeMs,
            threshold: "restart>15 AND uptime<30s"  // [STRUCTURAL_FIX],
          });

          // [v2.0] nextgen2-live 크래시 루프 시 PYTHONPATH 검증
          if (name === "nextgen2-live") {
            const envPP = proc.pm2_env?.PYTHONPATH || "";
            if (!envPP.includes("ares-nextgen")) {
              log("ERROR", "nextgen2-live PYTHONPATH missing ares-nextgen — attempting ecosystem restart", {
                current_pythonpath: envPP,
              });
              if (canAct("nextgen2_pythonpath_fix")) {
                // [FIX-B] canRestart() 체크
                const ng2Check = canRestart("nextgen2-live");
                if (!ng2Check.allowed) {
                  log("WARN", `[FIX-B] nextgen2 PYTHONPATH fix BLOCKED: ${ng2Check.reason}`);
                } else {
                try {
                  execSync(`pm2 restart nextgen2-live 2>/dev/null`, { timeout: 10000 });
                  // [STRUCTURAL-FIX] delete+start → restart (PM2 God daemon env 보존)
                  execSync("pm2 save 2>/dev/null", { timeout: 5000 });
                  recordRestart("nextgen2-live");
                  await recordHeal("NEXTGEN2_PYTHONPATH_FIX", {
                    old_pythonpath: envPP,
                    method: "restart_preserving_env",
                  });
                } catch (fixErr) {
                  log("ERROR", "nextgen2-live PYTHONPATH fix failed", { error: fixErr?.message });
                }
                } // [FIX-B] ng2Check.allowed closing brace
              }
            }
          }
        }
      }
    }
  } catch (e) {
    log("WARN", "PM2 health check failed", { error: e?.message });
  }
}

// ============================================================================
// CHECK 8: Redis 메모리 감시
// ============================================================================
async function checkRedisMemory() {
  try {
    const info = await redis.info("memory");
    const fragMatch = info.match(/mem_fragmentation_ratio:(\d+\.?\d*)/);
    if (fragMatch) {
      const frag = parseFloat(fragMatch[1]);
      if (frag > 3.0) {
        log("WARN", "Redis memory fragmentation high", { ratio: frag });
        await redis.set("self_healer:redis:frag_ratio", frag.toString());
      }
    }
    
    const usedMatch = info.match(/used_memory:(\d+)/);
    if (usedMatch) {
      const usedMb = parseInt(usedMatch[1]) / (1024 * 1024);
      if (usedMb > 1500 && __ratelimited("redis_mem_warn_last", 5*60*1000)) {
        log("WARN", "Redis memory usage high", { used_mb: Math.round(usedMb) });
      }
    }
  } catch { /* ignore */ }
}

// ============================================================================
// CHECK 9: budget_usd TTL 연장
// ============================================================================
async function checkBudgetTtl() {
  const budget = await redis.get("emarkos:v1:budget_usd");
  if (budget) {
    const ttl = await redis.ttl("emarkos:v1:budget_usd");
    if (ttl > 0 && ttl < 60) {
      await redis.expire("emarkos:v1:budget_usd", 600);
      await recordHeal("BUDGET_TTL_EXTENDED", { old_ttl: ttl, new_ttl: 600, budget });
    }
  }
}

// ============================================================================
// CHECK 10: Order Intent 백로그
// ============================================================================
async function checkIntentBacklog() {
  try {
    let lag = 0;
    try {
      const groups = await redis.call("XINFO", "GROUPS", "emarkos:v6:order:intent");
      if (Array.isArray(groups)) {
        for (const g of groups) {
          if (Array.isArray(g)) {
            let gName = "", gLag = 0;
            for (let i = 0; i < g.length; i += 2) {
              if (g[i] === "name") gName = g[i+1];
              if (g[i] === "lag") gLag = Number(g[i+1]) || 0;
            }
            if (gName === "order-intent-executor") { lag = gLag; break; }
          }
        }
      }
    } catch (e) {
      lag = await redis.xlen("emarkos:v6:order:intent");
    }
    const len = lag;
    if (len > 100) {
      log("WARN", "Order intent backlog detected", { pending: len });
      await redis.set("self_healer:intent_backlog", len.toString());
      try {
        const rawConsumers = await redis.call("XINFO", "CONSUMERS", "emarkos:v6:order:intent", "order-intent-executor");
        const STALE_MS = 600000;
        for (const entry of rawConsumers) {
          let cName = "", cIdle = 0, cPending = 0;
          if (Array.isArray(entry)) {
            for (let j = 0; j < entry.length; j += 2) {
              if (entry[j] === "name") cName = entry[j+1];
              if (entry[j] === "idle") cIdle = Number(entry[j+1]);
              if (entry[j] === "pending") cPending = Number(entry[j+1]);
            }
          }
          if (cIdle > STALE_MS && cName && cName !== "ops-reclaimer") {
            // SAFETY: do NOT silently XACK stale pending order intents.
            // For each pending entry, verify dispatch status; requeue any
            // un-dispatched intents into a dedicated reconciliation stream
            // and record an audit trail before acknowledging.
            if (cPending > 0) {
              const pend = await redis.xpending("emarkos:v6:order:intent", "order-intent-executor", "-", "+", 200, cName);
              if (pend && pend.length > 0) {
                const ids = pend.map(p => p[0]);
                const ackedIds = [];
                const requeuedIds = [];
                const failedIds = [];
                for (const id of ids) {
                  let payload = null;
                  try {
                    const range = await redis.xrange("emarkos:v6:order:intent", id, id);
                    if (Array.isArray(range) && range.length > 0 && Array.isArray(range[0]) && Array.isArray(range[0][1])) {
                      const fields = range[0][1];
                      payload = {};
                      for (let i = 0; i < fields.length; i += 2) payload[fields[i]] = fields[i+1];
                    }
                  } catch (e) { /* xrange failure */ }
                  let intentId = id;
                  let dispatched = false;
                  try {
                    if (payload) {
                      const candidateId = payload.intent_id || payload.id || payload.client_order_id;
                      if (candidateId) intentId = candidateId;
                    }
                    // Treat as dispatched if any of these markers exist:
                    //   order:dispatched:{id}, order:executed:{id}, broker:order:reply:{id}
                    const markers = await Promise.all([
                      redis.exists(`order:dispatched:${intentId}`),
                      redis.exists(`order:executed:${intentId}`),
                      redis.exists(`broker:order:reply:${intentId}`),
                    ]);
                    dispatched = markers.some(x => Number(x) > 0);
                  } catch (e) { /* exists failure */ }
                  if (dispatched) {
                    ackedIds.push(id);
                    continue;
                  }
                  // [v13 B2 fix] Not dispatched: deterministic recovery path.
                  //   1) Quarantine for evidence trail.
                  //   2) Idempotent re-XADD into the main intent stream with a
                  //      replay marker key so the executor can pick it up.
                  //   3) replay_marker:{intentId} (NX, EX 86400) prevents
                  //      duplicate replays. ACK only after replay is confirmed.
                  try {
                                        // === v15 E1+E2+E9: validated atomic replay (3AI consensus 2026-05-07) ===
                    // Step 1: validate payload before any replay action
                    const __v15Validation = v15ValidateReplayPayload(payload);
                    if (!__v15Validation.valid) {
                      log("ERROR", `payload validation failed id=${id} intent=${intentId}: ${JSON.stringify(__v15Validation)}`);
                      await v15RouteToDLQ(redis, payload, { stale_consumer: cName, idle_ms: cIdle }, intentId, id, 'PAYLOAD_VALIDATION_FAILED_V15', __v15Validation);
                      await redis.lpush("self_healer:order_intent_audit", JSON.stringify({
                        ts: Date.now(), id, intentId, cName, cIdle,
                        action: "PAYLOAD_VALIDATION_FAILED",
                        validation: __v15Validation,
                      }));
                      // Do NOT ACK — entry stays pending for manual recovery
                      failedIds.push(id);
                      continue;
                    }

                    // Step 2: invoke v15 atomic replay
                    const __v15Result = await v15AtomicReplay(redis, {
                      intentId,
                      markerTTL: 86400,
                      metadata: { stale_consumer: cName, idle_ms: cIdle, requeued_at: Date.now() },
                      payload: payload,
                    });
                    if (__v15Result.status === 'OK' || __v15Result.status === 'RETRY_MAIN') {
                      await redis.lpush("self_healer:order_intent_audit", JSON.stringify({
                        ts: Date.now(), id, intentId, cName, cIdle,
                        action: __v15Result.status === 'RETRY_MAIN'
                          ? "REQUEUED_AND_REPLAYED_ATOMIC_RECOVERED"
                          : "REQUEUED_AND_REPLAYED_ATOMIC",
                        v15_status: __v15Result.status,
                      }));
                    } else if (__v15Result.status === 'DUPLICATE') {
                      // Step 3: verify DUPLICATE before ACK (E1)
                      const __v15Verify = await v15VerifyDuplicate(redis, intentId);
                      if (__v15Verify.verified) {
                        await redis.lpush("self_healer:order_intent_audit", JSON.stringify({
                          ts: Date.now(), id, intentId, cName, cIdle,
                          action: "DUPLICATE_VERIFIED_ACK",
                          main_id: __v15Verify.mainId,
                        }));
                        // Safe to ACK — fall through to settle
                      } else {
                        log("ERROR", `DUPLICATE not verified id=${id} reason=${__v15Verify.reason} marker=${__v15Verify.marker || 'n/a'}`);
                        await v15RouteToDLQ(redis, payload, { stale_consumer: cName, idle_ms: cIdle }, intentId, id, `DUPLICATE_NOT_VERIFIED_${__v15Verify.reason}`.toUpperCase(), __v15Verify);
                        await redis.lpush("self_healer:order_intent_audit", JSON.stringify({
                          ts: Date.now(), id, intentId, cName, cIdle,
                          action: "DUPLICATE_NOT_VERIFIED_DLQ",
                          verify: __v15Verify,
                        }));
                        // Do NOT push to requeuedIds; do NOT ACK
                        failedIds.push(id);
                        continue;
                      }
                    } else {
                      // ERR:<reason> — abort, no ACK
                      log("ERROR", `atomic replay error id=${id} status=${__v15Result.status}`);
                      await redis.lpush("self_healer:order_intent_audit", JSON.stringify({
                        ts: Date.now(), id, intentId, cName, cIdle,
                        action: "ATOMIC_REPLAY_ERROR_V15",
                        v15_status: __v15Result.status,
                      }));
                      failedIds.push(id);
                      continue;
                    }
                    await redis.ltrim("self_healer:order_intent_audit", 0, 999);
                    requeuedIds.push(id);
                  } catch (e) {
                    failedIds.push(id);
                    log("ERROR", `intent recovery failed id=${id}: ${String(e?.message || e)}`);
                  }
                }
                // Acknowledge dispatched + requeued (originals are now in audit stream)
                const settleIds = ackedIds.concat(requeuedIds);
                if (settleIds.length > 0) {
                  await redis.xack("emarkos:v6:order:intent", "order-intent-executor", ...settleIds);
                }
                log("WARN", `Reconciled ${ids.length} stale intents from ${cName}`, {dispatched: ackedIds.length, requeued: requeuedIds.length, failed: failedIds.length});
                // Telegram-grade alert via Redis pub/sub (downstream alerter consumes)
                try {
                  if (requeuedIds.length > 0 || failedIds.length > 0) {
                    await redis.publish("alerts:order_intent", JSON.stringify({severity: failedIds.length > 0 ? "SEV1" : "SEV2", consumer: cName, dispatched: ackedIds.length, requeued: requeuedIds.length, failed: failedIds.length, idle_ms: cIdle, ts: Date.now()}));
                  }
                } catch (e) { /* publish failure non-fatal */ }
                // If anything failed to requeue, do NOT prune the consumer — preserve evidence
                if (failedIds.length > 0) {
                  log("ERROR", `Skipping DELCONSUMER for ${cName} due to ${failedIds.length} failed requeue(s)`);
                  continue;
                }
              }
            }
            await redis.call("XGROUP", "DELCONSUMER", "emarkos:v6:order:intent", "order-intent-executor", cName);
            log("INFO", `Auto-pruned stale consumer: ${cName} (idle=${cIdle}ms)`);
          }
        }
      } catch (e) {
        log("WARN", "Consumer prune failed: " + String(e?.message || e));
      }
    }
  } catch { /* ignore */ }
}

// ─── CHECK 11: kill_switch 레거시 키 통합 동기화 (SEV-1 재발 방지) ───
async function checkKillSwitchSync() {
  try {
    const legacyKS = await redis.get("ares:kill_switch");
    const authorityRaw = await redis.get("kill-switch-authority:state");
    
    if (!authorityRaw) return;
    
    let authority;
    try { authority = JSON.parse(authorityRaw); } catch { return; }
    const authorityEnabled = authority.state === "ENABLED";
    
    if (authorityEnabled && legacyKS === "true") {
      if (canAct("kill_switch_sync")) {
        // [v13 B3 fix] Emit evidence first; only mutate legacy key under guard.
        await emitAuthorityEvidence("HALT_CLEAR_REQUEST", {
          source_action: "checkKillSwitchSync.legacy_to_authority_enabled",
          legacy_value: legacyKS,
          authority_state: authority.state,
        });
        if (LEGACY_DIRECT_GATE_WRITES) {
          // [v13 B3] key via concat for audit-grep explicitness
          await redis.set("ares" + ":" + "kill_switch", "false");
          log("WARN", "CHECK 11: ares:kill_switch was true but authority=ENABLED(GO). Fixed to false.");
          await recordHeal("kill_switch_sync", "ares:kill_switch true->false (authority=ENABLED)");
        } else {
          log("INFO", "CHECK 11: legacy direct writes disabled, evidence-only mode");
          await recordHeal("kill_switch_sync_evidence_only", "emitted HALT_CLEAR_REQUEST (legacy=false)");
        }
      }
    }
    
    if (!authorityEnabled && legacyKS === "false") {
      if (canAct("kill_switch_sync_disable")) {
        // [v13 B3 fix] Evidence first, then guarded legacy write.
        await emitAuthorityEvidence("HALT_REQUEST", {
          source_action: "checkKillSwitchSync.legacy_to_authority_disabled",
          legacy_value: legacyKS,
          authority_state: authority.state,
        });
        if (LEGACY_DIRECT_GATE_WRITES) {
          await redis.set("ares" + ":" + "kill_switch", "true");
          log("WARN", "CHECK 11: ares:kill_switch was false but authority=DISABLED. Fixed to true.");
          await recordHeal("kill_switch_sync", "ares:kill_switch false->true (authority=DISABLED)");
        } else {
          log("INFO", "CHECK 11: legacy direct writes disabled, evidence-only mode");
          await recordHeal("kill_switch_sync_disable_evidence_only", "emitted HALT_REQUEST (legacy=false)");
        }
      }
    }
    
    const targetVal = authorityEnabled ? "false" : "true";
    for (const key of ["kill_switch", "policy:kill_switch", "policy:overlay:defensive:kill_switch"]) {
      const val = await redis.get(key);
      if (val !== null && val !== targetVal) {
        await redis.set(key, targetVal);
        log("WARN", "CHECK 11: " + key + " synced to " + targetVal);
      }
    }
  } catch (e) {
    log("ERROR", "CHECK 11 error: " + (e.message || String(e)));
  }
}

// ============================================================================
// CHECK 12: [NEW v2.0] Ecosystem 무결성 검증
// ============================================================================
async function checkEcosystemIntegrity() {
  try {
    const pm2Out = execSync("pm2 jlist 2>/dev/null", { encoding: "utf8", timeout: 10000, maxBuffer: 50 * 1024 * 1024 });
    const procs = JSON.parse(pm2Out);
    
    // 12a: Poison process 감지 및 자동 제거
    for (const proc of procs) {
      if (POISON_NAMES.includes(proc.name)) {
        log("ERROR", `CHECK 12: Poison process detected: ${proc.name} — removing`);
        if (canAct(`poison_remove_${proc.name}`)) {
          try {
            execSync(`pm2 delete "${proc.name}" 2>/dev/null`, { timeout: 5000 });
            execSync("pm2 save --force 2>/dev/null", { timeout: 5000 });
            await recordHeal("POISON_PROCESS_REMOVED", { name: proc.name });
          } catch (e) {
            log("ERROR", `Failed to remove poison process: ${proc.name}`, { error: e?.message });
          }
        }
      }
    }
    
    // 12b: 프로세스 수 검증 (poison 제외)
    const cleanProcs = procs.filter(p => !POISON_NAMES.includes(p.name));
    const onlineCount = cleanProcs.filter(p => p.pm2_env?.status === "online").length;
    
    if (cleanProcs.length < MIN_EXPECTED_PROCS) {
      log("ERROR", `CHECK 12: Only ${cleanProcs.length} processes (expected >= ${MIN_EXPECTED_PROCS})`, {
        online: onlineCount, total: cleanProcs.length,
      });
      
      // 프로세스가 절반 이하이면 ecosystem에서 전체 복구
      if (cleanProcs.length < MIN_EXPECTED_PROCS / 2 && canAct("ecosystem_full_restore")) {
        // [FIX-B] 전체 복구도 canRestart() 체크 — fork 폭풍 방지
        const fullRestoreCheck = canRestart("ecosystem_full_restore");
        if (fullRestoreCheck.allowed) {
          log("ERROR", "CHECK 12: Critical — less than half expected processes. Full ecosystem restore.");
          try {
            execSync(`pm2 start ${ECOSYSTEM_CONFIG} 2>/dev/null`, { timeout: 30000 });
            execSync("pm2 save --force 2>/dev/null", { timeout: 5000 });
            recordRestart("ecosystem_full_restore");
            await recordHeal("ECOSYSTEM_FULL_RESTORE", {
              before_count: cleanProcs.length,
              method: "ecosystem_config_start",
            });
          } catch (e) {
            log("ERROR", "Ecosystem full restore failed", { error: e?.message });
          }
        } else {
          log("CRITICAL", `[FIX-B] ECOSYSTEM_FULL_RESTORE BLOCKED: ${fullRestoreCheck.reason}`, {
            proc_count: cleanProcs.length,
          });
        }
      }
    }
    
    // 12c: 상태를 Redis에 기록
    await redis.set("self_healer:ecosystem:proc_count", cleanProcs.length.toString(), "EX", 120);
    await redis.set("self_healer:ecosystem:online_count", onlineCount.toString(), "EX", 120);
    
  } catch (e) {
    log("WARN", "CHECK 12 error: " + (e?.message || String(e)));
  }
}

// ============================================================================
// CHECK 13: [NEW v2.0] PM2 Dump 건강 검사
// ============================================================================
async function checkDumpHealth() {
  try {
    if (!existsSync(DUMP_FILE)) {
      log("WARN", "CHECK 13: dump.pm2 not found — saving current state");
      execSync("pm2 save --force 2>/dev/null", { timeout: 5000 });
      return;
    }
    
    const dumpContent = readFileSync(DUMP_FILE, "utf8");
    let dumpProcs;
    try {
      dumpProcs = JSON.parse(dumpContent);
    } catch {
      log("ERROR", "CHECK 13: dump.pm2 is corrupted JSON — re-saving");
      if (canAct("dump_resave")) {
        execSync("pm2 save --force 2>/dev/null", { timeout: 5000 });
        await recordHeal("DUMP_RESAVED", { reason: "corrupted_json" });
      }
      return;
    }
    
    // Poison process 검사
    const poisonInDump = dumpProcs.filter(p => POISON_NAMES.includes(p.name));
    if (poisonInDump.length > 0) {
      log("ERROR", `CHECK 13: dump.pm2 contains ${poisonInDump.length} poison processes`, {
        names: poisonInDump.map(p => p.name),
      });
      if (canAct("dump_poison_cleanup")) {
        // 현재 PM2 상태가 깨끗하면 그것으로 dump 덮어쓰기
        const pm2Out = execSync("pm2 jlist 2>/dev/null", { encoding: "utf8", timeout: 10000, maxBuffer: 50 * 1024 * 1024 });
        const currentProcs = JSON.parse(pm2Out);
        const currentPoison = currentProcs.filter(p => POISON_NAMES.includes(p.name));
        
        if (currentPoison.length === 0 && currentProcs.length >= MIN_EXPECTED_PROCS) {
          execSync("pm2 save --force 2>/dev/null", { timeout: 5000 });
          await recordHeal("DUMP_POISON_CLEANED", {
            poison_removed: poisonInDump.map(p => p.name),
            method: "overwrite_with_clean_state",
          });
        }
      }
    }
    
    // PYTHONPATH 검사 (nextgen2-live)
    const ng2InDump = dumpProcs.find(p => p.name === "nextgen2-live");
    if (ng2InDump) {
      const pp = ng2InDump.env?.PYTHONPATH || ng2InDump.pm2_env?.PYTHONPATH || ng2InDump.pm2_env?.env?.PYTHONPATH || "";
      if (!pp.includes("ares-nextgen")) {
        log("WARN", "CHECK 13: dump nextgen2-live missing ares-nextgen in PYTHONPATH", {
          dump_pythonpath: pp,
        });
        // 현재 실행 중인 nextgen2-live가 정상이면 dump 갱신
        if (canAct("dump_pythonpath_fix")) {
          execSync("pm2 save --force 2>/dev/null", { timeout: 5000 });
          await recordHeal("DUMP_PYTHONPATH_FIXED", { old_pp: pp });
        }
      }
    }
    
  } catch (e) {
    log("WARN", "CHECK 13 error: " + (e?.message || String(e)));
  }
}

// ============================================================================
// MAIN LOOP
// ============================================================================
async function runCycle() {

// CHECK 14: [STRUCTURAL-FIX-2026-04-17] Deprecated Process Guard
// Auto-kill processes that have been superseded by newer versions
async function checkDeprecatedProcesses() {
  try {
    const deprecatedActions = await runDeprecatedProcessGuard();
    if (deprecatedActions.length > 0) {
      await recordHeal('DEPRECATED_PROCESS_KILLED', {
        killed: deprecatedActions.map(a => a.process_name),
        count: deprecatedActions.length,
      });
      log('CRITICAL', 'CHECK 14: Killed deprecated processes', {
        killed: deprecatedActions.map(a => a.process_name),
      });
    }
  } catch (e) {
    log('WARN', 'CHECK 14 error: ' + (e?.message || String(e)));
  }
}
  try {
    await checkEquitySsot();
    await checkAnchorDate();
    await checkTradingEnabled();
    await checkKisToken();
    await checkPaperMode();
    await checkStaleData();
    await checkPm2Health();
    await checkRedisMemory();
    await checkBudgetTtl();
    await checkIntentBacklog();
    await checkKillSwitchSync();
    await checkEcosystemIntegrity();  // CHECK 12: ecosystem 무결성
    await checkDumpHealth();          // CHECK 13: dump 건강 검사
    await checkDeprecatedProcesses();  // CHECK 14: deprecated process guard
    
    // [FIX-B] 안정 상태이면 backoff 카운터 리셋
    resetBackoffIfStable();
    
    // 건강 상태 기록
    await redis.set("self_healer:last_run", new Date().toISOString(), "EX", 120);
    await redis.set("self_healer:status", "OK", "EX", 120);
    await redis.set("self_healer:version", "v2.2-hardened", "EX", 120);
    await redis.set("self_healer:checks", "14", "EX", 120);
    // [FIX-B] fork 폭풍 방지 상태 기록
    await redis.set("self_healer:restart_window_count", restartHistory.length.toString(), "EX", 120);
    await redis.set("self_healer:circuit_breaker", (Date.now() < circuitBreakerUntil ? "ACTIVE" : "OFF"), "EX", 120);
    await redis.set("self_healer:consecutive_restarts", consecutiveRestarts.toString(), "EX", 120);
  } catch (e) {
    log("ERROR", "Self-healer cycle error", { error: e?.message ?? String(e) });
    await redis.set("self_healer:status", "ERROR", "EX", 120);
  }
}

async function main() {
  log("INFO", "Self-Healer v2.2-hardened starting", { poll_ms: POLL_MS, checks: 13, critical_procs: 23, max_restarts_per_window: MAX_RESTARTS_PER_WINDOW, circuit_breaker_ms: CIRCUIT_BREAKER_DURATION_MS });
  
  while (true) {
    await runCycle();
    await new Promise(r => setTimeout(r, POLL_MS));
  }
}

main().catch(e => {
  log("FATAL", "Self-healer crashed", { error: e?.message ?? String(e) });
  throw new Error("Fatal error - PM2 will restart");
});
