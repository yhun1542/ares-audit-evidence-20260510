/**
 * ops-dashboard-api.mjs — Backend API for ARES 4-Panel Ops Dashboard
 * ====================================================================
 * v1.1.0 — Session 3, GAP-10: Added missing dashboard fields
 *
 * CHANGES from v1.0.0:
 *   Execution Panel: + DLQ count
 *   Freshness Panel: + KIS token freshness, + FRED freshness, + ORTEX freshness
 *   Risk Panel:      + exposure current vs target
 *   Control Panel:   + recovery ramp order rejection rate termination condition
 *
 * @version 1.1.1-final
 * @date 2026-04-15
 */

import { getRedis } from '../lib/redis-connection.mjs';
import http from 'http';

const PORT = parseInt(process.env.OPS_DASHBOARD_PORT || '8090');
const BIND_HOST = process.env.OPS_DASHBOARD_BIND || '127.0.0.1';
const AUTH_TOKEN = process.env.OPS_DASHBOARD_TOKEN || '';
const ALLOWED_ORIGIN = process.env.OPS_DASHBOARD_ALLOWED_ORIGIN || '';
let redis;

function normalizeIp(ip = '') {
  return String(ip).replace('::ffff:', '');
}

function isLoopback(req) {
  const ip = normalizeIp(req.socket?.remoteAddress || '');
  return ip === '127.0.0.1' || ip === '::1';
}

function isAuthorized(req) {
  if (isLoopback(req)) return true;
  if (!AUTH_TOKEN) return false;
  return (req.headers['authorization'] || '') === `Bearer ${AUTH_TOKEN}`;
}

function log(level, msg, data = {}) {
  console.log(JSON.stringify({ ts: new Date().toISOString(), level, proc: 'ops-dashboard-api', msg, ...data }));
}

function safeJson(raw) {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return raw; }
}

async function getRiskPanel() {
  const pipe = redis.pipeline();
  pipe.get('gpu:regime:operational:current');       // 0
  pipe.get('ares:dtch:current_dd');                 // 1
  pipe.get('ares:dtch:hedge_pct');                  // 2
  pipe.get('ares:dtch:trigger_level');              // 3
  pipe.hgetall('regime:current');                   // 4
  pipe.hgetall('regime:final:current');             // 5
  pipe.get('risk:evidence:crash_guard');            // 6
  pipe.get('ares:equity:snapshot');                 // 7
  pipe.get('ares:equity:lifetime_peak');            // 8
  pipe.get('ares:equity:session_peak');             // 9
  pipe.get('ares:guard:hard_stop:violations');      // 10
  // [GAP-10] Exposure current vs target
  pipe.get('ares:exposure:current');                // 11
  pipe.get('ares:exposure:target');                 // 12
  pipe.get('ares:dtch:risk_multiplier');            // 13

  const results = await pipe.exec();
  const val = (i) => results[i]?.[1];

  const gpuState = safeJson(val(0));
  const equitySnap = safeJson(val(7));
  const hardStop = safeJson(val(10));

  // [GAP-10] Compute exposure from equity snapshot if dedicated keys missing
  let exposureCurrent = parseFloat(val(11) || '0');
  let exposureTarget = parseFloat(val(12) || '0');
  if (!exposureCurrent && equitySnap) {
    const mv = parseFloat(equitySnap.positions_mv || 0);
    const total = parseFloat(equitySnap.total || 1);
    if (total > 0) exposureCurrent = mv / total;
  }

  return {
    panel: 'risk',
    gpu: {
      state: gpuState?.state || 'UNKNOWN',
      risk_score: gpuState?.risk_score || 0,
      crash_score: gpuState?.crash_score || 0,
      risk_multiplier: gpuState?.risk_multiplier || 0,
      stale: gpuState?.stale || false,
      reasons: gpuState?.reasons || [],
      ts: gpuState?.ts || null,
    },
    dtch: {
      current_dd: parseFloat(val(1) || '0'),
      hedge_pct: parseFloat(val(2) || '0'),
      trigger_level: parseFloat(val(3) || '0'),
      risk_multiplier: parseFloat(val(13) || '1'),
    },
    regime: {
      current: val(4) || {},
      final: val(5) || {},
    },
    crash_guard: safeJson(val(6)),
    equity: {
      total: equitySnap?.total || 0,
      cash: equitySnap?.cash || 0,
      positions_mv: equitySnap?.positions_mv || 0,
      lifetime_peak: parseFloat(val(8) || '0'),
      session_peak: parseFloat(val(9) || '0'),
      ts: equitySnap?.ts || null,
    },
    // [GAP-10] Exposure current vs target
    exposure: {
      current: parseFloat(exposureCurrent.toFixed(4)),
      target: parseFloat(exposureTarget.toFixed(4)),
      delta: parseFloat((exposureCurrent - exposureTarget).toFixed(4)),
    },
    hard_stop: {
      violations: hardStop?.violations?.length || 0,
      mode: hardStop?.mode || 'UNKNOWN',
      details: hardStop?.violations || [],
    },
    ts: new Date().toISOString(),
  };
}

async function getControlPanel() {
  const pipe = redis.pipeline();
  pipe.get('ares:final_trade_gate');                // 0
  pipe.get('ares:final_trade_gate:reason');          // 1
  pipe.get('ares:final_trade_gate:heartbeat');       // 2
  pipe.get('trading:enabled');                       // 3
  pipe.get('trading:disable_reason');                // 4
  pipe.get('trade:halt');                            // 5
  pipe.get('ares:halt:active');                      // 6
  pipe.get('ares:halt:severity');                    // 7
  pipe.get('ares:halt:request_summary');             // 8
  pipe.get('guardrail:status');                      // 9
  pipe.get('ares:guard:recovery_ramp:active');       // 10
  pipe.get('ares:guard:recovery_ramp:current_step'); // 11
  pipe.get('ares:guard:recovery_ramp:max_exposure_increase'); // 12
  pipe.get('ares:guard:recovery_ramp:canary_done');  // 13
  pipe.get('ares:guard:recovery_ramp:started_at');   // 14
  pipe.get('ofg:gate');                              // 15  // [FIX BUG-7] was ofg:gate:status
  pipe.get('emarkos:v1:mode');                       // 16
  // [GAP-10] Recovery ramp rejection rate
  pipe.get('ares:guard:recovery_ramp:rejection_rate'); // 17
  pipe.get('ares:guard:recovery_ramp:rejection_threshold'); // 18
  // Guardian last_halt_* keys are audit breadcrumbs, not current halt inputs.
  pipe.get('ares:guardian:last_halt_reason');              // 19
  pipe.get('ares:guardian:last_halt_ts');                  // 20
  pipe.get('ares:guardian:stale_halt_clear:last_ts');      // 21
  pipe.get('ares:guardian:stale_halt_clear:last_reason');  // 22
  // Supervisor-owned order-intent-executor state, used to explain stopped/online transitions.
  pipe.get('trade-actuator-supervisor:state');             // 23
  pipe.get('trade-actuator-supervisor:heartbeat');         // 24

  const results = await pipe.exec();
  const val = (i) => results[i]?.[1];

  const ftgHb = parseInt(val(2) || '0');
  const ftgAge = ftgHb > 0 ? Date.now() - ftgHb : Infinity;
  const supervisorHb = parseInt(val(24) || '0');
  const supervisorAge = supervisorHb > 0 ? Date.now() - supervisorHb : Infinity;
  let supervisorState = null;
  try { supervisorState = val(23) ? JSON.parse(val(23)) : null; } catch { supervisorState = { parse_error: true }; }

  return {
    panel: 'control',
    ftg: {
      gate: val(0) || 'MISSING',
      reason: val(1) || '',
      heartbeat_age_sec: ftgAge === Infinity ? null : Math.round(ftgAge / 1000),
      stale: ftgAge > 90_000,
    },
    trading: {
      enabled: val(3) || 'MISSING',
      disable_reason: val(4) || '',
    },
    halts: {
      trade_halt: val(5) || '',
      ares_halt_active: val(6) || '',
      ares_halt_severity: val(7) || '',
      ares_halt_summary: val(8) || '',
      guardrail_status: val(9) || '',
      ofg_gate: val(15) || '',
      current_halt_active: ['1', 'true', 'yes', 'on'].includes(String(val(5) || '').toLowerCase()) || ['1', 'true', 'yes', 'on'].includes(String(val(6) || '').toLowerCase()),
      guardian_audit: {
        last_halt_reason: val(19) || '',
        last_halt_ts: val(20) || '',
        stale_clear_ts: val(21) || '',
        stale_clear_reason: val(22) || '',
        semantic: 'audit_only_not_current_halt_input',
      },
    },
    actuator_supervisor: {
      state: supervisorState,
      heartbeat_age_sec: supervisorAge === Infinity ? null : Math.round(supervisorAge / 1000),
      stale: supervisorAge > 90_000,
      semantic: 'order-intent-executor lifecycle owner',
    },
    ramp: {
      active: val(10) === 'true',
      step: parseInt(val(11) || '0'),
      max_exposure_increase: parseFloat(val(12) || '0'),
      canary_done: val(13) === 'true',
      started_at: val(14) ? new Date(parseInt(val(14))).toISOString() : null,
      // [GAP-10] Rejection rate termination condition
      rejection_rate: parseFloat(val(17) || '0'),
      rejection_threshold: parseFloat(val(18) || '0.3'),
      rejection_should_terminate: parseFloat(val(17) || '0') > parseFloat(val(18) || '0.3'),
    },
    mode: val(16) || 'UNKNOWN',
    ts: new Date().toISOString(),
  };
}

async function getExecutionPanel() {
  const pipe = redis.pipeline();
  pipe.get('live:cycle:latest');                     // 0
  pipe.get('live:champion:telemetry');               // 1
  pipe.get('ofg:metrics');                           // 2
  pipe.get('ofg:drawdown:current');                  // 3
  pipe.get('ofg:drawdown:session');                  // 4
  pipe.get('ofg:equity:verified');                   // 5
  pipe.get('ares:rebalance:count:' + getNYDay());    // 6
  pipe.get('policy:max_daily_rebalance');             // 7
  // [GAP-10] DLQ count
  pipe.xlen('ares:execution:dlq');                   // 8
  pipe.get(`kpi:gap:rejected:${getNYDay()}`);        // 9
  pipe.get(`kpi:gap:partial:${getNYDay()}`);         // 10

  const results = await pipe.exec();
  const val = (i) => results[i]?.[1];

  const cycle = safeJson(val(0));
  const telemetry = safeJson(val(1));

  return {
    panel: 'execution',
    last_cycle: cycle ? {
      cycle_id: cycle.cycle_id,
      decision: cycle.decision,
      regime: cycle.regime,
      emitted: cycle.emitted || 0,
      failed: cycle.failed || 0,
      cycleNotional: cycle.cycleNotional || 0,
      champion_scale: cycle.champion_scale || 0,
      crash_scale: cycle.crash_scale || 1,
      gov_scale: cycle.gov_scale || 1,
      vix: cycle.vix || 0,
      dailyPnlPct: cycle.dailyPnlPct || 0,
      posCount: cycle.posCount || 0,
      ts: cycle.ts,
    } : null,
    champion: telemetry ? {
      stage1_hedge: telemetry.stage1_hedge,
      stage2_crisis: telemetry.stage2_hedge_crisis,
      stage3_regime: telemetry.stage3_hedge_regime,
      stage4_vol: telemetry.stage4_vol_scale,
    } : null,
    ofg: {
      drawdown_current: parseFloat(val(3) || '0'),
      drawdown_session: parseFloat(val(4) || '0'),
      equity_verified: parseFloat(val(5) || '0'),
    },
    rebalance: {
      today_count: parseInt(val(6) || '0'),
      max_daily: parseInt(val(7) || '50'),
    },
    // [GAP-10] DLQ and rejection metrics
    dlq: {
      count: parseInt(val(8) || '0'),
    },
    daily_rejections: parseInt(val(9) || '0'),
    daily_partials: parseInt(val(10) || '0'),
    ts: new Date().toISOString(),
  };
}

async function getFreshnessPanel() {
  const keys = [
    { key: 'ares:equity:updated_at',                label: 'Equity Snapshot',        maxStaleSec: 60 },
    { key: 'ares:final_trade_gate:heartbeat',        label: 'Final Trade Gate',       maxStaleSec: 90, isEpochMs: true },
    { key: 'gpu:regime:operational:heartbeat',        label: 'GPU Risk Guard',         maxStaleSec: 120, isEpochMs: true },
    { key: 'ares:guard:recovery_ramp:heartbeat',      label: 'Recovery Ramp',          maxStaleSec: 120, isEpochMs: true },
    { key: 'truth:ts:broker_snapshot_ms',             label: 'Broker Truth',           maxStaleSec: 120, isEpochMs: true },
    { key: 'ssot:target:v2:ts',                       label: 'SSOT Targets',           maxStaleSec: 900, isEpochMs: true },
    { key: 'live:cycle:latest',                       label: 'Last Trade Cycle',       maxStaleSec: 300, isJson: true, tsField: 'ts' },
    { key: 'kpi:perf_eval:ts',                        label: 'Perf Eval',             maxStaleSec: 120 },
    { key: 'emarkos:v1:perf_eval_ready',              label: 'Perf Eval Ready',        maxStaleSec: 120 },
    // [GAP-10] KIS token freshness
    { key: 'kis:access_token:expires_at',             label: 'KIS Token',              maxStaleSec: 3600, isEpochMs: true, invertAge: true },
    { key: 'kis:token:refresh_heartbeat',             label: 'KIS Token Refresher',    maxStaleSec: 3600, isEpochMs: true },
    // [GAP-10] FRED data freshness
    { key: 'ares:fred:last_update',                   label: 'FRED Data',              maxStaleSec: 86400 },
    // [GAP-10] ORTEX data freshness
    { key: 'ares:ortex:last_update',                  label: 'ORTEX Short Data',       maxStaleSec: 86400 },
  ];

  const pipe = redis.pipeline();
  for (const k of keys) pipe.get(k.key);
  const results = await pipe.exec();

  const now = Date.now();
  const items = keys.map((k, i) => {
    const raw = results[i]?.[1];
    let ageMs = null;
    let tsIso = null;

    if (raw) {
      if (k.isEpochMs) {
        const epoch = parseInt(raw);
        if (k.invertAge) {
          // For expiry timestamps: show time REMAINING not elapsed
          ageMs = epoch - now; // positive = still valid
          tsIso = new Date(epoch).toISOString();
        } else {
          ageMs = now - epoch;
          tsIso = new Date(epoch).toISOString();
        }
      } else if (k.isJson) {
        try {
          const obj = JSON.parse(raw);
          const ts = obj[k.tsField];
          if (ts) { ageMs = now - new Date(ts).getTime(); tsIso = ts; }
        } catch {}
      } else {
        const d = new Date(raw);
        if (!isNaN(d.getTime())) { ageMs = now - d.getTime(); tsIso = raw; }
        else {
          // [FIX BUG-8] Only try epoch ms if ISO parse failed AND raw looks numeric
          if (/^\d{13,}$/.test(raw)) {
            const ep = parseInt(raw);
            ageMs = now - ep;
            tsIso = new Date(ep).toISOString();
          }
        }
      }
    }

    const ageSec = ageMs !== null ? Math.round(ageMs / 1000) : null;
    let status;
    if (ageSec === null) {
      status = 'MISSING';
    } else if (k.invertAge) {
      // For expiry: negative age means expired
      status = ageSec < 0 ? 'STALE' : ageSec < 600 ? 'WARNING' : 'OK';
    } else {
      status = ageSec > k.maxStaleSec ? 'STALE' : 'OK';
    }

    return {
      key: k.key,
      label: k.label,
      age_sec: ageSec,
      max_stale_sec: k.maxStaleSec,
      status,
      ts: tsIso,
    };
  });

  const okCount = items.filter(i => i.status === 'OK').length;
  const staleCount = items.filter(i => i.status === 'STALE').length;
  const warningCount = items.filter(i => i.status === 'WARNING').length;
  const missingCount = items.filter(i => i.status === 'MISSING').length;

  return {
    panel: 'freshness',
    summary: { ok: okCount, stale: staleCount, warning: warningCount, missing: missingCount, total: items.length },
    items,
    ts: new Date().toISOString(),
  };
}

function getNYDay() {
  const ny = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
  return ny.toISOString().slice(0, 10).replace(/-/g, '');
}

async function handleRequest(req, res) {
  res.setHeader('Content-Type', 'application/json');
  const origin = req.headers.origin || '';
  if (ALLOWED_ORIGIN && origin === ALLOWED_ORIGIN) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
    res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
    res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  }
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = req.url?.split('?')[0];
  if (url !== '/health' && !isAuthorized(req)) {
    res.writeHead(401);
    res.end(JSON.stringify({ error: 'unauthorized' }));
    return;
  }

  try {
    let body;
    switch (url) {
      case '/api/risk':      body = await getRiskPanel(); break;
      case '/api/control':   body = await getControlPanel(); break;
      case '/api/execution': body = await getExecutionPanel(); break;
      case '/api/freshness': body = await getFreshnessPanel(); break;
      case '/api/all':
        const [risk, control, execution, freshness] = await Promise.all([
          getRiskPanel(), getControlPanel(), getExecutionPanel(), getFreshnessPanel(),
        ]);
        body = { risk, control, execution, freshness, ts: new Date().toISOString() };
        break;
      case '/health':
        body = { status: 'ok', version: '1.1.2-hardened', ts: new Date().toISOString() };
        break;
      default:
        res.writeHead(404);
        res.end(JSON.stringify({ error: 'Not found' }));
        return;
    }
    res.writeHead(200);
    res.end(JSON.stringify(body, null, 2));
  } catch (err) {
    log('ERROR', 'Request failed', { url, error: err.message });
    res.writeHead(500);
    res.end(JSON.stringify({ error: err.message }));
  }
}

async function main() {
  redis = await getRedis();
  const server = http.createServer(handleRequest);
  server.listen(PORT, BIND_HOST, () => {
    log('INFO', `Ops Dashboard API v1.1.2-hardened listening on ${BIND_HOST}:${PORT} (auth + scoped CORS)`);
  });
}

main().catch(err => {
  log('ERROR', 'Fatal', { error: err.message });
  throw err;
});
