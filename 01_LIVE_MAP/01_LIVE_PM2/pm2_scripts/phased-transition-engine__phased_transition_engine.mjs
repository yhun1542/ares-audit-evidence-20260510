#!/usr/bin/env node
/** phased_transition_engine.mjs (production) */
/**
 * v1.9.2 (buy skip breakdown + auto judgment hardening):
 *  - BUY 스킵을 원인별 분해: gate/budget/cash/no_price/offsession/min_notional
 *  - emitOrderIntent()가 상태 문자열 반환 (emitted/skipped_*)
 *  - auto toggle에 buySkip breakdown 전달 → 정밀 판단
 *  - quality에 buy_skip_breakdown 객체 추가
 *
 * v1.9.1 (auto cash hardening):
 *  - cash key 다중 fallback (5개 키 우선순위)
 *  - cash sanity guard (min/max/anomaly streak/block cooldown)
 *  - AUTO_ON=WARN / AUTO_OFF=INFO ops_event severity 분리
 *  - 오탐 시 cash 신호 차단 → buy_skip_rate 기반으로만 판단
 *
 * v1.9 (auto cash bottleneck control):
 *  - BUY_AFTER_SELL 자동 ON/OFF: 현금 부족/BUY 스킵률 기반 히스테리시스 토글
 *  - 쿨다운(15분) + streak(3회 연속) 조건으로 안정적 전환
 *  - transition:cash_bottleneck_state / policy:transition:buy_after_sell Redis 기록
 *
 * v1.8 (BUY fraction 동적 + SELL threshold):
 *  - effectiveBuyFraction: vol/risk 기반으로 BUY 예산 비율 자동 하향 (0.60→0.40)
 *  - SELL_EXEC_THRESHOLD_USD: SELL executed USD가 threshold 이상일 때만 BUY 허용
 *  - sellsExecutedUsd 누적 추적 + quality에 eff_buy_fraction/sell_exec_usd 기록
 *
 * v1.7:
 *  - BUY_AFTER_SELL 강화: SELL이 1건 이상 실행된 후에만 BUY 허용 (sellsExecuted 기반)
 *
 * v1.6 (profit pack):
 *  - BUY_BUDGET_FRACTION: BUY는 cycleBudget의 60%까지만 사용 (SELL은 100%)
 *  - Symbol netting: 같은 심볼의 buy/sell delta 합산 (왕복 거래 제거)
 *  - gap_min_bps 동적 조정: vol/risk 기반으로 자동 상/하향
 *  - MAX_ORDERS 동적 제한: vol/risk 기반으로 한 cycle 주문 수 자동 조절
 *
 * v1.5:
 *  - SELL 우선 실행(현금 확보 후 BUY) 옵션 추가
 *  - cash reserve로 BUY 병목이 생길 때 전환 속도/품질 개선
 *
 * v1.4 (profit/efficiency hardening):
 *  - 체결 비용 최소화: limit buffer bps를 volatility/risk 기반으로 동적 조절
 *  - 미세 리밸런싱 스킵: |Δw| < threshold_bps 는 주문 생성 금지(노이즈/수수료/슬리피지 감소)
 *
 * v1.3:
 *  - emergency half speed 지원: policy:transition:half_speed=true면 budget/alpha 적용을 0.5배로 제한
 *  - 장중 실행 품질 지표(transition:quality) 저장: slippage_state, fill_latency, kis_api_latency, reject, skips, executed
 *
 * v1.1 (transition 전용 강화):
 *   - market session 게이트(NY 09:30~16:00) 기본 ON (TRANSITION_ALLOW_OFFSESSION=false)
 *   - spread-aware / agg-limit 가격 자동 설정(몇 bps 버퍼)
 *   - delta_shares 최소/최대, notional 최소, cash reserve(현금 5% 남기기) 적용
 *   - md:mid staleness(기본 30s) 체크 + stale이면 micro_send_stale metadata 표시
 *
 * order_intent_exector expects:
 *   - Redis Stream: emarkos:v6:order:intent
 *   - Field: "json" (stringified intent object)
 *   - Intent fields: intent_id, symbol, side, delta_shares, last_price, (optional) limit.price, ts
 */
import { config as dotenvConfig } from "dotenv";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { existsSync } from "fs";
import process from "node:process";
import Redis from "ioredis";

const __dirname = dirname(fileURLToPath(import.meta.url));
// ARES hardening 2026-05-01: load all known env files in priority order.
// The previous implementation stopped at the first existing .env file; an empty
// /home/ubuntu/ops/.env therefore masked /home/ubuntu/ares_current/.env and
// made phased-transition-engine crash with "REDIS_URL required".
for (const p of [
  "/home/ubuntu/ares_current/.env",
  resolve(__dirname, ".env"),
  resolve(__dirname, "../.env"),
  "/home/ubuntu/.env",
]) {
  if (existsSync(p)) dotenvConfig({ path: p, override: false, quiet: true });
}

const REDIS_URL = process.env.REDIS_URL || process.env.CACHE_REDIS_URL || "";
const redisOptions = { maxRetriesPerRequest: 3, connectTimeout: 10000, enableReadyCheck: true };
function buildRedisClient() {
  if (REDIS_URL) return new Redis(REDIS_URL, redisOptions);
  const host = process.env.REDIS_HOST || process.env.ARES_REDIS_HOST || process.env.ELASTICACHE_ENDPOINT || process.env.CACHE_REDIS_HOST;
  const port = Number(process.env.REDIS_PORT || process.env.ARES_REDIS_PORT || process.env.CACHE_REDIS_PORT || 6379);
  const password = process.env.REDIS_PASSWORD || process.env.REDISCLI_AUTH || process.env.CACHE_REDIS_PASSWORD || undefined;
  const tlsFlag = String(process.env.REDIS_TLS || process.env.ARES_REDIS_SSL || process.env.CACHE_REDIS_TLS || "").toLowerCase();
  if (!host) { console.error("[FATAL] Redis config required: REDIS_URL/CACHE_REDIS_URL or ARES_REDIS_HOST/REDIS_HOST"); process.exit(2); }
  return new Redis({ ...redisOptions, host, port, password, tls: ["1","true","TRUE","yes","YES","on"].includes(tlsFlag) ? {} : undefined });
}

const redis = buildRedisClient();
const OPS_STREAM = "emarkos:v1:ops_event";

const ORDER_INTENT_STREAM = process.env.ORDER_INTENT_STREAM || "emarkos:v6:order:intent";
const DRY_RUN = (process.env.TRANSITION_DRY_RUN || "false") === "true";
const LOOP_SEC = Number(process.env.TRANSITION_LOOP_SEC || 60);
const MAX_ORDERS = Number(process.env.TRANSITION_MAX_ORDERS_PER_CYCLE || 25);

// market session gate (NY time)
const ALLOW_OFFSESSION = (process.env.TRANSITION_ALLOW_OFFSESSION || "false") === "true";

// price / spread / limit 정책
const MID_STALE_SEC = Number(process.env.TRANSITION_MID_STALE_SEC || 30);
const LIMIT_EXTRA_BPS = Number(process.env.TRANSITION_LIMIT_EXTRA_BPS || 8);
const LIMIT_MIN_BPS = Number(process.env.TRANSITION_LIMIT_MIN_BPS || 5);
const LIMIT_MAX_BPS = Number(process.env.TRANSITION_LIMIT_MAX_BPS || 50);
// v1.4: 미세 리밸런싱 스킵(기본 10bps)
const GAP_MIN_BPS = Number(process.env.TRANSITION_GAP_MIN_BPS || 10);

// v1.5: SELL 우선 실행
const SELL_FIRST = (process.env.TRANSITION_SELL_FIRST || "true") === "true";
const BUY_AFTER_SELL = (process.env.TRANSITION_BUY_AFTER_SELL || "false") === "true"; // (수동) BUY는 SELL 실행 이후부터 허용

// v1.8: BUY fraction 동적 하향 상수
const BUY_BUDGET_FRACTION_VOL_100 = Number(process.env.TRANSITION_BUY_BUDGET_FRACTION_VOL_100 || 0.40);
const BUY_BUDGET_FRACTION_RISK_75 = Number(process.env.TRANSITION_BUY_BUDGET_FRACTION_RISK_75 || 0.40);
const BUY_BUDGET_FRACTION_MIN = Number(process.env.TRANSITION_BUY_BUDGET_FRACTION_MIN || 0.20);
const BUY_BUDGET_FRACTION_MAX = Number(process.env.TRANSITION_BUY_BUDGET_FRACTION_MAX || 0.80);
const SELL_EXEC_THRESHOLD_USD = Number(process.env.TRANSITION_SELL_EXEC_THRESHOLD_USD || 5000);

// v1.9: auto cash bottleneck control
const AUTO_BUY_AFTER_SELL = (process.env.TRANSITION_AUTO_BUY_AFTER_SELL || "true") === "true";
const KEY_POLICY_BUY_AFTER_SELL = process.env.KEY_POLICY_BUY_AFTER_SELL || "policy:transition:buy_after_sell";
const KEY_CASH_AVAILABLE = process.env.KEY_CASH_AVAILABLE || "kis:live:available_cash";
const CASH_LOW_USD = Number(process.env.TRANSITION_CASH_LOW_USD || 15000);
const CASH_HIGH_USD = Number(process.env.TRANSITION_CASH_HIGH_USD || 30000);
// v1.9.1: cash sanity guard
const CASH_SANITY_MIN_USD = Number(process.env.TRANSITION_CASH_SANITY_MIN_USD || 1);
const CASH_SANITY_MAX_USD = Number(process.env.TRANSITION_CASH_SANITY_MAX_USD || 500000);
const CASH_ANOMALY_STREAK_NEED = Number(process.env.TRANSITION_CASH_ANOMALY_STREAK || 3);
const CASH_ANOMALY_COOLDOWN_SEC = Number(process.env.TRANSITION_CASH_ANOMALY_COOLDOWN_SEC || 3600);
// v1.9.1: cash key fallback list
const CASH_FALLBACK_KEYS = [
  KEY_CASH_AVAILABLE,
  "kis:cash:usd:available",
  "kis:cash:usd",
  "kis:live:cash_available_usd",
  "kis:live:available_cash",
].filter((v, i, a) => a.indexOf(v) === i); // dedupe
const BUY_SKIP_ON = Number(process.env.TRANSITION_BUY_SKIP_ON || 0.35);
const BUY_SKIP_OFF = Number(process.env.TRANSITION_BUY_SKIP_OFF || 0.15);
const AUTO_STREAK_NEED = Number(process.env.TRANSITION_AUTO_STREAK_NEED || 3);
const AUTO_COOLDOWN_SEC = Number(process.env.TRANSITION_AUTO_COOLDOWN_SEC || 900);
const KEY_AUTO_STATE = process.env.KEY_AUTO_STATE || "transition:cash_bottleneck_state";

// v1.6 profit optimizations
const BUY_BUDGET_FRACTION = Number(process.env.TRANSITION_BUY_BUDGET_FRACTION || 0.60);
const NETTING_ENABLED = (process.env.TRANSITION_NETTING_ENABLED || "true") === "true";
const GAP_MIN_BPS_BASE = Number(process.env.TRANSITION_GAP_MIN_BPS_BASE || 10);
const GAP_MIN_BPS_MIN = Number(process.env.TRANSITION_GAP_MIN_BPS_MIN || 5);
const GAP_MIN_BPS_MAX = Number(process.env.TRANSITION_GAP_MIN_BPS_MAX || 25);
const MAX_ORDERS_MIN = Number(process.env.TRANSITION_MAX_ORDERS_MIN || 8);
const MAX_ORDERS_MAX = Number(process.env.TRANSITION_MAX_ORDERS_MAX || 25);

// qty/notional/cash 정책
const MIN_NOTIONAL_USD = Number(process.env.TRANSITION_MIN_NOTIONAL_USD || 250);
const MAX_SHARES_PER_INTENT = Number(process.env.TRANSITION_MAX_SHARES_PER_INTENT || 250);

// Concentration guard (execution-time)
const CONCENTRATION_MAX_W = Number(process.env.TRANSITION_MAX_SINGLE_WEIGHT || 0.20);
const CONCENTRATION_TOPK = Number(process.env.TRANSITION_TOPK || 4);
const CONCENTRATION_TOPK_MAX = Number(process.env.TRANSITION_TOPK_MAX_WEIGHT_SUM || 0.60);
const PER_SYMBOL_DAILY_CAP_FRAC = Number(process.env.TRANSITION_PER_SYMBOL_DAILY_CAP_FRAC || 0.10);
const CASH_RESERVE_PCT = Number(process.env.TRANSITION_CASH_RESERVE_PCT || 0.05);

const KEY_ACTUAL_POS      = process.env.KEY_ACTUAL_POS      || "emarkos:v1:positions";
const KEY_CHAMPION_TARGET = process.env.KEY_CHAMPION_TARGET || "champion:target:positions";
const KEY_NEXTGEN2_TARGET = process.env.KEY_NEXTGEN2_TARGET || "ssot:target:v2:current";

const TRANSITION_ALPHA_KEY = process.env.TRANSITION_ALPHA_KEY || "transition:alpha";
const KEY_RISK_SCORE = process.env.KEY_RISK_SCORE || "risk:score";
const KEY_TRADE_HALT = process.env.KEY_TRADE_HALT || "policy:trade_halt";
const KEY_TRADE_HALT_EXEC = process.env.KEY_TRADE_HALT_EXEC || "policy:trade:halt";
const KEY_KILL_SWITCH = process.env.KEY_KILL_SWITCH || "policy:kill_switch";
const KEY_CORE_SAFE_LEVEL = process.env.KEY_CORE_SAFE_LEVEL || "core:safe_level";
const KEY_HALF_SPEED = process.env.KEY_HALF_SPEED || "policy:transition:half_speed";
const KEY_VOL_BPS = process.env.KEY_VOL_BPS || "kpi:volatility:ewma_bps";

// 품질 지표 키 (existing KPIs)
const KEY_KPI_FILL_P95 = process.env.KEY_KPI_FILL_P95 || "kpi:fill_latency:p95_ms";
const KEY_KPI_KIS_LAT = process.env.KEY_KPI_KIS_LAT || "kpi:kis_api:avg_latency_ms";
const KEY_KPI_REJECT = process.env.KEY_KPI_REJECT || "kpi:reject";
const KEY_KPI_SLIPPAGE = process.env.KEY_KPI_SLIPPAGE || "kpi:slippage_state";

function clamp(x,a,b){ return Math.max(a, Math.min(b, x)); }
function round(n,d=4){ const p=10**d; return Math.round(n*p)/p; }

function parsePositionsJson(raw){
  if (!raw) return [];
  let obj;
  try { obj = JSON.parse(raw); } catch { return []; }
  let arr = [];
  if (Array.isArray(obj)) arr = obj;
  else if (Array.isArray(obj.positions)) arr = obj.positions;
  else if (obj.target && Array.isArray(obj.target.positions)) arr = obj.target.positions;
  else if (obj.targets && Array.isArray(obj.targets.positions)) arr = obj.targets.positions;
  else if (obj.targets && typeof obj.targets === "object" && !Array.isArray(obj.targets)) {
    arr = Object.entries(obj.targets).map(([symbol, v]) => {
      if (typeof v === "object") return { symbol, weight: v.weight ?? v.w ?? 0, mv_usd: v.target_value ?? v.mv ?? 0 };
      return { symbol, weight: Number(v) };
    });
  }
  else if (obj.positions && typeof obj.positions === "object") {
    const totalMV = obj.totalMarketValue || 0;
    arr = Object.entries(obj.positions).map(([symbol, v]) => {
      if (typeof v === "object") {
        const mv = Number(v.marketValue ?? v.mv ?? v.value ?? 0);
        const w = totalMV > 0 ? mv / totalMV : 0;
        return { symbol, weight: w, mv_usd: mv };
      }
      return { symbol, weight: Number(v) };
    });
  }
  else if (typeof obj === "object") {
    const keys = Object.keys(obj);
    const looksLikeMap = keys.length > 0 && keys.every(k => typeof obj[k] === "number" || typeof obj[k] === "string");
    if (looksLikeMap) arr = keys.map(k => ({ symbol: k, weight: Number(obj[k]) }));
  }
  return arr.map(p=>{
    const symbol = (p.symbol||p.ticker||p.sym||"").toString().trim();
    if (!symbol) return null;
    const w = Number(p.weight ?? p.w ?? p.target_w ?? 0);
    const mv = Number(p.mv_usd ?? p.mv ?? p.value ?? p.market_value ?? p.marketValue ?? 0);
    return { symbol, w: Number.isFinite(w)?w:0, mv: Number.isFinite(mv)?mv:0 };
  }).filter(Boolean);
}

function normalizeWeights(pos){
  const sumAbs = pos.reduce((a,p)=>a+Math.abs(p.w||0),0);
  if (sumAbs > 0.5 && sumAbs < 1.5) return pos;
  if (sumAbs <= 0) return pos;
  return pos.map(p=>({...p, w:p.w/sumAbs}));
}

function toWeightMap(pos){
  const m = new Map();
  for (const p of pos) m.set(p.symbol, (m.get(p.symbol)||0) + (p.w||0));
  return m;
}

function unionSymbols(...maps){
  const s = new Set();
  maps.forEach(m=>{ for (const k of m.keys()) s.add(k); });
  return [...s];
}

function computeTopConcentration(weightMap, k){
  const arr = [];
  for (const [sym,w] of weightMap.entries()) arr.push({sym,w:Math.abs(w)});
  arr.sort((a,b)=>b.w-a.w);
  const top = arr.slice(0,k);
  const sum = top.reduce((a,x)=>a+x.w,0);
  return { top, sum };
}

// v1.6: 동적 gap_min_bps (vol/risk 기반)
function dynamicGapMinBps(volBps, riskScore) {
  let bps = GAP_MIN_BPS_BASE;
  if (Number.isFinite(volBps)) {
    if (volBps >= 150) bps += 10;
    else if (volBps >= 100) bps += 6;
    else if (volBps >= 70) bps += 3;
  }
  if (Number.isFinite(riskScore)) {
    if (riskScore >= 85) bps += 8;
    else if (riskScore >= 75) bps += 5;
    else if (riskScore >= 65) bps += 2;
  }
  return Math.max(GAP_MIN_BPS_MIN, Math.min(GAP_MIN_BPS_MAX, Math.round(bps)));
}

// v1.6: 동적 MAX_ORDERS (vol/risk 기반)
function dynamicMaxOrders(volBps, riskScore) {
  let m = MAX_ORDERS_MAX;
  if (Number.isFinite(volBps)) {
    if (volBps >= 150) m = Math.min(m, 10);
    else if (volBps >= 100) m = Math.min(m, 14);
    else if (volBps >= 70) m = Math.min(m, 18);
  }
  if (Number.isFinite(riskScore)) {
    if (riskScore >= 85) m = Math.min(m, 10);
    else if (riskScore >= 75) m = Math.min(m, 14);
  }
  return Math.max(MAX_ORDERS_MIN, Math.min(MAX_ORDERS_MAX, m));
}

// v1.6: symbol 단위 netting (같은 심볼의 buy/sell delta 합산)
function netDeltasBySymbol(diffs) {
  const m = new Map();
  for (const d of diffs) {
    const key = d.sym;
    const cur = m.get(key) || { sym: key, delta: 0, notional: 0 };
    cur.delta += d.delta;
    cur.notional += d.notional;
    m.set(key, cur);
  }
  return [...m.values()];
}

// v1.8: BUY fraction을 vol/risk 기반으로 동적 하향
function effectiveBuyFraction(baseFrac, volBps, riskScore) {
  let frac = baseFrac;
  if (Number.isFinite(volBps) && volBps >= 100) frac = Math.min(frac, BUY_BUDGET_FRACTION_VOL_100);
  if (Number.isFinite(riskScore) && riskScore >= 75) frac = Math.min(frac, BUY_BUDGET_FRACTION_RISK_75);
  return Math.max(BUY_BUDGET_FRACTION_MIN, Math.min(BUY_BUDGET_FRACTION_MAX, frac));
}

// v1.9.1: auto cash bottleneck helpers (multi-key fallback + sanity)
async function getCashAvailableForAuto() {
  for (const key of CASH_FALLBACK_KEYS) {
    const raw = await redis.get(key).catch(()=>null);
    if (raw === null || raw === undefined) continue;
    const v = Number(raw);
    if (!Number.isFinite(v) || v < 0) continue;
    return v;
  }
  return 0;
}

function isCashSane(cashUsd) {
  if (!Number.isFinite(cashUsd)) return { valid: false, reason: "nan" };
  if (cashUsd <= CASH_SANITY_MIN_USD) return { valid: false, reason: "too_small" };
  if (cashUsd >= CASH_SANITY_MAX_USD) return { valid: false, reason: "too_large" };
  return { valid: true, reason: "ok" };
}

async function getAutoBuyAfterSellState() {
  const raw = await redis.get(KEY_AUTO_STATE).catch(()=>null);
  const defaults = { enabled: null, streak: 0, last_change_ts: 0, last_decision: null, cash_anom_streak: 0, cash_block_until: 0 };
  if (!raw) return defaults;
  try { return { ...defaults, ...JSON.parse(raw) }; } catch { return defaults; }
}

async function setAutoBuyAfterSell(enabled, decision, meta, anomState) {
  const now = Math.floor(Date.now()/1000);
  const st = await getAutoBuyAfterSellState();
  const next = {
    ...st,
    enabled,
    streak: 0,
    last_change_ts: now,
    last_decision: decision,
    last_meta: meta,
    cash_anom_streak: anomState ? anomState.cash_anom_streak : (st.cash_anom_streak || 0),
    cash_block_until: anomState ? anomState.cash_block_until : (st.cash_block_until || 0),
    ts: new Date().toISOString(),
  };
  await redis.set(KEY_AUTO_STATE, JSON.stringify(next), "EX", 86400*14);
  await redis.set(KEY_POLICY_BUY_AFTER_SELL, enabled ? "true" : "false", "EX", 86400*2);
  // v1.9.1: AUTO_ON=WARN, AUTO_OFF=INFO
  const severity = decision === "AUTO_ON" ? "WARN" : "INFO";
  await opsEvent(severity, "TRANSITION_AUTO_BUY_AFTER_SELL",
    `${decision} enabled=${enabled} cash=$${meta?.cashUsd||"?"} cashValid=${meta?.cashValid} skipRate=${meta?.buySkipRate||"?"}`,
    `tx_auto_bas|${decision}|${Math.floor(now/3600)}`);
}

async function maybeAutoToggleBuyAfterSell(buySkipInput, cashUsd) {
  if (!AUTO_BUY_AFTER_SELL) return null;

  // v1.9.2: buySkipInput can be object {total,gate,budget,cash,...} or number
  const buySkipRate = (buySkipInput && typeof buySkipInput === 'object' && typeof buySkipInput.total === 'number')
    ? buySkipInput.total : Number(buySkipInput || 0);
  const buySkipCash = (buySkipInput && typeof buySkipInput === 'object' && typeof buySkipInput.cash === 'number')
    ? buySkipInput.cash : 0;

  const now = Math.floor(Date.now()/1000);
  const st = await getAutoBuyAfterSellState();

  // 쿨다운 중이면 토글 금지(상태만 기록)
  if (st.last_change_ts && (now - st.last_change_ts) < AUTO_COOLDOWN_SEC) {
    return st.enabled;
  }

  // v1.9.1: cash sanity guard
  const sanity = isCashSane(cashUsd);
  let cashValid = sanity.valid;
  let cashReason = sanity.reason;
  let cashAnomStreak = Number(st.cash_anom_streak || 0);
  let cashBlockUntil = Number(st.cash_block_until || 0);

  // check if currently blocked
  if (cashBlockUntil && now < cashBlockUntil) {
    cashValid = false;
    cashReason = "blocked";
  }

  // update anomaly streak
  if (!cashValid && cashReason !== "blocked") {
    cashAnomStreak += 1;
    if (cashAnomStreak >= CASH_ANOMALY_STREAK_NEED) {
      cashBlockUntil = now + CASH_ANOMALY_COOLDOWN_SEC;
      cashAnomStreak = 0;
    }
  } else if (cashValid) {
    cashAnomStreak = 0;
  }
  const anomState = { cash_anom_streak: cashAnomStreak, cash_block_until: cashBlockUntil };

  // v1.9.1+v1.9.2: 의사결정 (cash가 유효할 때만 cash 신호 사용)
  const cashOnSignal = cashValid && cashUsd > 0 && cashUsd <= CASH_LOW_USD;
  const skipOnSignal = buySkipRate >= BUY_SKIP_ON;
  const cashSkipOnSignal = buySkipCash >= BUY_SKIP_ON;  // v1.9.2
  const onCandidate = cashOnSignal || skipOnSignal || cashSkipOnSignal;
  const offCandidate = (cashValid && cashUsd >= CASH_HIGH_USD) && (buySkipRate <= BUY_SKIP_OFF);

  let enabled = st.enabled;
  let streak = Number(st.streak || 0);
  let decision = null;

  if (enabled === null || enabled === undefined) enabled = BUY_AFTER_SELL; // 초기값

  const metaBase = { cashUsd, cashValid, cashReason, buySkipRate, buySkip: buySkipInput };

  if (onCandidate && !enabled) {
    streak += 1;
    decision = "ON_CANDIDATE";
    if (streak >= AUTO_STREAK_NEED) {
      await setAutoBuyAfterSell(true, "AUTO_ON", metaBase, anomState);
      return true;
    }
  } else if (offCandidate && enabled) {
    streak += 1;
    decision = "OFF_CANDIDATE";
    if (streak >= AUTO_STREAK_NEED) {
      await setAutoBuyAfterSell(false, "AUTO_OFF", metaBase, anomState);
      return false;
    }
  } else {
    streak = 0;
    decision = "HOLD";
  }

  // 상태 업데이트(토글은 안함)
  const next = {
    ...st,
    enabled,
    streak,
    last_decision: decision,
    last_meta: metaBase,
    cash_anom_streak: cashAnomStreak,
    cash_block_until: cashBlockUntil,
    ts: new Date().toISOString(),
  };
  await redis.set(KEY_AUTO_STATE, JSON.stringify(next), "EX", 86400*14);
  return enabled;
}

async function opsEvent(kind, reason, msg, dedupe){
  const ts = new Date().toISOString();
  try {
    await redis.xadd(OPS_STREAM,"*",
      "schema","emarkos.ops_event.v1",
      "ts", ts,
      "kind", kind,
      "reason", reason,
      "source","phased_transition_engine",
      "dedupe_key", dedupe,
      "msg", msg
    );
  } catch {}
}

// ── Price helpers (v1.1) ──

async function getRefPrice(symbol) {
  // v1.1: NOAUTH/ERR filter + intraday fallback
  const midRaw = await redis.get(`md:mid:${symbol}`).catch(()=>null);
  if (midRaw && /NOAUTH|ERR|WRONGTYPE/i.test(String(midRaw))) {
    // auth/conn issue surfaced as string - skip
  } else if (midRaw) {
    try {
      const obj = JSON.parse(midRaw);
      const p = Number(obj.price ?? obj.mid ?? obj.value);
      const ts = obj.ts ?? obj.timestamp ?? obj.time ?? obj.updated_at ?? null;
      let stale = false;
      if (ts) {
        const t = (typeof ts === "number") ? ts*1000 : Date.parse(String(ts));
        if (Number.isFinite(t)) stale = (Date.now() - t) > MID_STALE_SEC*1000;
      }
      if (Number.isFinite(p) && p > 0) return { price: p, stale };
    } catch {}
    const p = Number(midRaw);
    if (Number.isFinite(p) && p > 0) return { price: p, stale: false };
  }
  // fallback 1: price:SYM
  const lastRaw = await redis.get(`price:${symbol}`).catch(()=>null);
  if (lastRaw && !/NOAUTH|ERR/i.test(String(lastRaw))) {
    const lp = Number(lastRaw);
    if (Number.isFinite(lp) && lp > 0) return { price: lp, stale: false };
  }
  // fallback 2: intraday:SYM JSON
  const intradayRaw = await redis.get(`intraday:${symbol}`).catch(()=>null);
  if (intradayRaw && !/NOAUTH|ERR/i.test(String(intradayRaw))) {
    try {
      const obj = JSON.parse(intradayRaw);
      const p = Number(obj.price);
      if (Number.isFinite(p) && p > 0) return { price: p, stale: true };
    } catch {}
  }
  return null;
}

function qtyFromNotional(notionalUsd, price) {
  const n = Number(notionalUsd);
  const p = Number(price);
  if (!Number.isFinite(n) || !Number.isFinite(p) || n <= 0 || p <= 0) return 0;
  return Math.max(1, Math.floor(n / p));
}

async function getSpreadBps(symbol) {
  try {
    const [bidRaw, askRaw] = await Promise.all([
      redis.get(`md:bid:${symbol}`).catch(()=>null),
      redis.get(`md:ask:${symbol}`).catch(()=>null),
    ]);
    const bid = Number(bidRaw||0);
    const ask = Number(askRaw||0);
    if (bid>0 && ask>0) {
      const mid = (bid+ask)/2;
      return mid>0 ? ((ask-bid)/mid)*10000 : 0;
    }
    const quoteRaw = await redis.get(`md:quote:${symbol}`).catch(()=>null);
    if (quoteRaw) {
      try {
        const q = JSON.parse(quoteRaw);
        const b = Number(q.bid ?? q.bidPrice ?? 0);
        const a = Number(q.ask ?? q.askPrice ?? 0);
        if (b>0 && a>0) {
          const mid = (b+a)/2;
          return mid>0 ? ((a-b)/mid)*10000 : 0;
        }
      } catch {}
    }
  } catch {}
  return 0;
}

async function getKisLastPrice(symbol) {
  const raw = await redis.get(`price:${symbol}`).catch(()=>null);
  const p = Number(raw||0);
  return (Number.isFinite(p) && p>0) ? p : null;
}

function limitPriceFromRef(side, refPrice, spreadBps) {
  // v1.4: 동적 버퍼(변동성/리스크에 따라 추가 bps 증가)
  const sp = Number.isFinite(spreadBps) ? spreadBps : 0;
  const v = Number.isFinite(globalThis.__VOL_BPS__) ? globalThis.__VOL_BPS__ : null;
  const r = Number.isFinite(globalThis.__RISK_SCORE__) ? globalThis.__RISK_SCORE__ : null;

  let dynExtra = LIMIT_EXTRA_BPS;
  // volatility가 높을수록 보수적 limit(버퍼↑) — 70/100/150bps 기준
  if (v !== null) {
    if (v >= 150) dynExtra += 15;
    else if (v >= 100) dynExtra += 8;
    else if (v >= 70) dynExtra += 4;
  }
  // risk가 높을수록 보수적 limit(버퍼↑)
  if (r !== null) {
    if (r >= 85) dynExtra += 10;
    else if (r >= 75) dynExtra += 6;
    else if (r >= 65) dynExtra += 3;
  }

  const baseBps = Math.max(LIMIT_MIN_BPS, sp/2 + dynExtra);
  const bps = Math.min(LIMIT_MAX_BPS, baseBps);
  const band = bps / 10000.0;
  const s = String(side).toUpperCase();
  const px = Number(refPrice);
  if (!Number.isFinite(px) || px<=0) return px;
  const lp = (s === "BUY") ? px*(1+band) : px*(1-band);
  return Math.round(lp*100)/100;
}

function isUsMarketSessionNY() {
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hhmm = ny.getHours()*100 + ny.getMinutes();
  return hhmm >= 930 && hhmm <= 1600;
}

async function getAvailableCashUsd() {
  const raw = await redis.get("kis:live:available_cash").catch(()=>null);
  const v = Number(raw||0);
  return Number.isFinite(v) ? v : 0;
}

// ── Order Intent (executor-compatible, v1.1) ──

async function emitOrderIntent(side, symbol, notionalUsd, meta){
  const ts = new Date().toISOString();
  if (!ALLOW_OFFSESSION && !isUsMarketSessionNY()) {
    await opsEvent("INFO","TRANSITION_SKIP_OFFSESSION",`${side} ${symbol} skip: off-session`, `tx_off|${symbol}|${Math.floor(Date.now()/60000)}`);
    return "skipped_offsession";
  }

  const ref = await getRefPrice(symbol);
  if (!ref) {
    await opsEvent("WARN","TRANSITION_SKIP_NO_PRICE",`${side} ${symbol} skip: no ref price`, `tx_nopx|${symbol}|${Math.floor(Date.now()/60000)}`);
    return "skipped_no_price";
  }
  const refPx = ref.price;
  const isStale = !!ref.stale;
  let nUsd = Number(notionalUsd);
  if (String(side).toUpperCase()==="BUY") {
    const avail = await getAvailableCashUsd();
    const cap = Math.max(0, avail * (1 - CASH_RESERVE_PCT));
    if (cap > 0) {
      nUsd = Math.min(nUsd, cap);
    } else if (avail > 0) {
      return "skipped_cash";
    }
  }
  if (nUsd < MIN_NOTIONAL_USD) return "skipped_min_notional";

  let qty = qtyFromNotional(nUsd, refPx);
  qty = Math.min(MAX_SHARES_PER_INTENT, qty);
  if (qty <= 0) return "skipped_qty_zero";

  const spreadBps = await getSpreadBps(symbol);
  const limitPx = limitPriceFromRef(side, refPx, spreadBps);
  const kisLast = await getKisLastPrice(symbol);
  let priceGapBpsSend = null;
  if (kisLast && kisLast > 0) {
    const gap = ((refPx - kisLast) / kisLast) * 10000;
    if (Math.abs(gap) <= 25) priceGapBpsSend = Math.round(gap*10)/10;
  }

  const intent = {
    schema: "emarkos.order_intent.v1",
    ts,
    intent_id: `tx_${symbol}_${Math.floor(Date.now()/1000)}`,
    origin_intent_id: `tx_${symbol}_${Math.floor(Date.now()/1000)}`,
    cycle_id: meta?.cycle_id || `transition_${ts.slice(0,16)}`,
    symbol,
    side: String(side).toUpperCase(),
    delta_shares: qty,
    last_price: refPx,
    limit: { type: "LIMIT", price: limitPx },
    reason: "PHASED_TRANSITION",
    priority: "NORMAL",
    alpha_metadata: {
      micro_send_stale: isStale,
      spread_bps: Number.isFinite(spreadBps) ? Math.round(spreadBps*10)/10 : 0,
      limit_buffer_bps: Math.round((Math.abs(limitPx-refPx)/refPx)*10000*10)/10,
      ...(kisLast ? { kis_quote: { last: kisLast } } : {}),
      ...(priceGapBpsSend !== null ? { price_gap_bps_send: priceGapBpsSend } : {}),
    },
    meta,
  };

  if (DRY_RUN){
    await opsEvent("INFO","TRANSITION_INTENT_DRYRUN",`${intent.side} ${symbol} qty=${qty} px=${refPx} lim=${limitPx} sp=${Math.round(spreadBps)}bps notional≈$${Math.round(qty*refPx)}`,`tx_dry|${symbol}|${intent.side}|${Math.floor(Date.now()/60000)}`);
    return "dryrun_emitted";
  }

  await redis.xadd(ORDER_INTENT_STREAM, "*", "json", JSON.stringify(intent));
  if (intent.side === "BUY") {
    await redis.set("buy:pipeline:emit:last_ts", String(Math.floor(Date.now()/1000)), "EX", 86400*14);
  }
  return "emitted";
}

// ── Main cycle ──

async function oneCycle(){
  const [
    planRaw, budgetRaw, pauseReason,
    rawActual, rawChampion, rawNext,
    alphaRaw, riskScoreRaw, tradeHalt, tradeHaltExec, killSwitch, safeLevelRaw,
    halfSpeedRaw,
    volBpsRaw,
    fillP95, kisLat, rejectRaw, slipRaw
  ] = await Promise.all([
    redis.get("transition:alpha_plan"),
    redis.get("transition:daily_budget_usd"),
    redis.get("transition:pause_reason"),
    redis.get(KEY_ACTUAL_POS),
    redis.get(KEY_CHAMPION_TARGET),
    redis.get(KEY_NEXTGEN2_TARGET),
    redis.get(TRANSITION_ALPHA_KEY),
    redis.get(KEY_RISK_SCORE),
    redis.get(KEY_TRADE_HALT),
    redis.get(KEY_TRADE_HALT_EXEC),
    redis.get(KEY_KILL_SWITCH),
    redis.get(KEY_CORE_SAFE_LEVEL),
    redis.get(KEY_HALF_SPEED),
    redis.get(KEY_VOL_BPS),
    redis.get(KEY_KPI_FILL_P95),
    redis.get(KEY_KPI_KIS_LAT),
    redis.get(KEY_KPI_REJECT),
    redis.get(KEY_KPI_SLIPPAGE),
  ]);

  const riskScore = riskScoreRaw ? Number(riskScoreRaw) : 0;
  const safeLevel = (safeLevelRaw && safeLevelRaw.trim()) ? safeLevelRaw.trim() : "NORMAL";
  const volBps = volBpsRaw ? Number(volBpsRaw) : null;
  // v1.4: 동적 limit buffer에서 사용할 전역 캐시(현재 cycle)
  globalThis.__VOL_BPS__ = Number.isFinite(volBps) ? volBps : null;
  globalThis.__RISK_SCORE__ = Number.isFinite(riskScore) ? riskScore : null;

  if (tradeHalt === "true" || tradeHaltExec === "true") return opsEvent("WARN","TRANSITION_PAUSED","trade_halt active","tx_pause|halt");
  if (killSwitch === "true") return opsEvent("WARN","TRANSITION_PAUSED","kill_switch active","tx_pause|kill");
  if (safeLevel === "L3" || safeLevel === "HALT") return opsEvent("WARN","TRANSITION_PAUSED","safe_level L3/HALT","tx_pause|l3");
  if (pauseReason && pauseReason !== "") return opsEvent("WARN","TRANSITION_PAUSED",`pause_reason=${pauseReason}`,"tx_pause|reason");
  if (riskScore >= 85) return opsEvent("WARN","TRANSITION_PAUSED",`risk_score=${riskScore}>=85`,"tx_pause|risk");

  const budget = budgetRaw ? Number(budgetRaw) : 0;
  const halfSpeed = (halfSpeedRaw === "true");
  if (!budget || budget <= 0) return opsEvent("WARN","TRANSITION_NO_BUDGET","daily_budget_usd<=0","tx_budget|0");
  const cycleBudget = halfSpeed ? Math.floor(budget * 0.5) : budget;
  // v1.8: BUY fraction 동적 조절
  const effBuyFrac = effectiveBuyFraction(BUY_BUDGET_FRACTION, globalThis.__VOL_BPS__, globalThis.__RISK_SCORE__);

  const alpha = alphaRaw ? Number(alphaRaw) : 0.0;
  let plan = null;
  try { plan = planRaw ? JSON.parse(planRaw) : null; } catch {}

  const actualPos = normalizeWeights(parsePositionsJson(rawActual));
  const champPos = normalizeWeights(parsePositionsJson(rawChampion));
  const nextPos = normalizeWeights(parsePositionsJson(rawNext));

  const actualMap = toWeightMap(actualPos);
  const champMap = toWeightMap(champPos);
  const nextMap = toWeightMap(nextPos);

  const aum = actualPos.reduce((a,p)=>a+(p.mv||0),0);
  if (!aum || aum <= 0) return opsEvent("WARN","TRANSITION_PAUSED","AUM unknown (actual mv missing)","tx_pause|aum");

  const alphaStep = plan && plan.alpha_step ? Number(plan.alpha_step) : 0;
  const newAlpha = clamp(alpha + alphaStep, 0, 1);

  const symbols = unionSymbols(champMap, nextMap, actualMap);
  const hybridMap = new Map();
  for (const sym of symbols){
    const cw = champMap.get(sym)||0;
    const nw = nextMap.get(sym)||0;
    const hw = (1-newAlpha)*cw + newAlpha*nw;
    if (Math.abs(hw) > 1e-8) hybridMap.set(sym, hw);
  }

  // v1.6: 동적 gap_min_bps (vol/risk 기반)
  const gapMinBps = dynamicGapMinBps(globalThis.__VOL_BPS__, globalThis.__RISK_SCORE__);
  const maxOrdersThisCycle = dynamicMaxOrders(globalThis.__VOL_BPS__, globalThis.__RISK_SCORE__);

  const rawDiffs = [];
  for (const sym of symbols){
    const a = actualMap.get(sym)||0;
    const t = hybridMap.get(sym)||0;
    const delta = t - a;
    // v1.6: 동적 gap threshold
    if (Math.abs(delta) < (gapMinBps / 10000.0)) continue;
    const notional = Math.abs(delta) * aum;
    if (notional < 50) continue;
    rawDiffs.push({ sym, delta, notional });
  }
  // v1.6: symbol netting (같은 심볼의 buy/sell delta 합산)
  const diffs = NETTING_ENABLED ? netDeltasBySymbol(rawDiffs) : rawDiffs;
  // netting 후 notional 재계산 (delta 합산으로 notional이 변경될 수 있음)
  for (const d of diffs) {
    d.notional = Math.abs(d.delta) * aum;
  }

  // v1.5: SELL 우선 실행(현금 확보) → BUY
  const sells = [];
  const buys = [];
  for (const d of diffs){
    if (d.delta < 0) sells.push(d);
    else buys.push(d);
  }
  sells.sort((x,y)=>y.notional - x.notional);
  buys.sort((x,y)=>y.notional - x.notional);
  const ordered = SELL_FIRST ? sells.concat(buys) : diffs.sort((x,y)=>y.notional - x.notional);

  let remaining = cycleBudget;
  // v1.8: 동적 BUY fraction 적용
  let remainingBuy = Math.floor(cycleBudget * effBuyFrac);
  let remainingSell = cycleBudget;
  let nOrders = 0;
  let executed = 0;
  let sellsEmitted = 0;
  let sellsExecuted = 0;
  let sellsExecutedUsd = 0;  // v1.8: SELL executed USD 누적
  let buySkippedNoSell = 0;
  let buyCandidates = 0;  // v1.9: BUY 후보 수
  let buySkippedByGate = 0;  // v1.9: SELL gate로 BUY 스킵된 수
  // v1.9.2: BUY 스킵 원인별 카운터
  let buySkippedByBudget = 0;
  let buySkippedByCash = 0;
  let buySkippedNoPrice = 0;
  let buySkippedOffsession = 0;
  let buySkippedMinNotional = 0;
  let nettedCount = NETTING_ENABLED ? (rawDiffs.length - diffs.length) : 0;

  // Concentration Guard
  const conc = computeTopConcentration(actualMap, CONCENTRATION_TOPK);
  const overConc = conc.top.some(x=>x.w>=CONCENTRATION_MAX_W) || conc.sum>=CONCENTRATION_TOPK_MAX;
  for (const d of ordered){
    if (nOrders >= maxOrdersThisCycle) break;
    if (remaining <= 0) break;

    const side = d.delta > 0 ? "BUY" : "SELL";
    if (overConc && side === "BUY") {
      const isTopConc = conc.top.find(x=>x.sym===d.sym);
      if (isTopConc) { await opsEvent("INFO","TRANSITION_CONC_SKIP", `skip BUY ${d.sym} due to concentration w=${(isTopConc.w*100).toFixed(1)}%`, `tx_conc_skip|${d.sym}|${Math.floor(Date.now()/60000)}`); continue; }
    }
    if (side === "BUY") buyCandidates += 1;

    // v1.9+v1.9.2: auto cash bottleneck control - effective BUY_AFTER_SELL 결정
    let buyAfterSellEffective = BUY_AFTER_SELL;
    if (AUTO_BUY_AFTER_SELL) {
      const cashUsd = await getCashAvailableForAuto();
      // v1.9.2: 실시간 buySkip 계산 (루프 중간)
      const totalSkipped = buySkippedByGate + buySkippedByBudget + buySkippedByCash + buySkippedNoPrice + buySkippedOffsession + buySkippedMinNotional;
      const liveSkipRate = buyCandidates > 0 ? (totalSkipped / buyCandidates) : 0;
      const liveCashRate = buyCandidates > 0 ? (buySkippedByCash / buyCandidates) : 0;
      const liveBuySkip = { total: liveSkipRate, gate: 0, budget: 0, cash: liveCashRate, no_price: 0, offsession: 0, min_notional: 0 };
      const autoVal = await maybeAutoToggleBuyAfterSell(liveBuySkip, cashUsd);
      if (autoVal === true) buyAfterSellEffective = true;
      if (autoVal === false) buyAfterSellEffective = false;
    }

    // v1.8+v1.9: SELL executed USD가 threshold 이상일 때만 BUY 허용
    if (buyAfterSellEffective && side === "BUY" && sellsExecutedUsd < SELL_EXEC_THRESHOLD_USD) {
      buySkippedByGate += 1;
      continue;
    }

    // v1.6: side별 예산 제한
    if (side === "BUY" && remainingBuy <= 0) { buySkippedByBudget += 1; continue; }
    if (side === "SELL" && remainingSell <= 0) continue;
    const sideBudget = (side === "BUY") ? remainingBuy : remainingSell;
    const perSymbolCapUsd = Math.floor(aum * PER_SYMBOL_DAILY_CAP_FRAC);
    const take = Math.min(d.notional, sideBudget, remaining, perSymbolCapUsd);
    if (take < 50) continue;

    const meta = { alpha_prev: round(alpha,4), alpha_new: round(newAlpha,4), risk: riskScore, reason: "phased_transition", cycle_id: `transition_${new Date().toISOString().slice(0,10)}` };
    const st = await emitOrderIntent(side, d.sym, take, meta);
    // v1.9.2: emitOrderIntent 반환값으로 원인별 카운터 업데이트
    if (side === "BUY" && st && st.startsWith("skipped_")) {
      if (st === "skipped_no_price") buySkippedNoPrice += 1;
      else if (st === "skipped_offsession") buySkippedOffsession += 1;
      else if (st === "skipped_cash") buySkippedByCash += 1;
      else if (st === "skipped_min_notional" || st === "skipped_qty_zero") buySkippedMinNotional += 1;
      continue;
    }
    if (st && st.startsWith("skipped_")) continue;

    remaining -= take;
    if (side === "BUY") remainingBuy -= take; else remainingSell -= take;
    executed += take;
    nOrders += 1;
    if (side === "SELL") { sellsEmitted += 1; sellsExecuted += 1; sellsExecutedUsd += take; }
  }

  // v1.9.2: BUY skip breakdown
  const buySkip = {
    total: buyCandidates > 0 ? ((buySkippedByGate + buySkippedByBudget + buySkippedByCash + buySkippedNoPrice + buySkippedOffsession + buySkippedMinNotional) / buyCandidates) : 0,
    gate: buyCandidates > 0 ? (buySkippedByGate / buyCandidates) : 0,
    budget: buyCandidates > 0 ? (buySkippedByBudget / buyCandidates) : 0,
    cash: buyCandidates > 0 ? (buySkippedByCash / buyCandidates) : 0,
    no_price: buyCandidates > 0 ? (buySkippedNoPrice / buyCandidates) : 0,
    offsession: buyCandidates > 0 ? (buySkippedOffsession / buyCandidates) : 0,
    min_notional: buyCandidates > 0 ? (buySkippedMinNotional / buyCandidates) : 0,
  };

  await redis.set(TRANSITION_ALPHA_KEY, String(round(newAlpha,6)));

  const quality = {
    ts: new Date().toISOString(),
    dry_run: DRY_RUN,
    alpha_prev: alpha,
    alpha_new: newAlpha,
    budget_total: budget,
    budget_used: Math.round(executed),
    budget_cycle: cycleBudget,
    orders: nOrders,
    risk_score: riskScore,
    half_speed: halfSpeed,
    vol_bps: volBps,
    gap_min_bps: gapMinBps,
    gap_min_bps_base: GAP_MIN_BPS_BASE,
    sell_first: SELL_FIRST,
    sells_emitted: sellsEmitted,
    sells_executed: sellsExecuted,
    eff_buy_fraction: effBuyFrac,
    sell_exec_usd: Math.round(sellsExecutedUsd),
    sell_exec_threshold_usd: SELL_EXEC_THRESHOLD_USD,
    buy_candidates: buyCandidates,
    buy_skipped_by_sell_gate: buySkippedByGate,
    buy_skipped_by_budget: buySkippedByBudget,
    buy_skipped_by_cash: buySkippedByCash,
    buy_skipped_no_price: buySkippedNoPrice,
    buy_skipped_offsession: buySkippedOffsession,
    buy_skipped_min_notional: buySkippedMinNotional,
    buy_skip_breakdown: {
      total: Math.round(buySkip.total*1000)/1000,
      gate: Math.round(buySkip.gate*1000)/1000,
      budget: Math.round(buySkip.budget*1000)/1000,
      cash: Math.round(buySkip.cash*1000)/1000,
      no_price: Math.round(buySkip.no_price*1000)/1000,
      offsession: Math.round(buySkip.offsession*1000)/1000,
      min_notional: Math.round(buySkip.min_notional*1000)/1000,
    },
    buy_skipped_no_sell: buySkippedNoSell,
    netting_enabled: NETTING_ENABLED,
    netted_count: nettedCount,
    max_orders_this_cycle: maxOrdersThisCycle,
    auto_buy_after_sell: AUTO_BUY_AFTER_SELL,
    kpi: {
      fill_p95_ms: fillP95 ? Number(fillP95) : null,
      kis_api_avg_ms: kisLat ? Number(kisLat) : null,
      reject: rejectRaw ? Number(rejectRaw) : null,
      slippage_state: (()=>{ try{return slipRaw?JSON.parse(slipRaw):null;}catch{return slipRaw||null;} })(),
    }
  };
  await redis.set("transition:quality", JSON.stringify(quality), "EX", 300);

  await redis.set("transition:last_cycle", JSON.stringify({
    ts: new Date().toISOString(),
    alpha_prev: alpha,
    alpha_new: newAlpha,
    budget: cycleBudget,
    executed: Math.round(executed),
    orders: nOrders,
    dry_run: DRY_RUN,
  }), "EX", 86400);

  await opsEvent("INFO","TRANSITION_CYCLE",
    `alpha ${round(alpha,3)}→${round(newAlpha,3)} executed=$${Math.round(executed)} orders=${nOrders}/${maxOrdersThisCycle} sells=${sellsEmitted} sellExec=$${Math.round(sellsExecutedUsd)} buyCand=${buyCandidates} buyGateSkip=${buySkippedByGate} buySkipRate=${Math.round(buySkip.total*100)}% skipBudget=${buySkippedByBudget} skipCash=${buySkippedByCash} skipNoPrice=${buySkippedNoPrice} skipOffSess=${buySkippedOffsession} skipMinNot=${buySkippedMinNotional} netted=${nettedCount} budget=$${cycleBudget} effBuyFrac=${effBuyFrac} half=${halfSpeed} sell_first=${SELL_FIRST} gap=${gapMinBps}bps vol=${volBps||"?"} auto_bas=${AUTO_BUY_AFTER_SELL} dry=${DRY_RUN} fill_p95=${fillP95||"?"} kis_ms=${kisLat||"?"} reject=${rejectRaw||"?"}`,
    `tx_cycle|${Math.floor(Date.now()/60000)}`
  );
}

async function main(){
  console.log(`[TRANSITION_ENGINE] v1.9.2 start dry_run=${DRY_RUN} loop=${LOOP_SEC}s sell_first=${SELL_FIRST} buy_after_sell=${BUY_AFTER_SELL} auto_bas=${AUTO_BUY_AFTER_SELL} buy_frac=${BUY_BUDGET_FRACTION} sell_thresh=$${SELL_EXEC_THRESHOLD_USD} cash_low=$${CASH_LOW_USD} cash_high=$${CASH_HIGH_USD} skip_on=${BUY_SKIP_ON} skip_off=${BUY_SKIP_OFF} streak=${AUTO_STREAK_NEED} cooldown=${AUTO_COOLDOWN_SEC}s netting=${NETTING_ENABLED} gap_base=${GAP_MIN_BPS_BASE} gap_range=[${GAP_MIN_BPS_MIN},${GAP_MIN_BPS_MAX}] max_orders_range=[${MAX_ORDERS_MIN},${MAX_ORDERS_MAX}] cash_sanity=[${CASH_SANITY_MIN_USD},${CASH_SANITY_MAX_USD}] anom_streak=${CASH_ANOMALY_STREAK_NEED} anom_cooldown=${CASH_ANOMALY_COOLDOWN_SEC}s cash_keys=${CASH_FALLBACK_KEYS.length}`);
  while (true){
    try { await oneCycle(); }
    catch(e){ await opsEvent("WARN","TRANSITION_ERROR",String(e.message||e),`tx_err|${Math.floor(Date.now()/60000)}`); }
    await new Promise(r=>setTimeout(r, LOOP_SEC*1000));
  }
}

main().catch(e=>{ console.error(e); process.exit(1); });
