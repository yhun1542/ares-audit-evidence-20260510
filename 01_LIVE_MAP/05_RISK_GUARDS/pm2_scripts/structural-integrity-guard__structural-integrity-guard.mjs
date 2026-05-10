/**
 * structural-integrity-guard.mjs — Structural Defect Regression Prevention Daemon
 *
 * PURPOSE: Continuously monitors the 5 structural issues fixed in v3.2 and
 *          raises alerts + takes protective action if any regression is detected.
 *
 * GUARDS:
 *   G1 — CAS Dual-Path Regression: Detects NEW WATCH/MULTI commands via delta tracking
 *   G2 — Kill-Switch Single-Writer: Ensures only kill-switch-authority writes trading:enabled
 *   G3 — ORTEX Cache Staleness: Detects if ORTEX cache HASH is stuck stale beyond threshold
 *   G4 — Risk Thresholds Integrity: Validates risk:thresholds HASH structure & CONFIG flags
 *   G5 — File Integrity: SHA256 hash verification of deployed structural upgrade files
 *
 * ACTIONS:
 *   - Structured JSON log (for PM2 log aggregation)
 *   - Redis Pub/Sub alert on channel "ares:structural:alert"
 *   - Auto-disable trading on critical violations (G1, G2, G5)
 *
 * @version 1.2.0
 */

import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { EventEmitter } from 'node:events';
import Redis from 'ioredis';

function envInt(name, defaultValue) {
  const n = Number.parseInt(process.env[name] ?? String(defaultValue), 10);
  return Number.isFinite(n) ? n : defaultValue;
}

function envBool(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined) return defaultValue;
  return !['0', 'false', 'no', 'off'].includes(String(raw).trim().toLowerCase());
}

// ── Configuration ────────────────────────────────────────────────────────────
const CFG = {
  REDIS_URL: (() => { const u = process.env.REDIS_URL || process.env.ARES_REDIS_URL; if (!u) throw new Error('REDIS_URL or ARES_REDIS_URL is required'); return u; })(),
  REDIS_PASSWORD:       process.env.REDIS_PASSWORD || '',
  CHECK_INTERVAL_MS:    60_000,       // 1 minute between full check cycles
  ALERT_CHANNEL:        'ares:structural:alert',
  TRADING_ENABLED_KEY:  'trading:enabled',

  // G1: CAS Dual-Path — delta-based: alert only if NEW WATCH/MULTI calls appear
  // (cumulative counts from before v3.2 deployment are ignored)

  // G2: Kill-Switch Single Writer
  KS_HEARTBEAT_KEY:     'kill-switch-authority:heartbeat',   // actual key from EC2
  KS_HEARTBEAT_STALE_MS: 30_000,     // 30s — if heartbeat older, KSA may be dead
  KS_ALLOWED_WRITERS:   ['kill-switch-authority'],

  // G3: ORTEX Cache Staleness — keys are HASH type: {updated_at, data}
  ORTEX_CACHE_PREFIX:   'premium:ortex',
  ORTEX_STALE_MS:       28_800_000,   // 8h — same as circuit breaker threshold
  ORTEX_SAMPLE_SYMBOLS: ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA'],

  // G4: Risk Thresholds
  RISK_HASH_KEY:        'risk:thresholds',
  RISK_REQUIRED_FIELDS: ['low', 'medium', 'high', 'critical', 'max_acceptable', 'version'],
  RISK_KEYSPACE_FLAGS:  'Kh',

  // G5: File Integrity — baseline hashes from deployment
  FILE_HASHES: {
    '/home/ubuntu/aub-trading-system/lib/atomic-cas-lock.mjs':
      '8ed924d4f96aff4bd62efc214f80191eda9e26bbb0f8614c95420d4ee36f4d2d',
    '/home/ubuntu/aub-trading-system/kill-switch-authority.mjs':
      '03459e12e76fb5ce9ee6b778872df7f704dd03fb14408724c3669270c4cbc0c8',
    '/home/ubuntu/aub-trading-system/lib/ortex-circuit-breaker.mjs':
      '298d51751ca2b2241de9d6d2dbf0d10847ad0b051b69e1a20744d8708808fc09',
    '/home/ubuntu/aub-trading-system/lib/risk-thresholds.mjs':
      '6ad743eabdbef0ddc12b80dc486791628f1366145ee90f44c734b796865a3e46',
    '/home/ubuntu/aub-trading-system/execution-reconciler.mjs':
      '22a7f8447a37aaf22bebb3ba10f6ec3afa014c1cea85655c71735b185500a68b',
  },

  // G6: Live Champion Attestation
  LIVE_MANIFEST_PATH:  process.env.ARES_LIVE_MANIFEST_PATH || '/home/ubuntu/ssot/live_manifest.json',
  LIVE_REGISTRY_PATH:  process.env.ARES_CHAMPION_REGISTRY_PATH || '/home/ubuntu/ssot/champion_registry.json',
  LIVE_RUNTIME_DIR:    process.env.ARES_LIVE_RUNTIME_DIR || '/home/ubuntu/ares_current',
  LIVE_AUTOPILOT_FILE: 'ares_v55_live_autopilot.py',
  LIVE_ADAPTER_FILE:   'engine_v65_c6_adapter.py',
  LIVE_ALPHA_CFG_FILE: 'tc6x_config.json',
  LIVE_ADAPTER_HASH:   process.env.ARES_LIVE_ADAPTER_HASH || '',
  LIVE_ALPHA_CFG_HASH: process.env.ARES_LIVE_ALPHA_CONFIG_HASH || '',

  // G7: OFG authoritative equity health / dynamic freshness SLA
  EQUITY_SCHEMA_CONTRACT: process.env.ARES_EQUITY_SCHEMA_CONTRACT || 'ares.authoritative_equity.v42',
  AUTH_EQUITY_KEY:        process.env.ARES_AUTH_EQUITY_KEY || 'ares:equity:authoritative',
  AUTH_EQUITY_HEALTH_KEY: process.env.ARES_AUTH_EQUITY_HEALTH_KEY || 'ares:equity:authoritative.health',
  OFG_VERIFIED_EQUITY_KEY: process.env.OFG_VERIFIED_EQUITY_KEY || 'ofg:equity:verified',
  OFG_STATUS_KEY:         process.env.OFG_STATUS_KEY || 'ofg:status',
  OFG_EQUITY_HEALTH_KEY:  process.env.OFG_EQUITY_HEALTH_KEY || 'ofg:equity:health',
  OFG_EQUITY_HEALTH_POLICY_KEY: process.env.OFG_EQUITY_HEALTH_POLICY_KEY || 'ofg:equity:health:policy',
  G7_EQUITY_HEALTH_POLICY: (process.env.OFG_EQUITY_HEALTH_POLICY || 'warning').trim().toLowerCase(),
  G7_EQUITY_REGULAR_ONLY: envBool('OFG_EQUITY_HEALTH_REGULAR_ONLY', true),
  G7_EQUITY_MAX_REGULAR_AGE_SEC: envInt('OFG_EQUITY_HEALTH_MAX_REGULAR_AGE_SEC', 900),
  G7_EQUITY_HALT_AFTER_SEC: envInt('OFG_EQUITY_HEALTH_HALT_AFTER_SEC', 300),
  G7_EQUITY_HALT_TTL_SEC: envInt('OFG_EQUITY_HEALTH_HALT_TTL_SEC', 600),
  G7_EQUITY_DEGRADED_SINCE_KEY: process.env.OFG_EQUITY_HEALTH_DEGRADED_SINCE_KEY || 'ofg:equity:health:degraded_since',
  TRADE_HALT_KEY: process.env.TRADE_HALT_KEY || 'trade:halt',
};


// ── Structured Logger ────────────────────────────────────────────────────────
function log(level, kind, data = {}) {
  const entry = {
    level,
    ts: new Date().toISOString(),
    component: 'structural-integrity-guard',
    kind,
    ...data,
  };
  const out = level === 'error' || level === 'warn' ? process.stderr : process.stdout;
  out.write(JSON.stringify(entry) + '\n');
}

// ── Redis Client ─────────────────────────────────────────────────────────────
const redisOpts = {
  password: CFG.REDIS_PASSWORD || undefined,
  maxRetriesPerRequest: 3,
  retryStrategy: (times) => Math.min(times * 1000, 10_000),
  lazyConnect: true,
};

let redis;
let alertPub;

function createRedisClient(name) {
  const client = new Redis(CFG.REDIS_URL, { ...redisOpts, connectionName: `sig-${name}` });
  client.on('error', (err) => log('error', 'redis-error', { client: name, err: err.message }));
  return client;
}

// ── Guard Results Accumulator ────────────────────────────────────────────────
class GuardResults extends EventEmitter {
  constructor() {
    super();
    this.results = [];
    this.criticalCount = 0;
    this.warnCount = 0;
  }

  pass(guard, message, data = {}) {
    this.results.push({ guard, status: 'PASS', message, ...data });
  }

  warn(guard, message, data = {}) {
    this.warnCount++;
    this.results.push({ guard, status: 'WARN', message, ...data });
    log('warn', `${guard}-WARN`, { message, ...data });
  }

  critical(guard, message, data = {}) {
    this.criticalCount++;
    this.results.push({ guard, status: 'CRITICAL', message, ...data });
    log('error', `${guard}-CRITICAL`, { message, ...data });
  }

  get hasCritical() { return this.criticalCount > 0; }
  get hasWarn() { return this.warnCount > 0; }
}

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G1: CAS Dual-Path Regression Detection (WATCH-based)
// ═══════════════════════════════════════════════════════════════════════════════
// The CAS dual-path bug was WATCH+MULTI together. MULTI alone is used legitimately
// by many modules (kis-balance-sync, open-orders-sync, etc.) for batching.
// Therefore we only trigger CRITICAL on new WATCH calls (delta > 0).
// MULTI delta is logged for observability but does NOT trigger alerts.
let g1_baseline = null;

function parseCommandStat(info, cmd) {
  const re = new RegExp(`cmdstat_${cmd}:calls=(\\d+)`);
  const m = info.match(re);
  return m ? parseInt(m[1], 10) : 0;
}

async function guardG1_CasDualPath(results) {
  try {
    const info = await redis.info('commandstats');
    const watchCalls = parseCommandStat(info, 'watch');
    const multiCalls = parseCommandStat(info, 'multi');

    // First run: record baseline (includes all historical calls before v3.2)
    if (g1_baseline === null) {
      g1_baseline = { watch: watchCalls, multi: multiCalls };
      log('info', 'G1-BASELINE-SET', {
        baselineWatch: watchCalls,
        baselineMulti: multiCalls,
      });
      results.pass('G1-CAS-DUAL-PATH',
        `Baseline recorded: WATCH=${watchCalls}, MULTI=${multiCalls} (historical, ignored)`,
        { watchCalls, multiCalls, delta: 0 });
      return;
    }

    // Delta since guard started
    const deltaWatch = watchCalls - g1_baseline.watch;
    const deltaMulti = multiCalls - g1_baseline.multi;

    // CRITICAL only on new WATCH calls — this is the actual CAS dual-path indicator
    if (deltaWatch > 0) {
      results.critical('G1-CAS-DUAL-PATH',
        `NEW WATCH commands detected since guard started — CAS dual-path regression!`,
        { deltaWatch, deltaMulti, totalWatch: watchCalls, totalMulti: multiCalls });
    } else {
      // MULTI delta is normal (other modules use it for batching) — PASS
      results.pass('G1-CAS-DUAL-PATH',
        `No new WATCH since guard started — single Lua CAS path confirmed`,
        { deltaWatch, deltaMulti });
    }
  } catch (err) {
    results.warn('G1-CAS-DUAL-PATH', `Check failed: ${err.message}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G2: Kill-Switch Single-Writer Enforcement
// ═══════════════════════════════════════════════════════════════════════════════
async function guardG2_KillSwitchWriter(results) {
  try {
    // Check 1: Is kill-switch-authority heartbeat fresh?
    const heartbeat = await redis.get(CFG.KS_HEARTBEAT_KEY);
    if (!heartbeat) {
      results.warn('G2-KS-WRITER', 'Kill-switch heartbeat key not found — KSA may not be running');
    } else {
      const hbValue = Number(heartbeat);
      if (isNaN(hbValue) || hbValue <= 0) {
        results.warn('G2-KS-WRITER',
          `Kill-switch heartbeat has invalid value: "${heartbeat}"`,
          { raw: heartbeat });
      } else {
        const age = Date.now() - hbValue;
        if (age > CFG.KS_HEARTBEAT_STALE_MS) {
          results.critical('G2-KS-WRITER',
            `Kill-switch heartbeat stale (${Math.round(age / 1000)}s) — KSA may be dead`,
            { heartbeat_age_ms: age });
        } else {
          results.pass('G2-KS-WRITER',
            `Kill-switch heartbeat fresh (${Math.round(age / 1000)}s)`,
            { heartbeat_age_ms: age });
        }
      }
    }

    // Check 2: Detect competing writers via CLIENT LIST
    const clientList = await redis.call('CLIENT', 'LIST');
    const legacyWriters = clientList.split('\n')
      .filter(line => /auto-kill|legacy-ks|killswitch-system|auto_killswitch/i.test(line));

    if (legacyWriters.length > 0) {
      results.critical('G2-KS-WRITER',
        `Legacy kill-switch writer detected — single-writer violated`,
        { legacyConnections: legacyWriters.length });
    } else {
      results.pass('G2-KS-WRITER', 'No legacy kill-switch writers detected');
    }

    // Check 3: Verify trading:enabled is a valid boolean string
    const tradingVal = await redis.get(CFG.TRADING_ENABLED_KEY);
    if (tradingVal !== 'true' && tradingVal !== 'false') {
      results.critical('G2-KS-WRITER',
        `trading:enabled has invalid value: "${tradingVal}" — may be corrupted by rogue writer`,
        { value: tradingVal });
    } else {
      results.pass('G2-KS-WRITER',
        `trading:enabled = ${tradingVal} (valid)`,
        { value: tradingVal });
    }
  } catch (err) {
    results.warn('G2-KS-WRITER', `Check failed: ${err.message}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G3: ORTEX Cache Staleness Detection (HASH type keys)
// ═══════════════════════════════════════════════════════════════════════════════
async function guardG3_OrtexCache(results) {
  try {
    let staleCount = 0;
    let missingCount = 0;
    let freshCount = 0;

    for (const sym of CFG.ORTEX_SAMPLE_SYMBOLS) {
      const key = `${CFG.ORTEX_CACHE_PREFIX}:${sym}`;

      // Keys are HASH type with fields: updated_at, data
      const updatedAt = await redis.hget(key, 'updated_at');

      if (!updatedAt) {
        missingCount++;
        continue;
      }

      try {
        const ts = new Date(updatedAt).getTime();
        if (isNaN(ts)) {
          staleCount++; // unparseable = stale
          continue;
        }
        const age = Date.now() - ts;
        if (age > CFG.ORTEX_STALE_MS) {
          staleCount++;
        } else {
          freshCount++;
        }
      } catch {
        staleCount++;
      }
    }

    const total = CFG.ORTEX_SAMPLE_SYMBOLS.length;

    if (staleCount === total || (staleCount + missingCount) === total) {
      results.warn('G3-ORTEX-CACHE',
        `All ORTEX cache entries are stale/missing — circuit breaker may be stuck`,
        { staleCount, missingCount, freshCount, total });
    } else if (staleCount > 0) {
      results.warn('G3-ORTEX-CACHE',
        `Some ORTEX cache entries stale`,
        { staleCount, missingCount, freshCount, total });
    } else {
      results.pass('G3-ORTEX-CACHE',
        `ORTEX cache healthy`,
        { staleCount, missingCount, freshCount, total });
    }

    // Check global last_updated timestamp
    const lastUpdated = await redis.get(`${CFG.ORTEX_CACHE_PREFIX}:last_updated`);
    if (lastUpdated) {
      const lastTs = new Date(lastUpdated).getTime();
      const globalAge = isNaN(lastTs) ? Infinity : Date.now() - lastTs;
      if (globalAge > CFG.ORTEX_STALE_MS) {
        results.warn('G3-ORTEX-GLOBAL',
          `ORTEX global last_updated is stale (${Math.round(globalAge / 3600000)}h)`,
          { lastUpdated, age_ms: globalAge });
      } else {
        results.pass('G3-ORTEX-GLOBAL',
          `ORTEX global last_updated fresh (${Math.round(globalAge / 60000)}m)`,
          { lastUpdated });
      }
    }
  } catch (err) {
    results.warn('G3-ORTEX-CACHE', `Check failed: ${err.message}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G4: Risk Thresholds Integrity
// ═══════════════════════════════════════════════════════════════════════════════
async function guardG4_RiskThresholds(results) {
  try {
    // Check 1: HASH structure
    const hash = await redis.hgetall(CFG.RISK_HASH_KEY);

    if (!hash || Object.keys(hash).length === 0) {
      results.critical('G4-RISK-THRESHOLDS',
        'risk:thresholds HASH is empty or missing — SSOT destroyed');
      return;
    }

    const missingFields = CFG.RISK_REQUIRED_FIELDS.filter(f => !(f in hash));
    if (missingFields.length > 0) {
      results.critical('G4-RISK-THRESHOLDS',
        `risk:thresholds missing required fields: ${missingFields.join(', ')}`,
        { missingFields, existingFields: Object.keys(hash) });
    } else {
      results.pass('G4-RISK-THRESHOLDS',
        'risk:thresholds HASH structure intact',
        { fields: Object.keys(hash), version: hash.version });
    }

    // Check 2: Validate threshold values are in [0, 1] range
    const numericFields = ['low', 'medium', 'high', 'critical', 'max_acceptable'];
    for (const field of numericFields) {
      if (field in hash) {
        const val = parseFloat(hash[field]);
        if (isNaN(val) || val < 0 || val > 1) {
          results.critical('G4-RISK-THRESHOLDS',
            `risk:thresholds.${field} has invalid value: ${hash[field]} (must be 0-1)`,
            { field, value: hash[field] });
        }
      }
    }

    // Check 3: Ordering invariant (low < medium < high < critical)
    const low = parseFloat(hash.low || 0);
    const medium = parseFloat(hash.medium || 0);
    const high = parseFloat(hash.high || 0);
    const critical = parseFloat(hash.critical || 0);
    if (!(low < medium && medium < high && high < critical)) {
      results.warn('G4-RISK-THRESHOLDS',
        `Threshold ordering violated: low(${low}) < medium(${medium}) < high(${high}) < critical(${critical})`,
        { low, medium, high, critical });
    }

    // Check 4: CONFIG notify-keyspace-events
    // Note: flags may be empty if risk-thresholds module hasn't run yet — this is INFO, not WARN
    try {
      const configVal = await redis.call('CONFIG', 'GET', 'notify-keyspace-events');
      const flags = configVal[1] || '';
      const hasK = flags.includes('K');
      const hash_flag = flags.includes('h');

      if (flags === '') {
        // Empty is expected if risk-thresholds hasn't initialized keyspace events yet
        results.pass('G4-RISK-CONFIG',
          'notify-keyspace-events empty (risk-thresholds timer fallback active)');
      } else if (!hasK || !hash_flag) {
        results.warn('G4-RISK-CONFIG',
          `notify-keyspace-events missing flags: ${!hasK ? 'K' : ''}${!hash_flag ? 'h' : ''} (current: "${flags}")`,
          { currentFlags: flags });
      } else {
        results.pass('G4-RISK-CONFIG',
          `notify-keyspace-events flags OK: "${flags}"`,
          { currentFlags: flags });
      }
    } catch (configErr) {
      results.pass('G4-RISK-CONFIG',
        `CONFIG GET not available (${configErr.message}) — timer fallback active`);
    }
  } catch (err) {
    results.warn('G4-RISK-THRESHOLDS', `Check failed: ${err.message}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G5: File Integrity (SHA256 Hash Verification)
// ═══════════════════════════════════════════════════════════════════════════════
async function guardG5_FileIntegrity(results) {
  for (const [filePath, expectedHash] of Object.entries(CFG.FILE_HASHES)) {
    try {
      const content = await readFile(filePath);
      const actualHash = createHash('sha256').update(content).digest('hex');

      if (actualHash !== expectedHash) {
        results.critical('G5-FILE-INTEGRITY',
          `File tampered: ${filePath.split('/').pop()}`,
          {
            file: filePath,
            expected: expectedHash.slice(0, 16) + '...',
            actual: actualHash.slice(0, 16) + '...',
          });
      } else {
        results.pass('G5-FILE-INTEGRITY',
          `Hash OK: ${filePath.split('/').pop()}`);
      }
    } catch (err) {
      if (err.code === 'ENOENT') {
        results.critical('G5-FILE-INTEGRITY',
          `File MISSING: ${filePath}`,
          { file: filePath, error: 'ENOENT' });
      } else {
        results.warn('G5-FILE-INTEGRITY',
          `Cannot read ${filePath}: ${err.message}`);
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G6: Live Champion Attestation
// ═══════════════════════════════════════════════════════════════════════════════
async function guardG6_LiveChampionAttestation(results) {
  try {
    const manifest = JSON.parse(await readFile(CFG.LIVE_MANIFEST_PATH, 'utf8'));
    const registry = JSON.parse(await readFile(CFG.LIVE_REGISTRY_PATH, 'utf8'));

    const manifestPath = manifest.engine_path;
    const manifestHash = manifest.engine_hash;
    const registryPath = registry.current_live_engine_path;
    const registryHash = registry.current_live_engine_hash;

    if (!manifestPath || !manifestHash) {
      results.critical('G6-LIVE-ATTEST', 'live_manifest missing engine_path or engine_hash');
      return;
    }

    if (manifestPath !== registryPath || manifestHash !== registryHash) {
      results.critical('G6-LIVE-ATTEST',
        'SSOT split-brain: manifest and champion_registry disagree',
        { manifestPath, registryPath, manifestHash, registryHash });
      return;
    }

    const runtimeFiles = [
      `${CFG.LIVE_RUNTIME_DIR}/${CFG.LIVE_AUTOPILOT_FILE}`,
      `${CFG.LIVE_RUNTIME_DIR}/${CFG.LIVE_ADAPTER_FILE}`,
      `${CFG.LIVE_RUNTIME_DIR}/${CFG.LIVE_ALPHA_CFG_FILE}`,
    ];

    for (const runtimeFile of runtimeFiles) {
      try {
        await readFile(runtimeFile);
      } catch (err) {
        if (err.code === 'ENOENT') {
          results.critical('G6-LIVE-ATTEST', `Runtime file missing: ${runtimeFile}`, { file: runtimeFile });
          return;
        }
        throw err;
      }
    }

    const engineContent = await readFile(manifestPath);
    const engineHash = createHash('sha256').update(engineContent).digest('hex');
    if (engineHash !== manifestHash) {
      results.critical('G6-LIVE-ATTEST',
        'Manifest engine hash mismatch',
        { manifestPath, expected: manifestHash.slice(0, 16) + '...', actual: engineHash.slice(0, 16) + '...' });
      return;
    }

    const adapterPath = `${CFG.LIVE_RUNTIME_DIR}/${CFG.LIVE_ADAPTER_FILE}`;
    const adapterContent = await readFile(adapterPath, 'utf8');
    const adapterHash = createHash('sha256').update(adapterContent).digest('hex');
    if (CFG.LIVE_ADAPTER_HASH && adapterHash !== CFG.LIVE_ADAPTER_HASH) {
      results.critical('G6-LIVE-ATTEST',
        'Adapter hash mismatch',
        { file: adapterPath, expected: CFG.LIVE_ADAPTER_HASH.slice(0, 16) + '...', actual: adapterHash.slice(0, 16) + '...' });
      return;
    }

    const cfgPath = `${CFG.LIVE_RUNTIME_DIR}/${CFG.LIVE_ALPHA_CFG_FILE}`;
    const cfgContent = await readFile(cfgPath, 'utf8');
    const cfgHash = createHash('sha256').update(cfgContent).digest('hex');
    if (CFG.LIVE_ALPHA_CFG_HASH && cfgHash !== CFG.LIVE_ALPHA_CFG_HASH) {
      results.critical('G6-LIVE-ATTEST',
        'Alpha config hash mismatch',
        { file: cfgPath, expected: CFG.LIVE_ALPHA_CFG_HASH.slice(0, 16) + '...', actual: cfgHash.slice(0, 16) + '...' });
      return;
    }

    const expectedEngineFile = String(manifestPath).split('/').pop();
    const adapterEngineFileMatch = adapterContent.match(/LIVE_TRUTH_ENGINE_FILE\s*=\s*"([^"]+)"/);
    const adapterEngineFile = adapterEngineFileMatch ? adapterEngineFileMatch[1] : null;
    if (adapterEngineFile && adapterEngineFile !== expectedEngineFile) {
      results.critical('G6-LIVE-ATTEST',
        'Adapter points to a different engine file than manifest',
        { expectedEngineFile, adapterEngineFile });
      return;
    }

    results.pass('G6-LIVE-ATTEST',
      'Live champion manifest/runtime attested',
      {
        manifestPath,
        engineHash: engineHash.slice(0, 16) + '...',
        adapterHash: adapterHash.slice(0, 16) + '...',
        alphaConfigHash: cfgHash.slice(0, 16) + '...',
      });
  } catch (err) {
    if (err.code === 'ENOENT') {
      results.critical('G6-LIVE-ATTEST', `Live attestation file missing: ${err.path || err.message}`);
    } else {
      results.warn('G6-LIVE-ATTEST', `Check failed: ${err.message}`);
    }
  }
 }

// ═══════════════════════════════════════════════════════════════════════════════
// GUARD G7: OFG Authoritative Equity Health + Dynamic Freshness SLA
// ═══════════════════════════════════════════════════════════════════════════════
function parseJsonMaybe(raw) {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

function finiteNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function g7PolicyMode() {
  return ['halt', 'enforce', 'critical'].includes(CFG.G7_EQUITY_HEALTH_POLICY) ? 'halt' : 'warning';
}

async function applyG7EquityPolicy(summary, results) {
  const mode = g7PolicyMode();
  const inRegularScope = !CFG.G7_EQUITY_REGULAR_ONLY || summary.market_session === 'REGULAR';
  const reasons = [];

  if (summary.auth_health !== 'OK') reasons.push('authoritative_health_not_ok');
  if (summary.verified_age_sec !== null && summary.verified_age_sec > CFG.G7_EQUITY_MAX_REGULAR_AGE_SEC) {
    reasons.push('verified_age_exceeds_policy_max');
  }

  const severe = inRegularScope && reasons.length > 0;
  const policy = {
    mode,
    in_regular_scope: inRegularScope,
    regular_only: CFG.G7_EQUITY_REGULAR_ONLY,
    max_regular_age_sec: CFG.G7_EQUITY_MAX_REGULAR_AGE_SEC,
    halt_after_sec: CFG.G7_EQUITY_HALT_AFTER_SEC,
    halt_ttl_sec: CFG.G7_EQUITY_HALT_TTL_SEC,
    reasons,
    action: 'OBSERVE',
  };

  if (!severe) {
    try { await redis.del(CFG.G7_EQUITY_DEGRADED_SINCE_KEY); } catch { /* ignore */ }
    policy.action = 'CLEAR';
    summary.policy = policy;
    await redis.set(CFG.OFG_EQUITY_HEALTH_POLICY_KEY, JSON.stringify(policy), 'EX', 300);
    return;
  }

  if (mode !== 'halt') {
    policy.action = 'WARNING_ONLY';
    summary.policy = policy;
    await redis.set(CFG.OFG_EQUITY_HEALTH_POLICY_KEY, JSON.stringify(policy), 'EX', 300);
    results.warn('G7-OFG-EQUITY', 'G7 equity health policy warning-only condition active', policy);
    return;
  }

  const nowMs = Date.now();
  let sinceIso = await redis.get(CFG.G7_EQUITY_DEGRADED_SINCE_KEY);
  let sinceMs = sinceIso ? Date.parse(sinceIso) : NaN;
  if (!Number.isFinite(sinceMs)) {
    sinceIso = new Date(nowMs).toISOString();
    sinceMs = nowMs;
    await redis.set(CFG.G7_EQUITY_DEGRADED_SINCE_KEY, sinceIso, 'EX', Math.max(600, CFG.G7_EQUITY_HALT_AFTER_SEC * 4));
  }

  const persistentSec = Math.max(0, Math.round((nowMs - sinceMs) / 1000));
  policy.degraded_since = sinceIso;
  policy.persistent_sec = persistentSec;

  if (persistentSec >= CFG.G7_EQUITY_HALT_AFTER_SEC) {
    policy.action = 'HALT_ASSERTED';
    const details = {
      component: 'structural-integrity-guard',
      guard: 'G7-OFG-EQUITY',
      policy,
      summary,
      ts: new Date(nowMs).toISOString(),
    };
    await redis.set(CFG.TRADE_HALT_KEY, 'true', 'EX', Math.max(60, CFG.G7_EQUITY_HALT_TTL_SEC));
    await redis.set(`${CFG.TRADE_HALT_KEY}:reason`, 'g7_equity_health_policy', 'EX', Math.max(60, CFG.G7_EQUITY_HALT_TTL_SEC));
    await redis.set(`${CFG.TRADE_HALT_KEY}:ts`, details.ts, 'EX', Math.max(60, CFG.G7_EQUITY_HALT_TTL_SEC));
    await redis.set(`${CFG.TRADE_HALT_KEY}:details`, JSON.stringify(details), 'EX', Math.max(60, CFG.G7_EQUITY_HALT_TTL_SEC));
    results.critical('G7-OFG-EQUITY', 'G7 equity health policy asserted trade:halt', details);
  } else {
    policy.action = 'PENDING_HALT';
    results.warn('G7-OFG-EQUITY', 'G7 equity health policy degraded persistence timer running', policy);
  }

  summary.policy = policy;
  await redis.set(CFG.OFG_EQUITY_HEALTH_POLICY_KEY, JSON.stringify(policy), 'EX', 300);
}

async function guardG7_OfgEquityHealth(results) {
  const summary = {
    ts: new Date().toISOString(),
    contract: CFG.EQUITY_SCHEMA_CONTRACT,
    auth_health: null,
    auth_age_sec: null,
    verified_age_sec: null,
    verified_sla_sec: null,
    verified_degraded: null,
    market_session: null,
    source: null,
    warnings: [],
  };

  try {
    const [authHealthRaw, authRaw, verifiedRaw, ofgStatusRaw] = await Promise.all([
      redis.get(CFG.AUTH_EQUITY_HEALTH_KEY),
      redis.get(CFG.AUTH_EQUITY_KEY),
      redis.get(CFG.OFG_VERIFIED_EQUITY_KEY),
      redis.get(CFG.OFG_STATUS_KEY),
    ]);

    const auth = parseJsonMaybe(authRaw);
    const verified = parseJsonMaybe(verifiedRaw);
    const ofgStatus = parseJsonMaybe(ofgStatusRaw);

    summary.auth_health = authHealthRaw || auth?.health?.status || auth?.health || null;

    if (summary.auth_health !== 'OK') {
      summary.warnings.push('authoritative_health_not_ok');
      results.warn('G7-OFG-EQUITY',
        `ares:equity:authoritative.health is not OK: ${summary.auth_health || 'missing'}`,
        { key: CFG.AUTH_EQUITY_HEALTH_KEY, value: summary.auth_health });
    } else {
      results.pass('G7-OFG-EQUITY', 'authoritative equity health OK', { key: CFG.AUTH_EQUITY_HEALTH_KEY });
    }

    if (!auth) {
      summary.warnings.push('authoritative_payload_missing_or_invalid');
      results.warn('G7-OFG-EQUITY', 'ares:equity:authoritative payload missing or invalid JSON', { key: CFG.AUTH_EQUITY_KEY });
    } else {
      const authSchema = auth.schema || auth.contract || auth.equity_schema_contract || null;
      if (authSchema !== CFG.EQUITY_SCHEMA_CONTRACT) {
        summary.warnings.push('schema_contract_mismatch');
        results.warn('G7-OFG-EQUITY',
          `authoritative equity schema contract mismatch: ${authSchema || 'missing'}`,
          { expected: CFG.EQUITY_SCHEMA_CONTRACT, actual: authSchema });
      } else {
        results.pass('G7-OFG-EQUITY', 'authoritative equity schema contract matches', { schema: authSchema });
      }

      const authTs = auth.ts || auth.updated_at || auth.timestamp || null;
      const authMs = authTs ? Date.parse(authTs) : NaN;
      if (Number.isFinite(authMs)) {
        summary.auth_age_sec = Math.max(0, Math.round((Date.now() - authMs) / 1000));
      }
    }

    if (!verified) {
      summary.warnings.push('ofg_verified_missing_or_invalid');
      results.warn('G7-OFG-EQUITY', 'ofg:equity:verified missing or invalid JSON', { key: CFG.OFG_VERIFIED_EQUITY_KEY });
    } else {
      summary.verified_age_sec = finiteNumber(verified.age_sec ?? verified.equity_age_sec);
      summary.verified_sla_sec = finiteNumber(verified.sla_sec ?? verified.max_age_sec ?? verified.freshness_sla_sec);
      summary.verified_degraded = Boolean(verified.degraded);
      summary.market_session = verified.market_session || verified.session || null;
      summary.source = verified.source || null;

      if (summary.verified_sla_sec !== null && summary.verified_age_sec !== null && summary.verified_age_sec > summary.verified_sla_sec) {
        summary.warnings.push('verified_age_exceeds_sla');
        results.warn('G7-OFG-EQUITY',
          `ofg:equity:verified age ${summary.verified_age_sec}s exceeds SLA ${summary.verified_sla_sec}s`,
          { age_sec: summary.verified_age_sec, sla_sec: summary.verified_sla_sec, market_session: summary.market_session, source: summary.source });
      } else if (summary.verified_sla_sec !== null && summary.verified_age_sec !== null) {
        results.pass('G7-OFG-EQUITY',
          `ofg:equity:verified age within dynamic SLA (${summary.verified_age_sec}s <= ${summary.verified_sla_sec}s)`,
          { age_sec: summary.verified_age_sec, sla_sec: summary.verified_sla_sec, market_session: summary.market_session, source: summary.source });
      } else {
        summary.warnings.push('verified_age_or_sla_missing');
        results.warn('G7-OFG-EQUITY', 'ofg:equity:verified missing age_sec or sla_sec', { payload_keys: Object.keys(verified) });
      }

      if (summary.verified_degraded) {
        summary.warnings.push('verified_degraded_true');
        results.warn('G7-OFG-EQUITY', 'ofg:equity:verified.degraded is true', { source: summary.source, market_session: summary.market_session });
      } else {
        results.pass('G7-OFG-EQUITY', 'ofg:equity:verified.degraded is false');
      }
    }

    const statusHealth = ofgStatus?.equity_health || ofgStatus?.metadata?.equity_health || null;
    if (statusHealth?.degraded === true) {
      summary.warnings.push('ofg_status_equity_health_degraded');
      results.warn('G7-OFG-EQUITY', 'ofg:status.equity_health.degraded is true', statusHealth);
    }

    await applyG7EquityPolicy(summary, results);
    summary.status = summary.policy?.action === 'HALT_ASSERTED' ? 'CRITICAL' : (summary.warnings.length === 0 ? 'OK' : 'WARN');
    await redis.set(CFG.OFG_EQUITY_HEALTH_KEY, JSON.stringify(summary), 'EX', 300);
  } catch (err) {
    summary.status = 'WARN';
    summary.error = err.message;
    try { await redis.set(CFG.OFG_EQUITY_HEALTH_KEY, JSON.stringify(summary), 'EX', 300); } catch { /* ignore */ }
    results.warn('G7-OFG-EQUITY', `Check failed: ${err.message}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Main Check Cycle
// ═══════════════════════════════════════════════════════════════════════════════
let checkCount = 0;
let lastCriticalAt = null;

async function runCheckCycle() {
  checkCount++;
  const results = new GuardResults();
  const startMs = Date.now();

  try {
    await guardG1_CasDualPath(results);
    await guardG2_KillSwitchWriter(results);
    await guardG3_OrtexCache(results);
    await guardG4_RiskThresholds(results);
    await guardG5_FileIntegrity(results);
    await guardG6_LiveChampionAttestation(results);
    await guardG7_OfgEquityHealth(results);
  } catch (err) {
    log('error', 'CHECK_CYCLE_ERROR', { err: err.message, checkCount });
  }

  const elapsed = Date.now() - startMs;

  const passCount = results.results.filter(r => r.status === 'PASS').length;
  const warnCount = results.warnCount;
  const criticalCount = results.criticalCount;
  const totalChecks = results.results.length;

  log('info', 'CHECK_CYCLE_COMPLETE', {
    cycle: checkCount,
    elapsed_ms: elapsed,
    total: totalChecks,
    pass: passCount,
    warn: warnCount,
    critical: criticalCount,
  });

  // Publish alerts for warnings and criticals
  if (results.hasCritical || results.hasWarn) {
    const alertPayload = {
      ts: new Date().toISOString(),
      cycle: checkCount,
      criticals: results.results.filter(r => r.status === 'CRITICAL'),
      warnings: results.results.filter(r => r.status === 'WARN'),
    };

    try {
      await alertPub.publish(CFG.ALERT_CHANNEL, JSON.stringify(alertPayload));
    } catch (pubErr) {
      log('error', 'ALERT_PUBLISH_FAILED', { err: pubErr.message });
    }
  }

  // Auto-protect: If G1 (CAS dual-path), G2 (kill-switch writer), or G5 (file integrity)
  // is critical, disable trading immediately as a protective measure
  if (results.hasCritical) {
    lastCriticalAt = Date.now();
    const criticalGuards = results.results
      .filter(r => r.status === 'CRITICAL')
      .map(r => r.guard);

    const tradingCritical = criticalGuards.some(g =>
      g === 'G1-CAS-DUAL-PATH' || g === 'G2-KS-WRITER' || g === 'G5-FILE-INTEGRITY'
    );

    if (tradingCritical) {
      log('error', 'AUTO_PROTECT_TRIGGERED', {
        action: 'disable-trading',
        reason: criticalGuards.join(', '),
      });
      try {
        await redis.set(CFG.TRADING_ENABLED_KEY, 'false');
        log('warn', 'TRADING_DISABLED_BY_GUARD', {
          reason: `Structural integrity violation: ${criticalGuards.join(', ')}`,
        });
      } catch (setErr) {
        log('error', 'AUTO_PROTECT_SET_FAILED', { err: setErr.message });
      }
    }
  }

  // Update heartbeat
  try {
    await redis.set('structural_integrity_guard:heartbeat', String(Date.now()), 'EX', 120);
    await redis.set('structural_integrity_guard:last_check', JSON.stringify({
      cycle: checkCount,
      ts: new Date().toISOString(),
      pass: passCount,
      warn: warnCount,
      critical: criticalCount,
      elapsed_ms: elapsed,
      guards: results.results,
    }), 'EX', 300);
  } catch (hbErr) {
    log('error', 'HEARTBEAT_SET_FAILED', { err: hbErr.message });
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Lifecycle
// ═══════════════════════════════════════════════════════════════════════════════
let intervalHandle;

async function start() {
  log('info', 'STARTING', {
    version: '1.3.0-equity-health',
    checkInterval: CFG.CHECK_INTERVAL_MS,
    guards: ['G1-CAS-DELTA', 'G2-KS-WRITER', 'G3-ORTEX-HASH', 'G4-RISK', 'G5-FILE', 'G6-LIVE-ATTEST', 'G7-OFG-EQUITY'],
    monitoredFiles: Object.keys(CFG.FILE_HASHES).length,
    liveAttestationFiles: 5,
  });

  redis = createRedisClient('main');
  alertPub = createRedisClient('alert-pub');

  await redis.connect();
  await alertPub.connect();

  log('info', 'REDIS_CONNECTED');

  // Run first check immediately
  await runCheckCycle();

  // Schedule periodic checks
  intervalHandle = setInterval(runCheckCycle, CFG.CHECK_INTERVAL_MS);

  log('info', 'GUARD_ACTIVE', {
    nextCheck: new Date(Date.now() + CFG.CHECK_INTERVAL_MS).toISOString(),
  });
}

async function shutdown(signal) {
  log('info', 'SHUTDOWN', { signal });
  if (intervalHandle) clearInterval(intervalHandle);

  try {
    await redis?.quit();
    await alertPub?.quit();
  } catch { /* ignore */ }

  process.exit(0);
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
  log('error', 'UNCAUGHT_EXCEPTION', { err: err.message, stack: err.stack });
  shutdown('uncaughtException');
});

// ── Health Check Endpoint (for external monitoring) ──────────────────────────
const healthServer = createServer((req, res) => {
  if (req.url === '/healthz') {
    const healthy = !lastCriticalAt || (Date.now() - lastCriticalAt > 300_000);
    res.writeHead(healthy ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: healthy ? 'healthy' : 'degraded',
      checkCount,
      lastCriticalAt: lastCriticalAt ? new Date(lastCriticalAt).toISOString() : null,
      uptime: process.uptime(),
    }));
  } else {
    res.writeHead(404);
    res.end();
  }
});

healthServer.listen(9877, '0.0.0.0', () => {
  log('info', 'HEALTH_ENDPOINT', { port: 9877, path: '/healthz' });
});

// ── Start ────────────────────────────────────────────────────────────────────
start().catch((err) => {
  log('error', 'STARTUP_FAILED', { err: err.message });
  process.exit(1);
});
