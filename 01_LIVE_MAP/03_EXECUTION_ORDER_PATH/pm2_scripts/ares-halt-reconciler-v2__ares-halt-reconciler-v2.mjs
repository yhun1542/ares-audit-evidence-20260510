#!/usr/bin/env node
'use strict';

import { createRequire } from 'module';
const require = createRequire(import.meta.url);

const { nowIso, safeJsonParse, makeRedis, xaddJson, isTruthyHalt, isTrue } = require('./lib-common.cjs');

const r = makeRedis();
const INTERVAL = parseInt(process.env.HALT_RECONCILE_INTERVAL_MS || '5000', 10);
const DIRECT_WRITE = String(process.env.HALT_RECONCILER_DIRECT_WRITE || 'false').toLowerCase() === 'true';
const IGNORE_STALE_LEGACY_TRADE_HALT = String(process.env.HALT_RECONCILER_IGNORE_STALE_LEGACY_TRADE_HALT || 'true').toLowerCase() !== 'false';
const DELETE_STALE_LEGACY_TRADE_HALT = String(process.env.HALT_RECONCILER_DELETE_STALE_LEGACY_TRADE_HALT || 'true').toLowerCase() !== 'false';

async function publishTradingRequest(enabled, reason) {
  const payload = { ts: nowIso(), source: 'ares-halt-reconciler-v2', enabled, reason };
  await r.publish('ctrl:request:trading_enabled', JSON.stringify(payload));
  await r.set('ops:halt:reconcile_action', JSON.stringify(payload));
  if (DIRECT_WRITE && enabled === false) {
    await r.set('trading:enabled', 'false');
  }
}

async function cycle() {
  try {
    const tradeHaltRaw = await r.get('trade:halt');
    const tradeHaltTtl = await r.ttl('trade:halt');
    const tradeHaltReason = await r.get('trade:halt:reason');
    const tradeHaltTs = await r.get('trade:halt:ts');
    const tradeHaltDetails = await r.get('trade:halt:details');
    const tradingEnabled = isTrue(await r.get('trading:enabled'));
    const finalTradeableRaw = await r.get('ops:final_tradeable');
    const finalTradeablePresent = finalTradeableRaw !== null;
    const finalTradeable = finalTradeablePresent ? isTrue(finalTradeableRaw) : true;
    const finalReason = await r.get('ops:final_tradeable:reason');
    const manualHaltRaw = await r.get('ops:manual_halt');
    const ssotReq = safeJsonParse(await r.get('ops:halt:request:ssot_closure'), null);
    const structuralReq = safeJsonParse(await r.get('ops:halt:request:structural_integrity'), null);

    const truthyTradeHalt = isTruthyHalt(tradeHaltRaw);
    const tradeHaltHasMetadata = Boolean(tradeHaltReason || tradeHaltTs || tradeHaltDetails);
    const staleLegacyTradeHalt = Boolean(truthyTradeHalt && IGNORE_STALE_LEGACY_TRADE_HALT && !tradeHaltHasMetadata && !(Number.isFinite(tradeHaltTtl) && tradeHaltTtl > 0));
    const effectiveTradeHalt = Boolean(truthyTradeHalt && !staleLegacyTradeHalt);

    if (staleLegacyTradeHalt && DELETE_STALE_LEGACY_TRADE_HALT) {
      const quarantine = { ts: nowIso(), source: 'ares-halt-reconciler-v2', action: 'delete_stale_legacy_trade_halt', raw: tradeHaltRaw, ttl: tradeHaltTtl, reason: 'metadata_absent_and_no_expiry' };
      await r.set('trade:halt:quarantine:last', JSON.stringify(quarantine), 'EX', 86400);
      await xaddJson(r, 'trade:halt:quarantine:audit', quarantine);
      await r.del('trade:halt');
    }

    const haltActive = effectiveTradeHalt || isTruthyHalt(manualHaltRaw) || Boolean(ssotReq) || Boolean(structuralReq);
    const reasons = [];
    if (effectiveTradeHalt) reasons.push(`trade_halt:${tradeHaltRaw}:${tradeHaltReason || ''}`);
    if (staleLegacyTradeHalt) reasons.push(`stale_legacy_trade_halt_ignored:${tradeHaltRaw}`);
    if (isTruthyHalt(manualHaltRaw)) reasons.push(`manual_halt:${manualHaltRaw}`);
    if (ssotReq) reasons.push(`ssot_request:${ssotReq.reason || 'unknown'}`);
    if (structuralReq) reasons.push(`structural_request:${structuralReq.reason || 'unknown'}`);
    if (!finalTradeable) reasons.push(`final_tradeable_false:${finalReason || ''}`);

    const shouldDisable = tradingEnabled && (haltActive || !finalTradeable);
    if (shouldDisable) {
      await publishTradingRequest(false, reasons.join(';') || 'halt_reconciler_disable');
    }

    const state = {
      ts: nowIso(),
      schema: 'ares.halt_reconciler.v2',
      halt_active: haltActive,
      trading_enabled: tradingEnabled,
      final_tradeable: finalTradeable,
      final_tradeable_present: finalTradeablePresent,
      should_disable: shouldDisable,
      direct_write: DIRECT_WRITE,
      reasons,
      truthy_trade_halt_raw: tradeHaltRaw,
      trade_halt_ttl: tradeHaltTtl,
      delete_stale_legacy_trade_halt: DELETE_STALE_LEGACY_TRADE_HALT,
      trade_halt_has_metadata: tradeHaltHasMetadata,
      stale_legacy_trade_halt_ignored: staleLegacyTradeHalt,
      effective_trade_halt: effectiveTradeHalt,
      patch: 'manus-stale-legacy-halt-self-heal-20260430',
      writer: 'ares-halt-reconciler-v2'
    };
    await r.set('ops:halt:state', JSON.stringify(state));
    await xaddJson(r, 'ops:halt:audit', state);
    console.log(JSON.stringify({ ts: nowIso(), svc: 'halt-reconciler-v2', level: shouldDisable ? 'warn' : 'info', event: 'cycle', halt_active: haltActive, should_disable: shouldDisable, reasons }));
  } catch (e) {
    console.error(JSON.stringify({ ts: nowIso(), svc: 'halt-reconciler-v2', level: 'error', event: 'cycle_failed', err: e.message }));
  }
}

cycle();
setInterval(cycle, INTERVAL);
