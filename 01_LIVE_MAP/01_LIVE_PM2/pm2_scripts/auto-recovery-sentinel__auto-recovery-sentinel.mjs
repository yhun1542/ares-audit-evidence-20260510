#!/usr/bin/env node
/**
 * auto-recovery-sentinel.mjs — trading:enabled 자동 복원
 *
 * [MUSK-RCA-20260421-A] Step 5: Automate
 *
 * 시나리오:
 *   - 모든 upstream 신호가 OK인데 trading:enabled=false
 *   - 사람이 의도적으로 끈 게 아니면 자동으로 복원
 *
 * 안전장치:
 *   - manual override(`trading:enabled:override = kill`) 있으면 절대 복원 안 함
 *   - GRACE_AFTER_FALSE_S(default 30s) 동안 모든 신호가 GO여야만 복원
 *   - 시간당 최대 6회만 자동 복원 (rate limit)
 *   - 모든 복원은 audit stream에 기록
 *
 * @version 1.0.0
 */

import IORedis from 'ioredis';

const REDIS_URL = process.env.ARES_REDIS_URL || process.env.REDIS_URL;

const CFG = {
  TRADING_ENABLED_KEY:  'trading:enabled',
  OVERRIDE_KEY:         'trading:enabled:override',
  AUTHORITY_STATE_KEY:  'kill-switch-authority:state',
  KILL_SWITCH_KEY:      'kill_switch',
  TRADE_HALT_KEY:       'trade:halt',
  SANITY_BLOCK_KEY:     'ares:ssot:sanity_block',
  FTG_KEY:              'ares:final_trade_gate',
  GO_NOGO_VERDICT_KEY:  'ares:go_nogo:verdict',
  AUDIT_STREAM:         'ares:trading:audit',
  STATE_KEY:            'auto-recovery-sentinel:state',
  HEARTBEAT_KEY:        'auto-recovery-sentinel:heartbeat',

  TICK_MS:              5_000,
  GRACE_AFTER_FALSE_S:  30,         // 30s 모든 신호 GO여야 복원
  MAX_RECOVERIES_PER_HOUR: 6,
  ENABLED:              process.env.AUTO_RECOVERY_ENABLED !== 'false',  // 기본 ON, 끄려면 false
};

const redis = new IORedis(REDIS_URL, {
  tls: { servername: 'master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com' },
  lazyConnect: false,
});

function nowMs() { return Date.now(); }
function iso() { return new Date().toISOString(); }
function log(level, msg, data = {}) {
  process.stdout.write(JSON.stringify({
    ts: iso(), svc: 'auto-recovery-sentinel', level, msg, ...data,
  }) + '\n');
}

let firstFalseSeenAt = 0;
const recoveryHistory = [];   // { ts: ms }

async function audit(event, payload) {
  try {
    await redis.xadd(CFG.AUDIT_STREAM, 'MAXLEN', '~', '50000', '*',
      'ts', iso(), 'by', 'auto-recovery-sentinel',
      'event', event, 'payload', JSON.stringify(payload),
    );
  } catch {}
}

function pruneRecoveryHistory() {
  const oneHourAgo = nowMs() - 3600 * 1000;
  while (recoveryHistory.length && recoveryHistory[0].ts < oneHourAgo) {
    recoveryHistory.shift();
  }
}

async function checkAllSignalsGo() {
  // 모든 신호를 다 읽어서 1개라도 BLOCK이면 복원 금지
  const reasons = [];

  const [override, killSwitch, tradeHalt, sanityBlock, ftg, authState, verdict] = await Promise.all([
    redis.get(CFG.OVERRIDE_KEY),
    redis.get(CFG.KILL_SWITCH_KEY),
    redis.get(CFG.TRADE_HALT_KEY),
    redis.get(CFG.SANITY_BLOCK_KEY),
    redis.get(CFG.FTG_KEY),
    redis.get(CFG.AUTHORITY_STATE_KEY),
    redis.get(CFG.GO_NOGO_VERDICT_KEY),
  ]);

  if (override === 'kill') reasons.push('manual_override_kill');
  if ((killSwitch || '').toLowerCase() === 'true') reasons.push('kill_switch_true');
  if ((tradeHalt || '').toLowerCase() === 'true') reasons.push('trade_halt_true');
  if ((sanityBlock || '').toLowerCase() === 'true') reasons.push('sanity_block_true');
  if (ftg && ftg.toUpperCase() !== 'OPEN') reasons.push(`ftg_${ftg}`);

  // verdict 체크
  try {
    if (verdict) {
      const v = JSON.parse(verdict);
      const decision = (v.result || v.verdict || v.decision || '').toUpperCase();
      if (decision !== 'GO' && decision !== 'PASS' && decision !== 'APPROVED') {
        reasons.push(`verdict_${decision}`);
      }
    } else {
      reasons.push('verdict_missing');
    }
  } catch (e) { reasons.push('verdict_parse_err'); }

  // authority state 체크
  try {
    if (authState) {
      const a = JSON.parse(authState);
      if (a.state === 'OVERRIDE_KILL') reasons.push('authority_override_kill');
    }
  } catch {}

  return { allGo: reasons.length === 0, reasons };
}

async function tick() {
  try {
    await redis.set(CFG.HEARTBEAT_KEY, nowMs().toString(), 'EX', 30);

    if (!CFG.ENABLED) {
      log('debug', 'disabled', {});
      return;
    }

    const tradingEnabled = await redis.get(CFG.TRADING_ENABLED_KEY);
    const isFalse = (tradingEnabled || '').toLowerCase() !== 'true';

    if (!isFalse) {
      // 정상 — 카운터 리셋
      firstFalseSeenAt = 0;
      await redis.set(CFG.STATE_KEY, JSON.stringify({
        ts: iso(), trading: 'true', action: 'none',
      }), 'EX', 60);
      return;
    }

    // trading=false 상태
    const { allGo, reasons } = await checkAllSignalsGo();

    if (!allGo) {
      // 차단 사유가 있음 → 정당한 차단, 카운터 리셋
      firstFalseSeenAt = 0;
      log('debug', 'legitimate-block', { reasons });
      await redis.set(CFG.STATE_KEY, JSON.stringify({
        ts: iso(), trading: 'false', action: 'wait_legitimate', reasons,
      }), 'EX', 60);
      return;
    }

    // 모든 upstream이 GO인데 trading=false → 비정상
    if (firstFalseSeenAt === 0) {
      firstFalseSeenAt = nowMs();
      log('warn', 'inconsistent-detected', { tradingEnabled, allUpstreamGo: true });
      await audit('INCONSISTENT_DETECTED', { tradingEnabled });
      return;
    }

    const inconsistentSec = (nowMs() - firstFalseSeenAt) / 1000;
    if (inconsistentSec < CFG.GRACE_AFTER_FALSE_S) {
      log('debug', 'grace-waiting', { inconsistentSec, threshold: CFG.GRACE_AFTER_FALSE_S });
      return;
    }

    // Rate limit
    pruneRecoveryHistory();
    if (recoveryHistory.length >= CFG.MAX_RECOVERIES_PER_HOUR) {
      log('warn', 'rate-limited', { count: recoveryHistory.length, max: CFG.MAX_RECOVERIES_PER_HOUR });
      await audit('RATE_LIMITED', { count: recoveryHistory.length });
      return;
    }

    // 자동 복원 실행
    log('warn', 'auto-recovering', { inconsistentSec });
    await redis.set(CFG.TRADING_ENABLED_KEY, 'true');
    recoveryHistory.push({ ts: nowMs() });
    firstFalseSeenAt = 0;
    await audit('AUTO_RECOVERY', {
      from: 'false', to: 'true',
      inconsistentSec,
      hourCount: recoveryHistory.length,
    });
    log('info', 'recovered', { hourCount: recoveryHistory.length });

  } catch (e) {
    log('error', 'tick-error', { err: e.message });
  } finally {
    setTimeout(tick, CFG.TICK_MS);
  }
}

process.on('SIGTERM', async () => { try { await redis.quit(); } catch {} process.exit(0); });
process.on('SIGINT',  async () => { try { await redis.quit(); } catch {} process.exit(0); });

log('info', 'auto-recovery-sentinel starting', {
  enabled: CFG.ENABLED,
  graceS: CFG.GRACE_AFTER_FALSE_S,
  maxPerHour: CFG.MAX_RECOVERIES_PER_HOUR,
});
tick();
