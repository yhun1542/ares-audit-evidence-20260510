#!/usr/bin/env node
/**
 * ARES KIS v9 fill backfill dry-run
 *
 * Does not write to production streams.
 * Optional writes only to quarantine stream: backfill:kis:fills:quarantine
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import Redis from "ioredis";

function parseArgs(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const val = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
      out[key] = val;
    }
  }
  return out;
}

const args = parseArgs(process.argv);
const kisJson = args["kis-json"];
const outDir = args["out-dir"] || `/tmp/ares_kis_backfill_dryrun_${Date.now()}`;
const writeQuarantine = String(args["write-quarantine"] || "false") === "true";
const quarantineStream = args["quarantine-stream"] || "backfill:kis:fills:quarantine";

if (!kisJson || !fs.existsSync(kisJson)) {
  console.error(`Error: kis-json file not found at ${kisJson}`);
  process.exit(2);
}

fs.mkdirSync(outDir, { recursive: true });

const REDIS_URL = process.env.REDIS_URL || process.env.ARES_REDIS_URL;

const redis = REDIS_URL
  ? new Redis(REDIS_URL, {
      tls: REDIS_URL.startsWith("rediss://") ? { rejectUnauthorized: false } : undefined,
      maxRetriesPerRequest: null,
      enableReadyCheck: true,
    })
  : null;

function asArray(x) {
  if (Array.isArray(x)) return x;
  if (Array.isArray(x?.output)) return x.output;
  if (Array.isArray(x?.output1)) return x.output1;
  if (Array.isArray(x?.fills)) return x.fills;
  if (Array.isArray(x?.data)) return x.data;
  return [];
}

function pick(o, keys) {
  for (const k of keys) {
    if (o[k] !== undefined && o[k] !== null && String(o[k]).trim() !== "") return String(o[k]).trim();
  }
  return "";
}

function n(v) {
  return String(v ?? "").replace(/,/g, "").trim();
}

function side(v) {
  const s = String(v || "").toUpperCase();
  if (s.includes("BUY") || s.includes("매수") || s === "02") return "BUY";
  if (s.includes("SELL") || s.includes("매도") || s === "01") return "SELL";
  return s || "UNKNOWN";
}

function compact(f) {
  const obj = {
    broker_order_id: pick(f, ["broker_order_id", "odno", "ODNO", "ord_no", "order_no", "KRX_FWDG_ORD_ORGNO"]),
    exec_id: pick(f, ["exec_id", "ccnl_no", "CCNL_NO", "ccnl_ord_no", "fill_id"]),
    date: pick(f, ["ord_dt", "ORD_DT", "exec_dt", "ccnl_dt", "date"]),
    time: pick(f, ["ord_tmd", "ORD_TMD", "exec_tmd", "ccnl_tmd", "time"]),
    symbol: pick(f, ["symbol", "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO", "ticker"]).toUpperCase(),
    side: side(pick(f, ["side", "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD", "sll_buy_dvsn_name", "buy_sell"])),
    qty: n(pick(f, ["qty", "exec_qty", "ccnl_qty", "CCNL_QTY", "ft_ccld_qty"])),
    price: n(pick(f, ["price", "exec_price", "ccnl_pric", "CCNL_PRIC", "ft_ccld_unpr3"])),
    media: pick(f, ["mdia_dvsn_name", "MDIA_DVSN_NAME", "media", "ord_mdia_dvsn_name"]),
    raw: f,
  };

  obj.dedupe_key = [
    obj.broker_order_id,
    obj.exec_id,
    obj.date,
    obj.time,
    obj.symbol,
    obj.side,
    obj.qty,
    obj.price,
  ].join("|");

  return obj;
}

async function loadStream(stream) {
  const keys = new Set();
  const rows = [];

  if (!redis) return { keys, rows };

  let entries = [];
  try {
    entries = await redis.xrange(stream, "-", "+");
  } catch {
    return { keys, rows };
  }

  for (const [id, arr] of entries) {
    const o = { _stream: stream, _stream_id: id };
    for (let i = 0; i < arr.length; i += 2) o[arr[i]] = arr[i + 1];

    for (const f of ["payload", "json", "data", "fill", "raw"]) {
      if (o[f]) {
        try { Object.assign(o, JSON.parse(o[f])); } catch {}
      }
    }

    const c = compact(o);
    if (c.dedupe_key.replace(/\|/g, "")) keys.add(c.dedupe_key);
    rows.push(c);
  }

  return { keys, rows };
}

const kisRaw = JSON.parse(fs.readFileSync(kisJson, "utf8"));
const kis = asArray(kisRaw).map(compact);

const seenKis = new Set();
const unique = [];
const duplicateKis = [];

for (const f of kis) {
  if (seenKis.has(f.dedupe_key)) duplicateKis.push(f);
  else {
    seenKis.add(f.dedupe_key);
    unique.push(f);
  }
}

const streams = [
  "stream:fills",
  "ares:fills:canonical",
  "ares:order_executions",
  "ares:broker_truth:events",
];

const redisKeys = new Set();
const streamStats = {};

for (const s of streams) {
  const { keys, rows } = await loadStream(s);
  streamStats[s] = { rows: rows.length, unique_keys: keys.size };
  for (const k of keys) redisKeys.add(k);
}

const matched = [];
const missing = [];

for (const f of unique) {
  if (redisKeys.has(f.dedupe_key)) matched.push(f);
  else missing.push(f);
}

const summary = {
  generated_at_utc: new Date().toISOString(),
  kis_json: kisJson,
  kis_total: kis.length,
  kis_unique: unique.length,
  kis_duplicates: duplicateKis.length,
  ares_stream_stats: streamStats,
  matched: matched.length,
  missing: missing.length,
  missing_rate_pct: unique.length ? Number(((missing.length / unique.length) * 100).toFixed(2)) : 0,
  write_quarantine: writeQuarantine,
  quarantine_stream: writeQuarantine ? quarantineStream : null,
  production_stream_write: false,
};

fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summary, null, 2));
fs.writeFileSync(path.join(outDir, "matched.json"), JSON.stringify(matched, null, 2));
fs.writeFileSync(path.join(outDir, "missing.json"), JSON.stringify(missing, null, 2));
fs.writeFileSync(path.join(outDir, "duplicate_kis.json"), JSON.stringify(duplicateKis, null, 2));
fs.writeFileSync(path.join(outDir, "quarantine.ndjson"), missing.map(x => JSON.stringify(x)).join("\n") + "\n");

if (writeQuarantine) {
  if (!redis) {
    console.error("REDIS_URL or ARES_REDIS_URL required for quarantine write");
    process.exit(3);
  }

  for (const f of missing) {
    await redis.xadd(
      quarantineStream,
      "*",
      "source", "kis_inquire_ccnl",
      "status", "dryrun_quarantine",
      "dedupe_key", f.dedupe_key,
      "payload", JSON.stringify(f)
    );
  }
}

if (redis) await redis.quit();

console.log(JSON.stringify(summary, null, 2));
