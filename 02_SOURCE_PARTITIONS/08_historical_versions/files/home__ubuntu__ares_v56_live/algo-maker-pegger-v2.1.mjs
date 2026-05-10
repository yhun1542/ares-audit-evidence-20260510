/**
 * algo-maker-pegger.mjs (production v2.1 — 4AI-reviewed, hardened)
 *
 * Features:
 *  - State machine: INIT → POSTED_MAKER → CHECK_FILL → REQUOTE → ESCAPE_TAKER → ESCAPE_POSTED → FINALIZING → DONE / ABORTED
 *  - fills / executions stream subscription (consumer group) — SSOT for fill detection
 *  - REST fallback minimization: global token bucket + per-order cooldown + stale gate
 *  - SELL conservative profile: lower spread cap, fewer requotes, faster escape
 *  - SELL escape bid cap: never dump below bid (policy-controlled)
 *  - Prefer request quote: use measured_bid/ask from PegRequest when fresh enough
 *  - KIS order/cancel/inquire endpoints configurable via env
 *  - Keep-Alive HTTP agent
 *  - [v2.0] Heartbeat: writes health:algo-maker-pegger (rate-limited 1s, pipelined)
 *  - [v2.0] Telemetry: emits execution quality metrics to stream:execution_quality on peg completion
 *  - [v2.0] Execution Router policy integration: loads policy:execution_router JSON
 *  - [v2.0] Arrival price capture + realized slippage bps calculation
 *  - [v2.0] Active peg count gauge: health:algo-maker-pegger:active_pegs
 *  - [v2.1] orderToPeg index: O(1) fill matching + historical order_nos tracking
 *  - [v2.1] ESCAPE_POSTED + FINALIZING states: prevents premature cleanup after escape
 *  - [v2.1] Explicit escape_triggered flag: accurate telemetry
 *  - [v2.1] Fill qty clamping: prevents avg_fill_price skew on overfills/duplicates
 *  - [v2.1] Slippage persistence: st._last_slippage_bps stored for Redis HSET
 *  - [v2.1] Heartbeat rate-limited (1s) + Redis pipeline (non-blocking)
 *  - [v2.1] Router policy: single load per loop, passed to stepPeg; negative cache
 *  - [v2.1] safeNum() validation for router policy fields (NaN protection)
 *  - [v2.1] Time-based escape deadline from router policy
 *  - [v2.1] REST path avg_fill_price update
 *  - [v2.1] Schema version in final HSET
 *
 * Dependencies:
 *   npm i ioredis
 *
 * PM2:
 *   pm2 start algo-maker-pegger.mjs --name algo-maker-pegger
 *
 * Required env:
 *   REDIS_URL, KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, KIS_ACCOUNT_PROD_CD
 */

import Redis from "ioredis";
import http from "http";
import https from "https";
import process from "process";

const env = process.env;

/* ══════════════════════════════════════════════════════════
   CONFIG
   ══════════════════════════════════════════════════════════ */

const CFG = {
  redisUrl: env.REDIS_URL || "redis://127.0.0.1:6379",

  // streams
  pegStream: env.PEG_REQUEST_STREAM || "stream:peg_requests",
  fillsStream: env.FILLS_STREAM || "stream:fills",
  execsStream: env.EXECS_STREAM || "stream:executions",

  group: env.PEG_GROUP || "maker-pegger",
  consumer: env.PEG_CONSUMER || `maker-pegger-${process.pid}`,

  // KIS API
  kisBaseUrl: env.KIS_BASE_URL || "https://openapi.koreainvestment.com:9443",
  kisAppKey: env.KIS_APP_KEY || "",
  kisAppSecret: env.KIS_APP_SECRET || "",
  kisAccountNo: env.KIS_ACCOUNT_NO || "",
  kisAccountProdCd: env.KIS_ACCOUNT_PROD_CD || "01",

  kisOrderPath: env.KIS_ORDER_PATH || "/uapi/overseas-stock/v1/trading/order",
  kisCancelPath: env.KIS_CANCEL_PATH || "/uapi/overseas-stock/v1/trading/order-rvsecncl",
  kisInquirePath: env.KIS_INQUIRE_PATH || "/uapi/overseas-stock/v1/trading/inquire-nccs",

  kisTrBuy: env.KIS_TR_BUY || "TTTT1002U",
  kisTrSell: env.KIS_TR_SELL || "TTTT1006U",
  kisTrCancel: env.KIS_TR_CANCEL || "TTTT1004U",
  kisTrInquire: env.KIS_TR_INQUIRE || "TTTT3001R",

  // timing
  pegLoopMs: Number(env.PEG_LOOP_MS || "1000"),
  pegIntervalDefault: Number(env.PEG_INTERVAL_SEC || "15"),
  maxRequotesDefault: Number(env.PEG_MAX_REQUOTES || "3"),

  // REST token bucket
  restRatePerSec: Number(env.PEG_REST_RATE_PER_SEC || "2"),
  restMaxTokens: Number(env.PEG_REST_MAX_TOKENS || "10"),

  // REST per-order cooldown
  restCooldownMs: Number(env.PEG_REST_COOLDOWN_MS || "15000"),
  restCooldownAfterRequoteMs: Number(env.PEG_REST_COOLDOWN_REQUOTE_MS || "6000"),

  // stale threshold (both fills + execs must be stale)
  staleSilenceMs: Number(env.PEG_STALE_SILENCE_MS || "10000"),

  // cancel-replace cooldown
  cancelReplaceCooldownMs: Number(env.PEG_CANCEL_REPLACE_COOLDOWN_MS || "500"),

  // escape bp (BUY)
  escapeBpDefault: Number(env.PEG_ESCAPE_BP || "15"),

  // SELL conservative defaults
  sellMaxSpreadBps: Number(env.PEG_SELL_MAX_SPREAD_BPS || "15"),
  sellMaxRequotes: Number(env.PEG_SELL_MAX_REQUOTES || "2"),
  sellEscapeAfterRequotes: Number(env.PEG_SELL_ESCAPE_AFTER_REQUOTES || "1"),
  sellEscapeBp: Number(env.PEG_SELL_ESCAPE_BP || "12"),
  sellEscapeUseBidCap: (env.PEG_SELL_ESCAPE_USE_BID_CAP || "true") === "true",
  sellEscapeBidTickOffset: Number(env.PEG_SELL_ESCAPE_BID_TICK_OFFSET || "0"),

  // request quote freshness
  requestQuoteMaxAgeMs: Number(env.PEG_REQUEST_QUOTE_MAX_AGE_MS || "5000"),

  // quote key pattern
  quoteKeyPattern: env.PEG_QUOTE_KEY || "poly:nbbo:{symbol}",

  // xread
  blockMs: Number(env.PEG_XREAD_BLOCK_MS || "2000"),
  xreadCount: Number(env.PEG_XREAD_COUNT || "50"),

  // idempotency
  seenTtlSec: Number(env.PEG_SEEN_TTL_SEC || "7200"),

  // session
  sessionEndCheckKey: env.SESSION_END_KEY || "session:state",

  // kill switch
  killKey: (pegId) => `peg:kill:${pegId}`,
  orderStateKey: (pegId) => `peg:order:${pegId}`,
  seenKey: (pegId) => `peg:seen:${pegId}`,
  restCooldownKey: (orderId) => `peg:rest:cooldown:${orderId}`,
  restTokensKey: "peg:rest:tokens",
  restRefillKey: "peg:rest:last_refill_ms",

  // [v2.0] heartbeat
  healthKey: "health:algo-maker-pegger",
  healthActivePegsKey: "health:algo-maker-pegger:active_pegs",
  healthTtlSec: 30,

  // [v2.0] telemetry stream
  execQualityStream: env.EXEC_QUALITY_STREAM || "stream:execution_quality",

  // [v2.0] execution router policy key
  routerPolicyKey: "policy:execution_router",
  routerPolicyCacheMs: 10000, // reload every 10s

  // [v2.1] ESCAPE_POSTED → FINALIZING timeout
  escapeFinalizingTimeoutMs: Number(env.PEG_ESCAPE_FINALIZING_TIMEOUT_MS || "30000"),
};

/* ── helpers ── */

function nowMs() { return Date.now(); }
function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

function jlog(level, event, fields = {}) {
  console.log(JSON.stringify({ ts_ms: nowMs(), level, event, ...fields }));
}

function safeJsonParse(s) {
  try { return JSON.parse(s); } catch { return null; }
}

/** [v2.1] Safe number conversion — returns fallback if NaN/Infinity */
function safeNum(val, fallback) {
  const n = Number(val);
  return Number.isFinite(n) ? n : fallback;
}

function spreadBps(bid, ask) {
  if (!bid || bid <= 0) return 9999;
  return Math.round(((ask - bid) / bid) * 10000);
}

/* ── Keep-Alive HTTP agent ── */

const keepAliveAgent = new https.Agent({ keepAlive: true, maxSockets: 4 });

/* ══════════════════════════════════════════════════════════
   [v2.1] ORDER-TO-PEG INDEX (O(1) fill matching)
   ══════════════════════════════════════════════════════════ */

/** Global index: broker order_no → peg_id for O(1) fill/exec matching */
const orderToPeg = new Map();

/** Attach a new broker order_no to a peg state, maintaining history */
function attachOrder(st, ordNo) {
  if (!ordNo) return;
  st.order_nos.add(ordNo);
  orderToPeg.set(ordNo, st.peg_id);
  st.broker_order_no = ordNo;
}

/** Detach all order_nos from the global index when a peg is cleaned up */
function detachAllOrders(st) {
  for (const o of st.order_nos) {
    orderToPeg.delete(o);
  }
}

/* ══════════════════════════════════════════════════════════
   [v2.1] HEARTBEAT (rate-limited 1s + pipelined)
   ══════════════════════════════════════════════════════════ */

let _heartbeatCount = 0;
let _lastHbMs = 0;

async function emitHeartbeat(redis) {
  const t = nowMs();
  if (t - _lastHbMs < 1000) return; // [v2.1] rate-limit: max once per second
  _lastHbMs = t;

  try {
    const payload = JSON.stringify({
      ts_ms: t,
      pid: process.pid,
      active_pegs: pegStates.size,
      loop_count: ++_heartbeatCount,
      uptime_sec: Math.floor(process.uptime()),
    });
    // [v2.1] Use pipeline for non-blocking dual SET
    redis.pipeline()
      .set(CFG.healthKey, payload, "EX", CFG.healthTtlSec)
      .set(CFG.healthActivePegsKey, String(pegStates.size), "EX", CFG.healthTtlSec)
      .exec()
      .catch(e => jlog("WARN", "HEARTBEAT_FAIL", { err: String(e?.message || e) }));
  } catch (e) {
    jlog("WARN", "HEARTBEAT_FAIL", { err: String(e?.message || e) });
  }
}

/* ══════════════════════════════════════════════════════════
   [v2.1] EXECUTION ROUTER POLICY LOADER (with negative cache)
   ══════════════════════════════════════════════════════════ */

let _routerPolicy = null;
let _routerPolicyTs = 0;

async function loadRouterPolicy(redis) {
  const now = nowMs();
  // [v2.1] Cache check works even when _routerPolicy is null (negative cache)
  if ((now - _routerPolicyTs) < CFG.routerPolicyCacheMs) return _routerPolicy;

  try {
    const raw = await redis.get(CFG.routerPolicyKey);
    // [v2.1] Always update timestamp (negative cache prevents Redis GET spam)
    _routerPolicyTs = now;
    if (raw) {
      const parsed = JSON.parse(raw);
      // [v2.1] Basic type validation
      if (typeof parsed === "object" && parsed !== null) {
        _routerPolicy = parsed;
      } else {
        jlog("WARN", "ROUTER_POLICY_INVALID_TYPE", { type: typeof parsed });
      }
    }
  } catch (e) {
    _routerPolicyTs = now; // [v2.1] negative cache on error too
    jlog("WARN", "ROUTER_POLICY_LOAD_FAIL", { err: String(e?.message || e) });
  }
  return _routerPolicy;
}

/**
 * [v2.1] Apply router policy overrides to pegger policy.
 * Router policy caps (tightens) limits — cannot relax beyond per-peg settings.
 * Uses safeNum() to prevent NaN from malformed policy values.
 */
function applyRouterOverrides(pegPolicy, routerPolicy, side) {
  if (!routerPolicy) return pegPolicy;
  const isSell = side === "SELL";
  const rp = routerPolicy;

  // spread caps (Math.min = can only tighten)
  if (isSell && rp.max_spread_bps_sell != null) {
    pegPolicy.maxSpread = Math.min(pegPolicy.maxSpread, safeNum(rp.max_spread_bps_sell, Infinity));
  } else if (!isSell && rp.max_spread_bps_buy != null) {
    pegPolicy.maxSpread = Math.min(pegPolicy.maxSpread, safeNum(rp.max_spread_bps_buy, Infinity));
  }

  // requote limits
  if (isSell && rp.max_requotes_sell != null) {
    pegPolicy.maxRequotes = Math.min(pegPolicy.maxRequotes, safeNum(rp.max_requotes_sell, Infinity));
  } else if (!isSell && rp.max_requotes_buy != null) {
    pegPolicy.maxRequotes = Math.min(pegPolicy.maxRequotes, safeNum(rp.max_requotes_buy, Infinity));
  }

  // [v2.1] Time-based escape deadline (stored on policy, checked in stepCheckFill)
  // NOTE: Math.min for pegInterval = router can only make escape MORE aggressive (shorter wait)
  if (isSell && rp.escape_after_ms_sell != null) {
    const escapeMs = safeNum(rp.escape_after_ms_sell, Infinity);
    pegPolicy.escapeDeadlineMs = Math.min(pegPolicy.escapeDeadlineMs || Infinity, escapeMs);
    const escapeSec = Math.ceil(escapeMs / 1000);
    pegPolicy.pegInterval = Math.min(pegPolicy.pegInterval, escapeSec);
  } else if (!isSell && rp.escape_after_ms_buy != null) {
    const escapeMs = safeNum(rp.escape_after_ms_buy, Infinity);
    pegPolicy.escapeDeadlineMs = Math.min(pegPolicy.escapeDeadlineMs || Infinity, escapeMs);
    const escapeSec = Math.ceil(escapeMs / 1000);
    pegPolicy.pegInterval = Math.min(pegPolicy.pegInterval, escapeSec);
  }

  // quote freshness from router
  if (rp.quotes?.max_quote_age_ms != null) {
    pegPolicy.requestQuoteMaxAge = Math.min(pegPolicy.requestQuoteMaxAge, safeNum(rp.quotes.max_quote_age_ms, Infinity));
  } else if (rp.quote_fresh_ms != null) {
    pegPolicy.requestQuoteMaxAge = Math.min(pegPolicy.requestQuoteMaxAge, safeNum(rp.quote_fresh_ms, Infinity));
  }

  return pegPolicy;
}

/* ══════════════════════════════════════════════════════════
   [v2.1] TELEMETRY: EXECUTION QUALITY EMITTER
   ══════════════════════════════════════════════════════════ */

async function emitExecutionQuality(redis, st) {
  try {
    const rp = _routerPolicy || {};

    // [v2.1] Telemetry sampling/gating from router policy
    const telemetryCfg = rp.telemetry || {};
    if (telemetryCfg.enabled === false) {
      jlog("DEBUG", "EXEC_QUALITY_DISABLED", { peg_id: st.peg_id });
      return;
    }

    // Calculate slippage if arrival price was captured
    let slippage_bps = null;
    if (st.arrival_bid > 0 && st.arrival_ask > 0 && st.filled_qty > 0 && st.avg_fill_price > 0) {
      const arrival_mid = (st.arrival_bid + st.arrival_ask) / 2;
      // Implementation Shortfall convention: positive = cost, negative = improvement
      if (st.side === "BUY") {
        slippage_bps = Math.round(((st.avg_fill_price - arrival_mid) / arrival_mid) * 10000 * 100) / 100;
      } else {
        slippage_bps = Math.round(((arrival_mid - st.avg_fill_price) / arrival_mid) * 10000 * 100) / 100;
      }
    }

    // [v2.1] Persist slippage on state for Redis HSET
    st._last_slippage_bps = slippage_bps;

    const elapsed_ms = st.final_ts_ms ? (st.final_ts_ms - (st.init_ts_ms || st.posted_ts_ms)) : 0;
    const fill_rate = st.total_qty > 0 ? Math.round((st.filled_qty / st.total_qty) * 10000) / 100 : 0;

    const metric = {
      ts_ms: nowMs(),
      source: "algo-maker-pegger",
      version: "v2.1",
      peg_id: st.peg_id,
      symbol: st.symbol,
      side: st.side,
      status: st.status,
      total_qty: st.total_qty,
      filled_qty: st.filled_qty,
      remaining_qty: st.remaining_qty,
      fill_rate_pct: fill_rate,
      requotes: st.requotes,
      elapsed_ms,
      arrival_bid: st.arrival_bid || 0,
      arrival_ask: st.arrival_ask || 0,
      arrival_mid: st.arrival_bid > 0 && st.arrival_ask > 0 ? (st.arrival_bid + st.arrival_ask) / 2 : 0,
      arrival_spread_bps: st.arrival_spread_bps || 0,
      avg_fill_price: Math.round((st.avg_fill_price || 0) * 100) / 100,
      fill_notional: Math.round((st.fill_notional || 0) * 100) / 100,
      slippage_bps,
      notional_usd: st.notional_usd || 0,
      broker_order_no: st.broker_order_no || "",
      // [v2.1] Explicit escape flag instead of heuristic
      escape_used: st.escape_triggered === true,
      router_mode: rp.mode || "unknown",
    };

    await redis.xadd(
      CFG.execQualityStream, "MAXLEN", "~", "10000", "*",
      "json", JSON.stringify(metric)
    );

    jlog("INFO", "EXEC_QUALITY_EMITTED", {
      peg_id: st.peg_id, slippage_bps, fill_rate_pct: fill_rate,
      elapsed_ms, requotes: st.requotes, escape_used: st.escape_triggered === true,
    });
  } catch (e) {
    jlog("WARN", "EXEC_QUALITY_EMIT_FAIL", { peg_id: st.peg_id, err: String(e?.message || e) });
  }
}

/* ══════════════════════════════════════════════════════════
   KIS API LAYER (thin wrapper)
   ══════════════════════════════════════════════════════════ */

let _kisToken = "";
let _kisTokenExpiry = 0;

async function getKisToken(redis) {
  const now = nowMs();
  if (_kisToken && _kisTokenExpiry > now + 60000) return _kisToken;

  const cached = await redis.get("kis:access_token").catch(() => null);
  if (cached) {
    _kisToken = cached;
    _kisTokenExpiry = now + 3600000;
    return _kisToken;
  }

  jlog("WARN", "KIS_TOKEN_NOT_FOUND", { msg: "No cached token in Redis" });
  return _kisToken;
}

function fetchJson(method, path, body, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(path, CFG.kisBaseUrl);
    const opts = {
      method,
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      agent: keepAliveAgent,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        authorization: `Bearer ${_kisToken}`,
        appkey: CFG.kisAppKey,
        appsecret: CFG.kisAppSecret,
        ...extraHeaders,
      },
    };

    const req = https.request(opts, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => {
        try { resolve(JSON.parse(data)); } catch { resolve({ _raw: data, _status: res.statusCode }); }
      });
    });

    req.on("error", reject);
    req.setTimeout(15000, () => { req.destroy(new Error("KIS_TIMEOUT")); });
    if (body) req.write(body);
    req.end();
  });
}

async function kisPlaceOrder(redis, symbol, side, qty, price) {
  const token = await getKisToken(redis);
  const trId = side === "BUY" ? CFG.kisTrBuy : CFG.kisTrSell;
  const body = {
    CANO: CFG.kisAccountNo,
    ACNT_PRDT_CD: CFG.kisAccountProdCd,
    OVRS_EXCG_CD: "NASD",
    PDNO: symbol,
    ORD_QTY: String(qty),
    OVRS_ORD_UNPR: String(price),
    ORD_SVR_DVSN_CD: "0",
    ORD_DVSN: "00", // limit
  };

  const headers = { tr_id: trId, custtype: "P" };
  return fetchJson("POST", CFG.kisOrderPath, JSON.stringify(body), headers);
}

async function kisCancelOrder(redis, ordNo, symbol) {
  const token = await getKisToken(redis);
  const body = {
    CANO: CFG.kisAccountNo,
    ACNT_PRDT_CD: CFG.kisAccountProdCd,
    OVRS_EXCG_CD: "NASD",
    PDNO: symbol,
    ORGN_ODNO: ordNo,
    RVSE_CNCL_DVSN_CD: "02",
    ORD_QTY: "0",
    OVRS_ORD_UNPR: "0",
    ORD_SVR_DVSN_CD: "0",
  };

  const headers = { tr_id: CFG.kisTrCancel, custtype: "P" };
  return fetchJson("POST", CFG.kisCancelPath, JSON.stringify(body), headers);
}

async function kisInquireOrder(redis, ordNo) {
  const token = await getKisToken(redis);
  const qs = new URLSearchParams({
    CANO: CFG.kisAccountNo,
    ACNT_PRDT_CD: CFG.kisAccountProdCd,
    OVRS_EXCG_CD: "NASD",
    SORT_SQN: "DS",
    CTX_AREA_FK200: "",
    CTX_AREA_NK200: "",
  });

  const headers = { tr_id: CFG.kisTrInquire, custtype: "P" };
  return fetchJson("GET", `${CFG.kisInquirePath}?${qs}`, null, headers);
}

/* ══════════════════════════════════════════════════════════
   REST TOKEN BUCKET (Lua)
   ══════════════════════════════════════════════════════════ */

const LUA_TOKEN_BUCKET = `
local tkey = KEYS[1]
local lkey = KEYS[2]
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local maxT = tonumber(ARGV[3])

local last = tonumber(redis.call('GET', lkey) or now)
local tok = tonumber(redis.call('GET', tkey) or maxT)

local delta = now - last
if delta > 0 then
  local add = math.floor((delta/1000) * rate)
  if add > 0 then
    tok = math.min(maxT, tok + add)
    last = now
  end
end

if tok >= 1 then
  tok = tok - 1
  redis.call('SET', tkey, tostring(tok), 'PX', 60000)
  redis.call('SET', lkey, tostring(last), 'PX', 60000)
  return 1
else
  redis.call('SET', tkey, tostring(tok), 'PX', 60000)
  redis.call('SET', lkey, tostring(last), 'PX', 60000)
  return 0
end
`;

async function tryAcquireRestToken(redis) {
  const ok = await redis.eval(
    LUA_TOKEN_BUCKET, 2,
    CFG.restTokensKey, CFG.restRefillKey,
    String(nowMs()), String(CFG.restRatePerSec), String(CFG.restMaxTokens)
  );
  return ok === 1;
}

/* ══════════════════════════════════════════════════════════
   QUOTE HELPERS
   ══════════════════════════════════════════════════════════ */

function quoteFromRequest(st) {
  const bid = Number(st.measured_bid || 0);
  const ask = Number(st.measured_ask || 0);
  const ts = Number(st.measured_quote_ts_ms || 0);
  const age = nowMs() - ts;

  const maxAge = st.policy?.requestQuoteMaxAge ?? CFG.requestQuoteMaxAgeMs;
  if (bid > 0 && ask > bid && age <= maxAge) {
    return { bid, ask, spread_bps: spreadBps(bid, ask), source: "REQUEST" };
  }
  return null;
}

async function getQuote(redis, symbol, st) {
  // 1) try request quote first
  const rq = quoteFromRequest(st);
  if (rq) return rq;

  // 2) Redis quote — supports both HASH (poly:nbbo) and STRING (JSON) formats
  const key = CFG.quoteKeyPattern.replace("{symbol}", symbol);
  const keyType = await redis.type(key);
  let q = null;
  if (keyType === "hash") {
    const h = await redis.hgetall(key);
    if (!h || Object.keys(h).length === 0) return null;
    q = h;
  } else {
    const raw = await redis.get(key);
    if (!raw) return null;
    q = safeJsonParse(raw);
    if (!q) return null;
  }

  const bid = Number(q.bid || q.bp || q.bidPrice || 0);
  const ask = Number(q.ask || q.ap || q.askPrice || 0);
  if (bid <= 0 || ask <= bid) return null;

  return { bid, ask, spread_bps: spreadBps(bid, ask), source: "REDIS" };
}

/* ══════════════════════════════════════════════════════════
   POLICY HELPERS (runtime Redis overrides)
   ══════════════════════════════════════════════════════════ */

async function getPolicy(redis, key, fallback) {
  const v = await redis.get(key).catch(() => null);
  if (v === null || v === undefined) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : (v === "true" ? true : v === "false" ? false : fallback);
}

async function loadPolicies(redis, side) {
  const isSell = side === "SELL";
  const spreadSkipBps = await getPolicy(redis, "policy:spread_skip_bps", 60);

  let maxSpread;
  if (isSell) {
    const sellSpread = await getPolicy(redis, "policy:peg:sell_max_spread_bps", CFG.sellMaxSpreadBps);
    maxSpread = Math.min(spreadSkipBps, sellSpread);
  } else {
    const buySpread = await getPolicy(redis, "policy:peg:max_spread_bps", 20);
    maxSpread = Math.min(spreadSkipBps, buySpread);
  }

  const maxRequotes = isSell
    ? await getPolicy(redis, "policy:peg:sell_max_requotes", CFG.sellMaxRequotes)
    : await getPolicy(redis, "policy:peg:max_requotes", CFG.maxRequotesDefault);

  const escapeAfterRequotes = isSell
    ? await getPolicy(redis, "policy:peg:sell_escape_after_requotes", CFG.sellEscapeAfterRequotes)
    : maxRequotes;

  const escapeBp = isSell
    ? await getPolicy(redis, "policy:peg:sell_escape_bp", CFG.sellEscapeBp)
    : await getPolicy(redis, "policy:peg:escape_bp", CFG.escapeBpDefault);

  const useBidCap = isSell
    ? await getPolicy(redis, "policy:peg:sell_escape_use_bid_cap", CFG.sellEscapeUseBidCap)
    : false;

  const bidTickOffset = isSell
    ? await getPolicy(redis, "policy:peg:sell_escape_bid_tick_offset", CFG.sellEscapeBidTickOffset)
    : 0;

  const pegInterval = isSell
    ? await getPolicy(redis, "policy:peg:sell_interval_sec", CFG.pegIntervalDefault)
    : await getPolicy(redis, "policy:peg:interval_sec", CFG.pegIntervalDefault);
  const requestQuoteMaxAge = await getPolicy(redis, "policy:peg:request_quote_max_age_ms", CFG.requestQuoteMaxAgeMs);

  return {
    maxSpread, maxRequotes, escapeAfterRequotes, escapeBp,
    useBidCap, bidTickOffset, pegInterval, requestQuoteMaxAge,
    escapeDeadlineMs: Infinity, // [v2.1] will be overridden by router policy
  };
}

/* ══════════════════════════════════════════════════════════
   PEG STATE MACHINE
   ══════════════════════════════════════════════════════════

   States: INIT, POSTED_MAKER, CHECK_FILL, REQUOTE, ESCAPE_TAKER, ESCAPE_POSTED, FINALIZING, DONE, ABORTED
   ══════════════════════════════════════════════════════════ */

const pegStates = new Map();

function initPegState(req) {
  const pegId = String(req.peg_id || `PEG-${nowMs()}-${Math.random().toString(36).slice(2, 6)}`);
  return {
    peg_id: pegId,
    symbol: String(req.symbol || ""),
    side: String(req.side || "BUY").toUpperCase(),
    total_qty: Math.max(1, Math.floor(Number(req.qty || req.delta_shares || 0))),
    filled_qty: 0,
    remaining_qty: Math.max(1, Math.floor(Number(req.qty || req.delta_shares || 0))),
    status: "INIT",
    broker_order_no: "",
    order_nos: new Set(),       // [v2.1] historical order numbers for fill matching
    requotes: 0,
    tick: Number(req.tick || 0.01),
    last_fill_ts_ms: 0,
    last_exec_ts_ms: 0,
    last_rest_ts_ms: 0,
    rest_backoff_ms: CFG.restCooldownMs,
    posted_ts_ms: 0,
    link: req.link || {},
    policy: null,
    // request quote cache
    measured_bid: Number(req.measured_bid || 0),
    measured_ask: Number(req.measured_ask || 0),
    measured_spread_bps: Number(req.measured_spread_bps || 0),
    measured_quote_ts_ms: Number(req.measured_quote_ts_ms || 0),
    notional_usd: Number(req.notional_usd || 0),
    peg_interval_sec: Number(req.peg_interval_sec || 0),
    max_requotes_req: Number(req.max_requotes ?? -1),
    // [v2.0] arrival price capture for slippage calculation
    init_ts_ms: nowMs(),
    final_ts_ms: 0,
    arrival_bid: 0,
    arrival_ask: 0,
    arrival_spread_bps: 0,
    avg_fill_price: 0,
    fill_notional: 0,
    // [v2.1] explicit escape tracking
    escape_triggered: false,
    escape_posted_ts_ms: 0,
    _last_slippage_bps: null,
  };
}

/* ── State transitions ── */

/* ── CORE STATE GATE helpers (added by hardening patch) ── */
let _coreGateCache = null;
let _coreGateCacheTs = 0;
const CORE_GATE_CACHE_MS = 1000;
async function readCoreGate(redis) {
  const now = Date.now();
  if (_coreGateCache && (now - _coreGateCacheTs) < CORE_GATE_CACHE_MS) return _coreGateCache;
  const [state, tradeHalt, emitKill] = await Promise.all([
    redis.get("core:state"),
    redis.get("policy:trade_halt"),
    redis.get("policy:emit_kill"),
  ]);
  _coreGateCache = { state: (state || "NORMAL").toUpperCase(), tradeHalt: tradeHalt === "true", emitKill: emitKill === "true" };
  _coreGateCacheTs = now;
  return _coreGateCache;
}
function isCoreBlocked(gate) {
  return gate.state === "HALT" || gate.state === "KILL" || gate.tradeHalt || gate.emitKill;
}

/* [v2.1] stepPeg now receives routerPolicy as parameter (loaded once per loop) */
async function stepPeg(redis, st, routerPolicy) {
  // [CORE_STATE_GATE] Global halt/kill
  const gate = await readCoreGate(redis);
  if (isCoreBlocked(gate)) {
    jlog("WARN", "CORE_STATE_BLOCK_PEG", {
      peg_id: st.peg_id, core_state: gate.state,
      trade_halt: gate.tradeHalt, emit_kill: gate.emitKill,
      broker_order_no: st.broker_order_no || null,
    });
    if (st.broker_order_no) {
      try {
        await kisCancelOrder(redis, st.broker_order_no, st.symbol);
        jlog("INFO", "CORE_STATE_CANCEL_SENT", { peg_id: st.peg_id, odno: st.broker_order_no });
      } catch (e) {
        jlog("WARN", "CORE_STATE_CANCEL_FAIL", { peg_id: st.peg_id, odno: st.broker_order_no, err: String(e?.message || e) });
      }
    }
    st.status = "ABORTED";
    st.kill_reason = "CORE_STATE_BLOCK";
    return;
  }

  // load policies once per peg lifetime
  if (!st.policy) {
    st.policy = await loadPolicies(redis, st.side);
    if (st.peg_interval_sec > 0) st.policy.pegInterval = st.peg_interval_sec;
    if (st.max_requotes_req >= 0) st.policy.maxRequotes = Math.min(st.policy.maxRequotes, st.max_requotes_req);
    // [v2.1] Apply router policy overrides (passed from pegLoop, not re-loaded)
    if (routerPolicy) {
      st.policy = applyRouterOverrides(st.policy, routerPolicy, st.side);
    }
  }

  // kill switch check
  const killed = await redis.exists(CFG.killKey(st.peg_id));
  if (killed) {
    st.status = "ABORTED";
    jlog("WARN", "PEG_KILLED", { peg_id: st.peg_id });
    return;
  }

  // session end check
  const sessState = await redis.get(CFG.sessionEndCheckKey).catch(() => null);
  if (sessState === "CLOSING" || sessState === "CLOSED") {
    if (st.status !== "DONE" && st.status !== "ABORTED" && st.status !== "ESCAPE_POSTED" && st.status !== "FINALIZING") {
      jlog("WARN", "PEG_SESSION_END", { peg_id: st.peg_id, status: st.status });
      if (st.remaining_qty > 0 && st.broker_order_no) {
        st.escape_triggered = true; // [v2.1]
        st.status = "ESCAPE_TAKER";
      } else {
        st.status = "ABORTED";
      }
    }
    return;
  }

  switch (st.status) {
    case "INIT":
      await stepInit(redis, st);
      break;
    case "POSTED_MAKER":
      await stepPostedMaker(redis, st);
      break;
    case "CHECK_FILL":
      await stepCheckFill(redis, st);
      break;
    case "REQUOTE":
      await stepRequote(redis, st);
      break;
    case "ESCAPE_TAKER":
      await stepEscapeTaker(redis, st);
      break;
    case "ESCAPE_POSTED":    // [v2.1]
      await stepEscapePosted(redis, st);
      break;
    case "FINALIZING":       // [v2.1]
      await stepFinalizing(redis, st);
      break;
    default:
      break;
  }
}

/* ── INIT ── */

async function stepInit(redis, st) {
  const q = await getQuote(redis, st.symbol, st);
  if (!q) {
    st.status = "ABORTED";
    jlog("WARN", "PEG_ABORTED_NO_QUOTE", { peg_id: st.peg_id, symbol: st.symbol });
    return;
  }

  if (q.spread_bps > st.policy.maxSpread) {
    st.status = "ABORTED";
    jlog("WARN", "PEG_ABORTED_SPREAD", {
      peg_id: st.peg_id, symbol: st.symbol,
      spread_bps: q.spread_bps, max: st.policy.maxSpread,
    });
    return;
  }

  const makerPrice = st.side === "BUY" ? q.bid : q.ask;

  // [v2.0] Capture arrival price for slippage calculation
  st.arrival_bid = q.bid;
  st.arrival_ask = q.ask;
  st.arrival_spread_bps = q.spread_bps;

  try {
    const resp = await kisPlaceOrder(redis, st.symbol, st.side, st.remaining_qty, makerPrice);
    const ordNo = resp?.output?.ODNO || resp?.output?.ORD_NO || "";

    if (!ordNo) {
      jlog("ERROR", "PEG_ORDER_FAIL", { peg_id: st.peg_id, resp });
      st.status = "ABORTED";
      return;
    }

    attachOrder(st, ordNo); // [v2.1] use attachOrder instead of direct assignment
    st.posted_ts_ms = nowMs();
    st.status = "POSTED_MAKER";

    jlog("INFO", "PEG_POSTED", {
      peg_id: st.peg_id, symbol: st.symbol, side: st.side,
      qty: st.remaining_qty, price: makerPrice, order_no: ordNo,
      quote_source: q.source,
      arrival_bid: q.bid, arrival_ask: q.ask, arrival_spread_bps: q.spread_bps,
    });
  } catch (e) {
    jlog("ERROR", "PEG_ORDER_EXCEPTION", { peg_id: st.peg_id, err: String(e?.message || e) });
    st.status = "ABORTED";
  }
}

/* ── POSTED_MAKER: wait for peg interval ── */

async function stepPostedMaker(redis, st) {
  const elapsed = nowMs() - st.posted_ts_ms;
  const intervalMs = st.policy.pegInterval * 1000;

  // [v2.1] Time-based escape deadline check
  if (st.policy.escapeDeadlineMs < Infinity) {
    const totalElapsed = nowMs() - st.init_ts_ms;
    if (totalElapsed >= st.policy.escapeDeadlineMs && st.remaining_qty > 0) {
      st.escape_triggered = true;
      st.status = "ESCAPE_TAKER";
      jlog("INFO", "PEG_ESCAPE_DEADLINE", { peg_id: st.peg_id, elapsed_ms: totalElapsed, deadline_ms: st.policy.escapeDeadlineMs });
      return;
    }
  }

  if (elapsed < intervalMs) return;
  st.status = "CHECK_FILL";
}

/* ── CHECK_FILL: determine fill status ── */

async function stepCheckFill(redis, st) {
  if (st.filled_qty >= st.total_qty) {
    st.status = "DONE";
    jlog("INFO", "PEG_DONE_FILLED", { peg_id: st.peg_id, filled_qty: st.filled_qty });
    return;
  }

  // if streams are stale, try REST
  const now = nowMs();
  const fillStale = (now - st.last_fill_ts_ms) > CFG.staleSilenceMs;
  const execStale = (now - st.last_exec_ts_ms) > CFG.staleSilenceMs;
  const cooldownOk = (now - st.last_rest_ts_ms) > st.rest_backoff_ms;

  if (fillStale && execStale && cooldownOk && st.remaining_qty > 0) {
    const tokenOk = await tryAcquireRestToken(redis);

    if (tokenOk) {
      const perOrderOk = await redis.set(
        CFG.restCooldownKey(st.broker_order_no), "1", "NX", "PX", CFG.restCooldownMs
      );

      if (perOrderOk) {
        try {
          const resp = await kisInquireOrder(redis, st.broker_order_no);
          st.last_rest_ts_ms = nowMs();
          st.rest_backoff_ms = CFG.restCooldownMs;

          const orders = resp?.output || [];
          for (const o of (Array.isArray(orders) ? orders : [orders])) {
            if (String(o?.ODNO || o?.ORD_NO) === st.broker_order_no) {
              const restFilled = Number(o?.CCLD_QTY || o?.TOT_CCLD_QTY || 0);
              if (restFilled > st.filled_qty) {
                // [v2.1] Update avg_fill_price from REST path too
                const restAvgPrice = Number(o?.AVG_PRVS || o?.CCLD_UNPR || 0);
                if (restAvgPrice > 0 && restFilled > 0) {
                  st.avg_fill_price = restAvgPrice;
                  st.fill_notional = restAvgPrice * restFilled;
                }
                st.filled_qty = Math.min(st.total_qty, restFilled);
                st.remaining_qty = st.total_qty - st.filled_qty;
                jlog("INFO", "PEG_REST_FILL_UPDATE", {
                  peg_id: st.peg_id, filled_qty: st.filled_qty, avg_price: restAvgPrice, source: "REST",
                });
              }
              const stat = String(o?.ORD_TMD_DVSN_NM || o?.PRCS_STAT_NM || "").toUpperCase();
              if (stat.includes("체결") || stat.includes("FILL")) {
                st.last_exec_ts_ms = nowMs();
              }
            }
          }
        } catch (e) {
          jlog("WARN", "PEG_REST_FAIL", { peg_id: st.peg_id, err: String(e?.message || e) });
          st.rest_backoff_ms = Math.min(60000, Math.max(CFG.restCooldownMs, st.rest_backoff_ms * 2));
          st.last_rest_ts_ms = nowMs();
        }
      }
    }
  }

  // re-check after REST
  if (st.filled_qty >= st.total_qty) {
    st.status = "DONE";
    jlog("INFO", "PEG_DONE_FILLED", { peg_id: st.peg_id, filled_qty: st.filled_qty });
    return;
  }

  // [v2.1] Time-based escape deadline
  if (st.policy.escapeDeadlineMs < Infinity) {
    const totalElapsed = nowMs() - st.init_ts_ms;
    if (totalElapsed >= st.policy.escapeDeadlineMs && st.remaining_qty > 0) {
      st.escape_triggered = true;
      st.status = "ESCAPE_TAKER";
      return;
    }
  }

  // SELL conservative: fast escape by requote threshold
  const escapeThreshold = st.policy.escapeAfterRequotes;
  if (st.requotes >= escapeThreshold && st.remaining_qty > 0) {
    st.escape_triggered = true; // [v2.1]
    st.status = "ESCAPE_TAKER";
    return;
  }

  // check if market moved away
  const q = await getQuote(redis, st.symbol, st);
  if (!q) {
    st.status = "POSTED_MAKER";
    st.posted_ts_ms = nowMs();
    return;
  }

  let needRequote = true; // simplified: always requote after interval

  if (needRequote && st.requotes < st.policy.maxRequotes) {
    if (st.last_fill_ts_ms > 0 && (nowMs() - st.last_fill_ts_ms) < st.policy.pegInterval * 1000) {
      st.status = "POSTED_MAKER";
      st.posted_ts_ms = nowMs();
      return;
    }
    st.status = "REQUOTE";
  } else if (st.requotes >= st.policy.maxRequotes && st.remaining_qty > 0) {
    st.escape_triggered = true; // [v2.1]
    st.status = "ESCAPE_TAKER";
  } else {
    st.status = "POSTED_MAKER";
    st.posted_ts_ms = nowMs();
  }
}

/* ── REQUOTE: Cancel & Replace ── */

async function stepRequote(redis, st) {
  if (!st.broker_order_no) {
    st.status = "ABORTED";
    return;
  }

  try {
    await kisCancelOrder(redis, st.broker_order_no, st.symbol);
    jlog("INFO", "PEG_CANCEL_SENT", { peg_id: st.peg_id, order_no: st.broker_order_no });
  } catch (e) {
    jlog("WARN", "PEG_CANCEL_FAIL", { peg_id: st.peg_id, err: String(e?.message || e) });
  }

  const cooldown = CFG.cancelReplaceCooldownMs + Math.floor(Math.random() * 300);
  await new Promise(r => setTimeout(r, cooldown));

  const q = await getQuote(redis, st.symbol, st);
  if (!q || q.spread_bps > st.policy.maxSpread) {
    st.status = "ABORTED";
    jlog("WARN", "PEG_REQUOTE_ABORTED_SPREAD", {
      peg_id: st.peg_id, spread_bps: q?.spread_bps, max: st.policy.maxSpread,
    });
    return;
  }

  let newPrice;
  if (st.side === "BUY") {
    newPrice = Math.min(q.bid + st.tick, q.ask - st.tick);
  } else {
    newPrice = Math.max(q.ask - st.tick, q.bid + st.tick);
  }
  newPrice = Math.round(newPrice * 100) / 100;

  try {
    const resp = await kisPlaceOrder(redis, st.symbol, st.side, st.remaining_qty, newPrice);
    const ordNo = resp?.output?.ODNO || resp?.output?.ORD_NO || "";

    if (!ordNo) {
      jlog("ERROR", "PEG_REQUOTE_ORDER_FAIL", { peg_id: st.peg_id, resp });
      st.status = st.remaining_qty > 0 ? "ESCAPE_TAKER" : "DONE";
      return;
    }

    attachOrder(st, ordNo); // [v2.1] use attachOrder
    st.requotes += 1;
    st.posted_ts_ms = nowMs();
    st.status = "POSTED_MAKER";

    jlog("INFO", "PEG_REQUOTE_POSTED", {
      peg_id: st.peg_id, symbol: st.symbol, side: st.side,
      price: newPrice, remaining_qty: st.remaining_qty,
      requote_num: st.requotes, order_no: ordNo,
    });
  } catch (e) {
    jlog("ERROR", "PEG_REQUOTE_EXCEPTION", { peg_id: st.peg_id, err: String(e?.message || e) });
    st.status = st.remaining_qty > 0 ? "ESCAPE_TAKER" : "DONE";
  }
}

/* ── ESCAPE_TAKER: aggressively fill remaining ── */

async function stepEscapeTaker(redis, st) {
  if (st.remaining_qty <= 0) {
    st.status = "DONE";
    return;
  }

  if (st.broker_order_no) {
    try {
      await kisCancelOrder(redis, st.broker_order_no, st.symbol);
    } catch {}
    await new Promise(r => setTimeout(r, CFG.cancelReplaceCooldownMs));
  }

  const q = await getQuote(redis, st.symbol, st);
  if (!q) {
    st.status = "ABORTED";
    jlog("WARN", "PEG_ESCAPE_NO_QUOTE", { peg_id: st.peg_id });
    return;
  }

  let escapePrice;
  const bp = st.policy.escapeBp;

  if (st.side === "BUY") {
    const mid = (q.bid + q.ask) / 2;
    escapePrice = mid * (1 + bp / 10000);
    escapePrice = Math.round(escapePrice * 100) / 100;
  } else {
    const mid = (q.bid + q.ask) / 2;
    let rawEscape = mid * (1 - bp / 10000);
    if (st.policy.useBidCap) {
      const bidFloor = q.bid - (st.policy.bidTickOffset * st.tick);
      rawEscape = Math.max(rawEscape, bidFloor);
    }
    escapePrice = Math.round(rawEscape * 100) / 100;
  }

  try {
    const resp = await kisPlaceOrder(redis, st.symbol, st.side, st.remaining_qty, escapePrice);
    const ordNo = resp?.output?.ODNO || resp?.output?.ORD_NO || "";

    if (!ordNo) {
      jlog("ERROR", "PEG_ESCAPE_ORDER_FAIL", { peg_id: st.peg_id, resp });
      st.status = "ABORTED";
      return;
    }

    attachOrder(st, ordNo); // [v2.1] use attachOrder
    st.escape_triggered = true; // [v2.1] explicit flag
    st.escape_posted_ts_ms = nowMs(); // [v2.1]
    st.status = "ESCAPE_POSTED"; // [v2.1] NOT DONE — wait for fills

    jlog("INFO", "PEG_ESCAPE_SENT", {
      peg_id: st.peg_id, symbol: st.symbol, side: st.side,
      price: escapePrice, remaining_qty: st.remaining_qty,
      order_no: ordNo, bid_cap: st.policy.useBidCap,
    });
  } catch (e) {
    jlog("ERROR", "PEG_ESCAPE_EXCEPTION", { peg_id: st.peg_id, err: String(e?.message || e) });
    st.status = "ABORTED";
  }
}

/* ── [v2.1] ESCAPE_POSTED: wait for escape order fills ── */

async function stepEscapePosted(redis, st) {
  // Check if fully filled
  if (st.filled_qty >= st.total_qty) {
    st.status = "DONE";
    jlog("INFO", "PEG_ESCAPE_FILLED", { peg_id: st.peg_id, filled_qty: st.filled_qty });
    return;
  }

  // Timeout: move to FINALIZING for REST confirmation
  const elapsed = nowMs() - st.escape_posted_ts_ms;
  if (elapsed > CFG.escapeFinalizingTimeoutMs) {
    st.status = "FINALIZING";
    jlog("INFO", "PEG_ESCAPE_TIMEOUT_FINALIZING", { peg_id: st.peg_id, elapsed_ms: elapsed });
    return;
  }

  // Otherwise, wait for stream fills (they'll be consumed in consumeFills)
}

/* ── [v2.1] FINALIZING: REST confirmation before cleanup ── */

async function stepFinalizing(redis, st) {
  // Try REST inquiry to confirm final fill state
  const tokenOk = await tryAcquireRestToken(redis);
  if (tokenOk && st.broker_order_no) {
    try {
      const resp = await kisInquireOrder(redis, st.broker_order_no);
      const orders = resp?.output || [];
      for (const o of (Array.isArray(orders) ? orders : [orders])) {
        if (String(o?.ODNO || o?.ORD_NO) === st.broker_order_no) {
          const restFilled = Number(o?.CCLD_QTY || o?.TOT_CCLD_QTY || 0);
          if (restFilled > st.filled_qty) {
            const restAvgPrice = Number(o?.AVG_PRVS || o?.CCLD_UNPR || 0);
            if (restAvgPrice > 0) {
              st.avg_fill_price = restAvgPrice;
              st.fill_notional = restAvgPrice * restFilled;
            }
            st.filled_qty = Math.min(st.total_qty, restFilled);
            st.remaining_qty = st.total_qty - st.filled_qty;
          }
        }
      }
    } catch (e) {
      jlog("WARN", "PEG_FINALIZING_REST_FAIL", { peg_id: st.peg_id, err: String(e?.message || e) });
    }
  }

  // Transition to terminal state
  if (st.filled_qty >= st.total_qty) {
    st.status = "DONE";
  } else {
    st.status = "DONE"; // Accept partial fill; remaining was escape attempt
  }

  jlog("INFO", "PEG_FINALIZED", {
    peg_id: st.peg_id, filled_qty: st.filled_qty, remaining_qty: st.remaining_qty,
    avg_fill_price: Math.round((st.avg_fill_price || 0) * 100) / 100,
  });
}

/* ══════════════════════════════════════════════════════════
   FILLS / EXECUTIONS CONSUMER
   ══════════════════════════════════════════════════════════ */

async function consumeFills(redis) {
  if (pegStates.size === 0) return;

  try {
    const res = await redis.xreadgroup(
      "GROUP", CFG.group, CFG.consumer,
      "COUNT", CFG.xreadCount,
      "BLOCK", 100,
      "STREAMS", CFG.fillsStream, ">"
    );

    if (!res) return;

    const msgs = res?.[0]?.[1] || [];
    for (const [id, kvs] of msgs) {
      const fields = {};
      for (let i = 0; i < kvs.length; i += 2) fields[kvs[i]] = kvs[i + 1];

      const fill = safeJsonParse(fields.json || "{}");
      if (!fill) { await redis.xack(CFG.fillsStream, CFG.group, id); continue; }

      const ordNo = String(fill.order_no || fill.ODNO || fill.odno || "");
      const fillQty = Number(fill.fill_qty || fill.CCLD_QTY || fill.qty || 0);
      const fillPrice = Number(fill.fill_price || fill.CCLD_UNPR || fill.price || 0);
      const fillTs = Number(fill.fill_ts_ms || fill.ts_ms || nowMs());

      // [v2.1] O(1) lookup via orderToPeg index instead of O(N) scan
      const pegId = orderToPeg.get(ordNo);
      if (pegId) {
        const st = pegStates.get(pegId);
        if (st) {
          // watermark check: skip out-of-order
          if (fillTs >= st.last_fill_ts_ms) {
            // [v2.1] Clamp fill qty to prevent overfill skew
            const deltaQty = Math.max(0, Math.min(fillQty, st.total_qty - st.filled_qty));
            if (deltaQty > 0 && fillPrice > 0) {
              st.fill_notional = (st.fill_notional || 0) + fillPrice * deltaQty;
              st.filled_qty += deltaQty;
              st.remaining_qty = st.total_qty - st.filled_qty;
              st.avg_fill_price = st.fill_notional / st.filled_qty;
            } else if (deltaQty > 0) {
              // fill without price (unusual)
              st.filled_qty += deltaQty;
              st.remaining_qty = st.total_qty - st.filled_qty;
            }
            st.last_fill_ts_ms = fillTs;

            jlog("INFO", "PEG_FILL_CONSUMED", {
              peg_id: st.peg_id, fill_qty: fillQty, clamped_qty: deltaQty,
              fill_price: fillPrice,
              filled_qty: st.filled_qty, remaining_qty: st.remaining_qty,
              avg_fill_price: Math.round(st.avg_fill_price * 100) / 100,
            });

            if (st.filled_qty >= st.total_qty) {
              st.status = "DONE";
            }
          }
        }
      }

      await redis.xack(CFG.fillsStream, CFG.group, id);
    }
  } catch (e) {
    if (!String(e?.message).includes("NOGROUP")) {
      jlog("WARN", "FILL_CONSUME_ERR", { err: String(e?.message || e) });
    } else {
      // [v2.1] Auto-recreate consumer group on NOGROUP
      await ensureGroups(redis).catch(() => {});
    }
  }
}

async function consumeExecutions(redis) {
  if (pegStates.size === 0) return;

  try {
    const res = await redis.xreadgroup(
      "GROUP", CFG.group, CFG.consumer,
      "COUNT", CFG.xreadCount,
      "BLOCK", 100,
      "STREAMS", CFG.execsStream, ">"
    );

    if (!res) return;

    const msgs = res?.[0]?.[1] || [];
    for (const [id, kvs] of msgs) {
      const fields = {};
      for (let i = 0; i < kvs.length; i += 2) fields[kvs[i]] = kvs[i + 1];

      const exec = safeJsonParse(fields.json || "{}");
      if (!exec) { await redis.xack(CFG.execsStream, CFG.group, id); continue; }

      const ordNo = String(exec.order_no || exec.ODNO || exec.odno || "");
      const status = String(exec.status || exec.ORD_STATUS || "").toUpperCase();

      // [v2.1] O(1) lookup
      const pegId = orderToPeg.get(ordNo);
      if (pegId) {
        const st = pegStates.get(pegId);
        if (st) {
          st.last_exec_ts_ms = nowMs();
          if (status.includes("FILL") || status.includes("체결")) {
            // hint only; actual qty from fills stream
          }
        }
      }

      await redis.xack(CFG.execsStream, CFG.group, id);
    }
  } catch (e) {
    if (!String(e?.message).includes("NOGROUP")) {
      jlog("WARN", "EXEC_CONSUME_ERR", { err: String(e?.message || e) });
    } else {
      await ensureGroups(redis).catch(() => {});
    }
  }
}

/* ══════════════════════════════════════════════════════════
   PEG REQUEST CONSUMER
   ══════════════════════════════════════════════════════════ */

async function consumePegRequests(redis) {
  try {
    const res = await redis.xreadgroup(
      "GROUP", CFG.group, CFG.consumer,
      "COUNT", 10,
      "BLOCK", CFG.blockMs,
      "STREAMS", CFG.pegStream, ">"
    );

    if (!res) return;

    const msgs = res?.[0]?.[1] || [];
    for (const [id, kvs] of msgs) {
      const fields = {};
      for (let i = 0; i < kvs.length; i += 2) fields[kvs[i]] = kvs[i + 1];

      const req = safeJsonParse(fields.json || "{}");
      if (!req || !req.symbol) {
        await redis.xack(CFG.pegStream, CFG.group, id);
        continue;
      }

      const st = initPegState(req);

      // idempotency
      const seenOk = await redis.set(CFG.seenKey(st.peg_id), "1", "NX", "EX", CFG.seenTtlSec);
      if (!seenOk) {
        jlog("INFO", "PEG_DUPLICATE_SKIPPED", { peg_id: st.peg_id });
        await redis.xack(CFG.pegStream, CFG.group, id);
        continue;
      }

      pegStates.set(st.peg_id, st);

      jlog("INFO", "PEG_REQUEST_ACCEPTED", {
        peg_id: st.peg_id, symbol: st.symbol, side: st.side,
        qty: st.total_qty, notional_usd: st.notional_usd,
      });

      await redis.xack(CFG.pegStream, CFG.group, id);
    }
  } catch (e) {
    jlog("WARN", "PEG_REQUEST_CONSUME_ERR", { err: String(e?.message || e) });
  }
}

/* ══════════════════════════════════════════════════════════
   MAIN LOOP
   ══════════════════════════════════════════════════════════ */

async function ensureGroups(redis) {
  for (const stream of [CFG.pegStream, CFG.fillsStream, CFG.execsStream]) {
    try {
      await redis.xgroup("CREATE", stream, CFG.group, "$", "MKSTREAM");
    } catch (e) {
      if (!String(e?.message).includes("BUSYGROUP")) {
        jlog("WARN", "GROUP_CREATE_FAIL", { stream, err: String(e?.message || e) });
      }
    }
  }
}

async function pegLoop(redis, routerPolicy) {
  // [v2.1] Heartbeat (rate-limited, pipelined, non-blocking)
  await emitHeartbeat(redis);

  // 1) consume fills
  await consumeFills(redis);

  // 2) consume executions
  await consumeExecutions(redis);

  // 3) step each active peg
  const done = [];
  for (const [pegId, st] of pegStates) {
    if (st.status === "DONE" || st.status === "ABORTED") {
      done.push(pegId);
      continue;
    }
    try {
      await stepPeg(redis, st, routerPolicy); // [v2.1] pass routerPolicy
    } catch (e) {
      jlog("ERROR", "PEG_STEP_ERR", { peg_id: pegId, err: String(e?.message || e) });
      st.status = "ABORTED";
      done.push(pegId);
    }
  }

  // 4) cleanup done pegs
  for (const pegId of done) {
    const st = pegStates.get(pegId);
    if (st) {
      st.final_ts_ms = nowMs();

      // [v2.1] Emit execution quality telemetry (stores _last_slippage_bps on st)
      await emitExecutionQuality(redis, st);

      // persist final state to Redis (for audit / recovery)
      await redis.hset(CFG.orderStateKey(pegId), {
        schema_version: "2.1", // [v2.1]
        peg_id: st.peg_id,
        symbol: st.symbol,
        side: st.side,
        total_qty: String(st.total_qty),
        filled_qty: String(st.filled_qty),
        remaining_qty: String(st.remaining_qty),
        status: st.status,
        requotes: String(st.requotes),
        broker_order_no: st.broker_order_no,
        final_ts_ms: String(st.final_ts_ms),
        avg_fill_price: String(Math.round((st.avg_fill_price || 0) * 100) / 100),
        fill_notional: String(Math.round((st.fill_notional || 0) * 100) / 100),
        arrival_bid: String(st.arrival_bid || 0),
        arrival_ask: String(st.arrival_ask || 0),
        slippage_bps: String(st._last_slippage_bps ?? ""),
        escape_triggered: String(st.escape_triggered),
        router_mode: (_routerPolicy?.mode || "unknown"),
      }).catch(() => {});
      await redis.expire(CFG.orderStateKey(pegId), 86400).catch(() => {});

      jlog("INFO", "PEG_FINAL", {
        peg_id: st.peg_id, status: st.status,
        filled_qty: st.filled_qty, remaining_qty: st.remaining_qty,
        requotes: st.requotes,
        avg_fill_price: Math.round((st.avg_fill_price || 0) * 100) / 100,
        slippage_bps: st._last_slippage_bps,
        arrival_bid: st.arrival_bid, arrival_ask: st.arrival_ask,
        escape_triggered: st.escape_triggered,
      });

      // [v2.1] Detach all order_nos from global index
      detachAllOrders(st);
    }
    pegStates.delete(pegId);
  }
}

async function main() {
  const redis = new Redis(CFG.redisUrl, { enableReadyCheck: true, maxRetriesPerRequest: null });

  redis.on("error", (e) => jlog("ERROR", "REDIS_ERROR", { err: String(e?.message || e) }));
  redis.on("connect", () => jlog("INFO", "REDIS_CONNECT", { url: CFG.redisUrl }));

  await ensureGroups(redis);

  let stopping = false;
  const stop = async (sig) => {
    if (stopping) return;
    stopping = true;
    jlog("WARN", "SHUTDOWN_SIGNAL", { sig });
    for (const [, st] of pegStates) {
      if (st.broker_order_no && st.status !== "DONE") {
        try { await kisCancelOrder(redis, st.broker_order_no, st.symbol); } catch {}
      }
    }
    try { await redis.quit(); } catch {}
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);

  // [v2.1] Load router policy on startup
  const startupPolicy = await loadRouterPolicy(redis);

  jlog("INFO", "MAKER_PEGGER_START", {
    version: "v2.1",
    pegStream: CFG.pegStream,
    fillsStream: CFG.fillsStream,
    execsStream: CFG.execsStream,
    execQualityStream: CFG.execQualityStream,
    group: CFG.group,
    consumer: CFG.consumer,
    healthKey: CFG.healthKey,
    routerPolicyKey: CFG.routerPolicyKey,
    routerMode: startupPolicy?.mode || "not_loaded",
    escapeFinalizingTimeoutMs: CFG.escapeFinalizingTimeoutMs,
  });

  // [v2.1] Emit initial heartbeat immediately
  await emitHeartbeat(redis);

  // main loop
  while (!stopping) {
    try {
      await consumePegRequests(redis);
      // [v2.1] Load router policy once per loop, pass to pegLoop
      const routerPolicy = await loadRouterPolicy(redis);
      await pegLoop(redis, routerPolicy);
    } catch (e) {
      jlog("ERROR", "MAIN_LOOP_ERR", { err: String(e?.message || e) });
    }
    await new Promise(r => setTimeout(r, CFG.pegLoopMs));
  }
}

main().catch((e) => {
  jlog("ERROR", "FATAL", { err: String(e?.message || e) });
  process.exit(1);
});
