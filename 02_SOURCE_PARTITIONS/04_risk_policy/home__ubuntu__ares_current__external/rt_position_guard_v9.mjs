#!/usr/bin/env node
/**
 * rt_position_guard_v9.mjs — Production-Grade Real-Time Position Guard
 * ═══════════════════════════════════════════════════════════════════════
 * Version: 9.0.0
 * Architecture: PM2 persistent process (NOT cron)
 *
 * First Principles Redesign:
 *   1. Reads price:{SYM} DIRECTLY — no middleman mirror
 *   2. Uses emarkos:v6:order:intent stream — same as live-trading-kis
 *   3. Data confidence scoring — no binary mode switching
 *   4. Self-healing with last-known-good price cache + decay
 *   5. Individual position drawdown monitoring
 *   6. Health telemetry for governance.py + structural-integrity-guard
 *
 * Redis Keys Read:
 *   - price:{SYM}              (from realtime-data-feed)
 *   - emarkos:v1:positions     (current positions)
 *   - emarkos:v1:open_orders   (pending orders)
 *
 * Redis Keys Written:
 *   - emarkos:v6:order:intent  (SELL intents — XADD)
 *   - ctx:rt_guard:health      (health telemetry — JSON string, TTL 300s)
 *   - ctx:rt_guard:last_ts     (epoch seconds)
 *   - rt_guard:v9:lkg:{SYM}    (last-known-good prices, no TTL)
 *   - kpi:rt_guard:sells:{YYYYMMDD}  (daily sell counter)
 *   - kpi:rt_guard:triggers:{YYYYMMDD}:{SYM}  (per-symbol daily trigger counter)
 */

import Redis from "ioredis";
import { readFileSync } from "fs";

// ═══════════════════════════════════════════════════════════════
// CONFIGURATION
// ═══════════════════════════════════════════════════════════════

const PROC = "rt-position-guard-v9";
const VERSION = "9.0.0";

// Redis
const REDIS_URL = process.env.REDIS_URL || "rediss://ares-admin:AresAdmin2026SecureA3kB6jY1xZ8@master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com:6379/0";
let REDIS_PASSWORD = process.env.REDIS_PASSWORD || "";
if (!REDIS_PASSWORD) {
  try {
    const envFile = readFileSync("/home/ubuntu/.env", "utf-8");
    for (const line of envFile.split("\n")) {
      if (line.includes("REDIS_PASSWORD") && line.includes("=")) {
        REDIS_PASSWORD = line.split("=")[1].trim().replace(/["']/g, "");
        break;
      }
    }
  } catch { /* ignore */ }
}

const redis = new Redis(REDIS_URL, {
  password: REDIS_PASSWORD || undefined,
  maxRetriesPerRequest: 3,
  retryStrategy: (times) => Math.min(times * 500, 5000),
  lazyConnect: false,
});

// Telegram
const TG_TOKEN = process.env.TG_TOKEN || "7580588555:AAGVMhaEY4V3Q3i9omqou19Yi3PuCVWc0Io";
const TG_CHAT = process.env.TG_CHAT || "7850622860";

// ─── Trading Window (ET) ───
const START_HHMM = 920;   // 09:20 ET
const END_HHMM   = 1610;  // 16:10 ET
const PRE_MARKET  = 900;  // pre-market monitoring starts

// ─── Cycle Interval ───
const CYCLE_MS = 15_000;  // 15 seconds (vs old 60s cron)

// ─── Sentinel ETFs for Data Confidence ───
// Only use symbols that actually have price:{SYM} keys from realtime-data-feed
const SENTINEL_ETFS = {
  // Tier 1: Critical (weight 1.0 each)
  QQQ: { weight: 1.0, tier: 1 },
  SPY: { weight: 1.0, tier: 1 },
  VIX: { weight: 1.0, tier: 1 },
  // Tier 2: Important (weight 0.7 each)
  IWM: { weight: 0.7, tier: 2 },
  XLY: { weight: 0.7, tier: 2 },
  XLP: { weight: 0.7, tier: 2 },
  HYG: { weight: 0.7, tier: 2 },
  IEF: { weight: 0.7, tier: 2 },
  // Tier 3: Supplementary (weight 0.4 each)
  TLT: { weight: 0.4, tier: 3 },
  GLD: { weight: 0.4, tier: 3 },
  XLK: { weight: 0.4, tier: 3 },
  XLF: { weight: 0.4, tier: 3 },
};

// ─── Drawdown Thresholds ───
// Scaled by data confidence: lower confidence → more conservative (wider thresholds)
const THRESHOLDS = {
  // Base thresholds (at confidence = 1.0)
  PARTIAL_25: -0.02,   // -2% → sell 25%
  PARTIAL_50: -0.03,   // -3% → sell 50%
  FULL_SELL:  -0.04,   // -4% → sell 100%
  // Confidence scaling
  MIN_CONFIDENCE_TO_ACT: 0.3,  // Below this, only monitor (no sells)
  CONFIDENCE_SCALE_FACTOR: 1.5, // threshold / confidence^factor
};

// ─── Safety Limits ───
const DAILY_MAX_TRIGGERS_PER_SYMBOL = 3;
const CONFIRM_CYCLES = 2;  // Must persist across 2 cycles (30s)
const SELL_BP = 0.001;     // 10bp price concession for limit orders
const LKG_DECAY_MINUTES = 10; // Last-known-good decays to 0 confidence after 10 min

// ─── DRY RUN MODE ───
const DRY_RUN = process.env.RT_GUARD_DRY_RUN === "true";

// ─── Redis Key Constants ───
const POS_KEY = "emarkos:v1:positions";
const OPEN_ORDERS_KEY = "emarkos:v1:open_orders";
const ORDER_INTENT_STREAM = "emarkos:v6:order:intent";
const HEALTH_KEY = "ctx:rt_guard:health";
const LAST_TS_KEY = "ctx:rt_guard:last_ts";
const LKG_PREFIX = "rt_guard:v9:lkg:";

// ═══════════════════════════════════════════════════════════════
// UTILITY FUNCTIONS
// ═══════════════════════════════════════════════════════════════

function isoNow() { return new Date().toISOString(); }
function epochNow() { return Math.floor(Date.now() / 1000); }
function yyyymmddET() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" }).replaceAll("-", "");
}

function nyHHMM() {
  const s = new Date().toLocaleString("en-US", { timeZone: "America/New_York", hour12: false });
  const m = s.match(/,\s(\d{1,2}):(\d{2}):/);
  if (!m) return 0;
  return Number(String(m[1]).padStart(2, "0") + m[2]);
}

function inWindow(hhmm) { return hhmm >= START_HHMM && hhmm <= END_HHMM; }
function inPreMarket(hhmm) { return hhmm >= PRE_MARKET && hhmm < START_HHMM; }

function log(level, event, data = {}) {
  const entry = {
    ts: isoNow(),
    proc: PROC,
    level,
    event,
    ...data,
  };
  const line = `[${PROC}] [${level}] ${event} ${JSON.stringify(data)}`;
  if (level === "ERROR" || level === "CRITICAL") {
    console.error(line);
  } else {
    console.log(line);
  }
}

async function sendTelegram(msg) {
  try {
    const url = `https://api.telegram.org/bot${TG_TOKEN}/sendMessage`;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: TG_CHAT, text: msg, parse_mode: "HTML" }),
    });
    if (!resp.ok) log("WARN", "TG_SEND_FAIL", { status: resp.status });
  } catch (e) {
    log("WARN", "TG_SEND_ERROR", { error: e.message });
  }
}

async function jget(key) {
  const v = await redis.get(key);
  if (!v) return null;
  try { return JSON.parse(v); } catch { return null; }
}

// ═══════════════════════════════════════════════════════════════
// DATA CONFIDENCE ENGINE
// ═══════════════════════════════════════════════════════════════

const lkgCache = new Map(); // In-memory last-known-good cache

async function fetchPriceWithConfidence(sym) {
  // Try live price
  const raw = await redis.get(`price:${sym}`);
  if (raw) {
    const price = Number(raw);
    if (Number.isFinite(price) && price > 0) {
      // Update last-known-good
      lkgCache.set(sym, { price, ts: Date.now() });
      await redis.set(`${LKG_PREFIX}${sym}`, JSON.stringify({ price, ts: Date.now() }));
      return { price, confidence: 1.0, source: "live" };
    }
  }

  // Try JSON format (some keys store {price: N})
  const jsonRaw = await redis.get(`price:${sym}`);
  if (jsonRaw) {
    try {
      const parsed = JSON.parse(jsonRaw);
      const price = parsed?.price ?? parsed?.last ?? parsed?.close;
      if (Number.isFinite(price) && price > 0) {
        lkgCache.set(sym, { price, ts: Date.now() });
        await redis.set(`${LKG_PREFIX}${sym}`, JSON.stringify({ price, ts: Date.now() }));
        return { price, confidence: 1.0, source: "live_json" };
      }
    } catch { /* not JSON */ }
  }

  // Try kis:price:{sym} fallback
  const kisRaw = await redis.get(`kis:price:${sym}`);
  if (kisRaw) {
    const price = Number(kisRaw);
    if (Number.isFinite(price) && price > 0) {
      lkgCache.set(sym, { price, ts: Date.now() });
      return { price, confidence: 0.9, source: "kis_fallback" };
    }
  }

  // Try last-known-good from memory
  const lkg = lkgCache.get(sym);
  if (lkg) {
    const ageMin = (Date.now() - lkg.ts) / 60_000;
    const decay = Math.max(0, 1.0 - (ageMin / LKG_DECAY_MINUTES));
    if (decay > 0) {
      return { price: lkg.price, confidence: decay * 0.8, source: "lkg_memory" };
    }
  }

  // Try last-known-good from Redis
  const lkgRedis = await jget(`${LKG_PREFIX}${sym}`);
  if (lkgRedis?.price) {
    const ageMin = (Date.now() - (lkgRedis.ts || 0)) / 60_000;
    const decay = Math.max(0, 1.0 - (ageMin / LKG_DECAY_MINUTES));
    if (decay > 0) {
      lkgCache.set(sym, lkgRedis);
      return { price: lkgRedis.price, confidence: decay * 0.6, source: "lkg_redis" };
    }
  }

  return { price: null, confidence: 0, source: "missing" };
}

function calculateDataConfidence(priceResults) {
  let totalWeight = 0;
  let weightedConfidence = 0;

  for (const [sym, cfg] of Object.entries(SENTINEL_ETFS)) {
    const result = priceResults.get(sym);
    const conf = result?.confidence ?? 0;
    totalWeight += cfg.weight;
    weightedConfidence += cfg.weight * conf;
  }

  return totalWeight > 0 ? weightedConfidence / totalWeight : 0;
}

// ═══════════════════════════════════════════════════════════════
// POSITION DRAWDOWN ENGINE
// ═══════════════════════════════════════════════════════════════

// Track intraday high per symbol for drawdown calculation
const intradayHighs = new Map();
const confirmCounters = new Map(); // sym → consecutive trigger count

function resetDayState() {
  intradayHighs.clear();
  confirmCounters.clear();
  log("INFO", "DAY_STATE_RESET");
}

function calculatePositionDrawdown(sym, currentPrice) {
  const high = intradayHighs.get(sym) || currentPrice;
  if (currentPrice > high) {
    intradayHighs.set(sym, currentPrice);
    return 0;
  }
  intradayHighs.set(sym, high);
  return high > 0 ? (currentPrice - high) / high : 0;
}

function getAdjustedThresholds(dataConfidence) {
  // Lower confidence → wider (more negative) thresholds → less aggressive
  const scale = Math.pow(Math.max(dataConfidence, 0.3), THRESHOLDS.CONFIDENCE_SCALE_FACTOR);
  return {
    partial25: THRESHOLDS.PARTIAL_25 / scale,
    partial50: THRESHOLDS.PARTIAL_50 / scale,
    fullSell:  THRESHOLDS.FULL_SELL / scale,
  };
}

function determineSellAction(drawdown, thresholds) {
  if (drawdown <= thresholds.fullSell)  return { action: "SELL", pct: 1.00, tier: "PANIC" };
  if (drawdown <= thresholds.partial50) return { action: "PARTIAL", pct: 0.50, tier: "CRITICAL" };
  if (drawdown <= thresholds.partial25) return { action: "PARTIAL", pct: 0.25, tier: "DEFAULT" };
  return { action: "HOLD", pct: 0, tier: "SAFE" };
}

// ═══════════════════════════════════════════════════════════════
// ORDER INTENT PUBLISHER
// ═══════════════════════════════════════════════════════════════

async function emitSellIntent(sym, shares, price, pct, reason, drawdown, dataConfidence) {
  const limitPx = +(price * (1 - SELL_BP)).toFixed(2);
  const intentId = `rtg9-${sym}-${Date.now()}`;
  const intent = {
    intent_id: intentId,
    run_id: `rtg9-run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ts: isoNow(),
    symbol: sym,
    side: "SELL",
    delta_shares: -Math.abs(shares),
    limit_price: limitPx,
    reason,
    source: PROC,
    version: VERSION,
    meta: {
      drawdown: +drawdown.toFixed(4),
      data_confidence: +dataConfidence.toFixed(3),
      sell_pct: pct,
      dry_run: DRY_RUN,
    },
  };

  if (DRY_RUN) {
    log("INFO", "DRY_RUN_SELL_INTENT", intent);
    return intentId;
  }

  try {
    await redis.xadd(ORDER_INTENT_STREAM, "*", ...Object.entries(intent).flat().map(v =>
      typeof v === "object" ? JSON.stringify(v) : String(v)
    ));
    log("WARN", "SELL_INTENT_PUBLISHED", { intent_id: intentId, symbol: sym, shares, pct, reason });

    // KPI counters
    const day = yyyymmddET();
    await redis.incr(`kpi:rt_guard:sells:${day}`);
    await redis.expire(`kpi:rt_guard:sells:${day}`, 86400 * 14);

    return intentId;
  } catch (e) {
    log("ERROR", "SELL_INTENT_PUBLISH_FAIL", { error: e.message, intent_id: intentId });
    return null;
  }
}

// ═══════════════════════════════════════════════════════════════
// HEALTH TELEMETRY
// ═══════════════════════════════════════════════════════════════

async function publishHealth(metrics) {
  const health = {
    ts: Date.now(),
    version: VERSION,
    proc: PROC,
    dry_run: DRY_RUN,
    ...metrics,
    status: metrics.data_confidence >= 0.5 ? "OK" :
            metrics.data_confidence >= 0.3 ? "DEGRADED" : "CRITICAL",
  };

  try {
    await redis.setex(HEALTH_KEY, 300, JSON.stringify(health));
    await redis.set(LAST_TS_KEY, String(epochNow()));
  } catch (e) {
    log("ERROR", "HEALTH_PUBLISH_FAIL", { error: e.message });
  }
}

// ═══════════════════════════════════════════════════════════════
// MAIN CYCLE
// ═══════════════════════════════════════════════════════════════

let lastDay = "";
let cycleCount = 0;
let totalSells = 0;

async function runCycle() {
  const cycleStart = Date.now();
  cycleCount++;

  const hhmm = nyHHMM();
  const today = yyyymmddET();

  // Day rollover reset
  if (today !== lastDay) {
    resetDayState();
    lastDay = today;
    totalSells = 0;
    log("INFO", "NEW_DAY", { date: today });
  }

  // Outside monitoring window → lightweight health only
  if (!inWindow(hhmm) && !inPreMarket(hhmm)) {
    await publishHealth({
      data_confidence: 0,
      positions_monitored: 0,
      positions_selling: 0,
      missing_price_keys: 0,
      fallbacks_used: 0,
      error_count: 0,
      latency_ms: Date.now() - cycleStart,
      hhmm,
      window: "OUTSIDE",
      cycle: cycleCount,
    });
    return;
  }

  let errorCount = 0;
  let fallbacksUsed = 0;
  let missingKeys = 0;
  let positionsMonitored = 0;
  let positionsSelling = 0;

  try {
    // ─── Step 1: Fetch sentinel ETF prices & calculate data confidence ───
    const priceResults = new Map();
    for (const sym of Object.keys(SENTINEL_ETFS)) {
      const result = await fetchPriceWithConfidence(sym);
      priceResults.set(sym, result);
      if (result.source === "missing") missingKeys++;
      if (result.source.startsWith("lkg")) fallbacksUsed++;
    }

    const dataConfidence = calculateDataConfidence(priceResults);
    const thresholds = getAdjustedThresholds(dataConfidence);

    // Log mode info (every 20 cycles ≈ 5 min)
    if (cycleCount % 20 === 1) {
      const available = [...priceResults.entries()].filter(([, r]) => r.confidence > 0).map(([s]) => s);
      const missing = [...priceResults.entries()].filter(([, r]) => r.confidence === 0).map(([s]) => s);
      log("INFO", "DATA_CONFIDENCE", {
        confidence: +dataConfidence.toFixed(3),
        available,
        missing,
        fallbacks: fallbacksUsed,
        thresholds: {
          partial25: +thresholds.partial25.toFixed(4),
          partial50: +thresholds.partial50.toFixed(4),
          fullSell: +thresholds.fullSell.toFixed(4),
        },
      });
    }

    // ─── Step 2: Pre-market → monitor only, no sells ───
    if (inPreMarket(hhmm)) {
      await publishHealth({
        data_confidence: +dataConfidence.toFixed(3),
        positions_monitored: 0,
        positions_selling: 0,
        missing_price_keys: missingKeys,
        fallbacks_used: fallbacksUsed,
        error_count: errorCount,
        latency_ms: Date.now() - cycleStart,
        hhmm,
        window: "PRE_MARKET",
        cycle: cycleCount,
      });
      return;
    }

    // ─── Step 3: Read positions ───
    const posRaw = await jget(POS_KEY);
    if (!posRaw) {
      log("WARN", "NO_POSITIONS_DATA");
      await publishHealth({
        data_confidence: +dataConfidence.toFixed(3),
        positions_monitored: 0,
        positions_selling: 0,
        missing_price_keys: missingKeys,
        fallbacks_used: fallbacksUsed,
        error_count: 0,
        latency_ms: Date.now() - cycleStart,
        hhmm,
        window: "ACTIVE",
        cycle: cycleCount,
      });
      return;
    }

    // Parse positions (handle both array and object formats)
    // [PATCH 20260507] emarkos:v1:positions wraps positions as { positions: { SYM: {...} } }.
    // Convert object dict to array with explicit symbol field so the for-loop can iterate.
    let positions = [];
    if (Array.isArray(posRaw)) {
      positions = posRaw;
    } else if (posRaw.positions) {
      const inner = posRaw.positions;
      if (Array.isArray(inner)) {
        positions = inner;
      } else if (inner && typeof inner === "object") {
        positions = Object.entries(inner).map(([sym, data]) => ({
          symbol: sym,
          ...(typeof data === "object" ? data : { qty: Number(data) }),
        }));
      }
    } else if (typeof posRaw === "object") {
      positions = Object.entries(posRaw).map(([sym, data]) => ({
        symbol: sym,
        ...(typeof data === "object" ? data : { qty: Number(data) }),
      }));
    }

    // Read open orders to avoid duplicate sells
    const openOrdersRaw = await jget(OPEN_ORDERS_KEY);
    const openOrders = openOrdersRaw?.orders || openOrdersRaw?.open_orders || [];

    // ─── Step 4: Evaluate each position ───
    let unparsedSamples = [];
    for (const pos of (Array.isArray(positions) ? positions : [])) {
      const sym = pos.symbol || pos.sym;
      // [PATCH 20260507] Support emarkos:v1:positions schema:
      //   qty aliases: economic_qty | sellable_qty | settled_qty | trade_basis_qty
      //   avg aliases: avg_px (CTRP/JTTT formula output)
      const qty = Number(
        pos.qty ?? pos.quantity ?? pos.shares ??
        pos.economic_qty ?? pos.sellable_qty ?? pos.settled_qty ?? pos.trade_basis_qty ?? 0
      );
      const entryPrice = Number(
        pos.avg_price ?? pos.entry_price ?? pos.avgPrice ?? pos.avg_px ?? 0
      );

      if (!sym || qty <= 0) {
        if (sym && unparsedSamples.length < 3) unparsedSamples.push({ sym, keys: Object.keys(pos).slice(0, 8) });
        continue;
      }
      positionsMonitored++;

      // Get current price
      const priceData = await fetchPriceWithConfidence(sym);
      if (!priceData.price || priceData.confidence === 0) continue;

      const currentPrice = priceData.price;

      // Calculate intraday drawdown from high
      const drawdown = calculatePositionDrawdown(sym, currentPrice);

      // Determine action
      const decision = determineSellAction(drawdown, thresholds);

      if (decision.action === "HOLD") {
        confirmCounters.delete(sym);
        continue;
      }

      // Below minimum confidence → monitor only
      if (dataConfidence < THRESHOLDS.MIN_CONFIDENCE_TO_ACT) {
        log("INFO", "LOW_CONFIDENCE_SKIP", { symbol: sym, drawdown: +drawdown.toFixed(4), dataConfidence: +dataConfidence.toFixed(3) });
        continue;
      }

      // Check pending sell orders
      const hasPending = Array.isArray(openOrders) && openOrders.some(
        o => String(o.symbol || "") === sym && String(o.side || "").toUpperCase() === "SELL"
      );
      if (hasPending) {
        log("DEBUG", "PENDING_SELL_EXISTS", { symbol: sym });
        continue;
      }

      // Confirmation counter (must trigger N consecutive cycles)
      const count = (confirmCounters.get(sym) || 0) + 1;
      confirmCounters.set(sym, count);

      if (count < CONFIRM_CYCLES) {
        log("INFO", "CONFIRMING", { symbol: sym, drawdown: +drawdown.toFixed(4), count, required: CONFIRM_CYCLES });
        continue;
      }

      // Daily limit check
      const dayKey = `kpi:rt_guard:triggers:${today}:${sym}`;
      const dailyCount = Number(await redis.get(dayKey) || 0);
      if (dailyCount >= DAILY_MAX_TRIGGERS_PER_SYMBOL) {
        log("WARN", "DAILY_LIMIT_REACHED", { symbol: sym, count: dailyCount });
        continue;
      }

      // Calculate shares to sell
      const sellShares = Math.max(1, Math.floor(qty * decision.pct));
      const reason = `RT_GUARD_V9_${decision.tier}`;

      // Emit sell intent
      const intentId = await emitSellIntent(
        sym, sellShares, currentPrice, decision.pct, reason, drawdown, dataConfidence
      );

      if (intentId) {
        positionsSelling++;
        totalSells++;
        confirmCounters.delete(sym);

        // Increment daily trigger counter
        await redis.incr(dayKey);
        await redis.expire(dayKey, 86400 * 14);

        // Alert on significant sells
        if (decision.pct >= 0.5) {
          await sendTelegram(
            `⚠️ <b>RT Guard v9 ${DRY_RUN ? "[DRY-RUN]" : ""}</b>\n` +
            `${decision.tier}: ${sym} ${decision.action} ${(decision.pct * 100).toFixed(0)}%\n` +
            `DD: ${(drawdown * 100).toFixed(2)}% | Conf: ${(dataConfidence * 100).toFixed(0)}%\n` +
            `Shares: ${sellShares} @ $${currentPrice.toFixed(2)}`
          );
        }
      }
    }

    // [PATCH 20260507] Diagnostic: log unparsed positions occasionally
    if (unparsedSamples.length > 0 && cycleCount % 20 === 1) {
      log("WARN", "POSITIONS_UNPARSED_SAMPLE", { count: unparsedSamples.length, samples: unparsedSamples });
    }

    // ─── Step 5: Publish health ───
    await publishHealth({
      data_confidence: +dataConfidence.toFixed(3),
      positions_monitored: positionsMonitored,
      positions_selling: positionsSelling,
      missing_price_keys: missingKeys,
      fallbacks_used: fallbacksUsed,
      error_count: errorCount,
      latency_ms: Date.now() - cycleStart,
      hhmm,
      window: "ACTIVE",
      cycle: cycleCount,
      total_sells_today: totalSells,
    });

    // Summary log (every 20 cycles ≈ 5 min)
    if (cycleCount % 20 === 0) {
      log("INFO", "CYCLE_SUMMARY", {
        cycle: cycleCount,
        confidence: +dataConfidence.toFixed(3),
        monitored: positionsMonitored,
        selling: positionsSelling,
        totalSellsToday: totalSells,
        latency: Date.now() - cycleStart,
      });
    }

  } catch (err) {
    errorCount++;
    log("ERROR", "CYCLE_ERROR", { error: err.message, stack: err.stack?.slice(0, 300) });
    await publishHealth({
      data_confidence: 0,
      positions_monitored: positionsMonitored,
      positions_selling: 0,
      missing_price_keys: missingKeys,
      fallbacks_used: fallbacksUsed,
      error_count: errorCount,
      latency_ms: Date.now() - cycleStart,
      hhmm,
      window: "ERROR",
      cycle: cycleCount,
    });
  }
}

// ═══════════════════════════════════════════════════════════════
// STARTUP & LIFECYCLE
// ═══════════════════════════════════════════════════════════════

async function startup() {
  log("INFO", "STARTING", { version: VERSION, dry_run: DRY_RUN, cycle_ms: CYCLE_MS });

  // Verify Redis connection
  try {
    await redis.ping();
    log("INFO", "REDIS_CONNECTED");
  } catch (e) {
    log("CRITICAL", "REDIS_CONNECT_FAIL", { error: e.message });
    await sendTelegram(`🔴 <b>RT Guard v9 STARTUP FAIL</b>\nRedis connection failed: ${e.message}`);
    process.exit(1);
  }

  // Verify order intent stream exists
  try {
    const streamInfo = await redis.xinfo("STREAM", ORDER_INTENT_STREAM).catch(() => null);
    if (!streamInfo) {
      log("WARN", "ORDER_STREAM_NOT_FOUND", { stream: ORDER_INTENT_STREAM });
    } else {
      log("INFO", "ORDER_STREAM_OK", { stream: ORDER_INTENT_STREAM });
    }
  } catch {
    log("WARN", "ORDER_STREAM_CHECK_FAIL");
  }

  // Startup notification
  await sendTelegram(
    `✅ <b>RT Guard v9 Started</b>\n` +
    `Version: ${VERSION}\n` +
    `Mode: ${DRY_RUN ? "DRY-RUN (no sells)" : "ACTIVE"}\n` +
    `Cycle: ${CYCLE_MS / 1000}s\n` +
    `Sentinels: ${Object.keys(SENTINEL_ETFS).join(", ")}`
  );

  // Initial health
  await publishHealth({
    data_confidence: 0,
    positions_monitored: 0,
    positions_selling: 0,
    missing_price_keys: 0,
    fallbacks_used: 0,
    error_count: 0,
    latency_ms: 0,
    hhmm: nyHHMM(),
    window: "STARTUP",
    cycle: 0,
  });
}

// Graceful shutdown
process.on("SIGINT", async () => {
  log("INFO", "SHUTTING_DOWN", { reason: "SIGINT" });
  await sendTelegram(`⚠️ <b>RT Guard v9 Shutting Down</b>\nReason: SIGINT`);
  await redis.quit();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  log("INFO", "SHUTTING_DOWN", { reason: "SIGTERM" });
  await sendTelegram(`⚠️ <b>RT Guard v9 Shutting Down</b>\nReason: SIGTERM`);
  await redis.quit();
  process.exit(0);
});

process.on("uncaughtException", async (err) => {
  log("CRITICAL", "UNCAUGHT_EXCEPTION", { error: err.message, stack: err.stack?.slice(0, 500) });
  await sendTelegram(`🔴 <b>RT Guard v9 CRASH</b>\n${err.message}`);
  // Don't exit — PM2 will restart, but let the loop continue if possible
});

// ─── Main Loop ───
(async () => {
  await startup();

  // Persistent interval loop
  setInterval(async () => {
    try {
      await runCycle();
    } catch (e) {
      log("ERROR", "MAIN_LOOP_ERROR", { error: e.message });
    }
  }, CYCLE_MS);

  // Run first cycle immediately
  await runCycle();
})();
