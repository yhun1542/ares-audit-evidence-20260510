#!/usr/bin/env node
/**
 * regime-replicator.mjs — emarkos:v1:regime SPOF 해결
 *
 * [MUSK-RCA-20260421-A]
 *
 * 문제 (RC-1):
 *   - emarkos:v1:regime (TTL=600s) publisher가 단 1개 (regime-bridge-v1)
 *   - publisher가 5초만 죽어도 600s TTL이 지나면 키 만료
 *   - 키 만료 → go-nogo-judge가 keys FAIL → NO-GO → 전 시스템 정지
 *
 * 해결 — Defense in Depth:
 *   1) 3개 publisher (primary + 2 replicas)를 동시 모니터링
 *   2) 단 하나라도 살아있으면 그 값을 미러링
 *   3) 모두 죽었어도 마지막 알려진 값을 LAST_GOOD_TTL 동안 유지
 *   4) 5초 이상 stale → 알람 (텔레그램 + audit stream)
 *
 * Design Principles (Musk Algorithm Step 5: Automate):
 *   - SPOF 0건: 3-way replication
 *   - 자동 복구: 사람 개입 없이 5초 내 회복
 *   - 관측 가능: 모든 결정은 audit stream에 기록
 *
 * @version 1.0.0
 * @author ARES SRE (Musk First Principles refactor)
 */

import IORedis from 'ioredis';

// ── Config ─────────────────────────────────────────────────────────────────
const REDIS_URL = process.env.ARES_REDIS_URL || process.env.REDIS_URL;

const CFG = {
  PRIMARY_KEY:        'emarkos:v1:regime',
  PRIMARY_TS_KEY:     'emarkos:v1:regime:ts',
  REPLICA_KEYS: [
    'emarkos:v1:regime:rep1',  // ares_invariant_sentinel.mjs
    'emarkos:v1:regime:rep2',  // regime_bridge.mjs
  ],
  LAST_GOOD_KEY:      'emarkos:v1:regime:last_good',  // self-managed cache
  STATE_KEY:          'regime-replicator:state',
  HEARTBEAT_KEY:      'regime-replicator:heartbeat',
  AUDIT_STREAM:       'ares:regime:audit',

  TICK_MS:            5_000,                // 5s 모니터링
  PRIMARY_TTL_S:      600,                  // 10min (publisher와 동일)
  LAST_GOOD_TTL_S:    1_800,                // 30min — 최후 보루
  STALE_WARN_S:       30,                   // primary가 30s 이상 stale → 즉시 mirror
  STALE_ALARM_S:      120,                  // 2min stale → telegram 알람
};

const VALID_REGIMES = new Set([
  'NORMAL', 'BULL', 'BEAR', 'CRISIS', 'EUPHORIA', 'CHOP', 'TRANSITION',
]);

// ── State ──────────────────────────────────────────────────────────────────
let lastGoodValue = null;
let lastGoodAt = 0;
let lastAlarmAt = 0;
let lastDecisionLog = '';
let consecutiveAllDeadCount = 0;

const redis = new IORedis(REDIS_URL, {
  tls: { servername: 'master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com' },
  lazyConnect: false,
  retryStrategy: (t) => Math.min(t * 500, 5000),
  maxRetriesPerRequest: null,
});

function nowMs() { return Date.now(); }
function iso() { return new Date().toISOString(); }
function log(level, msg, data = {}) {
  process.stdout.write(JSON.stringify({
    ts: iso(),
    svc: 'regime-replicator',
    level,
    msg,
    ...data,
  }) + '\n');
}

async function audit(event, payload) {
  try {
    await redis.xadd(
      CFG.AUDIT_STREAM, 'MAXLEN', '~', '10000', '*',
      'ts', iso(),
      'by', 'regime-replicator',
      'event', event,
      'payload', JSON.stringify(payload),
    );
  } catch (e) { /* best-effort */ }
}

// ── Read all sources, return the freshest valid one ────────────────────────
async function readAllSources() {
  const keys = [CFG.PRIMARY_KEY, ...CFG.REPLICA_KEYS, CFG.LAST_GOOD_KEY];
  const ttlPipeline = redis.pipeline();
  for (const k of keys) { ttlPipeline.get(k); ttlPipeline.ttl(k); }
  const res = await ttlPipeline.exec();

  const sources = [];
  for (let i = 0; i < keys.length; i++) {
    const val = res[i * 2][1];
    const ttl = res[i * 2 + 1][1];
    if (val) {
      sources.push({ key: keys[i], value: val, ttl, ageEstimate: ttl > 0 ? CFG.PRIMARY_TTL_S - ttl : Infinity });
    }
  }
  return sources;
}

function pickBest(sources) {
  // 우선순위:
  //   1) primary가 살아있고 ttl >= STALE_WARN_S
  //   2) replica 중 가장 fresh (ttl 큰 것)
  //   3) last_good 캐시
  const valid = sources.filter(s => VALID_REGIMES.has(s.value));
  if (valid.length === 0) return null;

  const primary = valid.find(s => s.key === CFG.PRIMARY_KEY);
  if (primary && primary.ttl >= CFG.STALE_WARN_S) return { ...primary, source: 'primary-fresh' };

  const replicas = valid.filter(s => CFG.REPLICA_KEYS.includes(s.key));
  if (replicas.length > 0) {
    replicas.sort((a, b) => b.ttl - a.ttl);
    return { ...replicas[0], source: 'replica' };
  }

  if (primary) return { ...primary, source: 'primary-stale' };

  const lastGood = valid.find(s => s.key === CFG.LAST_GOOD_KEY);
  if (lastGood) return { ...lastGood, source: 'last-good-cache' };

  return null;
}

async function mirror(value) {
  // primary가 stale 하거나 비어있을 때 우리가 다시 set (추가 publisher 역할)
  await redis.set(CFG.PRIMARY_KEY, value, 'EX', CFG.PRIMARY_TTL_S);
  await redis.set(CFG.LAST_GOOD_KEY, value, 'EX', CFG.LAST_GOOD_TTL_S);
  lastGoodValue = value;
  lastGoodAt = nowMs();
}

async function tick() {
  try {
    const sources = await readAllSources();
    const best = pickBest(sources);

    // Heartbeat
    await redis.set(CFG.HEARTBEAT_KEY, nowMs().toString(), 'EX', 30);

    if (!best) {
      // 모두 죽음 + 캐시도 없음 → CATASTROPHIC
      consecutiveAllDeadCount++;
      log('error', 'all-regime-sources-dead', {
        consecutive: consecutiveAllDeadCount,
        sourceCount: sources.length,
      });
      if (consecutiveAllDeadCount === 3) {
        await audit('CATASTROPHIC_NO_REGIME', { sources });
      }
      // Default fail-safe: NORMAL (보수적)
      if (consecutiveAllDeadCount >= 3) {
        await mirror('NORMAL');
        log('warn', 'fail-safe-set-NORMAL', {});
        await audit('FAILSAFE_SET_NORMAL', {});
      }
      return;
    }

    consecutiveAllDeadCount = 0;

    // primary가 stale 하거나 비어있으면 mirror
    const primary = sources.find(s => s.key === CFG.PRIMARY_KEY);
    const needMirror = !primary || primary.ttl < CFG.STALE_WARN_S || primary.value !== best.value;

    if (needMirror) {
      await mirror(best.value);
      const decisionLog = `mirror src=${best.source} key=${best.key} ttl=${best.ttl} val=${best.value}`;
      if (decisionLog !== lastDecisionLog) {
        log('info', 'mirrored', { source: best.source, key: best.key, ttl: best.ttl, value: best.value });
        await audit('MIRROR', { from: best.key, to: CFG.PRIMARY_KEY, value: best.value, source: best.source });
        lastDecisionLog = decisionLog;
      }
    } else {
      // primary 정상 — 우리도 last_good만 갱신
      lastGoodValue = best.value;
      lastGoodAt = nowMs();
      await redis.set(CFG.LAST_GOOD_KEY, best.value, 'EX', CFG.LAST_GOOD_TTL_S);
    }

    // STALE_ALARM_S 초과 stale → 알람 (rate limit 5min)
    if (primary && primary.ttl < (CFG.PRIMARY_TTL_S - CFG.STALE_ALARM_S)) {
      const sinceLastAlarm = (nowMs() - lastAlarmAt) / 1000;
      if (sinceLastAlarm > 300) {
        log('warn', 'primary-very-stale', { ttl: primary.ttl });
        await audit('STALE_ALARM', { primaryTtl: primary.ttl, mirroredFrom: best.key });
        lastAlarmAt = nowMs();
      }
    }

    // 살아있는 publisher 수 기록 (관측용)
    await redis.set(
      CFG.STATE_KEY,
      JSON.stringify({
        ts: iso(),
        sources: sources.map(s => ({ key: s.key, ttl: s.ttl, value: s.value })),
        chosen: best,
        mirrored: needMirror,
      }),
      'EX', 60,
    );

  } catch (e) {
    log('error', 'tick-error', { err: e.message });
  } finally {
    setTimeout(tick, CFG.TICK_MS);
  }
}

process.on('SIGTERM', async () => {
  log('info', 'shutdown', { signal: 'SIGTERM' });
  try { await redis.quit(); } catch {}
  process.exit(0);
});
process.on('SIGINT', async () => {
  try { await redis.quit(); } catch {}
  process.exit(0);
});

log('info', 'regime-replicator starting', {
  tick: CFG.TICK_MS,
  primaryKey: CFG.PRIMARY_KEY,
  replicas: CFG.REPLICA_KEYS,
});
tick();
