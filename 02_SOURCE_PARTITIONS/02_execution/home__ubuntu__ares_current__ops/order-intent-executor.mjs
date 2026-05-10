#!/usr/bin/env node
/**
 * Order Intent Executor (AOPS_EVT instrumented)
 * order_intent 스트림을 읽어서 KIS API로 실제 주문 실행
 *
 * [AOPS_EVT] 이벤트 삽입 v1:
 *  - order_state: SSOT(order:open:*) 쓰기 성공 직후 (OPEN/CLOSED 상태 전이)
 *  - retry_start/retry_success/retry_giveup: KIS API 호출 (토큰 발급 + 주문 실행)
 *
 * [AOPS_EVT v2] dep_call 이벤트 (depCall 래퍼 기반):
 *  - dep_call_start/dep_call_ok/dep_call_fail: KIS 토큰 발급 + 주문 API 실제 호출 증거
 *  - depCall() 래퍼로 표준화: Retry Expectation Guard가 증명 기반으로 전환
 *
 * [PATCH] 실시간 안정성 패치 적용 (2026-02-08)
 *  1. 멱등성 선점 (SET NX EX) — 크래시/재시작 중복주문 방지
 *  2. ACK 책임 단일화 — processIntent는 ACK하지 않음, 루프가 단일 책임
 *  3. KIS 토큰 캐시 키 정합화 (JSON + legacy 동시 호환)
 *  4. HTTPS 요청 timeout 방어
 *  5. tick 기반 소수점 처리
 *  6. withOrderLock 실제 적용
 *  7. trade halt guard + DLQ routing (policy:trade:halt)
 */
import Redis from "ioredis";
import { safeInt, safeFloat } from "../lib/patches/PATCH-005-safe-parse.mjs";
import https from "https";
// [PATCH-P0] HTTPS Keep-Alive agent for KIS API latency reduction
const kisKeepAliveAgent = new (await import('https')).Agent({ keepAlive: true, maxSockets: 6, keepAliveMsecs: 30000 });

import { withOrderLock } from "./shared/order_lock.mjs";
import { assertProcessEnvironment } from "./shared/env-isolation-guard.mjs";
import { emitEvent } from "./event_logger.mjs";
import { depCall, newRequestId } from "./dep_call_wrapper.mjs";
import { validateIntent } from "./preflight-validator.mjs";  // PATCH-13
import { validateStageDBSeriesIntent, STAGE_D_GUARD_LOADED, STAGE_D_SCHEMA_VALIDATION_PASS, STAGE_D_ROUTE_DETERMINISTIC, STAGE_D_REJECT_MALFORMED_INTENT } from "./stage_d_intent_route_guard.mjs";  // Stage D validation-only
// [L8++] Order Flow Governor gate check
import { checkOFGGate, recordOFGMetric } from "../ares_work/ofg_gate_check.mjs";
import { loadCanonicalKisToken, publishCanonicalKisToken } from "./lib/kis_token_ssot.mjs";

// ─── Paced Drain (v3.2 structural fix) ─────────────────────────
// During rebalance mode, enforce minimum inter-order spacing to stay
// under OFG rate limits (5/sec) without triggering SELL_STORM or HALT.
const PACED_DRAIN_INTERVAL_MS = 250;  // 4/sec max → stays under 5/sec limit
let _lastOrderTs = 0;

// ═══ [STRUCTURAL-FIX-v4] Session-based metrics:drop_reasons reset ═══
// At market open (first intent of the day), archive previous session's
// metrics and reset the counter. This prevents stale cumulative counts
// from triggering permanent alerts.
let _metricsSessionResetDone = false;
async function ensureMetricsSessionReset(redisConn) {
    if (_metricsSessionResetDone) return;
    _metricsSessionResetDone = true;
    try {
        const today = new Date().toISOString().slice(0, 10);
        const resetKey = "metrics:drop_reasons:reset_date";
        const lastReset = await redisConn.get(resetKey).catch(() => null);
        
        if (lastReset === today) return; // Already reset today
        
        // Archive current metrics
        const archiveKey = `metrics:drop_reasons:archive:${lastReset || "unknown"}`;
        const currentMetrics = await redisConn.hgetall("metrics:drop_reasons").catch(() => null);
        if (currentMetrics && Object.keys(currentMetrics).length > 0) {
            await redisConn.hmset(archiveKey, currentMetrics).catch(() => {});
            await redisConn.expire(archiveKey, 7 * 86400).catch(() => {}); // 7-day retention
        }
        
        // Reset metrics
        await redisConn.del("metrics:drop_reasons").catch(() => {});
        await redisConn.set(resetKey, today, "EX", 86400 * 2).catch(() => {}); // 2-day TTL
        
        console.log(`[STRUCTURAL-FIX-v4] metrics:drop_reasons reset for session ${today} (archived ${lastReset || "none"})`);
    } catch (e) {
        console.error(`[STRUCTURAL-FIX-v4] metrics reset error: ${e.message}`);
    }
}
// ═══ END [STRUCTURAL-FIX-v4] ═══


async function pacedDrainWait(redis, intentId) {
  try {
    const profile = await getUrgencyProfile(redis);
    const waitTarget = Number(profile.pacingMs || PACED_DRAIN_INTERVAL_MS);
    const now = Date.now();
    const elapsed = now - _lastOrderTs;
    if (elapsed < waitTarget) {
      const waitMs = waitTarget - elapsed;
      log("DEBUG", "[PACED_DRAIN] waiting", { intent_id: intentId, wait_ms: waitMs, urgency: profile.mode });
      await new Promise(r => setTimeout(r, waitMs));
    }
    _lastOrderTs = Date.now();
  } catch (e) {
    // Non-critical, proceed without pacing
  }
}

// === AOPS_EVT PROC NAME ===

// R14 FIX: KIS API rate limiting (max 5 calls per second)
async function kisRateLimit(redis) {
  const key = "kis:api:rate_limit";
  const count = await redis.incr(key);
  if (count === 1) await redis.expire(key, 1);
  if (count > 5) {
    const delay = 1000;
    await new Promise(r => setTimeout(r, delay));
  }
}

const PROC = process.env.PM2_PROCESS_NAME || "order-intent-executor";
const CONSUMER_HEARTBEAT_KEY = "ares:v55:consumer:heartbeat";
const CONSUMER_LAST_ACK_KEY = "ares:v55:consumer:last_ack";
const EXECUTION_ACK_PREFIX = "ares:v55:execution:ack";
const EXECUTION_ACK_TTL_SEC = Number(process.env.EXECUTION_ACK_TTL_SEC || 300);
const CONSUMER_HEARTBEAT_TTL_SEC = Number(process.env.CONSUMER_HEARTBEAT_TTL_SEC || 120);
const STALE_PENDING_MIN_IDLE_MS = Number(process.env.STALE_PENDING_MIN_IDLE_MS || 30000);
const STALE_PENDING_CLAIM_COUNT = Number(process.env.STALE_PENDING_CLAIM_COUNT || 50);

const redis = getRedis();  // [v2.0] 싱글톤 통일
// === Risk-On minimal safe apply (freq_multiplier) ===
const RISK_ON_ENABLED_KEY = 'policy:risk_on:enabled';
const RISK_ON_FREQ_MULT_KEY = 'policy:risk_on:freq_multiplier';
const RISK_ON_FREQ_CAP = 1.1; // hard cap for safety

async function getRiskOnFreqMultiplier(redisClient) {
  try {
    const [en, mult] = await redisClient.mget(RISK_ON_ENABLED_KEY, RISK_ON_FREQ_MULT_KEY);
    if (String(en).toLowerCase() !== 'true') return 1.0;
    const m = Number(mult);
    if (!Number.isFinite(m) || m <= 0) return 1.0;
    return Math.min(m, RISK_ON_FREQ_CAP);
  } catch {
    return 1.0;
  }
}

function applyQtyMultiplierSafe(qty, mult) {
  const q = Number(qty);
  if (!Number.isFinite(q) || q === 0) return qty;
  const m = Number(mult);
  if (!Number.isFinite(m) || m <= 0 || m === 1) return qty;
  const scaled = q * m;
  // If qty is integer-like, keep integer floor for safety
  if (Number.isInteger(q)) return Math.max(0, Math.floor(scaled));
  return Math.max(0, scaled);
}

// === Risk-On global daily uplift cap (portfolio-level) ===
// 목표: 개별 주문은 최대 10% 증액(riskOnMult<=1.1) 가능하되,
//      하루 전체 '추가 노치널(증액분)'은 기본 예산 대비 cap_ratio(예: 3%)를 넘지 않도록 제한
const DAILY_UPLIFT_USED_KEY_PREFIX = 'risk_on:uplift_used'; // suffix: YYYY-MM-DD
const DAILY_UPLIFT_CAP_RATIO_KEY = 'policy:risk_on:daily_uplift_cap_ratio'; // default 0.03
const DAILY_NOTIONAL_BUDGET_KEY = 'policy:trade:daily_notional_budget_usd'; // required for ratio mode
const DAILY_UPLIFT_CAP_USD_KEY = 'policy:risk_on:daily_uplift_cap_usd'; // optional override (absolute)
const DAILY_UPLIFT_KEY_TTL_SEC = 3 * 24 * 3600;

function todayUTCDateStr() {
  return new Date().toISOString().slice(0, 10);
}

// === Mid price fallback (Redis) for risk-on global cap ===
// If intent has no usable price fields, try Redis mid price with staleness guard.
const MID_PRICE_KEY_PREFIX = process.env.MID_PRICE_KEY_PREFIX || 'md:mid'; // key = <prefix>:<SYMBOL>
const MID_PRICE_MAX_AGE_MS = Number(process.env.MID_PRICE_MAX_AGE_MS || 30000); // 30s
const ALLOW_PRICE_WITHOUT_TS = (process.env.ALLOW_PRICE_WITHOUT_TS || 'false').toLowerCase() === 'true';

async function getMidPriceFromRedis(redisClient, symbol) {
  if (!symbol) return { ok: false, reason: 'missing_symbol' };
  const key = `${MID_PRICE_KEY_PREFIX}:${symbol}`;
  const raw = await redisClient.get(key).catch(() => null);
  if (!raw) return { ok: false, reason: 'missing_mid_key', key };
  let obj = null;
  try { obj = JSON.parse(raw); } catch { obj = null; }
  if (obj && typeof obj === 'object') {
    const p = Number(obj.price ?? obj.mid ?? obj.value);
    const tsRaw = obj.ts ?? obj.timestamp ?? obj.time ?? obj.updated_at;
    let tsMs = null;
    if (typeof tsRaw === 'number') tsMs = tsRaw > 1e12 ? tsRaw : tsRaw * 1000;
    else if (typeof tsRaw === 'string') {
      const t = Date.parse(tsRaw);
      if (Number.isFinite(t)) tsMs = t;
      else if (/^\d+$/.test(tsRaw)) {
        const n = Number(tsRaw);
        if (Number.isFinite(n)) tsMs = n > 1e12 ? n : n * 1000;
      }
    }
    if (!Number.isFinite(p) || p <= 0) return { ok: false, reason: 'invalid_mid_price', key, raw };
    if (!tsMs) {
      if (!ALLOW_PRICE_WITHOUT_TS) return { ok: false, reason: 'missing_ts', key };
      return { ok: true, price: p, tsMs: null, key, note: 'no_ts_allowed' };
    }
    const age = Date.now() - tsMs;
    if (age > MID_PRICE_MAX_AGE_MS) return { ok: false, reason: 'stale_mid_price', key, age_ms: age };
    return { ok: true, price: p, tsMs, key, age_ms: age };
  }
  // plain string number
  const p = Number(raw);
  if (!Number.isFinite(p) || p <= 0) return { ok: false, reason: 'invalid_mid_raw', key };
  if (!ALLOW_PRICE_WITHOUT_TS) return { ok: false, reason: 'plain_price_no_ts_blocked', key };
  return { ok: true, price: p, tsMs: null, key, note: 'plain_price_allowed' };
}

function pickPriceFromIntent(intent) {
  const candidates = [intent?.price, intent?.limit_price, intent?.limitPrice, intent?.mid_price, intent?.midPrice, intent?.ref_price, intent?.refPrice, intent?.last_price];
  for (const c of candidates) {
    const p = Number(c);
    if (Number.isFinite(p) && p > 0) return p;
  }
  return null;
}


// [4AI-PATCH-5] Spread info lookup (bid/ask -> spreadBps)
async function getSpreadInfo(redisClient, symbol) {
  try {
    if (!symbol) return { spreadBps: 0, bid: 0, ask: 0 };
    const [bidRaw, askRaw] = await Promise.all([
      redisClient.get(`md:bid:${symbol}`).catch(() => null),
      redisClient.get(`md:ask:${symbol}`).catch(() => null)
    ]);
    if (!bidRaw || !askRaw) {
      const quoteRaw = await redisClient.get(`md:quote:${symbol}`).catch(() => null);
      if (quoteRaw) {
        try {
          const q = JSON.parse(quoteRaw);
          const bid = Number(q.bid ?? q.bidPrice ?? 0);
          const ask = Number(q.ask ?? q.askPrice ?? 0);
          if (bid > 0 && ask > 0) {
            const mid = (bid + ask) / 2;
            const spreadBps = mid > 0 ? ((ask - bid) / mid) * 10000 : 0;
            return { spreadBps, bid, ask };
          }
        } catch {}
      }
      return { spreadBps: 0, bid: 0, ask: 0 };
    }
    const bid = Number(bidRaw);
    const ask = Number(askRaw);
    if (bid > 0 && ask > 0) {
      const mid = (bid + ask) / 2;
      const spreadBps = mid > 0 ? ((ask - bid) / mid) * 10000 : 0;
      return { spreadBps, bid, ask };
    }
    return { spreadBps: 0, bid: 0, ask: 0 };
  } catch {
    return { spreadBps: 0, bid: 0, ask: 0 };
  }
}

async function getPriceForIntent(redisClient, intent) {
  const direct = pickPriceFromIntent(intent);
  if (direct) {
    const bidAsk = await getSpreadInfo(redisClient, intent?.symbol);
    return { ok: true, price: direct, source: 'intent', ...bidAsk };
  }
  const mid = await getMidPriceFromRedis(redisClient, intent?.symbol);
  if (!mid.ok) return { ok: false, source: 'redis_mid', ...mid };
  const bidAsk2 = await getSpreadInfo(redisClient, intent?.symbol);
  return { ok: true, price: mid.price, source: 'redis_mid', meta: mid, ...bidAsk2 };
}

// ============================================================
// PATCH-15: Exposure Gap — Urgency Profile + Cancel/Replace helpers
// ============================================================

async function getUrgencyProfile(redisClient) {
  const [modeRaw, replaceRaw, buyBpsRaw, sellBpsRaw, pacingNormalRaw, pacingAggRaw] = await Promise.all([
    redisClient.get("execution:urgency_mode"),
    redisClient.get("execution:replace_threshold_pct"),
    redisClient.get("execution:aggressive_buy_bps"),
    redisClient.get("execution:aggressive_sell_bps"),
    redisClient.get("execution:pacing_ms:normal"),
    redisClient.get("execution:pacing_ms:aggressive"),
  ]);
  const mode = String(modeRaw || "PASSIVE").toUpperCase();
  return {
    mode,
    replaceThresholdPct: Number(replaceRaw || 25),
    aggressiveBuyBps: Number(buyBpsRaw || 12),
    aggressiveSellBps: Number(sellBpsRaw || 18),
    pacingMs: mode === "AGGRESSIVE" ? Number(pacingAggRaw || 100) : Number(pacingNormalRaw || 250),
  };
}

async function loadOpenOrderForSymbol(redisClient, symbol, side) {
  const raw = await redisClient.get("emarkos:v1:open_orders").catch(() => null);
  if (!raw) return null;
  let snap;
  try { snap = JSON.parse(raw); } catch { return null; }
  const orders = Array.isArray(snap?.orders) ? snap.orders : [];
  return orders.find(
    (o) =>
      String(o.symbol || "").toUpperCase() === String(symbol || "").toUpperCase() &&
      String(o.side || "").toUpperCase() === String(side || "").toUpperCase() &&
      Number(o.remainingQty || 0) > 0
  ) || null;
}

function computeMarketableLimit(side, lastPx, profile) {
  const px = Number(lastPx || 0);
  if (!(px > 0)) return null;
  if (String(side).toUpperCase() === "BUY") {
    return +(px * (1 + profile.aggressiveBuyBps / 10000)).toFixed(2);
  }
  return +(px * (1 - profile.aggressiveSellBps / 10000)).toFixed(2);
}

function shouldReplaceOpenOrder(existingOrder, newIntent, profile) {
  if (!existingOrder) return false;
  const existingNotional = Number(existingOrder.remainingQty || 0) * Number(existingOrder.orderPx || 0);
  const newNotional = Math.abs(Number(newIntent.delta_shares || 0)) * Number(newIntent.last_price || 0);
  if (!(existingNotional > 0) || !(newNotional > 0)) return false;
  return newNotional >= existingNotional * (1 + profile.replaceThresholdPct / 100);
}

async function cancelExistingOpenOrder(existingOrder, symbol) {
  const token = await getAccessToken();
  const cancelExch = getExchange(symbol);
  const cancelBody = {
    CANO: CFG.kis.accountNo,
    ACNT_PRDT_CD: CFG.kis.accountProdCd,
    OVRS_EXCG_CD: cancelExch,
    PDNO: symbol,
    ORGN_ODNO: existingOrder.brokerOrderId,
    RVSE_CNCL_DVSN_CD: "02",
    ORD_QTY: "0",
    OVRS_ORD_UNPR: "0",
  };
  const cancelOptions = {
    hostname: CFG.kis.baseUrl,
    port: CFG.kis.port,
    path: "/uapi/overseas-stock/v1/trading/order-rvsecncl",
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "authorization": `Bearer ${token}`,
      "appkey": CFG.kis.appKey,
      "appsecret": CFG.kis.appSecret,
      "tr_id": "TTTT1004U",
      "custtype": "P"
    }
  };
  return httpsRequestJson(cancelOptions, cancelBody, 10000);
}

async function requestCancelReplace(existingOrder, intent, profile) {
  const symbol = String(intent.symbol || "").toUpperCase();
  const replaceKey = `order:replace:cooldown:${symbol}`;
  if (await redis.get(replaceKey)) {
    return { ok: false, reason: "REPLACE_COOLDOWN" };
  }
  const cancelResult = await cancelExistingOpenOrder(existingOrder, symbol);
  if (cancelResult?.rt_cd !== "0") {
    return { ok: false, reason: "CANCEL_FAILED", detail: cancelResult?.msg1 || "" };
  }
  await redis.set(replaceKey, String(Date.now()), "EX", 120).catch(() => {});
  await redis.set(
    `execution:last_replace_reason:${symbol}`,
    JSON.stringify({
      ts: new Date().toISOString(),
      symbol,
      old_odno: existingOrder.brokerOrderId,
      reason: "NOTIONAL_GAP_REPLACE",
      urgency_mode: profile.mode,
    }),
    "EX",
    300
  ).catch(() => {});
  return { ok: true };
}

// ============================================================
// V237-Phase4D: IntentStateMachine
// Intent 생명주기 상태 관리 (PENDING → SUBMITTED → ACKED / FAILED / EXPIRED)
// Redis Hash 기반으로 상태 전이를 원자적으로 관리하여
// 동일 intent의 중복 실행을 방지하고 실행 이력을 추적합니다.
// ============================================================

const ISM_HASH_KEY_PREFIX = "ism:intent:";
const ISM_HASH_TTL_SEC = 3600; // 1시간 만료

/**
 * IntentStateMachine: 주어진 intent_id의 상태를 Redis Hash로 관리합니다.
 * 상태 전이 규칙:
 *   PENDING   → SUBMITTED | FAILED | EXPIRED
 *   SUBMITTED → ACKED | FAILED
 */
const IntentStateMachine = {
  /**
   * intent를 PENDING 상태로 등록합니다.
   * 이미 등록된 경우 false를 반환하여 중복 실행을 방지합니다.
   */
  async claimPending(intentId) {
    const key = `${ISM_HASH_KEY_PREFIX}${intentId}`;
    const now = Date.now();
    // HSETNX: 필드가 없을 때만 성공 (0 = 이미 존재)
    const claimed = await redis.hsetnx(key, "state", "PENDING").catch(() => 0);
    if (!claimed) return false;
    await redis.hset(key, "created_at", String(now), "updated_at", String(now)).catch(() => {});
    await redis.expire(key, ISM_HASH_TTL_SEC).catch(() => {});
    return true;
  },

  /**
   * intent 상태를 SUBMITTED로 전이하고 broker_order_id를 기록합니다.
   */
  async markSubmitted(intentId, brokerOrderId) {
    const key = `${ISM_HASH_KEY_PREFIX}${intentId}`;
    await redis.hset(key,
      "state", "SUBMITTED",
      "broker_order_id", String(brokerOrderId || ""),
      "submitted_at", String(Date.now()),
      "updated_at", String(Date.now())
    ).catch(() => {});
    await redis.expire(key, ISM_HASH_TTL_SEC).catch(() => {});
  },

  /**
   * intent 상태를 ACKED로 전이합니다 (실행 완료).
   */
  async markAcked(intentId) {
    const key = `${ISM_HASH_KEY_PREFIX}${intentId}`;
    await redis.hset(key,
      "state", "ACKED",
      "acked_at", String(Date.now()),
      "updated_at", String(Date.now())
    ).catch(() => {});
    await redis.expire(key, ISM_HASH_TTL_SEC).catch(() => {});
  },

  /**
   * intent 상태를 FAILED로 전이합니다.
   */
  async markFailed(intentId, reason) {
    const key = `${ISM_HASH_KEY_PREFIX}${intentId}`;
    await redis.hset(key,
      "state", "FAILED",
      "fail_reason", String(reason || "").slice(0, 200),
      "failed_at", String(Date.now()),
      "updated_at", String(Date.now())
    ).catch(() => {});
    await redis.expire(key, ISM_HASH_TTL_SEC).catch(() => {});
  },

  /**
   * intent의 현재 상태를 반환합니다.
   */
  async getState(intentId) {
    const key = `${ISM_HASH_KEY_PREFIX}${intentId}`;
    return await redis.hgetall(key).catch(() => null);
  }
};

// ============================================================
// V237-STABILIZE: 2nd-pass Liquidity Throttle (executor-side)
// 위치: getPriceForIntent() 바로 아래
// ============================================================

async function getAdv20Usd(redisClient, symbol) {
  try {
    if (!symbol) return 0;

    const keys = [
      `md:adv20_usd:${symbol}`,
      `md:adv20:${symbol}`,
      `adv20:${symbol}`,
      `liq:adv20_usd:${symbol}`
    ];

    for (const k of keys) {
      const raw = await redisClient.get(k).catch(() => null);
      const v = Number(raw);
      if (Number.isFinite(v) && v > 0) return v;
    }

    const quoteRaw = await redisClient.get(`md:quote:${symbol}`).catch(() => null);
    if (quoteRaw) {
      try {
        const q = JSON.parse(quoteRaw);
        const adv =
          Number(q.adv20_usd ?? q.adv20Usd ?? q.adv20 ?? q.avgDollarVolume ?? 0);
        if (Number.isFinite(adv) && adv > 0) return adv;
      } catch {}
    }

    return 0;
  } catch {
    return 0;
  }
}

async function applyExecutorLiquidityThrottle(redisClient, intent, qty) {
  const symbol = String(intent?.symbol || "").toUpperCase();
  const side = String(intent?.side || "").toUpperCase();

  const pxInfo = await getPriceForIntent(redisClient, intent).catch(() => ({ ok: false }));
  const refPx = Number(
    pxInfo?.price ??
    intent?.last_price ??
    intent?.limit?.price ??
    0
  );

  if (!Number.isFinite(refPx) || refPx <= 0) {
    return {
      ok: false,
      skip: true,
      reason: "LIQ_NO_PRICE",
      qty,
      finalNotional: 0,
      requestedNotional: 0,
      throttleRatio: 0,
      spreadBps: Number(pxInfo?.spreadBps || 0),
      adv20Usd: 0
    };
  }

  const requestedQty = Math.max(1, Math.floor(Math.abs(Number(qty || 0))));
  const requestedNotional = requestedQty * refPx;

  const [
    adv20Usd,
    maxTradePctAdvRaw,
    spreadSoftRaw,
    spreadHardRaw,
    minNotionalRaw,
    eventMultRaw
  ] = await Promise.all([
    getAdv20Usd(redisClient, symbol),
    redisClient.get("policy:liq:max_trade_pct_adv").catch(() => null),
    redisClient.get("policy:liq:spread_soft_bps").catch(() => null),
    redisClient.get("policy:liq:spread_hard_bps").catch(() => null),
    redisClient.get("policy:liq:min_notional_usd").catch(() => null),
    redisClient.get("policy:liq:event_mult").catch(() => null),
  ]);

  const maxTradePctAdv = Number(maxTradePctAdvRaw || 0.05);
  const spreadSoftBps = Number(spreadSoftRaw || 40);
  const spreadHardBps = Number(spreadHardRaw || 60);
  const minNotionalUsd = Number(minNotionalRaw || 500);
  const eventMult = Number(eventMultRaw || 0.5);

  const spreadBps = Number(pxInfo?.spreadBps || 0);
  const eventRisk =
    Boolean(intent?.event_risk) ||
    Boolean(intent?.alpha_metadata?.event_risk) ||
    Boolean(intent?.risk_context?.event_risk);

  let finalQty = requestedQty;
  let reason = "ok";

  // 1) hard spread guard
  if (spreadBps >= spreadHardBps) {
    return {
      ok: true,
      skip: true,
      reason: "LIQ_SPREAD_HARD_SKIP",
      qty: 0,
      finalNotional: 0,
      requestedNotional,
      throttleRatio: 0,
      spreadBps,
      adv20Usd
    };
  }

  // 2) soft spread reduce
  if (spreadBps >= spreadSoftBps && finalQty > 1) {
    finalQty = Math.max(1, Math.floor(finalQty * 0.5));
    reason = "LIQ_SPREAD_SOFT_REDUCE";
  }

  // 3) event risk reduce
  if (eventRisk && finalQty > 1) {
    finalQty = Math.max(1, Math.floor(finalQty * eventMult));
    reason = reason === "ok" ? "LIQ_EVENT_REDUCE" : `${reason}+EVENT`;
  }

  // 4) ADV cap
  if (Number.isFinite(adv20Usd) && adv20Usd > 0 && Number.isFinite(maxTradePctAdv) && maxTradePctAdv > 0) {
    const maxNotional = adv20Usd * maxTradePctAdv;
    if (maxNotional > 0) {
      const cappedQty = Math.floor(maxNotional / refPx);
      if (cappedQty >= 1 && cappedQty < finalQty) {
        finalQty = cappedQty;
        reason = reason === "ok" ? "LIQ_ADV_CAP" : `${reason}+ADV`;
      }
    }
  }

  const finalNotional = finalQty * refPx;

  // 5) min notional cut
  if (!Number.isFinite(finalNotional) || finalNotional < minNotionalUsd) {
    return {
      ok: true,
      skip: true,
      reason: "LIQ_BELOW_MIN_NOTIONAL",
      qty: 0,
      finalNotional: 0,
      requestedNotional,
      throttleRatio: 0,
      spreadBps,
      adv20Usd
    };
  }

  const throttleRatio = requestedQty > 0 ? finalQty / requestedQty : 0;

  return {
    ok: true,
    skip: false,
    reason,
    qty: finalQty,
    finalNotional,
    requestedNotional,
    throttleRatio,
    spreadBps,
    adv20Usd,
    refPx,
    pxSource: pxInfo?.source || "unknown"
  };
}

async function getDailyUpliftCapUsd(redisClient) {
  // 1) absolute override
  const absRaw = await redisClient.get(DAILY_UPLIFT_CAP_USD_KEY).catch(()=>null);
  const abs = Number(absRaw);
  if (Number.isFinite(abs) && abs > 0) return { ok:true, capUsd: abs, mode: 'absolute' };

  // 2) ratio * daily notional budget
  const [ratioRaw, budgetRaw] = await redisClient.mget(DAILY_UPLIFT_CAP_RATIO_KEY, DAILY_NOTIONAL_BUDGET_KEY).catch(()=>[null,null]);
  const ratio = Number(ratioRaw);
  const budget = Number(budgetRaw);
  const r = (Number.isFinite(ratio) && ratio > 0) ? ratio : 0.03;
  if (!Number.isFinite(budget) || budget <= 0) {
    return { ok:false, reason: 'missing_daily_notional_budget', ratio: r };
  }
  return { ok:true, capUsd: budget * r, mode: 'ratio', ratio: r, budgetUsd: budget };
}

const LUA_ALLOCATE_UPLIFT = `
local usedKey = KEYS[1]
local capKey = KEYS[2]
local delta = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])

local cap = tonumber(redis.call('GET', capKey))
if not cap then return {err='missing_cap'} end

local used = tonumber(redis.call('GET', usedKey) or '0')
if used < 0 then used = 0 end

local remaining = cap - used
if remaining <= 0 then
  return {0, used, cap, 0}
end

local allowed = delta
if delta > remaining then allowed = remaining end

local newUsed = used + allowed
redis.call('SET', usedKey, tostring(newUsed), 'EX', ttl)
return {1, newUsed, cap, allowed}`;

async function allocateDailyUplift(redisClient, dateStr, deltaUsd, capUsd) {
  const usedKey = `${DAILY_UPLIFT_USED_KEY_PREFIX}:${dateStr}`;
  const capKey = `${DAILY_UPLIFT_USED_KEY_PREFIX}:cap:${dateStr}`;

  // ensure cap key exists for the day (set once, keep ttl aligned)
  await redisClient.set(capKey, String(capUsd), 'EX', DAILY_UPLIFT_KEY_TTL_SEC).catch(()=>null);

  try {
    const res = await redisClient.eval(LUA_ALLOCATE_UPLIFT, 2, usedKey, capKey, String(deltaUsd), String(DAILY_UPLIFT_KEY_TTL_SEC));
    // res = [okFlag, newUsed, cap, allowed]
    return { ok: true, newUsed: Number(res[1]), cap: Number(res[2]), allowed: Number(res[3]) };
  } catch (e) {
    return { ok: false, reason: 'eval_failed', error: String(e?.message || e) };
  }
}


function requireEnv(name, value) {
  if (!value || !String(value).trim()) {
    throw new Error(`[STARTUP] ${name} environment variable is required`);
  }
}
function validateKisAccountNo(value) {
  const v = String(value || "").trim();
  if (!/^\d{8,14}$/.test(v)) {
    throw new Error(`[STARTUP] KIS_ACCOUNT_NO is invalid: ${v || "<empty>"}`);
  }
}

// Config
const CFG = {
  order_intent_stream: process.env.ORDER_INTENT_STREAM || "emarkos:v6:order:intent",
  execution_stream: process.env.EXECUTION_STREAM || "emarkos:v1:execution",
  consumer_group: "order-intent-executor",
  consumer_name: `executor-${process.pid}`,
  poll_interval_ms: 1000,
  dlq_stream: "risk:dlq:order_intent",
  alert_stream: "risk:alerts",
  kis: {
    baseUrl: "openapi.koreainvestment.com",
    port: 9443,
    appKey: process.env.KIS_APP_KEY,
    appSecret: process.env.KIS_APP_SECRET,
    accountNo: process.env.KIS_ACCOUNT_NO,
    accountProdCd: "01"
  }
};


// ============================================================
// [FIREWALL v1.0] Producer Allowlist / Blocklist + QUARANTINE
// 목적: 허가되지 않은 producer(nextgen-core-v50 등)의 intent를
//       실행하지 않고 격리 스트림으로 복사 후 ACK 처리
// ============================================================

  // [Batch A] Startup validation — fail fast if env missing
  requireEnv("KIS_APP_KEY", process.env.KIS_APP_KEY);
  requireEnv("KIS_APP_SECRET", process.env.KIS_APP_SECRET);
  requireEnv("KIS_ACCOUNT_NO", process.env.KIS_ACCOUNT_NO);
  validateKisAccountNo(process.env.KIS_ACCOUNT_NO);
const QUAR_STREAM = "emarkos:v6:order:intent:quarantine";
const BLOCK_RUN_IDS = new Set(["nextgen-core-v50"]);
// [HARDENING v1.0] phased-transition 제거 — 오늘은 NG2만 producer
const ALLOW_RUN_ID_PREFIXES = ["nextgen2-orchestrator", "nextgen2-", "ng2-", "live-trading-kis", "kis-live", "ltk-", "RAM26", "ram26", "ares-live", "ares_live"];

function isAllowedRunId(runId) {
  if (!runId) return false;
  if (BLOCK_RUN_IDS.has(runId)) return false;
  return ALLOW_RUN_ID_PREFIXES.some(p => runId.startsWith(p));
}

async function quarantineAndAck(_redis, group, msgId, rawFields, reason) {
  try {
    const obj = {};
    if (Array.isArray(rawFields)) {
      for (let i = 0; i < rawFields.length; i += 2) obj[String(rawFields[i])] = String(rawFields[i+1]);
    }
    const flatPayload = [
      "ts", String(Date.now()),
      "reason", reason,
      "original_stream", CFG.order_intent_stream,
      "original_id", msgId
    ];
    for (const [k, v] of Object.entries(obj)) {
      flatPayload.push(k, v);
    }
    await _redis.xadd(QUAR_STREAM, "MAXLEN", "~", "10000", "*", ...flatPayload);
    await _redis.xack(CFG.order_intent_stream, group, msgId);
    log("WARN", "[FIREWALL] QUARANTINED: " + reason, { msgId, reason });
  } catch (e) {
    log("ERROR", "[FIREWALL] quarantineAndAck failed", { msgId, reason, error: String(e) });
    try { await _redis.xack(CFG.order_intent_stream, group, msgId); } catch {}
  }
}

function firewallCheck(intent) {
  if (!intent) return { pass: false, reason: "JSON_PARSE_FAIL" };
  const runId = intent.run_id || intent.runId || (intent.meta && intent.meta.run_id);
  if (!runId) return { pass: false, reason: "MISSING_RUN_ID" };
  if (BLOCK_RUN_IDS.has(runId)) return { pass: false, reason: "BLOCKED_RUN_ID" };
  if (!isAllowedRunId(runId)) return { pass: false, reason: "RUN_ID_NOT_ALLOWLISTED" };
  if (intent.schema && intent.schema !== "ORDER_INTENT") return { pass: false, reason: "BAD_SCHEMA" };
  return { pass: true };
}
// [END FIREWALL v1.0]

// Exchange mapping
// [REMOVED IN v10.0] const EXCHANGE_MAP = {
// [REMOVED IN v10.0]   "META": "NASD", "NVDA": "NASD", "GOOGL": "NASD", "NFLX": "NASD", "TSLA": "NASD",
// [REMOVED IN v10.0]   "AAPL": "NASD", "MSFT": "NASD", "AMZN": "NASD", "PYPL": "NASD", "OKTA": "NASD",
// [REMOVED IN v10.0]   "GILD": "NASD", "CRWD": "NASD", "CMCSA": "NASD", "PEP": "NASD", "COST": "NASD",
// [REMOVED IN v10.0]   "HD": "NYSE", "JNJ": "NYSE", "KO": "NYSE", "MCD": "NYSE", "WMT": "NYSE",
// [REMOVED IN v10.0]   "JPM": "NYSE", "V": "NYSE", "MA": "NYSE", "UNH": "NYSE", "PG": "NYSE",
// [REMOVED IN v10.0]   "BIL": "AMEX", "TLT": "NASD", "IEF": "NASD", "GLD": "AMEX", "SPY": "AMEX",
// [REMOVED IN v10.0]   "QQQ": "NASD", "IWM": "AMEX", "VIXY": "AMEX",
// [REMOVED IN v10.0]   "VRTX": "NASD", "ABBV": "NYSE", "LLY": "NYSE", "REGN": "NASD"
// [REMOVED IN v10.0] };

// ==========================================
// [ARES v10.0] DYNAMIC EXCHANGE MAP LOADER
// Injected by phase1_patcher.py
// Redis에서 거래소 매핑을 동적 로드
// ==========================================
import { getRedis } from './shared_redis.mjs';
import { detectShortCapability } from "./src/broker/short_capability.mjs";
import { locateCheck } from "./src/broker/short_locate_preflight.mjs";


let globalExchangeMap = {};

/**
 * loadExchangeMap()
 * Reads Redis Hash: system:exchange_map → { kis_symbol: exchange_code }
 * Called once during initialize()
 */
async function loadExchangeMap() {
  const _redis = getRedis();
  const MAX_RETRIES = 5;
  const BASE_DELAY = 2000; // 2s
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const raw = await _redis.hgetall('system:exchange_map');
      if (!raw || Object.keys(raw).length === 0) {
        throw new Error("Redis key 'system:exchange_map' is empty or missing.");
      }
      globalExchangeMap = raw;
      log("INFO", `[Executor v10.0] Exchange map loaded: ${Object.keys(globalExchangeMap).length} entries`);
      return; // success
    } catch (err) {
      const delay = Math.min(BASE_DELAY * Math.pow(2, attempt - 1), 30000);
      log("WARN", `[Executor v10.0] loadExchangeMap() attempt ${attempt}/${MAX_RETRIES} failed — ${err.message}, retrying in ${delay}ms`);
      if (attempt === MAX_RETRIES) {
        log("ERROR", `[Executor v10.0] loadExchangeMap() failed after ${MAX_RETRIES} attempts — ${err.message}`);
        throw err;
      }
      await new Promise(r => setTimeout(r, delay));
    }
  }
}
// ==========================================

function getExchange(symbol) {
  return globalExchangeMap[symbol] || "NASD";
}

// Logging
function log(level, message, data = {}) {
  const ts = new Date().toISOString();
  console.log(JSON.stringify({ ts, level, component: "order-intent-executor", message, ...data }));
}

// === STAGE-D B-SERIES LIVE-LINK VALIDATION-ONLY GUARD LOADED ===
log("INFO", STAGE_D_GUARD_LOADED, {
  stage_d_marker: "stage_d_bseries_validation_only",
  redis_mutation: false,
  pm2_lifecycle_action: false,
  broker_order_api_impact: "none"
});
// === END STAGE-D B-SERIES LIVE-LINK VALIDATION-ONLY GUARD LOADED ===

// ============================================================
// [V5.6.2] Unidirectional State Flow: trading:enabled is the SOLE SSOT
// Legacy halt keys (trade:halt, policy:trade:halt, policy:trade_halt) are
// deliberately IGNORED to prevent stale keys from causing permanent halt.
// All halt/resume decisions flow through: go-nogo → kill-switch → trading:enabled
// ============================================================
const _TRUTHY = new Set(["1", "true", "yes", "on"]);

// trading:enabled가 명시적 truthy가 아니면 → 실행 금지 (fail-closed)
async function isTradingEnabled() {
  try {
    const v = await redis.get("trading:enabled");
    return _TRUTHY.has(String(v || "").toLowerCase());
  } catch {
    return false;  // fail-closed: Redis 오류 시 비활성
  }
}

// [V5.6.2] Legacy halt check — always returns false (not halted)
// Kept as stub for backward compatibility with callers.
async function isTradeHalted() {
  return false;
}

// [V5.6.2] Legacy compat halt check — always returns false (not halted)
// All halt logic is now handled solely by trading:enabled.
async function isTradeHaltedCompat() {
  return false;
}
// [END V5.6.2]

// ═══════ [HARDENING v2.1] Divergence Guard Policy Gates ═══════
// recovery_mode: BUY 완전 차단 (SELL만 허용) — equity 괴리 심각 시
// half_speed: qty × 0.5 스케일 — equity 괴리 경고 시
const KEY_RECOVERY_MODE = "policy:transition:recovery_mode";
const KEY_HALF_SPEED    = "policy:transition:half_speed";

async function isRecoveryMode() {
  try {
    const v = await redis.get(KEY_RECOVERY_MODE);
    return _TRUTHY.has(String(v || "").toLowerCase());
  } catch {
    return false;  // fail-open: 정책 키 장애 시 정상 운영 유지
  }
}

async function isHalfSpeed() {
  try {
    const v = await redis.get(KEY_HALF_SPEED);
    return _TRUTHY.has(String(v || "").toLowerCase());
  } catch {
    return false;  // fail-open: 정책 키 장애 시 정상 운영 유지
  }
}
// [END HARDENING v2.1]

// [V2] CRITICAL priority detection — emergency liquidation orders bypass halt
function isCriticalPriority(intent) {
  if (!intent) return false;
  if (String(intent.priority).toUpperCase() === "CRITICAL") return true;
  if (typeof intent.reason === "string" && intent.reason.includes("EMERGENCY")) return true;
  return false;
}

// [PATCH-7] DLQ routing for halted intents
async function routeToDLQ(msgId, intent, reason, extra = {}) {
  const dlqKey = `dlq:order_intent:${msgId}`;
  const ok = await redis.set(dlqKey, "1", "NX", "EX", 86400);
  if (!ok) return { already: true };

  const payload = {
    schema: "emarkos.dlq.order_intent.v1",
    ts: new Date().toISOString(),
    reason,
    original: {
      stream: CFG.order_intent_stream,
      group: CFG.consumer_group,
      id: msgId,
    },
    intent,
    extra,
  };

  await redis.xadd(CFG.dlq_stream, "MAXLEN", "~", "20000", "*",
    "reason", String(reason),
    "intent_id", String(intent?.intent_id ?? ""),
    "symbol", String(intent?.symbol ?? ""),
    "side", String(intent?.side ?? ""),
    "payload", JSON.stringify(payload)
  );
  return { ok: true };
}

// [PATCH-7] Alert emitter
async function emitAlert(severity, type, message, details = {}) {
  try {
    await redis.xadd(CFG.alert_stream, "MAXLEN", "~", "5000", "*",
      "severity", String(severity),
      "type", String(type),
      "message", String(message),
      "details", JSON.stringify(details),
      "ts", new Date().toISOString()
    );
  } catch {
    // best-effort only
  }
}

// [FIX-F3] 4AI consensus: Micro-jitter to avoid minute-boundary crowding
const JITTER_MAX_MS = 2500; // 0~2.5s random delay
async function applyMicroJitter() {
  const jitterMs = Math.floor(Math.random() * JITTER_MAX_MS);
  await new Promise(r => setTimeout(r, jitterMs));
  return jitterMs;
}
// [FIX-F3] Spread-aware limit price protection (8-12bps cap from last price)
function protectedLimitPrice(side, lastPrice, slipCapBps = 10) {
  const cap = lastPrice * (slipCapBps / 10000);
  return side === "BUY"
    ? Math.round((lastPrice + cap) * 100) / 100
    : Math.round((lastPrice - cap) * 100) / 100;
}
// [END FIX-F3]

// [PATCH-5] tick 기반 소수점 자리수 계산
function decimalsFromTick(tick) {
  const s = String(tick);
  if (s.includes("e-")) {
    const m = s.match(/e-(\d+)/);
    return m ? Number(m[1]) : 2;
  }
  const idx = s.indexOf(".");
  return idx >= 0 ? (s.length - idx - 1) : 0;
}

// [PATCH-4] 공통 HTTPS 요청 함수 (timeout + 비2xx 방어)
function httpsRequestJson(options, bodyObj, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(bodyObj);
    options.headers = options.headers || {};
    options.headers["Content-Length"] = Buffer.byteLength(data);
    if (!options.agent) options.agent = kisKeepAliveAgent; // [PATCH-P0] Keep-alive

    const req = https.request(options, (res) => {
      let body = "";
      res.on("data", chunk => body += chunk);
      res.on("end", () => {
        try {
          if (res.statusCode < 200 || res.statusCode >= 300) {
            return reject(new Error(`HTTP_${res.statusCode}: ${body.slice(0, 300)}`));
          }
          resolve(JSON.parse(body));
        } catch (e) {
          reject(new Error("JSON_PARSE_ERROR: " + body.slice(0, 200)));
        }
      });
    });
    req.setTimeout(timeoutMs, () => {
      try { req.destroy(new Error("REQUEST_TIMEOUT")); } catch {}
    });
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}



// ── [APBK0988-FIX] Broker Sellable Qty Helper Functions ──
async function loadBrokerPositionRecord(symbol) {
  const keys = ["kis:broker:positions", "emarkos:v1:positions"];

  const tryJsonString = async (key) => {
    try {
      const raw = await redis.get(key);
      if (!raw) return null;
      const obj = JSON.parse(raw);
      if (Array.isArray(obj)) {
        const rec = obj.find(r => String(r.symbol || r.s || "").toUpperCase() === String(symbol).toUpperCase());
        return rec || null;
      }
      if (obj && typeof obj === "object") {
        return obj[symbol] || obj[String(symbol).toUpperCase()] || obj[String(symbol).toLowerCase()] || null;
      }
    } catch (_) {}
    return null;
  };

  const tryHash = async (key) => {
    try {
      let raw = await redis.hget(key, symbol);
      if (!raw) raw = await redis.hget(key, String(symbol).toUpperCase());
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  };

  for (const key of keys) {
    const rec1 = await tryJsonString(key);
    if (rec1) return rec1;
    const rec2 = await tryHash(key);
    if (rec2) return rec2;
  }
  return null;
}

function extractQtyField(rec, fields) {
  if (!rec || typeof rec !== "object") return 0;
  for (const f of fields) {
    const v = Number(rec[f] ?? 0);
    if (Number.isFinite(v) && v > 0) return Math.floor(v);
  }
  return 0;
}

async function getSellableQty(symbol) {
  const rec = await loadBrokerPositionRecord(symbol);
  return extractQtyField(rec, [
    "sellable_qty",
    "available_qty",
    "ord_psbl_qty",
    "possible_qty",
    "qty_available",
    "avail_qty",
    "shares",
    "qty",
    "quantity",
    "position_qty",
    "holding_qty",
  ]);
}

async function getOpenSellQtySameSymbol(symbol) {
  const keys = ["emarkos:v1:open_orders", "emarkos:v1:open_orders_snapshot"];

  const tryJsonString = async (key) => {
    try {
      const raw = await redis.get(key);
      if (!raw) return 0;
      const obj = JSON.parse(raw);

      // snapshot format: { by_symbol: { CAT: { open_sell_remaining: N } } }
      if (obj && obj.by_symbol && obj.by_symbol[symbol]) {
        const v = Number(obj.by_symbol[symbol].open_sell_remaining || 0);
        if (v > 0) return Math.floor(v);
      }

      // array/object of orders format
      let rows = [];
      if (Array.isArray(obj)) rows = obj;
      else if (obj && obj.orders && Array.isArray(obj.orders)) rows = obj.orders;
      else if (obj && typeof obj === "object") rows = Object.values(obj);

      let qty = 0;
      for (const row of rows) {
        if (!row || typeof row !== "object") continue;
        const sym = String(row.symbol || row.ovrs_pdno || row.s || "").toUpperCase();
        const side = String(row.side || row.sll_buy_dvsn_cd_name || row.bs || row.order_side || "").toUpperCase();
        const status = String(row.status || row.ord_status || "").toUpperCase();
        if (sym !== String(symbol).toUpperCase()) continue;
        if (!side.includes("SELL") && side !== "01") continue;
        if (["FILLED", "CANCELLED", "REJECTED", "DONE"].includes(status)) continue;
        qty += extractQtyField(row, ["remaining_qty", "open_qty", "nccs_qty", "ft_ord_qty", "qty", "quantity", "shares"]);
      }
      return qty;
    } catch (_) {
      return 0;
    }
  };

  for (const key of keys) {
    const q = await tryJsonString(key);
    if (q > 0) return q;
  }
  return 0;
}

async function computeFinalSellQty(symbol, requestedQty) {
  const sellableQty = await getSellableQty(symbol);
  const openSellQtySameSymbol = await getOpenSellQtySameSymbol(symbol);
  const effectiveSellableQty = Math.max(0, sellableQty - openSellQtySameSymbol);
  const finalQty = Math.max(0, Math.min(Number(requestedQty || 0), effectiveSellableQty));
  return {
    requestedQty: Number(requestedQty || 0),
    sellableQty,
    openSellQtySameSymbol,
    effectiveSellableQty,
    finalQty,
  };
}

async function requestSellableResync(symbol, intent, context = {}) {
  const nowIso = new Date().toISOString();
  const payload = JSON.stringify({
    symbol,
    intent_id: intent.intent_id || intent.id || "",
    reason: "BROKER_SELLABLE_EXCEEDED",
    side: intent.side || "",
    context,
    ts: nowIso,
  });

  try {
    const pipe = redis.multi();
    pipe.set(`ares:ops:force_sync:open_orders:${symbol}`, payload, "EX", 180);
    pipe.set(`ares:ops:force_sync:positions:${symbol}`, payload, "EX", 180);
    pipe.xadd(
      "ares:ops:sync:requests",
      "MAXLEN", "~", 1000,
      "*",
      "symbol", String(symbol),
      "reason", "BROKER_SELLABLE_EXCEEDED",
      "intent_id", String(intent.intent_id || ""),
      "side", String(intent.side || ""),
      "ts", nowIso
    );
    await pipe.exec();
    log("INFO", "[RESYNC] Sellable resync requested", { symbol, intent_id: intent.intent_id || "", context });
  } catch (resyncErr) {
    log("WARN", "[RESYNC] Failed to request sellable resync", { symbol, error: resyncErr.message });
  }
}

function isApbk0988(errOrText) {
  const s = String(errOrText || "");
  return s.includes("APBK0988") || s.includes("주문수량이 가능수량보다 큽니다");
}

// ── [OIE-RACE-FIX] Post-Timeout Confirmation Polling ──
async function postTimeoutConfirmation(redis, intent) {
    const MAX_POLLS = 3;
    const POLL_MS = 10000;
    const intentId = String(intent?.intent_id || "");
    const symbol = String(intent?.symbol || "").toUpperCase();
    const side = String(intent?.side || "").toUpperCase();

    log("WARN", "[RACE_CHECK] post-timeout polling start", {
        intent_id: intentId, symbol, side, max_polls: MAX_POLLS
    });

    for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise(resolve => setTimeout(resolve, POLL_MS));
        try {
            const openOrdersRaw = await redis.hgetall("kis:open_orders") || {};
            let filled = null;
            for (const [key, val] of Object.entries(openOrdersRaw)) {
                try {
                    const order = typeof val === "string" ? JSON.parse(val) : val;
                    if (order.intent_id === intentId || 
                        (order.symbol === symbol && order.side === side &&
                         Math.abs(Date.now() - new Date(order.ts || 0).getTime()) < 120000)) {
                        filled = order;
                        break;
                    }
                } catch { continue; }
            }
            if (filled) {
                const raceKey = `order:race_confirmed:${intentId}`;
                await redis.set(raceKey, JSON.stringify({
                    intent_id: intentId, symbol, side,
                    filled_at: new Date().toISOString(),
                    poll_attempt: i + 1, fill_data: filled,
                }), "EX", 86400);
                log("CRITICAL", "[RACE_CONFIRMED] order filled despite timeout", {
                    intent_id: intentId, symbol, side, poll: i + 1
                });
                return { status: "RACE_CONFIRMED", fill: filled };
            }
            log("DEBUG", "[RACE_CHECK] poll " + (i+1) + " no fill", { intent_id: intentId });
        } catch (pollErr) {
            log("WARN", "[RACE_CHECK] poll error", {
                intent_id: intentId, attempt: i + 1, error: pollErr.message
            });
        }
    }
    log("INFO", "[RACE_CHECK] no fill after timeout — safe to retry", { intent_id: intentId, symbol });
    return { status: "TIMEOUT_NO_FILL", retryable: true };
}

// === OPEN ORDER LEDGER (ODNO-based) — SSOT for pending notional ===
// NOTE: keep existing intent_id-based SSOT (order:open:{intent_id}) for retry-worker compatibility.
// This ODNO ledger is used for accurate pending BUY notional deduction and APBK0952 prevention.
async function markOpenOdno(redis, intent, brokerOrderId, limitPrice, exchCode, qty) {
  try {
    const odno = String(brokerOrderId || "");
    if (!odno) return;
    const key = `order:open:ODNO:${odno}`;
    const _origin = String(intent?.origin_intent_id || intent?.intent_id || "");
    const _cycle = String(intent?.cycle_id || "");
    const _refPx = Number(limitPrice || intent?.last_price || 0);

    // [PR-C] qty fallback 안전화: final_qty → delta_shares → 0, NaN 방어
    let _q = Number(qty);
    if (!Number.isFinite(_q) || _q <= 0) {
      const fallback = Math.abs(Number(intent?.delta_shares || 0));
      log("WARN", "markOpenOdno_qty_fallback", {
        odno, symbol: intent?.symbol, original_qty: qty, fallback_qty: fallback
      });
      _q = fallback;
    }

    const _limitPx = Number(limitPrice || intent?.last_price || 0);
    const _lastPx = Number(intent?.last_price || 0);
    const notional = Math.abs(_q * _refPx);

    // [PR-C] notional 0 경고
    if (notional === 0) {
      log("WARN", "markOpenOdno_zero_notional", {
        odno, symbol: intent?.symbol, qty: _q, ref_px: _refPx
      });
    }

    const payload = {
      broker_order_id: odno,
      intent_id: intent.intent_id,
      origin_intent_id: _origin,
      cycle_id: _cycle,
      symbol: intent.symbol,
      side: intent.side,
      qty: _q,
      limit_price: _limitPx,
      last_price: _lastPx,
      ref_px: _refPx,
      notional,
      ovrs_excg_cd: exchCode || "",
      status: "OPEN",
      submitted_ts: new Date().toISOString(),
      submitted_ts_ms: Date.now(),
    };
    await redis.set(key, JSON.stringify(payload), "EX", 172800);
    await redis.sadd("orders:open:odno", odno);
    await redis.expire("orders:open:odno", 172800).catch(() => {});
  } catch (e) {
    log("ERROR", "markOpenOdno_unexpected", {
      odno: String(brokerOrderId || ""),
      symbol: intent?.symbol,
      err: String(e?.message || e).slice(0, 300)
    });
  }
}

// === [PR-B] POST-ORDER ERROR MARKER (주문 성공 후 후처리 실패 기록) ===
async function markPostError(redis, intent, brokerOrderId, errMsg) {
  const ts = new Date().toISOString();
  const intentId = String(intent?.intent_id || "");
  const odno = String(brokerOrderId || "");
  const key = `order:post_error:${intentId}:${odno}`;
  const payload = { ts, intent_id: intentId, odno, symbol: String(intent?.symbol || ""), err: String(errMsg).slice(0, 800) };
  try {
    await redis.set(key, JSON.stringify(payload), "EX", 7 * 24 * 3600);
  } catch {}
  try {
    await redis.xadd(
      "py:cutover:events",
      "MAXLEN", "~", "20000",
      "*",
      "action", "OIE_POST_ERROR",
      "ts", ts,
      "intent_id", intentId,
      "odno", odno,
      "symbol", String(intent?.symbol || ""),
      "err", String(errMsg).slice(0, 400)
    );
  } catch {}
}
// === END POST-ORDER ERROR MARKER ===

// === OPEN ORDER TRACKING (for retry worker) — AOPS_EVT instrumented ===
async function markOpen(redis, intent, brokerOrderId, limitPrice, exchCode) {
  const key = `order:open:${intent.intent_id}`;
  const nowMs = Date.now();
  const _moOrigin = String(intent?.origin_intent_id || intent?.intent_id || "");
  const _moCycle = String(intent?.cycle_id || "");
  const _moRefPx = Number(limitPrice || intent?.last_price || 0);
  const _moQty = Number(intent?.delta_shares || 0);
  const payload = {
    intent_id: intent.intent_id,
    origin_intent_id: _moOrigin,
    cycle_id: _moCycle,
    symbol: intent.symbol,
    side: intent.side,
    qty: Number(intent.delta_shares || 0),
    limit_price: Number(limitPrice || intent.last_price || 0),
    last_price: Number(intent.last_price || 0),
    ref_px: _moRefPx,
    notional: Math.abs(_moQty * _moRefPx),
    submitted_ts: new Date().toISOString(),
    retry_count: 0,
    broker_order_id: brokerOrderId || "",
    ovrs_excg_cd: exchCode || "",
    status: "OPEN"
  };
  await redis.set(key, JSON.stringify(payload), "EX", 172800);

  // === AOPS_EVT: order_state — SSOT에 OPEN 상태 기록 직후 ===
  emitEvent({
    proc: PROC,
    event: "order_state",
    sev: "INFO",
    corr: { order_id: brokerOrderId || "", intent_id: intent.intent_id, symbol: intent.symbol },
    msg: `order OPEN: ${intent.symbol} ${intent.side} ${payload.qty}@${payload.limit_price}`,
    ctx: {
      order: {
        status: "OPEN",
        qty: payload.qty,
        filled_qty: 0,
        remaining_qty: payload.qty,
        submitted_ts_ms: nowMs,
        last_update_ts_ms: nowMs
      }
    }
  });

  // [PATCH-OO + HARDENING v2.0] Trade halt guard: block SADD when halted
  const tradeHalt = await isTradeHaltedCompat();
  if (tradeHalt) {
    log('WARN', 'OPEN_TRACK_BLOCKED_TRADE_HALT', { intent_id: intent.intent_id, symbol: intent.symbol });
  } else {
    await redis.sadd("orders:open", intent.intent_id);
  }
}

// [A3-06] Lua script for atomic clearOpen (del + srem in one round-trip)
const LUA_CLEAR_OPEN = `
local intentId = ARGV[1]
redis.call('DEL', 'order:open:' .. intentId)
redis.call('SREM', 'orders:open', intentId)
return 1
`;

async function clearOpen(redis, intentId) {
  // [A3-06] atomic cleanup
  await redis.eval(LUA_CLEAR_OPEN, 0, intentId).catch((e) => {
    log('WARN', 'CLEAR_OPEN_LUA_FAIL', { intent_id: intentId, err: String(e?.message || e) });
    // fallback to non-atomic
    redis.del(`order:open:${intentId}`).catch(() => {});
    redis.srem('orders:open', intentId).catch(() => {});
  });

  // === AOPS_EVT: order_state — SSOT에서 제거(CLOSED/REJECTED) ===
  emitEvent({
    proc: PROC,
    event: "order_state",
    sev: "WARN",
    corr: { intent_id: intentId },
    msg: `order cleared from SSOT: ${intentId}`,
    ctx: {
      order: {
        status: "CLOSED",
        last_update_ts_ms: Date.now(),
        closed_ts_ms: Date.now()
      }
    }
  });
}
// === END OPEN ORDER TRACKING ===

// KIS Token
let accessToken = null;
let tokenExpiry = 0;

// Canonical KIS token loader.
async function loadTokenFromRedis() {
  const resolved = await loadCanonicalKisToken(redis, { minValidityMs: 60000 });
  if (!resolved?.access_token) return false;
  accessToken = resolved.access_token;
  tokenExpiry = resolved.expires_at_ms || (Date.now() + 10 * 60 * 1000);
  log("INFO", "Token loaded from canonical resolver", {
    source: resolved.source,
    expiresIn: Math.max(0, Math.floor((tokenExpiry - Date.now()) / 1000)),
  });
  return true;
}

// [A3-05] Token refresh mutex to prevent concurrent refresh race
let _tokenRefreshPromise = null;

// [PATCH-3+4] 토큰 발급: 양쪽 키에 저장 + timeout 방어
const LUA_RELEASE_LOCK_IF_OWNER = `
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
`;

async function releaseLockIfOwned(key, owner) {
  try {
    await redis.eval(LUA_RELEASE_LOCK_IF_OWNER, 1, key, owner);
  } catch {}
}

async function getAccessToken() {
  if (accessToken && Date.now() < tokenExpiry - 60000) {
    return accessToken;
  }
  if (_tokenRefreshPromise) {
    await _tokenRefreshPromise;
    if (accessToken && Date.now() < tokenExpiry - 60000) return accessToken;
  }

  const lockKey = "kis:token:refresh:lock";
  const lockValue = `${process.pid}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  const deadline = Date.now() + 15000;

  while (Date.now() < deadline) {
    if (await loadTokenFromRedis()) return accessToken;
    const lockAcquired = await redis.set(lockKey, lockValue, "NX", "EX", 15);
    if (lockAcquired) {
      if (await loadTokenFromRedis()) {
        await releaseLockIfOwned(lockKey, lockValue);
        return accessToken;
      }
      _tokenRefreshPromise = _doTokenRefresh();
      try {
        return await _tokenRefreshPromise;
      } finally {
        _tokenRefreshPromise = null;
        await releaseLockIfOwned(lockKey, lockValue);
      }
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }

  throw new Error("TOKEN_REFRESH_LOCK_TIMEOUT");
}

async function _doTokenRefresh() {
  // Try Redis first
  if (await loadTokenFromRedis()) {
    return accessToken;
  }
  
  // Request new token
  log("INFO", "Requesting new KIS access token");

  // === AOPS_EVT: retry_start — 토큰 발급 시도 ===
  const corrToken = { endpoint: "/oauth2/tokenP" };
  emitEvent({
    proc: PROC,
    event: "retry_start",
    sev: "WARN",
    corr: corrToken,
    msg: "requesting new KIS access token",
    ctx: { attempt: 1 }
  });
  
  const bodyObj = {
    grant_type: "client_credentials",
    appkey: CFG.kis.appKey,
    appsecret: CFG.kis.appSecret
  };
  
  const options = {
    hostname: CFG.kis.baseUrl,
    port: CFG.kis.port,
    path: "/oauth2/tokenP",
    method: "POST",
    headers: { "Content-Type": "application/json" }
  };
  
  // === AOPS_EVT v2: depCall 래퍼 — KIS 토큰 발급 HTTP 호출 (증명 기반) ===
  let result;
  try {
    result = await depCall({
      proc: PROC,
      corr: { ...corrToken, request_id: newRequestId() },
      dep: { name: "KIS", type: "http", method: "POST", path: "/oauth2/tokenP" }
    }, async () => {
      return await httpsRequestJson(options, bodyObj, 15000);
    });
  } catch (e) {
    // === AOPS_EVT v1: retry_giveup — 토큰 발급 네트워크 실패 ===
    emitEvent({
      proc: PROC,
      event: "retry_giveup",
      sev: "P1",
      corr: corrToken,
      msg: `KIS token request failed: ${e.message}`,
      ctx: { attempt: 1, error: e.message }
    });
    throw e;
  }

  if (!result.access_token) {
    // === AOPS_EVT: retry_giveup — 토큰 응답에 access_token 없음 ===
    emitEvent({
      proc: PROC,
      event: "retry_giveup",
      sev: "P1",
      corr: corrToken,
      msg: "KIS token response missing access_token",
      ctx: { attempt: 1, response_keys: Object.keys(result || {}).join(",") }
    });
    throw new Error("Failed to get access token: " + JSON.stringify(result).slice(0, 200));
  }
  
  accessToken = result.access_token;
  const ttl = Math.max(60, (result.expires_in || 600) - 60);
  tokenExpiry = Date.now() + ttl * 1000;
  
  // Redis에 canonical + compatibility 토큰 저장
  await redis.set("kis:token:access_token", accessToken, "EX", ttl);
  await redis.set(
    "kis:live:token",
    JSON.stringify({ access_token: accessToken, expires_at: new Date(Date.now() + (ttl + 30) * 1000).toISOString() }),
    "EX", ttl + 30
  );
  await publishCanonicalKisToken(redis, {
    access_token: accessToken,
    expires_at_ms: Date.now() + ttl * 1000,
    source: "order-intent-executor",
    ttl_sec: ttl,
  }).catch((err) => log("WARN", "CANONICAL_TOKEN_PUBLISH_FAILED", { error: err.message }));
  
  log("INFO", "KIS access token obtained", { expiresIn: result.expires_in });

  // === AOPS_EVT: retry_success — 토큰 발급 성공 ===
  emitEvent({
    proc: PROC,
    event: "retry_success",
    sev: "INFO",
    corr: corrToken,
    msg: "KIS access token obtained",
    ctx: { attempt: 1, expires_in: result.expires_in }
  });

  return accessToken;
}

// KIS Order Execution (AOPS_EVT instrumented)

// ============================================================
// [CHUNKED-SELL] KIS 1회 주문 수량 한도 분할 매도
// KIS 해외주식은 종목/가격대별 1회 주문 수량 한도가 있음 (예: CAT ~40주)
// 이 한도를 초과하는 SELL은 자동 분할하여 순차 실행
// ============================================================
const KIS_MAX_SELL_QTY_PER_ORDER = 40; // 보수적 상한 (실측: CAT@$715 = 40주)

async function chunkedSellOrder(executeOneFn, totalQty, symbol, intentId) {
  const chunks = [];
  let remaining = totalQty;
  while (remaining > 0) {
    const chunkQty = Math.min(remaining, KIS_MAX_SELL_QTY_PER_ORDER);
    chunks.push(chunkQty);
    remaining -= chunkQty;
  }
  
  log("INFO", "[CHUNKED-SELL] Splitting large SELL order", {
    symbol,
    total_qty: totalQty,
    max_per_order: KIS_MAX_SELL_QTY_PER_ORDER,
    num_chunks: chunks.length,
    chunks: chunks.join("+"),
    intent_id: intentId,
  });
  
  const results = [];
  let totalFilledQty = 0;
  
  for (let i = 0; i < chunks.length; i++) {
    const chunkQty = chunks[i];
    
    // 첫 번째가 아니면 KIS rate limit 방어를 위해 대기
    if (i > 0) {
      const delay = 1500; // 1.5초
      log("INFO", "[CHUNKED-SELL] Waiting between chunks", {
        symbol, chunk_index: i, delay_ms: delay, intent_id: intentId,
      });
      await new Promise(r => setTimeout(r, delay));
    }
    
    try {
      const chunkResult = await executeOneFn(chunkQty);
      results.push({ chunk_index: i, qty: chunkQty, result: chunkResult });
      
      if (chunkResult.rt_cd === "0" && chunkResult.output?.ODNO) {
        totalFilledQty += chunkQty;
        log("INFO", "[CHUNKED-SELL] Chunk accepted", {
          symbol,
          chunk_index: i,
          chunk_qty: chunkQty,
          odno: chunkResult.output.ODNO,
          total_filled_so_far: totalFilledQty,
          intent_id: intentId,
        });
      } else {
        // Chunk 거부 — 나머지 중단
        log("WARN", "[CHUNKED-SELL] Chunk rejected, stopping", {
          symbol,
          chunk_index: i,
          chunk_qty: chunkQty,
          rt_cd: chunkResult.rt_cd,
          msg_cd: chunkResult.msg_cd || "",
          msg1: chunkResult.msg1 || "",
          total_filled_so_far: totalFilledQty,
          remaining_chunks: chunks.length - i - 1,
          intent_id: intentId,
        });
        break;
      }
    } catch (chunkErr) {
      log("ERROR", "[CHUNKED-SELL] Chunk error, stopping", {
        symbol, chunk_index: i, chunk_qty: chunkQty,
        error: chunkErr.message, total_filled_so_far: totalFilledQty,
        intent_id: intentId,
      });
      break;
    }
  }
  
  // 최종 결과: 첫 번째 성공한 chunk의 결과를 기반으로 반환
  const firstSuccess = results.find(r => r.result.rt_cd === "0");
  if (firstSuccess) {
    log("INFO", "[CHUNKED-SELL] Completed", {
      symbol,
      total_requested: totalQty,
      total_accepted: totalFilledQty,
      chunks_sent: results.length,
      chunks_total: chunks.length,
      odnos: results.filter(r => r.result.rt_cd === "0").map(r => r.result.output.ODNO).join(","),
      intent_id: intentId,
    });
    return {
      ...firstSuccess.result,
      _chunked: true,
      _total_accepted_qty: totalFilledQty,
      _chunk_results: results.map(r => ({
        chunk_index: r.chunk_index,
        qty: r.qty,
        rt_cd: r.result.rt_cd,
        odno: r.result.output?.ODNO || "",
      })),
    };
  }
  
  // 전부 실패
  const lastResult = results.length > 0 ? results[results.length - 1].result : { rt_cd: "1", msg1: "CHUNKED_SELL_ALL_FAILED", output: { ODNO: "" } };
  return lastResult;
}

async function executeKISOrder(intent) {
  const token = await getAccessToken();
  const exchange = getExchange(intent.symbol);
  const sideCode = intent.side === "SELL" ? "01" : "02";
  const trId = intent.side === "SELL" ? "TTTT1006U" : "TTTT1002U"; // 실투자
  
  // KIS 해외주식(미국) 시장가(01) 미지원 → 항상 지정가(00) + aggressive pricing
  const side = String(intent?.side || "").toUpperCase();
  let qty = Math.max(1, Math.floor(Math.abs(Number(intent.delta_shares || 0))));
  const riskOnMult = await getRiskOnFreqMultiplier(redis);
  if (side === 'BUY' && riskOnMult > 1.0) {
    const origQty = qty;
    const dateStr = todayUTCDateStr();
    const pxInfo = await getPriceForIntent(redis, intent);
    const px = pxInfo.ok ? pxInfo.price : null;

    // [4AI-PATCH-1] SPREAD_TOO_WIDE skip/reduce (3AI consensus: 60bps skip, 40bps reduce)
    if (pxInfo.ok && pxInfo.spreadBps > 0) {
      const spreadSkipBps = Number(await redis.get('policy:spread_skip_bps') || 60);
      const spreadReduceBps = Number(await redis.get('policy:spread_reduce_bps') || 40);
      if (pxInfo.spreadBps >= spreadSkipBps) {
        log('WARN', 'SPREAD_TOO_WIDE', {
          intent_id: intent.intent_id, symbol: intent.symbol, side: intent.side,
          spreadBps: pxInfo.spreadBps.toFixed(1), threshold: spreadSkipBps,
          bid: pxInfo.bid, ask: pxInfo.ask
        });
        await ensureMetricsSessionReset(redis || redisClient).catch(() => {});
            await redis.hincrby('metrics:drop_reasons', 'SPREAD_TOO_WIDE', 1).catch(() => {});
        await emitDecision({ intent, action: 'SKIP', drop_reason: 'SPREAD_TOO_WIDE',
          drop_detail: { spreadBps: pxInfo.spreadBps, threshold: spreadSkipBps, bid: pxInfo.bid, ask: pxInfo.ask }
        });
        return { ok: false, reason: 'SPREAD_TOO_WIDE' };
      }
      if (pxInfo.spreadBps >= spreadReduceBps && intent.delta_shares > 1) {
        const reduceRatio = Number(await redis.get('policy:spread_reduce_ratio') || 0.5);
        const origShares = intent.delta_shares;
        intent.delta_shares = Math.max(1, Math.floor(origShares * reduceRatio));
        log('INFO', 'SPREAD_REDUCED', {
          intent_id: intent.intent_id, symbol: intent.symbol,
          spreadBps: pxInfo.spreadBps.toFixed(1), threshold: spreadReduceBps,
          origShares, reducedShares: intent.delta_shares, reduceRatio
        });
        await redis.hincrby('metrics:drop_reasons', 'SPREAD_REDUCED', 1).catch(() => {});
      }
    }

    if (!px) {
      // price unknown => skip uplift for safety
      log('WARN', 'RISK_ON_SKIP_NO_PRICE', { intent_id: intent.intent_id, symbol: intent.symbol, side, origQty, riskOnMult, pxInfo });
    } else {
      const capInfo = await getDailyUpliftCapUsd(redis);
      if (!capInfo.ok) {
        log('WARN', 'RISK_ON_SKIP_NO_DAILY_BUDGET', { intent_id: intent.intent_id, symbol: intent.symbol, side, origQty, riskOnMult, capInfo });
      } else {
        const baseNotional = origQty * px;
        const desiredNotional = baseNotional * riskOnMult;
        const deltaUsd = Math.max(0, desiredNotional - baseNotional);
        if (deltaUsd <= 0) {
          // nothing to allocate
        } else {
          const alloc = await allocateDailyUplift(redis, dateStr, deltaUsd, capInfo.capUsd);
          if (!alloc.ok) {
            log('WARN', 'RISK_ON_ALLOC_FAIL', { intent_id: intent.intent_id, symbol: intent.symbol, side, origQty, px, riskOnMult, capInfo, alloc });
          } else {
            const allowed = Math.max(0, alloc.allowed);
            const effectiveMult = 1.0 + (allowed / deltaUsd) * (riskOnMult - 1.0);
            const eff = Math.min(riskOnMult, Math.max(1.0, effectiveMult));
            qty = applyQtyMultiplierSafe(qty, eff);
            log('INFO', 'RISK_ON_GLOBAL_CAP', { intent_id: intent.intent_id, symbol: intent.symbol, side, origQty, px, riskOnMult, pxSource: pxInfo.source, effectiveMult: eff, deltaUsd, allowedUsd: allowed, usedUsd: alloc.newUsed, capUsd: alloc.cap, capMode: capInfo.mode });
          }
        }
      }
    }
  }
  
  // ============================================================
  // V237-STABILIZE: 2nd-pass Liquidity Throttle (submit 직전)
  // 위치: risk-on uplift 반영 직후, aggressive pricing 직전
  // ============================================================
  const liq2 = await applyExecutorLiquidityThrottle(redis, intent, qty).catch((e) => {
    log("WARN", "EXECUTOR_LIQUIDITY_THROTTLE_ERROR", {
      intent_id: intent?.intent_id,
      symbol: intent?.symbol,
      side: intent?.side,
      error: String(e?.message || e)
    });
    return { ok: false, skip: false, qty };
  });

  if (liq2?.skip) {
    log("WARN", "EXECUTOR_LIQUIDITY_SKIP", {
      intent_id: intent.intent_id,
      symbol: intent.symbol,
      side: intent.side,
      reason: liq2.reason,
      requested_notional: liq2.requestedNotional,
      spread_bps: liq2.spreadBps,
      adv20_usd: liq2.adv20Usd
    });

    await redis.hincrby("metrics:drop_reasons", String(liq2.reason || "LIQ_SKIP"), 1).catch(() => {});
    await emitDecision({
      intent,
      action: "SKIP",
      drop_reason: String(liq2.reason || "LIQ_SKIP"),
      drop_detail: {
        requested_notional: liq2.requestedNotional,
        spread_bps: liq2.spreadBps,
        adv20_usd: liq2.adv20Usd
      }
    });

    await redis.xadd("emarkos:v1:ops_event", "*",
      "schema", "emarkos.ops_event.v1",
      "ts", new Date().toISOString(),
      "kind", "BLOCK",
      "reason", String(liq2.reason || "LIQ_SKIP"),
      "intent_id", String(intent?.intent_id || ""),
      "symbol", String(intent?.symbol || ""),
      "side", String(intent?.side || "")
    ).catch(() => {});

    return { ok: false, reason: String(liq2.reason || "LIQ_SKIP") };
  }

  if (liq2?.ok && Number.isFinite(liq2.qty) && liq2.qty > 0 && liq2.qty !== qty) {
    const origQty = qty;
    qty = liq2.qty;
    intent.delta_shares = qty;

    intent.alpha_metadata = intent.alpha_metadata || {};
    intent.alpha_metadata.executor_liquidity_throttle = {
      applied: true,
      reason: liq2.reason,
      throttle_ratio: liq2.throttleRatio,
      requested_notional: liq2.requestedNotional,
      final_notional: liq2.finalNotional,
      spread_bps: liq2.spreadBps,
      adv20_usd: liq2.adv20Usd,
      ref_px: liq2.refPx,
      px_source: liq2.pxSource
    };

    log("INFO", "EXECUTOR_LIQUIDITY_REDUCED", {
      intent_id: intent.intent_id,
      symbol: intent.symbol,
      side: intent.side,
      reason: liq2.reason,
      orig_qty: origQty,
      final_qty: qty,
      throttle_ratio: liq2.throttleRatio,
      requested_notional: liq2.requestedNotional,
      final_notional: liq2.finalNotional,
      spread_bps: liq2.spreadBps,
      adv20_usd: liq2.adv20Usd
    });

    await redis.xadd("emarkos:v1:ops_event", "*",
      "schema", "emarkos.ops_event.v1",
      "ts", new Date().toISOString(),
      "kind", "INFO",
      "reason", "EXECUTOR_LIQUIDITY_REDUCED",
      "intent_id", String(intent?.intent_id || ""),
      "symbol", String(intent?.symbol || ""),
      "side", String(intent?.side || ""),
      "orig_qty", String(origQty),
      "final_qty", String(qty),
      "throttle_ratio", String(liq2.throttleRatio || 0)
    ).catch(() => {});
  }

  // --- Aggressive Limit Pricing (market-like) ---
  const agglimitEnabled = (await redis.get("policy:agglimit:enabled")) === "true";
  const bpCap = Number(await redis.get("policy:agglimit:bp_cap") || "30") || 30;
  const buyBp = Math.min(bpCap, Number(await redis.get("policy:agglimit:buy_bp") || "5") || 5);
  let sellBp = Math.min(bpCap, Number(await redis.get("policy:agglimit:sell_bp") || "15") || 15);
  const tick = Number(await redis.get("policy:agglimit:min_price_tick") || "0.01") || 0.01;
  
  // [V2] CRITICAL/MARKET 주문: agglimit 강제 + 최소 30bp 슬리피지
  const isCritical = isCriticalPriority(intent);
  const isMarketType = (intent?.limit?.type === "MARKET") || 
                       (typeof intent?.reason === "string" && intent.reason.includes("EMERGENCY"));
  const forceAgglimit = isCritical || isMarketType;
  
  if (forceAgglimit) {
    sellBp = Math.max(sellBp, 30);
    log("INFO", "CRITICAL_MARKET_PRICING", {
      symbol: intent.symbol, side, isCritical, isMarketType,
      forced_sell_bp: sellBp, reason: intent.reason || intent.priority
    });
    emitEvent({
      proc: PROC, event: "critical_market_pricing", sev: "WARN",
      corr: { intent_id: intent.intent_id, symbol: intent.symbol },
      msg: `Forced aggressive limit: ${intent.symbol} sellBp=${sellBp}`,
      ctx: { isCritical, isMarketType, sellBp }
    });
  }
  
  const ref = Number(intent?.last_price ?? intent?.limit?.price);
  if (!isFinite(ref) || ref <= 0) {
    throw new Error("MISSING_PRICE: ref=" + ref);
  }
  
  // SELL: 현재가보다 낮게 (-bp), BUY: 현재가보다 높게 (+bp)
  const urgencyProfile = await getUrgencyProfile(redis);
  const effectiveEnabled = agglimitEnabled || forceAgglimit || urgencyProfile.mode === "AGGRESSIVE";
  const adj_bp = effectiveEnabled ? (side === "SELL" ? -sellBp : +buyBp) : 0;
  let limitPx = ref * (1 + adj_bp / 10000.0);
  if (urgencyProfile.mode === "AGGRESSIVE") {
    const marketable = computeMarketableLimit(side, ref, urgencyProfile);
    if (marketable && marketable > 0) {
      limitPx = marketable;
    }
  }
  
  // tick rounding
  limitPx = Math.round(limitPx / tick) * tick;
  if (!isFinite(limitPx) || limitPx <= 0) {
    throw new Error("BAD_LIMIT_PX: limitPx=" + limitPx);
  }
  // [PATCH-5] tick 기반 소수점 처리
  const px = limitPx.toFixed(Math.max(0, decimalsFromTick(tick)));
  
  // [PRE-SUBMIT-CLAMP] For SELL: ensure limit price <= current market price
  let clampedPx = px;
  let clampApplied = false;
  if (side === "SELL") {
    try {
      // Fetch current market price
      let currentMktPrice = null;
      const rtPx = await redis.get(`price:${intent.symbol}`);
      if (rtPx && !isNaN(Number(rtPx)) && Number(rtPx) > 0) {
        currentMktPrice = Number(rtPx);
      } else {
        // Fallback: broker positions
        const posRaw = await redis.hGet("kis:broker:positions", intent.symbol);
        if (posRaw) {
          try {
            const posData = JSON.parse(posRaw);
            if (posData.current_price && Number(posData.current_price) > 0) {
              currentMktPrice = Number(posData.current_price);
            }
          } catch {}
        }
      }
      
      if (currentMktPrice && Number(px) > currentMktPrice) {
        // Clamp: set to currentPrice * (1 - sellBp/10000)
        const maxPassiveGapBp = Number(await redis.get("policy:agglimit:max_passive_gap_bp") || "10") || 10;
        let newLimitPx = currentMktPrice * (1 - sellBp / 10000.0);
        // Floor: don't go below ref * (1 - 200bp) to avoid excessive slippage
        const floorPx = ref * (1 - 200 / 10000.0);
        newLimitPx = Math.max(newLimitPx, floorPx);
        newLimitPx = Math.round(newLimitPx / tick) * tick;
        clampedPx = newLimitPx.toFixed(Math.max(0, decimalsFromTick(tick)));
        clampApplied = true;
        
        log("WARN", "[PRE-SUBMIT-CLAMP] SELL price clamped to current market", {
          symbol: intent.symbol,
          original_ref: ref,
          original_limit_px: px,
          current_market_price: currentMktPrice,
          clamped_px: clampedPx,
          floor_px: floorPx.toFixed(2),
          sell_bp: sellBp,
          gap_bp_before: Math.round((Number(px) - currentMktPrice) / currentMktPrice * 10000),
          intent_id: intent.intent_id,
        });
        
        await redis.xadd("emarkos:v1:ops_event", "*",
          "schema", "emarkos.ops_event.v1",
          "ts", new Date().toISOString(),
          "kind", "WARN",
          "reason", "PRE_SUBMIT_CLAMP",
          "symbol", String(intent?.symbol || ""),
          "side", "SELL",
          "original_px", String(px),
          "clamped_px", String(clampedPx),
          "current_mkt_px", String(currentMktPrice)
        );
      }
    } catch (clampErr) {
      log("WARN", "[PRE-SUBMIT-CLAMP] Error in clamp check, using original price", {
        symbol: intent.symbol, error: clampErr.message
      });
    }
  }
  // Use clamped price for the actual order
  const finalPx = clampApplied ? clampedPx : px;
  
  // 로그
  log("INFO", "AGG_LIMIT_PRICING", {
    symbol: intent.symbol,
    side,
    ref_px: ref,
    limit_px: finalPx,
    adj_bp: adj_bp,
    clamp_applied: clampApplied,
    original_px: px,
    enabled: effectiveEnabled,
    qty
  });
  
  // audit event
  await redis.xadd("emarkos:v1:ops_event", "*",
    "schema", "emarkos.ops_event.v1",
    "ts", new Date().toISOString(),
    "kind", "INFO",
    "reason", "AGG_LIMIT_APPLIED",
    "symbol", String(intent?.symbol || ""),
    "side", side,
    "ref_px", String(ref),
    "limit_px", String(finalPx),
    "bp", String(adj_bp)
  );
  
  // [APBK0988-FIX-v3] SELL은 computeFinalSellQty 기준으로 cap (open sell 차감 포함)
  if (side === "SELL") {
    try {
      const sellable = await computeFinalSellQty(intent.symbol, qty);
      log("INFO", "[SELLABLE_GUARD] Pre-send check", {
        symbol: intent.symbol,
        requested_qty: qty,
        broker_sellable_qty: sellable.sellableQty,
        open_sell_qty_same_symbol: sellable.openSellQtySameSymbol,
        effective_sellable_qty: sellable.effectiveSellableQty,
        final_qty: sellable.finalQty,
        intent_id: intent.intent_id,
      });
      if (sellable.finalQty <= 0) {
        log("WARN", "[SELLABLE_GUARD] No sellable quantity; skip SELL", {
          symbol: intent.symbol,
          requested_qty: qty,
          sellable_qty: sellable.sellableQty,
          open_sell_qty_same_symbol: sellable.openSellQtySameSymbol,
          effective_sellable_qty: sellable.effectiveSellableQty,
          intent_id: intent.intent_id,
        });
        await redis.set(
          `oie:sellable_guard:${intent.intent_id}`,
          JSON.stringify({
            symbol: intent.symbol,
            requested_qty: qty,
            sellable_qty: sellable.sellableQty,
            open_sell_qty_same_symbol: sellable.openSellQtySameSymbol,
            effective_sellable_qty: sellable.effectiveSellableQty,
            ts: new Date().toISOString(),
            reason: "NO_SELLABLE_QTY_AFTER_OPEN_SELL_DEDUCTION"
          }),
          "EX", 3600
        );
        return { ...({ rt_cd: "1", msg1: "SELLABLE_GUARD_BLOCKED", output: { ODNO: "" } }), adj_bp, final_qty: 0 };
      }
      if (qty > sellable.finalQty) {
        log("WARN", "[SELLABLE_GUARD] SELL qty capped by broker sellable - open sells deducted", {
          symbol: intent.symbol,
          requested_qty: qty,
          sellable_qty: sellable.sellableQty,
          open_sell_qty_same_symbol: sellable.openSellQtySameSymbol,
          effective_sellable_qty: sellable.effectiveSellableQty,
          capped_qty: sellable.finalQty,
          intent_id: intent.intent_id,
        });
        qty = sellable.finalQty;
      }
    } catch (sellableErr) {
      log("WARN", "[SELLABLE_GUARD] Error in pre-send check, proceeding with original qty", {
        symbol: intent.symbol, qty, error: sellableErr.message
      });
    }
  }

  if (!Number.isFinite(qty) || qty <= 0) {
    return { ...({ rt_cd: "1", msg1: "INVALID_FINAL_QTY", output: { ODNO: "" } }), adj_bp, final_qty: 0 };
  }

  // [CHUNKED-SELL] 대량 SELL 자동 분할
  if (side === "SELL" && qty > KIS_MAX_SELL_QTY_PER_ORDER) {
    log("INFO", "[CHUNKED-SELL] Triggering chunked sell", {
      symbol: intent.symbol, total_qty: qty, max_per_order: KIS_MAX_SELL_QTY_PER_ORDER,
      intent_id: intent.intent_id,
    });
    const chunkedResult = await chunkedSellOrder(
      async (chunkQty) => {
        const chunkBody = {
          CANO: CFG.kis.accountNo,
          ACNT_PRDT_CD: CFG.kis.accountProdCd,
          OVRS_EXCG_CD: exchange,
          PDNO: intent.symbol,
          ORD_DVSN: "00",
          ORD_QTY: String(chunkQty),
          OVRS_ORD_UNPR: finalPx,
          SLL_BUY_DVSN_CD: sideCode,
          ORD_SVR_DVSN_CD: "0"
        };
        const chunkOptions = {
          hostname: CFG.kis.baseUrl,
          port: CFG.kis.port,
          path: "/uapi/overseas-stock/v1/trading/order",
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "authorization": `Bearer ${token}`,
            "appkey": CFG.kis.appKey,
            "appsecret": CFG.kis.appSecret,
            "tr_id": trId,
            "custtype": "P"
          }
        };
        return await httpsRequestJson(chunkOptions, chunkBody, 20000);
      },
      qty,
      intent.symbol,
      intent.intent_id
    );
    const totalAccepted = chunkedResult._total_accepted_qty || 0;
    return { ...chunkedResult, adj_bp, final_qty: totalAccepted };
  }

  const body = {
    CANO: CFG.kis.accountNo,
    ACNT_PRDT_CD: CFG.kis.accountProdCd,
    OVRS_EXCG_CD: exchange,
    PDNO: intent.symbol,
    ORD_DVSN: "00",
    ORD_QTY: String(qty),
    OVRS_ORD_UNPR: finalPx,
    SLL_BUY_DVSN_CD: sideCode,
    ORD_SVR_DVSN_CD: "0"
  };
  
  // === AOPS_EVT: retry_start — KIS 주문 API 호출 ===
  const corrOrder = {
    endpoint: "/uapi/overseas-stock/v1/trading/order",
    symbol: intent.symbol,
    intent_id: intent.intent_id
  };
  emitEvent({
    proc: PROC,
    event: "retry_start",
    sev: "WARN",
    corr: corrOrder,
    msg: `KIS order ${side} ${intent.symbol} qty=${qty} px=${px}`,
    ctx: { attempt: 1, side, qty, limit_px: px }
  });

  // [PATCH-4] httpsRequestJson으로 교체 (timeout 방어)
  const orderOptions = {
    hostname: CFG.kis.baseUrl,
    port: CFG.kis.port,
    path: "/uapi/overseas-stock/v1/trading/order",
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "authorization": `Bearer ${token}`,
      "appkey": CFG.kis.appKey,
      "appsecret": CFG.kis.appSecret,
      "tr_id": trId,
      "custtype": "P"
    }
  };
  
  // === AOPS_EVT v2: depCall 래퍼 — KIS 주문 API HTTP 호출 (증명 기반) ===
  let orderResult;
  const _kisApiStart = Date.now();
  try {
    orderResult = await depCall({
      proc: PROC,
      corr: { ...corrOrder, request_id: newRequestId() },
      dep: { name: "KIS", type: "http", method: "POST", path: "/uapi/overseas-stock/v1/trading/order", symbol: intent.symbol, side, qty }
    }, async () => {
      return await httpsRequestJson(orderOptions, body, 20000);
    });
    // HARDENING: Record KIS API latency
    const _kisApiLatencyMs = Date.now() - _kisApiStart;
    try {
      await redis.xadd("kpi:v1:kis_api_latency", "*",
        "latency_ms", String(_kisApiLatencyMs),
        "symbol", intent.symbol,
        "side", side,
        "qty", String(qty),
        "rt_cd", String(orderResult?.rt_cd || ""),
        "ts", new Date().toISOString()
      );
      await redis.set("kpi:kis_api:last_latency_ms", String(_kisApiLatencyMs), "EX", 300);
      // Rolling average
      const prevAvg = Number(await redis.get("kpi:kis_api:avg_latency_ms") || _kisApiLatencyMs);
      const newAvg = Math.round(prevAvg * 0.8 + _kisApiLatencyMs * 0.2);
      await redis.set("kpi:kis_api:avg_latency_ms", String(newAvg), "EX", 600);
      // Circuit breaker: if avg > 15s, set warning
      if (newAvg > 15000) {
        log("WARN", "KIS_API_SLOW", { avg_latency_ms: newAvg, last_latency_ms: _kisApiLatencyMs, symbol: intent.symbol });
        await redis.set("policy:kis_api_slow", "true", "EX", 300);
      }
    } catch (_metricsErr) { /* non-critical */ }
  } catch (e) {
    // HARDENING: Record failed API latency
    const _kisFailLatencyMs = Date.now() - _kisApiStart;
    try {
      await redis.xadd("kpi:v1:kis_api_latency", "*",
        "latency_ms", String(_kisFailLatencyMs),
        "symbol", intent.symbol, "side", side,
        "error", String(e.message).slice(0, 200),
        "ts", new Date().toISOString()
      );
    } catch (_) {}
    // === AOPS_EVT v1: retry_giveup — KIS 주문 네트워크 실패 ===
    emitEvent({
      proc: PROC,
      event: "retry_giveup",
      sev: "P1",
      corr: corrOrder,
      msg: `KIS order API failed: ${e.message}`,
      ctx: { attempt: 1, error: e.message, latency_ms: _kisFailLatencyMs }
    });
    throw e;
  }

  // === AOPS_EVT v1: retry_success — KIS 주문 API 응답 수신 ===
  emitEvent({
    proc: PROC,
    event: "retry_success",
    sev: "INFO",
    corr: corrOrder,
    msg: `KIS order API responded: rt_cd=${orderResult.rt_cd}`,
    ctx: { attempt: 1, rt_cd: orderResult.rt_cd, odno: orderResult.output?.ODNO || "" }
  });

  return { ...orderResult, adj_bp, final_qty: qty };
}

// ═══════════════════════════════════════════════════════════════════════
// EXECUTION ROUTER v6.1 — Lua-atomic, token-owned, 4AI Round-4+5 hardened
// v6 fixes: LUA-ATOMIC(lock+xadd+marker in single Lua eval),
//           TOKEN-LOCK(UUID ownership + compare-and-del Lua),
//           ABORT-UNCONFIRMED-FAIL(rt_cd:"1" instead of fake success),
//           All v3/v4/v5 fixes preserved
// ═══════════════════════════════════════════════════════════════════════

const EXEC_ROUTER_CFG = {
  policyKey: "policy:execution_router",
  pegStream: "stream:peg_requests",
  pegShadowStream: "stream:peg_requests_shadow",
  execQualityStream: "stream:execution_quality",
  pegStatePrefix: "peg:order:",
  intentLockPrefix: "exec:intent:",
  pegTimeoutMs: Number(process.env.PEG_TIMEOUT_MS || "5000"),
  pegPollIntervalMs: Number(process.env.PEG_POLL_MS || "500"),
  cancelWaitMs: Number(process.env.PEG_CANCEL_WAIT_MS || "5000"),
  cancelPollMs: 250,
  policyCacheTtlMs: 500,
  fallbackOnTimeout: true,
  fallbackOnAbort: true,
  fallbackOnError: true,
  forceMode: process.env.EXEC_ROUTER_FORCE_MODE || null,
  lockTtlSec: 300,
};

// ─── Lua Scripts (loaded once, cached by Redis) ───────────────────
// LUA_ACQUIRE_AND_PUBLISH: Atomically acquires intent lock + publishes peg request
// KEYS[1] = intentLockKey, KEYS[2] = pegStream, KEYS[3] = pegStateKey
// ARGV[1] = lockToken, ARGV[2] = lockTtlSec, ARGV[3] = pegRequestJson, ARGV[4] = pegId
const LUA_ACQUIRE_AND_PUBLISH = `
local lockKey = KEYS[1]
local pegStream = KEYS[2]
local pegStateKey = KEYS[3]
local token = ARGV[1]
local ttl = tonumber(ARGV[2])
local pegJson = ARGV[3]
local pegId = ARGV[4]

-- Step 1: Check if peg state already exists (crash recovery)
local existingStatus = redis.call('HGET', pegStateKey, 'status')
if existingStatus then
  return {'EXISTING', existingStatus, pegId}
end

-- Step 2: Try to acquire lock with NX
local acquired = redis.call('SET', lockKey, token, 'NX', 'EX', ttl)
if not acquired then
  -- Lock held by another process — read holder's token
  local holderToken = redis.call('GET', lockKey)
  return {'LOCKED', holderToken or 'unknown', pegId}
end

-- Step 3: Lock acquired — publish peg request + set submission marker
redis.call('XADD', pegStream, 'MAXLEN', '~', '10000', '*', 'json', pegJson)
redis.call('HSET', pegStateKey, 'submitted_by', token, 'submitted_at', tostring(ARGV[5] or '0'))
redis.call('EXPIRE', pegStateKey, ttl)
return {'ACQUIRED', token, pegId}
`;

// LUA_COMPARE_AND_DEL: Only delete lock if we own it (token matches)
// KEYS[1] = intentLockKey
// ARGV[1] = ourToken
const LUA_COMPARE_AND_DEL = `
local lockKey = KEYS[1]
local ourToken = ARGV[1]
local current = redis.call('GET', lockKey)
if current == ourToken then
  redis.call('DEL', lockKey)
  return 1
end
return 0
`;

let _routerPolicyCache = null;
let _routerPolicyCacheTs = 0;

async function loadExecutionRouterPolicy() {
  if (EXEC_ROUTER_CFG.forceMode) {
    return { mode: EXEC_ROUTER_CFG.forceMode };
  }
  const now = Date.now();
  if (_routerPolicyCache && (now - _routerPolicyCacheTs) < EXEC_ROUTER_CFG.policyCacheTtlMs) {
    return _routerPolicyCache;
  }
  try {
    const raw = await redis.get(EXEC_ROUTER_CFG.policyKey);
    if (raw) {
      const parsed = JSON.parse(raw);
      _routerPolicyCache = parsed;
      _routerPolicyCacheTs = now;
      return parsed;
    }
  } catch (e) {
    log("WARN", "[EXEC_ROUTER] Policy load error, forcing disabled", { error: String(e?.message || e) });
    return { mode: "disabled" };
  }
  return _routerPolicyCache || { mode: "disabled" };
}

async function getArrivalQuote(symbol) {
  // [STRUCTURAL_FIX] Multi-source arrival quote with cascading fallback
  // Priority: 1) premium:polygon (if populated) → 2) price:* (realtime-data-feed) → 3) zeros
  try {
    // Source 1: premium:polygon (legacy Pegger data)
    const key = `premium:polygon:${symbol}`;
    const keyType = await redis.type(key);
    if (keyType === "hash") {
      const h = await redis.hgetall(key);
      if (h && h.data) {
        const d = JSON.parse(h.data);
        const bid = Number(d.bp || d.bid || 0);
        const ask = Number(d.ap || d.ask || 0);
        if (bid > 0 && ask > 0) return { bid, ask, last: Number(d.c || d.close || h.close || 0), ts_ms: Date.now(), source: "premium:polygon:hash" };
      }
      const bid = Number(h.bid || h.bp || 0);
      const ask = Number(h.ask || h.ap || 0);
      if (bid > 0 && ask > 0) return { bid, ask, last: Number(h.close || h.last || 0), ts_ms: Date.now(), source: "premium:polygon:hash_flat" };
    } else if (keyType === "string") {
      const raw = await redis.get(key);
      if (raw) {
        const d = JSON.parse(raw);
        const bid = Number(d.bid || d.bp || 0);
        const ask = Number(d.ask || d.ap || 0);
        if (bid > 0 && ask > 0) return { bid, ask, last: Number(d.c || d.close || 0), ts_ms: Date.now(), source: "premium:polygon:string" };
      }
    }
  } catch (e) { /* non-critical */ }
  // Source 2: price:* keys from realtime-data-feed (always available)
  try {
    const lastRaw = await redis.get(`price:${symbol}`);
    if (lastRaw) {
      const last = Number(lastRaw);
      if (last > 0) {
        // Estimate bid/ask from last price with minimal spread (conservative 2bps)
        const halfSpread = last * 0.0001; // 1bps each side
        return { bid: last - halfSpread, ask: last + halfSpread, last, ts_ms: Date.now(), source: "price:realtime_estimated" };
      }
    }
  } catch (e) { /* non-critical */ }
  return { bid: 0, ask: 0, last: 0, ts_ms: Date.now(), source: "none" };
}

function buildPegRequest(intent, arrivalQuote, deterministicPegId) {
  const pegId = deterministicPegId || `PEG-${String(intent.intent_id || Date.now())}`;
  const symbol = String(intent.symbol || "").trim();
  if (!symbol) {
    throw new Error(`[EXEC_ROUTER] Missing symbol for peg request`);
  }
  const signedDelta = Number(intent.delta_shares || 0);
  const qty = Math.abs(signedDelta);
  if (!qty || qty <= 0 || !Number.isFinite(qty)) {
    throw new Error(`[EXEC_ROUTER] Invalid delta_shares for peg request: ${intent.delta_shares}`);
  }
  const sideFromDelta = signedDelta > 0 ? "BUY" : "SELL";
  const side = String(intent.side || sideFromDelta).toUpperCase();
  if (intent.side && side !== sideFromDelta) {
    throw new Error(`[EXEC_ROUTER] side/delta_shares direction mismatch: side=${side} delta_shares=${signedDelta}`);
  }
  return {
    peg_id: pegId,
    symbol: symbol,
    side: side,
    qty: qty,
    delta_shares: signedDelta,
    tick: 0.01,
    measured_bid: arrivalQuote.bid,
    measured_ask: arrivalQuote.ask,
    measured_spread_bps: (arrivalQuote.bid > 0 && arrivalQuote.ask > arrivalQuote.bid)
      ? Math.round((arrivalQuote.ask - arrivalQuote.bid) / ((arrivalQuote.ask + arrivalQuote.bid) / 2) * 10000) : 0,
    measured_quote_ts_ms: arrivalQuote.ts_ms,
    notional_usd: qty * Number(intent.last_price || arrivalQuote.last || 0),
    link: {
      intent_id: String(intent.intent_id || ""),
      origin_intent_id: String(intent.origin_intent_id || intent.intent_id || ""),
      cycle_id: String(intent.cycle_id || ""),
    },
    last_price: Number(intent.last_price || 0),
    ref_px: Number(intent?.limit?.price || intent?.last_price || 0),
  };
}

const TERMINAL_STATES = new Set(["DONE", "ABORTED", "CANCELLED", "REJECTED", "ERROR"]);

async function pollPegResult(pegId, timeoutMs) {
  const stateKey = `${EXEC_ROUTER_CFG.pegStatePrefix}${pegId}`;
  const startMs = Date.now();
  const deadline = startMs + timeoutMs;
  let consecutiveErrors = 0;
  while (Date.now() < deadline) {
    try {
      const state = await redis.hgetall(stateKey);
      consecutiveErrors = 0;
      if (state && state.status && TERMINAL_STATES.has(String(state.status).toUpperCase())) {
        return {
          completed: true, status: state.status,
          filled_qty: Number(state.filled_qty || 0),
          remaining_qty: Number(state.remaining_qty || 0),
          avg_fill_price: Number(state.avg_fill_price || 0),
          broker_order_no: state.broker_order_no || "",
          slippage_bps: (state.slippage_bps !== undefined && state.slippage_bps !== "" && state.slippage_bps !== null) ? Number(state.slippage_bps) : null,
          escape_triggered: state.escape_triggered === "true",
          elapsed_ms: Date.now() - startMs,
        };
      }
    } catch (e) {
      consecutiveErrors++;
      log("WARN", "[EXEC_ROUTER] Poll Redis error", { peg_id: pegId, error: String(e?.message || e).slice(0, 100), consecutive_errors: consecutiveErrors });
      if (consecutiveErrors >= 5) {
        return { completed: false, status: "REDIS_ERROR", filled_qty: 0, remaining_qty: 0, avg_fill_price: 0, broker_order_no: "", slippage_bps: null, escape_triggered: false, elapsed_ms: Date.now() - startMs };
      }
    }
    await new Promise(r => setTimeout(r, EXEC_ROUTER_CFG.pegPollIntervalMs));
  }
  return { completed: false, status: "TIMEOUT", filled_qty: 0, remaining_qty: 0, avg_fill_price: 0, broker_order_no: "", slippage_bps: null, escape_triggered: false, elapsed_ms: Date.now() - startMs };
}

async function abortAndWaitForPegger(pegId) {
  const stateKey = `${EXEC_ROUTER_CFG.pegStatePrefix}${pegId}`;
  try {
    await redis.hset(stateKey, "cancel_requested", "true", "cancel_requested_ts", String(Date.now()));
    log("INFO", "[EXEC_ROUTER] Cancel requested for pegger", { peg_id: pegId });
    const cancelDeadline = Date.now() + EXEC_ROUTER_CFG.cancelWaitMs;
    while (Date.now() < cancelDeadline) {
      try {
        const state = await redis.hgetall(stateKey);
        if (state && state.status && TERMINAL_STATES.has(String(state.status).toUpperCase())) {
          log("INFO", "[EXEC_ROUTER] Pegger confirmed cancel", { peg_id: pegId, status: state.status, filled_qty: state.filled_qty });
          return {
            confirmed: true, status: state.status,
            filled_qty: Number(state.filled_qty || 0),
            broker_order_no: state.broker_order_no || "",
            avg_fill_price: Number(state.avg_fill_price || 0),
          };
        }
      } catch (e) { /* retry */ }
      await new Promise(r => setTimeout(r, EXEC_ROUTER_CFG.cancelPollMs));
    }
    const finalState = await redis.hgetall(stateKey).catch(() => ({}));
    log("WARN", "[EXEC_ROUTER] Cancel wait timeout, using last known state", { peg_id: pegId, status: finalState?.status, filled_qty: finalState?.filled_qty });
    return {
      confirmed: false, status: finalState?.status || "UNKNOWN",
      filled_qty: Number(finalState?.filled_qty || 0),
      broker_order_no: finalState?.broker_order_no || "",
      avg_fill_price: Number(finalState?.avg_fill_price || 0),
    };
  } catch (e) {
    log("ERROR", "[EXEC_ROUTER] Abort handshake error", { peg_id: pegId, error: String(e?.message || e) });
    return { confirmed: false, status: "ERROR", filled_qty: 0, broker_order_no: "", avg_fill_price: 0 };
  }
}

function emitRouterTelemetry(intent, route, policyMode, arrivalQuote, pegResult, directResult, elapsedMs) {
  try {
    const submittedQty = Math.abs(Number(intent.delta_shares || 0));
    const bid = Number(arrivalQuote?.bid || 0) || null;
    const ask = Number(arrivalQuote?.ask || 0) || null;
    const last = Number(arrivalQuote?.last || 0) || null;
    const mid = (bid && ask && ask >= bid) ? ((bid + ask) / 2) : (last || null);
    const spreadBps = (bid && ask && ask >= bid && mid) ? (((ask - bid) / mid) * 10000) : null;
    const pegFilledQty = Number(pegResult?.filled_qty || 0) || 0;
    const pegAvgFillPrice = Number(pegResult?.avg_fill_price || 0) || null;
    const directOdno = directResult?.output?.ODNO || null;
    const directAccepted = String(directResult?.rt_cd || "") === "0" && directOdno;
    const status = pegResult?.status || (directAccepted ? "SUBMITTED" : (directResult?.rt_cd ? "REJECTED" : "UNKNOWN"));
    const record = {
      schema: "ExecutionQualityEvent", version: "4.1", ts: new Date().toISOString(),
      intent_id: String(intent.intent_id || ""), origin_intent_id: String(intent.origin_intent_id || ""),
      symbol: String(intent.symbol || ""), side: String(intent.side || ""),
      original_qty: submittedQty,
      requested_qty: submittedQty,
      submitted_qty: submittedQty,
      filled_qty: pegFilledQty,
      avg_fill_price: pegAvgFillPrice,
      ref_price: mid || last || Number(intent?.limit?.price ?? intent?.last_price ?? 0) || null,
      limit_price: Number(intent?.limit?.price ?? intent?.last_price ?? 0) || null,
      bid, ask, mid, spread_bps: spreadBps,
      broker_order_id: pegResult?.broker_order_no || directOdno || "",
      status,
      route, policy_mode: policyMode,
      arrival_bid: bid, arrival_ask: ask, arrival_last: last, arrival_ts_ms: arrivalQuote?.ts_ms || null,
      peg_id: pegResult?.peg_id || null,
      peg_status: pegResult?.status || null, peg_filled_qty: pegFilledQty,
      peg_avg_fill_price: pegAvgFillPrice || 0, peg_slippage_bps: pegResult?.slippage_bps ?? null,
      peg_escape_triggered: pegResult?.escape_triggered || false, peg_elapsed_ms: pegResult?.elapsed_ms || 0,
      direct_rt_cd: directResult?.rt_cd || null, direct_odno: directOdno,
      fallback_qty: directResult?._fallback_qty || null,
      total_elapsed_ms: elapsedMs,
      price_age_ms: arrivalQuote?.ts_ms ? Math.max(0, Date.now() - Number(arrivalQuote.ts_ms || 0)) : null,
    };
    redis.xadd(EXEC_ROUTER_CFG.execQualityStream, "MAXLEN", "~", "50000", "*", "json", JSON.stringify(record)).catch(e => {
      log("WARN", "[EXEC_ROUTER] Telemetry XADD failed", { error: String(e?.message || e).slice(0, 100) });
    });
  } catch (e) { /* non-critical telemetry */ }
}

async function routeAndExecute(intent) {
  const routerStartMs = Date.now();
  const policy = await loadExecutionRouterPolicy();
  const mode = String(policy?.mode || "disabled").toLowerCase();
  const arrivalQuote = await getArrivalQuote(intent.symbol);
  const originalDelta = Number(intent.delta_shares || 0);
  const originalSign = Math.sign(originalDelta) || 1;
  const originalQty = Math.abs(originalDelta);

  log("INFO", "[EXEC_ROUTER] Routing intent", {
    intent_id: intent.intent_id, symbol: intent.symbol, side: intent.side, mode,
    original_qty: originalQty, original_sign: originalSign,
    arrival_bid: arrivalQuote.bid, arrival_ask: arrivalQuote.ask,
  });

  // MODE: DISABLED — direct KIS only (original behavior)
  if (mode === "disabled") {
    const result = await executeKISOrder(intent);
    const elapsed = Date.now() - routerStartMs;
    emitRouterTelemetry(intent, "direct_only", mode, arrivalQuote, null, result, elapsed);
    return result;
  }

  // MODE: SHADOW_ONLY — direct KIS + async shadow peg publish
  if (mode === "shadow_only") {
    const result = await executeKISOrder(intent);
    const elapsed = Date.now() - routerStartMs;
    try {
      const pegReq = buildPegRequest(intent, arrivalQuote);
      redis.xadd(EXEC_ROUTER_CFG.pegShadowStream, "MAXLEN", "~", "10000", "*", "json", JSON.stringify(pegReq)).catch(e => {
        log("WARN", "[EXEC_ROUTER] Shadow peg publish failed", { intent_id: intent.intent_id, error: String(e?.message || e).slice(0, 100) });
      });
    } catch (e) {
      log("WARN", "[EXEC_ROUTER] Shadow peg build failed", { intent_id: intent.intent_id, error: String(e?.message || e).slice(0, 100) });
    }
    emitRouterTelemetry(intent, "shadow_direct", mode, arrivalQuote, null, result, elapsed);
    return result;
  }

  // MODE: LIVE — pegger primary with Lua-atomic lock, direct KIS fallback
  if (mode === "live") {
    const deterministicPegId = `PEG-${String(intent.intent_id || Date.now())}`;
    const intentLockKey = `${EXEC_ROUTER_CFG.intentLockPrefix}${intent.intent_id}`;
    const pegStateKey = `${EXEC_ROUTER_CFG.pegStatePrefix}${deterministicPegId}`;
    const lockToken = `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    let pegId = deterministicPegId;
    let confirmedPegFills = 0;

    try {
      // ─── LUA ATOMIC: Lock + Publish + Marker in one round-trip ───
      const pegReq = buildPegRequest(intent, arrivalQuote, deterministicPegId);
      const pegReqJson = JSON.stringify(pegReq);

      const luaResult = await redis.eval(
        LUA_ACQUIRE_AND_PUBLISH,
        3,  // number of KEYS
        intentLockKey, EXEC_ROUTER_CFG.pegStream, pegStateKey,  // KEYS
        lockToken, String(EXEC_ROUTER_CFG.lockTtlSec), pegReqJson, deterministicPegId, String(Date.now())  // ARGV
      );

      const [action, detail, returnedPegId] = luaResult;
      pegId = returnedPegId || deterministicPegId;

      if (action === "EXISTING") {
        // Peg state already exists — crash recovery, skip XADD, just poll
        log("INFO", "[EXEC_ROUTER] Lua: existing peg state found, resuming poll", {
          intent_id: intent.intent_id, peg_id: pegId, existing_status: detail
        });
        // Check if already terminal
        if (TERMINAL_STATES.has(String(detail).toUpperCase())) {
          const existingState = await redis.hgetall(pegStateKey).catch(() => ({}));
          const filledQty = Number(existingState.filled_qty || 0);
          const remaining = Math.max(0, originalQty - filledQty);
          if (remaining <= 0) {
            const elapsed = Date.now() - routerStartMs;
            emitRouterTelemetry(intent, "existing_peg_complete", mode, arrivalQuote, { peg_id: pegId, status: detail, filled_qty: filledQty }, null, elapsed);
            return { rt_cd: "0", output: { ODNO: existingState.broker_order_no || pegId }, adj_bp: 0, final_qty: filledQty, _routed_via: "existing_peg_complete", _peg_id: pegId };
          }
          // Partial — fallback for remaining
          let fallbackIntent = { ...intent };
          fallbackIntent.delta_shares = originalSign * remaining;
          const directResult = await executeKISOrder(fallbackIntent);
          directResult._fallback_qty = remaining;
          const elapsed = Date.now() - routerStartMs;
          emitRouterTelemetry(intent, "existing_peg_partial_fallback", mode, arrivalQuote, { peg_id: pegId, status: detail, filled_qty: filledQty }, directResult, elapsed);
          return directResult;
        }
      } else if (action === "LOCKED") {
        // Another process holds the lock — check if they published
        const holderToken = detail;
        log("INFO", "[EXEC_ROUTER] Lua: lock held by another process", {
          intent_id: intent.intent_id, peg_id: pegId, holder_token: String(holderToken).slice(0, 20)
        });
        // Check if peg state was created by holder (use HGET submitted_by for stronger check per 4AI)
        const holderSubmittedBy = await redis.hget(pegStateKey, "submitted_by").catch(() => null);
        if (!holderSubmittedBy) {
          // Orphan lock: holder crashed between lock and XADD (no submitted_by marker)
          // We cannot re-publish because we don't own the lock — wait briefly
          log("WARN", "[EXEC_ROUTER] Possible orphan lock (no submitted_by), waiting for holder or TTL expiry", { peg_id: pegId });
          await new Promise(r => setTimeout(r, 2000));
          // Re-check after wait with stronger HGET
          const retrySubmittedBy = await redis.hget(pegStateKey, "submitted_by").catch(() => null);
          if (!retrySubmittedBy) {
            // Still no peg state — holder definitely crashed. Force fallback to direct KIS
            log("WARN", "[EXEC_ROUTER] Orphan lock confirmed after wait, falling back to direct KIS", { peg_id: pegId });
            const directResult = await executeKISOrder(intent);
            const elapsed = Date.now() - routerStartMs;
            emitRouterTelemetry(intent, "orphan_lock_direct_fallback", mode, arrivalQuote, null, directResult, elapsed);
            return directResult;
          }
        }
        // Holder's peg exists — resume polling with their pegId
      } else if (action === "ACQUIRED") {
        // Success — lock acquired + peg published + marker set atomically
        log("INFO", "[EXEC_ROUTER] Lua: atomic lock+publish success", {
          peg_id: pegId, symbol: intent.symbol, side: intent.side, qty: originalQty, lock_token: lockToken.slice(0, 20)
        });
      } else {
        // Unknown Lua response — safe fallback
        log("ERROR", "[EXEC_ROUTER] Lua: unexpected response", { action, detail, peg_id: pegId });
        const directResult = await executeKISOrder(intent);
        const elapsed = Date.now() - routerStartMs;
        emitRouterTelemetry(intent, "lua_unexpected_fallback", mode, arrivalQuote, null, directResult, elapsed);
        return directResult;
      }

      // ─── Poll for pegger result ───
      const pegResult = await pollPegResult(pegId, EXEC_ROUTER_CFG.pegTimeoutMs);
      confirmedPegFills = pegResult.filled_qty || 0;

      if (pegResult.completed) {
        const remaining = Math.max(0, originalQty - Math.min(pegResult.filled_qty, originalQty));
        if (pegResult.filled_qty > originalQty) {
          log("ERROR", "[EXEC_ROUTER] filled_qty exceeds original, clamping", { peg_id: pegId, original_qty: originalQty, reported_filled: pegResult.filled_qty });
        }
        if (remaining <= 0) {
          const elapsed = Date.now() - routerStartMs;
          log("INFO", "[EXEC_ROUTER] Pegger complete", { peg_id: pegId, status: pegResult.status, filled_qty: pegResult.filled_qty, avg_fill_price: pegResult.avg_fill_price, slippage_bps: pegResult.slippage_bps, elapsed_ms: elapsed });
          emitRouterTelemetry(intent, "pegger_complete", mode, arrivalQuote, { ...pegResult, peg_id: pegId }, null, elapsed);
          return { rt_cd: "0", output: { ODNO: pegResult.broker_order_no || pegId }, adj_bp: 0, final_qty: pegResult.filled_qty, _routed_via: "pegger_complete", _peg_id: pegId, _peg_slippage_bps: pegResult.slippage_bps };
        }
        // Partial fill — fallback for remaining
        log("WARN", "[EXEC_ROUTER] Pegger partial, fallback for remaining", { peg_id: pegId, status: pegResult.status, filled_qty: pegResult.filled_qty, remaining });
        if (EXEC_ROUTER_CFG.fallbackOnAbort) {
          let fallbackIntent = { ...intent };
          fallbackIntent.delta_shares = originalSign * remaining;
          const directResult = await executeKISOrder(fallbackIntent);
          directResult._fallback_qty = remaining;
          const elapsed = Date.now() - routerStartMs;
          emitRouterTelemetry(intent, "pegger_partial_fallback", mode, arrivalQuote, { ...pegResult, peg_id: pegId }, directResult, elapsed);
          return directResult;
        }
        const elapsed = Date.now() - routerStartMs;
        emitRouterTelemetry(intent, "pegger_partial_no_fallback", mode, arrivalQuote, { ...pegResult, peg_id: pegId }, null, elapsed);
        return { rt_cd: "0", output: { ODNO: pegResult.broker_order_no || pegId }, adj_bp: 0, final_qty: pegResult.filled_qty, _routed_via: "pegger_partial_no_fallback", _peg_id: pegId };
      }

      // ─── TIMEOUT or REDIS_ERROR — abort handshake ───
      log("WARN", "[EXEC_ROUTER] Pegger timeout/error, initiating abort handshake", { peg_id: pegId, peg_status: pegResult.status, peg_elapsed_ms: pegResult.elapsed_ms });
      const abortResult = await abortAndWaitForPegger(pegId);
      confirmedPegFills = abortResult.filled_qty || 0;
      const remaining = Math.max(0, originalQty - confirmedPegFills);

      if (remaining <= 0) {
        const elapsed = Date.now() - routerStartMs;
        log("INFO", "[EXEC_ROUTER] Pegger filled during abort window", { peg_id: pegId, filled_qty: confirmedPegFills });
        emitRouterTelemetry(intent, "pegger_late_complete", mode, arrivalQuote, { ...abortResult, peg_id: pegId, elapsed_ms: pegResult.elapsed_ms }, null, elapsed);
        return { rt_cd: "0", output: { ODNO: abortResult.broker_order_no || pegId }, adj_bp: 0, final_qty: confirmedPegFills, _routed_via: "pegger_late_complete", _peg_id: pegId };
      }

      // [ARES-PATCH-v5] Abort NOT confirmed → KIS direct fallback (was: return FAILURE)
      if (!abortResult.confirmed) {
        log("WARN", "[EXEC_ROUTER] Abort NOT confirmed — attempting KIS direct fallback", {
          peg_id: pegId, abort_status: abortResult.status, filled_qty: confirmedPegFills
        });
        // Idempotency lock: prevent double execution
        const directLockKey = `exec:direct_lock:${intent.intent_id}`;
        const lockOk = await redis.set(directLockKey, String(Date.now()), "NX", "EX", 300).catch(() => null);
        if (lockOk !== "OK") {
          log("CRITICAL", "[EXEC_ROUTER] Direct lock failed (already executing?) — returning FAILURE", { intent_id: intent.intent_id });
          const elapsed = Date.now() - routerStartMs;
          emitRouterTelemetry(intent, "abort_unconfirmed_lock_fail", mode, arrivalQuote, { ...abortResult, peg_id: pegId }, null, elapsed);
          return { rt_cd: "1", msg1: "EXEC_ROUTER_DIRECT_LOCK_FAIL", output: { ODNO: "" }, adj_bp: 0, final_qty: confirmedPegFills, _routed_via: "abort_unconfirmed_lock_fail", _peg_id: pegId };
        }
        // Fallback to KIS direct for remaining qty
        const abortRemaining = Math.max(0, originalQty - confirmedPegFills);
        if (abortRemaining > 0) {
          let fallbackIntent = { ...intent };
          fallbackIntent.delta_shares = originalSign * abortRemaining;
          log("INFO", "[EXEC_ROUTER] KIS direct fallback after abort_unconfirmed", { peg_id: pegId, fallback_qty: abortRemaining });
          const directResult = await executeKISOrder(fallbackIntent);
          directResult._fallback_qty = abortRemaining;
          directResult._routed_via = "abort_unconfirmed_direct_fallback";
          directResult._peg_id = pegId;
          const elapsed = Date.now() - routerStartMs;
          emitRouterTelemetry(intent, "abort_unconfirmed_direct_fallback", mode, arrivalQuote, { ...abortResult, peg_id: pegId }, directResult, elapsed);
          return directResult;
        }
      }

      // Abort confirmed — fallback for remaining
      const allowFallback = (pegResult.status === "TIMEOUT" && EXEC_ROUTER_CFG.fallbackOnTimeout) ||
                            (pegResult.status === "REDIS_ERROR" && EXEC_ROUTER_CFG.fallbackOnError);
      if (allowFallback) {
        let fallbackIntent = { ...intent };
        fallbackIntent.delta_shares = originalSign * remaining;
        log("INFO", "[EXEC_ROUTER] Fallback after abort handshake", { peg_id: pegId, confirmed_fills: confirmedPegFills, fallback_qty: remaining });
        const directResult = await executeKISOrder(fallbackIntent);
        directResult._fallback_qty = remaining;
        const elapsed = Date.now() - routerStartMs;
        emitRouterTelemetry(intent, "direct_fallback_after_abort", mode, arrivalQuote, { ...abortResult, peg_id: pegId, elapsed_ms: pegResult.elapsed_ms }, directResult, elapsed);
        return directResult;
      }
      throw new Error(`Pegger failed (${pegResult.status}) and fallback disabled`);

    } catch (pegError) {
      log("ERROR", "[EXEC_ROUTER] Pegger error, evaluating fallback", { peg_id: pegId, error: String(pegError?.message || pegError).slice(0, 200) });
      if (EXEC_ROUTER_CFG.fallbackOnError) {
        // Check if pegger is still active before fallback
        try {
          const lastPegState = await redis.hgetall(pegStateKey).catch(() => null);
          if (lastPegState && lastPegState.status) {
            if (!TERMINAL_STATES.has(String(lastPegState.status).toUpperCase())) {
              log("CRITICAL", "[EXEC_ROUTER] Catch path: pegger still active, aborting before fallback", { peg_id: pegId, status: lastPegState.status });
              const abortResult = await abortAndWaitForPegger(pegId);
              confirmedPegFills = Math.max(confirmedPegFills, abortResult.filled_qty || 0);
              if (!abortResult.confirmed) {
                log("WARN", "[EXEC_ROUTER] Catch path: abort NOT confirmed — proceeding to KIS direct fallback", { peg_id: pegId });
                // [ARES-PATCH-v5] Don't return FAILURE — let the catch-path fallback handle it
                // The code below (errorFallbackIntent) will execute KIS direct for remaining qty
              }
            } else {
              confirmedPegFills = Math.max(confirmedPegFills, Number(lastPegState.filled_qty || 0));
            }
          }
        } catch (stateCheckErr) {
          log("WARN", "[EXEC_ROUTER] Catch path peg state check failed", { error: String(stateCheckErr?.message || stateCheckErr).slice(0, 100) });
        }
        let errorFallbackIntent = { ...intent };
        const remaining = Math.max(0, originalQty - confirmedPegFills);
        if (remaining <= 0) {
          log("INFO", "[EXEC_ROUTER] Catch path: pegger already filled everything", { confirmed_fills: confirmedPegFills });
          const elapsed = Date.now() - routerStartMs;
          emitRouterTelemetry(intent, "error_pegger_already_filled", mode, arrivalQuote, { peg_id: pegId, filled_qty: confirmedPegFills }, null, elapsed);
          return { rt_cd: "0", output: { ODNO: pegId }, adj_bp: 0, final_qty: confirmedPegFills, _routed_via: "error_pegger_filled", _peg_id: pegId };
        }
        errorFallbackIntent.delta_shares = originalSign * remaining;
        log("INFO", "[EXEC_ROUTER] Error fallback with partial fill adjustment", { confirmed_fills: confirmedPegFills, fallback_qty: remaining });
        const directResult = await executeKISOrder(errorFallbackIntent);
        const elapsed = Date.now() - routerStartMs;
        emitRouterTelemetry(intent, "direct_fallback_error", mode, arrivalQuote, { peg_id: pegId, filled_qty: confirmedPegFills }, directResult, elapsed);
        return directResult;
      }
      throw pegError;
    } finally {
      // [TOKEN-LOCK] Only delete lock if we own it — Lua compare-and-del
      try {
        await redis.eval(LUA_COMPARE_AND_DEL, 1, intentLockKey, lockToken);
      } catch (e) {
        log("WARN", "[EXEC_ROUTER] Lock cleanup failed", { error: String(e?.message || e).slice(0, 100) });
      }
    }
  }

  // UNKNOWN MODE — safe default to direct
  log("WARN", "[EXEC_ROUTER] Unknown mode, defaulting to direct KIS", { mode });
  const result = await executeKISOrder(intent);
  const elapsed = Date.now() - routerStartMs;
  emitRouterTelemetry(intent, "direct_unknown_mode", mode, arrivalQuote, null, result, elapsed);
  return result;
}

// ═══════════════════════════════════════════════════════════════════════
// END EXECUTION ROUTER v6.1
// ═══════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════

// Process Intent

function isUsMarketSessionNY() {
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hhmm = ny.getHours() * 100 + ny.getMinutes();
  return hhmm >= 930 && hhmm <= 1600; // executor ack/cleanup until 16:00
}

// === GPT-PATCH-2: Decision event emission for stability_judge ===
const DECISION_STREAM = process.env.DECISION_STREAM || "stream:order_decisions";

async function emitDecision({ intent, action, drop_reason = null, drop_detail = null, broker_order_id = null }) {
  try {
    // [CHAIN-100] Bulletproof origin + schema injection
    const originIntentId = String(intent?.origin_intent_id ?? intent?.intent_id ?? intent?.intentId ?? "");
    const cycleId = String(intent?.cycle_id ?? "");
    const qty = Number(intent?.delta_shares ?? 0);
    const refPx = Number(intent?.limit?.price ?? intent?.ref_px ?? intent?.last_price ?? 0);
    const notional = Math.abs(qty * refPx);

    const intentId = String(intent?.intent_id ?? "");
    const symbol = String(intent?.symbol ?? "");
    const side = String(intent?.side ?? "");
    if (!intentId || !symbol || !side) return;

    let strategyVersion = "unknown";
    try {
      strategyVersion = (await redis.get("strategy:active_version")) || "unknown";
    } catch {}

    const ev = {
      schema: "OrderDecisionEvent",
      version: "1.0",
      ts_ms: Date.now(),
      strategy_version: strategyVersion,
      signal: {
        signal_id: intentId,
        symbol,
        side,
        signal_ts_ms: intent?.ts ? Date.parse(intent.ts) : Date.now(),
      },
      decision: {
        action,
        order_id: action === "SENT" ? (broker_order_id || null) : null,
        drop_reason: action === "SKIP" ? (drop_reason || "UNKNOWN") : null,
        drop_detail: action === "SKIP" ? (drop_detail || {}) : null,
      },
      link: {
        intent_id: intentId,
        origin_intent_id: originIntentId || null,
        cycle_id: cycleId || null,
        broker_order_id: broker_order_id || null,
      },
      order_context: {
        ref_px: (refPx > 0 ? refPx : null),
        notional: (refPx > 0 && qty > 0 ? notional : null),
        qty: (qty > 0 ? qty : null),
      },
    };

    await redis.xadd(DECISION_STREAM, "MAXLEN", "~", "200000", "*", "json", JSON.stringify(ev));
  } catch {
    // best-effort: never block order flow
  }
}
// === END GPT-PATCH-2 ===


// [FIX-F2] 4AI consensus: Atomic cash reservation per cycle (prevents APBK0952)
const CASH_RESERVE_LUA = `
  local key = KEYS[1]
  local req = tonumber(ARGV[1])
  local ttl = tonumber(ARGV[2])
  local cur = tonumber(redis.call("GET", key) or "-1")
  if cur < 0 then return -1 end
  if cur >= req then
    redis.call("SET", key, tostring(cur - req), "EX", ttl)
    return 1
  end
  return 0
`;
async function reserveCash(redis, cycleId, requiredUSD) {
  const cashKey = `cycle_cash:${cycleId}:remaining`;
  const initKey = `cycle_cash:${cycleId}:init_done`;
  const TTL = 20 * 60; // 20min cycle TTL
  const initDone = await redis.get(initKey);
  if (!initDone) {
    // v2.0: usable_usd 우선, fallback available_cash
    const usableRaw = await redis.get("kis:cash:usable_usd");
    const availRaw = usableRaw || await redis.get("kis:live:available_cash");
    const avail = Number(availRaw || 0) * 0.95; // FINAL_CASH_CAP_95
    if (avail > 0) {
      await redis.set(cashKey, String(avail), "EX", TTL);
      await redis.set(initKey, "1", "EX", TTL);
    }
  }
  const ok = await redis.eval(CASH_RESERVE_LUA, 1, cashKey, String(requiredUSD), String(TTL));
  return ok === 1;
}
// [END FIX-F2]

// ═══════════════════════════════════════════════════════════════
// Cash Ledger Split v2.0 — Reservation Model + Cash Gate Feedback
// ═══════════════════════════════════════════════════════════════
const CASH_RESERVE_INTENT_PREFIX = "cash:reserve:intent:";
const CASH_RESERVE_ODNO_PREFIX = "cash:reserve:odno:";
const CASH_MODE_KEY = "policy:cash_gate:mode";
const BUY_SCALE_KEY = "policy:cash_gate:buy_scale";
const CASH_GATE_FAIL_STREAK_KEY = "policy:cash_gate:fail_streak";
const CASH_BACKOFF_PREFIX = "order:cash_backoff:";

// Lua: usable_usd 원자 차감 + intent reservation 기록
async function atomicReserveCashV2(redis, intentId, symbol, estCost) {
  // CB-2 FIX: 모든 키를 JS에서 완전히 구성하여 KEYS[]로 전달 (CROSSSLOT 방지)
  // CB-3 FIX: cash threshold는 고정 0.95 사용 (buy_scale은 intent_builder에서 sizing용으로만 사용)
  const LUA_RESERVE_V2 = `
    local usable = tonumber(redis.call('GET', KEYS[1]) or '0')
    local legacy = tonumber(redis.call('GET', KEYS[2]) or '0')
    local cash = (usable > 0) and usable or legacy
    local cost = tonumber(ARGV[1])
    local symbol = ARGV[2]
    local ts = ARGV[3]
    if cash >= (cost * 0.95) then
      local newCash = cash - cost
      if newCash < 0 then newCash = 0 end
      redis.call('SET', KEYS[1], tostring(newCash))
      redis.call('SET', KEYS[2], tostring(newCash))
      redis.call('SET', KEYS[4],
        cjson.encode({cost=cost, symbol=symbol, ts=ts, status='PENDING'}), 'EX', 1200)
      redis.call('INCRBY', KEYS[3], math.floor(cost * 100))
      return 1
    else
      return 0
    end
  `;
  // CB-2 FIX: intentKey를 JS에서 완전히 구성하여 KEYS[4]로 전달
  const intentKey = `cash:reserve:intent:${intentId}`;
  const result = await redis.eval(LUA_RESERVE_V2, 4,
    'kis:cash:usable_usd', 'kis:live:available_cash',
    'kis:cash:reserved_buy_usd_local', intentKey,
    String(estCost), symbol, String(Date.now()));
  return Number(result) === 1;
}

// Lua: reservation 해제 + usable_usd 복원
// FIX-6: partial fill 처리 — releaseCost 파라미터 추가
// CB-2 FIX: intentKey를 JS에서 완전히 구성하여 KEYS[]로 전달 (CROSSSLOT 방지)
async function releaseReservation(redis, intentId, estCost, filledValue = 0) {
  const LUA_RELEASE = `
    local raw = redis.call('GET', KEYS[1])
    if raw then
      local origCost = tonumber(ARGV[1])
      local filledVal = tonumber(ARGV[2]) or 0
      local releaseCost = origCost - filledVal
      if releaseCost < 0 then releaseCost = 0 end
      redis.call('DEL', KEYS[1])
      local usable = tonumber(redis.call('GET', KEYS[2]) or '0')
      redis.call('SET', KEYS[2], tostring(usable + releaseCost))
      redis.call('SET', KEYS[3], tostring(usable + releaseCost))
      local localReserved = tonumber(redis.call('GET', KEYS[4]) or '0')
      local decrAmt = math.floor(origCost * 100)
      if decrAmt > localReserved then decrAmt = localReserved end
      if decrAmt > 0 then redis.call('DECRBY', KEYS[4], decrAmt) end
      return 1
    end
    return 0
  `;
  // CB-2 FIX: intentKey를 JS에서 완전히 구성
  const intentKey = `cash:reserve:intent:${intentId}`;
  await redis.eval(LUA_RELEASE, 4,
    intentKey, 'kis:cash:usable_usd',
    'kis:live:available_cash', 'kis:cash:reserved_buy_usd_local',
    String(estCost), String(filledValue)).catch(() => {});
}
// CB-1 FIX: cashGateFeedback 함수 제거 — reconciler가 streak/mode 전환의 single source of truth
// executor는 per-symbol backoff만 설정 (inline SET)

// ODNO 발급 후 intent reserve → ODNO reserve 승격
// FIX-4 + P1: atomic Lua로 변환 (race condition 제거)
async function promoteReservation(redis, intentId, odno, symbol, estCost) {
  const LUA_PROMOTE = `
    -- KEYS[1] = intent key, KEYS[2] = odno key, KEYS[3] = reserved_buy_usd_local
    -- ARGV[1] = odno JSON payload, ARGV[2] = estCost in cents
    redis.call('DEL', KEYS[1])
    redis.call('SET', KEYS[2], ARGV[1], 'EX', 3600)
    local decrAmt = tonumber(ARGV[2]) or 0
    if decrAmt > 0 then
      local cur = tonumber(redis.call('GET', KEYS[3]) or '0')
      if decrAmt > cur then decrAmt = cur end
      if decrAmt > 0 then
        redis.call('DECRBY', KEYS[3], decrAmt)
      end
    end
    return 1
  `;
  try {
    const intentKey = `${CASH_RESERVE_INTENT_PREFIX}${intentId}`;
    const odnoKey = `${CASH_RESERVE_ODNO_PREFIX}${odno}`;
    const odnoPayload = JSON.stringify({
      intentId, symbol, cost: estCost, ts: Date.now(), status: "SUBMITTED"
    });
    const decrAmtCents = Math.floor(estCost * 100);
    await redis.eval(LUA_PROMOTE, 3,
      intentKey, odnoKey, 'kis:cash:reserved_buy_usd_local',
      odnoPayload, String(decrAmtCents));
  } catch (e) {
    log("WARN", "RESERVE_PROMOTE_ERROR", { intentId, odno, error: String(e).slice(0, 100) });
  }
}

async function processIntent(msgId, intent) {
  // === STAGE-D B-SERIES LIVE-LINK VALIDATION-ONLY GUARD ===
  // Pure pre-order-path schema/route validation. This block does not mutate intent,
  // Redis, PM2, broker/order APIs, target weights, quantities, risk thresholds, champion, or DTCH state.
  const stageDValidation = validateStageDBSeriesIntent(intent, { msgId });
  if (!stageDValidation.ok) {
    log("WARN", STAGE_D_REJECT_MALFORMED_INTENT, {
      intent_id: String(intent?.intent_id || intent?.intentId || ""),
      msg_id: String(msgId || ""),
      symbol: String(intent?.symbol || ""),
      side: String(intent?.side || ""),
      code: stageDValidation.code,
      reason: stageDValidation.reason,
      stage_d_marker: stageDValidation.stage_d_marker,
      stage_d_schema_validation_pass: false,
      stage_d_route_deterministic: stageDValidation.stage_d_route_deterministic,
      stage_d_reject_reason_for_malformed_intent: stageDValidation.stage_d_reject_reason_for_malformed_intent,
      closed_event_validation_status: stageDValidation.closed_event_validation_status,
      redis_mutation: false,
      broker_order_api_called: false
    });
    return {
      ok: false,
      reason: `${STAGE_D_REJECT_MALFORMED_INTENT}:${stageDValidation.code}`,
      stage_d_marker: stageDValidation.stage_d_marker,
      stage_d_schema_validation_pass: false,
      stage_d_route_deterministic: stageDValidation.stage_d_route_deterministic,
      stage_d_reject_reason_for_malformed_intent: stageDValidation.stage_d_reject_reason_for_malformed_intent,
      closed_event_validation_status: stageDValidation.closed_event_validation_status
    };
  }
  log("INFO", STAGE_D_SCHEMA_VALIDATION_PASS, {
    intent_id: String(intent?.intent_id || intent?.intentId || msgId || ""),
    symbol: String(intent?.symbol || ""),
    side: String(intent?.side || ""),
    route_marker: stageDValidation.route_marker,
    stage_d_marker: stageDValidation.stage_d_marker,
    stage_d_schema_validation_pass: true,
    closed_event_validation_status: stageDValidation.closed_event_validation_status,
    redis_mutation: false,
    broker_order_api_called: false
  });
  log("INFO", STAGE_D_ROUTE_DETERMINISTIC, {
    intent_id: String(intent?.intent_id || intent?.intentId || msgId || ""),
    route_marker: stageDValidation.route_marker,
    stage_d_route_deterministic: stageDValidation.stage_d_route_deterministic,
    redis_mutation: false,
    broker_order_api_called: false
  });
  // === END STAGE-D B-SERIES LIVE-LINK VALIDATION-ONLY GUARD ===

  // ═══════ [SPOF-3A] Executor Watchdog Heartbeat ═══════
  // executor가 살아있음을 Redis에 기록 (autopilot watchdog이 확인 가능)
  redis.set("executor:watchdog:last_ts", String(Math.floor(Date.now()/1000)), "EX", 120).catch(() => {});

  // [CHAIN-100] Normalize intent_id + preserve origin for full chain tracking
  intent = intent || {};
  intent.origin_intent_id = String(intent.origin_intent_id || intent.intent_id || intent.intentId || msgId || "");
  intent.intent_id = String(intent.intent_id || intent.intentId || msgId || intent.origin_intent_id);

  // ═══════ [v8.0.0 FP-FIX P0-1] Strategy Version Fail-Closed Guard ═══════
  // If strategy_version != current champion version, reject BUY orders.
  // SELL orders are exempt (risk-reducing).
  try {
    const _intentSide = String(intent?.side || "").toUpperCase();
    if (_intentSide === "BUY") {
      const _championVer = await redis.get("champion:version").catch(() => null);
      const _activeVer = await redis.get("strategy:active_version").catch(() => null);
      const _intentVer = String(intent?.strategy_version || intent?.version || _activeVer || "unknown");
      if (_championVer && _intentVer !== _championVer && _intentVer !== "unknown") {
        log("WARN", "[FP-P0-1] VERSION_MISMATCH_GUARD: BUY blocked — stale strategy version", {
          intent_id: intent.intent_id, symbol: intent.symbol,
          intent_version: _intentVer, champion_version: _championVer
        });
        await redis.hincrby("metrics:drop_reasons", "VERSION_MISMATCH_BUY_BLOCKED", 1).catch(() => {});
        await emitDecision({ intent, action: "SKIP", drop_reason: "VERSION_MISMATCH_BUY_BLOCKED",
          drop_detail: { intent_version: _intentVer, champion_version: _championVer } });
        return;
      }
    }
  } catch (_verErr) {
    log("WARN", "[FP-P0-1] VersionGuard error (fail-open for safety)", { error: String(_verErr?.message || _verErr) });
  }

  // ═══════ [v8.0.0 FP-FIX P0-2] Target Positions Null Guard ═══════
  // If champion:targets:ssot is empty, block BUY orders (no anchor = no position building).
  try {
    const _intentSide2 = String(intent?.side || "").toUpperCase();
    if (_intentSide2 === "BUY") {
      const _targetCount = await redis.hlen("champion:targets:ssot").catch(() => 0);
      if (!_targetCount || _targetCount === 0) {
        log("WARN", "[FP-P0-2] TARGET_NULL_GUARD: BUY blocked — champion:targets:ssot is empty", {
          intent_id: intent.intent_id, symbol: intent.symbol, target_count: _targetCount
        });
        await redis.hincrby("metrics:drop_reasons", "TARGET_NULL_BUY_BLOCKED", 1).catch(() => {});
        await emitDecision({ intent, action: "SKIP", drop_reason: "TARGET_NULL_BUY_BLOCKED",
          drop_detail: { target_count: _targetCount } });
        return;
      }
    }
  } catch (_tgtErr) {
    log("WARN", "[FP-P0-2] TargetNullGuard error (fail-open for safety)", { error: String(_tgtErr?.message || _tgtErr) });
  }

  // ═══════ [SPOF-3A] PositionFreshnessGuard ═══════
  // position-sync-service가 30초마다 fake timestamp만 갱신하는 문제를 executor 레벨에서 방어
  // kis:broker:positions_sync_status의 last_sync_ts 기준으로 실제 동기화 freshness 확인
  try {
    const _pfgSide = String(intent?.side || "").toUpperCase();
    if (_pfgSide === "BUY") {
      const _syncStatusRaw = await redis.get("kis:broker:positions_sync_status").catch(() => null);
      if (_syncStatusRaw) {
        const _syncStatus = JSON.parse(_syncStatusRaw);
        const _lastSyncMs = new Date(_syncStatus.last_sync_ts || 0).getTime();
        const _ageMs = Date.now() - _lastSyncMs;
        const _maxAgeMs = 300_000; // 5분 이상 stale이면 BUY 차단
        if (_ageMs > _maxAgeMs) {
          const _ageSec = Math.floor(_ageMs / 1000);
          log("WARN", "[SPOF3A] POSITION_FRESHNESS_GUARD: BUY blocked — position data stale", {
            intent_id: intent.intent_id, symbol: intent.symbol,
            position_age_sec: _ageSec, max_age_sec: 300
          });
          await redis.hincrby("metrics:drop_reasons", "POSITION_STALE_BUY_BLOCKED", 1).catch(() => {});
          await emitDecision({ intent, action: "SKIP", drop_reason: "POSITION_STALE_BUY_BLOCKED",
            drop_detail: { position_age_sec: _ageSec } });
          // Redis 이벤트 기록
          await redis.xadd("ares:live:spof3a:events", "*",
            "ts", new Date().toISOString(),
            "event", "POSITION_FRESHNESS_GUARD",
            "intent_id", String(intent.intent_id),
            "symbol", String(intent.symbol),
            "age_sec", String(_ageSec)
          ).catch(() => {});
          return { status: "position_stale_blocked", ack: true };
        }
      }
    }
  } catch (_pfgErr) {
    log("WARN", "[SPOF3A] PositionFreshnessGuard error (fail-open)", { error: String(_pfgErr?.message || _pfgErr) });
  }

  // ═══════ [SPOF-3A] Intent Idempotency Gate ═══════
  // 동일 intent_id가 재시작 후 중복 실행되는 것을 Redis Set으로 방어
  // TTL: 24시간 (당일 내 중복만 방어, 다음 날 자동 만료)
  try {
    const _idemKey = `executor:processed_intents:${new Date().toISOString().slice(0,10)}`;
    const _idemId  = String(intent.intent_id || "");
    if (_idemId && _idemId !== msgId) { // msgId와 다를 때만 (msgId는 스트림 ID라 항상 unique)
      const _alreadyProcessed = await redis.sismember(_idemKey, _idemId).catch(() => 0);
      if (String(_alreadyProcessed) === "1") {
        log("WARN", "[SPOF3A] IDEMPOTENCY_GATE: duplicate intent_id blocked", {
          intent_id: _idemId, symbol: intent.symbol, msgId
        });
        await redis.hincrby("metrics:drop_reasons", "DUPLICATE_INTENT_BLOCKED", 1).catch(() => {});
        await emitDecision({ intent, action: "SKIP", drop_reason: "DUPLICATE_INTENT_BLOCKED",
          drop_detail: { original_intent_id: _idemId } });
        return { status: "duplicate_blocked", ack: true };
      }
      // 처리 완료 후 Set에 추가 (함수 끝에서 하면 에러 시 누락 → 시작 시점에 등록)
      await redis.sadd(_idemKey, _idemId).catch(() => {});
      await redis.expire(_idemKey, 86400).catch(() => {}); // 24시간 TTL
    }
  } catch (_idemErr) {
    log("WARN", "[SPOF3A] IdempotencyGate error (fail-open)", { error: String(_idemErr?.message || _idemErr) });
  }

  // [PR-FINAL2] Per-symbol halt guard: chronos:halt:symbols + chronos:halt:probe
  try {
    const sym = String(intent?.symbol || "").toUpperCase();
    if (sym) {
      const halted = await redis.sismember("chronos:halt:symbols", sym);
      const haltedProbe = await redis.sismember("chronos:halt:probe", sym).catch(()=>0);
      if (String(halted) === "1") {
        await routeToDLQ(msgId, intent, "CHRONOS_SYMBOL_HALT");
        return { status: "halted_symbol", ack: true };
      }
      if (String(haltedProbe) === "1") {
        await routeToDLQ(msgId, intent, "KIS_POS_DRIFT_HALT");
        return { status: "halted_probe_drift", ack: true };
      }
    }
  } catch {}
  // ═══════ [HARDENING v2.1] Recovery Mode Gate: BUY 차단 ═══════
  try {
    const side = String(intent?.side || "").toUpperCase();
    if (side === "BUY" && await isRecoveryMode()) {
      log("WARN", "[RECOVERY_MODE] BUY blocked in recovery mode", {
        intent_id: intent.intent_id, symbol: intent.symbol, side
      });
      await routeToDLQ(msgId, intent, "RECOVERY_MODE_BUY_BLOCKED");
      await redis.hincrby("metrics:drop_reasons", "RECOVERY_MODE_BUY_BLOCKED", 1).catch(() => {});
      return { status: "recovery_buy_blocked", ack: true };
    }
  } catch (e) {
    log("WARN", "[RECOVERY_MODE] check error, fail-open", { error: String(e?.message || e) });
  }

  // ═══════ [HARDENING v2.1] Half Speed Gate: qty × 0.5 ═══════
  try {
    if (await isHalfSpeed()) {
      const origShares = Math.abs(Number(intent.delta_shares || 0));
      const scaled = Math.max(1, Math.floor(origShares * 0.5));
      if (scaled !== origShares) {
        log("INFO", "[HALF_SPEED] qty scaled 0.5x", {
          intent_id: intent.intent_id, symbol: intent.symbol,
          side: intent.side, origShares, scaledShares: scaled
        });
        intent.delta_shares = scaled;
      }
    }
  } catch (e) {
    log("WARN", "[HALF_SPEED] check error, fail-open", { error: String(e?.message || e) });
  }

  // ═══════ [L8++] Order Flow Governor Gate ═══════
  // OFG provides 3-tier rate limiting, circuit breaker, and anomaly detection
  // Gate states: OPEN (allow), THROTTLE (delay), HALT (block except CRITICAL)
  try {
    const _ofgVerdict = await checkOFGGate(redis, intent);
    await recordOFGMetric(redis, _ofgVerdict);
    
    if (!_ofgVerdict.allowed) {
      log("WARN", "[L8++] OFG_GATE_BLOCKED", {
        intent_id: intent.intent_id,
        symbol: intent.symbol,
        side: intent.side,
        ofg_state: _ofgVerdict.state,
        ofg_reason: _ofgVerdict.reason,
      });
      await redis.hincrby("metrics:drop_reasons", "OFG_BLOCKED", 1).catch(() => {});
      await emitDecision({ intent, action: "SKIP", drop_reason: "OFG_BLOCKED",
        drop_detail: { state: _ofgVerdict.state, reason: _ofgVerdict.reason } });
      return { status: "ofg_blocked", ack: true };
    }
    
    // Apply throttle delay if needed
    if (_ofgVerdict.delay_ms > 0) {
      log("INFO", "[L8++] OFG_THROTTLE_DELAY", {
        intent_id: intent.intent_id,
        delay_ms: _ofgVerdict.delay_ms,
        ofg_state: _ofgVerdict.state,
      });
      await new Promise(r => setTimeout(r, _ofgVerdict.delay_ms));
    }
  } catch (_ofgErr) {
    // Fail-open: OFG errors should not block trading
    log("WARN", "[L8++] OFG_CHECK_ERROR (fail-open)", {
      error: String(_ofgErr?.message || _ofgErr).slice(0, 200),
    });
  }
  // ═══════ [END L8++ OFG Gate] ═══════

  if (!isUsMarketSessionNY()) {
    // [PATCH-2] ACK하지 않고 스킵 사유만 리턴 — 루프가 ACK 단일 책임
    await redis.xadd("emarkos:v1:ops_event", "*",
      "schema", "emarkos.ops_event.v1",
      "ts", new Date().toISOString(),
      "kind", "BLOCK",
      "reason", "OUTSIDE_SESSION_ACK_SKIP",
      "intent_id", String(intent?.intent_id ?? ""),
      "symbol", String(intent?.symbol ?? ""),
      "side", String(intent?.side ?? "")
    );
    await emitDecision({ intent, action: "SKIP", drop_reason: "OUTSIDE_SESSION_ACK_SKIP" });  // GPT-PATCH-2
    return { status: "skipped_outside_session", ack: true };
  }



  // === MVP DEDUPE (STREAM MULTI-PRODUCER) ===
  // === RECENT ORDER DEDUPE (prevent rapid re-ordering of instantly-filled symbols) ===
  try {
    const recentKey = `order:recent:${intent.symbol}:${intent.side}`;
    const recentOdno = await redis.get(recentKey);
    if (recentOdno) {
      log("WARN", "[RECENT_ORDER_DEDUPE] Skipping: recent order exists (instant-fill protection)", {
        symbol: intent.symbol, side: intent.side, recent_odno: recentOdno, intent_id: intent.intent_id
      });
      await emitDecision({ intent, action: "SKIP", drop_reason: "RECENT_ORDER_EXISTS" });
      return { ok: false, reason: "RECENT_ORDER_EXISTS" };
    }
  } catch (rdErr) {
    log("WARN", "[RECENT_ORDER_DEDUPE] Error (non-fatal)", { err: String(rdErr) });
  }
  // === END RECENT ORDER DEDUPE ===
  // === OPEN ORDERS DEDUPE (prevent duplicate BUY/SELL for same symbol) ===
  // PATCH-15: Exposure Gap — replace logic for larger notional orders
  try {
    const STALE_ORDER_AGE_SEC = 300; // 5분 이상 미체결 → stale
    const existing = await loadOpenOrderForSymbol(redis, intent.symbol, intent.side);
    if (existing) {
      const profile = await getUrgencyProfile(redis);
      if (shouldReplaceOpenOrder(existing, intent, profile)) {
        log("WARN", "[OPEN_ORDER_REPLACE] existing open order smaller than new target", {
          symbol: intent.symbol,
          side: intent.side,
          existing_odno: existing.brokerOrderId,
          intent_id: intent.intent_id,
          urgency: profile.mode,
        });
        const replace = await requestCancelReplace(existing, intent, profile);
        if (!replace.ok) {
          await emitDecision({
            intent,
            action: "SKIP",
            drop_reason: replace.reason || "REPLACE_FAILED",
            drop_detail: replace,
          });
          return { ok: false, reason: replace.reason || "REPLACE_FAILED" };
        }
        // Cancel succeeded — allow new order to proceed after brief delay
        await new Promise(r => setTimeout(r, 300));
      } else {
          // === STALE ORDER AUTO-CANCEL ===
          let orderAgeSec = 0;
          try {
            const ordTime = existing.orderedAt || "";  // "HHMMSS" format (ET)
            if (ordTime.length >= 6) {
              const hh = safeInt(ordTime.substring(0, 2), 10);
              const mm = safeInt(ordTime.substring(2, 4), 10);
              const ss = safeInt(ordTime.substring(4, 6), 10);
              const now = new Date();
              // Convert current UTC to ET (UTC-4 EDT / UTC-5 EST)
              const etOffset = -4; // EDT
              const etNow = new Date(now.getTime() + etOffset * 3600000);
              const ordSec = hh * 3600 + mm * 60 + ss;
              const nowSec = etNow.getUTCHours() * 3600 + etNow.getUTCMinutes() * 60 + etNow.getUTCSeconds();
              orderAgeSec = Math.max(0, nowSec - ordSec);
            }
          } catch (_ageErr) { /* non-critical */ }

          if (orderAgeSec >= STALE_ORDER_AGE_SEC) {
            // Stale order detected → auto-cancel via KIS API, then allow new order
            log('WARN', '[OPEN_ORDERS_DEDUPE] Stale order detected, auto-cancelling', {
              symbol: intent.symbol, side: intent.side,
              existing_odno: existing.brokerOrderId, existing_qty: existing.remainingQty,
              order_age_sec: orderAgeSec, threshold_sec: STALE_ORDER_AGE_SEC,
              intent_id: intent.intent_id
            });
            try {
              const cancelToken = await getAccessToken();
              const cancelExch = getExchange(intent.symbol);
              const cancelBody = {
                CANO: CFG.kis.accountNo,
                ACNT_PRDT_CD: CFG.kis.accountProdCd,
                OVRS_EXCG_CD: cancelExch,
                PDNO: intent.symbol,
                ORGN_ODNO: existing.brokerOrderId,
                RVSE_CNCL_DVSN_CD: "02",
                ORD_QTY: "0",
                OVRS_ORD_UNPR: "0",
              };
              const cancelOptions = {
                hostname: CFG.kis.baseUrl,
                port: CFG.kis.port,
                path: "/uapi/overseas-stock/v1/trading/order-rvsecncl",
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "authorization": `Bearer ${cancelToken}`,
                  "appkey": CFG.kis.appKey,
                  "appsecret": CFG.kis.appSecret,
                  "tr_id": "TTTT1004U",
                  "custtype": "P"
                }
              };
              const cancelResult = await httpsRequestJson(cancelOptions, cancelBody, 10000);
              log('INFO', '[OPEN_ORDERS_DEDUPE] Stale order cancel result', {
                symbol: intent.symbol, odno: existing.brokerOrderId,
                rt_cd: cancelResult?.rt_cd, msg: cancelResult?.msg1 || ""
              });
              // Remove from Redis open_orders cache immediately
              try {
                const rawCache = await redis.get('emarkos:v1:open_orders');
                if (rawCache) {
                  const cacheObj = JSON.parse(rawCache);
                  if (cacheObj.orders && Array.isArray(cacheObj.orders)) {
                    cacheObj.orders = cacheObj.orders.filter(o => o.brokerOrderId !== existing.brokerOrderId);
                    cacheObj.count = cacheObj.orders.length;
                    await redis.set('emarkos:v1:open_orders', JSON.stringify(cacheObj), 'EX', 120);
                  }
                }
              } catch (_cacheErr) { /* non-critical */ }
              // Allow new order to proceed (don't return)
              await new Promise(r => setTimeout(r, 300)); // brief delay for KIS processing
            } catch (cancelErr) {
              log('WARN', '[OPEN_ORDERS_DEDUPE] Stale order cancel failed, skipping intent', {
                symbol: intent.symbol, odno: existing.brokerOrderId, err: String(cancelErr)
              });
              await emitDecision({ intent, action: 'SKIP', drop_reason: 'STALE_CANCEL_FAILED',
                drop_detail: { existing_odno: existing.brokerOrderId, age_sec: orderAgeSec } });
              return { ok: false, reason: 'STALE_CANCEL_FAILED' };
            }
          } else {
            // Order is fresh → normal DEDUPE skip
            log('WARN', '[OPEN_ORDERS_DEDUPE] Skipping: open order already exists (fresh)', {
              symbol: intent.symbol, side: intent.side,
              existing_odno: existing.brokerOrderId, existing_qty: existing.remainingQty,
              order_age_sec: orderAgeSec, intent_id: intent.intent_id
            });
            await emitDecision({ intent, action: 'SKIP', drop_reason: 'OPEN_ORDER_EXISTS', 
              drop_detail: { existing_odno: existing.brokerOrderId, age_sec: orderAgeSec } });
            return { ok: false, reason: 'OPEN_ORDER_EXISTS' };
          }
      }
    }
  } catch (oodErr) {
    log('ERROR', '[OPEN_ORDERS_DEDUPE] Error — FAIL_CLOSED before broker order', { err: String(oodErr) });
    await emitDecision({
      intent, action: 'SKIP',
      drop_reason: 'OPEN_ORDERS_DEDUPE_ERROR_FAIL_CLOSED',
      drop_detail: { error: String(oodErr?.message || oodErr).slice(0, 300) }
    });
    return { ok: false, reason: 'OPEN_ORDERS_DEDUPE_ERROR_FAIL_CLOSED' };
  }
  // === END OPEN ORDERS DEDUPE ===
  // 목적: signal-orchestrator + phased-transition-engine 이중 생산자가
  //       동일 심볼/동일 방향 intent를 동시에 발행할 때 executor에서 차단
  // 키: order:dedupe:<SYMBOL>:<SIDE>:<60s_bucket>  TTL: 120s (CRITICAL: 15s)
  try {
    const _sym = String(intent?.symbol || "").toUpperCase();
    const _side = String(intent?.side || "").toUpperCase();
    if (_sym && (_side === "BUY" || _side === "SELL")) {
      const bucket = Math.floor(Date.now() / 60000);
      const ttlSec = isCriticalPriority(intent) ? 15 : 120;
      const dk = `order:dedupe:${_sym}:${_side}:${bucket}`;
      const dClaim = await redis.set(dk, intent.intent_id || "1", "NX", "EX", ttlSec);
      if (!dClaim) {
        log("WARN", "[DEDUPE] Duplicate symbol-side intent blocked", {
          symbol: _sym, side: _side, bucket, ttlSec,
          intent_id: intent.intent_id, dedupe_key: dk
        });
        await emitDecision({
          intent, action: "SKIP",
          drop_reason: "DUPLICATE_SYMBOL_SIDE_WINDOW",
          drop_detail: { dedupe_key: dk, ttlSec }
        });
        return { ok: false, reason: "DUPLICATE_SYMBOL_SIDE_WINDOW", dedupe_key: dk };
      }
    }
  } catch (dedupeErr) {
    log("ERROR", "[DEDUPE] Error in dedupe check — FAIL_CLOSED before broker order", { err: String(dedupeErr) });
    await emitDecision({
      intent, action: "SKIP",
      drop_reason: "DEDUPE_ERROR_FAIL_CLOSED",
      drop_detail: { error: String(dedupeErr?.message || dedupeErr).slice(0, 300) }
    });
    return { ok: false, reason: "DEDUPE_ERROR_FAIL_CLOSED" };
  }
  // === END MVP DEDUPE ===

  // === IDEMPOTENCY KEY CHECK (4AI HIGH: 브로커 멱등성) ===
  const idempotencyKey = `order:idem:${intent.intent_id}`;

  // [PATCH-1] 선점(NX)으로 '주문 전' 중복을 차단 (크래시/재시작 재처리에도 안전)
  const claimed = await redis.set(idempotencyKey, "PENDING", "NX", "EX", 900);
  if (!claimed) {
    const existing = await redis.get(idempotencyKey);
    log("WARN", "[IDEMPOTENCY] Duplicate order blocked (pre-claim)", {
      intent_id: intent.intent_id,
      existing_order: existing
    });
    await emitDecision({ intent, action: "SKIP", drop_reason: "DUPLICATE_ORDER_BLOCKED", drop_detail: { existing_order: existing } });  // GPT-PATCH-2
    return { ok: false, reason: "DUPLICATE_ORDER_BLOCKED", existing_order: existing };
  }
  // === END IDEMPOTENCY CHECK ===

  // === [V237-Phase4D] IntentStateMachine: PENDING 등록 ===
  // 멱등성 키(order:idem:*) 선점 이후, ISM에도 등록하여 상태 추적 시작
  // fail-open: ISM 등록 실패시도 주문 실행 계속
  try {
    const ismClaimed = await IntentStateMachine.claimPending(intent.intent_id);
    if (!ismClaimed) {
      const ismState = await IntentStateMachine.getState(intent.intent_id);
      log("WARN", "[ISM] Intent already in state machine, possible duplicate", {
        intent_id: intent.intent_id, symbol: intent.symbol, ism_state: ismState?.state
      });
      // 이미 SUBMITTED/ACKED라면 중복 실행 차단
      if (ismState?.state === "SUBMITTED" || ismState?.state === "ACKED") {
        await emitDecision({ intent, action: "SKIP", drop_reason: "ISM_DUPLICATE_BLOCKED",
          drop_detail: { ism_state: ismState?.state, broker_order_id: ismState?.broker_order_id } });
        await redis.del(idempotencyKey).catch(() => {});
        return { ok: false, reason: "ISM_DUPLICATE_BLOCKED", ism_state: ismState?.state };
      }
    }
  } catch (ismErr) {
    log("ERROR", "[ISM] claimPending error — FAIL_CLOSED before broker order", { error: String(ismErr?.message || ismErr) });
    await redis.del(idempotencyKey).catch(() => {});
    await emitDecision({
      intent, action: "SKIP",
      drop_reason: "ISM_CLAIM_ERROR_FAIL_CLOSED",
      drop_detail: { error: String(ismErr?.message || ismErr).slice(0, 300) }
    });
    return { ok: false, reason: "ISM_CLAIM_ERROR_FAIL_CLOSED" };
  }
  // === END IntentStateMachine PENDING ===

  // === PREFLIGHT VALIDATION (PATCH-13) ===
  const preflight = await validateIntent({
    symbol: intent.symbol,
    side: intent.side,
    notional: Math.abs(Number(intent.delta_shares || 0) * Number(intent.last_price || 0))
  }).catch(e => {
    log("ERROR", "PREFLIGHT_ERROR_FAIL_CLOSED", { error: e.message, symbol: intent.symbol });
    return { ok: false, code: "PREFLIGHT_VALIDATOR_ERROR", reason: String(e?.message || e) };
  });
  
  if (!preflight.ok) {
    log("WARN", "PREFLIGHT_REJECTED", {
      intent_id: intent.intent_id,
      symbol: intent.symbol,
      code: preflight.code,
      reason: preflight.reason
    });
    
    // [BUG-FIX-1] PREFLIGHT 거절은 정책 거절이므로 멱등성 키 즉시 삭제 (timeout 유지 불필요)
    await redis.del(idempotencyKey).catch(() => {});
    await redis.xadd("emarkos:v1:execution_report", "*",
      "kind", "REJECTED",
      "intent_id", String(intent.intent_id || ""),
      "symbol", String(intent.symbol || ""),
      "side", String(intent.side || ""),
      "reason", "PREFLIGHT:" + String(preflight.code || "UNKNOWN") + ":" + String(preflight.reason || "")
    );
    await emitDecision({ intent, action: "SKIP", drop_reason: "PREFLIGHT_REJECTED", drop_detail: { code: preflight.code, reason: preflight.reason } });  // GPT-PATCH-2
    return { ok: false, reason: "PREFLIGHT_REJECTED: " + preflight.reason };
  }
  // === END PREFLIGHT VALIDATION ===


  log("INFO", "Processing order intent", { 
    intent_id: intent.intent_id, 
    symbol: intent.symbol, 
    side: intent.side, 
    shares: intent.delta_shares 
  });
  
  // [PATCH-6] withOrderLock으로 동시 실행 경합 방지
  return await withOrderLock(intent.symbol || intent.intent_id, async () => {
    let cashReserved = false;
    let estCost = 0;
    let result = null;   // HOTFIX: catch 블록에서도 참조 가능하도록 try 바깥으로 승격
    try {
      // === [MASTER-PATCH-3A + Cash Ledger Split v2.0] PRE-ORDER ATOMIC CASH CHECK ===
      if (intent.side === "BUY") {
        // v2.0: usable_usd 우선, fallback available_cash
        const usableRaw = await redis.get("kis:cash:usable_usd");
        const availCash = Number(usableRaw || await redis.get("kis:live:available_cash") || 0);
        estCost = Number(intent.delta_shares || 0) * Number(intent.last_price || 0);
        if (estCost > 0 && availCash > 0 && estCost > availCash * 1.05) {
          log("WARN", "Order cost exceeds available cash", { 
            symbol: intent.symbol, estCost: estCost.toFixed(2), availCash: availCash.toFixed(2) 
          });
          const maxShares = Math.floor(availCash * 0.95 / Number(intent.last_price || 1));
          if (maxShares >= 1) {
            log("INFO", "Reducing order shares to fit cash", { 
              original: intent.delta_shares, reduced: maxShares 
            });
            intent.delta_shares = maxShares;
          } else {
            log("WARN", "Skipping BUY: insufficient cash for even 1 share", { symbol: intent.symbol });
            await redis.del(idempotencyKey).catch(() => {});
            await emitDecision({ intent, action: "SKIP", drop_reason: "INSUFFICIENT_CASH" });
            // CB-1 FIX: executor에서 streak INCR 안 함 — reconciler가 single source of truth
            // per-symbol backoff만 설정 (streak/mode 전환은 reconciler에서)
            await redis.set(`order:cash_backoff:${intent.symbol}`, JSON.stringify({ ts: Date.now(), reason: 'PREFLIGHT_NO_SHARES' }), 'EX', 300).catch(() => {});
            return { ok: false, reason: "INSUFFICIENT_CASH" };
          }
        }
        // Recalculate after possible share reduction
        estCost = Number(intent.delta_shares || 0) * Number(intent.last_price || 0);
        if (estCost > 0) {
          // v2.0: Reservation Model — usable_usd 원자 차감 + intent reservation 기록
          const reserveOk = await atomicReserveCashV2(redis, intent.intent_id, intent.symbol, estCost);
          if (!reserveOk) {
            log("WARN", "Skipping BUY: insufficient cash (Reservation Model v2)", { symbol: intent.symbol, estCost });
            await redis.del(idempotencyKey).catch(() => {});
            await emitDecision({ intent, action: "SKIP", drop_reason: "INSUFFICIENT_CASH" });
            // CB-1 FIX: per-symbol backoff만 설정 (streak/mode 전환은 reconciler에서)
            await redis.set(`order:cash_backoff:${intent.symbol}`, JSON.stringify({ ts: Date.now(), reason: 'ATOMIC_RESERVE_FAIL' }), 'EX', 300).catch(() => {});
            return { ok: false, reason: "INSUFFICIENT_CASH" };
          }
          cashReserved = true;
          log("INFO", "Cash reserved (Reservation Model v2)", { 
            symbol: intent.symbol, intentId: intent.intent_id, estCost: estCost.toFixed(2) 
          });
        }
      }
      // === END PRE-ORDER ATOMIC CASH CHECK ===
      
// [APBK0988-FIX] SELL cooldown check (blind retry 방지)
      if (String(intent.side).toUpperCase() === "SELL") {
        try {
          const cooldownKey = `oie:cooldown:${intent.symbol}:SELL`;
          const cooldown = await redis.get(cooldownKey);
          if (cooldown) {
            log("WARN", "[SELLABLE_GUARD] SELL cooldown active, skipping", {
              symbol: intent.symbol, intent_id: intent.intent_id
            });
            await emitDecision({ intent, action: "SKIP", drop_reason: "SELL_COOLDOWN_ACTIVE" });
            return { ok: false, reason: "SELL_COOLDOWN_ACTIVE" };
          }
        } catch (_) {}
      }
      // [SELL_QTY_PRECHECK] APBK0988 사전 방지: SELL 시 보유수량 확인
      if (String(intent.side).toUpperCase() === "SELL") {
        try {
          // R14 FIX: Support both HASH and STRING formats for positions
    let posRaw = null;
    // STRUCTURAL FIX: Try kis:broker:positions first (more reliable)
    try {
      const kisPosRaw = await redis.hget("kis:broker:positions", intent.symbol);
      if (kisPosRaw) {
        const kisPos = JSON.parse(kisPosRaw);
        if (Number(kisPos.sellable_qty || kisPos.qty || 0) > 0) {
          posRaw = kisPosRaw;
        }
      }
    } catch (_kisErr) {}
    if (!posRaw) {
    const posType = await redis.type("emarkos:v1:positions");
    if (posType === "hash") {
      posRaw = await redis.hget("emarkos:v1:positions", intent.symbol);
    } else {
      const wholeRaw = await redis.get("emarkos:v1:positions");
      if (wholeRaw) {
        try {
          const whole = JSON.parse(wholeRaw);
          const posObj = whole.positions?.[intent.symbol] || whole[intent.symbol];
          if (posObj) posRaw = JSON.stringify(posObj);
        } catch {}
      }
    }
    } // end of !posRaw emarkos fallback
          const pos = posRaw ? JSON.parse(posRaw) : null;
          const held = Number(pos?.current_shares || pos?.shares || pos?.qty || pos?.sellable_qty || pos?.settled_qty || pos?.economic_qty || pos?.trade_basis_qty || 0);
          const sellQty = Math.abs(Number(intent.delta_shares || 0));
          if (sellQty > held) {
            log("WARN", "SELL_QTY_PRECHECK_BLOCK", { symbol: intent.symbol, sellQty, held });
            if (typeof emitDecision === "function") await emitDecision({ intent, action: "SKIP", drop_reason: "SELL_QTY_EXCEEDS_HELD", drop_detail: { sellQty, held } });
            return { ok: false, reason: "SELL_QTY_EXCEEDS_HELD" };
          }
        } catch (e) { log("WARN", "SELL_QTY_PRECHECK_ERR", { err: e.message }); }
      }
      // [FINAL_CASH_CAP_95] — SELL은 cash를 소비하지 않으므로 BUY만 체크
      try {
        if (String(intent.side).toUpperCase() === "SELL") { /* SELL은 cash 불필요 - skip */ }
        else {
        const marginPct = Number(await redis.get("policy:cash_margin_pct") || 0.10);
        const _fcUsable = await redis.get("kis:cash:usable_usd");
        const avail = Number(_fcUsable || await redis.get("kis:live:available_cash") || 0);
        const est = Number(intent.delta_shares||0) * Number(intent.last_price||0);
        if (est > 0 && avail > 0 && est > avail * (1.0 - marginPct)) {
          log("WARN","Final cash cap block", {symbol:intent.symbol, est, avail, marginPct});
          if (typeof emitDecision === "function") await emitDecision({ intent, action: "SKIP", drop_reason: "FINAL_CASH_CAP", drop_detail: { est, avail, marginPct } });
          return { ok:false, reason:"INSUFFICIENT_CASH_FINAL_CAP" };
        }
        }
      } catch {}


      // ═══════════════════════════════════════════════════════════════════════
      // PR-PRICEGAP-v50 (PR-2): Price Gap Guard — DLQ / Scale / Limit Clamp
      // ═══════════════════════════════════════════════════════════════════════
      try {
        const _pgClamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
        const _pgSafeNum = (x, d = 0) => { const v = Number(x); return Number.isFinite(v) ? v : d; };

        const GAP_DLQ_BPS    = safeFloat(process.env.PRICE_GAP_DLQ_BPS    || "60");
        const GAP_SCALE_BPS  = safeFloat(process.env.PRICE_GAP_SCALE_BPS  || "30");
        const SCALE_FACTOR   = safeFloat(process.env.PRICE_GAP_SCALE_FACTOR || "0.5");
        const MAX_SLIP_BPS   = safeFloat(process.env.KIS_MAX_SLIP_BPS     || "50");

        const am = intent?.alpha_metadata || {};
        const gap_bps = _pgSafeNum(am?.price_gap_bps_send, NaN);
        const stale   = Boolean(am?.micro_send_stale);
        const kis_last = _pgSafeNum(am?.kis_quote?.last, _pgSafeNum(intent?.last_price, 0));

        // Rule 1: Hard reject to DLQ if gap too large (fail-closed)
        if (Number.isFinite(gap_bps) && Math.abs(gap_bps) >= GAP_DLQ_BPS) {
          log("WARN", "PRICE_GAP_DLQ", {
            symbol: intent.symbol, gap_bps: gap_bps.toFixed(2),
            threshold: GAP_DLQ_BPS, stale
          });
          await routeToDLQ(msgId, intent, "PRICE_GAP_TOO_LARGE", {
            gap_bps, threshold: GAP_DLQ_BPS, stale
          });
          await redis.xadd("py:cutover:events", "MAXLEN", "~", "20000", "*",
            "action", "PRICE_GAP_TOO_LARGE",
            "ts_ms", String(Date.now()),
            "symbol", String(intent?.symbol || ""),
            "gap_bps", String(gap_bps.toFixed(2))
          ).catch(() => {});
          await redis.del(idempotencyKey).catch(() => {});
          if (cashReserved && intent.side === "BUY" && estCost > 0) {
            await releaseReservation(redis, intent.intent_id, estCost);
          }
          if (typeof emitDecision === "function") {
            await emitDecision({ intent, action: "SKIP", drop_reason: "PRICE_GAP_TOO_LARGE",
              drop_detail: { gap_bps, threshold: GAP_DLQ_BPS, stale } });
          }
          return { ok: false, reason: "PRICE_GAP_TOO_LARGE", gap_bps };
        }

        // Rule 2: Scale down qty if moderate gap or stale
        if (stale || (Number.isFinite(gap_bps) && Math.abs(gap_bps) >= GAP_SCALE_BPS)) {
          const q = _pgSafeNum(intent?.delta_shares, 0);
          if (q !== 0) {
            const scaledQty = Math.max(1, Math.round(q * _pgClamp(SCALE_FACTOR, 0.1, 1.0)));
            intent.delta_shares = scaledQty;
          }
          intent.alpha_metadata = intent.alpha_metadata || {};
          intent.alpha_metadata.price_gap_scaled = true;
          intent.alpha_metadata.price_gap_bps_applied = Number.isFinite(gap_bps) ? gap_bps : null;
          intent.alpha_metadata.price_gap_scale_factor = SCALE_FACTOR;
          log("INFO", "PRICE_GAP_SCALED", {
            symbol: intent.symbol, gap_bps: Number.isFinite(gap_bps) ? gap_bps.toFixed(2) : "N/A",
            stale, original_qty: q, scaled_qty: intent.delta_shares
          });
          await redis.xadd("py:cutover:events", "MAXLEN", "~", "20000", "*",
            "action", "PRICE_GAP_SCALED",
            "ts_ms", String(Date.now()),
            "symbol", String(intent?.symbol || ""),
            "gap_bps", String(Number.isFinite(gap_bps) ? gap_bps.toFixed(2) : "NaN"),
            "stale", String(stale)
          ).catch(() => {});
        }

        // Rule 3: Clamp limit price to KIS last band
        if (kis_last > 0 && intent?.limit?.price) {
          const lp = _pgSafeNum(intent.limit.price, 0);
          const band = MAX_SLIP_BPS / 10000.0;
          const side = String(intent?.side || "").toUpperCase();
          if (side === "BUY") {
            const maxP = kis_last * (1 + band);
            if (lp > maxP) intent.limit.price = Math.round(maxP * 100) / 100;
          } else if (side === "SELL") {
            const minP = kis_last * (1 - band);
            if (lp < minP) intent.limit.price = Math.round(minP * 100) / 100;
          }
          intent.alpha_metadata = intent.alpha_metadata || {};
          intent.alpha_metadata.limit_clamped_to_kis = true;
          intent.alpha_metadata.kis_last_for_clamp = kis_last;
          intent.alpha_metadata.kis_max_slip_bps = MAX_SLIP_BPS;
        }
      } catch (pgErr) {
        log("WARN", "PRICE_GAP_GUARD_ERROR", { error: String(pgErr?.message || pgErr).slice(0, 200) });
      }
      // ═══ END PR-PRICEGAP-v50 (PR-2) ═══

      result = await routeAndExecute(intent);  // [EXEC_ROUTER] Routes through pegger or direct KIS based on policy
      
      if (result.rt_cd === "0" && result.output?.ODNO) {
        log("INFO", "Order executed successfully", {
          intent_id: intent.intent_id,
          symbol: intent.symbol,
          broker_order_id: result.output.ODNO
        });
        
        // [PATCH-1] 멱등성 키를 ODNO로 확정 (PENDING → ODNO)
        await redis.set(idempotencyKey, result.output.ODNO, "EX", 86400);
        // [V237-Phase4D] ISM: SUBMITTED 상태로 전이
        await IntentStateMachine.markSubmitted(intent.intent_id, result.output.ODNO).catch(() => {});
        // [PATCH-RECENT] Mark recent order to prevent rapid re-ordering of instantly-filled symbols
        await redis.set(`order:recent:${intent.symbol}:${intent.side}`, result.output.ODNO, "EX", 300).catch(() => {});
        
        // === CASH TRACKING: Already deducted atomically via Lua (MASTER-PATCH-3A) ===
        // No post-order deduction needed.
        
        // Emit execution event
        const execTs = new Date().toISOString();
        const day = execTs.slice(0,10).replace(/-/g,"");
        
        await redis.xadd(CFG.execution_stream, "*", "json", JSON.stringify({
          schema: "EXECUTION",
          kind: "FILL_PENDING",
          intent_id: intent.intent_id,
      origin_intent_id: String(intent.origin_intent_id || intent.intent_id || ""),
      ref_px: Number(intent?.limit?.price ?? intent?.last_price ?? 0) || null,
          symbol: intent.symbol,
          side: intent.side,
          shares: intent.delta_shares,
          broker_order_id: result.output.ODNO,
          status: "SUBMITTED",
          ts: execTs
        }));
        
        // 슬리피지 샘플 수집 (체결가 vs 요청가)
        const reqPrice = Number(intent.last_price || 0);
        const notional = reqPrice * Number(intent.delta_shares || 0);
        await redis.xadd("kpi:v1:slip_samples", "*",
          "ts", execTs,
          "day", day,
          "origin_intent_id", String(intent.origin_intent_id || intent.intent_id || ""),
          "symbol", String(intent.symbol || ""),
          "side", String(intent.side || ""),
          "req_price", String(reqPrice),
          "notional", String(notional),
          "ord_type", result.adj_bp !== 0 ? "AGG_LIMIT" : "LIMIT",
          "broker_order_id", result.output.ODNO
        );
        await redis.set(`kpi:v1:slip_pending:${result.output.ODNO}`, JSON.stringify({
          ts: execTs,
          day,
          origin_intent_id: String(intent.origin_intent_id || intent.intent_id || ""),
          symbol: String(intent.symbol || ""),
          side: String(intent.side || ""),
          req_price: String(reqPrice),
          notional: String(notional),
          ord_type: result.adj_bp !== 0 ? "AGG_LIMIT" : "LIMIT",
          broker_order_id: String(result.output.ODNO)
        }), "EX", 172800);
        await redis.xtrim("kpi:v1:slip_samples", "MAXLEN", "~", "10000");
        
        // Track open order for retry worker
        const exchCode = getExchange(intent.symbol);
        const limitPrice = Number(intent.limit?.price || intent.last_price || 0);
        await markOpen(redis, intent, result.output.ODNO, limitPrice, exchCode);
        // [PATCH-OPEN-LEDGER] ODNO-based open ledger for pending notional deduction
        // This is the SSOT that cash-sync should use to compute pendingBuyUsd accurately.
        await markOpenOdno(redis, intent, result.output.ODNO, limitPrice, exchCode, result.final_qty).catch((e) => {
          log("ERROR", "markOpenOdno failed", { err: String(e), odno: result?.output?.ODNO, intent_id: intent?.intent_id });
        });
        await emitDecision({ intent, action: "SENT", broker_order_id: result.output.ODNO, origin_intent_id: String(intent.origin_intent_id || intent.intent_id || "") });  // GPT-PATCH-2
        // [V237-Phase4D] ISM: ACKED 상태로 전이 (실행 완료)
        await IntentStateMachine.markAcked(intent.intent_id).catch(() => {});
        // v2.0: intent reserve → ODNO reserve 승격
        if (cashReserved && intent.side === "BUY" && estCost > 0) {
          await promoteReservation(redis, intent.intent_id, result.output.ODNO, intent.symbol, estCost);
        }
        return { ok: true, broker_order_id: result.output.ODNO };
      } else {
        // [MASTER-PATCH-3B] KIS 치명적 에러 시 Auto-Blocklist (TTL 4시간) + 현금 롤백
        const fatalCodes = ["APBK0066", "APBK0656"];
        if (fatalCodes.includes(result.msg_cd)) {
          log("CRITICAL", "AUTO_BLOCKLISTED_SYMBOL", { symbol: intent.symbol, msg: result.msg1, code: result.msg_cd });
          // Runtime blocklist v2: the set is an index only; per-symbol TTL key is the authority.
          // This prevents permanent stale SYMBOL_BLOCKED after the intended 4h window expires.
          const autoBlockTtlSec = Number(process.env.AUTO_BLOCKLIST_TTL_SEC || 14400);
          await redis.sadd("policy:blocklist:symbols", intent.symbol);
          await redis.set(`blocklist:auto:${intent.symbol}`, JSON.stringify({
            code: result.msg_cd,
            msg: String(result.msg1 || ""),
            ts_ms: Date.now(),
            source: "order-intent-executor",
            ttl_sec: autoBlockTtlSec
          }), "EX", autoBlockTtlSec).catch(() => {});
          await redis.expire("policy:blocklist:symbols", Math.max(autoBlockTtlSec, 60)).catch(() => {});
          await emitAlert("CRITICAL", "AUTO_BLOCKLIST", `${intent.symbol} auto-blocklisted: ${result.msg_cd} ${result.msg1}`).catch(() => {});
        } else {
          await redis.del(idempotencyKey).catch(() => {});
        }
        // v2.0: 거절 시 reservation 해제 + Cash Gate 피드백
        if (cashReserved && intent.side === "BUY" && estCost > 0) {
          await releaseReservation(redis, intent.intent_id, estCost);
          log("INFO", "Cash reservation released after rejection", { symbol: intent.symbol, estCost: estCost.toFixed(2) });
          // CB-1 FIX: executor에서 cashGateFeedback 제거 — reconciler가 single source of truth
          // (double streak-counting 방지: executor+reconciler 양쪽에서 INCR하면 2회 reject만으로 SELL_ONLY 진입)
        }
        
        // [BUG-FIX-2] fatalCodes 중복 선언 제거 (L2036에서 이미 처리됨)

        log("WARN", "Order rejected", {
          intent_id: intent.intent_id,
          symbol: intent.symbol,
          msg: result.msg1,
          code: result.msg_cd
        });
        
        // [APBK0988-FIX] SELL 가능수량 초과 시 cooldown + 재동기화 트리거
        if (isApbk0988(result.msg1) || result.msg_cd === 'APBK0988') {
          try {
            log("ERROR", "[SELLABLE_GUARD] KIS rejected SELL due to available quantity", {
              symbol: intent.symbol,
              side: intent.side,
              intent_id: intent.intent_id,
              msg: result.msg1,
              code: result.msg_cd,
            });
            await redis.set(
              `oie:apbk0988:${intent.intent_id}`,
              JSON.stringify({
                symbol: intent.symbol,
                side: intent.side,
                requested_qty: result.final_qty || 0,
                msg: result.msg1,
                ts: new Date().toISOString(),
              }),
              "EX", 86400
            );
            // [RESYNC-v3] 즉시 재동기화 요청 (open-orders-sync + position-sync-service)
            await requestSellableResync(intent.symbol, intent, {
              phase: "response",
              broker_reject: "APBK0988",
              requested_qty: result.final_qty || 0,
            });
            // blind retry 금지: 300초 cooldown
            await redis.set(`oie:cooldown:${intent.symbol}:SELL`, "1", "EX", 300);
          } catch (apbkErr) {
            log("WARN", "[SELLABLE_GUARD] APBK0988 handler error", { error: apbkErr.message });
          }
        }
                // [4AI-PATCH-3] APBK0952: margin dynamic elevation (+5%p, max 0.20, TTL 1h)
        if (result.msg_cd === 'APBK0952') {
          try {
            const curMargin = Number(await redis.get('policy:cash_margin_pct') || 0.10);
            const newMargin = Math.min(curMargin + 0.05, 0.20);
            await redis.set('policy:cash_margin_pct', newMargin.toFixed(2), 'EX', 3600);
            log('CRITICAL', 'MARGIN_ELEVATED_APBK0952', {
              symbol: intent.symbol, prevMargin: curMargin, newMargin,
              ttlSec: 3600, msg: result.msg1
            });
            await redis.hincrby('metrics:margin_bumps', new Date().toISOString().slice(0,10), 1).catch(() => {});
          } catch (marginErr) {
            log('WARN', 'MARGIN_BUMP_FAILED', { error: marginErr.message });
          }
        }

        await clearOpen(redis, intent.intent_id);
        await emitDecision({ intent, action: "SKIP", drop_reason: "ORDER_REJECTED", drop_detail: { msg: result.msg1, msg_cd: result.msg_cd } });  // GPT-PATCH-2
        // [V237-Phase4D] ISM: FAILED 상태로 전이
        await IntentStateMachine.markFailed(intent.intent_id, `ORDER_REJECTED:${result.msg_cd}`).catch(() => {});
        return { ok: false, reason: result.msg1 || "Unknown error" };
      }
    } catch (e) {
      // [MASTER-PATCH-3C] Timeout 시 멱등성 키 5분 유지 (Double-Spend 방지)
      const errMsg = String(e?.message ?? e);
      const isTimeout = errMsg.includes("TIMEOUT") || errMsg.includes("timeout") || 
                        errMsg.includes("ECONNRESET") || errMsg.includes("socket hang up");

      // [PR-B] 주문 성공(ODNO 발급) 이후 후처리 에러인지 판별
      const hasOdno = !!(result && result.rt_cd === "0" && result?.output?.ODNO);

      if (hasOdno) {
        // ✅ 주문은 이미 나감 — rollback/clearOpen 절대 금지
        log("ERROR", "POST_ORDER_ERROR_AFTER_ODNO", {
          intent_id: intent.intent_id,
          symbol: intent.symbol,
          odno: result.output.ODNO,
          error: errMsg.slice(0, 200),
          msg: "Order was placed successfully but post-processing failed. NOT rolling back."
        });
        await markPostError(redis, intent, result.output.ODNO, errMsg).catch(() => {});
        // 주문은 OPEN 유지, reconciler가 후속 처리
        return { ok: true, broker_order_id: result.output.ODNO, post_error: errMsg.slice(0, 200) };
      }

      if (isTimeout) {
        log("CRITICAL", "API_TIMEOUT_IDEMPOTENCY_HELD", { 
          symbol: intent.symbol, 
          msg: "Keeping idempotency lock 5min to prevent double-spend" 
        });
        await redis.expire(idempotencyKey, 300).catch(() => {});
        // [OIE-RACE-FIX] Post-timeout confirmation polling
        const raceResult = await postTimeoutConfirmation(redis, intent);
        if (raceResult.status === "RACE_CONFIRMED") {
          log("CRITICAL", "RACE_CONFIRMED_AFTER_TIMEOUT", {
            intent_id: intent.intent_id, symbol: intent.symbol,
            fill: raceResult.fill
          });
          // Order was actually filled - do NOT retry, do NOT rollback
          return { ok: true, race_confirmed: true, fill: raceResult.fill };
        }
        // Timeout 시 현금 롤백 안 함 — KIS에 주문이 접수됐을 수 있음
      } else {
        await redis.del(idempotencyKey).catch(() => {});
        // v2.0: 타임아웃이 아닌 에러: reservation 해제
        if (cashReserved && intent.side === "BUY" && estCost > 0) {
          await releaseReservation(redis, intent.intent_id, estCost);
          log("INFO", "Cash reservation released after error", { symbol: intent.symbol, estCost: estCost.toFixed(2) });
        }
      }
      log("ERROR", "Order execution failed", {
        intent_id: intent.intent_id,
        symbol: intent.symbol,
        error: errMsg.slice(0, 200),
        isTimeout
      });
      await clearOpen(redis, intent.intent_id);
      await emitDecision({ intent, action: "SKIP", drop_reason: isTimeout ? "API_TIMEOUT" : (errMsg.includes("MISSING_PRICE") ? "MISSING_PRICE" : "ORDER_EXECUTION_FAILED"), drop_detail: { error: errMsg.slice(0, 200) } });  // GPT-PATCH-2
      // [V237-Phase4D] ISM: FAILED 상태로 전이
      await IntentStateMachine.markFailed(intent.intent_id, isTimeout ? "API_TIMEOUT" : errMsg.slice(0, 100)).catch(() => {});
      return { ok: false, reason: errMsg };
    }
  });
}

// Main loop

// ============================================================
// [P1-FIX] Idempotent consumer group bootstrap
// Ensures stream + consumer group exist. Safe to call repeatedly.
// Emits structured AOPS_EVT events for operational observability.
// ============================================================
async function ensureConsumerGroup(redisClient, stream, group) {
  const PROC = "order-intent-executor";
  emitEvent({
    proc: PROC, event: "consumer_group_bootstrap_start", sev: "INFO", corr: {},
    msg: `Bootstrapping consumer group: ${group} on stream: ${stream}`,
    ctx: { stream, group }
  });
  try {
    await redisClient.xgroup("CREATE", stream, group, "0", "MKSTREAM");
    log("INFO", "[P1-FIX] Consumer group created (new)", { stream, group });
    emitEvent({
      proc: PROC, event: "consumer_group_bootstrap_ok", sev: "INFO", corr: {},
      msg: `Consumer group created: ${group}`,
      ctx: { stream, group, created: true }
    });
  } catch (e) {
    if (String(e.message).includes("BUSYGROUP")) {
      log("INFO", "[P1-FIX] Consumer group already exists (BUSYGROUP)", { stream, group });
      emitEvent({
        proc: PROC, event: "consumer_group_bootstrap_busygroup", sev: "INFO", corr: {},
        msg: `Consumer group already exists: ${group}`,
        ctx: { stream, group }
      });
    } else {
      log("ERROR", "[P1-FIX] Consumer group bootstrap failed", { stream, group, error: e.message });
      emitEvent({
        proc: PROC, event: "consumer_group_bootstrap_fail", sev: "ERROR", corr: {},
        msg: `Consumer group bootstrap failed: ${e.message}`,
        ctx: { stream, group, error: e.message }
      });
      throw e; // re-throw to let caller handle
    }
  }

  // Verify group exists
  try {
    const groups = await redisClient.xinfo("GROUPS", stream);
    const found = groups && groups.some(g => {
      if (Array.isArray(g)) {
        const nameIdx = g.indexOf("name");
        return nameIdx >= 0 && g[nameIdx + 1] === group;
      }
      return g?.name === group;
    });
    if (found) {
      log("INFO", "[P1-FIX] Consumer group verified", { stream, group });
    } else {
      log("WARN", "[P1-FIX] Consumer group NOT found after creation", { stream, group });
    }
  } catch (verifyErr) {
    log("WARN", "[P1-FIX] Could not verify consumer group", { stream, group, error: verifyErr.message });
  }
}


function normalizeRedisFieldValue(value) {
  if (Buffer.isBuffer(value)) return value.toString("utf8");
  return value;
}

function parseStreamIntent(fields, msgId) {
  if (!fields || !Array.isArray(fields)) return null;
  const arr = fields.map(normalizeRedisFieldValue);
  const obj = {};
  for (let i = 0; i < arr.length; i += 2) {
    obj[String(arr[i])] = normalizeRedisFieldValue(arr[i + 1]);
  }

  let parsed = null;
  for (const candidate of [obj.json, obj.payload]) {
    if (typeof candidate === "string" && candidate.trim()) {
      try {
        parsed = JSON.parse(candidate);
        break;
      } catch {}
    }
  }
  if (!parsed && Object.keys(obj).length > 0) parsed = { ...obj };
  if (!parsed || typeof parsed !== "object") return null;

  for (const jf of ["alpha_metadata", "regime_snapshot", "risk_context", "meta"]) {
    if (typeof parsed[jf] === "string") {
      try { parsed[jf] = JSON.parse(parsed[jf]); } catch {}
    }
  }
  for (const nf of [
    "delta_shares", "last_price", "notional_usd", "target_value", "current_value",
    "current_shares", "target_shares", "champion_scale", "crash_scale", "edge_score",
    "turnover_scale", "pi_scale", "qty", "limit_price", "price",
  ]) {
    if (parsed[nf] !== undefined && parsed[nf] !== null && parsed[nf] !== "") {
      const v = Number(parsed[nf]);
      if (Number.isFinite(v)) parsed[nf] = v;
    }
  }

  if (!parsed.intent_id && parsed.exec_id) parsed.intent_id = String(parsed.exec_id);
  if (!parsed.exec_id && parsed.intent_id) parsed.exec_id = String(parsed.intent_id);
  if (!parsed.intent_id && msgId) parsed.intent_id = String(msgId);
  if (!parsed.run_id && parsed.meta && parsed.meta.run_id) parsed.run_id = parsed.meta.run_id;
  return (parsed.intent_id || parsed.symbol) ? parsed : null;
}

async function writeConsumerHeartbeat(tag = "idle") {
  try {
    await redis.set(CONSUMER_HEARTBEAT_KEY, String(Math.floor(Date.now() / 1000)), "EX", CONSUMER_HEARTBEAT_TTL_SEC);
    await redis.set(`ares:v55:consumer:status:${CFG.consumer_name}`, JSON.stringify({
      ts: new Date().toISOString(),
      consumer: CFG.consumer_name,
      group: CFG.consumer_group,
      tag,
    }), "EX", CONSUMER_HEARTBEAT_TTL_SEC);
  } catch {}
}

async function writeExecutionAck(msgId, intent, outcome = {}) {
  try {
    const execId = String(intent?.exec_id || intent?.intent_id || msgId || "");
    if (!execId) return;
    const nowSec = Math.floor(Date.now() / 1000);
    const ack = {
      ts: new Date().toISOString(),
      msg_id: msgId,
      exec_id: execId,
      intent_id: String(intent?.intent_id || execId),
      symbol: String(intent?.symbol || ""),
      side: String(intent?.side || ""),
      consumer: CFG.consumer_name,
      status: outcome?.ok === false ? "failed" : "processed",
      broker_order_id: outcome?.broker_order_id || null,
      post_error: outcome?.post_error || null,
    };
    await redis.multi()
      .set(`${EXECUTION_ACK_PREFIX}:${execId}`, JSON.stringify(ack), "EX", EXECUTION_ACK_TTL_SEC)
      .set(CONSUMER_LAST_ACK_KEY, String(nowSec), "EX", CONSUMER_HEARTBEAT_TTL_SEC)
      .set(CONSUMER_HEARTBEAT_KEY, String(nowSec), "EX", CONSUMER_HEARTBEAT_TTL_SEC)
      .exec();
  } catch (e) {
    log("WARN", "EXECUTION_ACK_WRITE_FAILED", { error: String(e?.message || e) });
  }
}

async function claimStalePending(redisClient, stream, group, consumer) {
  try {
    const resp = await redisClient.call(
      "XAUTOCLAIM",
      stream,
      group,
      consumer,
      String(STALE_PENDING_MIN_IDLE_MS),
      "0-0",
      "COUNT",
      String(STALE_PENDING_CLAIM_COUNT),
    );
    if (!Array.isArray(resp) || resp.length < 2) return [];
    return Array.isArray(resp[1]) ? resp[1] : [];
  } catch (e) {
    const msg = String(e?.message || e || "");
    if (!msg.includes("unknown command") && !msg.includes("ERR")) {
      log("WARN", "XAUTOCLAIM_FAILED", { error: msg });
    }
    return [];
  }
}

async function handleStreamEntry(msgId, fields, phase = "new") {
  if (!fields || !Array.isArray(fields)) {
    log("WARN", "Skipping message with null/invalid fields", { msgId, phase });
    try { await redis.xack(CFG.order_intent_stream, CFG.consumer_group, msgId); } catch {}
    return { acked: true, reason: "invalid_fields" };
  }

  const intent = parseStreamIntent(fields, msgId);
  if (!intent) {
    log("WARN", "Unable to parse intent", { msgId, phase });
    await routeToDLQ(msgId, { intent_id: msgId }, "PARSE_FAILED", { phase });
    await redis.xack(CFG.order_intent_stream, CFG.consumer_group, msgId);
    return { acked: true, reason: "parse_failed" };
  }

  try {
    const fw = firewallCheck(intent);
    if (!fw.pass) {
      await quarantineAndAck(redis, CFG.consumer_group, msgId, fields, fw.reason);
      return { acked: true, reason: fw.reason };
    }

    if (String(intent.side || "").toUpperCase() === "BUY") {
      await redis.set("buy:pipeline:exec:last_ts", String(Math.floor(Date.now()/1000)), "EX", 86400*14);
    }

    if (!(await isTradingEnabled())) {
      log("WARN", `TRADING_DISABLED: ${phase} intent skipped`, { msgId, symbol: intent.symbol });
      await emitDecision({ intent, action: "SKIP", drop_reason: "TRADING_DISABLED" });
      await routeToDLQ(msgId, intent, "TRADING_DISABLED", { phase });
      await redis.xack(CFG.order_intent_stream, CFG.consumer_group, msgId);
      return { acked: true, reason: "TRADING_DISABLED" };
    }

    if (await isTradeHaltedCompat()) {
      if (isCriticalPriority(intent)) {
        log("WARN", `CRITICAL_OVERRIDE_HALT: executing despite trade halt (${phase})`, {
          msgId, symbol: intent.symbol, priority: intent.priority, reason: intent.reason,
        });
      } else {
        await emitDecision({ intent, action: "SKIP", drop_reason: "TRADE_HALTED", drop_detail: { dlq: true, stream_id: msgId, phase } });
        await routeToDLQ(msgId, intent, "TRADE_HALTED", { phase });
        await emitAlert("CRITICAL", "trade_halt_dlq", `Halted ${phase} intent DLQ'd: ${intent.symbol}`, { msgId, symbol: intent.symbol });
        await redis.xack(CFG.order_intent_stream, CFG.consumer_group, msgId);
        return { acked: true, reason: "TRADE_HALTED" };
      }
    }

    await pacedDrainWait(redis, intent.intent_id);
    const processed = await processIntent(msgId, intent);
    await writeExecutionAck(msgId, intent, processed);
    await redis.xack(CFG.order_intent_stream, CFG.consumer_group, msgId);
    log("INFO", `Processed ${phase} intent`, { msgId, symbol: intent.symbol, ok: processed?.ok !== false });
    return { acked: true, processed };
  } catch (e) {
    log("ERROR", `Failed to process ${phase} message`, { msgId, error: String(e) });
    return { acked: false, error: String(e) };
  }
}

// --- v87 fix: process pending first, then new messages ---
async function readGroupPending(redis, stream, group, consumer) {
  // Read pending messages (id=0)
  const resp = await redis.xreadgroup(
    "GROUP", group, consumer,
    "COUNT", 10,
    "STREAMS", stream, "0"
  );
  return resp;
}

async function main() {
  // [P0.5-FIX] Wait for Redis to be ready before loading exchange map
  const _mainRedis = getRedis();
  if (_mainRedis.status !== 'ready') {
    log("INFO", "[Executor v10.0] Waiting for Redis to become ready...");
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        log("WARN", "[Executor v10.0] Redis ready timeout after 30s, proceeding anyway");
        resolve();
      }, 30000);
      if (_mainRedis.status === 'ready') { clearTimeout(timeout); resolve(); return; }
      _mainRedis.once('ready', () => { clearTimeout(timeout); resolve(); });
      _mainRedis.once('error', (err) => {
        log("WARN", `[Executor v10.0] Redis error while waiting: ${err.message}`);
        // Don't reject - let retry in loadExchangeMap handle it
      });
    });
  }
  await assertProcessEnvironment("LIVE", _mainRedis, { processName: PROC, logger: (level, payload) => log(level, "[ENV_GUARD]", payload) });
  await loadExchangeMap();  // [ARES v10.0] Load exchange map from Redis (with retry)
  log("INFO", "Order Intent Executor starting (patched, AOPS_EVT instrumented)", { 
    stream: CFG.order_intent_stream,
    consumer_group: CFG.consumer_group
  });

  // === AOPS_EVT: process_start ===
  emitEvent({
    proc: PROC,
    event: "process_start",
    sev: "INFO",
    corr: {},
    msg: "order-intent-executor starting (AOPS_EVT instrumented)",
    ctx: { stream: CFG.order_intent_stream, consumer_group: CFG.consumer_group }
  });
  
  // [P1-FIX] Idempotent consumer group bootstrap with structured logging
  await ensureConsumerGroup(redis, CFG.order_intent_stream, CFG.consumer_group);
  
  // [STRUCTURAL_FIX_S2] Observability counters
  let _nogroup_count = 0;
  let _poll_cycle = 0;
  const OBS_INTERVAL = 60;  // lag/pending 체크 주기 (사이클 수)
  const LAG_ALERT_THRESHOLD = 20;  // lag이 이 이상이면 텔레그램 알림
  const PENDING_ALERT_THRESHOLD = 10;
  let _last_lag_alert_ts = 0;
  const LAG_ALERT_COOLDOWN_MS = 300000;  // 5분 쿨다운
  
  // Poll loop
  while (true) {
    _poll_cycle++;
    
    // [STRUCTURAL_FIX_S2] 주기적 lag/pending 모니터링
    if (_poll_cycle % OBS_INTERVAL === 0) {
      try {
        const _xinfoGroups = await redis.xinfo("GROUPS", CFG.order_intent_stream);
        for (const _g of _xinfoGroups) {
          const _gArr = Array.isArray(_g) ? _g : Object.entries(_g).flat();
          const _nameIdx = _gArr.indexOf("name");
          if (_nameIdx >= 0 && _gArr[_nameIdx + 1] === CFG.consumer_group) {
            const _lagIdx = _gArr.indexOf("lag");
            const _pelIdx = _gArr.indexOf("pel-count");
            const _lag = _lagIdx >= 0 ? Number(_gArr[_lagIdx + 1]) : -1;
            const _pel = _pelIdx >= 0 ? Number(_gArr[_pelIdx + 1]) : -1;
            await redis.set("oie:obs:lag", String(_lag), "EX", 120);
            await redis.set("oie:obs:pending", String(_pel), "EX", 120);
            await redis.set("oie:obs:nogroup_count", String(_nogroup_count), "EX", 120);
            await redis.set("oie:obs:poll_cycle", String(_poll_cycle), "EX", 120);
            await redis.set("oie:obs:ts", new Date().toISOString(), "EX", 120);
            if ((_lag > LAG_ALERT_THRESHOLD || _pel > PENDING_ALERT_THRESHOLD) && (Date.now() - _last_lag_alert_ts > LAG_ALERT_COOLDOWN_MS)) {
              _last_lag_alert_ts = Date.now();
              log("WARN", "[S2] OIE LAG/PENDING ALERT", { lag: _lag, pending: _pel, nogroup_count: _nogroup_count });
              // 텔레그램 알림은 별도 alerting 서비스에서 oie:obs:lag 키를 모니터링
            }
          }
        }
      } catch (_obsErr) {
        // Non-critical observability — don't break poll loop
      }
    }
    try {
      // 0) Idle heartbeat
      await writeConsumerHeartbeat("poll");

      // 1) Claim stale pending from dead consumers first.
      const claimedEntries = await claimStalePending(redis, CFG.order_intent_stream, CFG.consumer_group, CFG.consumer_name);
      if (claimedEntries.length > 0) {
        for (const [msgId, fields] of claimedEntries) {
          await handleStreamEntry(msgId, fields, "claimed");
        }
      }

      // 2) Process pending already owned by this consumer.
      for (let pendingRound = 0; pendingRound < 3; pendingRound++) {
        const pendingMsgs = await readGroupPending(redis, CFG.order_intent_stream, CFG.consumer_group, CFG.consumer_name);
        if (!pendingMsgs || pendingMsgs.length === 0) break;
        for (const [stream, entries] of pendingMsgs) {
          for (const [msgId, fields] of entries) {
            await handleStreamEntry(msgId, fields, "pending");
          }
        }
      }

      // 3) Then read new messages (id=>)
      const messages = await redis.xreadgroup(
        "GROUP", CFG.consumer_group, CFG.consumer_name,
        "COUNT", 10,
        "BLOCK", CFG.poll_interval_ms,
        "STREAMS", CFG.order_intent_stream, ">"
      );

      if (messages && messages.length > 0) {
        for (const [stream, entries] of messages) {
          for (const [msgId, fields] of entries) {
            await handleStreamEntry(msgId, fields, "new");
          }
        }
      }
    } catch (e) {
      // [P1-FIX] Auto-recover consumer group on NOGROUP error inside poll loop
      if (String(e).includes("NOGROUP")) {
        _nogroup_count++;
        log("WARN", "[P1-FIX] NOGROUP detected in poll loop, re-bootstrapping consumer group", {
          stream: CFG.order_intent_stream, group: CFG.consumer_group, nogroup_count: _nogroup_count
        });
        try { await redis.set("oie:obs:nogroup_count", String(_nogroup_count), "EX", 86400); } catch {}  // 24h TTL
        emitEvent({
          proc: PROC, event: "consumer_group_lost", sev: "WARN", corr: {},
          msg: "NOGROUP detected in poll loop, attempting re-bootstrap",
          ctx: { stream: CFG.order_intent_stream, group: CFG.consumer_group }
        });
        await ensureConsumerGroup(redis, CFG.order_intent_stream, CFG.consumer_group);
        await new Promise(r => setTimeout(r, 2000));
      } else {
        log("ERROR", "Poll error", { error: String(e) });
        await new Promise(r => setTimeout(r, 5000));
      }
    }
  }
}

main().catch(async (e) => {
  log("FATAL", "Executor crashed", { error: String(e) });
  // [P0.5-FIX] Delay before exit to prevent PM2 rapid restart loop
  // PM2 will restart us, but with a delay to let Redis recover
  const CRASH_DELAY = 10000; // 10 seconds
  log("INFO", `[P0.5-FIX] Delaying exit by ${CRASH_DELAY}ms to prevent rapid restart loop`);
  await new Promise(r => setTimeout(r, CRASH_DELAY));
  process.exit(1);
});


// Graceful shutdown: never XACK pending work; leave it claimable by survivors.
(function installGracefulShutdown() {
  const STREAM = CFG.order_intent_stream;
  const GROUP = CFG.consumer_group;
  const CONSUMER = CFG.consumer_name;

  async function gracefulExit(sig) {
    console.log(JSON.stringify({ ts: new Date().toISOString(), proc: "OIE", msg: `${sig} received, cleaning up consumer ${CONSUMER}` }));
    try {
      await redis.del(CONSUMER_HEARTBEAT_KEY).catch(() => {});
      await redis.del(`ares:v55:consumer:status:${CONSUMER}`).catch(() => {});
      await redis.xgroup("DELCONSUMER", STREAM, GROUP, CONSUMER).catch(() => {});
      console.log(JSON.stringify({ ts: new Date().toISOString(), proc: "OIE", msg: `DELCONSUMER ${CONSUMER} done` }));
      await redis.quit();
    } catch (e) {
      console.error(JSON.stringify({ ts: new Date().toISOString(), proc: "OIE", msg: "graceful shutdown error", err: String(e?.message || e) }));
    }
    process.exit(0);
  }

  process.on("SIGTERM", () => gracefulExit("SIGTERM"));
  process.on("SIGINT", () => gracefulExit("SIGINT"));
})();
