/**
 * ares-data-freshness-enforcer.mjs
 * ═══════════════════════════════════════════════════════════════════
 * ARES Data Freshness Enforcer — 주기적 검증 + 자동 복구
 * ═══════════════════════════════════════════════════════════════════
 *
 * 기능:
 *   1. 5분마다 모든 데이터 소스 freshness 검증
 *   2. stale 감지 시 자동 복구 시도 (cron 재실행, PM2 재시작, KIS 토큰 갱신)
 *   3. Redis TTL 이상 감지 및 자동 수정
 *   4. PM2 프로세스 상태 모니터링
 *   5. 검증 결과를 Redis에 저장 (대시보드 연동)
 *   6. CRITICAL 위반 시 trading halt 연동
 *
 * 의존성:
 *   - ares-data-freshness-contract.mjs (contract 정의)
 *   - Redis (ElastiCache TLS)
 *   - better-sqlite3 (DB freshness 조회)
 *   - child_process (복구 명령 실행)
 *
 * PM2: ecosystem.config.cjs에 추가하여 실행
 *
 * @version 1.0.0
 * @date 2026-04-14
 */

import { createClient } from 'redis';
import Database from 'better-sqlite3';
import { execSync, exec } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

import {
  CANONICAL_PATHS,
  SEVERITY,
  RECOVERY,
  DB_FRESHNESS_CONTRACT,
  REDIS_FRESHNESS_CONTRACT,
  PM2_PROCESS_CONTRACT,
  CRON_CONTRACT,
  FRESHNESS_INVARIANTS,
  calculateStalenessHours,
  calculateTimestampStaleness,
  adjustForWeekend,
} from './ares-data-freshness-contract.mjs';

// ─── CONFIG ──────────────────────────────────────────────────────
const REDIS_URL = process.env.ARES_REDIS_URL || process.env.REDIS_URL;
if (!REDIS_URL) {
  console.error('[FRESHNESS-ENFORCER] FATAL: REDIS_URL not set');
  process.exit(1);
}

const CHECK_INTERVAL_MS   = 5 * 60 * 1000;   // 5분
const RECOVERY_COOLDOWN   = 30 * 60 * 1000;   // 30분 (같은 소스 재복구 방지)
const MAX_RECOVERY_PER_DAY = 3;                // 하루 최대 복구 시도
const CRITICAL_AUTO_HALT   = true;             // CRITICAL stale 시 trading halt
const LOG_PREFIX           = '[FRESHNESS-ENFORCER]';

// ─── STATE ───────────────────────────────────────────────────────
let redis;
const recoveryLog = {};    // key → { lastAttempt: Date, count: number, date: string }
const consecutiveFails = {}; // key → count

// ─── REDIS CONNECTION ────────────────────────────────────────────
async function connectRedis() {
  redis = createClient({
    url: REDIS_URL,
    socket: { tls: true, rejectUnauthorized: false },
  });
  redis.on('error', (err) => console.error(`${LOG_PREFIX} Redis error:`, err.message));
  await redis.connect();
  console.log(`${LOG_PREFIX} Connected to Redis`);
}

// ─── HELPERS ─────────────────────────────────────────────────────
function now() { return new Date().toISOString(); }
function todayStr() { return new Date().toISOString().slice(0, 10); }

function isUSMarketHours() {
  const n = new Date();
  const day = n.getUTCDay();
  if (day === 0 || day === 6) return false;
  const h = n.getUTCHours();
  // US market: 13:30–20:00 UTC (EDT) / 14:30–21:00 UTC (EST)
  return h >= 13 && h <= 21;
}

function isWeekend() {
  const day = new Date().getUTCDay();
  return day === 0 || day === 6;
}

function canRecover(key) {
  const today = todayStr();
  const entry = recoveryLog[key];
  if (!entry) return true;
  if (entry.date !== today) {
    recoveryLog[key] = { lastAttempt: null, count: 0, date: today };
    return true;
  }
  if (entry.count >= MAX_RECOVERY_PER_DAY) return false;
  if (entry.lastAttempt && (Date.now() - entry.lastAttempt.getTime()) < RECOVERY_COOLDOWN) return false;
  return true;
}

function markRecovery(key) {
  const today = todayStr();
  if (!recoveryLog[key] || recoveryLog[key].date !== today) {
    recoveryLog[key] = { lastAttempt: new Date(), count: 1, date: today };
  } else {
    recoveryLog[key].lastAttempt = new Date();
    recoveryLog[key].count++;
  }
}

// ─── DB FRESHNESS CHECK ─────────────────────────────────────────
function checkDBFreshness() {
  const results = [];

  for (const [key, contract] of Object.entries(DB_FRESHNESS_CONTRACT)) {
    const result = {
      key,
      source: 'db',
      severity: contract.severity,
      status: 'UNKNOWN',
      stalenessHours: null,
      maxDate: null,
      message: '',
    };

    try {
      if (!existsSync(contract.db)) {
        result.status = 'ERROR';
        result.message = `DB file not found: ${contract.db}`;
        results.push(result);
        continue;
      }

      const db = new Database(contract.db, { readonly: true });
      const row = db.prepare(`SELECT MAX(${contract.dateColumn}) as max_date FROM ${contract.table}`).get();
      db.close();

      if (!row || !row.max_date) {
        result.status = 'ERROR';
        result.message = `No data in ${contract.table}`;
        results.push(result);
        continue;
      }

      result.maxDate = row.max_date;
      const rawHours = calculateStalenessHours(row.max_date);
      result.stalenessHours = adjustForWeekend(rawHours);

      if (result.stalenessHours <= contract.maxStalenessHours) {
        result.status = 'FRESH';
        result.message = `OK (${result.stalenessHours.toFixed(1)}h / ${contract.maxStalenessHours}h)`;
      } else {
        result.status = 'STALE';
        result.message = `STALE (${result.stalenessHours.toFixed(1)}h > ${contract.maxStalenessHours}h)`;
      }
    } catch (err) {
      result.status = 'ERROR';
      result.message = `Check failed: ${err.message}`;
    }

    results.push(result);
  }

  return results;
}

// ─── REDIS FRESHNESS CHECK ──────────────────────────────────────
async function checkRedisFreshness() {
  const results = [];

  for (const [key, contract] of Object.entries(REDIS_FRESHNESS_CONTRACT)) {
    const result = {
      key,
      source: 'redis',
      severity: contract.severity,
      status: 'UNKNOWN',
      ttl: null,
      value: null,
      message: '',
    };

    try {
      const exists = await redis.exists(key);
      if (!exists) {
        result.status = 'MISSING';
        result.message = `Key does not exist`;
        results.push(result);
        continue;
      }

      result.ttl = await redis.ttl(key);
      const type = await redis.type(key);

      // TTL 검증
      const [minTTL, maxTTL] = contract.expectedTTL;
      if (minTTL !== -1 && maxTTL !== -1) {
        // 유한 TTL 예상
        if (result.ttl === -1) {
          result.status = 'TTL_PERMANENT';
          result.message = `Permanent TTL (expected ${minTTL}~${maxTTL}s)`;
          results.push(result);
          continue;
        }
        if (result.ttl < minTTL * 0.1) {
          result.status = 'TTL_EXPIRING';
          result.message = `TTL=${result.ttl}s (dangerously low, min=${minTTL}s)`;
        }
      }

      // 값 기반 freshness (timestamp 필드가 있는 경우)
      if (contract.timestampField && type === 'hash') {
        const ts = await redis.hGet(key, contract.timestampField);
        if (ts) {
          const staleH = calculateTimestampStaleness(ts);
          const adjH = adjustForWeekend(staleH);
          if (adjH > contract.maxStalenessHours) {
            result.status = 'STALE';
            result.message = `Timestamp stale: ${adjH.toFixed(1)}h > ${contract.maxStalenessHours}h`;
            results.push(result);
            continue;
          }
        }
      }

      // state 필드 검증 (CRASH 등)
      if (contract.stateField) {
        let val;
        if (type === 'hash') {
          val = await redis.hGet(key, contract.stateField);
        } else if (type === 'string') {
          try {
            const raw = await redis.get(key);
            const parsed = JSON.parse(raw);
            val = parsed[contract.stateField];
          } catch { val = null; }
        }
        if (val && contract.crashValue && val === contract.crashValue) {
          result.status = 'CRASH';
          result.value = val;
          result.message = `State=${val} (forbidden: ${contract.crashValue})`;
          results.push(result);
          continue;
        }
      }

      // allowedValues 검증
      if (contract.allowedValues) {
        let val;
        if (type === 'string') {
          val = await redis.get(key);
        } else if (type === 'hash') {
          val = await redis.hGet(key, 'regime') || await redis.hGet(key, 'value');
        }
        if (val && !contract.allowedValues.includes(val)) {
          result.status = 'INVALID_VALUE';
          result.value = val;
          result.message = `Value '${val}' not in allowed: [${contract.allowedValues.join(',')}]`;
          results.push(result);
          continue;
        }
      }

      if (!result.status || result.status === 'UNKNOWN') {
        result.status = 'OK';
        result.message = `TTL=${result.ttl}s`;
      }
    } catch (err) {
      result.status = 'ERROR';
      result.message = `Check failed: ${err.message}`;
    }

    results.push(result);
  }

  return results;
}

// ─── PM2 PROCESS CHECK ──────────────────────────────────────────
function checkPM2Processes() {
  const results = [];

  try {
    const raw = execSync('pm2 jlist 2>/dev/null', { encoding: 'utf-8', timeout: 10000, maxBuffer: 100 * 1024 * 1024 });
    const processes = JSON.parse(raw);
    const pm2Map = {};
    for (const p of processes) {
      pm2Map[p.name] = {
        status: p.pm2_env?.status || 'unknown',
        restarts: p.pm2_env?.restart_time || 0,
        uptime: p.pm2_env?.pm_uptime || 0,
        memory: p.monit?.memory || 0,
        cpu: p.monit?.cpu || 0,
      };
    }

    for (const [name, contract] of Object.entries(PM2_PROCESS_CONTRACT)) {
      const info = pm2Map[name];
      const result = {
        key: name,
        source: 'pm2',
        severity: contract.severity,
        status: 'UNKNOWN',
        message: '',
        restarts: 0,
      };

      if (!info) {
        result.status = 'NOT_FOUND';
        result.message = `Process not registered in PM2`;
      } else if (info.status !== 'online') {
        result.status = 'DOWN';
        result.message = `Status=${info.status}, restarts=${info.restarts}`;
        result.restarts = info.restarts;
      } else if (info.restarts > contract.maxRestarts && (Date.now() - (info.uptime || 0)) < 300000) {
        result.status = 'FLAPPING';
        result.message = `Restarts=${info.restarts} > max=${contract.maxRestarts}`;
        result.restarts = info.restarts;
      } else {
        result.status = 'ONLINE';
        result.message = `OK (restarts=${info.restarts}, mem=${(info.memory / 1024 / 1024).toFixed(0)}MB)`;
        result.restarts = info.restarts;
      }

      results.push(result);
    }
  } catch (err) {
    results.push({
      key: 'pm2_check',
      source: 'pm2',
      severity: SEVERITY.CRITICAL,
      status: 'ERROR',
      message: `PM2 check failed: ${err.message}`,
    });
  }

  return results;
}

// ─── RECOVERY ENGINE ─────────────────────────────────────────────
async function attemptRecovery(result, contract) {
  const key = result.key;

  if (!canRecover(key)) {
    console.log(`${LOG_PREFIX} Recovery cooldown active for: ${key}`);
    return { attempted: false, reason: 'cooldown' };
  }

  const recovery = contract.recovery;
  let success = false;
  let detail = '';

  try {
    switch (recovery) {
      case RECOVERY.CRON_RERUN: {
        const cmd = contract.recoveryCommand;
        if (!cmd) {
          detail = 'No recovery command defined';
          break;
        }
        console.log(`${LOG_PREFIX} Executing recovery: ${cmd.slice(0, 80)}...`);
        exec(cmd, { timeout: 300000 }, (err, stdout, stderr) => {
          if (err) {
            console.error(`${LOG_PREFIX} Recovery failed for ${key}: ${err.message}`);
          } else {
            console.log(`${LOG_PREFIX} Recovery completed for ${key}`);
          }
        });
        success = true;
        detail = `Cron rerun initiated: ${cmd.slice(0, 60)}`;
        break;
      }

      case RECOVERY.PM2_RESTART: {
        const pm2Name = contract.recoveryPM2 || key;
        console.log(`${LOG_PREFIX} Restarting PM2 process: ${pm2Name}`);
        execSync(`pm2 restart ${pm2Name} 2>/dev/null`, { timeout: 15000, maxBuffer: 10 * 1024 * 1024 });
        success = true;
        detail = `PM2 restart: ${pm2Name}`;
        break;
      }

      case RECOVERY.KIS_TOKEN_RENEW: {
        const pm2Name = contract.recoveryPM2 || 'kis-token-service';
        console.log(`${LOG_PREFIX} Renewing KIS token via PM2 restart: ${pm2Name}`);
        execSync(`pm2 restart ${pm2Name} 2>/dev/null`, { timeout: 15000, maxBuffer: 10 * 1024 * 1024 });
        success = true;
        detail = `KIS token renewal via PM2 restart: ${pm2Name}`;
        break;
      }

      case RECOVERY.REDIS_TTL_FIX: {
        if (result.status === 'TTL_PERMANENT') {
          const ttlSec = 86400; // 24h
          await redis.expire(key, ttlSec);
          console.log(`${LOG_PREFIX} Fixed permanent TTL: ${key} → ${ttlSec}s`);
          success = true;
          detail = `TTL set to ${ttlSec}s`;
        }
        break;
      }

      case RECOVERY.MANUAL:
        detail = 'Manual recovery required';
        break;

      case RECOVERY.NONE:
        detail = 'No recovery available';
        break;

      default:
        detail = `Unknown recovery strategy: ${recovery}`;
    }
  } catch (err) {
    detail = `Recovery error: ${err.message}`;
  }

  if (success) {
    markRecovery(key);
  }

  return { attempted: true, success, detail };
}

// ─── INVARIANT CHECK ─────────────────────────────────────────────
async function checkInvariants(dbResults, redisResults, pm2Results) {
  const violations = [];

  for (const [id, inv] of Object.entries(FRESHNESS_INVARIANTS)) {
    let violated = false;
    let detail = '';

    switch (inv.check) {
      case 'db_staleness': {
        const r = dbResults.find(r => r.key === inv.target);
        if (r && r.status === 'STALE') {
          violated = true;
          detail = r.message;
        } else if (r && r.status === 'ERROR') {
          violated = true;
          detail = r.message;
        }
        break;
      }

      case 'redis_ttl_not_permanent': {
        for (const target of (inv.targets || [inv.target])) {
          const r = redisResults.find(r => r.key === target);
          if (r && r.status === 'TTL_PERMANENT') {
            violated = true;
            detail += `${target}: permanent TTL; `;
          }
        }
        break;
      }

      case 'redis_value_not_equals': {
        const r = redisResults.find(r => r.key === inv.target);
        if (r && r.status === 'CRASH') {
          violated = true;
          detail = `${inv.target}: ${r.message}`;
        }
        break;
      }

      case 'redis_value_in_set': {
        for (const target of (inv.targets || [inv.target])) {
          const r = redisResults.find(r => r.key === target);
          if (r && r.status === 'INVALID_VALUE') {
            violated = true;
            detail += `${target}: ${r.message}; `;
          }
        }
        break;
      }

      case 'redis_key_exists': {
        const r = redisResults.find(r => r.key === inv.target);
        if (r && r.status === 'MISSING') {
          violated = true;
          detail = `${inv.target}: key missing`;
        }
        break;
      }

      case 'redis_value_range': {
        for (const target of (inv.targets || [inv.target])) {
          try {
            const val = await redis.get(target);
            if (val !== null) {
              let num;
              try {
                const parsed = JSON.parse(val);
                num = typeof parsed === 'number' ? parsed : parseFloat(parsed.value || parsed);
              } catch {
                num = parseFloat(val);
              }
              if (!isNaN(num) && (num < inv.min || num > inv.max)) {
                violated = true;
                detail += `${target}: ${num} not in [${inv.min}, ${inv.max}]; `;
              }
            }
          } catch { /* skip */ }
        }
        break;
      }

      case 'pm2_status': {
        const criticalDown = pm2Results.filter(
          r => r.severity === SEVERITY.CRITICAL && (r.status === 'DOWN' || r.status === 'NOT_FOUND')
        );
        if (criticalDown.length > 0) {
          violated = true;
          detail = criticalDown.map(r => `${r.key}: ${r.status}`).join('; ');
        }
        break;
      }
    }

    if (violated) {
      violations.push({
        id,
        name: inv.name,
        severity: inv.severity,
        detail,
      });
    }
  }

  return violations;
}

// ─── TRADING HALT ────────────────────────────────────────────────
async function haltTrading(reason) {
  console.error(`${LOG_PREFIX} *** HALT REQUEST *** ${reason}`);
  try {
    // [FP-FIX v2.0] trade:halt 직접 쓰기 제거 → 요청 키 기반
    await redis.set('ares:halt:request:freshness_enforcer', JSON.stringify({
      severity: 'CRITICAL',
      message: `FRESHNESS_ENFORCER: ${reason}`,
      source: 'ares-data-freshness-enforcer',
      ts: now(),
    }), { EX: 600 });
  } catch (err) {
    console.error(`${LOG_PREFIX} Failed to set halt request: ${err.message}`);
  }
}

async function clearHaltRequest() {
  try {
    const exists = await redis.exists('ares:halt:request:freshness_enforcer');
    if (exists) {
      await redis.del('ares:halt:request:freshness_enforcer');
      console.log(`${LOG_PREFIX} Halt request cleared — freshness OK`);
    }
  } catch (e) {
    console.error(`${LOG_PREFIX} Clear request failed: ${e.message}`);
  }
}

// ─── PERSIST RESULTS ─────────────────────────────────────────────
async function persistResults(dbResults, redisResults, pm2Results, violations, recoveries) {
  const summary = {
    timestamp: now(),
    db: {
      total: dbResults.length,
      fresh: dbResults.filter(r => r.status === 'FRESH').length,
      stale: dbResults.filter(r => r.status === 'STALE').length,
      error: dbResults.filter(r => r.status === 'ERROR').length,
    },
    redis: {
      total: redisResults.length,
      ok: redisResults.filter(r => r.status === 'OK').length,
      stale: redisResults.filter(r => ['STALE', 'TTL_PERMANENT', 'TTL_EXPIRING', 'CRASH', 'INVALID_VALUE'].includes(r.status)).length,
      missing: redisResults.filter(r => r.status === 'MISSING').length,
    },
    pm2: {
      total: pm2Results.length,
      online: pm2Results.filter(r => r.status === 'ONLINE').length,
      down: pm2Results.filter(r => r.status === 'DOWN').length,
      flapping: pm2Results.filter(r => r.status === 'FLAPPING').length,
      notFound: pm2Results.filter(r => r.status === 'NOT_FOUND').length,
    },
    violations: violations.length,
    criticalViolations: violations.filter(v => v.severity === SEVERITY.CRITICAL).length,
    recoveries: recoveries.length,
    successfulRecoveries: recoveries.filter(r => r.success).length,
  };

  try {
    await redis.set('ares:freshness:last_check', JSON.stringify(summary), { EX: 600 });
    await redis.set('ares:freshness:last_check_time', now(), { EX: 600 });

    // 상세 결과 (10분 TTL)
    await redis.set('ares:freshness:db_results', JSON.stringify(dbResults), { EX: 600 });
    await redis.set('ares:freshness:redis_results', JSON.stringify(redisResults), { EX: 600 });
    await redis.set('ares:freshness:pm2_results', JSON.stringify(pm2Results), { EX: 600 });
    await redis.set('ares:freshness:violations', JSON.stringify(violations), { EX: 600 });

    // 히스토리 (24시간 보관)
    const historyKey = `ares:freshness:history:${todayStr()}`;
    await redis.lPush(historyKey, JSON.stringify(summary));
    await redis.expire(historyKey, 86400);
    await redis.lTrim(historyKey, 0, 287); // 최대 288개 (5분 × 24시간)
  } catch (err) {
    console.error(`${LOG_PREFIX} Failed to persist results: ${err.message}`);
  }

  return summary;
}

// ─── MAIN CHECK CYCLE ────────────────────────────────────────────
async function runCheck() {
  const startTime = Date.now();
  console.log(`${LOG_PREFIX} ─── Check cycle start: ${now()} ───`);

  // 1. DB Freshness
  const dbResults = checkDBFreshness();
  const dbStale = dbResults.filter(r => r.status === 'STALE');
  const dbErrors = dbResults.filter(r => r.status === 'ERROR');

  // 2. Redis Freshness
  const redisResults = await checkRedisFreshness();
  const redisIssues = redisResults.filter(r => !['OK', 'UNKNOWN'].includes(r.status));

  // 3. PM2 Processes
  const pm2Results = checkPM2Processes();
  const pm2Issues = pm2Results.filter(r => !['ONLINE'].includes(r.status));

  // 4. Invariant Check
  const violations = await checkInvariants(dbResults, redisResults, pm2Results);

  // 5. Recovery
  const recoveries = [];
  const allIssues = [
    ...dbStale.map(r => ({ result: r, contract: DB_FRESHNESS_CONTRACT[r.key] })),
    ...redisIssues.map(r => ({ result: r, contract: REDIS_FRESHNESS_CONTRACT[r.key] })),
    ...pm2Issues.filter(r => r.status === 'DOWN').map(r => ({ result: r, contract: PM2_PROCESS_CONTRACT[r.key] ? { recovery: RECOVERY.PM2_RESTART, recoveryPM2: r.key, ...PM2_PROCESS_CONTRACT[r.key] } : null })),
  ].filter(x => x.contract);

  for (const { result, contract } of allIssues) {
    if (contract.recovery === RECOVERY.NONE || contract.recovery === RECOVERY.MANUAL) continue;
    // 주말에는 DB stale 복구 스킵
    if (isWeekend() && result.source === 'db') continue;

    const recovery = await attemptRecovery(result, contract);
    if (recovery.attempted) {
      recoveries.push({
        key: result.key,
        ...recovery,
      });
    }
  }

  // 6. CRITICAL halt
  if (CRITICAL_AUTO_HALT && isUSMarketHours()) {
    const criticalViolations = violations.filter(v => v.severity === SEVERITY.CRITICAL);
    for (const v of criticalViolations) {
      // 3회 연속 CRITICAL 시 halt
      consecutiveFails[v.id] = (consecutiveFails[v.id] || 0) + 1;
      if (consecutiveFails[v.id] >= 3) {
        await haltTrading(`${v.id}: ${v.name} — ${v.detail}`);
      }
    }
    // 통과한 invariant는 카운터 리셋
    for (const [id] of Object.entries(FRESHNESS_INVARIANTS)) {
      if (!violations.find(v => v.id === id)) {
        consecutiveFails[id] = 0;
      }
    }
  }

  // 7. Persist
  const summary = await persistResults(dbResults, redisResults, pm2Results, violations, recoveries);

  // 8. Log summary
  const elapsed = Date.now() - startTime;
  const statusLine = [
    `DB: ${summary.db.fresh}/${summary.db.total} fresh`,
    `Redis: ${summary.redis.ok}/${summary.redis.total} ok`,
    `PM2: ${summary.pm2.online}/${summary.pm2.total} online`,
    `Violations: ${summary.violations} (${summary.criticalViolations} CRITICAL)`,
    `Recoveries: ${summary.successfulRecoveries}/${summary.recoveries}`,
    `${elapsed}ms`,
  ].join(' | ');

  if (summary.criticalViolations > 0) {
    console.error(`${LOG_PREFIX} ${statusLine}`);
    for (const v of violations.filter(v => v.severity === SEVERITY.CRITICAL)) {
      console.error(`${LOG_PREFIX}   CRITICAL: ${v.id} ${v.name} — ${v.detail}`);
    }
  } else if (summary.violations > 0) {
    console.warn(`${LOG_PREFIX} ${statusLine}`);
    for (const v of violations) {
      console.warn(`${LOG_PREFIX}   ${v.severity}: ${v.id} ${v.name} — ${v.detail}`);
    }
  } else {
    console.log(`${LOG_PREFIX} ${statusLine}`);
  }

  // Log stale details
  for (const r of dbStale) {
    console.warn(`${LOG_PREFIX}   DB STALE: ${r.key} — ${r.message}`);
  }
  for (const r of redisIssues) {
    console.warn(`${LOG_PREFIX}   REDIS: ${r.key} — ${r.status}: ${r.message}`);
  }
  for (const r of pm2Issues) {
    console.warn(`${LOG_PREFIX}   PM2: ${r.key} — ${r.status}: ${r.message}`);
  }
  for (const r of recoveries) {
    console.log(`${LOG_PREFIX}   RECOVERY: ${r.key} — ${r.success ? 'OK' : 'FAIL'}: ${r.detail}`);
  }

  console.log(`${LOG_PREFIX} ─── Check cycle end ───`);
}

// ─── STARTUP ─────────────────────────────────────────────────────
async function main() {
  console.log(`${LOG_PREFIX} Starting ARES Data Freshness Enforcer v2.0.0 [FP-FIX: request-key based halt]`);
  console.log(`${LOG_PREFIX} Check interval: ${CHECK_INTERVAL_MS / 1000}s`);
  console.log(`${LOG_PREFIX} Recovery cooldown: ${RECOVERY_COOLDOWN / 1000}s`);
  console.log(`${LOG_PREFIX} Max recovery/day: ${MAX_RECOVERY_PER_DAY}`);
  console.log(`${LOG_PREFIX} Critical auto-halt: ${CRITICAL_AUTO_HALT}`);

  await connectRedis();

  // 즉시 첫 번째 체크 실행
  await runCheck();

  // 주기적 체크
  setInterval(async () => {
    try {
      await runCheck();
    } catch (err) {
      console.error(`${LOG_PREFIX} Check cycle error: ${err.message}`);
      console.error(err.stack);
    }
  }, CHECK_INTERVAL_MS);

  // Graceful shutdown
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, async () => {
      console.log(`${LOG_PREFIX} Received ${sig}, shutting down...`);
      try {
        await redis.set('ares:freshness:enforcer:status', JSON.stringify({
          status: 'stopped',
          timestamp: now(),
          signal: sig,
        }), { EX: 3600 });
        await redis.quit();
      } catch { /* ignore */ }
      process.exit(0);
    });
  }
}

main().catch(err => {
  console.error(`${LOG_PREFIX} Fatal error:`, err);
  process.exit(1);
});
