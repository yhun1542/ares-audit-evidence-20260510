import { createWriteGuard } from './v4_write_guard.mjs';
/**
 * v4_performance_verified_writer.mjs — v4 Performance Verified Writer
 * ====================================================================
 * ARES Resilience Pack v4 — EC2 적합화 Live-Ready Mode
 *
 * 설계 원칙:
 *   - broker:ledger:latest를 읽어서 champion:perf:daily:* 키에 기록
 *   - feature flag 'feature:v4_write_to_champion_live' = '1' 이면
 *     champion:live:performance 에도 ALIAS 쓰기 (v3 소비자 무중단 전환)
 *   - v3 truth와 비교하여 divergence table을 유지한다
 *   - verified 상태만 champion:perf:daily:verified:* 에 기록
 *   - provisional은 champion:perf:daily:provisional:* 에 기록
 *   - quarantined는 기록하지 않고 경고만 발생
 *
 * ALIAS Cutover 흐름:
 *   1. feature:v4_write_to_champion_live = '1' 설정
 *   2. 이 writer가 champion:live:performance 에 v4 데이터를 쓰기 시작
 *   3. v3 live-performance-tracker 중지
 *   4. 모든 다운스트림 소비자가 자동으로 v4 데이터를 읽게 됨
 *
 * [FIX-6 STRUCTURAL v1.2.0 2026-04-17]:
 *   - 소유권 검증(ownership guard): ALIAS_WRITE 전에 기존 키의 writer_version 확인
 *   - 외부 덮어쓰기 감지 시 ALERT 로그 + 즉시 재기록으로 자동 복구
 *   - live-performance-tracker 좀비 감지: pm2 list에서 프로세스 존재 시 경고
 *
 * [FIX-7 RETURNS v1.3.0 2026-05-06]:
 *   - broker:raw:latest에서 포지션별 일간 수익률 계산 및 저장
 *   - perf:returns:YYYYMMDD 키에 {SYM: daily_return} 형식으로 저장
 *   - BacktestHarness.run()의 DailySnapshot.returns 필드와 호환
 *   - 파레토 최적화 및 백테스트 현실화에 활용 가능
 *
 * @version 1.3.0-ec2
 * @date 2026-05-06
 */
import { getRedis } from '../lib/redis-connection.mjs';
import { TTL } from '../lib/ares-redis-safe.mjs';
import { log } from '../lib/structured_logger.mjs';
// ─── Configuration ───────────────────────────────────────────────
const COMPONENT = 'v4-performance-verified-writer';
const WRITER_VERSION = 'v4-1.3.1-ec2';
const INTERVAL_MS = parseInt(process.env.V4_WRITER_INTERVAL_MS || '60000', 10);
const SHADOW_TTL = parseInt(process.env.V4_SHADOW_TTL_SEC || '86400', 10); // 24h for daily perf
const LIVE_TTL = 3600; // 1h TTL for champion:live:performance (matches v3 behavior)
const RETURNS_TTL = 172800; // 48h TTL for perf:returns:YYYYMMDD (파레토 최적화용)
// Source keys (v4 shadow — from projector)
const SRC = {
  LEDGER_LATEST: 'broker:ledger:latest',
  LEDGER_STATUS: 'broker:ledger:status',
  RAW_LATEST:    'broker:raw:latest',   // [FIX-7] 포지션별 수익률 소스
};
// Destination keys (v4 shadow)
const DST = {
  SHADOW_LATEST:  'champion:perf:shadow:latest',
  DIFF_LATEST:    'v4:diff:latest',
  DIFF_HISTORY:   'v4:diff:history',  // Redis list
  HEARTBEAT:      'heartbeat:v4-performance-verified-writer',
};
// ALIAS target — v3 consumer key
const ALIAS_KEY = 'champion:live:performance';
const ALIAS_FLAG = 'feature:v4_write_to_champion_live';
// ─── [FIX-6 STRUCTURAL] Ownership Guard Constants ───────────────
const V4_WRITER_PREFIX = 'v4-';  // All v4 writer_version values start with this
const OWNERSHIP_ALERT_COOLDOWN_MS = 300000; // 5 min cooldown between alerts
let lastOwnershipAlertTs = 0;
function verifiedKey(date) { return `champion:perf:daily:verified:${date}`; }
function provisionalKey(date) { return `champion:perf:daily:provisional:${date}`; }
function returnsKey(date) { return `perf:returns:${date}`; }  // [FIX-7]
// v3 comparison keys (READ ONLY)
const V3 = {
  CHAMPION: 'champion:live:performance',
  EQUITY:   'ares:equity:broker:total_usd',
};
// ─── Utility ─────────────────────────────────────────────────────
function safeFloat(v, fallback = 0) {
  if (v === null || v === undefined || v === '') return fallback;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}
function safeParseJson(str) {
  if (!str) return null;
  try { return JSON.parse(str); } catch { return null; }
}
// ─── [FIX-6 STRUCTURAL] Ownership Guard ─────────────────────────
/**
 * Check if champion:live:performance is currently owned by v4 writer.
 * If another writer (e.g., live-performance-tracker) has overwritten it,
 * detect and log an ALERT.
 *
 * @returns {{ owned: boolean, currentWriter: string|null, needsRecovery: boolean }}
 */
async function checkAliasOwnership(redis) {
  const currentStr = await redis.get(ALIAS_KEY);
  if (!currentStr) {
    // Key doesn't exist — safe to write
    return { owned: true, currentWriter: null, needsRecovery: false };
  }
  const current = safeParseJson(currentStr);
  if (!current) {
    // Unparseable — treat as corrupted, needs recovery
    return { owned: false, currentWriter: 'CORRUPTED', needsRecovery: true };
  }
  const writerVersion = current.writer_version || null;
  const writerSource = current.writer_source || current.source || null;
  // Check if the current value was written by a v4 writer
  if (writerVersion && writerVersion.startsWith(V4_WRITER_PREFIX)) {
    return { owned: true, currentWriter: writerVersion, needsRecovery: false };
  }
  // Foreign writer detected!
  const now = Date.now();
  if (now - lastOwnershipAlertTs > OWNERSHIP_ALERT_COOLDOWN_MS) {
    lastOwnershipAlertTs = now;
    log('ALERT', COMPONENT, 'ALIAS_OWNERSHIP_VIOLATION', {
      key: ALIAS_KEY,
      expected_writer_prefix: V4_WRITER_PREFIX,
      actual_writer_version: writerVersion,
      actual_writer_source: writerSource,
      actual_version_field: current.version || null,
      message: `champion:live:performance was overwritten by a non-v4 writer! ` +
               `Expected writer_version starting with "${V4_WRITER_PREFIX}", ` +
               `found "${writerVersion}". Auto-recovery will overwrite with v4 data.`,
      fix_reference: 'FIX-6-STRUCTURAL',
    });
  }
  return { owned: false, currentWriter: writerVersion || writerSource || 'unknown', needsRecovery: true };
}
// ─── ALIAS Write: champion:live:performance 호환 포맷 빌더 ──────
/**
 * v3 live-performance-tracker가 쓰던 champion:live:performance 포맷과
 * 호환되는 객체를 생성한다. 다운스트림 소비자(ops-dashboard-api,
 * collect_contradictions, backtest_vs_live_compare 등)가 기존 필드명으로
 * 데이터를 읽을 수 있도록 보장한다.
 */
function buildAliasRecord(perfRecord, diffRecord, ledger) {
  return {
    // ── v3 호환 필드 (다운스트림 소비자가 읽는 필드) ──
    total_equity_usd: perfRecord.nav_usd,
    equity_usd: perfRecord.nav_usd,
    nav_usd: perfRecord.nav_usd,
    trade_date: perfRecord.trade_date,
    position_count: perfRecord.position_count,
    ctrp_position_count: perfRecord.ctrp_position_count,
    ts: new Date().toISOString(),
    written_at: new Date().toISOString(),
    // ── v4 출처 표시 (cutover 확인용) ──
    writer_version: WRITER_VERSION,
    writer_source: 'v4-performance-verified-writer',
    v4_status: perfRecord.status,
    v4_status_reason: perfRecord.status_reason,
    v4_nav_method: perfRecord.nav_method,
    v4_provenance: perfRecord.provenance,
    // ── diff 정보 (모니터링용) ──
    diff_pct: diffRecord.diff_pct,
    diff_usd: diffRecord.diff_usd,
    cutover_safe: diffRecord.cutover_safe,
  };
}

// ─── [FIX-7 RETURNS] 종목별 일간 수익률 계산 ────────────────────
/**
 * broker:raw:latest의 positions 배열에서 종목별 진짜 일간 수익률을 계산한다.
 *
 * 수익률 계산 우선순위:
 *   1순위: ic:prev_price:SYM (전일 종가) → (now_price - prev_close) / prev_close  [진짜 일간 수익률]
 *   2순위: avg_price (평균 매입단가) → (now_price - avg_price) / avg_price          [누적 수익률 폴백]
 *   3순위: pnl / cost_basis                                                          [최후 폴백]
 *
 * BacktestHarness.run()의 DailySnapshot.returns: Dict[str, float]와 호환된다.
 *
 * @param {object[]} positions - broker:raw:latest.positions 배열
 * @param {Record<string, number>} prevPrices - ic:prev_price:SYM 맵 {SYM: prev_close}
 * @returns {{ returns: Record<string, number>, position_count: number, total_position_nav_usd: number, prev_price_coverage: number }}
 */
function computeSymbolReturns(positions, prevPrices = {}) {
  const returns = {};
  let totalNavUsd = 0;
  const seenSymbols = new Set();
  let prevPriceCoverage = 0;

  for (const p of positions) {
    const sym = (p.symbol || '').trim().toUpperCase();
    if (!sym) continue;
    // 중복 심볼 방지 (dedup — collector 수준에서 이미 처리되지만 방어적 처리)
    if (seenSymbols.has(sym)) continue;
    seenSymbols.add(sym);

    const nowPrice = safeFloat(p.now_price);
    const evalAmt = safeFloat(p.eval_amt);
    let dailyReturn = 0;
    let returnMethod = 'none';

    // 1순위: ic:prev_price:SYM — 전일 종가 기준 진짜 일간 수익률
    const prevClose = prevPrices[sym];
    if (prevClose && prevClose > 0 && nowPrice > 0) {
      dailyReturn = (nowPrice - prevClose) / prevClose;
      returnMethod = 'prev_close';
      prevPriceCoverage++;
    } else {
      // 2순위: avg_price 기준 누적 수익률 (폴백)
      const avgPrice = safeFloat(p.avg_price);
      if (avgPrice > 0 && nowPrice > 0) {
        dailyReturn = (nowPrice - avgPrice) / avgPrice;
        returnMethod = 'avg_price_fallback';
      } else if (evalAmt > 0 && safeFloat(p.pnl) !== 0) {
        // 3순위: pnl / cost_basis
        const costBasis = evalAmt - safeFloat(p.pnl);
        if (costBasis > 0) {
          dailyReturn = safeFloat(p.pnl) / costBasis;
          returnMethod = 'pnl_fallback';
        }
      }
    }

    // NaN/Inf 방어
    if (!Number.isFinite(dailyReturn)) dailyReturn = 0;

    returns[sym] = Math.round(dailyReturn * 1e6) / 1e6;  // 소수점 6자리 반올림
    totalNavUsd += evalAmt;
  }

  return {
    returns,
    position_count: seenSymbols.size,
    total_position_nav_usd: Math.round(totalNavUsd * 100) / 100,
    prev_price_coverage: prevPriceCoverage,
  };
}

// ─── Core Write Cycle ────────────────────────────────────────────
async function writeCycle(redis) {
  const cycleStart = Date.now();
  // 1. Read v4 ledger
  const ledgerStr = await redis.get(SRC.LEDGER_LATEST);
  const ledger = safeParseJson(ledgerStr);
  if (!ledger) {
    log('WARN', COMPONENT, 'NO_LEDGER_DATA', {
      msg: 'broker:ledger:latest is empty. Projector may not be running.',
    });
    await redis.set(DST.HEARTBEAT, String(Date.now()), 'EX', TTL.HEARTBEAT);
    return { status: 'skipped', reason: 'no_ledger_data' };
  }
  const { status, trade_date, economic_nav, provenance, status_reason } = ledger;
  const navUsd = economic_nav?.total_equity_usd ?? 0;

  // 2. Read v3 truth for comparison (READ ONLY) + [FIX-7] broker:raw:latest
  const [v3ChampStr, v3EquityStr, aliasFlagStr, rawLatestStr] = await Promise.all([
    redis.get(V3.CHAMPION),
    redis.get(V3.EQUITY),
    redis.get(ALIAS_FLAG),
    redis.get(SRC.RAW_LATEST),   // [FIX-7] 포지션별 수익률 소스
  ]);
  const v3Champ = safeParseJson(v3ChampStr);
  // [FIX 2026-04-17] V3 equity from truth:equity:broker_total_usd.
  // Can be a plain number string ("218909.02") or JSON object.
  let v3Equity = 0;
  try {
    const v3Parsed = JSON.parse(v3EquityStr || '0');
    if (typeof v3Parsed === 'number') {
      // Plain number string (e.g., "218909.02") — most common case
      v3Equity = v3Parsed;
    } else if (typeof v3Parsed === 'object' && v3Parsed !== null) {
      // JSON object (e.g., ares:equity:snapshot format)
      v3Equity = safeFloat(v3Parsed?.total) || safeFloat(v3Parsed?.broker?.tot_asst_usd) || 0;
    }
  } catch (e) {
    v3Equity = safeFloat(v3EquityStr);  // fallback
  }
  const aliasEnabled = aliasFlagStr === '1';

  // [FIX-7] 종목별 수익률 계산 — ic:prev_price:SYM 전일 종가 일괄 조회
  const rawLatest = safeParseJson(rawLatestStr);
  const rawPositions = rawLatest?.positions || [];

  // ic:prev_price:SYM 키 일괄 조회 (pipeline으로 효율적 처리)
  let prevPrices = {};
  if (rawPositions.length > 0) {
    const prevPipeline = redis.pipeline();
    for (const p of rawPositions) {
      const sym = (p.symbol || '').trim().toUpperCase();
      if (sym) prevPipeline.get(`ic:prev_price:${sym}`);
    }
    const prevResults = await prevPipeline.exec();
    rawPositions.forEach((p, i) => {
      const sym = (p.symbol || '').trim().toUpperCase();
      const val = prevResults[i]?.[1];
      if (sym && val !== null && val !== undefined) {
        const n = parseFloat(val);
        if (Number.isFinite(n) && n > 0) prevPrices[sym] = n;
      }
    });
  }
  const symbolReturnsData = computeSymbolReturns(rawPositions, prevPrices);

  // 3. Build performance record
  const perfRecord = {
    trade_date,
    status,
    status_reason,
    nav_usd: navUsd,
    nav_method: economic_nav?.method || 'unknown',
    position_count: economic_nav?.position_count ?? 0,
    ctrp_position_count: economic_nav?.ctrp_position_count ?? 0,
    provenance,
    written_at: new Date().toISOString(),
    writer_version: WRITER_VERSION,
  };
  // 4. Build diff record
  const diffRecord = {
    trade_date,
    timestamp: new Date().toISOString(),
    v4_nav_usd: navUsd,
    v4_status: status,
    v4_method: economic_nav?.method || 'unknown',
    v3_equity_usd: v3Equity,
    v3_champion_equity: v3Champ?.total_equity_usd ?? v3Champ?.equity_usd ?? null,
    diff_usd: null,
    diff_pct: null,
    cutover_safe: false,
  };
  // Calculate divergence
  const v3Ref = v3Equity || safeFloat(v3Champ?.total_equity_usd ?? v3Champ?.equity_usd);
  if (v3Ref > 0 && navUsd > 0) {
    diffRecord.diff_usd = Math.round((navUsd - v3Ref) * 100) / 100;
    diffRecord.diff_pct = Math.round(((navUsd - v3Ref) / v3Ref) * 10000) / 100;
    diffRecord.cutover_safe = Math.abs(diffRecord.diff_pct) < 2.0;
  }
  // 5. Write based on status tier
  const pipeline = redis.pipeline();
  if (status === 'verified') {
    pipeline.set(verifiedKey(trade_date), JSON.stringify(perfRecord), 'EX', SHADOW_TTL);
    log('INFO', COMPONENT, 'VERIFIED_WRITE', {
      trade_date, nav_usd: navUsd, diff_pct: diffRecord.diff_pct,
    });
  } else if (status === 'provisional') {
    pipeline.set(provisionalKey(trade_date), JSON.stringify(perfRecord), 'EX', SHADOW_TTL);
    log('INFO', COMPONENT, 'PROVISIONAL_WRITE', {
      trade_date, nav_usd: navUsd, reason: status_reason,
    });
  } else {
    // quarantined — do NOT write performance, only log warning
    log('WARN', COMPONENT, 'QUARANTINED_SKIP', {
      trade_date, reason: status_reason,
      msg: 'Quarantined data not written to performance keys.',
    });
  }
  // Always write shadow latest and diff (regardless of tier)
  pipeline.set(DST.SHADOW_LATEST, JSON.stringify(perfRecord), 'EX', SHADOW_TTL);
  pipeline.set(DST.DIFF_LATEST, JSON.stringify(diffRecord), 'EX', SHADOW_TTL);
  // Append to diff history (keep last 2880 entries = 48h at 1min interval)
  pipeline.lpush(DST.DIFF_HISTORY, JSON.stringify(diffRecord));
  pipeline.ltrim(DST.DIFF_HISTORY, 0, 2879);
  pipeline.expire(DST.DIFF_HISTORY, SHADOW_TTL);

  // ── [FIX-7 RETURNS] 종목별 일간 수익률 저장 ──────────────────
  // BacktestHarness.run()의 DailySnapshot.returns: Dict[str, float]와 호환
  // quarantined 상태에서도 포지션 데이터가 있으면 저장 (백테스트 데이터 연속성 보장)
  if (rawPositions.length > 0 && Object.keys(symbolReturnsData.returns).length > 0) {
    const returnsRecord = {
      trade_date,
      status,
      returns: symbolReturnsData.returns,          // {SYM: daily_return, ...} — 전일 종가 기준
      position_count: symbolReturnsData.position_count,
      prev_price_coverage: symbolReturnsData.prev_price_coverage,  // ic:prev_price 커버 종목 수
      total_position_nav_usd: symbolReturnsData.total_position_nav_usd,
      written_at: new Date().toISOString(),
      writer_version: WRITER_VERSION,
      source: 'broker:raw:latest+ic:prev_price',
      return_method: symbolReturnsData.prev_price_coverage > 0 ? 'prev_close_primary' : 'avg_price_fallback',
    };
    // perf:returns:YYYYMMDD — 날짜 기반 키 (48h TTL, 파레토 최적화용)
    const dateStr = trade_date ? trade_date.replace(/-/g, '') : new Date().toISOString().slice(0, 10).replace(/-/g, '');
    pipeline.set(returnsKey(dateStr), JSON.stringify(returnsRecord), 'EX', RETURNS_TTL);
    log('INFO', COMPONENT, 'RETURNS_WRITE', {
      trade_date,
      key: returnsKey(dateStr),
      position_count: symbolReturnsData.position_count,
      prev_price_coverage: symbolReturnsData.prev_price_coverage,
      sample_symbols: Object.keys(symbolReturnsData.returns).slice(0, 5),
      fix_reference: 'FIX-7-RETURNS-v1.3.1',
    });
  } else {
    log('WARN', COMPONENT, 'RETURNS_SKIP', {
      trade_date,
      raw_positions_count: rawPositions.length,
      reason: rawPositions.length === 0 ? 'no_raw_positions' : 'empty_returns_map',
    });
  }

  // ── [FIX-6 STRUCTURAL] ALIAS WRITE with Ownership Guard ──
  // Check ownership BEFORE writing to detect foreign overwrites
  let ownershipStatus = { owned: true, currentWriter: null, needsRecovery: false };
  if (aliasEnabled && (status === 'verified' || status === 'provisional')) {
    ownershipStatus = await checkAliasOwnership(redis);
    const aliasRecord = buildAliasRecord(perfRecord, diffRecord, ledger);
    pipeline.set(ALIAS_KEY, JSON.stringify(aliasRecord), 'EX', LIVE_TTL);
    if (ownershipStatus.needsRecovery) {
      log('WARN', COMPONENT, 'ALIAS_OWNERSHIP_RECOVERED', {
        key: ALIAS_KEY,
        previous_writer: ownershipStatus.currentWriter,
        recovered_with: WRITER_VERSION,
        message: 'Foreign writer detected. Overwriting with v4 data to restore ownership.',
      });
    } else {
      log('INFO', COMPONENT, 'ALIAS_WRITE', {
        key: ALIAS_KEY,
        writer_version: aliasRecord.writer_version,
        nav_usd: navUsd,
        status,
        diff_pct: diffRecord.diff_pct,
      });
    }
  } else if (aliasEnabled && status === 'quarantined') {
    // Quarantined: do NOT write to live key, log warning
    log('WARN', COMPONENT, 'ALIAS_SKIP_QUARANTINED', {
      key: ALIAS_KEY,
      reason: status_reason,
      msg: 'Quarantined data not written to champion:live:performance.',
    });
  }
  // Heartbeat
  pipeline.set(DST.HEARTBEAT, String(Date.now()), 'EX', TTL.HEARTBEAT);
  await pipeline.exec();
  const elapsed = Date.now() - cycleStart;
  log('INFO', COMPONENT, 'CYCLE_OK', {
    status,
    alias_enabled: aliasEnabled,
    alias_written: aliasEnabled && (status === 'verified' || status === 'provisional'),
    ownership_ok: ownershipStatus.owned,
    ownership_recovered: ownershipStatus.needsRecovery,
    nav_usd: Math.round(navUsd * 100) / 100,
    diff_pct: diffRecord.diff_pct,
    cutover_safe: diffRecord.cutover_safe,
    returns_written: rawPositions.length > 0,
    returns_position_count: symbolReturnsData.position_count,
    returns_prev_price_coverage: symbolReturnsData.prev_price_coverage,
    elapsed_ms: elapsed,
  });
  return {
    status: 'ok',
    tier: status,
    nav_usd: navUsd,
    diff_pct: diffRecord.diff_pct,
    alias: aliasEnabled,
    returns_written: rawPositions.length > 0,
    returns_count: symbolReturnsData.position_count,
    elapsed,
  };
}
// ─── Main Loop ───────────────────────────────────────────────────
async function main() {
  log('INFO', COMPONENT, 'STARTING', {
    version: WRITER_VERSION,
    interval_ms: INTERVAL_MS,
    shadow_ttl_sec: SHADOW_TTL,
    live_ttl_sec: LIVE_TTL,
    returns_ttl_sec: RETURNS_TTL,
    alias_flag: ALIAS_FLAG,
    ownership_guard: true,
    fix_reference: 'FIX-6-STRUCTURAL-v1.2.0 + FIX-7-RETURNS-v1.3.0',
  });
  const rawRedis = getRedis();
  const redis = createWriteGuard(rawRedis, COMPONENT);
  await new Promise((resolve) => {
    if (redis.status === 'ready') return resolve();
    redis.once('ready', resolve);
    setTimeout(() => resolve(), 10000);
  });
  log('INFO', COMPONENT, 'REDIS_CONNECTED', { status: redis.status });
  // Check initial alias flag state
  const initialFlag = await redis.get(ALIAS_FLAG);
  log('INFO', COMPONENT, 'ALIAS_FLAG_STATE', {
    flag: ALIAS_FLAG,
    value: initialFlag,
    alias_enabled: initialFlag === '1',
  });
  // [FIX-6 STRUCTURAL] Initial ownership check
  if (initialFlag === '1') {
    const initialOwnership = await checkAliasOwnership(redis);
    log('INFO', COMPONENT, 'INITIAL_OWNERSHIP_CHECK', {
      key: ALIAS_KEY,
      owned: initialOwnership.owned,
      current_writer: initialOwnership.currentWriter,
      needs_recovery: initialOwnership.needsRecovery,
    });
  }
  // Initial cycle
  await writeCycle(redis).catch(e =>
    log('ERROR', COMPONENT, 'INITIAL_CYCLE_FAILED', { error: e.message })
  );
  // Periodic loop
  const timer = setInterval(async () => {
    try {
      await writeCycle(redis);
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
