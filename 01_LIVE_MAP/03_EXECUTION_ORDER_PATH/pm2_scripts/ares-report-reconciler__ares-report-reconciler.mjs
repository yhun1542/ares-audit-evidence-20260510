#!/usr/bin/env node
// ARES Structural Patch 2026-04-29
// Reconciles daily report order counts against Redis execution/order streams.

import Redis from 'ioredis';

function resolveRedisUrl() {
  const url = process.env.REDIS_URL || process.env.ARES_REDIS_URL || '';
  if (!url) throw new Error('REDIS_URL or ARES_REDIS_URL is required');
  return url;
}
const redis = new Redis(resolveRedisUrl(), { connectionName: 'ares-report-reconciler', maxRetriesPerRequest: 3 });
const STREAMS = (process.env.REPORT_RECON_STREAMS || 'emarkos:v6:order:intent,emarkos:v1:execution,ares:order_executions,stream:execution_quality').split(',');
const INTERVAL_MS = Number(process.env.REPORT_RECON_INTERVAL_MS || 300000);

function log(level, msg, data = {}) { console.log(JSON.stringify({ ts: new Date().toISOString(), svc: 'ares-report-reconciler', level, msg, ...data })); }
function ymd(d = new Date()) { return d.toISOString().slice(0, 10); }
function startEndMs(dateStr) {
  const start = Date.parse(dateStr + 'T00:00:00.000Z');
  return { start, end: start + 86400_000 - 1 };
}
async function countStream(stream, start, end) {
  try {
    const rows = await redis.xrange(stream, `${start}-0`, `${end}-999999`);
    const bySide = {}; const bySymbol = {};
    let qtyAbs = 0;
    for (const [id, fields] of rows) {
      const obj = {};
      for (let i = 0; i < fields.length; i += 2) obj[String(fields[i])] = String(fields[i+1]);
      let payload = obj.json || obj.payload || null;
      if (payload) { try { Object.assign(obj, JSON.parse(payload)); } catch {} }
      const side = String(obj.side || obj.order_side || '').toUpperCase();
      const sym = String(obj.symbol || obj.ticker || 'UNKNOWN').toUpperCase();
      const qty = Math.abs(Number(obj.qty || obj.shares || obj.delta_shares || obj.filled_qty || 0));
      if (side) bySide[side] = (bySide[side] || 0) + 1;
      if (sym) bySymbol[sym] = (bySymbol[sym] || 0) + 1;
      if (Number.isFinite(qty)) qtyAbs += qty;
    }
    return { stream, count: rows.length, qtyAbs, bySide, topSymbols: Object.entries(bySymbol).sort((a,b)=>b[1]-a[1]).slice(0,20) };
  } catch (e) {
    return { stream, error: String(e?.message || e), count: null };
  }
}

async function reconcile(dateStr = ymd()) {
  const { start, end } = startEndMs(dateStr);
  const streams = [];
  for (const s of STREAMS) streams.push(await countStream(s, start, end));
  const execution = streams.find(s => s.stream === 'emarkos:v1:execution') || { count: 0 };
  const intent = streams.find(s => s.stream === 'emarkos:v6:order:intent') || { count: 0 };
  const summary = { schema: 'ares.report_reconciler.v1', date: dateStr, ts: new Date().toISOString(), streams, orders_sent_min: execution.count || 0, intents_seen: intent.count || 0 };

  // If any legacy daily report claims zero orders while execution stream has orders, mark invalid.
  let legacy = null;
  for (const k of [`ops:daily_core:${dateStr}`, `daily:core:${dateStr}`, `ares:daily_core:${dateStr}`]) {
    const raw = await redis.get(k).catch(() => null);
    if (raw) { try { legacy = { key: k, value: JSON.parse(raw) }; } catch { legacy = { key: k, raw }; } break; }
  }
  if (legacy) summary.legacy_report = legacy;
  const legacyOrders = Number(legacy?.value?.orders_sent ?? legacy?.value?.ordersSent ?? NaN);
  summary.validity = 'OK';
  summary.issues = [];
  if (Number.isFinite(legacyOrders) && legacyOrders === 0 && (execution.count || 0) > 0) {
    summary.validity = 'INVALID_REPORT';
    summary.issues.push('LEGACY_REPORT_ZERO_BUT_EXECUTION_STREAM_NONZERO');
  }

  await redis.set(`ops:report:orders:${dateStr}`, JSON.stringify(summary), 'EX', 86400 * 14);
  await redis.xadd('ops:report:reconciler:audit', 'MAXLEN', '~', '5000', '*', 'json', JSON.stringify(summary));
  if (summary.validity !== 'OK') await redis.xadd('ops:report:reconciler:alerts', 'MAXLEN', '~', '5000', '*', 'json', JSON.stringify(summary));
  log(summary.validity === 'OK' ? 'info' : 'warn', 'report_reconciled', { date: dateStr, validity: summary.validity, execution_count: execution.count, issues: summary.issues });
}

async function main() {
  const dateArg = process.argv.find(a => a.startsWith('--date='));
  if (dateArg) { await reconcile(dateArg.split('=')[1]); process.exit(0); }
  await reconcile(ymd(new Date(Date.now() - 24*3600_000)));
  await reconcile(ymd());
  setInterval(() => reconcile(ymd()).catch(e => log('error','cycle_failed',{err:String(e)})), INTERVAL_MS);
}
main().catch(e => { log('error','fatal',{err:String(e?.stack || e)}); process.exit(1); });
