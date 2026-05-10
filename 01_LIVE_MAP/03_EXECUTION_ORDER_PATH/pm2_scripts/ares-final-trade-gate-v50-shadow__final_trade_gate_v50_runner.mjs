#!/usr/bin/env node
/**
 * ARES V5.0 Final Trade Gate Runner
 * =================================
 * Standalone PM2-friendly service wrapper around gate_core_v50.mjs.
 *
 * Default output mode is SHADOW to avoid overwriting live SSOT keys during first deployment.
 * Use FTG_V50_OUTPUT_MODE=active to publish to canonical final-trade-gate keys.
 *
 * @version 5.0.0-aegis-prod
 */

import Redis from 'ioredis';
import fs from 'fs';
import {
  DEFAULT_CONFIG,
  DEFAULT_KEYS,
  VERSION,
  evaluateRawGateV50,
  finalizeDebouncedEvaluationV50,
  applyRelaxDebounceV50,
  makeShadowOutputKeys,
  readInputsV50,
  writeGateOutputsV50,
} from './gate_core_v50.mjs';

const EVAL_INTERVAL_MS = parseEnvInt('FTG_EVAL_INTERVAL_MS', 15_000);
const MAX_CONSECUTIVE_ERRORS = parseEnvInt('FTG_MAX_CONSECUTIVE_ERRORS', 10);
const OUTPUT_MODE = String(process.env.FTG_V50_OUTPUT_MODE || 'shadow').toLowerCase() === 'active' ? 'active' : 'shadow';
const OUTPUT_KEYS = OUTPUT_MODE === 'active' ? DEFAULT_KEYS : makeShadowOutputKeys(DEFAULT_KEYS);

// [SHADOW-HALT-SPLIT-V2] Shadow 모드: LIVE invariant:halt 대신 shadow 전용 키 참조
const INPUT_KEYS = (() => {
  if (OUTPUT_MODE !== 'shadow') return DEFAULT_KEYS;
  const shadowHaltKey = process.env.FTG_SHADOW_INVARIANT_HALT_KEY || 'ares:invariant:halt:shadow';
  const clone = Object.assign({}, DEFAULT_KEYS);
  clone.INVARIANT_HALT = shadowHaltKey;
  console.log('[ares-ftg-v50][shadow-halt-split-v2] shadow INVARIANT_HALT:', shadowHaltKey);
  return clone;
})();

const config = buildConfigFromEnv();
const redis = getRedis();
let machineState = {};
let consecutiveErrors = 0;
let stopping = false;

console.log(`[ares-ftg-v50] starting version=${VERSION} output_mode=${OUTPUT_MODE} interval_ms=${EVAL_INTERVAL_MS}`);

process.once('SIGINT', () => shutdown('SIGINT'));
process.once('SIGTERM', () => shutdown('SIGTERM'));

runLoop().catch(err => {
  console.error('[ares-ftg-v50] fatal bootstrap error', err);
  process.exitCode = 1;
});

async function runLoop() {
  while (!stopping) {
    const started = Date.now();
    await tick().catch(err => handleTickError(err));
    const elapsed = Date.now() - started;
    await sleep(Math.max(250, EVAL_INTERVAL_MS - elapsed));
  }
}

async function tick() {
  const inputs = await readInputsV50(redis, INPUT_KEYS, config);
  const rawEval = evaluateRawGateV50(inputs, config, machineState);
  const previousGate = machineState.previousGate ?? rawEval.gate;
  const debounced = applyRelaxDebounceV50(previousGate, rawEval.gate, machineState, config);
  const finalEval = finalizeDebouncedEvaluationV50(rawEval, debounced, config);

  await writeGateOutputsV50(redis, finalEval, OUTPUT_KEYS, config, { nowMs: inputs.nowMs });
  machineState = finalEval.nextState ?? {};
  consecutiveErrors = 0;

  console.log(JSON.stringify({
    ts: new Date(inputs.nowMs).toISOString(),
    version: VERSION,
    outputMode: OUTPUT_MODE,
    gate: finalEval.gate,
    rawGate: finalEval.rawGate,
    riskScore: finalEval.riskScore,
    exposureMultiplier: finalEval.exposureMultiplier,
    debounced: finalEval.debounced,
    reasons: finalEval.reasons,
  }));
}

async function handleTickError(err) {
  consecutiveErrors += 1;
  console.error(`[ares-ftg-v50] tick error count=${consecutiveErrors}`, err?.stack || err);

  if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
    const nowMs = Date.now();
    const syntheticHalt = {
      version: VERSION,
      gate: 'HALT',
      rawGate: 'HALT',
      debounced: false,
      riskScore: 1,
      exposureMultiplier: 0,
      reasons: [`ftg_v50_consecutive_errors=${consecutiveErrors}`],
      diagnostics: { error: String(err?.message || err), consecutiveErrors },
      nextState: { ...machineState, previousGate: 'HALT', lastFinalGate: 'HALT', lastEvalTsMs: nowMs },
    };
    try {
      await writeGateOutputsV50(redis, syntheticHalt, OUTPUT_KEYS, config, { nowMs });
      machineState = syntheticHalt.nextState;
      console.error('[ares-ftg-v50] fail-closed synthetic HALT published');
    } catch (writeErr) {
      console.error('[ares-ftg-v50] failed to publish synthetic HALT', writeErr?.stack || writeErr);
    }
  }
}

function getRedis() {
  const envPath = process.env.ARES_REDIS_ENV || '/etc/ares/redis.env';
  let redisUrl = process.env.REDIS_URL;
  if (!redisUrl && fs.existsSync(envPath)) {
    const envContent = fs.readFileSync(envPath, 'utf8');
    const match = envContent.match(/^REDIS_URL=(.*)$/m);
    if (match) redisUrl = match[1].trim();
  }
  if (!redisUrl) throw new Error('REDIS_URL is not defined');
  const useTls = redisUrl.startsWith('rediss://');
  return new Redis(redisUrl, {
    tls: useTls ? { rejectUnauthorized: false } : undefined,
    maxRetriesPerRequest: 3,
    connectTimeout: 10000,
    lazyConnect: false,
  });
}

function buildConfigFromEnv() {
  return {
    ...DEFAULT_CONFIG,
    ignoreStaleLegacyTradeHalt: parseEnvBool('FTG_IGNORE_STALE_LEGACY_TRADE_HALT', DEFAULT_CONFIG.ignoreStaleLegacyTradeHalt),
    v4Required: parseEnvBool('FTG_V4_REQUIRED', DEFAULT_CONFIG.v4Required),
    v4RequiredFailureGate: process.env.FTG_V4_FAILURE_GATE || DEFAULT_CONFIG.v4RequiredFailureGate,
    llmReduceThreshold: parseEnvFloat('FTG_V4_LLM_REDUCE_THRESHOLD', DEFAULT_CONFIG.llmReduceThreshold),
    llmHoldThreshold: parseEnvFloat('FTG_V4_LLM_HOLD_THRESHOLD', DEFAULT_CONFIG.llmHoldThreshold),
    llmConfidenceMin: parseEnvFloat('FTG_V4_LLM_CONFIDENCE_MIN', DEFAULT_CONFIG.llmConfidenceMin),
    llmMaxAgeMs: parseEnvInt('FTG_V4_LLM_MAX_AGE_MS', DEFAULT_CONFIG.llmMaxAgeMs),
    monitorMaxAgeMs: parseEnvInt('FTG_V4_MONITOR_MAX_AGE_MS', DEFAULT_CONFIG.monitorMaxAgeMs),
    allowMonitorHardHalt: parseEnvBool('FTG_V4_ALLOW_MONITOR_HARD_HALT', DEFAULT_CONFIG.allowMonitorHardHalt),
    featureFluidExposure: parseEnvBool('FTG_V50_FLUID_EXPOSURE', DEFAULT_CONFIG.featureFluidExposure),
    featureKineticPreemption: parseEnvBool('FTG_V50_KINETIC_PREEMPTION', DEFAULT_CONFIG.featureKineticPreemption),
    featureTrapDetector: parseEnvBool('FTG_V50_TRAP_DETECTOR', DEFAULT_CONFIG.featureTrapDetector),
    relaxDebounceTicks: parseEnvInt('FTG_RELAX_DEBOUNCE_TICKS', DEFAULT_CONFIG.relaxDebounceTicks),
    publishTtlSec: parseEnvInt('FTG_GATE_TTL_SEC', DEFAULT_CONFIG.publishTtlSec),
  };
}

function parseEnvBool(name, fallback) {
  if (process.env[name] == null) return fallback;
  const token = String(process.env[name]).trim().toLowerCase();
  if (['1', 'true', 'yes', 'y', 'on'].includes(token)) return true;
  if (['0', 'false', 'no', 'n', 'off'].includes(token)) return false;
  return fallback;
}

function parseEnvInt(name, fallback) {
  const n = Number.parseInt(process.env[name], 10);
  return Number.isFinite(n) ? n : fallback;
}

function parseEnvFloat(name, fallback) {
  const n = Number.parseFloat(process.env[name]);
  return Number.isFinite(n) ? n : fallback;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function shutdown(signal) {
  stopping = true;
  console.log(`[ares-ftg-v50] shutdown signal=${signal}`);
  try { await redis.quit(); } catch { redis.disconnect(); }
}
