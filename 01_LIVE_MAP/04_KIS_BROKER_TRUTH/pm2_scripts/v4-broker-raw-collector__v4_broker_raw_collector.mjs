import { createWriteGuard } from './v4_write_guard.mjs';
/**
 * v4_broker_raw_collector.mjs — v4 Shadow Broker Raw Collector
 * =============================================================
 * ARES Resilience Pack v4 — EC2 적합화 Shadow Mode
 *
 * 설계 원칙:
 *   - 기존 kis-balance-sync.mjs를 절대 건드리지 않는다.
 *   - 기존 kis:raw_balance:* 키를 읽어서 v4 shadow 키(broker:raw:*)로 변환 저장한다.
 *   - 기존 truth:* 키를 절대 덮어쓰지 않는다.
 *   - KIS API를 직접 호출하지 않는다 (기존 collector가 이미 수집 중).
 *
 * 데이터 흐름:
 *   kis:raw_balance:NASD:latest  ─┐
 *   kis:raw_balance:NYSE:latest  ─┤→ 읽기 → 통합 → broker:raw:latest (shadow)
 *   kis:raw_balance:AMEX:latest  ─┤                  broker:raw:NASD:latest
 *   kis:raw_balance:CTRP6504R:latest ─┘               broker:raw:NYSE:latest
 *                                                     broker:raw:AMEX:latest
 *                                                     broker:raw:CTRP6504R:latest
 *                                                     broker:raw:ts
 *
 * [FIX 2026-04-17] v1.1.0-ec2:
 *   - Bug Fix: 교차거래소 중복 제거 (symbol-based dedup)
 *     KIS JTTT3012R API가 NYSE 종목을 NASD 조회에도 반환하여
 *     동일 symbol이 2중 계산되는 문제 수정
 *   - 중복 발견 시 실제 상장 거래소(NYSE) 데이터를 우선 사용
 *
 * 환경변수:
 *   V4_COLLECTOR_INTERVAL_MS  — 수집 주기 (기본: 30000ms)
 *   V4_SHADOW_TTL_SEC         — shadow 키 TTL (기본: 3600s)
 *
 * @version 1.1.0-ec2
 * @date 2026-04-17
 */

import { getRedis, log as connLog } from '../lib/redis-connection.mjs';
import { safeSetJson, safeSet, TTL } from '../lib/ares-redis-safe.mjs';
import { log } from '../lib/structured_logger.mjs';
import crypto from 'node:crypto';

// ─── Configuration ───────────────────────────────────────────────
const COMPONENT = 'v4-broker-raw-collector';
const INTERVAL_MS = parseInt(process.env.V4_COLLECTOR_INTERVAL_MS || '30000', 10);
const SHADOW_TTL = parseInt(process.env.V4_SHADOW_TTL_SEC || '3600', 10);

// Source keys (v3 — read only)
const SRC_KEYS = {
  NASD:      'kis:raw_balance:NASD:latest',
  NYSE:      'kis:raw_balance:NYSE:latest',
  AMEX:      'kis:raw_balance:AMEX:latest',
  CTRP6504R: 'kis:raw_balance:CTRP6504R:latest',
  TS:        'kis:raw_balance:ts',
};

// Destination keys (v4 shadow — write only)
const DST_KEYS = {
  NASD:      'broker:raw:NASD:latest',
  NYSE:      'broker:raw:NYSE:latest',
  AMEX:      'broker:raw:AMEX:latest',
  CTRP6504R: 'broker:raw:CTRP6504R:latest',
  LATEST:    'broker:raw:latest',
  TS:        'broker:raw:ts',
  HEARTBEAT: 'heartbeat:v4-broker-raw-collector',
};

// ─── Utility ─────────────────────────────────────────────────────
function computePayloadHash(obj) {
  return crypto.createHash('sha256')
    .update(JSON.stringify(obj))
    .digest('hex')
    .slice(0, 16);
}

function safeParseJson(str) {
  if (!str) return null;
  try { return JSON.parse(str); } catch { return null; }
}

// ─── Core Collection Cycle ───────────────────────────────────────
async function collectCycle(redis) {
  const cycleStart = Date.now();
  const tradeDate = new Date().toISOString().slice(0, 10);

  // 1. Read all source keys from existing v3 pipeline
  const [nasdRaw, nyseRaw, amexRaw, ctRaw, tsRaw] = await Promise.all([
    redis.get(SRC_KEYS.NASD),
    redis.get(SRC_KEYS.NYSE),
    redis.get(SRC_KEYS.AMEX),
    redis.get(SRC_KEYS.CTRP6504R),
    redis.get(SRC_KEYS.TS),
  ]);

  const nasd = safeParseJson(nasdRaw);
  const nyse = safeParseJson(nyseRaw);
  const amex = safeParseJson(amexRaw);
  const ctrp = safeParseJson(ctRaw);
  const srcTs = tsRaw || new Date().toISOString();

  // 2. Validate — at least one source must have data
  const hasSomeData = [nasd, nyse, amex, ctrp].some(d => d !== null);
  if (!hasSomeData) {
    log('WARN', COMPONENT, 'NO_SOURCE_DATA', {
      msg: 'All kis:raw_balance:* keys are empty. Skipping cycle.',
    });
    return { status: 'skipped', reason: 'no_source_data' };
  }

  // 3. Build unified v4 payload WITH DEDUPLICATION
  //
  // [FIX v1.1.0] KIS JTTT3012R API returns NYSE-listed stocks in NASD query too.
  // We must deduplicate by symbol. Priority order:
  //   1. NYSE data (authoritative for NYSE-listed stocks)
  //   2. AMEX data (authoritative for AMEX-listed stocks)
  //   3. NASD data (authoritative for NASDAQ-listed stocks, but also contains NYSE/AMEX dupes)
  //
  // Strategy: Process NYSE → AMEX → NASD. For each symbol, keep the first occurrence.
  // This ensures NYSE stocks use NYSE data, and NASDAQ-only stocks use NASD data.
  //
  const positionMap = new Map(); // symbol → position object
  const exchangeStats = {};
  let dupesFound = 0;

  // Process in priority order: NYSE → AMEX → NASD
  // NYSE and AMEX are authoritative for their listed stocks
  // NASD contains all stocks (including NYSE/AMEX dupes) so process last
  const exchangeOrder = [
    ['NYSE', nyse],
    ['AMEX', amex],
    ['NASD', nasd],
  ];

  for (const [excg, data] of exchangeOrder) {
    if (!data) {
      exchangeStats[excg] = { count: 0, status: 'missing' };
      continue;
    }
    const items = Array.isArray(data) ? data : (data.items || data.output1 || []);
    let added = 0;
    let skipped = 0;

    for (const item of items) {
      const symbol = item.symbol || item.ovrs_pdno || '';
      if (!symbol) continue;

      if (positionMap.has(symbol)) {
        // Duplicate found — skip (already have authoritative data)
        skipped++;
        dupesFound++;
        continue;
      }

      positionMap.set(symbol, {
        symbol,
        exchange: excg,
        qty: item.ovrs_cblc_qty ?? item.qty ?? 0,
        ord_psbl_qty: item.ord_psbl_qty ?? 0,
        avg_price: item.pchs_avg_pric ?? item.avg_price ?? 0,
        eval_amt: item.ovrs_stck_evlu_amt ?? item.eval_amt ?? 0,
        pnl: item.frcr_evlu_pfls_amt ?? item.pnl ?? 0,
        now_price: item.now_pric2 ?? item.now_price ?? 0,
        source: 'JTTT3012R',
        original_exchange: excg,
      });
      added++;
    }
    exchangeStats[excg] = { count: items.length, added, skipped, status: 'ok' };
  }

  const allPositions = Array.from(positionMap.values());

  // Log dedup stats
  if (dupesFound > 0) {
    log('INFO', COMPONENT, 'DEDUP_APPLIED', {
      total_raw: Object.values(exchangeStats).reduce((s, e) => s + (e.count || 0), 0),
      after_dedup: allPositions.length,
      dupes_removed: dupesFound,
      exchange_stats: exchangeStats,
    });
  }

  // CTRP6504R — settled basis
  let ctPositions = [];
  let ctSummary = null;
  if (ctrp) {
    const items = Array.isArray(ctrp) ? ctrp : (ctrp.items || ctrp.output1 || []);
    ctPositions = items.map(item => ({
      symbol: item.symbol || item.pdno || '',
      cblc_qty13: item.cblc_qty13 ?? item.qty ?? 0,
      ord_psbl_qty1: item.ord_psbl_qty1 ?? item.ord_psbl_qty ?? 0,
      thdt_buy_qty: item.thdt_buy_ccld_qty1 ?? 0,
      thdt_sll_qty: item.thdt_sll_ccld_qty1 ?? 0,
      avg_price: item.avg_unpr3 ?? item.pchs_avg_pric ?? item.avg_price ?? 0,
      now_price: item.ovrs_now_pric1 ?? item.now_pric2 ?? item.now_price ?? 0,
      source: 'CTRP6504R',
    }));
    ctSummary = ctrp.output2 || ctrp.summary || null;
  }

  // CTRP output3 — account-level totals (contains tot_asst_amt in KRW)
  let ctOutput3 = null;
  if (ctrp) {
    ctOutput3 = ctrp.output3 || null;
  }

  // 4. Build the unified snapshot
  const snapshot = {
    trade_date: tradeDate,
    collected_at: new Date().toISOString(),
    source_ts: srcTs,
    positions: allPositions,
    ctrp_positions: ctPositions,
    ctrp_summary: ctSummary,
    ctrp_output3: ctOutput3,
    exchange_stats: exchangeStats,
    position_count: allPositions.length,
    ctrp_position_count: ctPositions.length,
    dedup_applied: dupesFound > 0,
    dedup_removed: dupesFound,
    payload_hash: computePayloadHash({ allPositions, ctPositions }),
    collector_version: '1.1.0-ec2',
    provenance: 'v4-shadow-from-v3-keys',
  };

  // 5. Write to v4 shadow keys (NEVER touch v3 keys)
  const pipeline = redis.pipeline();

  // Per-exchange raw snapshots
  if (nasd) pipeline.set(DST_KEYS.NASD, nasdRaw, 'EX', SHADOW_TTL);
  if (nyse) pipeline.set(DST_KEYS.NYSE, nyseRaw, 'EX', SHADOW_TTL);
  if (amex) pipeline.set(DST_KEYS.AMEX, amexRaw, 'EX', SHADOW_TTL);
  if (ctrp) pipeline.set(DST_KEYS.CTRP6504R, ctRaw, 'EX', SHADOW_TTL);

  // Unified snapshot
  pipeline.set(DST_KEYS.LATEST, JSON.stringify(snapshot), 'EX', SHADOW_TTL);
  pipeline.set(DST_KEYS.TS, new Date().toISOString(), 'EX', SHADOW_TTL);

  // Heartbeat
  pipeline.set(DST_KEYS.HEARTBEAT, String(Date.now()), 'EX', TTL.HEARTBEAT);

  await pipeline.exec();

  const elapsed = Date.now() - cycleStart;
  log('INFO', COMPONENT, 'CYCLE_OK', {
    positions: allPositions.length,
    ctrp_positions: ctPositions.length,
    exchanges: exchangeStats,
    dedup_removed: dupesFound,
    payload_hash: snapshot.payload_hash,
    elapsed_ms: elapsed,
  });

  return { status: 'ok', positions: allPositions.length, dedup_removed: dupesFound, elapsed };
}

// ─── Main Loop ───────────────────────────────────────────────────
async function main() {
  log('INFO', COMPONENT, 'STARTING', {
    version: '1.1.0-ec2',
    interval_ms: INTERVAL_MS,
    shadow_ttl_sec: SHADOW_TTL,
    src_keys: Object.values(SRC_KEYS),
    dst_keys: Object.values(DST_KEYS),
    dedup_enabled: true,
  });

  const rawRedis = getRedis();
  const redis = createWriteGuard(rawRedis, COMPONENT);

  // Wait for Redis ready
  await new Promise((resolve) => {
    if (redis.status === 'ready') return resolve();
    redis.once('ready', resolve);
    setTimeout(() => resolve(), 10000); // fallback
  });

  log('INFO', COMPONENT, 'REDIS_CONNECTED', { status: redis.status });

  // Initial cycle
  await collectCycle(redis).catch(e =>
    log('ERROR', COMPONENT, 'INITIAL_CYCLE_FAILED', { error: e.message })
  );

  // Periodic loop
  const timer = setInterval(async () => {
    try {
      await collectCycle(redis);
    } catch (e) {
      log('ERROR', COMPONENT, 'CYCLE_ERROR', { error: e.message, stack: e.stack });
    }
  }, INTERVAL_MS);

  // Graceful shutdown
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      log('INFO', COMPONENT, 'SHUTDOWN', { signal: sig });
      clearInterval(timer);
      redis.disconnect();
      process.exit(0);
    });
  }
}

main().catch(e => {
  log('CRITICAL', COMPONENT, 'FATAL', { error: e.message, stack: e.stack });
  process.exit(1);
});
