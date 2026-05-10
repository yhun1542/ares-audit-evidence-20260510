#!/usr/bin/env node
/**
 * ARES Schema Contract Guard
 * --------------------------
 * Periodically validates that Producer (feat_v1_publisher), Consumer (xgb_writer_v7),
 * and Model (xgb_drop_v4 + xgb_feature_cols.json) all agree on the same 22-feature
 * schema. If drift is detected, writes contract:status=BREACH which fail-closed
 * gates downstream (kill-switch-authority will block trading until resolved).
 *
 * Run cycle: 300s (5 min)
 *
 * Validations:
 *   1. xgb_feature_cols.json exists and is exactly 22 features
 *   2. feat:v1:status published within last 600s
 *   3. xgb:v7:status published within last 120s
 *   4. Sample 5 random feat:v1:{sym} keys → schema matches
 *   5. Sample 5 random xgb:risk:{sym} keys → uses model_version v7+
 *   6. p_drop_std >= 0.05 (anti-homogenization SLO)
 */

import { createClient } from 'redis';
import fs from 'node:fs';
import path from 'node:path';

const ARES_HOME      = process.env.ARES_HOME || '/home/ubuntu/ares_current';
const SCHEMA_PATH    = process.env.SCHEMA_PATH || path.join(ARES_HOME, 'models/xgb_feature_cols.json');
const REDIS_URL      = process.env.REDIS_URL || `redis://${process.env.REDIS_HOST||'127.0.0.1'}:${process.env.REDIS_PORT||6379}`;
const REDIS_PASS     = process.env.REDIS_PASSWORD || '';
const CONTRACT_KEY   = 'contract:schema:status';
const CYCLE_MS       = parseInt(process.env.CONTRACT_CYCLE_MS || '300000', 10);
const FEAT_FRESH_S   = parseInt(process.env.FEAT_FRESH_S || '600', 10);
const RISK_FRESH_S   = parseInt(process.env.RISK_FRESH_S || '120', 10);
const MIN_DIV_STD    = parseFloat(process.env.MIN_DIV_STD || '0.05');

// ─── Market Hours Guard ───────────────────────────────────────────────────────
// US 동부 시간(ET) 기준 정규 장중 여부를 반환합니다.
// DST 처리: UTC 오프셋이 EDT(-4h) / EST(-5h)에 따라 자동 조정됩니다.
// 장외 시간(pre-market, after-hours, 주말)에는 STALE 계열 위반을 suppressed 처리합니다.
const SUPPRESS_STALE_OFFHOURS = process.env.SUPPRESS_STALE_OFFHOURS !== 'false'; // 기본 true
function isUSMarketHours(nowMs) {
  const d = new Date(nowMs);
  // UTC 기준으로 ET 오프셋 계산 (DST: 3월 둘째 일요일 ~ 11월 첫째 일요일)
  const jan = new Date(d.getFullYear(), 0, 1).getTimezoneOffset();
  const jul = new Date(d.getFullYear(), 6, 1).getTimezoneOffset();
  const isDST = Math.min(jan, jul) === d.getTimezoneOffset();
  const etOffsetH = isDST ? -4 : -5; // EDT = UTC-4, EST = UTC-5
  const etMs = nowMs + etOffsetH * 3600 * 1000;
  const et = new Date(etMs);
  const dow = et.getUTCDay();    // 0=일, 1=월 ... 5=금, 6=토
  const hhmm = et.getUTCHours() * 100 + et.getUTCMinutes();
  if (dow === 0 || dow === 6) return false;          // 주말
  if (hhmm < 930 || hhmm >= 1600) return false;     // 09:30 ~ 15:59 ET
  return true;
}
// ─────────────────────────────────────────────────────────────────────────────


function log(...args) { console.log(new Date().toISOString(), '[contract-guard]', ...args); }

async function runCheck(client, expectedSchema) {
  const violations = [];
  const suppressed = [];   // 장외 시간에 억제된 위반 목록 (알람 없음, 증거는 보존)
  const evidence = {};
  const marketHours = isUSMarketHours(Date.now());
  evidence.market_hours = marketHours;

  // 1. Schema file integrity
  if (!Array.isArray(expectedSchema) || expectedSchema.length !== 22) {
    violations.push({ code: 'SCHEMA_INVALID', msg: `expected 22 features, got ${expectedSchema?.length}` });
  }
  evidence.schema_n = expectedSchema?.length;

  // 2-3. Producer/Consumer heartbeats
  const featStatusRaw = await client.get('feat:v1:status');
  const xgbStatusRaw  = await client.get('xgb:v7:status');
  const now = Date.now();
  if (!featStatusRaw) {
    violations.push({ code: 'FEAT_STATUS_MISSING', msg: 'feat:v1:status not published' });
  } else {
    const fs1 = JSON.parse(featStatusRaw);
    evidence.feat = fs1;
    const ageS = (now - new Date(fs1.ts).getTime())/1000;
    if (ageS > FEAT_FRESH_S) {
      const item = { code: 'FEAT_STALE', msg: `${ageS.toFixed(0)}s > ${FEAT_FRESH_S}s` };
      if (SUPPRESS_STALE_OFFHOURS && !marketHours) {
        suppressed.push(item);  // 장외 시간: 알람 억제
      } else {
        violations.push(item);  // 장중: 정상 위반 처리
      }
    }
    if (fs1.status && fs1.status !== 'OK') {
      const item = { code: 'FEAT_DEGRADED', msg: fs1.status };
      // STALE 상태는 장외 시간에 정상 — 억제 대상
      const isStaleStatus = (fs1.status === 'STALE' || fs1.status === 'DEGRADED_STALE');
      if (SUPPRESS_STALE_OFFHOURS && !marketHours && isStaleStatus) {
        suppressed.push(item);  // 장외 시간 STALE: 알람 억제
      } else {
        violations.push(item);  // 장중 또는 비-STALE 오류: 정상 위반 처리
      }
    }
  }
  if (!xgbStatusRaw) {
    violations.push({ code: 'XGB_STATUS_MISSING', msg: 'xgb:v7:status not published' });
  } else {
    const xs = JSON.parse(xgbStatusRaw);
    evidence.xgb = xs;
    const ageS = (now - new Date(xs.ts).getTime())/1000;
    if (ageS > RISK_FRESH_S) {
      const item = { code: 'XGB_STALE', msg: `${ageS.toFixed(0)}s > ${RISK_FRESH_S}s` };
      if (SUPPRESS_STALE_OFFHOURS && !marketHours) {
        suppressed.push(item);  // 장외 시간: 알람 억제
      } else {
        violations.push(item);  // 장중: 정상 위반 처리
      }
    }
    if (xs.status && xs.status !== 'OK') {
      const item = { code: 'XGB_DEGRADED', msg: xs.status };
      const isStaleStatus = (xs.status === 'STALE' || xs.status === 'DEGRADED_STALE');
      if (SUPPRESS_STALE_OFFHOURS && !marketHours && isStaleStatus) {
        suppressed.push(item);  // 장외 시간 STALE: 알람 억제
      } else {
        violations.push(item);  // 장중 또는 비-STALE 오류: 정상 위반 처리
      }
    }
    if (xs.p_drop_std !== undefined && xs.p_drop_std < MIN_DIV_STD) {
      violations.push({ code: 'SIGNAL_HOMOGENIZED', msg: `p_drop_std=${xs.p_drop_std} < ${MIN_DIV_STD}` });
    }
  }

  // 4. Sample feat:v1:{sym}
  let sampleKeys = [];
  outerFeat: for await (const batch of client.scanIterator({ MATCH: 'feat:v1:*', COUNT: 100, match: 'feat:v1:*', count: 100 })) {
    for (const key of batch) {
      if (!key.startsWith('feat:v1:')) continue;
      if (key === 'feat:v1:status' || key === 'feat:v1:heartbeat' || key === 'feat:v1:halt') continue;
      sampleKeys.push(key);
      if (sampleKeys.length >= 5) break outerFeat;
    }
  }
  evidence.feat_sample_count = sampleKeys.length;
  for (const k of sampleKeys) {
    const raw = await client.get(k);
    if (!raw) continue;
    try {
      const o = JSON.parse(raw);
      const featSet = Object.keys(o.features || {}).sort().join(',');
      const expSet = [...expectedSchema].sort().join(',');
      if (featSet !== expSet) {
        violations.push({ code: 'FEAT_SCHEMA_DRIFT', msg: `${k} schema differs` });
        break;
      }
    } catch (e) {
      violations.push({ code: 'FEAT_PARSE_ERROR', msg: `${k}: ${e.message}` });
    }
  }

  // 5. Sample xgb:risk:{sym}
  let riskKeys = [];
  outerRisk: for await (const batch of client.scanIterator({ MATCH: 'xgb:risk:*', COUNT: 100, match: 'xgb:risk:*', count: 100 })) {
    for (const key of batch) {
      if (!key.startsWith('xgb:risk:')) continue;
      if (key === 'xgb:risk:status') continue;
      riskKeys.push(key);
      if (riskKeys.length >= 5) break outerRisk;
    }
  }
  evidence.risk_sample_count = riskKeys.length;
  for (const k of riskKeys) {
    const raw = await client.get(k);
    if (!raw) continue;
    try {
      const o = JSON.parse(raw);
      if (!o.model_version || !o.model_version.startsWith('v7')) {
        violations.push({ code: 'RISK_OLD_VERSION', msg: `${k} version=${o.model_version}` });
      }
    } catch {/* ignore */}
  }

  const status = violations.length === 0 ? 'OK' : 'BREACH';
  const result = {
    ts: new Date().toISOString(),
    status,
    violations,
    suppressed,          // 장외 시간 억제된 위반 (정보 보존용)
    market_hours: marketHours,
    evidence,
  };
  await client.set(CONTRACT_KEY, JSON.stringify(result), { expiration: { type: 'EX', value: 900 } });
  log(`status=${status} violations=${violations.length} suppressed=${suppressed.length} market_hours=${marketHours}`);
  if (status === 'BREACH') {
    log('VIOLATIONS:', JSON.stringify(violations, null, 2));
  }
  if (suppressed.length > 0) {
    log(`SUPPRESSED (off-hours, no alert): ${suppressed.map(v => v.code).join(', ')}`);
  }
  return result;
}

async function main() {
  if (!fs.existsSync(SCHEMA_PATH)) {
    log('FATAL: schema not found:', SCHEMA_PATH);
    process.exit(1);
  }
  const expectedSchema = JSON.parse(fs.readFileSync(SCHEMA_PATH, 'utf8'));
  log(`Loaded ${expectedSchema.length} expected features from ${SCHEMA_PATH}`);

  const client = createClient({ 
    url: REDIS_URL, 
    username: process.env.ARES_REDIS_USERNAME || process.env.REDIS_USERNAME || 'ares-admin',
    password: REDIS_PASS || undefined 
  });
  client.on('error', (e) => log('redis error:', e.message));
  await client.connect();
  log(`Connected to Redis: ${REDIS_URL.replace(/:[^@/]*@/,':<REDACTED>@')}`);

  while (true) {
    try {
      await runCheck(client, expectedSchema);
    } catch (e) {
      log('cycle error:', e.message);
    }
    await new Promise(r => setTimeout(r, CYCLE_MS));
  }
}

main().catch(e => { log('fatal:', e); process.exit(1); });
