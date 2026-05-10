#!/usr/bin/env node
// ARES P1 Gate Authority Unification 2026-04-29
// This process is intentionally demoted to shadow/evaluation mode.
// Canonical authority is /home/ubuntu/ares_current/guards/final-trade-gate.mjs,
// which owns machine-facing gate state and final operational judgement.
// This daemon may evaluate the shared final tradeability formula, but it MUST NOT
// write canonical ops:final_tradeable keys or publish trading-enable control events.

import Redis from 'ioredis';
import { evaluateFinalTradeGate } from '/home/ubuntu/ares_current/lib/ares_final_gate_lib.mjs';

function resolveRedisUrl() {
  const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || '';
  if (!url) throw new Error('REDIS_URL or ARES_REDIS_URL is required');
  if (/localhost|127\.0\.0\.1/.test(url) && process.env.ALLOW_LOCAL_REDIS !== 'true') {
    throw new Error('Local Redis fallback forbidden; set ALLOW_LOCAL_REDIS=true only for offline tests');
  }
  return url;
}

const CFG = {
  redisUrl: resolveRedisUrl(),
  intervalMs: Number(process.env.FINAL_TRADE_GATE_INTERVAL_MS || 5000),
  ttlSec: Number(process.env.FINAL_TRADEABLE_SHADOW_TTL_SEC || process.env.FINAL_TRADEABLE_TTL_SEC || 30),
  shadowPrefix: process.env.ARES_SHADOW_FINAL_GATE_PREFIX || 'ops:final_tradeable:shadow:ares',
  canonicalAuthority: process.env.ARES_CANONICAL_FINAL_GATE || 'final-trade-gate',
  mode: 'shadow/evaluation',
  requireGoNoGo: process.env.FTG_REQUIRE_GONOGO === 'true',
  requireStructural: process.env.FTG_REQUIRE_STRUCTURAL !== 'false',
  requireBroker: process.env.FTG_REQUIRE_BROKER !== 'false',
  requireUniverse: process.env.FTG_REQUIRE_UNIVERSE !== 'false',
};

const redis = new Redis(CFG.redisUrl, {
  maxRetriesPerRequest: 3,
  retryStrategy: (times) => Math.min(times * 500, 5000),
  enableReadyCheck: true,
  connectionName: 'ares-final-trade-gate-shadow',
});

function log(level, msg, data = {}) {
  const safeCfg = { ...CFG, redisUrl: '[redacted]' };
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    svc: 'ares-final-trade-gate',
    level,
    msg,
    mode: CFG.mode,
    canonical_authority: CFG.canonicalAuthority,
    ...data,
    cfg: data.cfg === true ? safeCfg : undefined,
  });
  (level === 'error' || level === 'warn' ? process.stderr : process.stdout).write(line + '\n');
}

async function publishShadowFinalGate(evaluation) {
  const ts = evaluation.ts || new Date().toISOString();
  const shadow = {
    ...evaluation,
    schema: 'ares.final_trade_gate.shadow.v1',
    mode: CFG.mode,
    canonical_authority: CFG.canonicalAuthority,
    note: 'shadow_only_no_canonical_writes_no_ctrl_publish',
  };
  const payload = JSON.stringify(shadow);
  const val = evaluation.ok ? 'true' : 'false';
  await Promise.allSettled([
    redis.set(`${CFG.shadowPrefix}:value`, val, 'EX', CFG.ttlSec),
    redis.set(`${CFG.shadowPrefix}:reason`, evaluation.reason || '', 'EX', CFG.ttlSec),
    redis.set(`${CFG.shadowPrefix}:components`, payload, 'EX', CFG.ttlSec),
    redis.set(`${CFG.shadowPrefix}:updated_at`, ts, 'EX', CFG.ttlSec),
    redis.xadd(`${CFG.shadowPrefix}:audit`, 'MAXLEN', '~', '5000', '*', 'json', payload),
  ]);
}

async function cycle() {
  try {
    const ev = await evaluateFinalTradeGate(redis, {
      requireGoNoGo: CFG.requireGoNoGo,
      requireStructural: CFG.requireStructural,
      requireBroker: CFG.requireBroker,
      requireUniverse: CFG.requireUniverse,
    });
    await publishShadowFinalGate(ev);
    log(ev.ok ? 'info' : 'warn', 'shadow_final_tradeable_evaluated', {
      shadow_final_tradeable: ev.final_tradeable,
      reason: ev.reason,
      critical_components: ev.reasons.map(r => r.code),
    });
  } catch (e) {
    log('error', 'shadow_cycle_failed_no_canonical_write', { err: String(e?.stack || e) });
    const ev = {
      schema: 'ares.final_trade_gate.shadow.v1',
      ts: new Date().toISOString(),
      ts_ms: Date.now(),
      ok: false,
      final_tradeable: false,
      verdict: 'NO_GO',
      reason: 'SHADOW_FINAL_GATE_CYCLE_ERROR',
      reasons: [{ code: 'SHADOW_FINAL_GATE_CYCLE_ERROR', error: String(e?.message || e) }],
      components: {},
      mode: CFG.mode,
      canonical_authority: CFG.canonicalAuthority,
    };
    await publishShadowFinalGate(ev).catch(() => {});
  }
}

async function main() {
  redis.on('error', (e) => log('error', 'redis_error', { err: e.message }));
  log('info', 'starting_shadow_evaluator', { cfg: true });
  await cycle();
  setInterval(cycle, CFG.intervalMs);
}

main().catch(e => { log('error', 'fatal', { err: String(e?.stack || e) }); process.exit(1); });
