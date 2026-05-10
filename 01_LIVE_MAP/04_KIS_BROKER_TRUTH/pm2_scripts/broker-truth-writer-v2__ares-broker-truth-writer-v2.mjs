#!/usr/bin/env node
'use strict';

import { createRequire } from 'module';
const require = createRequire(import.meta.url);

const { nowIso, safeJsonParse, makeRedis, xaddJson, shaJson } = require('./lib-common.cjs');

const r = makeRedis();
const INTERVAL = parseInt(process.env.BROKER_TRUTH_INTERVAL_MS || '15000', 10);
const MIRROR_LEGACY = String(process.env.BROKER_TRUTH_MIRROR_LEGACY || 'true').toLowerCase() === 'true';
const EQUITY_SCHEMA_CONTRACT = process.env.EQUITY_SCHEMA_CONTRACT || 'ares.authoritative_equity.v42';

function parseMaybePosition(symbol, raw) {
  const j = safeJsonParse(raw, null);
  if (j && typeof j === 'object') {
    return {
      symbol,
      qty: Number(j.qty ?? j.economic_qty ?? j.trade_basis_qty ?? j.settled_qty ?? j.sellable_qty ?? 0),
      sellable_qty: Number(j.sellable_qty ?? j.ctrp_sellable ?? 0),
      market_value: Number(j.market_value ?? j.eval_amt ?? j.mv ?? 0),
      avg_px: Number(j.avg_px ?? j.avg_price ?? 0),
      last_px: Number(j.last_px ?? j.now_px ?? j.last_price ?? 0),
      source: 'kis:broker:positions'
    };
  }
  const n = Number(raw);
  return { symbol, qty: Number.isFinite(n) ? n : 0, sellable_qty: 0, market_value: 0, avg_px: 0, last_px: 0, source: 'kis:broker:positions:raw' };
}

async function readPositions() {
  const type = await r.type('kis:broker:positions');
  let positions = [];
  if (type === 'hash') {
    const h = await r.hgetall('kis:broker:positions');
    positions = Object.entries(h).map(([sym, raw]) => parseMaybePosition(sym, raw));
  }

  if (positions.length === 0) {
    const raw = await r.get('emarkos:v1:positions');
    const j = safeJsonParse(raw, null);
    if (j && j.positions && typeof j.positions === 'object') {
      positions = Object.entries(j.positions).map(([sym, p]) => ({
        symbol: sym,
        qty: Number(p.economic_qty ?? p.trade_basis_qty ?? p.settled_qty ?? 0),
        sellable_qty: Number(p.sellable_qty ?? p.ctrp_sellable ?? 0),
        market_value: Number(p.market_value ?? p.eval_amt ?? 0),
        avg_px: Number(p.avg_px ?? 0),
        last_px: Number(p.now_px ?? p.last_px ?? 0),
        source: 'emarkos:v1:positions'
      }));
    }
  }
  positions = positions.filter(p => p.symbol && Number.isFinite(p.qty));
  positions.sort((a, b) => a.symbol.localeCompare(b.symbol));
  return positions;
}

async function readCashComponents() {
  const compRaw = await r.get('truth:broker:components');
  const comp = safeJsonParse(compRaw, {});
  const usableRaw = await r.get('kis:cash:usable_usd');
  const usable = Number(usableRaw || 0);
  // [ARES-FIX-20260508-v2] total_equity_usd가 0이면 broker:ledger:latest에서 NAV 보완
  // broker:ledger:latest 구조: { nav_usd: ..., economic_nav: { total_equity_usd: ... } }
  let totalEquity = Number(comp.total_equity_usd ?? 0);
  if (!totalEquity || totalEquity <= 0) {
    try {
      const ledgerRaw = await r.get('broker:ledger:latest');
      const ledger = safeJsonParse(ledgerRaw, {});
      // nav_usd 최상위 또는 economic_nav.total_equity_usd 참조
      const navUsd = Number(ledger.nav_usd || (ledger.economic_nav && ledger.economic_nav.total_equity_usd) || 0);
      if (navUsd > 0) {
        totalEquity = navUsd;
      }
    } catch (_e) { /* non-fatal */ }
  }
  return {
    ts: nowIso(),
    cash_settled_usd: Number(comp.cash_settled_usd ?? 0),
    cash_unsettled_usd: Number(comp.cash_unsettled_usd ?? 0),
    cash_total_usd: Number(comp.cash_total_usd ?? usable ?? 0),
    usable_usd: Number.isFinite(usable) ? usable : 0,
    positions_mv_usd: Number(comp.positions_mv_usd ?? 0),
    total_equity_usd: totalEquity,
    source: comp.source || 'broker_truth_writer_v2'
  };
}

async function readOpenOrders() {
  const keys = [];
  let cursor = '0';
  do {
    const res = await r.scan(cursor, 'MATCH', 'order:open:*', 'COUNT', '500');
    cursor = res[0];
    keys.push(...res[1]);
  } while (cursor !== '0' && keys.length < 5000);
  return { ts: nowIso(), count: keys.length, keys: keys.slice(0, 200) };
}

async function cycle() {
  try {
    const positions = await readPositions();
    const cash = await readCashComponents();
    const openOrders = await readOpenOrders();
    const mv = positions.reduce((s, p) => s + (Number(p.market_value) || 0), 0);
    const summary = {
      ts: nowIso(),
      schema: 'ares.broker_truth.v2',
      positions_count: positions.length,
      positions_mv_usd_from_rows: Math.round(mv * 100) / 100,
      cash_total_usd: cash.cash_total_usd,
      usable_usd: cash.usable_usd,
      open_orders_count: openOrders.count,
      sha256: shaJson({ positions, cash, openOrders })
    };
    const positionsObj = { ...summary, positions };
    const cashObj = { ...cash, schema: 'ares.broker_cash.v2' };
    const equityTotal = Number(cash.total_equity_usd || cash.cash_total_usd || 0);
    const equityHealth = Number.isFinite(equityTotal) && equityTotal > 0 ? 'OK' : 'NO_EQUITY_SOURCE';

    // [ARES_PATCH_E6_20260508] SSOT unification: read execution_gross_exposure_target from
    // champion (single source of truth) instead of hard-coded 0.685.
    // Resolves 19%p discrepancy (champion 49.79% vs broker truth 68.5%) observed in 1h checkpoint.
    // Fallback to 0.685 only if champion key missing (preserves legacy behavior).
    let _ares_e6_target = 0.685;
    try {
      const _championTargetRaw = await r.get('champion:target:execution_gross_exposure');
      if (_championTargetRaw !== null && _championTargetRaw !== undefined) {
        const _v = Number(_championTargetRaw);
        if (Number.isFinite(_v) && _v > 0 && _v <= 1) {
          _ares_e6_target = Number(_v.toFixed(6));
        }
      }
    } catch (_e6_err) {
      // Non-fatal: keep fallback default
    }
    const health = {
      ok: positions.length > 0 && equityHealth === 'OK',
      state: positions.length > 0 ? equityHealth : 'NO_POSITIONS_SOURCE',
      ts: nowIso(),
      positions_count: positions.length,
      cash_total_usd: cash.cash_total_usd,
      total_equity_usd: equityTotal,
      open_orders_count: openOrders.count,
      equity_schema_contract: EQUITY_SCHEMA_CONTRACT,
      writer: 'ares-broker-truth-writer-v2'
    };
    const authoritativeEquity = {
      ts: nowIso(),
      schema: EQUITY_SCHEMA_CONTRACT,
      contract: EQUITY_SCHEMA_CONTRACT,
      writer: 'ares-broker-truth-writer-v2',
      health: equityHealth,
      ok: equityHealth === 'OK',
      total: Math.round(equityTotal * 100) / 100,
      total_usd: Math.round(equityTotal * 100) / 100,
      total_equity_usd: Math.round(equityTotal * 100) / 100,
      cash_total_usd: cash.cash_total_usd,
      usable_usd: cash.usable_usd,
      positions_mv_usd: cash.positions_mv_usd,
      positions_count: positions.length,
      open_orders_count: openOrders.count,
      // ═══════ [P2_US_SLEEVE_FIELDS_V1] US sleeve additive fields ═══════
      // P2 — execution basis = US_SLEEVE (사용자 결정). consumer 가 일관성 있게 사용 가능.
      // 모두 ADDITIVE. 기존 필드 (cash_total_usd, usable_usd, positions_mv_usd) 의미 변경 없음.
      us_equity_mv_usd: Number(positions.reduce((s, p) => s + (Number(p.market_value) || 0), 0).toFixed(2)),
      usd_cash: Number(cash.usable_usd ?? 0),
      us_sleeve_nav_usd: Number((positions.reduce((s, p) => s + (Number(p.market_value) || 0), 0) + Number(cash.usable_usd ?? 0)).toFixed(2)),
      us_sleeve_gross_exposure: (() => { const us = positions.reduce((s, p) => s + (Number(p.market_value) || 0), 0); const nav = us + Number(cash.usable_usd ?? 0); return nav > 0 ? Number((us / nav).toFixed(6)) : 0; })(),
      execution_gross_exposure_basis: 'US_SLEEVE',
      // [ARES_PATCH_E6_20260508] SSOT-driven target (champion key) with safe fallback
      execution_gross_exposure_target: _ares_e6_target,
      execution_gross_exposure_target_source: 'champion:target:execution_gross_exposure',
      execution_gross_exposure_soft_cap: 0.75,
      p2_field_marker: 'P2_US_SLEEVE_FIELDS_V1_E6'
    };

    await r.set('truth:broker:positions:v2', JSON.stringify(positionsObj));
    await r.set('truth:broker:cash:v2', JSON.stringify(cashObj));
    await r.set('truth:broker:open_orders:v2', JSON.stringify(openOrders));
    await r.set('truth:broker:last_sync:v2', nowIso());
    await r.set('truth:broker:health:v2', JSON.stringify(health));
    await r.set('ares:equity:authoritative', JSON.stringify(authoritativeEquity), 'EX', 60);
    await r.set('ares:equity:authoritative.health', equityHealth);
    // ═══════ [P4A_COMPONENTS_OWNER_MARKER] writer 가 truth:broker:components 직접 owner ═══════
    // P4-A — 근본 원인 해결: components writer 부재로 인한 STALE 누적 방지
    // writer 가 매 cycle 끝에 자신이 산출한 cash 데이터로 components 를 SET
    // TTL 120s 부여: writer 가 죽으면 자동 expire → stale fallback 방지
    try {
      const componentsObj = {
        ts: nowIso(),
        schema: 'ares.broker_components.v2',
        writer: 'ares-broker-truth-writer-v2',
        source: 'p4a_components_owner',
        cash_settled_usd: cash.cash_settled_usd,
        cash_unsettled_usd: cash.cash_unsettled_usd,
        cash_total_usd: cash.cash_total_usd,
        usable_usd: cash.usable_usd,
        positions_mv_usd: cash.positions_mv_usd,
        total_equity_usd: cash.total_equity_usd,
      };
      await r.set('truth:broker:components', JSON.stringify(componentsObj), 'EX', 120);
    } catch (e) {
      console.error(JSON.stringify({ts: nowIso(), level: 'WARN', component: 'broker-truth-writer-v2', evt: 'P4A_COMPONENTS_SET_FAILED', error: String(e?.message || e)}));
    }
    // STRUCTURAL-FIX-2026-04-30: canonical-to-legacy broker truth aliases for legacy preflight/observability.
    // Numeric aliases intentionally use usable_usd for buy-side preflight compatibility; TTL prevents stale cash reuse.
    const legacyCashUsd = Number.isFinite(Number(cashObj.usable_usd)) ? Number(cashObj.usable_usd) : Number(cashObj.cash_total_usd || 0);
    await r.set('broker:truth:equity', String(authoritativeEquity.total_equity_usd), 'EX', 120);
    // SSOT-DISABLED: managed exclusively by ares-cash-ssot-gatekeeper (TTL=90s)
    // await r.set('broker:truth:cash_usd', String(legacyCashUsd), 'EX', 120);
    // SSOT-DISABLED: managed exclusively by ares-cash-ssot-gatekeeper (TTL=90s)
    // await r.set('broker:truth:cash', String(legacyCashUsd), 'EX', 120);
    await r.set('broker:truth:last_sync', nowIso(), 'EX', 120);
    // STRUCTURAL-FIX-2026-04-30: compatibility aliases for legacy guardrail
    // consumers that still read numeric ares:equity:total plus timestamp.
    // The authoritative JSON remains the canonical source; these mirrors are TTL-bound
    // so stale equity cannot silently persist if this writer stops.
    await r.set('ares:equity:total', String(authoritativeEquity.total_equity_usd), 'EX', 120);
    await r.set('ares:equity:total:ts', authoritativeEquity.ts, 'EX', 120);
    await r.set('ares:equity:current', String(authoritativeEquity.total_equity_usd), 'EX', 120);
    await r.set('equity:broker:usd', String(authoritativeEquity.total_equity_usd));
    await r.set('equity:broker:ts', authoritativeEquity.ts);

    if (MIRROR_LEGACY) {
      await r.set('truth:broker:positions', JSON.stringify(positionsObj));
      await r.set('truth:broker:cash', JSON.stringify(cashObj));
      await r.set('truth:broker:open_orders', JSON.stringify(openOrders));
      await r.set('truth:broker:last_sync', nowIso());
      await r.set('truth:broker:health', JSON.stringify(health));
    }

    // [TIER-0 FIX 20260505] SSOT contract Tier-0 11 keys
    // Required by ares_daily_precheck.contract_compliance check.
    // Source data: cash (truth:broker:components), authoritativeEquity, kis:live:exchange_rate,
    //   kis:live:cash_usd_only (cash usd), truth:pnl:broker_unrealized_usd (unrealized).
    try {
      const TIER0_TTL = 120;
      const fxRaw = await r.get('kis:live:exchange_rate');
      const fxUsdKrw = Number(fxRaw || 0);
      const cashUsd = Number(cash.cash_total_usd || 0);
      const totalUsd = Number(authoritativeEquity.total_equity_usd || 0);
      const mvUsd = Number(cash.positions_mv_usd || 0);
      const totalKrw = (Number.isFinite(fxUsdKrw) && fxUsdKrw > 0) ? Math.round(totalUsd * fxUsdKrw * 100) / 100 : 0;
      const buyMarginUsd = Number(cash.usable_usd || 0);
      const unrealizedRaw = await r.get('truth:pnl:broker_unrealized_usd');
      const unrealizedDayUsd = Number(unrealizedRaw || 0);
      const snapshotMs = Date.now();
      const positionsCount = positions.length;

      // Tier-0 (truth:* preferred names per ares_daily_precheck.py contract definition)
      await r.set('truth:equity:broker_total_usd',          String(totalUsd),          'EX', TIER0_TTL);
      await r.set('truth:equity:broker_cash_usd',           String(cashUsd),           'EX', TIER0_TTL);
      await r.set('truth:equity:broker_total_deposit_usd',  String(totalUsd),          'EX', TIER0_TTL);
      await r.set('truth:equity:broker_market_value_usd',   String(mvUsd),             'EX', TIER0_TTL);
      await r.set('truth:equity:broker_total_krw',          String(totalKrw),          'EX', TIER0_TTL);
      await r.set('truth:equity:exchange_rate',             String(fxUsdKrw),          'EX', TIER0_TTL);
      await r.set('truth:equity:broker_buy_margin_usd',     String(buyMarginUsd),      'EX', TIER0_TTL);
      await r.set('truth:pnl:broker_unrealized_day_usd',    String(unrealizedDayUsd),  'EX', TIER0_TTL);
      await r.set('truth:ts:broker_snapshot_ms',            String(snapshotMs),        'EX', TIER0_TTL);
      await r.set('truth:positions:broker_count',           String(positionsCount),    'EX', TIER0_TTL);

      // Modern alias namespace (ares:equity:broker:*) per contract
      await r.set('ares:equity:broker:total_usd',           String(totalUsd),          'EX', TIER0_TTL);
      await r.set('ares:equity:broker:cash_usd',            String(cashUsd),           'EX', TIER0_TTL);
      await r.set('ares:equity:broker:market_value_usd',    String(mvUsd),             'EX', TIER0_TTL);
      await r.set('ares:equity:broker:total_krw',           String(totalKrw),          'EX', TIER0_TTL);
      await r.set('ares:equity:broker:fx_usd_krw',          String(fxUsdKrw),          'EX', TIER0_TTL);
    } catch (tierErr) {
      console.error(JSON.stringify({ ts: nowIso(), svc: 'broker-truth-writer-v2', level: 'error', event: 'tier0_write_failed', err: tierErr.message }));
    }

    // [SSOT-FIX-20260507] mirror broker_truth:positions:current for validator compatibility
    await r.set('broker_truth:positions:current', JSON.stringify(positionsObj), 'EX', 120);
    await xaddJson(r, 'ops:ledger:broker_truth', { summary, cash: cashObj, open_orders_count: openOrders.count });
    console.log(JSON.stringify({ ts: nowIso(), svc: 'broker-truth-writer-v2', level: 'info', event: 'cycle_ok', positions: positions.length, equity_total: authoritativeEquity.total_equity_usd, equity_health: equityHealth, equity_schema_contract: EQUITY_SCHEMA_CONTRACT, mirror_legacy: MIRROR_LEGACY }));
  } catch (e) {
    console.error(JSON.stringify({ ts: nowIso(), svc: 'broker-truth-writer-v2', level: 'error', event: 'cycle_failed', err: e.message, stack: e.stack }));
  }
}

cycle();
setInterval(cycle, INTERVAL);
