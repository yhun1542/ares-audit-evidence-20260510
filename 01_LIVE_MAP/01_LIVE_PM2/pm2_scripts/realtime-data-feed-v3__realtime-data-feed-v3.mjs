/**
 * realtime-data-feed-v3.mjs — 시간축 인식(Temporal-Aware) 가격 피드
 * 
 * v3 핵심 변경사항 (v2 대비):
 * 1. data_sources_v82.mjs 사용 (실시간/기준가 분리된 합의 시스템)
 * 2. price:{symbol}에 실시간 가격만 저장 (전일 종가 혼입 방지)
 * 3. 폴백 시에도 Polygon /prev 대신 Yahoo direct → Finnhub 순서 유지
 * 4. 가격 급변 감지 + 텔레그램 알림
 * 5. 기존 v2와 동일한 Redis 키 구조 유지 (호환성)
 * 
 * @version 3.0.0
 * @date 2026-04-17
 */

import { installCrashGuard as installRebalCrashGuard } from "./lib/patches/PATCH-006-rebal-gate-crash-guard.mjs";
import { updateAllContext, writeMacroRegime, writeBreadthHealth } from "./context_writer_v81.mjs";
import { getPriceConsensus, getVIXConsensus, checkSourceHealth } from "./data_sources_v82.mjs";
import https from 'https';
import Redis from 'ioredis';
import { installCrashGuard } from './shared/crash-guard.mjs';
import { guardedPriceSet } from './price-write-guard.mjs';

// ─── CONFIG ───
const REDIS_URL = process.env.REDIS_URL;
const FETCH_INTERVAL_MS = 60000;  // 1분
const HEALTH_CHECK_INTERVAL_MS = 30000;  // 30초
const CRITICAL_SYMBOLS = ["SPY", "QQQ", "IWM"];
const MAX_CONSECUTIVE_FAILURES = 5;  // [PATCH-E01] 5회로 완화: 일시적 지연 허용
const FINNHUB_API_KEY = process.env.FINNHUB_API_KEY || "";
const TG_TOKEN = process.env.TG_TOKEN || "7580588555:AAGVMhaEY4V3Q3i9omqou19Yi3PuCVWc0Io";
const TG_CHAT = process.env.TG_CHAT || "7850622860";

// 가격 급변 임계값 (전 사이클 대비)
// [PRICE-SPIKE-FIX-20260424] env-tunable + age guard
const PRICE_SPIKE_THRESHOLD = Number(process.env.PRICE_SPIKE_THRESHOLD || 0.10);
const PRICE_SPIKE_MAX_AGE_MS = Number(process.env.PRICE_SPIKE_MAX_AGE_MS || 600000); // 10m default
const PRICE_SPIKE_CONSENSUS_MAX_DISAGREE = Number(process.env.PRICE_SPIKE_CONSENSUS_MAX_DISAGREE || 4.0);  // [3AI-FIX] 장외 가격 차이 허용: 3→4% // %
// ROOTFIX-20260424: source disagreement should produce symbol-level quarantine,
// not a process-level crash or global halt. Defaults reflect the live false-positive report.
const PRICE_DISAGREE_WARN_PCT = Number(process.env.PRICE_DISAGREE_WARN_PCT || '2.0');
const PRICE_DISAGREE_HARD_PCT = Number(process.env.PRICE_DISAGREE_HARD_PCT || '5.0');
const PRICE_LOW_CONF_WARN = Number(process.env.PRICE_LOW_CONF_WARN || '0.30');

// ─── TELEMETRY STATE ───
const state = {
  redisConnected: false,
  consecutiveRedisFailures: 0,
  consecutiveFetchFailures: 0,
  lastSuccessfulWrite: null,
  lastFetchCycle: null,
  criticalSymbolsMissing: [],
  totalCycles: 0,
  totalRedisReconnects: 0,
  startTime: new Date().toISOString(),
  priceCache: {}, // 이전 사이클 가격 캐시 (급변 감지용)
};

// ─── STRUCTURED LOGGING ───
function log(level, message, data = {}) {
  const ts = new Date().toISOString();
  const entry = { ts, level, proc: "realtime-data-feed-v3", message, ...data };
  if (level === "ERROR" || level === "WARN") {
    console.error(JSON.stringify(entry));
  } else {
    console.log(JSON.stringify(entry));
  }
}

// ─── TELEGRAM ALERT ───
async function sendTelegram(text) {
  try {
    const body = JSON.stringify({ chat_id: TG_CHAT, text, parse_mode: "HTML" });
    const res = await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    if (!res.ok) log("WARN", "Telegram send failed", { status: res.status });
  } catch (e) {
    log("WARN", "Telegram error", { error: e.message });
  }
}

// ─── REDIS CLIENT (ioredis with auto-reconnect) ───
const redis = new Redis(REDIS_URL, {
  retryStrategy(times) {
    const delay = Math.min(times * 500, 30000);
    log("WARN", `Redis reconnecting (attempt ${times})`, { delay });
    state.totalRedisReconnects++;
    return delay;
  },
  maxRetriesPerRequest: 3,
  enableOfflineQueue: true,
  lazyConnect: false,
  reconnectOnError(err) {
    const targetErrors = ["READONLY", "ECONNRESET", "ETIMEDOUT", "ECONNREFUSED"];
    return targetErrors.some(e => err.message.includes(e));
  },
});

redis.on("connect", () => { state.redisConnected = true; state.consecutiveRedisFailures = 0; log("INFO", "Redis connected"); });
redis.on("ready", () => { state.redisConnected = true; log("INFO", "Redis ready"); });
redis.on("error", (err) => {
  state.consecutiveRedisFailures++;
  log("ERROR", "Redis error", { error: err.message, consecutiveFailures: state.consecutiveRedisFailures });
  if (state.consecutiveRedisFailures === 5) {
    sendTelegram("🔴 <b>realtime-data-feed-v3: Redis 연결 5회 연속 실패</b>\n자동 재연결 시도 중...");
  }
});
redis.on("reconnecting", () => { state.redisConnected = false; log("WARN", "Redis reconnecting..."); });
redis.on("close", () => { state.redisConnected = false; log("WARN", "Redis connection closed"); });

// ─── SAFE REDIS WRITE (with retry) ───
async function safeRedisSet(key, value, options = {}) {
  const maxRetries = 3;
  for (let i = 0; i < maxRetries; i++) {
    try {
      if (options.ex) {
        await redis.set(key, value, "EX", options.ex);
      } else {
        await redis.set(key, value);
      }
      return true;
    } catch (err) {
      log("WARN", `Redis SET retry ${i + 1}/${maxRetries}`, { key, error: err.message });
      if (i < maxRetries - 1) await sleep(500 * (i + 1));
    }
  }
  log("ERROR", "Redis SET failed after retries", { key });
  return false;
}

// ─── GLOBAL ERROR HANDLERS ───
process.on('uncaughtException', (err) => {
  log("ERROR", "Uncaught exception", { error: err.message, code: err.code, stack: err.stack?.slice(0, 300) });
  if (!['ECONNRESET', 'ETIMEDOUT', 'ECONNREFUSED', 'EPIPE'].includes(err.code)) {
    log("ERROR", "Non-network uncaught exception - continuing", { code: err.code });
  }
});

process.on('unhandledRejection', (reason) => {
  const msg = reason?.message || String(reason);
  log("ERROR", "Unhandled rejection", { reason: msg });
});

// ─── CHAMPION UNIVERSE ───
const CHAMPION_UNIVERSE = [
  // Original 45
  "AAPL", "ABBV", "ASML", "BA", "CAT", "CL", "COP", "CVX", "DE",
  "JNJ", "KO", "LMT", "MA", "MCD", "META", "MO", "REGN", "MRK", "MS",
  "MU", "NOC", "PG", "TGT", "XOM",
  "CMCSA", "CRWD", "GILD", "GOOGL", "HD", "NFLX", "OKTA", "PEP", "TSLA", "VRTX", "MSFT", "AMZN",
  "SPY", "QQQ", "IWM", "BIL", "XLY", "XLP", "HYG", "IEF", "NVDA",
  // SSOT expanded (v1.1 hotfix)
  "ADBE", "AMD", "ARM", "AVGO", "AXP", "BAC", "BRK.B", "COST", "CVS",
  "GD", "GE", "GS", "INTC", "JPM", "LLY", "LOW", "PFE", "PLTR",
  "PM", "QCOM", "RTX", "SMCI", "SNOW", "SYY", "UNH", "V", "WFC", "WMT"
];

// ROOTFIX-20260430-DYNAMIC-UNIVERSE:
// Feed universe must follow live champion SSOT, current broker positions, and regime/guardrail anchors.
// Static CHAMPION_UNIVERSE remains a fallback only; it must not silently diverge from Redis champion:universe:global.
const REQUIRED_REFERENCE_SYMBOLS = [
  "SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "XLF", "XLK",
  "BIL", "XLY", "XLP", "HYG", "IEF"
];

function normalizeSymbol(sym) {
  return String(sym || '').trim().toUpperCase().replace('/', '.');
}

async function readChampionUniverseFromRedis() {
  try {
    const syms = await redis.smembers('champion:universe:global');
    return syms.map(normalizeSymbol).filter(Boolean);
  } catch (e) {
    log('WARN', 'champion universe redis read failed', { error: e.message });
    return [];
  }
}

async function readBrokerPositionSymbolsFromRedis() {
  try {
    const raw = await redis.get('truth:broker:positions:v2') || await redis.get('truth:broker:positions');
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    const arr = Array.isArray(parsed?.positions) ? parsed.positions : (Array.isArray(parsed) ? parsed : []);
    return arr.map(x => normalizeSymbol(x?.symbol || x?.ticker || x?.pdno || x?.code)).filter(Boolean);
  } catch (e) {
    log('WARN', 'broker positions universe read failed', { error: e.message });
    return [];
  }
}

async function buildOrderedUniverse() {
  const champion = await readChampionUniverseFromRedis();
  const positions = await readBrokerPositionSymbolsFromRedis();
  const championSource = champion.length >= 20 ? 'redis:champion:universe:global' : 'static:fallback';
  const base = champion.length >= 20 ? champion : CHAMPION_UNIVERSE;
  const merged = [...CRITICAL_SYMBOLS, ...REQUIRED_REFERENCE_SYMBOLS, ...base, ...positions]
    .map(normalizeSymbol).filter(Boolean);
  const seen = new Set();
  const ordered = [];
  for (const sym of merged) {
    if (sym === 'VIX') continue;
    if (!seen.has(sym)) { seen.add(sym); ordered.push(sym); }
  }
  await safeRedisSet('realtime:feed:universe:source', championSource);
  await safeRedisSet('realtime:feed:universe:validation', JSON.stringify({
    ts: new Date().toISOString(),
    champion_count: champion.length,
    fallback_count: CHAMPION_UNIVERSE.length,
    position_count: positions.length,
    ref_count: REQUIRED_REFERENCE_SYMBOLS.length,
    total: ordered.length,
    source: championSource,
    required_reference_missing: REQUIRED_REFERENCE_SYMBOLS.filter(s => !seen.has(s)),
  }));
  return ordered;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// ─── DATA SOURCE: Yahoo Finance Direct (Tier 2 Fallback) ───
async function fetchYahooQuote(symbol) {
  return new Promise((resolve, reject) => {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=1d&range=15d`;
    const req = https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' }, timeout: 10000 }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          const result = json.chart.result[0];
          const quote = result.indicators.quote[0];
          const closes = quote.close.filter(c => c !== null);
          const volumes = quote.volume.filter(v => v !== null);
          const currentPrice = closes[closes.length - 1];
          const prevPrice = closes[closes.length - 2] || currentPrice;
          const mom3d = closes.length >= 3 ? (closes[closes.length-1] / closes[closes.length-3] - 1) : 0;
          const mom5d = closes.length >= 5 ? (closes[closes.length-1] / closes[closes.length-5] - 1) : 0;
          const mom10d = closes.length >= 10 ? (closes[closes.length-1] / closes[closes.length-10] - 1) : 0;
          const returns = [];
          for (let i = 1; i < closes.length; i++) returns.push(closes[i] / closes[i-1] - 1);
          const volatility = Math.sqrt(returns.reduce((a, b) => a + b*b, 0) / returns.length) * Math.sqrt(252);
          const avgVolume = volumes.slice(0, -1).reduce((a, b) => a + b, 0) / (volumes.length - 1);
          const volumeRatio = volumes[volumes.length - 1] / avgVolume;
          resolve({ symbol, price: currentPrice, change: (currentPrice - prevPrice) / prevPrice, mom3d, mom5d, mom10d, volatility, volumeRatio });
        } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Yahoo timeout')); });
  });
}

// ─── DATA SOURCE: Finnhub (Tier 3 Fallback) ───
async function fetchFinnhubQuote(symbol) {
  if (!FINNHUB_API_KEY) return null;
  try {
    const res = await fetch(`https://finnhub.io/api/v1/quote?symbol=${symbol}&token=${FINNHUB_API_KEY}`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) return null;
    const data = await res.json();
    return (data.c && data.c > 0) ? { price: data.c, source: "finnhub" } : null;
  } catch (e) { return null; }
}

// ─── 3-TIER FALLBACK FETCH (v3: Polygon /prev 제거) ───
async function fetchWithTripleFallback(symbol) {
  // Tier 1: data_sources_v82 consensus (실시간 소스만 사용)
  try {
    const consensus = await getPriceConsensus(symbol);
    if (consensus && consensus.price > 0) {
      return { ...consensus, source: consensus.source || "consensus_v82" };
    }
  } catch (e) {
    log("WARN", `Consensus v82 failed for ${symbol}`, { error: e.message });
  }

  // Tier 2: Yahoo Finance direct (실시간)
  try {
    const yahoo = await fetchYahooQuote(symbol);
    if (yahoo && yahoo.price > 0) {
      return { ...yahoo, source: "yahoo_direct" };
    }
  } catch (e) {
    log("WARN", `Yahoo failed for ${symbol}`, { error: e.message });
  }

  // Tier 3: Finnhub (실시간)
  const finnhub = await fetchFinnhubQuote(symbol);
  if (finnhub) return finnhub;

  // ❌ v3에서 제거: Polygon REST /prev (전일 종가) 폴백
  // v2에서는 여기에 fetchPolygonQuote(symbol)이 있었으나,
  // 이것이 전일 종가를 실시간 가격으로 저장하는 근본 원인이었음.
  // 대신 data_sources_v82.mjs가 참고용으로 별도 저장함.

  log("ERROR", `ALL REALTIME SOURCES FAILED for ${symbol}`);
  return null;
}


// ROOTFIX-20260424: publish consensus metadata and symbol quarantine hints for OIE.
function extractDisagreementPct(data) {
  const direct = Number(data?.disagreement_pct ?? data?.max_disagreement_pct ?? data?.diff_pct ?? data?.source_disagreement_pct);
  if (Number.isFinite(direct) && direct >= 0) return direct;
  const sources = data?.source_prices || data?.prices_by_source || data?.raw_prices || null;
  if (sources && typeof sources === 'object') {
    const vals = Object.values(sources).map(v => Number(typeof v === 'object' ? (v.price ?? v.value ?? v.last) : v)).filter(v => Number.isFinite(v) && v > 0);
    if (vals.length >= 2) {
      const hi = Math.max(...vals), lo = Math.min(...vals);
      return ((hi - lo) / Math.max(hi, lo)) * 100;
    }
  }
  return 0;
}

async function publishPriceConsensusGuard(symbol, data) {
  const disagreementPct = extractDisagreementPct(data);
  const confidence = Number(data?.confidence ?? 1);
  let level = 'ok';
  if (disagreementPct >= PRICE_DISAGREE_HARD_PCT) level = 'hard';
  else if (disagreementPct >= PRICE_DISAGREE_WARN_PCT || confidence < PRICE_LOW_CONF_WARN) level = 'warn';
  const payload = {
    symbol,
    ts: new Date().toISOString(),
    price: Number(data?.price || 0),
    source: data?.source || 'unknown',
    confidence: Number.isFinite(confidence) ? confidence : null,
    disagreement_pct: Number(disagreementPct.toFixed(4)),
    level,
    source_count: Number(data?.sources || data?.rt_sources || data?.source_count || 0) || null,
  };
  await safeRedisSet(`price:consensus:${symbol}`, JSON.stringify(payload), { ex: 300 });
  if (level === 'hard') {
    await safeRedisSet(`policy:price:quarantine:${symbol}`, JSON.stringify({ ...payload, action: 'hard', reason: 'PRICE_SOURCE_DISAGREE_HARD' }), { ex: 300 });
    log('WARN', `PRICE_QUARANTINE_HARD ${symbol}`, payload);
  } else if (level === 'warn') {
    await safeRedisSet(`policy:price:quarantine:${symbol}`, JSON.stringify({ ...payload, action: 'warn', reason: 'PRICE_SOURCE_DISAGREE_WARN' }), { ex: 180 });
    log('WARN', `PRICE_QUARANTINE_WARN ${symbol}`, payload);
  } else {
    await redis.del(`policy:price:quarantine:${symbol}`).catch(() => {});
  }
  return payload;
}

// ─── VIX FETCH ───
async function fetchVIX() {
  return new Promise((resolve, reject) => {
    const url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d';
    const req = https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' }, timeout: 10000 }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          const closes = json.chart.result[0].indicators.quote[0].close.filter(c => c !== null);
          resolve({ current: closes[closes.length - 1], ma: closes.reduce((a, b) => a + b, 0) / closes.length, d5_ago: closes[0] });
        } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('VIX timeout')); });
  });
}

// ─── MAIN UPDATE CYCLE ───
async function updateMarketData() {
  state.totalCycles++;
  const cycleStart = Date.now();
  const cycleMissing = [];
  const cycleSuccess = [];

  log("INFO", `Fetch cycle #${state.totalCycles} starting`, { redisConnected: state.redisConnected });

  try {
    // === VIX ===
    let vixValue = null;
    try {
      const vixConsensus = await getVIXConsensus();
      if (vixConsensus) {
        vixValue = vixConsensus;
      } else {
        const vixData = await fetchVIX();
        vixValue = vixData.current;
      }
    } catch (e) {
      log("WARN", "VIX fetch failed", { error: e.message });
    }

    if (vixValue) {
      await safeRedisSet('market:vix', vixValue.toString());
      await safeRedisSet('price:VIX', vixValue.toString(), { ex: 600 });
      await safeRedisSet('market:vix:ts', new Date().toISOString());
      await safeRedisSet('market:vix:updated_at', new Date().toISOString());
      await safeRedisSet('market:vix:current', vixValue.toString());
      await safeRedisSet('signals:latest', JSON.stringify({ vix: vixValue, updated: new Date().toISOString(), source: 'realtime-data-feed-v3' }), { ex: 7200 });
      log("INFO", `VIX: ${vixValue.toFixed(2)}`);
    }

    // === INDIVIDUAL SYMBOLS (Critical first) ===
    const orderedUniverse = await buildOrderedUniverse();

    for (const symbol of orderedUniverse) {
      try {
        const data = await fetchWithTripleFallback(symbol);
        if (data && data.price > 0) {
          // === ROOTFIX-20260424: consensus/quarantine metadata ===
          const consensusMeta = await publishPriceConsensusGuard(symbol, data).catch(e => { log("WARN", `PRICE_CONSENSUS_GUARD_FAIL ${symbol}`, { error: e.message }); return null; });

          // === 가격 급변 감지 [PRICE-SPIKE-FIX-20260424] ===
          // Multi-guard: age, source-change, consensus-disagreement.
          if (!state.priceCacheMeta) state.priceCacheMeta = {};
          const prevMeta = state.priceCacheMeta[symbol] || null;
          const prevPrice = prevMeta ? prevMeta.price : state.priceCache[symbol];
          const nowMs = Date.now();
          if (prevPrice && prevPrice > 0) {
            const changeRatio = Math.abs(data.price - prevPrice) / prevPrice;
            if (changeRatio > PRICE_SPIKE_THRESHOLD) {
              // Suppression reasons
              const prevAgeMs = prevMeta && prevMeta.ts ? (nowMs - prevMeta.ts) : null;
              const prevSrc = prevMeta ? prevMeta.source : null;
              const currSrc = data.source || "unknown";
              const disagree = consensusMeta && Number.isFinite(Number(consensusMeta.disagreement_pct))
                ? Number(consensusMeta.disagreement_pct) : 0;
              const consensusLevel = consensusMeta ? consensusMeta.level : "ok";
              // [SPIKE-GUARD-20260427-v2] suppress spike alerts when price is in quarantine (untradable, unreliable)
              const dataStatus = (data && (data.status || data.quarantine_status)) || null;
              const dataTradable = (data && typeof data.tradable === "boolean") ? data.tradable : null;
              let suppress = null;
              if (dataStatus === "quarantine" || dataTradable === false) {
                suppress = `SUPPRESSED_BY_QUARANTINE (status=${dataStatus}, tradable=${dataTradable})`;
              } else if (prevAgeMs !== null && prevAgeMs > PRICE_SPIKE_MAX_AGE_MS) {
                suppress = `SUPPRESSED_BY_STALE_PREV (age=${Math.round(prevAgeMs/1000)}s > ${Math.round(PRICE_SPIKE_MAX_AGE_MS/1000)}s)`;
              } else if (prevSrc && currSrc && prevSrc !== currSrc) {
                suppress = `SUPPRESSED_BY_SOURCE_CHANGE (${prevSrc} -> ${currSrc})`;
              } else if (consensusLevel === 'hard') {
                suppress = `SUPPRESSED_BY_CONSENSUS_HARD (disagree=${disagree.toFixed(2)}%)`;
              } else if (disagree > PRICE_SPIKE_CONSENSUS_MAX_DISAGREE) {
                suppress = `SUPPRESSED_BY_CONSENSUS_DISAGREE (disagree=${disagree.toFixed(2)}% > ${PRICE_SPIKE_CONSENSUS_MAX_DISAGREE}%)`;
              }
              if (suppress) {
                log("INFO", `PRICE_SPIKE_SUPPRESSED ${symbol}`, {
                  prev: prevPrice, current: data.price,
                  change: `${(changeRatio * 100).toFixed(1)}%`,
                  prev_source: prevSrc, current_source: currSrc,
                  prev_age_ms: prevAgeMs, consensus_level: consensusLevel,
                  disagreement_pct: disagree, reason: suppress
                });
                await safeRedisSet(`metrics:price_spike:suppressed:${symbol}:last`, JSON.stringify({
                  ts: new Date().toISOString(), reason: suppress,
                  change_pct: Number((changeRatio * 100).toFixed(2))
                }), { ex: 3600 }).catch(() => {});
              } else {
                // detection + log are ALWAYS emitted (Jason directive: 탐지·로그 유지)
                log("WARN", `PRICE SPIKE detected for ${symbol}`, {
                  prev: prevPrice, current: data.price,
                  change: `${(changeRatio * 100).toFixed(1)}%`,
                  prev_source: prevSrc, current_source: currSrc,
                  prev_age_ms: prevAgeMs
                });
                // [ALERT-COOLDOWN-20260427] per-symbol 10min Redis TTL cooldown — alert send only
                const cooldownKey = `alert:cooldown:PRICE_SPIKE:${symbol}`;
                let cooldownAcquired = false;
                let cooldownTtl = null;
                try {
                  const setRes = await redis.set(cooldownKey, new Date().toISOString(), "EX", 600, "NX");
                  cooldownAcquired = (setRes === "OK");
                  if (!cooldownAcquired) {
                    cooldownTtl = await redis.ttl(cooldownKey).catch(() => null);
                  }
                } catch (e) {
                  log("WARN", `PRICE_SPIKE_COOLDOWN_REDIS_FAIL ${symbol}`, { error: e.message });
                  cooldownAcquired = true; // fail-open: do NOT silently swallow alerts on Redis failure
                }
                if (cooldownAcquired) {
                  await sendTelegram(
                    `⚡ <b>[PRICE SPIKE] ${symbol}</b>\n` +
                    `$${prevPrice.toFixed(2)} → $${data.price.toFixed(2)} (${(changeRatio * 100).toFixed(1)}%)\n` +
                    `소스: ${currSrc}`
                  );
                } else {
                  log("INFO", `PRICE_SPIKE_SUPPRESSED ${symbol}`, {
                    prev: prevPrice, current: data.price,
                    change: `${(changeRatio * 100).toFixed(1)}%`,
                    prev_source: prevSrc, current_source: currSrc,
                    prev_age_ms: prevAgeMs,
                    reason: "SUPPRESSED_BY_COOLDOWN",
                    cooldown_ttl_remaining_sec: cooldownTtl,
                  });
                  await safeRedisSet(`metrics:price_spike:suppressed:${symbol}:last`, JSON.stringify({
                    ts: new Date().toISOString(),
                    reason: "SUPPRESSED_BY_COOLDOWN",
                    cooldown_ttl_remaining_sec: cooldownTtl,
                    change_pct: Number((changeRatio * 100).toFixed(2)),
                  }), { ex: 3600 }).catch(() => {});
                }
              }
            }
          }
          state.priceCache[symbol] = data.price;
          state.priceCacheMeta[symbol] = { price: data.price, ts: nowMs, source: data.source || "unknown" };

          // === Redis 저장 (Price Write Guard 적용) ===
          const guardResult = await guardedPriceSet(redis, symbol, data.price, { source: data.source || 'consensus_v82', ex: 600, consensus: consensusMeta });
          const written = guardResult.allowed;
          if (written) {
            cycleSuccess.push(symbol);
            state.lastSuccessfulWrite = new Date().toISOString();
          } else {
            cycleMissing.push(symbol);
          }

          // === price:{symbol}:updated_at (go-nogo-judge-v2 의존: freshness 판정) ===
          await safeRedisSet(`price:${symbol}:updated_at`, new Date().toISOString(), { ex: 1200 });

          // === price:{symbol}:source_count (live-trading-kis 의존) ===
          if (data.sources) await safeRedisSet(`price:${symbol}:source_count`, data.sources.toString(), { ex: 600 });
          else if (data.rt_sources) await safeRedisSet(`price:${symbol}:source_count`, data.rt_sources.toString(), { ex: 600 });

          // Signal data (모멘텀, 변동성 등)
          if (data.mom3d) await safeRedisSet(`signal:${symbol}:mom3`, data.mom3d.toString());
          if (data.mom5d) await safeRedisSet(`signal:${symbol}:mom5`, data.mom5d.toString());
          if (data.mom10d) await safeRedisSet(`signal:${symbol}:mom10`, data.mom10d.toString());
          if (data.volatility) await safeRedisSet(`signal:${symbol}:vol`, data.volatility.toString());
          if (data.volumeRatio) await safeRedisSet(`signal:${symbol}:volume_ratio`, data.volumeRatio.toString());

          log("DEBUG", `${symbol}: $${data.price.toFixed(2)} (${data.source || 'unknown'}, conf=${data.confidence?.toFixed(2) || 'N/A'})`);
        } else {
          cycleMissing.push(symbol);
          log("WARN", `No price data for ${symbol}`);
        }
      } catch (e) {
        cycleMissing.push(symbol);
        log("ERROR", `Error fetching ${symbol}`, { error: e.message });
      }
      await sleep(200);
    }

    // === HEARTBEAT KEYS ===
    const nowIso = new Date().toISOString();
    await safeRedisSet('market:last_update', nowIso);
    await safeRedisSet('prices:latest:ts', nowIso, { ex: 600 });
    await safeRedisSet('realtime:feed:heartbeat', nowIso, { ex: 600 });
    await safeRedisSet('ares:feed:heartbeat', String(Date.now()));  // [STRUCTURAL-FIX FEED-001] anchor_drift_guard용

    // === signals:raw:market (v2 호환성 유지) ===
    if (vixValue) {
      await safeRedisSet('signals:raw:market', JSON.stringify({ vix: vixValue, updated: nowIso, source: 'realtime-data-feed-v3' }));
    }

    // === Universe 메타데이터 (v2 호환성 유지) ===
    await safeRedisSet('realtime:feed:universe:count', orderedUniverse.length.toString());
    await safeRedisSet('realtime:feed:universe:core_count', CRITICAL_SYMBOLS.length.toString());
    await safeRedisSet('realtime:feed:universe:ts', nowIso);

    // === TELEMETRY ===
    state.lastFetchCycle = nowIso;
    state.criticalSymbolsMissing = CRITICAL_SYMBOLS.filter(s => cycleMissing.includes(s));

    const cycleDuration = Date.now() - cycleStart;
    log("INFO", "Fetch cycle complete", {
      cycle: state.totalCycles,
      success: cycleSuccess.length,
      missing: cycleMissing.length,
      criticalMissing: state.criticalSymbolsMissing,
      durationMs: cycleDuration,
    });

    // === CRITICAL SYMBOL SELF-HEALING ===
    if (state.criticalSymbolsMissing.length > 0) {
      state.consecutiveFetchFailures++;
      log("ERROR", "CRITICAL SYMBOLS MISSING", { 
        symbols: state.criticalSymbolsMissing, 
        consecutiveFailures: state.consecutiveFetchFailures 
      });

      if (state.consecutiveFetchFailures >= MAX_CONSECUTIVE_FAILURES) {
        sendTelegram(`🔴 <b>realtime-data-feed-v3: Critical symbols missing ${state.consecutiveFetchFailures}x</b>\n누락: ${state.criticalSymbolsMissing.join(', ')}\n자동 재시도 중...`);
      }

      // 즉시 Critical symbols만 재시도 (Yahoo direct)
      for (const sym of state.criticalSymbolsMissing) {
        log("INFO", `Emergency retry for ${sym}`);
        try {
          const yahoo = await fetchYahooQuote(sym);
          if (yahoo && yahoo.price > 0) {
            await safeRedisSet(`price:${sym}`, yahoo.price.toString(), { ex: 600 });
            log("INFO", `Emergency recovery: ${sym} = $${yahoo.price} (yahoo_direct)`);
            state.criticalSymbolsMissing = state.criticalSymbolsMissing.filter(s => s !== sym);
          }
        } catch (e) {
          log("ERROR", `Emergency retry failed for ${sym}`, { error: e.message });
        }
      }
    } else {
      state.consecutiveFetchFailures = 0;
    }

    // === WRITE FEED STATUS TO REDIS ===
    await safeRedisSet('realtime:feed:status', JSON.stringify({
      ts: nowIso,
      version: "v3.0.0",
      cycle: state.totalCycles,
      redisConnected: state.redisConnected,
      criticalMissing: state.criticalSymbolsMissing,
      success: cycleSuccess.length,
      missing: cycleMissing.length,
      consecutiveFailures: state.consecutiveFetchFailures,
    }), { ex: 300 });

  } catch (e) {
    log("ERROR", "Market data update failed", { error: e.message, stack: e.stack?.slice(0, 300) });
    state.consecutiveFetchFailures++;
  }
}

// ─── HEALTH CHECK ───
async function healthCheck() {
  try {
    const pingStart = Date.now();
    await redis.ping();
    const pingMs = Date.now() - pingStart;

    const missing = [];
    for (const sym of CRITICAL_SYMBOLS) {
      const val = await redis.get(`price:${sym}`);
      if (!val) missing.push(sym);
    }

    if (missing.length > 0) {
      log("WARN", "Health check: critical symbols missing in Redis", { missing });
    }

    // v3: 소스 건전성 체크 추가
    let sourceHealth = null;
    if (state.totalCycles % 5 === 0) { // 5사이클마다 소스 건전성 체크
      try {
        sourceHealth = await checkSourceHealth();
      } catch (e) {
        log("WARN", "Source health check failed", { error: e.message });
      }
    }

    const healthPayload = {
      ts: new Date().toISOString(),
      version: "v3.0.0",
      status: missing.length === 0 ? "healthy" : "degraded",
      redisPingMs: pingMs,
      redisConnected: state.redisConnected,
      uptime: process.uptime(),
      totalCycles: state.totalCycles,
      totalReconnects: state.totalRedisReconnects,
      criticalMissing: missing,
      sourceHealth: sourceHealth ? "checked" : "skipped",
      rss: (process.memoryUsage().rss / 1024 / 1024).toFixed(1) + "MB",
    };
    await safeRedisSet('realtime:feed:health', JSON.stringify(healthPayload), { ex: 120 });
    // ROOTFIX-20260430-DYNAMIC-UNIVERSE: keep legacy guard/monitor health keys synchronized with canonical health.
    await safeRedisSet('feed:health_status', missing.length === 0 ? 'ACTIVE' : 'DEGRADED', { ex: 180 });
    await safeRedisSet('ares:feed:health_status', missing.length === 0 ? 'HEALTHY' : 'DEGRADED', { ex: 180 });
    await safeRedisSet('ares:feed:health:status', missing.length === 0 ? 'ACTIVE' : 'DEGRADED', { ex: 180 });
    await safeRedisSet('price_feed:health', JSON.stringify(healthPayload), { ex: 180 });

  } catch (e) {
    log("ERROR", "Health check failed", { error: e.message });
  }
}

// ─── GRACEFUL SHUTDOWN ───
async function shutdown(signal) {
  log("INFO", `Shutdown signal received: ${signal}`);
  try {
    await safeRedisSet('realtime:feed:status', JSON.stringify({
      ts: new Date().toISOString(),
      status: "shutting_down",
      signal,
      version: "v3.0.0",
    }), { ex: 300 });
    await redis.quit();
  } catch (e) {
    log("ERROR", "Shutdown error", { error: e.message });
  }
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

// ─── STARTUP ───
log("INFO", "=== realtime-data-feed v3.0.0 Starting ===", {
  universe: CHAMPION_UNIVERSE.length,
  criticalSymbols: CRITICAL_SYMBOLS,
  fetchInterval: FETCH_INTERVAL_MS,
  healthCheckInterval: HEALTH_CHECK_INTERVAL_MS,
  hasFinnhubKey: !!FINNHUB_API_KEY,
  changes: [
    "data_sources_v82 (realtime/reference separation)",
    "Polygon /prev fallback REMOVED",
    "Price spike detection added",
    "Source health monitoring added"
  ]
});

// Initial fetch
await updateMarketData();

// Periodic fetch (1분)
setInterval(updateMarketData, FETCH_INTERVAL_MS);

// Periodic health check (30초)
setInterval(healthCheck, HEALTH_CHECK_INTERVAL_MS);

log("INFO", "Real-time data feed v3 started. Press Ctrl+C to stop.");
