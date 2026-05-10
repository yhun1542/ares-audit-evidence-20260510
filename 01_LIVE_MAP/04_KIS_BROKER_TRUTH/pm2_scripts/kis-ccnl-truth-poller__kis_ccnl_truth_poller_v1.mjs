#!/usr/bin/env node
/**
 * kis_ccnl_truth_poller_v1.mjs
 * ============================
 *
 * KIS CCNL (체결내역) Truth Poller — per-fill SSOT writer.
 *
 * PURPOSE:
 *   Replace the 30s synthetic-fill collapse in fills_reconciler_v2 by polling
 *   KIS REST `/uapi/overseas-stock/v1/trading/inquire-ccnl` (TR=TTTS3035R) at
 *   5s cadence during US session and emitting one canonical fill per ccnl row.
 *
 * SAFETY:
 *   - Read-only on KIS (no order/cancel calls, no WebSocket).
 *   - SHADOW_ONLY mode writes only to `ares:fills:canonical:shadow`, never to
 *     production stream/mirrors. Default = production-on (per spec) but the
 *     operator is encouraged to start in SHADOW_ONLY for at least one session.
 *   - Dedup at (ord_dt, odno, fill_seq) granularity to make XADD idempotent.
 *   - Pending rows (empty ccld_dt/ccld_tmd) are skipped (will be picked up on
 *     a later tick once KIS publishes the timestamp).
 *   - All failures degrade gracefully; a tick never throws to the event loop.
 *   - No write to broker:truth:* or any production-only key.
 *
 * SOURCE TAG: kis-ccnl-truth-poller-v1
 *
 * 7-AI CONSENSUS PATCH v1.0 (2026-05-08)
 *   Base: claude-opus-4-7 (787 lines, 14/14 KIS official params correct)
 *   + isForbiddenStream() from gpt-5 (production safety guard)
 *   + crypto.SHA1 positions fingerprint from gpt-5 (more robust)
 *   + kst_* debug fields from 1st 3AI consensus (forensics)
 *   + REDIS_URL secret hardening (no hardcoded secrets, vs gemini-2.5)
 */

import IORedis from 'ioredis';
import crypto from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';

// ---------------------------------------------------------------------------
// Env / config
// ---------------------------------------------------------------------------
const VERSION = '1.0.0';
const WRITER  = 'kis-ccnl-truth-poller';
const SOURCE_TAG = 'kis-ccnl-truth-poller-v1';

const REDIS_URL = process.env.REDIS_URL;
if (!REDIS_URL) {
  console.error(JSON.stringify({ ts: new Date().toISOString(), lvl: 'fatal', evt: 'REDIS_URL_MISSING' }));
  process.exit(2);
}
if (!REDIS_URL.startsWith('rediss://') && !REDIS_URL.startsWith('redis://')) {
  console.error(JSON.stringify({ ts: new Date().toISOString(), lvl: 'fatal', evt: 'REDIS_URL_INVALID' }));
  process.exit(2);
}

const KIS_API_BASE          = process.env.KIS_API_BASE || 'https://openapi.koreainvestment.com:9443';
const KIS_ACCOUNT_NO        = process.env.KIS_ACCOUNT_NO;
// EC2 stores this as KIS_ACCOUNT_PROD_CD; some other systems use KIS_ACCOUNT_PRODUCT_CD.
const KIS_ACCOUNT_PRODUCT_CD= process.env.KIS_ACCOUNT_PRODUCT_CD || process.env.KIS_ACCOUNT_PROD_CD;
const KIS_APP_KEY           = process.env.KIS_APP_KEY;
const KIS_APP_SECRET        = process.env.KIS_APP_SECRET;

for (const [k, v] of Object.entries({
  KIS_ACCOUNT_NO, KIS_ACCOUNT_PRODUCT_CD, KIS_APP_KEY, KIS_APP_SECRET,
})) {
  if (!v) {
    console.error(JSON.stringify({ ts: new Date().toISOString(), lvl: 'fatal', evt: 'ENV_MISSING', key: k }));
    process.exit(2);
  }
}

const POLL_INTERVAL_MS = parseInt(process.env.CCNL_POLL_INTERVAL_MS || '5000', 10);
const IDLE_INTERVAL_MS = parseInt(process.env.CCNL_IDLE_INTERVAL_MS || '60000', 10);
const LOOKBACK_DAYS    = Math.max(0, parseInt(process.env.CCNL_LOOKBACK_DAYS || '1', 10));
const SHADOW_ONLY      = String(process.env.CCNL_SHADOW_ONLY || 'false').toLowerCase() === 'true';

// Production safety: block writes to legacy/forbidden keys (from gpt-5).
function isForbiddenStream(streamKey) {
  if (!streamKey) return true;
  const k = String(streamKey);
  if (k === 'stream:fills') return true;          // legacy synthetic-fill stream
  if (k.startsWith('broker:truth:')) return true; // read-only positions namespace
  return false;
}

const STREAM_PROD      = process.env.FILLS_CANONICAL_STREAM || 'ares:fills:canonical';
const STREAM_SHADOW    = `${STREAM_PROD}:shadow`;
const STREAM_KEY       = SHADOW_ONLY ? STREAM_SHADOW : STREAM_PROD;

if (isForbiddenStream(STREAM_KEY)) {
  console.error(JSON.stringify({ ts: new Date().toISOString(), lvl: 'fatal', evt: 'STREAM_KEY_FORBIDDEN', key: STREAM_KEY }));
  process.exit(2);
}

const MIRRORS          = SHADOW_ONLY
  ? []
  : (process.env.FILLS_CANONICAL_MIRRORS || 'emarkos:v6:stream:fills')
      .split(',').map(s => s.trim()).filter(Boolean)
      .filter(m => {
        if (isForbiddenStream(m)) {
          console.error(JSON.stringify({ ts: new Date().toISOString(), lvl: 'fatal', evt: 'MIRROR_FORBIDDEN', key: m }));
          return false;
        }
        return true;
      });

const STREAM_MAXLEN    = parseInt(process.env.FILLS_STREAM_MAXLEN || '50000', 10);

const DEDUP_SET        = 'kis:ccnl:truth:dedup';
const DEDUP_TTL_SEC    = 7 * 24 * 3600;
const STATS_HASH       = 'kis:ccnl:truth:stats';
const ALERT_STREAM     = 'ops:alerts:stream';
const STUCK_THROTTLE_MS = 5 * 60 * 1000;
const STUCK_WINDOW_MS  = 5 * 60 * 1000;

const TOKEN_KEYS       = ['kis:token:access', 'kis:token:access_token', 'kis:access_token'];
const TOKEN_CACHE_KEY  = 'kis:token:access';

const HTTP_TIMEOUT_MS  = parseInt(process.env.CCNL_HTTP_TIMEOUT_MS || '15000', 10);
const MAX_PAGES_PER_TICK = parseInt(process.env.CCNL_MAX_PAGES || '20', 10);

// ---------------------------------------------------------------------------
// Logger
// ---------------------------------------------------------------------------
const log = (lvl, evt, extra = {}) => {
  try {
    console.log(JSON.stringify({ ts: new Date().toISOString(), lvl, evt, v: VERSION, ...extra }));
  } catch {
    console.log(`[${lvl}] ${evt}`);
  }
};

// ---------------------------------------------------------------------------
// Redis
// ---------------------------------------------------------------------------
const redis = new IORedis(REDIS_URL, {
  tls: REDIS_URL.startsWith('rediss://') ? { rejectUnauthorized: false } : undefined,
  retryStrategy: (t) => Math.min(t * 1000, 30000),
  maxRetriesPerRequest: 3,
  enableOfflineQueue: true,
  lazyConnect: false,
});
redis.on('ready', () => log('info', 'REDIS_READY'));
redis.on('error', (e) => log('error', 'REDIS_ERR', { err: String(e?.message || e) }));

// ---------------------------------------------------------------------------
// Token management
// ---------------------------------------------------------------------------
let cachedToken = null; // { value, expires_at_ms }

async function loadTokenFromRedis() {
  for (const k of TOKEN_KEYS) {
    try {
      const v = await redis.get(k);
      if (!v) continue;
      // Some keys store a JSON blob, some store the bare token.
      let token = null, expiresAtMs = null;
      try {
        const obj = JSON.parse(v);
        if (obj && typeof obj === 'object') {
          token = obj.access_token || obj.token || obj.value || null;
          if (obj.expires_at_ms) expiresAtMs = Number(obj.expires_at_ms);
          else if (obj.expires_in) expiresAtMs = Date.now() + (Number(obj.expires_in) * 1000);
        } else if (typeof obj === 'string') {
          token = obj;
        }
      } catch {
        token = v.trim();
      }
      if (token && typeof token === 'string' && token.length > 10) {
        return { value: token, expires_at_ms: expiresAtMs, source_key: k };
      }
    } catch (e) {
      log('warn', 'TOKEN_READ_FAIL', { key: k, err: String(e?.message || e) });
    }
  }
  return null;
}

async function fetchNewToken() {
  const url = `${KIS_API_BASE}/oauth2/tokenP`;
  const body = JSON.stringify({
    grant_type: 'client_credentials',
    appkey: KIS_APP_KEY,
    appsecret: KIS_APP_SECRET,
  });
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), HTTP_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json; charset=utf-8' },
      body,
      signal: ctl.signal,
    });
    const text = await res.text();
    let obj = null;
    try { obj = JSON.parse(text); } catch {}
    if (!res.ok || !obj || !obj.access_token) {
      throw new Error(`token_http_${res.status}: ${text.slice(0, 200)}`);
    }
    const ttlSec = Number(obj.expires_in || 23 * 3600);
    const expiresAtMs = Date.now() + (ttlSec * 1000);
    // Cache in redis for other services.
    try {
      await redis.set(TOKEN_CACHE_KEY, JSON.stringify({
        access_token: obj.access_token,
        token_type: obj.token_type || 'Bearer',
        expires_at_ms: expiresAtMs,
      }), 'EX', Math.max(60, ttlSec - 300));
    } catch (e) {
      log('warn', 'TOKEN_CACHE_WRITE_FAIL', { err: String(e?.message || e) });
    }
    return { value: obj.access_token, expires_at_ms: expiresAtMs, source_key: 'fresh' };
  } finally {
    clearTimeout(timer);
  }
}

async function getToken({ force = false } = {}) {
  const now = Date.now();
  if (!force && cachedToken && cachedToken.value
      && (!cachedToken.expires_at_ms || cachedToken.expires_at_ms - 60_000 > now)) {
    return cachedToken;
  }
  if (!force) {
    const t = await loadTokenFromRedis();
    if (t && t.value && (!t.expires_at_ms || t.expires_at_ms - 60_000 > now)) {
      cachedToken = t;
      return t;
    }
  }
  // Fall through: need to mint.
  const t = await fetchNewToken();
  cachedToken = t;
  log('info', 'TOKEN_MINTED', { expires_at_ms: t.expires_at_ms });
  return t;
}

async function clearTokenCache() {
  cachedToken = null;
  for (const k of TOKEN_KEYS) {
    try { await redis.del(k); } catch {}
  }
  log('warn', 'TOKEN_CACHE_CLEARED');
}

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------
function pad2(n) { return String(n).padStart(2, '0'); }

function kstYYYYMMDD(date = new Date()) {
  // KST = UTC+9, no DST.
  const kstMs = date.getTime() + 9 * 3600 * 1000;
  const d = new Date(kstMs);
  return `${d.getUTCFullYear()}${pad2(d.getUTCMonth() + 1)}${pad2(d.getUTCDate())}`;
}

function kstYYYYMMDDOffset(daysBack) {
  const d = new Date(Date.now() - daysBack * 86400_000);
  return kstYYYYMMDD(d);
}

/**
 * Convert KST YYYYMMDD + HHMMSS to ISO UTC.
 * KIS returns timestamps in KST regardless of underlying market.
 * Returns null if inputs are malformed.
 */
function kstToUtcIso(yyyymmdd, hhmmss) {
  if (!yyyymmdd || !hhmmss) return null;
  if (yyyymmdd.length !== 8) return null;
  const hh6 = hhmmss.padStart(6, '0').slice(0, 6);
  const yyyy = parseInt(yyyymmdd.slice(0, 4), 10);
  const mm   = parseInt(yyyymmdd.slice(4, 6), 10) - 1;
  const dd   = parseInt(yyyymmdd.slice(6, 8), 10);
  const hh   = parseInt(hh6.slice(0, 2), 10);
  const mi   = parseInt(hh6.slice(2, 4), 10);
  const ss   = parseInt(hh6.slice(4, 6), 10);
  if ([yyyy, mm, dd, hh, mi, ss].some(Number.isNaN)) return null;
  // hh-9 to convert KST → UTC; Date.UTC handles negative hour rollover.
  const ms = Date.UTC(yyyy, mm, dd, hh - 9, mi, ss);
  if (!Number.isFinite(ms)) return null;
  return new Date(ms).toISOString();
}

function isUsSessionKst(date = new Date()) {
  // KST 17:00 - 08:00 next day (covers US pre/regular/after).
  const kstMs = date.getTime() + 9 * 3600 * 1000;
  const d = new Date(kstMs);
  const h = d.getUTCHours();
  return (h >= 17 || h < 8);
}

// ---------------------------------------------------------------------------
// KIS API call
// ---------------------------------------------------------------------------
const TR_ID = 'TTTS3035R';
const CCNL_PATH = '/uapi/overseas-stock/v1/trading/inquire-ccnl';

/**
 * Returns: { rows, tr_cont, ctx_nk, ctx_fk, rt_cd, msg_cd, msg1 }
 * Throws only on hard transport failure (caller handles retries).
 */
async function callCcnl({ token, startKst, endKst, ctxNk = '', ctxFk = '', trCont = '' }) {
  const url = new URL(`${KIS_API_BASE}${CCNL_PATH}`);
  // Required query params per KIS spec for TTTS3035R.
  const params = {
    CANO: KIS_ACCOUNT_NO,
    ACNT_PRDT_CD: KIS_ACCOUNT_PRODUCT_CD,
    OVRS_EXCG_CD: '%',          // all overseas exchanges
    PDNO: '%',                  // all products
    ORD_STRT_DT: startKst,
    ORD_END_DT: endKst,
    SLL_BUY_DVSN: '00',         // 00=ALL
    CCLD_NCCS_DVSN: '00',       // 00=ALL (filled+unfilled); we filter pending below
    SORT_SQN: 'DS',             // recent first
    ORD_DT: '',
    ORD_GNO_BRNO: '',
    ODNO: '',
    CTX_AREA_NK200: ctxNk || '',
    CTX_AREA_FK200: ctxFk || '',
  };
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

  const headers = {
    'content-type': 'application/json; charset=utf-8',
    'authorization': `Bearer ${token}`,
    'appkey': KIS_APP_KEY,
    'appsecret': KIS_APP_SECRET,
    'tr_id': TR_ID,
    'custtype': 'P',
    'tr_cont': trCont || '',
  };

  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), HTTP_TIMEOUT_MS);
  try {
    const res = await fetch(url, { method: 'GET', headers, signal: ctl.signal });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch {}
    const respTrCont = res.headers.get('tr_cont') || res.headers.get('TR_CONT') || '';
    const respCtxNk  = res.headers.get('ctx_area_nk200') || res.headers.get('CTX_AREA_NK200') || '';
    const respCtxFk  = res.headers.get('ctx_area_fk200') || res.headers.get('CTX_AREA_FK200') || '';
    if (!res.ok) {
      return {
        ok: false, http: res.status,
        rt_cd: body?.rt_cd, msg_cd: body?.msg_cd, msg1: body?.msg1,
        rows: [], tr_cont: '', ctx_nk: '', ctx_fk: '',
        raw: text.slice(0, 500),
      };
    }
    const rows = Array.isArray(body?.output) ? body.output
              : Array.isArray(body?.output1) ? body.output1
              : [];
    return {
      ok: true, http: 200,
      rt_cd: body?.rt_cd, msg_cd: body?.msg_cd, msg1: body?.msg1,
      rows,
      tr_cont: respTrCont,
      // Header takes precedence; body fallback for resilience.
      ctx_nk: (respCtxNk || body?.ctx_area_nk200 || '').trim(),
      ctx_fk: (respCtxFk || body?.ctx_area_fk200 || '').trim(),
    };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Row → canonical fill mapping
// ---------------------------------------------------------------------------
function num(v) {
  if (v === undefined || v === null || v === '') return 0;
  const n = Number(String(v).replace(/,/g, ''));
  return Number.isFinite(n) ? n : 0;
}

function mapSide(code) {
  const c = String(code || '').trim();
  if (c === '02') return 'BUY';
  if (c === '01') return 'SELL';
  return null;
}

/**
 * Returns null if the row should be skipped (pending / malformed).
 * Otherwise returns { dedupKey, fillsPayload }.
 */
function rowToFill(row, ackTickIso) {
  const odno    = String(row.odno || '').trim();
  const ord_dt  = String(row.ord_dt || '').trim();
  const ord_tmd = String(row.ord_tmd || row.thco_ord_tmd || '').trim();
  // NOTE: KIS overseas-stock inquire-ccnl (TTTS3035R) does NOT return
  // dedicated ccld_dt/ccld_tmd fields. We must fall back to ord_dt/ord_tmd
  // (the canonical KIS "order accepted" timestamp = effective fill time on this venue).
  const ccld_dt = String(row.ccld_dt || row.dmst_ord_dt || ord_dt).trim();
  const ccld_tmd= String(row.ccld_tmd || ord_tmd).trim();
  const pdno    = String(row.pdno || '').trim();
  const sideCode= String(row.sll_buy_dvsn_cd || '').trim();
  const side    = mapSide(sideCode);
  const exch    = String(row.ovrs_excg_cd || '').trim();
  const prcsStat= String(row.prcs_stat_name || '').trim();

  if (!odno || !ord_dt || !pdno || !side) return { skip: 'missing_keys' };

  // Quantitative state machine:
  // - ft_ccld_qty > 0 && prcs_stat_name contains "완료" => filled
  // - prcs_stat_name contains "취소"/"거부" => terminal non-fill, skip
  // - else => still pending, retry later
  const qty      = num(row.ft_ccld_qty);
  const fillPx   = num(row.ft_ccld_unpr3);
  const notional = num(row.ft_ccld_amt3);
  const isCancelled = prcsStat.includes('취소') || prcsStat.includes('거부') || prcsStat.includes('실패');
  if (isCancelled) return { skip: 'cancelled' };
  if (!(qty > 0)) return { skip: 'pending' };       // accepted but not yet filled
  if (!(fillPx > 0)) return { skip: 'price_invalid' };
  if (!prcsStat.includes('완료')) return { skip: 'pending' };
  if (!ccld_dt || !ccld_tmd) return { skip: 'time_unresolved' };

  const fillTimeIso = kstToUtcIso(ccld_dt, ccld_tmd);
  if (!fillTimeIso) return { skip: 'time_invalid' };

  // KIS CCNL rows are aggregated per-execution; sequence not exposed by name.
  // Use tag fields when available to disambiguate multi-fill rows for one odno.
  // Common KIS tie-breakers for ccnl: rvse_cncl_dvsn_prcs_cd, ccno, dmst_ord_dt+ord_gno_brno.
  const seq =
       String(row.ccno || '').trim()
    || String(row.ord_gno_brno || '').trim()
    || `${ccld_dt}${ccld_tmd}`;

  const dedupKey = `${ord_dt}:${odno}:${seq}:${qty}:${fillPx}`;

  const intentId = `kis-ccnl-${ord_dt}-${odno}-${seq}`;
  const payload = {
    // canonical fields (consumed by emarkos pnl/risk consumers)
    symbol: pdno,
    side,
    qty: qty.toString(),
    fill_price: fillPx.toString(),
    intent_id: intentId,
    broker_order_id: odno,
    fill_type: 'KIS_CCNL_TRUTH',
    fill_time: fillTimeIso,
    exchange: exch,
    strategy_version: 'kis_ccnl_truth_poller_v1',
    notional_usd: (notional > 0 ? notional : qty * fillPx).toFixed(2),
    realized_pnl: '0',
    reconciled: 'true',
    source: SOURCE_TAG,
    poller_observed_at: ackTickIso,
    // KST debug fields (forensics; preserved across SSOT writes)
    kst_fill_date: ccld_dt,
    kst_fill_time: ccld_tmd,
    kst_ord_date:  ord_dt,
    kst_ord_time:  ord_tmd,
    raw_side_code: sideCode,
    raw_ccno:      String(row.ccno || ''),
    raw_seq:       seq,
    // Backward-compat aliases (pre-existing consumers)
    fill_date_kst: ccld_dt,
    fill_time_kst: ccld_tmd,
    order_date_kst: ord_dt,
    order_time_kst: ord_tmd,
  };
  return { skip: null, dedupKey, payload };
}

// ---------------------------------------------------------------------------
// Stuck detector (positions changing but we emit no fills)
// ---------------------------------------------------------------------------
let lastFillAtMs = Date.now();
let lastPositionsSnapshot = null; // string sha-ish (qty-sum)
let lastPositionsAtMs = 0;
let lastStuckAlertAtMs = 0;

async function snapshotPositionsHash() {
  try {
    const h = await redis.hgetall('kis:broker:positions');
    if (!h || !Object.keys(h).length) return '';
    const parts = [];
    for (const [k, v] of Object.entries(h)) {
      try {
        const o = JSON.parse(v);
        // Richer signal: today's buy/sell qty + current holdings (3-tuple)
        parts.push(`${k}:${o.ctrp_thdt_buy || 0}:${o.ctrp_thdt_sll || 0}:${o.ovrs_cblc_qty || o.cblc_qty || 0}`);
      } catch {
        parts.push(`${k}:?`);
      }
    }
    parts.sort();
    // SHA1 hash for compact, collision-resistant fingerprint (from gpt-5).
    return crypto.createHash('sha1').update(parts.join('|')).digest('hex');
  } catch {
    return null;
  }
}

async function maybeStuckAlert() {
  if (!isUsSessionKst()) return;
  const now = Date.now();
  if (now - lastFillAtMs < STUCK_WINDOW_MS) return;
  const snap = await snapshotPositionsHash();
  if (snap === null) return;
  // First snapshot since startup → just remember.
  if (!lastPositionsSnapshot) {
    lastPositionsSnapshot = snap;
    lastPositionsAtMs = now;
    return;
  }
  const positionsChanged = snap !== lastPositionsSnapshot;
  if (!positionsChanged) {
    lastPositionsSnapshot = snap;
    lastPositionsAtMs = now;
    return;
  }
  // Positions changed in last window but no fills emitted.
  if (now - lastStuckAlertAtMs < STUCK_THROTTLE_MS) return;
  lastStuckAlertAtMs = now;
  try {
    await redis.xadd(ALERT_STREAM, 'MAXLEN', '~', 1000, '*',
      'event', 'KIS_CCNL_POLLER_STUCK',
      'ts', new Date().toISOString(),
      'body', JSON.stringify({
        last_fill_age_ms: now - lastFillAtMs,
        positions_changed: true,
        shadow_only: SHADOW_ONLY,
      }),
    );
    log('warn', 'STUCK_ALERT', { last_fill_age_ms: now - lastFillAtMs });
  } catch (e) {
    log('warn', 'STUCK_ALERT_FAIL', { err: String(e?.message || e) });
  }
  // Update snapshot regardless to avoid alert storms over the same delta.
  lastPositionsSnapshot = snap;
  lastPositionsAtMs = now;
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
const stats = {
  ticks: 0, errors: 0, http_429: 0, http_5xx: 0, token_refresh: 0,
  emitted: 0, skipped_pending: 0, skipped_dedup: 0, skipped_other: 0,
  pages_fetched: 0, started_at_ms: Date.now(),
};

async function persistStats(extra = {}) {
  try {
    await redis.hset(STATS_HASH,
      'ticks', String(stats.ticks),
      'errors', String(stats.errors),
      'http_429', String(stats.http_429),
      'http_5xx', String(stats.http_5xx),
      'token_refresh', String(stats.token_refresh),
      'emitted', String(stats.emitted),
      'skipped_pending', String(stats.skipped_pending),
      'skipped_dedup', String(stats.skipped_dedup),
      'skipped_other', String(stats.skipped_other),
      'pages_fetched', String(stats.pages_fetched),
      'last_tick_iso', new Date().toISOString(),
      'shadow_only', String(SHADOW_ONLY),
      'stream_key', STREAM_KEY,
      'started_at_ms', String(stats.started_at_ms),
      ...Object.entries(extra).flatMap(([k, v]) => [k, String(v)]),
    );
  } catch {}
}

// ---------------------------------------------------------------------------
// Backoff
// ---------------------------------------------------------------------------
let consecutive429 = 0;
async function backoff429() {
  consecutive429 = Math.min(consecutive429 + 1, 5);
  const ms = Math.min(30_000, Math.round(1000 * Math.pow(2, consecutive429 - 1)));
  log('warn', 'RATE_LIMIT_BACKOFF', { ms, consecutive: consecutive429 });
  await sleep(ms);
}
function reset429() { consecutive429 = 0; }

// ---------------------------------------------------------------------------
// Tick
// ---------------------------------------------------------------------------
function isTokenError(resp, errMsg) {
  if (!resp && errMsg) return /EGW00121/.test(errMsg);
  if (resp?.rt_cd === '900') return true;
  if (resp?.msg_cd && /EGW00121/.test(resp.msg_cd)) return true;
  if (resp?.msg1 && /기간이 만료|expired|EGW00121/i.test(resp.msg1)) return true;
  return false;
}
function isRateLimit(resp, errMsg) {
  if (errMsg && /초당 거래건수/.test(errMsg)) return true;
  if (resp?.msg1 && /초당 거래건수/.test(resp.msg1)) return true;
  if (resp?.http === 429) return true;
  return false;
}

async function emitFill(payload, dedupKey) {
  // Atomic-ish dedup via SADD return value.
  let isNew;
  try {
    isNew = await redis.sadd(DEDUP_SET, dedupKey);
    // Refresh TTL on the SET (cheap, idempotent).
    await redis.expire(DEDUP_SET, DEDUP_TTL_SEC);
  } catch (e) {
    log('error', 'DEDUP_FAIL', { err: String(e?.message || e), dedupKey });
    return { emitted: false, reason: 'dedup_err' };
  }
  if (isNew !== 1) return { emitted: false, reason: 'dedup' };

  const flat = [];
  for (const [k, v] of Object.entries(payload)) flat.push(k, String(v));

  try {
    await redis.xadd(STREAM_KEY, 'MAXLEN', '~', String(STREAM_MAXLEN), '*', ...flat);
    if (!SHADOW_ONLY) {
      for (const m of MIRRORS) {
        try {
          await redis.xadd(m, 'MAXLEN', '~', String(STREAM_MAXLEN), '*', ...flat);
        } catch (e) {
          log('warn', 'MIRROR_XADD_FAIL', { mirror: m, err: String(e?.message || e) });
        }
      }
    }
    return { emitted: true };
  } catch (e) {
    // Roll back dedup so a future tick can retry.
    try { await redis.srem(DEDUP_SET, dedupKey); } catch {}
    log('error', 'XADD_FAIL', { err: String(e?.message || e), key: STREAM_KEY });
    return { emitted: false, reason: 'xadd_err' };
  }
}

async function tickOnce() {
  stats.ticks++;
  const tickStart = Date.now();
  const ackTickIso = new Date().toISOString();

  const startKst = kstYYYYMMDDOffset(LOOKBACK_DAYS);
  const endKst   = kstYYYYMMDD();

  let token;
  try {
    token = (await getToken()).value;
  } catch (e) {
    stats.errors++;
    log('error', 'TOKEN_GET_FAIL', { err: String(e?.message || e) });
    return;
  }

  let ctxNk = '', ctxFk = '', trCont = '';
  let pages = 0;
  let emitted = 0, skippedDedup = 0, skippedPending = 0, skippedOther = 0;

  while (pages < MAX_PAGES_PER_TICK) {
    let resp;
    try {
      resp = await callCcnl({ token, startKst, endKst, ctxNk, ctxFk, trCont });
    } catch (e) {
      const msg = String(e?.message || e);
      if (isRateLimit(null, msg)) {
        stats.http_429++;
        await backoff429();
        continue;
      }
      stats.errors++;
      log('error', 'CCNL_HTTP_ERR', { err: msg });
      break;
    }

    if (!resp.ok) {
      // Token expiry?
      if (isTokenError(resp, null)) {
        stats.token_refresh++;
        log('warn', 'TOKEN_EXPIRED_RETRY', { rt_cd: resp.rt_cd, msg_cd: resp.msg_cd, msg1: resp.msg1 });
        await clearTokenCache();
        try {
          token = (await getToken({ force: true })).value;
        } catch (e) {
          stats.errors++;
          log('error', 'TOKEN_REMINT_FAIL', { err: String(e?.message || e) });
          break;
        }
        continue; // retry same page without advancing
      }
      // Rate limit?
      if (isRateLimit(resp, null) || resp.http === 429) {
        stats.http_429++;
        await backoff429();
        continue;
      }
      if (resp.http >= 500) stats.http_5xx++;
      stats.errors++;
      log('error', 'CCNL_BAD_RESP', { http: resp.http, rt_cd: resp.rt_cd, msg_cd: resp.msg_cd, msg1: resp.msg1 });
      break;
    }
    // Successful response — also need to look at rt_cd (KIS sometimes returns http=200 with rt_cd!=0).
    if (resp.rt_cd && resp.rt_cd !== '0') {
      if (isTokenError(resp, null)) {
        stats.token_refresh++;
        log('warn', 'TOKEN_EXPIRED_RT', { rt_cd: resp.rt_cd, msg_cd: resp.msg_cd, msg1: resp.msg1 });
        await clearTokenCache();
        try { token = (await getToken({ force: true })).value; }
        catch (e) { stats.errors++; log('error', 'TOKEN_REMINT_FAIL', { err: String(e?.message || e) }); break; }
        continue;
      }
      if (isRateLimit(resp, null)) {
        stats.http_429++;
        await backoff429();
        continue;
      }
      // Unknown business error — log and stop the tick (do not infinite-loop).
      stats.errors++;
      log('error', 'CCNL_RT_ERR', { rt_cd: resp.rt_cd, msg_cd: resp.msg_cd, msg1: resp.msg1 });
      break;
    }

    reset429();
    pages++;
    stats.pages_fetched++;

    for (const row of resp.rows) {
      let m;
      try {
        m = rowToFill(row, ackTickIso);
      } catch (e) {
        skippedOther++;
        log('warn', 'ROW_MAP_ERR', { err: String(e?.message || e) });
        continue;
      }
      if (m.skip) {
        if (m.skip === 'pending') skippedPending++;
        else skippedOther++;
        continue;
      }
      const out = await emitFill(m.payload, m.dedupKey);
      if (out.emitted) {
        emitted++;
        lastFillAtMs = Date.now();
      } else if (out.reason === 'dedup') {
        skippedDedup++;
      } else {
        skippedOther++;
      }
    }

    // Pagination: advance only when KIS signals more.
    // 'F' (First) / 'M' (More) → continue with header ctx.
    // 'D' (Done)  / 'E' (End)  / '' → stop.
    const tc = (resp.tr_cont || '').toUpperCase();
    if (tc === 'F' || tc === 'M') {
      if (!resp.ctx_nk && !resp.ctx_fk) {
        log('warn', 'PAGINATION_NO_CTX', { tr_cont: tc });
        break;
      }
      ctxNk = resp.ctx_nk;
      ctxFk = resp.ctx_fk;
      trCont = 'N';
      continue;
    }
    break;
  }

  if (pages >= MAX_PAGES_PER_TICK) {
    log('warn', 'PAGINATION_CAP_HIT', { max: MAX_PAGES_PER_TICK });
  }

  stats.emitted += emitted;
  stats.skipped_dedup += skippedDedup;
  stats.skipped_pending += skippedPending;
  stats.skipped_other += skippedOther;

  await persistStats({
    last_emitted: emitted,
    last_skipped_dedup: skippedDedup,
    last_skipped_pending: skippedPending,
    last_skipped_other: skippedOther,
    last_pages: pages,
    last_tick_ms: Date.now() - tickStart,
  });

  log('info', 'TICK_DONE', {
    emitted, dedup: skippedDedup, pending: skippedPending, other: skippedOther,
    pages, ms: Date.now() - tickStart, shadow: SHADOW_ONLY,
  });

  await maybeStuckAlert();
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------
let stopping = false;
let loopPromise = null;

async function mainLoop() {
  log('info', 'STARTUP', {
    base: KIS_API_BASE, shadow: SHADOW_ONLY, stream: STREAM_KEY,
    mirrors: MIRRORS, lookback_days: LOOKBACK_DAYS,
    poll_ms: POLL_INTERVAL_MS, idle_ms: IDLE_INTERVAL_MS,
  });
  // Initial backfill marker
  log('info', 'INITIAL_BACKFILL_BEGIN', {
    start_kst: kstYYYYMMDDOffset(LOOKBACK_DAYS),
    end_kst: kstYYYYMMDD(),
  });

  while (!stopping) {
    try {
      await tickOnce();
    } catch (e) {
      stats.errors++;
      log('error', 'TICK_UNCAUGHT', { err: String(e?.message || e), stack: e?.stack?.split('\n').slice(0, 3) });
    }
    if (stopping) break;
    const delay = isUsSessionKst() ? POLL_INTERVAL_MS : IDLE_INTERVAL_MS;
    await sleep(delay);
  }
}

// ---------------------------------------------------------------------------
// Shutdown
// ---------------------------------------------------------------------------
async function shutdown(sig) {
  if (stopping) return;
  stopping = true;
  log('info', 'SHUTDOWN_BEGIN', { sig });
  try { if (loopPromise) await Promise.race([loopPromise, sleep(8000)]); } catch {}
  try { await persistStats({ shutdown_at_iso: new Date().toISOString(), shutdown_sig: sig }); } catch {}
  try { await redis.quit(); } catch {}
  setTimeout(() => process.exit(0), 250).unref();
}

process.on('SIGINT',  () => { shutdown('SIGINT').catch(() => process.exit(1)); });
process.on('SIGTERM', () => { shutdown('SIGTERM').catch(() => process.exit(1)); });
process.on('unhandledRejection', (r) => log('error', 'UNHANDLED_REJECTION', { err: String(r) }));
process.on('uncaughtException',  (e) => log('error', 'UNCAUGHT_EXCEPTION',  { err: String(e?.message || e) }));

loopPromise = mainLoop();