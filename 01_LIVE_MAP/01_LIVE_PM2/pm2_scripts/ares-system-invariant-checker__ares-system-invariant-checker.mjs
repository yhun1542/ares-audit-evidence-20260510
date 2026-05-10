// SOURCE: /home/ubuntu/ares_current/services/ares-system-invariant-checker.mjs
// SIZE: 14169 chars

/**
 * ares-system-invariant-checker.mjs  —  [PATCH 2026-04-21 v3]
 * ─────────────────────────────────────────────────────────────────
 * ARES 전체 시스템 독립 검증 워치독
 *
 * 30초마다 21개 invariant를 검증하고:
 *   - CRITICAL 위반 → halt REQUEST (severity=CRITICAL) 발행 + trading:enabled=false + 알림
 *   - HIGH 위반 → 경고 로그 + N회 연속 시 halt REQUEST (severity=HIGH) 발행
 *   - MEDIUM 위반 → 경고 로그만
 *
 * [PATCH 2026-04-21 v3] ROOT-CAUSE STRUCTURAL FIX:
 *   Previous implementation wrote `trade:halt=true` directly.
 *   That violated the Single Writer contract (halt-controller owns that key)
 *   and caused the 15s OPEN<->HALT oscillation observed in production:
 *     - checker writes trade:halt=true on every failed invariant tick
 *     - halt-controller / live-trading-kis delete the stale key
 *     - checker re-writes it on next tick
 *   Fix:
 *     (1) Publish halt requests to `ares:halt:request:invariant_<id>` keys
 *         with explicit severity, TTL, source metadata.
 *     (2) halt-controller aggregates these requests and is the sole
 *         authority that flips `trade:halt` / `ares:halt:active`.
 *     (3) Legacy direct write of `trade:halt` REMOVED.
 *     (4) Request keys auto-expire after 120s; checker re-asserts every
 *         cycle while invariant is still failing — so if checker dies,
 *         stale halt auto-clears (fail-open for checker, fail-closed
 *         for any still-failing real invariant because halt-controller
 *         will see the active request).
 *     (5) trading:enabled is NOT touched here anymore — kill-switch-authority
 *         owns that key; we only request, never override.
 *
 * 미등록 키 감지 → ares:contract:unregistered_keys에 기록
 * ─────────────────────────────────────────────────────────────────
 */

import { createClient } from 'redis';
import { INVARIANTS, KEY_REGISTRY, runAllInvariants, CONTRACT_VERSION, REGISTERED_KEY_COUNT } from './ares-system-contract.mjs';
import fs from 'fs';

// [STRUCTURAL P1-A 2026-04-21] invariants.yaml metadata SSOT
const INVARIANTS_YAML_PATH = process.env.INVARIANTS_YAML_PATH || '/home/ubuntu/config/invariants.yaml';
let _invariantMeta = {};
let _invariantMetaTs = 0;
function loadInvariantMeta() {
  try {
    const stat = fs.statSync(INVARIANTS_YAML_PATH);
    if (stat.mtimeMs === _invariantMetaTs) return _invariantMeta;
    const txt = fs.readFileSync(INVARIANTS_YAML_PATH, 'utf-8');
    // Minimal YAML parser (only what we need: id -> {field: value})
    const meta = {};
    let curId = null;
    let inInvariants = false;
    for (const rawLine of txt.split('\n')) {
      const line = rawLine.replace(/\s+$/, '');
      if (!line.trim() || line.trim().startsWith('#')) continue;
      if (line === 'invariants:') { inInvariants = true; continue; }
      if (!inInvariants) continue;
      if (!line.startsWith(' ') && line.endsWith(':')) { inInvariants = false; continue; }
      const m2 = line.match(/^  ([A-Z][A-Z0-9-]+):\s*$/);
      if (m2) { curId = m2[1]; meta[curId] = {}; continue; }
      const m3 = line.match(/^    (\w+):\s*(.+?)\s*$/);
      if (m3 && curId) {
        let v = m3[2];
        if (v === 'true') v = true;
        else if (v === 'false') v = false;
        else if (/^\d+$/.test(v)) v = parseInt(v);
        else v = v.replace(/^["']|["']$/g, '');
        meta[curId][m3[1]] = v;
      }
    }
    _invariantMeta = meta;
    _invariantMetaTs = stat.mtimeMs;
    console.log(`[INVARIANT-CHECKER] loaded ${Object.keys(meta).length} invariant metadata from yaml`);
  } catch (e) {
    console.warn(`[INVARIANT-CHECKER] yaml load failed: ${e.message} (using defaults)`);
  }
  return _invariantMeta;
}
function isInvariantActive(id, nowUtc = new Date()) {
  const meta = loadInvariantMeta()[id];
  const hours = (meta && meta.active_hours) || 'always';
  if (hours === 'always') return true;
  const day = nowUtc.getUTCDay();   // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const minutes = nowUtc.getUTCHours() * 60 + nowUtc.getUTCMinutes();
  if (hours === 'us_market')   return minutes >= 13*60+30 && minutes < 20*60;
  if (hours === 'pre_open')    return minutes >= 12*60    && minutes < 13*60+30;
  if (hours === 'post_close')  return minutes >= 20*60    && minutes < 22*60;
  return true;
}
function getInvariantThreshold(id) {
  const meta = loadInvariantMeta()[id];
  return (meta && meta.streak_threshold) || HIGH_CONSECUTIVE_THRESHOLD;
}
function isAutoHaltEnabled(id) {
  const meta = loadInvariantMeta()[id];
  if (meta && meta.auto_halt === false) return false;
  return true;
}


// [SSOT 전환 2026-04-24] SSOT에서 동적 로드 (ACL-safe URL)
import { getRedisUrl } from '/home/ubuntu/scripts/redis_ssot_loader.mjs';
const REDIS_URL = getRedisUrl();
const CHECK_INTERVAL = 30_000;  // 30초
const CRITICAL_AUTO_HALT = true;
const HIGH_CONSECUTIVE_THRESHOLD = Number(process.env.HIGH_CONSECUTIVE_THRESHOLD || 3);

// Halt request TTL — must be > 2 * CHECK_INTERVAL so one missed cycle
// does not release the halt, but bounded so a dead checker cannot
// permanently hold the system in halt.
const HALT_REQUEST_TTL_SEC = 120;

// [PATCH 2026-04-20] Option-3-A: exclude listed HIGH invariants from AUTO HALT (alert-only).
// Support comma-separated list; defensively split on commas *and* whitespace.
const HIGH_AUTOHALT_EXCLUDE = new Set(
  (process.env.INV_HIGH_AUTOHALT_EXCLUDE || '')
    .split(/[,\s]+/).map(s => s.trim()).filter(Boolean)
);
if (HIGH_AUTOHALT_EXCLUDE.size > 0) {
  console.log(`[INVARIANT-CHECKER] HIGH AUTO-HALT excluded for: ${[...HIGH_AUTOHALT_EXCLUDE].join(', ')}`);
}

let redis;
const highFailStreak = {};  // invariant id → consecutive fail count

async function connectRedis() {
  redis = createClient({ url: REDIS_URL, socket: { tls: true, rejectUnauthorized: true } });
  redis.on('error', (err) => console.error('[INVARIANT-CHECKER] Redis error:', err.message));
  await redis.connect();
  console.log(`[INVARIANT-CHECKER] Connected to Redis — contract v${CONTRACT_VERSION}, ${REGISTERED_KEY_COUNT} keys, ${INVARIANTS.length} invariants`);
}

/**
 * [PATCH v3] Publish a HALT REQUEST (not a direct write).
 * halt-controller aggregates these and owns the final flip.
 */
async function requestHalt(invariantId, severity, reason) {
  const requestKey = `ares:halt:request:invariant_${invariantId}`;
  try {
    console.error(`[INVARIANT-CHECKER] *** HALT REQUEST *** ${invariantId} sev=${severity} reason=${reason}`);
    await redis.set(requestKey, JSON.stringify({
      severity,
      message: reason,
      source: `invariant-checker:${invariantId}`,
      ts: new Date().toISOString(),
    }), { EX: HALT_REQUEST_TTL_SEC });
    await redis.xAdd('ares:contract:halt_events', '*', {
      ts: Date.now().toString(),
      invariant_id: invariantId,
      severity,
      reason,
      source: 'ares-system-invariant-checker',
      action: 'HALT_REQUEST',
      request_key: requestKey,
      request_ttl_sec: String(HALT_REQUEST_TTL_SEC),
    });
  } catch (e) {
    console.error('[INVARIANT-CHECKER] HALT request write failed:', e.message);
  }
}

/**
 * [PATCH v3] Clear a previously-asserted halt request once the invariant
 * passes again. This is what prevents stale halts from persisting and
 * causing the user-visible OPEN<->HALT oscillation.
 */
async function clearHaltRequest(invariantId) {
  const requestKey = `ares:halt:request:invariant_${invariantId}`;
  try {
    const existed = await redis.del(requestKey);
    if (existed) {
      console.log(`[INVARIANT-CHECKER] cleared HALT request for ${invariantId}`);
      await redis.xAdd('ares:contract:halt_events', '*', {
        ts: Date.now().toString(),
        invariant_id: invariantId,
        action: 'HALT_REQUEST_CLEARED',
        source: 'ares-system-invariant-checker',
      });
    }
  } catch (e) {
    console.error('[INVARIANT-CHECKER] clear halt request failed:', e.message);
  }
}

async function scanUnregisteredKeys() {
  const registeredKeys = new Set(Object.keys(KEY_REGISTRY));
  const criticalPrefixes = [
    'ares:equity:', 'ares:equity:broker:', 'emarkos:v1:equity', 'champion:equity_anchor',
    'truth:equity:', 'ares:equity:broker:', 'truth:champion:', 'trading:', 'trade:halt',
    'ssot:target:v2:', 'champion:target:', 'regime:final:',
    'ofg:gate', 'ofg:halt', 'ares:kill_switch'
  ];

  const unregistered = [];
  for await (const rawKey of redis.scanIterator({ MATCH: '*', COUNT: 500 })) {
    const key = String(rawKey);
    if (registeredKeys.has(key)) continue;
    if (criticalPrefixes.some(p => key.startsWith(p))) {
      unregistered.push(key);
    }
  }

  if (unregistered.length > 0) {
    console.warn(`[INVARIANT-CHECKER] UNREGISTERED critical keys: ${unregistered.length}`);
    await redis.set('ares:contract:unregistered_keys', JSON.stringify({
      ts: new Date().toISOString(),
      count: unregistered.length,
      keys: unregistered.slice(0, 50)
    }));
  }

  return unregistered;
}

async function checkOwnershipViolations() {
  try {
    const violations = await redis.xRange('ares:contract:violations', '-', '+', { COUNT: 10 });
    if (violations.length > 0) {
      const recent = violations.filter(v => {
        const ts = parseInt(v.message.ts);
        return Date.now() - ts < 300000;
      });
      if (recent.length > 0) {
        console.warn(`[INVARIANT-CHECKER] ${recent.length} ownership violations in last 5min`);
      }
    }
  } catch (e) { /* stream may not exist */ }
}

async function runCheck() {
  const startMs = Date.now();
  const results = await runAllInvariants(redis);
  const elapsed = Date.now() - startMs;

  let criticalFails = 0;
  let highFails = 0;
  let mediumFails = 0;
  const failures = [];
  // [PATCH v3] Track which invariants passed THIS cycle so we can clear
  // their stale halt requests.
  const passedIds = new Set();

  for (const r of results) {
    if (r.pass) {
      highFailStreak[r.id] = 0;
      passedIds.add(r.id);
      continue;
    }

    if (r.severity === 'CRITICAL') {
      criticalFails++;
      failures.push(r);
      console.error(`[INVARIANT-CHECKER] CRITICAL FAIL: ${r.id} ${r.name} — ${JSON.stringify(r.actual)}`);
    } else if (r.severity === 'HIGH') {
      highFails++;
      highFailStreak[r.id] = (highFailStreak[r.id] || 0) + 1;
      failures.push(r);
      console.warn(`[INVARIANT-CHECKER] HIGH FAIL: ${r.id} ${r.name} (streak: ${highFailStreak[r.id]}) — ${JSON.stringify(r.actual)}`);
    } else {
      mediumFails++;
      console.warn(`[INVARIANT-CHECKER] MEDIUM FAIL: ${r.id} ${r.name} — ${JSON.stringify(r.actual)}`);
    }
  }

  // [PATCH v3] Halt requests (CRITICAL)
  if (CRITICAL_AUTO_HALT) {
    for (const r of failures.filter(f => f.severity === 'CRITICAL')) {
      await requestHalt(r.id, 'CRITICAL',
        `CRITICAL invariant violation: ${r.id} ${r.name} ${JSON.stringify(r.actual).slice(0, 120)}`);
    }
  }

  // [PATCH v3] Halt requests (HIGH consecutive), respecting the exclusion list
  for (const r of failures.filter(f => f.severity === 'HIGH')) {
    // [STRUCTURAL P1-A] yaml metadata-aware HIGH halt
    if (!isInvariantActive(r.id)) {
      console.log(`[INVARIANT-CHECKER] HIGH ${r.id} skipped (outside active_hours) — streak=${highFailStreak[r.id]}`);
      highFailStreak[r.id] = 0;
      await clearHaltRequest(r.id);
      continue;
    }
    if (HIGH_AUTOHALT_EXCLUDE.has(r.id) || !isAutoHaltEnabled(r.id)) {
      console.warn(`[INVARIANT-CHECKER] HIGH ${r.id} auto-halt SKIPPED (alert-only) — streak=${highFailStreak[r.id]}`);
      await clearHaltRequest(r.id);
      continue;
    }
    const threshold = getInvariantThreshold(r.id);
    if (highFailStreak[r.id] >= threshold) {
      await requestHalt(r.id, 'HIGH',
        `HIGH invariant ${r.id} failed ${highFailStreak[r.id]}/${threshold} consecutive times`);
    }
  }

  // [PATCH v3] Clear halt requests for invariants that passed THIS cycle.
  // Without this, halt requests live until TTL and FTG stays HALT even though
  // the actual condition has resolved — this was the dominant flapping driver.
  for (const id of passedIds) {
    await clearHaltRequest(id);
  }

  // Write check result
  const summary = {
    ts: new Date().toISOString(),
    elapsed_ms: elapsed,
    total: results.length,
    pass: results.filter(r => r.pass).length,
    fail: results.filter(r => !r.pass).length,
    critical_fails: criticalFails,
    high_fails: highFails,
    medium_fails: mediumFails,
    contract_version: CONTRACT_VERSION,
    failures: failures.map(f => ({ id: f.id, name: f.name, severity: f.severity, actual: f.actual }))
  };

  await redis.set('ares:contract:invariant_check:latest', JSON.stringify(summary));
  await redis.xAdd('ares:contract:invariant_check:history', '*', {
    ts: Date.now().toString(),
    pass: summary.pass.toString(),
    fail: summary.fail.toString(),
    critical: criticalFails.toString(),
    elapsed_ms: elapsed.toString()
  }, { TRIM: { strategy: 'MAXLEN', strategyModifier: '~', threshold: 50000 } });

  const status = criticalFails > 0 ? 'CRITICAL' : highFails > 0 ? 'WARN' : 'OK';
  console.log(`[INVARIANT-CHECKER] ${status} — ${summary.pass}/${summary.total} pass, ${criticalFails}C/${highFails}H/${mediumFails}M fail, ${elapsed}ms`);

  return summary;
}

async function main() {
  await connectRedis();

  console.log(`[INVARIANT-CHECKER] Starting v3-patch — ${INVARIANTS.length} invariants, check every ${CHECK_INTERVAL / 1000}s`);
  console.log(`[INVARIANT-CHECKER] CRITICAL auto-halt: ${CRITICAL_AUTO_HALT}, HIGH consecutive threshold: ${HIGH_CONSECUTIVE_THRESHOLD}`);
  console.log(`[INVARIANT-CHECKER] Halt requests TTL: ${HALT_REQUEST_TTL_SEC}s (via ares:halt:request:invariant_*)`);

  await runCheck();
  await scanUnregisteredKeys();
  await checkOwnershipViolations();

  setInterval(async () => {
    try { await runCheck(); }
    catch (e) { console.error('[INVARIANT-CHECKER] Check cycle error:', e.message); }
  }, CHECK_INTERVAL);

  setInterval(async () => {
    try { await scanUnregisteredKeys(); await checkOwnershipViolations(); }
    catch (e) { console.error('[INVARIANT-CHECKER] Scan error:', e.message); }
  }, 300_000);
}

main().catch(e => {
  console.error('[INVARIANT-CHECKER] Fatal:', e);
  process.exit(1);
});
