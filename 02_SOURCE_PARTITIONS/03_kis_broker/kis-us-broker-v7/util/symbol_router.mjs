// util/symbol_router.mjs — 심볼 → 거래소 동적 판별
// 하드코딩 whitelist를 제거하고 실시간으로 KIS price_detail 을 조회하여 거래소를 판별합니다.
// - 로컬 캐시: 24시간 TTL
// - fallback: 기본값 NAS (나스닥)
// - 저장 위치: /tmp/ares-kis-token/symbol-cache.json

import fs from "node:fs";
import path from "node:path";
import { STOCK_QUOTE_TR, EXCH } from "../core/tr_ids.mjs";

// v7.3 (GPT C2): typed error so placeOrder can translate it into
// a REJECTED/UNKNOWN_ROUTE outcome instead of a silent NASD misroute.
export class ExchangeResolutionError extends Error {
  constructor(symbol, attempted, { cause } = {}) {
    super(`Cannot resolve exchange for symbol=${symbol} (tried ${attempted.join(",")})`);
    this.name = "ExchangeResolutionError";
    this.symbol = symbol;
    this.attempted = attempted;
    if (cause) this.cause = cause;
  }
}

const CACHE_PATH = process.env.ARES_SYMBOL_CACHE || "/tmp/ares-kis-token/symbol-cache.json";
const TTL_MS = 24 * 3600 * 1000;

function readCache() {
  try {
    if (!fs.existsSync(CACHE_PATH)) return {};
    return JSON.parse(fs.readFileSync(CACHE_PATH, "utf-8"));
  } catch (_) { return {}; }
}
function writeCache(obj) {
  // v7.2 round-2: atomic write (tmp + rename) to avoid truncated cache if
  // two processes race on the same file (Claude round-1 finding).
  try {
    const dir = path.dirname(CACHE_PATH);
    fs.mkdirSync(dir, { recursive: true });
    const tmp = `${CACHE_PATH}.tmp.${process.pid}.${Date.now()}`;
    fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, CACHE_PATH);
  } catch (_) {}
}

/**
 * 거래소 3코드(NAS/NYS/AMS)를 반환.
 * 캐시에 없으면 NAS/NYS/AMS 순으로 시험 조회하여 유효한 곳을 기록.
 */
export async function resolveExchange(symbol, rest) {
  const sym = String(symbol).trim().toUpperCase();
  const cache = readCache();
  const hit = cache[sym];
  if (hit && hit.t && (Date.now() - hit.t) < TTL_MS) return hit.ex;

  const trials = [EXCH.QUOTE_NAS, EXCH.QUOTE_NYS, EXCH.QUOTE_AMS];
  const attempts = [];
  let lastNetworkError = null;
  for (const excd of trials) {
    try {
      const r = await rest.get("/uapi/overseas-price/v1/quotations/price", {
        tr_id: STOCK_QUOTE_TR.PRICE,
        params: { AUTH: "", EXCD: excd, SYMB: sym },
      });
      attempts.push({ excd, httpOk: !!r.ok, rt_cd: r.data?.rt_cd, hasLast: !!r.data?.output?.last });
      if (r.ok && r.data?.output && (r.data.output.last || r.data.output.base)) {
        cache[sym] = { ex: excd, t: Date.now() };
        writeCache(cache);
        return excd;
      }
    } catch (e) {
      attempts.push({ excd, thrown: String(e).slice(0, 120) });
      lastNetworkError = e;
      /* 다음 거래소 시도 */
    }
  }
  // v7.3 (GPT C2): FAIL-CLOSED by default. The previous silent fallback to
  // NAS could send NYSE/AMEX orders to NASD, which KIS will either reject or
  // (worse) route incorrectly. Callers must catch ExchangeResolutionError
  // and translate it to a REJECTED order outcome.
  //
  // Legacy behaviour is preserved via env toggle for the read-only quote
  // path (e.g., a cold-start dashboard that wants a best-effort price);
  // orderers should never set this flag.
  if (process.env.ARES_ALLOW_NAS_FALLBACK === "1") {
    return EXCH.QUOTE_NAS;
  }
  throw new ExchangeResolutionError(sym, trials.map(t => String(t)), { cause: lastNetworkError });
}

/** 주문 API용 4글자 코드 */
export function quoteToOrderExch(excdQuote) {
  switch (excdQuote) {
    case EXCH.QUOTE_NAS: return EXCH.ORDER_NASD;
    case EXCH.QUOTE_NYS: return EXCH.ORDER_NYSE;
    case EXCH.QUOTE_AMS: return EXCH.ORDER_AMEX;
    default: return EXCH.ORDER_NASD;
  }
}

/** WebSocket 심볼 prefix (정규장 / 주간거래) */
export function wsKey(excdQuote, symbol, extended = false) {
  const sym = String(symbol).trim().toUpperCase();
  if (extended) {
    switch (excdQuote) {
      case EXCH.QUOTE_NAS: return EXCH.WS_NASD_EXTENDED + sym;
      case EXCH.QUOTE_NYS: return EXCH.WS_NYSE_EXTENDED + sym;
      case EXCH.QUOTE_AMS: return EXCH.WS_AMEX_EXTENDED + sym;
    }
  }
  switch (excdQuote) {
    case EXCH.QUOTE_NAS: return EXCH.WS_NASD_REGULAR + sym;
    case EXCH.QUOTE_NYS: return EXCH.WS_NYSE_REGULAR + sym;
    case EXCH.QUOTE_AMS: return EXCH.WS_AMEX_REGULAR + sym;
  }
  return EXCH.WS_NASD_REGULAR + sym;
}
