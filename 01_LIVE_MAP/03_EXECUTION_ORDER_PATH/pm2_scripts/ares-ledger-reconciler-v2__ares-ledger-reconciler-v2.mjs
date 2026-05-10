#!/usr/bin/env node
'use strict';

import { createRequire } from 'module';
const require = createRequire(import.meta.url);

const { nowIso, safeJsonParse, makeRedis, xaddJson } = require('./lib-common.cjs');

const r = makeRedis();
const INTERVAL = parseInt(process.env.LEDGER_RECONCILE_INTERVAL_MS || '30000', 10);

function isoDate(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

async function streamLen(stream) {
  try { return Number(await r.xlen(stream)); } catch (_) { return 0; }
}

async function streamRange(stream, count = 500) {
  try {
    const rows = await r.xrevrange(stream, '+', '-', 'COUNT', count);
    return rows.map(([id, arr]) => {
      const obj = { _id: id, _stream: stream };
      for (let i = 0; i < arr.length; i += 2) obj[arr[i]] = arr[i+1];
      return obj;
    });
  } catch (_) { return []; }
}

function normalizeSide(x) {
  const s = String(x.side || x.action || x.order_side || '').toUpperCase();
  if (s.includes('BUY')) return 'BUY';
  if (s.includes('SELL')) return 'SELL';
  return s || 'UNKNOWN';
}

function extractTs(row) {
  const v = row.ts || row.iso || row.timestamp || row.ts_iso || row.created_at || '';
  const t = Date.parse(v);
  if (Number.isFinite(t)) return t;
  const idMs = Number(String(row._id).split('-')[0]);
  return Number.isFinite(idMs) ? idMs : Date.now();
}

async function cycle() {
  try {
    const streams = ['emarkos:v6:order:intent', 'emarkos:v1:execution', 'ares:order_executions', 'stream:execution_quality'];
    const xlens = {};
    for (const s of streams) xlens[s] = await streamLen(s);

    const execution = await streamRange('emarkos:v1:execution', 1000);
    const byDate = {};
    for (const row of execution) {
      const ts = extractTs(row);
      const date = isoDate(ts);
      const side = normalizeSide(row);
      const symbol = row.symbol || row.ticker || 'UNKNOWN';
      const shares = Math.abs(Number(row.shares || row.qty || row.quantity || 0));
      const key = date;
      if (!byDate[key]) byDate[key] = { orders: 0, buy_orders: 0, sell_orders: 0, shares_abs: 0, symbols: {} };
      byDate[key].orders += 1;
      if (side === 'BUY') byDate[key].buy_orders += 1;
      if (side === 'SELL') byDate[key].sell_orders += 1;
      byDate[key].shares_abs += Number.isFinite(shares) ? shares : 0;
      byDate[key].symbols[symbol] = (byDate[key].symbols[symbol] || 0) + 1;
    }

    const today = new Date().toISOString().slice(0, 10);
    const latestDate = Object.keys(byDate).sort().pop() || today;
    const latest = byDate[latestDate] || { orders: 0, buy_orders: 0, sell_orders: 0, shares_abs: 0, symbols: {} };

    const reportCandidates = [
      `ares:daily:core:${latestDate}`,
      `daily:core:${latestDate}`,
      `report:daily:${latestDate}`,
      `ops:daily:${latestDate}`
    ];
    let report = null, reportKey = null;
    for (const k of reportCandidates) {
      const raw = await r.get(k);
      const j = safeJsonParse(raw, null);
      if (j) { report = j; reportKey = k; break; }
    }

    const reportedOrders = report ? Number(report.orders_sent ?? report.orders ?? report.ordersSent ?? 0) : null;
    const mismatch = report && Number.isFinite(reportedOrders) && reportedOrders !== latest.orders;
    const state = {
      ts: nowIso(),
      schema: 'ares.ledger_reconciliation.v2',
      xlens,
      latest_execution_date: latestDate,
      latest_execution_summary: latest,
      report_key: reportKey,
      reported_orders_sent: reportedOrders,
      report_mismatch: Boolean(mismatch),
      state: mismatch ? 'INVALID_REPORT' : 'OK_OR_NO_REPORT',
      writer: 'ares-ledger-reconciler-v2'
    };

    await r.set('ops:ledger:order_reconciliation:last', JSON.stringify(state));
    await xaddJson(r, 'ops:ledger:order_reconciliation', state);
    console.log(JSON.stringify({ ts: nowIso(), svc: 'ledger-reconciler-v2', level: mismatch ? 'warn' : 'info', event: 'cycle', latestDate, orders: latest.orders, report_mismatch: Boolean(mismatch) }));
  } catch (e) {
    console.error(JSON.stringify({ ts: nowIso(), svc: 'ledger-reconciler-v2', level: 'error', event: 'cycle_failed', err: e.message }));
  }
}

cycle();
setInterval(cycle, INTERVAL);
