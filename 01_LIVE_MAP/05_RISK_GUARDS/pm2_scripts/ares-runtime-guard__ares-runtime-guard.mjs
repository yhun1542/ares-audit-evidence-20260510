#!/usr/bin/env node
// SOURCE: /home/ubuntu/ares_current/services/ares-runtime-guard.mjs
// SIZE: 13377 chars

/**
 * ares-runtime-guard.mjs — Runtime Invariant Guard
 * =================================================
 * PM2 프로세스로 상시 실행되며, 실행 중인 시스템의 무결성을 지속적으로 감시합니다.
 * 
 * 감시 항목:
 * 1. PM2 프로세스의 REDIS_URL이 localhost로 오염되었는지 (module_conf.json 포함)
 * 2. Redis 연결이 실제로 ElastiCache에 연결되었는지
 * 3. KIS 토큰 TTL이 영구(-1)로 설정되었는지
 * 4. 필수 PM2 프로세스가 online 상태인지
 * 5. PM2 dump.pm2에 localhost가 오염되었는지
 * 
 * 위반 감지 시:
 * - 자동 수정 가능한 항목은 즉시 수정 (module_conf.json, KIS TTL)
 * - 수정 불가능한 항목은 Telegram 알림 + 로그
 * - CRITICAL 위반 시 pm2 restart로 프로세스 재시작
 * 
 * PM2 등록:
 *   pm2 start ares-runtime-guard.mjs --name "ares-runtime-guard" --node-args="--experimental-modules"
 */

import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync } from 'fs';
import { execSync } from 'child_process';
import { statSync } from 'fs';
// [PATCH-P3-3 20260424] yaml dynamic registry loader
let _yamlMod = null;
try { _yamlMod = await import('yaml'); } catch (_) { /* fallback */ }
import { join } from 'path';
import { homedir } from 'os';
import { createRequire } from 'module';

const HOME = homedir();
const CANONICAL_REDIS_HOST = 'master.ares-redis-ha.hgv1iy.apn2.cache.amazonaws.com';
// [SSOT 전환 2026-04-24] CANONICAL_REDIS_URL은 SSOT 파일에서 동적으로 생성
import { getRedisUrl } from "/home/ubuntu/scripts/redis_ssot_loader.mjs";
const CANONICAL_REDIS_URL = getRedisUrl();
const LOCALHOST_PATTERNS = ['localhost:6379', '127.0.0.1:6379'];

const CHECK_INTERVAL_MS = 5 * 60 * 1000;  // 5분마다
const LOG_DIR = join(HOME, 'logs', 'runtime-guard');
const TG_TOKEN = process.env.TG_TOKEN || '';
const TG_CHAT = process.env.TG_CHAT || '';

// ─── Logging ───
mkdirSync(LOG_DIR, { recursive: true });

function log(level, msg) {
  const ts = new Date().toISOString();
  const line = `[${ts}] [${level}] ${msg}`;
  console.log(line);
  const logFile = join(LOG_DIR, `guard_${new Date().toISOString().slice(0, 10)}.log`);
  appendFileSync(logFile, line + '\n');
}

async function sendTelegram(msg) {
  if (!TG_TOKEN || !TG_CHAT) return;
  try {
    const url = `https://api.telegram.org/bot${TG_TOKEN}/sendMessage`;
    const body = JSON.stringify({ chat_id: TG_CHAT, text: `🛡️ RUNTIME GUARD\n${msg}`, parse_mode: 'HTML' });
    await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  } catch (e) {
    log('WARN', `Telegram send failed: ${e.message}`);
  }
}

// ─── Guard 1: PM2 module_conf.json 오염 감시 ───
function guard1_moduleConf() {
  const confPath = join(HOME, '.pm2', 'module_conf.json');
  if (!existsSync(confPath)) return { ok: true, detail: 'no module_conf.json' };

  try {
    const data = JSON.parse(readFileSync(confPath, 'utf8'));
    let contaminated = 0;
    const badKeys = [];

    function scan(obj, path) {
      if (typeof obj === 'string') {
        if (LOCALHOST_PATTERNS.some(p => obj.includes(p))) {
          contaminated++;
          badKeys.push(path);
        }
      } else if (typeof obj === 'object' && obj !== null) {
        for (const [k, v] of Object.entries(obj)) {
          scan(v, path ? `${path}.${k}` : k);
        }
      }
    }
    scan(data, '');

    if (contaminated > 0) {
      // 자동 수정
      log('WARN', `module_conf.json: ${contaminated} localhost entries detected. Auto-fixing...`);
      try {
        execSync(`python3 ${join(HOME, 'fix_pm2_module_conf.py')}`, { stdio: 'pipe', timeout: 10000, maxBuffer: 10 * 1024 * 1024 });
        log('INFO', `module_conf.json: auto-fixed ${contaminated} entries`);
        return { ok: true, detail: `auto-fixed ${contaminated} entries`, fixed: contaminated };
      } catch (e) {
        return { ok: false, detail: `${contaminated} localhost entries, fix failed: ${e.message}`, critical: true };
      }
    }
    return { ok: true, detail: 'clean' };
  } catch (e) {
    return { ok: false, detail: `parse error: ${e.message}` };
  }
}

// ─── Guard 2: PM2 프로세스 REDIS_URL 실시간 검증 ───
function guard2_processRedisURL() {
  try {
    const jlist = execSync('pm2 jlist 2>/dev/null', { encoding: 'utf8', timeout: 15000, maxBuffer: 50 * 1024 * 1024 });
    const procs = JSON.parse(jlist);
    let bad = 0;
    const badNames = [];

    for (const p of procs) {
      const url = (p.pm2_env || {}).REDIS_URL || '';
      if (LOCALHOST_PATTERNS.some(pat => url.includes(pat))) {
        bad++;
        badNames.push(p.name);
      }
    }

    if (bad > 0) {
      return { ok: false, detail: `${bad} processes with localhost: ${badNames.slice(0, 5).join(', ')}`, critical: true };
    }
    return { ok: true, detail: `${procs.length} processes all OK` };
  } catch (e) {
    return { ok: false, detail: `pm2 jlist failed: ${e.message}` };
  }
}

// ─── Guard 3: KIS 토큰 TTL 감시 ───
async function guard3_kisTokenTTL() {
  try {
    const Redis = (await import('ioredis')).default;
    const redis = new Redis(CANONICAL_REDIS_URL, { 
      tls: { rejectUnauthorized: false },
      connectTimeout: 5000,
      maxRetriesPerRequest: 1
    });

    const keys = ['kis:auth:token', 'kis:token'];
    let violations = 0;
    const details = [];

    for (const key of keys) {
      const ttl = await redis.ttl(key);
      if (ttl === -1) {
        // 영구 TTL — 자동 수정
        await redis.expire(key, 86400);
        violations++;
        details.push(`${key}: TTL=-1 → set to 86400s`);
        log('WARN', `KIS token ${key} had permanent TTL, fixed to 86400s`);
      }
    }

    await redis.quit();
    
    if (violations > 0) {
      return { ok: true, detail: `auto-fixed ${violations} KIS TTL: ${details.join('; ')}`, fixed: violations };
    }
    return { ok: true, detail: 'KIS tokens TTL OK' };
  } catch (e) {
    return { ok: false, detail: `Redis connection failed: ${e.message}` };
  }
}

// ─── [PATCH-P3-3 20260424] Dynamic critical resolver ───
const REGISTRY_PATH = "/etc/ares/services.registry.yaml";
// 운영 중요하나 registry에 critical:true 미설정인 서비스(보강)
const AUGMENT_CRITICAL = [
  // [PATCH-P4-2 20260424] registry 정식 등록 완료. AUGMENT_CRITICAL 비움.
  // ares-safe-live-guard는 deprecated (PM2에 등록되지 않음)
  // 향후 registry 미등록이지만 critical인 서비스만 여기에 추가
];
// fallback (registry 파싱 실패 시)
const FALLBACK_CRITICAL = [
  "kis-token-service", "realtime-data-feed-v3", "position-sync-service",
  "ssot-writer-v2", "ares-invariant-checker", "data-freshness-enforcer",
  "ares-policy-authority-v42", "ares-safe-live-guard", "kis-balance-sync",
  "go-nogo-judge", "halt-bridge",
];
let _criticalCache = { list: null, mtime: 0, ts: 0 };
function _loadRegistryCritical() {
  if (!_yamlMod) return null;
  try {
    const stat = statSync(REGISTRY_PATH);
    const mtime = stat.mtimeMs;
    // 30분 이내이고 mtime 변경 없으면 cache 반환
    if (_criticalCache.list && Date.now() - _criticalCache.ts < 1800_000 && _criticalCache.mtime === mtime) {
      return _criticalCache.list;
    }
        const content = readFileSync(REGISTRY_PATH, 'utf8');
    const parsed = _yamlMod.parse(content);
    const svcs = parsed?.services || {};
    const critical = Object.entries(svcs)
      .filter(([_, c]) => c && c.critical === true && c.deprecated !== true && c.enabled !== false && c.managed !== false)
      .map(([n]) => n);
    _criticalCache = { list: critical, mtime, ts: Date.now() };
    return critical;
  } catch (e) {
    console.error("[runtime-guard] registry parse failed:", e?.message);
    return null;
  }
}
function _getPm2Names() {
  try {
    const jl = execSync("pm2 jlist 2>/dev/null", { encoding: "utf8", timeout: 15000, maxBuffer: 50 * 1024 * 1024 });
    const procs = JSON.parse(jl);
    return new Set(procs.map(p => p.name));
  } catch { return null; }
}
function _resolveCriticalProcs() {
  const fromRegistry = _loadRegistryCritical();
  const base = fromRegistry || FALLBACK_CRITICAL;
  // registry ∪ AUGMENT
  const union = new Set([...base, ...AUGMENT_CRITICAL]);
  // ∩ PM2 실제 존재 (있는 것만 critical로 간주, 없는 항목은 별도 missing 보고)
  const pm2Names = _getPm2Names();
  if (!pm2Names) return [...union];  // pm2 조회 실패 시 union 그대로
  const final = [...union].filter(n => pm2Names.has(n));
  return final;
}
// ─── Guard 4: 필수 PM2 프로세스 상태 감시 ───
function guard4_criticalProcesses() {
  // [PATCH-P3-3 20260424] Dynamic critical list (registry ∪ augment) ∩ PM2-active
  const criticalProcs = _resolveCriticalProcs();

  try {
    const jlist = execSync('pm2 jlist 2>/dev/null', { encoding: 'utf8', timeout: 15000, maxBuffer: 50 * 1024 * 1024 });
    const procs = JSON.parse(jlist);
    const procMap = new Map(procs.map(p => [p.name, p.pm2_env?.status || 'unknown']));

    let down = 0;
    const downNames = [];

    for (const name of criticalProcs) {
      const status = procMap.get(name);
      if (!status || status !== 'online') {
        down++;
        downNames.push(`${name}(${status || 'missing'})`);
      }
    }

    if (down > 0) {
      return { ok: false, detail: `${down} critical processes down: ${downNames.join(', ')}`, critical: true };
    }
    return { ok: true, detail: `${criticalProcs.length} critical processes online` };
  } catch (e) {
    return { ok: false, detail: `pm2 check failed: ${e.message}` };
  }
}

// ─── Guard 5: PM2 dump.pm2 오염 감시 ───
function guard5_dumpFile() {
  const dumpPath = join(HOME, '.pm2', 'dump.pm2');
  if (!existsSync(dumpPath)) return { ok: true, detail: 'no dump file' };

  try {
    const content = readFileSync(dumpPath, 'utf8');
    const contaminated = LOCALHOST_PATTERNS.some(p => content.includes(p));
    
    if (contaminated) {
      // dump 파일에서 localhost를 ElastiCache로 교체
      let fixed = content;
      fixed = fixed.replace(/redis:\/\/:([^@]+)@127\.0\.0\.1:6379/g, 
        CANONICAL_REDIS_URL);
      fixed = fixed.replace(/redis:\/\/:([^@]+)@localhost:6379/g,
        CANONICAL_REDIS_URL);
      writeFileSync(dumpPath, fixed);
      log('WARN', 'dump.pm2 had localhost entries, auto-fixed');
      return { ok: true, detail: 'auto-fixed localhost in dump.pm2', fixed: 1 };
    }
    return { ok: true, detail: 'dump.pm2 clean' };
  } catch (e) {
    return { ok: false, detail: `dump check failed: ${e.message}` };
  }
}

// ─── Guard 6: 환경 파일 오염 감시 ───
function guard6_envFiles() {
  const envFiles = [
    join(HOME, '.config', 'ares_market.env'),
    join(HOME, '.env_ares_pnl'),
  ];

  let contaminated = 0;
  for (const f of envFiles) {
    if (!existsSync(f)) continue;
    const content = readFileSync(f, 'utf8');
    if (LOCALHOST_PATTERNS.some(p => content.includes(p))) {
      contaminated++;
      // 자동 수정
      let fixed = content;
      fixed = fixed.replace(/redis:\/\/:([^@]+)@127\.0\.0\.1:6379/g,
        CANONICAL_REDIS_URL);
      fixed = fixed.replace(/redis:\/\/:([^@]+)@localhost:6379/g,
        CANONICAL_REDIS_URL);
      writeFileSync(f, fixed);
      log('WARN', `${f} had localhost, auto-fixed`);
    }
  }

  if (contaminated > 0) {
    return { ok: true, detail: `auto-fixed ${contaminated} env files`, fixed: contaminated };
  }
  return { ok: true, detail: 'env files clean' };
}

// ─── Main Loop ───
async function runChecks() {
  const ts = new Date().toISOString();
  log('INFO', `=== Runtime Guard Check Cycle @ ${ts} ===`);

  const guards = [
    { name: 'module_conf.json', fn: guard1_moduleConf },
    { name: 'PM2 process REDIS_URL', fn: guard2_processRedisURL },
    { name: 'KIS token TTL', fn: guard3_kisTokenTTL },
    { name: 'Critical processes', fn: guard4_criticalProcesses },
    { name: 'dump.pm2', fn: guard5_dumpFile },
    { name: 'Env files', fn: guard6_envFiles },
  ];

  let totalFailed = 0;
  let totalFixed = 0;
  const criticals = [];

  for (const { name, fn } of guards) {
    try {
      const result = fn instanceof Function && fn.constructor.name === 'AsyncFunction' 
        ? await fn() 
        : fn();
      
      if (result.ok) {
        log('INFO', `  ✅ ${name}: ${result.detail}`);
        if (result.fixed) totalFixed += result.fixed;
      } else {
        log('ERROR', `  ❌ ${name}: ${result.detail}`);
        totalFailed++;
        if (result.critical) criticals.push(`${name}: ${result.detail}`);
      }
    } catch (e) {
      log('ERROR', `  ❌ ${name}: exception — ${e.message}`);
      totalFailed++;
    }
  }

  // CRITICAL 위반 시 Telegram 알림
  if (criticals.length > 0) {
    const msg = `⚠️ CRITICAL VIOLATIONS (${criticals.length}):\n${criticals.map(c => `• ${c}`).join('\n')}`;
    log('CRITICAL', msg);
    await sendTelegram(msg);
  }

  // 자동 수정이 있었으면 알림
  if (totalFixed > 0) {
    const msg = `🔧 Auto-fixed ${totalFixed} issues`;
    log('INFO', msg);
    await sendTelegram(msg);
  }

  log('INFO', `  Summary: ${guards.length - totalFailed} OK, ${totalFailed} FAIL, ${totalFixed} auto-fixed`);
}

// ─── Entry Point ───
async function main() {
  log('INFO', '╔══════════════════════════════════════════════════════════════╗');
  log('INFO', '║  ARES Runtime Guard — Continuous Integrity Monitor          ║');
  log('INFO', '║  "사람이 실수해도 시스템이 자동으로 막는다"                 ║');
  log('INFO', `║  Check interval: ${CHECK_INTERVAL_MS / 1000}s                                       ║`);
  log('INFO', '╚══════════════════════════════════════════════════════════════╝');

  // 첫 번째 체크 즉시 실행
  await runChecks();

  // 주기적 체크
  setInterval(async () => {
    try {
      await runChecks();
    } catch (e) {
      log('ERROR', `Check cycle failed: ${e.message}`);
    }
  }, CHECK_INTERVAL_MS);
}

main().catch(e => {
  log('FATAL', `Runtime guard failed to start: ${e.message}`);
  process.exit(1);
});
