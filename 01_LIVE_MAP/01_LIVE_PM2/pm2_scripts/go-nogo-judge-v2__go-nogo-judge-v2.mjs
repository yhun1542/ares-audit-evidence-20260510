// SOURCE: /home/ubuntu/ares_current/go-nogo-judge-v2.mjs
// SIZE: 38239 chars

import { activateRebalGate, checkRebalGate } from "./lib/patches/PATCH-006-rebal-gate-crash-guard.mjs";
// [PART6-B3-UNIFIED] 2026-04-25: kis_token_resolver.mjs helper 통합.
// AUB 버전의 4-source fallback + 60s soft-warn grace를 1차로 사용하고,
// 기존 inline 4단 fallback을 2차 안전망으로 유지.
import { judgeKisToken } from './kis_token_resolver.mjs';
/**
 * go-nogo-judge-v2.mjs — ARES Go/No-Go 판정 서비스 v2.0 (구조적 재설계)
 *
 * === 아키텍처 변경 (v1 → v2) ===
 *
 * [1] one-shot → 상주 데몬
 *     - v1: PM2 cron_restart로 매분 실행 후 process.exit() (제거됨) → 외부 스케줄러 의존
 *     - v2: 자체 setInterval(60초) 데몬, PM2 autorestart=true, cron 불필요
 *     - 효과: PM2 dump 복원 시 autorestart 설정 불일치 문제 근본 제거
 *
 * [2] price 키 존재 여부 → freshness 기반 판정
 *     - v1: redis.get('price:SPY') === null이면 FAIL → TTL 만료 시 거짓 음성
 *     - v2: price:SPY:updated_at 타임스탬프 기반 freshness 판정
 *           키가 없어도 updated_at이 maxAge 이내면 PASS (TTL 만료 무관)
 *     - 효과: price 키 TTL과 go-nogo 판정의 결합도 완전 제거
 *
 * [3] 히스테리시스(debounce) verdict
 *     - v1: 매 실행마다 즉시 verdict 기록 → 일시적 이상에도 NO-GO
 *     - v2: N회 연속 NO-GO일 때만 verdict 확정 (기본 3회 = 3분)
 *           1회 GO로 즉시 복구 (비대칭 히스테리시스)
 *     - 효과: 일시적 키 만료, 네트워크 지연 등에 의한 플리핑 완전 차단
 *
 * [4] 장외 시간 자동 GO
 *     - v1: 장외 시간에도 동일 로직 실행 → 비거래 시간 NO-GO 발생
 *     - v2: 장외 시간에는 verdict=GO 고정 (체크 스킵, 불필요한 알림 차단)
 *     - 효과: 주말/야간 NO-GO 플리핑 근본 차단
 *
 * [5] 인메모리 + Redis 이중 dedup
 *     - v1: Redis 키 기반 dedup만 → 재시작 시 dedup 상태 유실
 *     - v2: 인메모리 lastNotifiedResult + Redis dedup 이중화
 *     - 효과: 프로세스 재시작 시에도 알림 폭풍 방지
 *
 * SSOT 원칙 유지:
 *   - 이 서비스는 trading:enabled를 직접 쓰지 않습니다.
 *   - ares:go_nogo:verdict 키에 판정 결과만 기록합니다.
 *   - kill-switch-authority-v2가 verdict를 읽고 trading:enabled를 최종 반영합니다.
 */

import Redis from 'ioredis';
import { assessRequiredPm2Processes, DEFAULT_SUPERVISOR_PM2_CONTRACT } from './lib/ares-supervisor-aware-pm2.mjs';
import { exec as execCb } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(execCb);

// ============================================================
// 설정
// ============================================================
function localhostRedisAllowed() {
  return ['1', 'true', 'yes', 'on'].includes(String(process.env.ARES_ALLOW_LOCALHOST_REDIS || '').toLowerCase());
}

function assertNonLocalRedisTarget(value, label = 'Redis target') {
  const v = String(value || '').toLowerCase();
  if ((v.includes('localhost') || v.includes('127.0.0.1')) && !localhostRedisAllowed()) {
    throw new Error(`${label} points to localhost; set ARES_ALLOW_LOCALHOST_REDIS=1 only for local development`);
  }
}

function redisOptionsFromEnv() {
  const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || process.env.REDIS_WRITER_URL || process.env.REDIS_READER_URL;
  if (url) {
    assertNonLocalRedisTarget(url, 'Redis URL');
    return url;
  }
  const host = process.env.ARES_REDIS_HOST || process.env.REDIS_HOST;
  if (!host) throw new Error('REDIS_URL/ARES_REDIS_URL/REDIS_HOST missing; refusing implicit localhost Redis fallback');
  assertNonLocalRedisTarget(host, 'Redis host');
  return {
    host,
    port: Number(process.env.ARES_REDIS_PORT || process.env.REDIS_PORT || 6379),
    password: process.env.REDIS_PASSWORD || process.env.ARES_REDIS_PASSWORD || undefined,
    retryStrategy: (times) => Math.min(times * 200, 5000),
    maxRetriesPerRequest: 3,
    lazyConnect: true,
  };
}

const CONFIG = {
  redis: redisOptionsFromEnv(),
  telegram: {
    botToken: process.env.TELEGRAM_BOT_TOKEN,
    chatId: process.env.TELEGRAM_CHAT_ID,
  },

  // 데몬 설정
  checkIntervalMs: 60_000,  // 60초 간격

  // 히스테리시스 설정
  hysteresis: {
    noGoThreshold: 3,   // N회 연속 NO-GO일 때 verdict 확정
    goThreshold: 1,     // 1회 GO로 즉시 복구 (비대칭)
    degradedThreshold: 2, // 2회 연속 DEGRADED일 때 확정
  },

  // 필수 PM2 프로세스 목록
  //
  // STRUCTURAL HOTFIX 2026-04-17 — alias-set entries.
  // Each entry is either a single name (exact match) or an array of
  // acceptable names (any one online satisfies the requirement).
  // The array form — the "alias set" — prevents list-of-names drift when a
  // service is version-bumped (e.g. realtime-data-feed → realtime-data-feed-v3)
  // but this list is not updated, causing a permanent NO-GO verdict even when
  // the functional dependency is satisfied. See ROOT_CAUSE_ANALYSIS_20260417.md.
  requiredProcesses: [
    ['realtime-data-feed', 'realtime-data-feed-v3'],  // v3 is current; v1 is deprecated self-exit
    'watchdog-monitor',
    'kis-balance-sync',
    // 'order-intent-executor', // DEADLOCK-FIX: managed by trade-actuator-supervisor
    'nextgen2-live',
    // 'final-to-champion-bridge', // DEADLOCK-FIX: managed by trade-actuator-supervisor
    'ssot-pipeline-guard',
    'ssot-writer-v2',
    'ssot-promote-v2',
  ],

  // FIX-S3: Required Redis keys — emarkos:v1:positions removed (uses fallback check instead)
  // Rationale: emarkos:v1:positions key expires when normalizer is not running,
  // but kis:broker:positions (hash) is always available from kis-balance-sync.
  // checkRequiredKeys now handles the OR-fallback logic.
  // [PART6-B3-UNIFIED] 2026-04-25: requiredKeys 엔트리는 다음 중 하나의 형식:
  //   string                    -> exact match + value non-empty
  //   { key, type:'hash' }       -> HLEN > 0 요구 (regime:final:current 같은 hash SSOT)
  //   { key, type:'string' }     -> 문자열 동일하게 exists+non-empty
  //   { key, fallback:'...' }    -> primary missing 시 fallback 시도
  // emarkos:v1:regime은 현재 Redis에 존재하지 않고, policy:regime_ssot_key가
  // "regime:final:current" (hash)로 지정하므로 이를 canonical로 사용.
  requiredKeys: [
    { key: 'regime:final:current', type: 'hash', fallback: 'regime:current' },
    // [STRUCTURAL-UPGRADE 2026-04-29] authoritative equity fallback.
    // Primary legacy key may be absent while the current equity writer owns
    // ares:equity:authoritative; this prevents a false NO-GO caused by key-contract drift.
    { key: 'ares:equity:total', type: 'string', fallback: 'ares:equity:authoritative' },
  ],

  // FIX-S3: Positions key pairs (primary + fallback)
  positionsKeyCheck: {
    primary: 'emarkos:v1:positions',
    fallback: 'kis:broker:positions',  // Redis hash — check via HLEN
    fallbackType: 'hash',
    minCount: 5,  // At least 5 positions to be considered valid
  },

  // PATCH-GNG-001 (2026-04-20): PM2 check fallbacks & grace period.
  // Motivation: on 2026-04-20, ssot-pipeline-guard entered a restart-loop due to
  //   a /tmp lock collision with a systemd instance. pm2 jlist reported
  //   status='waiting restart' (not 'online'), so this check immediately
  //   tripped NO-GO, which cascaded into trading:enabled=false and a full halt
  //   of FTG/OFG. We now (a) accept a short grace window on transient PM2
  //   restarts and (b) fall back to a Redis-heartbeat probe per process, so
  //   a process that is demonstrably healthy at the functional level (writing
  //   recent heartbeats) does not cause a system-wide halt just because pm2
  //   state momentarily differs.
  //
  // Each fallback entry: { key: redis-key, maxAgeSec: freshness bound,
  //   tsField?: JSON field name if the value is JSON with a ts field,
  //   tsFormat?: 'epoch_s' | 'epoch_ms' | 'iso' }
  // PATCH-GNG-002 (2026-04-20): unified key convention via heartbeat-sentinel
  // sidecar. Sentinel observes pm2 jlist every 5s and writes ares:hb:<name>
  // with TTL ~60s. maxAgeSec is tuned to 2x TTL for safety (120s). Native
  // heartbeats (e.g. ssot-pipeline-guard self-written ssot:guard:v3:heartbeat)
  // are still accepted via multi-key lookup inside checkPM2Processes if
  // implemented; for now we consolidate to the sidecar convention.
  pm2Fallbacks: {
    'ssot-pipeline-guard':     { key: 'ares:hb:ssot-pipeline-guard',     maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'realtime-data-feed-v3':   { key: 'ares:hb:realtime-data-feed-v3',   maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'realtime-data-feed':      { key: 'ares:hb:realtime-data-feed',      maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'watchdog-monitor':        { key: 'ares:hb:watchdog-monitor',        maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'kis-balance-sync':        { key: 'ares:hb:kis-balance-sync',        maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'order-intent-executor':   { key: 'ares:hb:order-intent-executor',   maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'nextgen2-live':           { key: 'ares:hb:nextgen2-live',           maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'final-to-champion-bridge':{ key: 'ares:hb:final-to-champion-bridge',maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'ssot-writer-v2':          { key: 'ares:hb:ssot-writer-v2',          maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
    'ssot-promote-v2':         { key: 'ares:hb:ssot-promote-v2',         maxAgeSec: 120, tsField: 'ts', tsFormat: 'epoch_s' },
  },
  // A process must be missing from pm2 'online' AND its heartbeat must be stale
  // for longer than this grace window before it is counted as a PM2 failure.
  pm2GraceMs: 90_000,

  // freshness 기반 판정 (price 키 포함)
  // updated_at 타임스탬프가 maxAgeSec 이내이면 PASS
  // 키 자체가 없어도 updated_at이 유효하면 PASS (TTL 만료 무관)
  freshnessChecks: {
    // [PATCH-P5 2026-05-07] benchmark freshness:
    //   - critical:false (장외시간 false-critical 제거)
    //   - tsKey를 ohlcv json의 timestamp로 변경 (별도 :updated_at 키 미발행)
    //   - maxAgeSec=86400 (24h) — 장 닫혀있어도 false fail 없도록
    // QQQ는 universe에 없으므로 키 부재 시 무시. SPY는 :ohlcv json 안의 timestamp 사용.
    'price:QQQ': { maxAgeSec: 86400, tsKey: 'price:QQQ:ohlcv', tsField: 'timestamp', tsFormat: 'epoch_ms', critical: false },
    'price:SPY': { maxAgeSec: 86400, tsKey: 'price:SPY:ohlcv', tsField: 'timestamp', tsFormat: 'epoch_ms', critical: false },
    'emarkos:v1:positions': { maxAgeSec: 600, tsKey: 'emarkos:v1:positions:ts', critical: false,
      fallbackKey: 'kis:broker:positions', fallbackType: 'hash' },  // FIX-S3
    'ares:equity:total': { maxAgeSec: 600, tsKey: 'ares:equity:total:ts', critical: false },
  },

  // 시장 시간 (ET) — Intl.DateTimeFormat 기반
  marketHours: {
    preMarginMinutes: 30,  // 장 시작 30분 전부터 체크 시작
    open: { h: 9, m: 30 },
    close: { h: 16, m: 0 },
    postMarginMinutes: 30, // 장 마감 30분 후까지 체크 유지
  },
};

// ============================================================
// 상태 (인메모리)
// ============================================================
const STATE = {
  consecutiveNoGo: 0,       // 연속 NO-GO 횟수
  consecutiveGo: 0,         // 연속 GO 횟수
  consecutiveDegraded: 0,   // 연속 DEGRADED 횟수
  confirmedVerdict: 'GO',   // 히스테리시스 적용 후 확정된 verdict (GO/DEGRADED/NO-GO)
  lastNotifiedResult: null,  // 마지막 알림 발송 결과 (인메모리 dedup)
  checkCount: 0,            // 총 체크 횟수
  lastCheckTs: null,        // 마지막 체크 시각
  startedAt: new Date().toISOString(),
  // PATCH-GNG-001: per-process epoch-ms when it first went non-online and
  // had no fresh heartbeat. Cleared when the process is healthy again.
  pm2RecentFailSince: {},
};

// ============================================================
// Redis 연결
// ============================================================
let redis;

// FIX-S3: Fixed malformed initRedis — DEGRADED block was incorrectly spliced into Redis init
function initRedis() {
  // [MUSK-FIX-001] Ensure lazyConnect for URL-based connections
  // [MUSK-FIX-001] Force lazyConnect to prevent auto-connect race
  if (typeof CONFIG.redis === "string") {
    redis = new Redis(CONFIG.redis, { lazyConnect: true, maxRetriesPerRequest: 3, retryStrategy: (t) => Math.min(t * 200, 5000) });
  } else {
    redis = new Redis({ ...CONFIG.redis, lazyConnect: true });
  }

  redis.on('error', (err) => {
    log('ERROR', 'Redis connection error', { error: err.message });
  });

  redis.on('connect', () => {
    log('INFO', 'Redis connected');
  });

  return redis;
}

// ============================================================
// 로깅
// ============================================================
function log(level, msg, data = {}) {
  console.log(JSON.stringify({
    ts: new Date().toISOString(),
    level,
    proc: 'go-nogo-judge-v2',
    msg,
    ...data,
  }));
}

// ============================================================
// 시장 시간 판정 — Intl.DateTimeFormat 기반
// ============================================================
function getETNow() {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  });
  const parts = {};
  for (const { type, value } of formatter.formatToParts(new Date())) {
    parts[type] = value;
  }
  return {
    hour: parseInt(parts.hour),
    minute: parseInt(parts.minute),
    second: parseInt(parts.second),
    dayOfWeek: new Date(
      new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
    ).getDay(),
  };
}

function isActiveCheckWindow() {
  const et = getETNow();
  // 주말 → 체크 불필요
  if (et.dayOfWeek === 0 || et.dayOfWeek === 6) return false;

  const etMinutes = et.hour * 60 + et.minute;
  const openMinutes = CONFIG.marketHours.open.h * 60 + CONFIG.marketHours.open.m;
  const closeMinutes = CONFIG.marketHours.close.h * 60 + CONFIG.marketHours.close.m;

  // 장 시작 30분 전 ~ 장 마감 30분 후
  return etMinutes >= (openMinutes - CONFIG.marketHours.preMarginMinutes)
      && etMinutes <= (closeMinutes + CONFIG.marketHours.postMarginMinutes);
}

// ============================================================
// Telegram 알림 (이중 dedup)
// ============================================================
const VERDICT_DEDUP_KEY = 'ares:go_nogo:last_notified_result';
const VERDICT_DEDUP_TTL = 86400;

async function sendTelegramIfChanged(result, message) {
  // 1차: 인메모리 dedup
  if (STATE.lastNotifiedResult === result) {
    log('DEBUG', 'Telegram suppressed (in-memory dedup)', { result });
    return;
  }

  // 2차: Redis dedup (프로세스 재시작 시 보호)
  try {
    const lastRedis = await redis.get(VERDICT_DEDUP_KEY);
    if (lastRedis === result) {
      STATE.lastNotifiedResult = result; // 인메모리 동기화
      log('DEBUG', 'Telegram suppressed (Redis dedup)', { result });
      return;
    }
  } catch (err) {
    log('WARN', 'Redis dedup check failed, proceeding with send', { error: err.message });
  }

  // 알림 발송
  if (!CONFIG.telegram.botToken || !CONFIG.telegram.chatId) return;
  try {
    const url = `https://api.telegram.org/bot${CONFIG.telegram.botToken}/sendMessage`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: CONFIG.telegram.chatId,
        text: message,
        disable_web_page_preview: true,
      }),
    });
    if (resp.ok) {
      STATE.lastNotifiedResult = result;
      await redis.set(VERDICT_DEDUP_KEY, result, 'EX', VERDICT_DEDUP_TTL).catch(() => {});
      log('INFO', 'Telegram notification sent', { result });
    } else if (rawVerdict === "DEGRADED") {
    STATE.consecutiveDegraded++;
    STATE.consecutiveGo = 0;
    STATE.consecutiveNoGo = 0;
    if (STATE.consecutiveDegraded >= (CONFIG.hysteresis.degradedThreshold || 2)) {
      STATE.confirmedVerdict = "DEGRADED";
    }
  } else {
      log('WARN', 'Telegram send failed', { status: resp.status });
    }
  } catch (err) {
    log('ERROR', 'Telegram error', { error: err.message });
  }
}

// ============================================================
// 검증 함수들
// ============================================================
async function checkRedis() {
  try {
    const pong = await redis.ping();
    return { pass: pong === 'PONG', detail: pong };
  } catch (err) {
    return { pass: false, detail: err.message };
  }
}

// PATCH-GNG-001 (2026-04-20): heartbeat-aware PM2 check with grace period.
//
// Semantics:
//   1. If any alias is 'online' in pm2 jlist → PASS (unchanged).
//   2. Otherwise, if a Redis heartbeat is configured for any alias and the
//      heartbeat's ts is within maxAgeSec → PASS (degraded label attached for
//      observability but not failing).
//   3. Otherwise, start/continue a grace timer. Fail only if the process has
//      been continuously non-online AND heartbeat-stale for pm2GraceMs.
//
// Rationale: prevents single-process pm2 restart loops from cascading into
// kill-switch→trading:enabled=false→FTG/OFG HALT chain observed on 2026-04-20.
async function _heartbeatFreshFor(name) {
  const fb = CONFIG.pm2Fallbacks && CONFIG.pm2Fallbacks[name];
  if (!fb || !fb.key) return { ok: false };
  let raw;
  try { raw = await redis.get(fb.key); } catch { return { ok: false }; }
  if (!raw) return { ok: false };
  let ts = null;
  if (fb.tsField) {
    try {
      const j = JSON.parse(raw);
      ts = j && j[fb.tsField];
    } catch { /* not JSON */ }
  } else {
    ts = raw;
  }
  if (ts == null) return { ok: false };
  const tsMs = parseTimestamp(ts, fb.tsFormat);
  if (!tsMs) return { ok: false };
  const ageSec = (Date.now() - tsMs) / 1000;
  return { ok: ageSec <= (fb.maxAgeSec || 180), ageSec, key: fb.key };
}

async function checkPM2Processes() {
  try {
    const { stdout } = await execAsync('pm2 jlist', { timeout: 10000, maxBuffer: 10 * 1024 * 1024 });
    const procs = JSON.parse(stdout);
    const supervisorStateRaw = await redis.get(DEFAULT_SUPERVISOR_PM2_CONTRACT.stateKey).catch(() => null);
    return await assessRequiredPm2Processes({
      apps: procs,
      required: CONFIG.requiredProcesses,
      heartbeatFreshFor: _heartbeatFreshFor,
      supervisorState: supervisorStateRaw,
      graceState: STATE.pm2RecentFailSince,
      graceMs: CONFIG.pm2GraceMs || 0,
    });
  } catch (err) {
    // [PART5-L3] prefix 객체 지원 추가
    const flat = CONFIG.requiredProcesses.map(r => {
      if (typeof r === 'string') return r;
      if (Array.isArray(r)) return r.join('|');
      if (r && typeof r === 'object' && typeof r.prefix === 'string') return r.prefix + '*';
      return String(r);
    });
    return { pass: false, ok: false, detail: flat, missing: flat, online: 0, error: err.message };
  }
}

async function checkRequiredKeys() {
  const missing = [];
  // [PART6-B3-UNIFIED] 2026-04-25: string / {key,type,fallback} 두 형식 모두 지원.
  for (const entry of CONFIG.requiredKeys) {
    let ok = false;
    let label;
    if (typeof entry === 'string') {
      label = entry;
      const tp = await redis.type(entry).catch(() => 'none');
      if (tp === 'hash') { ok = (await redis.hlen(entry).catch(() => 0)) > 0; }
      else if (tp !== 'none') { const v = await redis.get(entry).catch(() => null); ok = !!v; }
    } else if (entry && typeof entry === 'object' && entry.key) {
      label = entry.key;
      const tp = await redis.type(entry.key).catch(() => 'none');
      const expect = entry.type;
      if (expect === 'hash' || (!expect && tp === 'hash')) {
        ok = (await redis.hlen(entry.key).catch(() => 0)) > 0;
      } else if (tp !== 'none') {
        const v = await redis.get(entry.key).catch(() => null);
        ok = !!v;
      }
      // primary missing → fallback 시도
      if (!ok && entry.fallback) {
        const fbTp = await redis.type(entry.fallback).catch(() => 'none');
        if (fbTp === 'hash') { ok = (await redis.hlen(entry.fallback).catch(() => 0)) > 0; }
        else if (fbTp !== 'none') { const v = await redis.get(entry.fallback).catch(() => null); ok = !!v; }
        if (ok) log('WARN', 'REQUIRED_KEY_FALLBACK', { primary: entry.key, fallback: entry.fallback, action: 'USING_FALLBACK' });
      }
    } else {
      label = String(entry);
    }
    if (!ok) missing.push(label);
  }

  // FIX-S3: Positions key check with broker fallback
  const posCheck = CONFIG.positionsKeyCheck;
  if (posCheck) {
    const primaryVal = await redis.get(posCheck.primary).catch(() => null);
    if (!primaryVal) {
      // Primary missing — try fallback
      let fallbackOk = false;
      if (posCheck.fallbackType === 'hash') {
        const hlen = await redis.hlen(posCheck.fallback).catch(() => 0);
        fallbackOk = hlen >= (posCheck.minCount || 1);
        if (fallbackOk) {
          log('WARN', 'POSITIONS_KEY_FALLBACK', {
            primary: posCheck.primary,
            primary_status: 'MISSING',
            fallback: posCheck.fallback,
            fallback_count: hlen,
            action: 'USING_BROKER_FALLBACK',
          });
        }
      } else {
        const fbVal = await redis.get(posCheck.fallback).catch(() => null);
        fallbackOk = !!fbVal;
      }
      if (!fallbackOk) {
        missing.push(posCheck.primary);
      }
    }
  }

  return { pass: missing.length === 0, detail: missing.length > 0 ? missing : [] };
}

/**
 * [V2 핵심 변경] freshness 기반 판정
 *
 * 기존: price:SPY 키가 존재하는지 확인 → TTL 만료 시 FAIL
 * 변경: price:SPY:updated_at 타임스탬프가 maxAgeSec 이내인지 확인
 *       키 자체가 TTL로 만료되어도 updated_at이 유효하면 PASS
 *
 * 추가로, updated_at 키가 없으면 price 키의 TTL을 fallback으로 사용
 */
async function checkFreshness() {
  const stale = [];
  const details = {};

  for (const [dataKey, cfg] of Object.entries(CONFIG.freshnessChecks)) {
    let fresh = false;
    let ageInfo = '';

    // 1차: updated_at 타임스탬프 기반 판정
    let tsVal = await redis.get(cfg.tsKey);
    // [PATCH-P5] cfg.tsField가 지정되면 tsVal을 JSON으로 파싱해 그 필드 추출
    if (tsVal && cfg.tsField) {
      try {
        const obj = JSON.parse(tsVal);
        const v = obj[cfg.tsField];
        if (v != null) tsVal = String(v);
        else tsVal = null;
      } catch (e) {
        // not JSON; leave as-is
      }
    }
    if (tsVal) {
      const tsMs = parseTimestamp(tsVal);
      if (tsMs && !isNaN(tsMs)) {
        const ageSec = (Date.now() - tsMs) / 1000;
        if (ageSec >= 0 && ageSec <= cfg.maxAgeSec) {
          fresh = true;
          ageInfo = `${Math.round(ageSec)}s (via updated_at)`;
        } else if (ageSec > cfg.maxAgeSec) {
          ageInfo = `stale: ${Math.round(ageSec)}s > ${cfg.maxAgeSec}s`;
        }
      }
    }

    // 2차 fallback: 키 자체의 TTL 기반 판정
    if (!fresh) {
      const ttl = await redis.ttl(dataKey);
      if (ttl > 0) {
        // TTL이 남아있으면 키가 존재하고 비교적 최근 갱신됨
        fresh = true;
        ageInfo = `TTL ${ttl}s remaining (fallback)`;
      } else if (ttl === -1) {
        // TTL 없음 (영속 키) → 키 존재 확인
        const exists = await redis.exists(dataKey);
        if (exists) {
          fresh = true;
          ageInfo = 'persistent key (no TTL)';
        }
      }
      // ttl === -2: 키 없음 → fresh = false 유지
    }

    // 3차 fallback: 키의 :ts 변형 확인 (레거시 호환)
    if (!fresh) {
      const legacyTs = await redis.get(`${dataKey}:ts`);
      if (legacyTs) {
        const tsMs = parseTimestamp(legacyTs);
        if (tsMs && !isNaN(tsMs)) {
          const ageSec = (Date.now() - tsMs) / 1000;
          if (ageSec >= 0 && ageSec <= cfg.maxAgeSec) {
            fresh = true;
            ageInfo = `${Math.round(ageSec)}s (via :ts legacy)`;
          }
        }
      }
    }

    details[dataKey] = { fresh, info: ageInfo, critical: cfg.critical };
    if (!fresh) {
      stale.push({ key: dataKey, info: ageInfo, critical: cfg.critical });
    }
  }

  // critical 키가 하나라도 stale이면 FAIL
  const criticalStale = stale.filter(s => s.critical);
  return {
    pass: criticalStale.length === 0,
    detail: stale.length > 0 ? stale : [],
    allDetails: details,
  };
}

async function checkKISToken() {
  // [PART6-B3-UNIFIED] 2026-04-25: 1순위는 kis_token_resolver.mjs::judgeKisToken()
  // (4-source fallback + 60s soft-warn grace, race-window NO-GO 억제).
  // 실패 시 기존 inline 4단계 fallback으로 안전망 제공.
  try {
    const t = await judgeKisToken(redis);
    if (t && t.verdict === 'GO')   return { pass: true,  detail: `resolver: ${t.reason}` };
    if (t && t.verdict === 'WARN') return { pass: true,  detail: `resolver WARN: ${t.reason}`, warn: true };
    if (t && t.verdict === 'NOGO') {
      log('WARN', 'kis_token_resolver returned NOGO, consulting inline fallback', { reason: t.reason });
      // fall-through to inline
    }
  } catch (err) {
    log('WARN', 'kis_token_resolver failed, consulting inline fallback', { error: err?.message });
  }
  // [INLINE FALLBACK] 기존 v2.1 4단 로직 (resolver 장애 시 안전망)
  try {
    // [v2.1] 1차: canonical 키에서 expires_at_ms 읽기 (SSOT — 재발 방지)
    const canonicalRaw = await redis.get('kis:token:canonical');
    if (canonicalRaw) {
      try {
        const canonical = JSON.parse(canonicalRaw);
        const expiresMs = canonical.expires_at_ms;
        if (expiresMs) {
          const remaining = (Number(expiresMs) - Date.now()) / 1000;
          return { pass: remaining > 600, detail: `Expires in ${Math.round(remaining)}s (canonical)` };
        }
      } catch (_parseErr) {
        // JSON parse 실패 시 fallback으로 진행
      }
    }

    // 2차: 레거시 kis:auth:token_expires_at (하위 호환)
    const expiresAtMs = await redis.get('kis:auth:token_expires_at');
    if (expiresAtMs) {
      const remaining = (Number(expiresAtMs) - Date.now()) / 1000;
      return { pass: remaining > 600, detail: `Expires in ${Math.round(remaining)}s (legacy)` };
    }

    // 3차: kis:token:access TTL fallback
    const ttl = await redis.ttl('kis:token:access');
    if (ttl > 0) {
      return { pass: ttl > 600, detail: `Token TTL: ${ttl}s` };
    }

    // 4차: kis:token:access_token TTL fallback
    const ttl2 = await redis.ttl('kis:token:access_token');
    if (ttl2 > 0) {
      return { pass: ttl2 > 600, detail: `Token TTL: ${ttl2}s (access_token)` };
    }

    return { pass: false, detail: 'No KIS token expiry info (all sources checked)' };
  } catch (err) {
    return { pass: false, detail: err.message };
  }
}

// ============================================================
// 유틸리티
// ============================================================
function parseTimestamp(val) {
  if (!val) return null;
  const trimmed = String(val).trim();
  if (!trimmed) return null;

  const num = Number(trimmed);
  if (isNaN(num)) {
    // ISO 문자열
    const ms = new Date(trimmed).getTime();
    return isNaN(ms) ? null : ms;
  } else if (num > 1e12) {
    return num; // epoch ms
  } else if (num > 1e9) {
    return num * 1000; // epoch seconds → ms
  }
  return null; // 비정상 값
}

// ============================================================
// 히스테리시스 verdict 결정
// ============================================================
function applyHysteresis(rawVerdict) {
  if (rawVerdict === "GO") {
    STATE.consecutiveGo++;
    STATE.consecutiveNoGo = 0;

    if (STATE.consecutiveGo >= CONFIG.hysteresis.goThreshold) {
      STATE.confirmedVerdict = 'GO';
    }
  } else if (rawVerdict === "DEGRADED") {
    STATE.consecutiveDegraded++;
    STATE.consecutiveGo = 0;
    STATE.consecutiveNoGo = 0;
    if (STATE.consecutiveDegraded >= (CONFIG.hysteresis.degradedThreshold || 2)) {
      STATE.confirmedVerdict = "DEGRADED";
    }
  } else {
    STATE.consecutiveNoGo++;
    STATE.consecutiveGo = 0;

    if (STATE.consecutiveNoGo >= CONFIG.hysteresis.noGoThreshold) {
      STATE.confirmedVerdict = 'NO-GO';
    }
    // threshold 미달이면 이전 confirmedVerdict 유지
  }

  return STATE.confirmedVerdict;
}

// ============================================================
// 메인 체크 사이클
// ============================================================
async function runCheck() {
  STATE.checkCount++;
  STATE.lastCheckTs = new Date().toISOString();

  // [V2] 장외 시간 → 자동 GO (체크 스킵)
  if (!isActiveCheckWindow()) {
    const prevVerdict = STATE.confirmedVerdict;
    STATE.confirmedVerdict = 'GO';
    STATE.consecutiveNoGo = 0;
    STATE.consecutiveGo = CONFIG.hysteresis.goThreshold; // 즉시 GO 확정

    // 장외 시간 verdict 기록 (kill-switch가 읽을 수 있도록)
    const verdict = {
      result: 'GO',
      ts: new Date().toISOString(),
      reason: 'OFF_HOURS_AUTO_GO',
      checks: {},
      failures: [],
      hysteresis: { consecutiveGo: STATE.consecutiveGo, consecutiveNoGo: 0, confirmed: 'GO' },
    };
    await redis.set('ares:go_nogo:verdict', JSON.stringify(verdict), 'EX', 86400);
    // STRUCTURAL-FIX-2026-04-30: mirror canonical Go/No-Go verdict to legacy observability keys.
    // Canonical authority remains ares:go_nogo:verdict; aliases are TTL-bound and overwritten each cycle.
    await redis.set('go-nogo:status', String(verdict.result || confirmedVerdict || verdict.rawResult || 'UNKNOWN'), 'EX', 180).catch(() => {});
    await redis.set('go-nogo:last', JSON.stringify(verdict), 'EX', 180).catch(() => {});
    await redis.publish("ares:go-nogo:verdicts", JSON.stringify(verdict)).catch(() => {});

    // 장외 시간 진입 시 한 번만 로그
    if (prevVerdict !== 'GO' || STATE.checkCount === 1) {
      log('INFO', 'Off-hours: auto GO', { et: getETNow() });
    }
    return;
  }

  log('INFO', '=== Go/No-Go Check Started ===', { cycle: STATE.checkCount });

  // 5가지 체크 실행
  const results = {
    redis: await checkRedis(),
    pm2: await checkPM2Processes(),
    keys: await checkRequiredKeys(),
    freshness: await checkFreshness(),
    kisToken: await checkKISToken(),
  };

  const rawPass = Object.values(results).every(r => r.pass);

  // [V3] 3-tier verdict: GO / DEGRADED / NO-GO
  const nonPricePass = Object.entries(results).filter(([k]) => k !== "freshness").every(([, r]) => r.pass);
  const rawVerdict = rawPass ? "GO" : (nonPricePass && !results.freshness?.pass) ? "DEGRADED" : "NO-GO";

  // [V2 핵심] 히스테리시스 적용
  const confirmedVerdict = applyHysteresis(rawVerdict);

  log('INFO', 'Go/No-Go Results', {
    rawVerdict,
    confirmedVerdict,
    hysteresis: {
      consecutiveGo: STATE.consecutiveGo,
      consecutiveNoGo: STATE.consecutiveNoGo,
      consecutiveDegraded: STATE.consecutiveDegraded,
      threshold: CONFIG.hysteresis.noGoThreshold,
    },
    results: Object.fromEntries(
      Object.entries(results).map(([k, v]) => [k, v.pass ? 'PASS' : 'FAIL'])
    ),
    degradedMode: confirmedVerdict === 'DEGRADED' ? 'ACTIVE — hold positions, block new buys' : 'INACTIVE',
  });

  // verdict 기록 (확정된 verdict만 기록)
  const verdict = {
    result: confirmedVerdict,
    ts: new Date().toISOString(),
    rawResult: rawVerdict,
    degradedMode: confirmedVerdict === 'DEGRADED',
    checks: Object.fromEntries(
      Object.entries(results).map(([k, v]) => [k, { pass: v.pass, detail: v.detail }])
    ),
    failures: Object.entries(results)
      .filter(([, r]) => !r.pass)
      .map(([k, v]) => ({ check: k, ...v })),
    hysteresis: {
      consecutiveGo: STATE.consecutiveGo,
      consecutiveNoGo: STATE.consecutiveNoGo,
      confirmed: confirmedVerdict,
    },
  };

  await redis.set('ares:go_nogo:verdict', JSON.stringify(verdict), 'EX', 86400);
    // STRUCTURAL-FIX-2026-04-30: mirror canonical Go/No-Go verdict to legacy observability keys.
    // Canonical authority remains ares:go_nogo:verdict; aliases are TTL-bound and overwritten each cycle.
    await redis.set('go-nogo:status', String(verdict.result || confirmedVerdict || verdict.rawResult || 'UNKNOWN'), 'EX', 180).catch(() => {});
    await redis.set('go-nogo:last', JSON.stringify(verdict), 'EX', 180).catch(() => {});
    await redis.publish("ares:go-nogo:verdicts", JSON.stringify(verdict)).catch(() => {});

  // 텔레그램 알림 (확정된 verdict 기준, 이중 dedup)
  if (confirmedVerdict === 'GO') {
    log('INFO', `VERDICT: ${confirmedVerdict} (confirmed)`, { raw: rawVerdict });
    await sendTelegramIfChanged('GO',
      `✅ ARES Go/No-Go: GO\n` +
      `All ${Object.keys(results).length} checks passed\n` +
      `Verdict written to ares:go_nogo:verdict\n` +
      `(kill-switch-authority will set trading:enabled = true)`
    );
  } else if (confirmedVerdict === 'DEGRADED') {
    const staleKeys = Object.entries(results).filter(([, r]) => !r.pass);
    log('WARN', `VERDICT: DEGRADED — hold positions, block new buys`, {
      raw: rawVerdict,
      staleChecks: staleKeys.map(([k, v]) => ({ check: k, detail: v.detail })),
    });
    await sendTelegramIfChanged('DEGRADED',
      `🟡 ARES Go/No-Go: DEGRADED\n` +
      `Price freshness failed but other checks OK\n` +
      `Action: HOLD existing positions, BLOCK new buys\n` +
      `Stale: ${staleKeys.map(([k]) => k).join(', ')}\n` +
      `Will auto-recover to GO when prices refresh`
    );
  } else {
    const failures = Object.entries(results).filter(([, r]) => !r.pass);
    log('WARN', `VERDICT: ${confirmedVerdict} (confirmed after ${STATE.consecutiveNoGo} consecutive NO-GO)`, {
      raw: rawPass ? 'GO' : 'NO-GO',
      failures: failures.map(([k, v]) => ({ check: k, detail: v.detail })),
    });
    await sendTelegramIfChanged('NO-GO',
      `🔴 ARES Go/No-Go: NO-GO (confirmed: ${STATE.consecutiveNoGo}/${CONFIG.hysteresis.noGoThreshold} consecutive)\n` +
      `Failed: ${failures.map(([k]) => k).join(', ')}\n` +
      `Details: ${JSON.stringify(failures.map(([k, v]) => ({ [k]: v.detail })))}\n` +
      `Verdict written to ares:go_nogo:verdict\n` +
      `(kill-switch-authority will set trading:enabled = false)`
    );
  }

  // 데몬 상태 heartbeat
  await redis.set('go_nogo_judge:heartbeat', JSON.stringify({
    ts: new Date().toISOString(),
    checkCount: STATE.checkCount,
    confirmedVerdict: STATE.confirmedVerdict,
    consecutiveGo: STATE.consecutiveGo,
    consecutiveNoGo: STATE.consecutiveNoGo,
    consecutiveDegraded: STATE.consecutiveDegraded,
    degradedMode: STATE.confirmedVerdict === 'DEGRADED',
    startedAt: STATE.startedAt,
  }), 'EX', 300);
}

// ============================================================
// 메인 (데몬 모드)
// ============================================================
async function main() {
  initRedis();
  // [MUSK-FIX-001] Safe connect: only if not already connected
  if (redis.status !== "ready" && redis.status !== "connecting" && redis.status !== "connect") {
    await redis.connect();
  } else {
    // Already connected or connecting — wait for ready
    if (redis.status !== "ready") {
      await new Promise((resolve, reject) => {
        redis.once("ready", resolve);
        redis.once("error", reject);
        setTimeout(() => reject(new Error("Redis connect timeout")), 15000);
      });
    }
  }

  log('INFO', 'Go/No-Go Judge v2.2-unified-B3 started (daemon mode)', {
    checkInterval: `${CONFIG.checkIntervalMs / 1000}s`,
    hysteresis: CONFIG.hysteresis,
    requiredProcesses: CONFIG.requiredProcesses,
    freshnessChecks: Object.keys(CONFIG.freshnessChecks),
    marketHours: CONFIG.marketHours,
  });

  // 초기화: Redis에서 이전 dedup 상태 복원
  try {
    const lastNotified = await redis.get(VERDICT_DEDUP_KEY);
    if (lastNotified) {
      STATE.lastNotifiedResult = lastNotified;
      log('INFO', 'Restored dedup state from Redis', { lastNotified });
    }
  } catch (err) {
    log('WARN', 'Failed to restore dedup state', { error: err.message });
  }

  // 초기 실행
  await runCheck();

  // Safe sequential pattern (setInterval 대신 — 이전 체크 완료 후 다음 스케줄)
  const scheduleNext = () => {
    setTimeout(async () => {
      try {
        await runCheck();
      } catch (err) {
        log('ERROR', 'Check cycle failed', { error: err.message, stack: err.stack });
      }
      scheduleNext();
    }, CONFIG.checkIntervalMs);
  };
  scheduleNext();
}

// ============================================================
// PATCH-GNG-003 (2026-04-21): hot-reload of tunables via SIGHUP and at
// boot. Reads recommended tunables from Redis without restarting the
// process. Two sources are tried in order:
//   1) ares:gng:tunables:override   (operator-set, JSON object)
//   2) ares:hysteresis:report       (auto-derived weekly by
//      hysteresis_analyzer.py; field path: .recommendations)
// Recognized fields (all optional; clamped to safe ranges):
//   pm2GraceMs                       -> CONFIG.pm2GraceMs       [15000..600000]
//   hysteresis.noGoThreshold         -> CONFIG.hysteresis.noGoThreshold [1..10]
//   hysteresis.goThreshold           -> CONFIG.hysteresis.goThreshold   [1..10]
//   hysteresis.degradedThreshold     -> CONFIG.hysteresis.degradedThreshold [1..10]
//   checkIntervalMs                  -> CONFIG.checkIntervalMs  [10000..600000]
// After applying, publishes the active tunables JSON to
//   ares:gng:config:active          (no TTL)
// and an audit entry to
//   ares:gng:config:audit           (Redis stream, MAXLEN ~1k)
function _clamp(v, lo, hi) { v = Number(v); return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : null; }

async function loadTunablesFromRedis(reason = 'manual') {
  const applied = {};
  const sources = ['ares:gng:tunables:override', 'ares:hysteresis:report'];
  let chosen = null;
  let raw = null;
  try {
    for (const k of sources) {
      const v = await redis.get(k);
      if (v) { chosen = k; raw = v; break; }
    }
    if (!raw) {
      log('INFO', 'Tunable hot-reload: no source key present; keeping defaults', { reason });
      return;
    }
    let payload;
    try { payload = JSON.parse(raw); }
    catch (e) { log('WARN', 'Tunable hot-reload: source not JSON', { source: chosen, error: e.message }); return; }
    // recommendations may be nested under .recommendations
    const rec = payload.recommendations && typeof payload.recommendations === 'object' ? payload.recommendations : payload;

    if (rec.pm2GraceMs !== undefined) {
      const v = _clamp(rec.pm2GraceMs, 15_000, 600_000);
      if (v !== null) { CONFIG.pm2GraceMs = v; applied.pm2GraceMs = v; }
    }
    if (rec.checkIntervalMs !== undefined) {
      const v = _clamp(rec.checkIntervalMs, 10_000, 600_000);
      if (v !== null) { CONFIG.checkIntervalMs = v; applied.checkIntervalMs = v; }
    }
    const hys = rec.hysteresis || {};
    for (const f of ['noGoThreshold', 'goThreshold', 'degradedThreshold']) {
      if (hys[f] !== undefined) {
        const v = _clamp(hys[f], 1, 10);
        if (v !== null) { CONFIG.hysteresis[f] = v; applied[`hysteresis.${f}`] = v; }
      }
    }

    const active = {
      ts: new Date().toISOString(),
      reason,
      source: chosen,
      applied,
      effective: {
        pm2GraceMs: CONFIG.pm2GraceMs,
        checkIntervalMs: CONFIG.checkIntervalMs,
        hysteresis: { ...CONFIG.hysteresis },
      },
    };
    await redis.set('ares:gng:config:active', JSON.stringify(active));
    try {
      await redis.xadd('ares:gng:config:audit', 'MAXLEN', '~', '1000', '*',
        'ts', active.ts, 'reason', reason, 'source', chosen,
        'applied', JSON.stringify(applied), 'effective', JSON.stringify(active.effective));
    } catch (_) {}
    log('INFO', 'Tunable hot-reload applied', active);
  } catch (e) {
    log('ERROR', 'Tunable hot-reload failed', { error: e.message, reason });
  }
}

// SIGHUP: hot-reload tunables without restart.
process.on('SIGHUP', () => {
  log('INFO', 'SIGHUP received — reloading tunables');
  loadTunablesFromRedis('SIGHUP').catch(() => {});
});

// At boot, attempt to load (best-effort; non-fatal if unavailable).
setTimeout(() => { loadTunablesFromRedis('boot').catch(() => {}); }, 5_000);

// Graceful shutdown
process.on('SIGTERM', async () => {
  log('INFO', 'Shutting down gracefully (SIGTERM)');
  try { await redis.quit(); } catch (_) {}
  process.exit(0);
});

process.on('SIGINT', async () => {
  log('INFO', 'Shutting down gracefully (SIGINT)');
  try { await redis.quit(); } catch (_) {}
  process.exit(0);
});

main().catch(async (err) => {
  // [MUSK-FIX-001] Cleanup stale redis before retry
  log('CRITICAL', 'Go/No-Go Judge v2 crashed — retrying in 30s', { error: err.message, stack: err.stack });
  try { if (redis) { redis.disconnect(); redis = null; } } catch(_) {}
  await new Promise(r => setTimeout(r, 30000));
  initRedis();  main().catch(e2 => log('CRITICAL', 'Retry also failed', { error: e2?.message }));
});
