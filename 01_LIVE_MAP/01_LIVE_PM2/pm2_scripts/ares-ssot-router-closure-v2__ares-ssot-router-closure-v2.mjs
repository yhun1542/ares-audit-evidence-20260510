#!/usr/bin/env node
'use strict';

import { createRequire } from 'module';
const require = createRequire(import.meta.url);

const { nowIso, safeJsonParse, makeRedis, xaddJson, isTrue, isTruthyHalt } = require('./lib-common.cjs');

const r = makeRedis();
const INTERVAL = parseInt(process.env.SSOT_CLOSURE_INTERVAL_MS || '15000', 10);
const HARD_FAIL_IDENTITY = String(process.env.SSOT_CLOSURE_HARD_FAIL_ON_IDENTITY_DRIFT || 'false').toLowerCase() === 'true';

async function getPolicyUniverse() {
  const t = await r.type('policy:universe:symbols');
  if (t === 'set') return new Set(await r.smembers('policy:universe:symbols'));
  const raw = await r.get('policy:universe:symbols:json');
  const arr = safeJsonParse(raw, []);
  return new Set(Array.isArray(arr) ? arr : []);
}

function extractPositions(target) {
  const t = target && target.targets ? target.targets : {};
  const p = t.positions || t.weights || t.allocations || [];
  // MANUS_IGNORE_RESIDUAL_HOLD_ONLY_20260429:
  // ssot current may include broker book visibility rows tagged residual_holding
  // with trade_policy=hold_only and zero weight. They are not active targets and
  // must not be counted as unknown policy-universe symbols or gross exposure.
  if (Array.isArray(p)) return p
    .filter(x => {
      const w = Number(x.w ?? x.weight ?? x.target_weight ?? 0);
      const tag = String(x.tag || '').toLowerCase();
      const tp = String(x.trade_policy || '').toLowerCase();
      return !(Math.abs(w) === 0 && (tag === 'residual_holding' || tp === 'hold_only'));
    })
    .map(x => ({ symbol: x.symbol || x.ticker, w: Number(x.w ?? x.weight ?? x.target_weight ?? 0) }))
    .filter(x => x.symbol);
  if (p && typeof p === 'object') return Object.entries(p).map(([symbol, w]) => ({ symbol, w: Number(w) }));
  return [];
}

async function cycle() {
  const reasons = [];
  const observations = [];
  let hardFail = false;
  try {
    const currentRaw = await r.get('ssot:target:v2:current');
    const lastGoodRaw = await r.get('ssot:target:v2:last_good');
    const execSrcRaw = await r.get('ssot:target:v2:exec_src');
    const engineSrcRaw = await r.get('ssot:target:v2:engine_src');
    const current = safeJsonParse(currentRaw, null);
    const lastGood = safeJsonParse(lastGoodRaw, null);
    const execSrc = safeJsonParse(execSrcRaw, null);
    const engineSrc = safeJsonParse(engineSrcRaw, null);

    if (!current) { reasons.push('missing_or_invalid_ssot_current'); hardFail = true; }
    if (!lastGood) reasons.push('missing_or_invalid_ssot_last_good');
    if (!execSrc) reasons.push('missing_or_invalid_exec_src');
    if (!engineSrc) reasons.push('missing_or_invalid_engine_src');

    const runtimeChampion = await r.get('champion:version');
    const activeVersion = await r.get('strategy:active_version');
    const policyUniverse = await getPolicyUniverse();
    const positions = extractPositions(current || {});
    const unknownSymbols = positions.map(p => p.symbol).filter(s => !policyUniverse.has(s));
    const gross = positions.reduce((s, p) => s + Math.abs(Number(p.w) || 0), 0);

    if (policyUniverse.size < 10) { reasons.push(`policy_universe_too_small:${policyUniverse.size}`); hardFail = true; }
    if (positions.length === 0) reasons.push('no_positions_in_ssot_current');
    if (unknownSymbols.length > 0) reasons.push(`unknown_symbols:${unknownSymbols.slice(0, 10).join(',')}`);
    if (gross > 1.5) { reasons.push(`gross_too_high:${gross.toFixed(4)}`); hardFail = true; }

    const meta = current?.targets?.meta || current?.meta || {};
    const ssotChampion = meta.champion_version || meta.champion || '';
    const engineNote = meta.engine_router_note || '';
    const identityDrift = Boolean(ssotChampion && runtimeChampion && !String(ssotChampion).includes(String(runtimeChampion)) && !String(runtimeChampion).includes(String(ssotChampion)));
    const runtimeStrategyAligned = Boolean(runtimeChampion && activeVersion && String(runtimeChampion) === String(activeVersion));
    if (identityDrift) {
      reasons.push(`identity_drift:runtime=${runtimeChampion}:ssot=${ssotChampion}:note=${engineNote}`);
      // MANUS_FULL_LIVE_DEADLOCK_FIX_20260429: keep drift observable, but avoid fail-closed
      // deadlock when live runtime and strategy_active_version are aligned and only SSOT meta is stale.
      if (HARD_FAIL_IDENTITY && !runtimeStrategyAligned) hardFail = true;
    }

    const tradeHalt = isTruthyHalt(await r.get('trade:halt'));
    const tradingEnabled = isTrue(await r.get('trading:enabled'));
    // MANUS_STRUCTURAL_FIX_20260430:
    // trade:halt and trading:enabled are downstream/external safety states, not
    // SSOT data-quality failures.  Treat them as observations only to prevent a
    // self-reinforcing loop: legacy halt -> ssot_closure hard fail -> halt request
    // -> halt remains forever.
    if (tradeHalt) observations.push('external_trade_halt_observed');
    if (!tradingEnabled) observations.push('trading_not_enabled_observed');

    const ok = !hardFail;
    const state = {
      ts: nowIso(),
      schema: 'ares.ssot_closure.v2',
      ok,
      hard_fail: hardFail,
      reasons,
      policy_universe_count: policyUniverse.size,
      ssot_positions_count: positions.length,
      ssot_gross: Number(gross.toFixed(8)),
      runtime_champion: runtimeChampion,
      strategy_active_version: activeVersion,
      ssot_champion: ssotChampion,
      engine_router_note: engineNote,
      identity_drift: identityDrift,
      observations,
      external_trade_halt_observed: tradeHalt,
      trading_enabled: tradingEnabled,
      patch: 'manus-ssot-no-self-halt-loop-20260430',
      writer: 'ares-ssot-router-closure-v2'
    };

    await r.set('ops:ssot:closure:state', JSON.stringify(state));
    await r.set('ops:final_tradeable:input:ssot', JSON.stringify({ ts: nowIso(), ok, reason: reasons.join(';') || 'OK' }));
    if (!ok) {
      await r.set('ops:halt:request:ssot_closure', JSON.stringify({ ts: nowIso(), source: 'ssot_closure', reason: reasons.join(';') }));
    } else {
      await r.del('ops:halt:request:ssot_closure');
    }
    await xaddJson(r, 'ops:ssot:closure:audit', state);
    console.log(JSON.stringify({ ts: nowIso(), svc: 'ssot-router-closure-v2', level: ok ? 'info' : 'warn', event: 'cycle', ok, reasons }));
  } catch (e) {
    const state = { ts: nowIso(), schema: 'ares.ssot_closure.v2', ok: false, hard_fail: true, reasons: ['exception:' + e.message], writer: 'ares-ssot-router-closure-v2' };
    await r.set('ops:ssot:closure:state', JSON.stringify(state));
    await r.set('ops:final_tradeable:input:ssot', JSON.stringify({ ts: nowIso(), ok: false, reason: e.message }));
    console.error(JSON.stringify({ ts: nowIso(), svc: 'ssot-router-closure-v2', level: 'error', event: 'cycle_failed', err: e.message }));
  }
}

cycle();
setInterval(cycle, INTERVAL);
