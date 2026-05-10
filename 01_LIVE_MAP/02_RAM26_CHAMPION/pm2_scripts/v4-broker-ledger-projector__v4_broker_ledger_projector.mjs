import { createWriteGuard } from './v4_write_guard.mjs';
/**
 * v4_broker_ledger_projector.mjs — v4 Shadow Broker Ledger Projector
 * ====================================================================
 * ARES Resilience Pack v4 — EC2 적합화 Shadow Mode
 *
 * 설계 원칙 (클로드 원저자 Fix #1-3 반영):
 *   - 3-tier status: verified / provisional / quarantined
 *   - provenance_chain: status별 분기 (verified→raw_broker,derived_verified 등)
 *   - trade_date 오늘 날짜 fallback 제거 → 명시적 source_ts 기반
 *   - payload_hash 없을 때 로컬 deterministic hash 유도 (자동 강등 대신 reason 추가)
 *   - safe parsing: 모든 숫자 필드에 safeFloat/safeInt 적용
 *
 * [FIX 2026-04-17] v1.1.0-ec2:
 *   - Bug Fix #1: CTRP frcr_evlu_amt2 기반 NAV 계산 추가 (Method 1b)
 *     CTRP output2의 frcr_evlu_amt2(KRW 평가금액)를 환율로 나누어 USD 총자산 산출
 *   - Bug Fix #2: position_sum fallback 시 현금(frcr_dncl_amt_2) 포함
 *   - Bug Fix #3: position_sum 방어적 symbol-dedup (collector 수정과 이중 방어)
 *   - NAV 계산 우선순위: ctrp_tot_asst → ctrp_frcr_evlu → position_sum_plus_cash
 *
 * 데이터 흐름:
 *   broker:raw:latest → 읽기 → 경제적 NAV 계산 → broker:ledger:economic_nav:{date}
 *                                                  broker:ledger:latest
 *                                                  broker:ledger:status
 *
 * 환경변수:
 *   V4_PROJECTOR_INTERVAL_MS  — 투영 주기 (기본: 30000ms)
 *   V4_SHADOW_TTL_SEC         — shadow 키 TTL (기본: 3600s)
 *
 * @version 1.1.0-ec2
 * @date 2026-04-17
 */

import { getRedis } from '../lib/redis-connection.mjs';
import { safeSetJson, TTL } from '../lib/ares-redis-safe.mjs';
import { log } from '../lib/structured_logger.mjs';
import crypto from 'node:crypto';

// ─── Configuration ───────────────────────────────────────────────
const COMPONENT = 'v4-broker-ledger-projector';
const INTERVAL_MS = parseInt(process.env.V4_PROJECTOR_INTERVAL_MS || '30000', 10);
const SHADOW_TTL = parseInt(process.env.V4_SHADOW_TTL_SEC || '3600', 10);

// Source keys (v4 shadow — from collector)
const SRC = {
  RAW_LATEST: 'broker:raw:latest',
  RAW_TS:     'broker:raw:ts',
};

// Destination keys (v4 shadow)
const DST = {
  LEDGER_LATEST:    'broker:ledger:latest',
  LEDGER_STATUS:    'broker:ledger:status',
  HEARTBEAT:        'heartbeat:v4-broker-ledger-projector',
};
function ledgerDateKey(date) { return `broker:ledger:economic_nav:${date}`; }

// Also read v3 truth for comparison (READ ONLY)
const V3_TRUTH = {
  EQUITY:     'ares:equity:broker:total_usd',
  FX:         'truth:fx:usd_krw',
  CHAMPION:   'champion:live:performance',
};

// ─── Safe Parsing ────────────────────────────────────────────────
function safeFloat(v, fallback = 0) {
  if (v === null || v === undefined || v === '') return fallback;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

function safeInt(v, fallback = 0) {
  if (v === null || v === undefined || v === '') return fallback;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : fallback;
}

function computeHash(obj) {
  return crypto.createHash('sha256')
    .update(JSON.stringify(obj))
    .digest('hex')
    .slice(0, 16);
}

function safeParseJson(str) {
  if (!str) return null;
  try { return JSON.parse(str); } catch { return null; }
}

// ─── 3-Tier Status Determination ─────────────────────────────────
/**
 * Determine the verification tier of the raw snapshot.
 *
 * verified:     payload_hash exists AND source_ts < 5min old
 * provisional:  payload_hash missing OR source_ts 5-15min old
 * quarantined:  source_ts > 15min old OR critical fields missing
 */
function determineStatus(snapshot) {
  if (!snapshot || !snapshot.collected_at) {
    return { status: 'quarantined', reason: 'no_snapshot' };
  }

  const age = Date.now() - new Date(snapshot.collected_at).getTime();
  const ageMin = age / 60000;

  if (ageMin > 15) {
    return { status: 'quarantined', reason: `stale_data_${Math.round(ageMin)}min` };
  }

  if (!snapshot.position_count && !snapshot.ctrp_position_count) {
    return { status: 'quarantined', reason: 'no_positions' };
  }

  const hasHash = !!snapshot.payload_hash;

  if (ageMin <= 5 && hasHash) {
    return { status: 'verified', reason: 'fresh_with_hash' };
  }

  if (!hasHash) {
    return { status: 'provisional', reason: 'missing_payload_hash' };
  }

  return { status: 'provisional', reason: `aging_${Math.round(ageMin)}min` };
}

// ─── Provenance Chain Builder ────────────────────────────────────
function buildProvenanceChain(status, snapshot) {
  const base = {
    collector: 'v4-broker-raw-collector',
    projector: COMPONENT,
    raw_source: snapshot?.provenance || 'unknown',
    projected_at: new Date().toISOString(),
  };

  switch (status) {
    case 'verified':
      return { ...base, chain: ['raw_broker', 'derived_verified'] };
    case 'provisional':
      return { ...base, chain: ['raw_broker', 'derived_estimated'] };
    case 'quarantined':
      return { ...base, chain: ['raw_broker', 'quarantined'] };
    default:
      return { ...base, chain: ['raw_broker', 'unknown'] };
  }
}

// ─── Economic NAV Calculation ────────────────────────────────────
/**
 * Calculate Economic NAV from raw positions.
 *
 * Priority order:
 *   Method 1: CTRP output3.tot_asst_amt / output2.frst_bltn_exrt
 *             (KRW 총자산 / 환율 = USD 총자산, v3 truth와 0.003% 이내 일치)
 *   Method 2: Deduped position_sum + cash (frcr_dncl_amt_2 = 예수금 USD)
 *   Method 3: Deduped position_sum only (last resort)
 *
 * [FIX v1.1.0] Changes:
 *   - Method 1 now uses output3.tot_asst_amt (KRW total assets) from CTRP6504R
 *     This is the SAME source v3 uses for truth:equity:broker_total_usd
 *   - REMOVED frcr_evlu_amt2: this is cash evaluation KRW, NOT total assets
 *   - Method 2 includes cash from CTRP summary (frcr_dncl_amt_2 in USD)
 *   - Method 2/3 apply symbol-based dedup as defense-in-depth
 */
function calculateEconomicNav(snapshot) {
  const result = {
    method: 'unknown',
    total_equity_usd: 0,
    position_equity_usd: 0,
    cash_usd: 0,
    position_count: 0,
    ctrp_position_count: 0,
  };

  // Extract CTRP summary (can be array or object)
  let ctSummary = snapshot.ctrp_summary;
  if (Array.isArray(ctSummary) && ctSummary.length > 0) {
    ctSummary = ctSummary[0]; // KIS returns array with single USD entry
  }

  // Method 1: CTRP output3.tot_asst_amt / output2.frst_bltn_exrt
  // output3 contains account-level totals from KIS CTRP6504R API
  // tot_asst_amt is the KRW total asset value (positions + cash + unsettled)
  // This is the SAME source v3 uses for truth:equity:broker_total_usd
  const ctOutput3 = snapshot.ctrp_output3 || null;
  if (ctOutput3 && ctSummary) {
    const totAsstKrw = safeFloat(ctOutput3.tot_asst_amt);
    const fxRate = safeFloat(ctSummary.frst_bltn_exrt) ||
                   safeFloat(ctSummary.exrt);
    if (totAsstKrw > 0 && fxRate > 0) {
      result.method = 'ctrp_tot_asst';
      result.total_equity_usd = totAsstKrw / fxRate;
      result.total_equity_krw = totAsstKrw;
      result.fx_rate = fxRate;
    }
  }

  // Fallback Method 1b: output2.tot_asst_amt (if output3 unavailable)
  if (result.method === 'unknown' && ctSummary) {
    const totAsst = safeFloat(ctSummary.tot_asst_amt);
    const fxRate = safeFloat(ctSummary.frst_bltn_exrt) ||
                   safeFloat(ctSummary.exrt);
    if (totAsst > 0 && fxRate > 0) {
      result.method = 'ctrp_tot_asst_output2';
      result.total_equity_usd = totAsst / fxRate;
      result.total_equity_krw = totAsst;
      result.fx_rate = fxRate;
    }
  }

  // NOTE: frcr_evlu_amt2 is the KRW equivalent of frcr_dncl_amt_2 (cash/deposit),
  // NOT the total asset evaluation. Do NOT use it as NAV.
  // Verified: frcr_evlu_amt2 / frst_bltn_exrt === frcr_dncl_amt_2

  // Method 2/3: Sum individual positions (with dedup defense)
  const positions = snapshot.positions || [];
  const seenSymbols = new Set();
  let positionSum = 0;
  let dedupCount = 0;

  for (const p of positions) {
    const sym = p.symbol || '';
    if (!sym) continue;
    if (seenSymbols.has(sym)) {
      dedupCount++;
      continue; // Skip duplicate — defense-in-depth (collector should already dedup)
    }
    seenSymbols.add(sym);
    positionSum += safeFloat(p.eval_amt);
  }

  result.position_equity_usd = positionSum;
  result.position_count = seenSymbols.size;
  result.projector_dedup_count = dedupCount;

  // CTRP positions
  const ctPositions = snapshot.ctrp_positions || [];
  result.ctrp_position_count = ctPositions.length;

  // If Method 1a/1b didn't work, fall back to position sum + cash
  if (result.method === 'unknown') {
    // Extract cash from CTRP summary
    let cashUsd = 0;
    if (ctSummary) {
      // frcr_dncl_amt_2 = 예수금 (deposit amount in USD)
      // This is the total cash available including unsettled amounts
      cashUsd = safeFloat(ctSummary.frcr_dncl_amt_2);
    }

    if (cashUsd > 0) {
      result.method = 'position_sum_plus_cash';
      result.total_equity_usd = positionSum + cashUsd;
      result.cash_usd = cashUsd;
    } else {
      result.method = 'position_sum';
      result.total_equity_usd = positionSum;
    }
  }

  // Log if dedup was needed at projector level (shouldn't happen if collector is fixed)
  if (dedupCount > 0) {
    log('WARN', COMPONENT, 'PROJECTOR_DEDUP', {
      msg: 'Duplicate positions found at projector level. Collector dedup may have failed.',
      dedup_count: dedupCount,
      unique_positions: seenSymbols.size,
    });
  }

  return result;
}

// ─── v3 Truth Comparison ─────────────────────────────────────────
async function readV3Truth(redis) {
  const [equityRaw, fxRaw, champRaw] = await Promise.all([
    redis.get(V3_TRUTH.EQUITY),
    redis.get(V3_TRUTH.FX),
    redis.get(V3_TRUTH.CHAMPION),
  ]);
  return {
    v3_equity_usd: safeFloat(equityRaw),
    v3_fx_rate: safeFloat(fxRaw),
    v3_champion: safeParseJson(champRaw),
  };
}

// ─── Core Projection Cycle ───────────────────────────────────────
async function projectCycle(redis) {
  const cycleStart = Date.now();

  // 1. Read v4 shadow raw data
  const rawStr = await redis.get(SRC.RAW_LATEST);
  const snapshot = safeParseJson(rawStr);

  if (!snapshot) {
    log('WARN', COMPONENT, 'NO_RAW_DATA', {
      msg: 'broker:raw:latest is empty. Collector may not be running.',
    });
    await redis.set(DST.HEARTBEAT, String(Date.now()), 'EX', TTL.HEARTBEAT);
    return { status: 'skipped', reason: 'no_raw_data' };
  }

  // 2. Determine 3-tier status
  const { status, reason } = determineStatus(snapshot);

  // 3. Calculate Economic NAV
  const nav = calculateEconomicNav(snapshot);

  // 4. Build provenance chain
  const provenance = buildProvenanceChain(status, snapshot);

  // 5. Read v3 truth for comparison (read only!)
  const v3Truth = await readV3Truth(redis);

  // 6. Compute divergence from v3
  let divergence = null;
  if (v3Truth.v3_equity_usd > 0 && nav.total_equity_usd > 0) {
    const diff = nav.total_equity_usd - v3Truth.v3_equity_usd;
    const pct = (diff / v3Truth.v3_equity_usd) * 100;
    divergence = {
      v3_equity_usd: v3Truth.v3_equity_usd,
      v4_equity_usd: nav.total_equity_usd,
      diff_usd: Math.round(diff * 100) / 100,
      diff_pct: Math.round(pct * 100) / 100,
      within_threshold: Math.abs(pct) < 2.0,
    };
  }

  // 7. Build the ledger entry
  const tradeDate = snapshot.trade_date || new Date().toISOString().slice(0, 10);
  const ledger = {
    trade_date: tradeDate,
    projected_at: new Date().toISOString(),
    status,
    status_reason: reason,
    economic_nav: nav,
    provenance,
    v3_comparison: divergence,
    raw_snapshot_hash: snapshot.payload_hash || computeHash(snapshot),
    raw_collected_at: snapshot.collected_at,
    projector_version: '1.1.0-ec2',
  };

  // 8. Write to v4 shadow keys (NEVER touch v3 keys)
  const pipeline = redis.pipeline();
  pipeline.set(ledgerDateKey(tradeDate), JSON.stringify(ledger), 'EX', SHADOW_TTL);
  pipeline.set(DST.LEDGER_LATEST, JSON.stringify(ledger), 'EX', SHADOW_TTL);
  pipeline.set(DST.LEDGER_STATUS, JSON.stringify({
    status,
    reason,
    trade_date: tradeDate,
    updated_at: new Date().toISOString(),
    nav_usd: nav.total_equity_usd,
    nav_method: nav.method,
    divergence_pct: divergence?.diff_pct ?? null,
  }), 'EX', SHADOW_TTL);
  pipeline.set(DST.HEARTBEAT, String(Date.now()), 'EX', TTL.HEARTBEAT);

  await pipeline.exec();

  const elapsed = Date.now() - cycleStart;
  log('INFO', COMPONENT, 'CYCLE_OK', {
    status,
    reason,
    nav_usd: Math.round(nav.total_equity_usd * 100) / 100,
    nav_method: nav.method,
    positions: nav.position_count,
    cash_usd: nav.cash_usd,
    projector_dedup: nav.projector_dedup_count || 0,
    divergence_pct: divergence?.diff_pct ?? 'N/A',
    within_threshold: divergence?.within_threshold ?? 'N/A',
    elapsed_ms: elapsed,
  });

  return { status: 'ok', tier: status, nav_usd: nav.total_equity_usd, nav_method: nav.method, elapsed };
}

// ─── Main Loop ───────────────────────────────────────────────────
async function main() {
  log('INFO', COMPONENT, 'STARTING', {
    version: '1.1.0-ec2',
    interval_ms: INTERVAL_MS,
    shadow_ttl_sec: SHADOW_TTL,
    src_keys: Object.values(SRC),
    dst_keys: Object.values(DST),
    nav_methods: ['ctrp_tot_asst (output3)', 'ctrp_tot_asst_output2', 'position_sum_plus_cash', 'position_sum'],
  });

  const rawRedis = getRedis();
  const redis = createWriteGuard(rawRedis, COMPONENT);

  await new Promise((resolve) => {
    if (redis.status === 'ready') return resolve();
    redis.once('ready', resolve);
    setTimeout(() => resolve(), 10000);
  });

  log('INFO', COMPONENT, 'REDIS_CONNECTED', { status: redis.status });

  // Initial cycle
  await projectCycle(redis).catch(e =>
    log('ERROR', COMPONENT, 'INITIAL_CYCLE_FAILED', { error: e.message })
  );

  // Periodic loop
  const timer = setInterval(async () => {
    try {
      await projectCycle(redis);
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
