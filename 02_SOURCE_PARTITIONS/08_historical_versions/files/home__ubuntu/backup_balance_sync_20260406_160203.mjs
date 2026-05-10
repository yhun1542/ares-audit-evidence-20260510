#!/usr/bin/env node
/**
 * KIS Balance Sync Service v1.2.0
 * 
 * 기능:
 * 1. 주기적으로 KIS 실제 잔고 조회 (30초마다)
 * 2. cost_basis와 target_positions를 실제 잔고와 동기화
 * 3. 체결 내역 조회하여 외부 거래 감지
 * 4. 불일치 발생 시 감사 로그 기록
 * 5. v1.1.0: emarkos:v1:positions에 포지션 스냅샷 저장 (orchestrator용)
 * 6. v1.2.0: TTTS3007R API로 정확한 현금 조회 (원화 예수금 포함)
 */
import Redis from "ioredis";
import fetch from "node-fetch";
import { loadCanonicalKisToken } from "./aub-trading-system/lib/kis_token_ssot.mjs";

const redis = new Redis(process.env.REDIS_URL || "redis://localhost:6379", { retryStrategy: (times) => Math.min(times * 500, 30000), maxRetriesPerRequest: null });

// 설정
const CONFIG = {
  KIS_BASE_URL: process.env.KIS_BASE_URL || "https://openapi.koreainvestment.com:9443",
  KIS_APP_KEY: process.env.KIS_APP_KEY,
  KIS_APP_SECRET: process.env.KIS_APP_SECRET,
  KIS_ACCOUNT_NO: process.env.KIS_ACCOUNT_NO,
  KIS_ACCOUNT_PROD_CD: process.env.KIS_ACCOUNT_PROD_CD || "01",
  SYNC_INTERVAL_MS: Number(process.env.KIS_BALANCE_SYNC_INTERVAL_MS || "30000"), // 30초
  COST_BASIS_KEY: "emarkos:v1:cost_basis",
  EXECUTION_POSITION_STATE_KEY: process.env.EXECUTION_POSITION_STATE_KEY || "execution:position_state:v1",
  LEGACY_EXECUTION_STATE_KEY: process.env.LEGACY_EXECUTION_STATE_KEY || "champion:v670:target_positions",
  AUDIT_KEY: "emarkos:v1:ops_audit",
  TOKEN_KEY: "emarkos:v1:kis:token",
  // v1.1.0: 포지션 스냅샷 키 추가
  POSITIONS_KEY: "emarkos:v1:positions",
  POSITIONS_TTL_SEC: 0, // FIX v1.1.0: No TTL — persist last valid positions forever
  // Cash Sync 설정
  CASH_KEY: "kis:live:available_cash",
  CASH_SYNC_STATUS_KEY: "kis:live:cash_sync_status",
  CASH_TTL_MARKET: 0,   // FIX v1.1.0: No TTL — persist last valid cash forever
  CASH_TTL_OFF: 0,       // FIX v1.1.0: No TTL — prevent stale cash disappearance
};

let syncRunning = false;

function requireEnv(name, value) {
  if (!value) throw new Error(`${name} environment variable is required`);
}

function log(level, message, data = {}) {
  console.log(JSON.stringify({
    timestamp: new Date().toISOString(),
    level,
    component: "kis-balance-sync",
    message,
    ...data,
  }));
}

async function getAccessToken() {
  try {
    const token = await loadCanonicalKisToken(redis, { minValidityMs: 60000 });
    return token?.access_token || null;
  } catch (e) {
    log("ERROR", "Failed to get canonical access token", { error: e.message });
  }
  return null;
}

async function fetchKISBalance() {
  // [MULTI-EXCHANGE-FIX v1.0.0] KIS JTTT3012R은 OVRS_EXCG_CD별로 조회 필요.
  // NASD(NASDAQ), NYSE, AMEX 3개 거래소를 순차 조회하여 전체 포지션 수집.
  // 각 거래소별 tr_cont 기반 페이지네이션 완전 지원.
  const accessToken = await getAccessToken();
  if (!accessToken) {
    log("ERROR", "No access token available");
    return null;
  }
  try {
    const allPositions = {};
    let output2 = null;
    const EXCHANGES = ["NASD", "NYSE", "AMEX"];
    const MAX_PAGES = 10;

    for (const excg of EXCHANGES) {
      let ctxAreaFk = "";
      let ctxAreaNk = "";
      let pageNum = 0;

      while (pageNum < MAX_PAGES) {
        pageNum++;
        const url = new URL(`${CONFIG.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance`);
        url.searchParams.append("CANO", CONFIG.KIS_ACCOUNT_NO);
        url.searchParams.append("ACNT_PRDT_CD", CONFIG.KIS_ACCOUNT_PROD_CD);
        url.searchParams.append("OVRS_EXCG_CD", excg);
        url.searchParams.append("TR_CRCY_CD", "USD");
        url.searchParams.append("CTX_AREA_FK200", ctxAreaFk);
        url.searchParams.append("CTX_AREA_NK200", ctxAreaNk);

        const resp = await fetch(url.toString(), {
          method: "GET",
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": `Bearer ${accessToken}`,
            "appkey": CONFIG.KIS_APP_KEY,
            "appsecret": CONFIG.KIS_APP_SECRET,
            "tr_id": "JTTT3012R",
          },
        });

        const data = await resp.json();

        if (data.rt_cd !== "0") {
          log("WARN", "KIS API error for exchange", { excg, rt_cd: data.rt_cd, msg: data.msg1, page: pageNum });
          break; // 이 거래소 스킵, 다음 거래소로
        }

        // 이 페이지의 종목 누적 (중복 종목은 qty 합산)
        const pageItems = data.output1 || [];
        for (const item of pageItems) {
          const symbol = item.ovrs_pdno?.trim().toUpperCase();
          if (!symbol) continue;
          const qty = parseInt(item.ovrs_cblc_qty, 10) || 0;
          const sellableQty = parseInt(item.ord_psbl_qty, 10) || 0; // [SELLABLE-FIX] 매도가능수량
          if (qty <= 0) continue;

          if (allPositions[symbol]) {
            // 중복 종목: qty 합산, 평균단가 가중평균
            const existing = allPositions[symbol];
            const newQty = existing.qty + qty;
            const newAvgPx = ((existing.avg_px * existing.qty) + (parseFloat(item.pchs_avg_pric) * qty)) / newQty;
            allPositions[symbol] = {
              qty: newQty,
              avg_px: newAvgPx,
              eval_amt: existing.eval_amt + (parseFloat(item.ovrs_stck_evlu_amt) || 0),
              pnl: existing.pnl + (parseFloat(item.frcr_evlu_pfls_amt) || 0),
              sellable_qty: (existing.sellable_qty || 0) + sellableQty, // [SELLABLE-FIX]
              excg: existing.excg + "+" + excg,
            };
          } else {
            allPositions[symbol] = {
              qty,
              avg_px: parseFloat(item.pchs_avg_pric) || 0,
              eval_amt: parseFloat(item.ovrs_stck_evlu_amt) || 0,
              pnl: parseFloat(item.frcr_evlu_pfls_amt) || 0,
              sellable_qty: sellableQty, // [SELLABLE-FIX]
              excg,
            };
          }
        }

        // output2는 NASD 첫 페이지에서만 수집 (계좌 요약)
        if (excg === "NASD" && pageNum === 1 && data.output2) {
          output2 = data.output2;
        }

        log("DEBUG", "KIS API page fetched", {
          excg,
          page: pageNum,
          items: pageItems.length,
          total_so_far: Object.keys(allPositions).length,
        });

        // 연속 조회 여부
        const trCont = resp.headers.get("tr_cont") || data.tr_cont || "";
        if ((trCont === "F" || trCont === "M") && (data.ctx_area_fk200 || data.ctx_area_nk200)) {
          ctxAreaFk = data.ctx_area_fk200 || "";
          ctxAreaNk = data.ctx_area_nk200 || "";
          await new Promise(r => setTimeout(r, 200));
        } else {
          break; // 마지막 페이지
        }
      }
    }

    // excg 필드 제거 (내부 메타데이터)
    const positions = {};
    for (const [sym, pos] of Object.entries(allPositions)) {
      positions[sym] = {
        qty: pos.qty,
        avg_px: pos.avg_px,
        eval_amt: pos.eval_amt,
        pnl: pos.pnl,
        sellable_qty: pos.sellable_qty || 0, // [SELLABLE-FIX]
      };
    }

    log("INFO", "KIS balance fetch complete (multi-exchange)", {
      exchanges: "NASD+NYSE+AMEX",
      total_positions: Object.keys(positions).length,
      symbols: Object.keys(positions).sort().join(","),
    });

    return { positions, output2 };
  } catch (e) {
    log("ERROR", "Failed to fetch KIS balance", { error: e.message });
    return null;
  }
}

async function fetchKISExecutions() {
  const accessToken = await getAccessToken();
  if (!accessToken) return [];

  try {
    const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    const url = new URL(`${CONFIG.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-ccnl`);
    url.searchParams.append("CANO", CONFIG.KIS_ACCOUNT_NO);
    url.searchParams.append("ACNT_PRDT_CD", CONFIG.KIS_ACCOUNT_PROD_CD);
    url.searchParams.append("PDNO", "");
    url.searchParams.append("ORD_STRT_DT", today);
    url.searchParams.append("ORD_END_DT", today);
    url.searchParams.append("SLL_BUY_DVSN", "00");
    url.searchParams.append("CCLD_NCCS_DVSN", "01"); // 체결만
    url.searchParams.append("OVRS_EXCG_CD", "NASD");
    url.searchParams.append("SORT_SQN", "DS");
    url.searchParams.append("ORD_DT", "");
    url.searchParams.append("ORD_GNO_BRNO", "");
    url.searchParams.append("ODNO", "");
    url.searchParams.append("CTX_AREA_NK200", "");
    url.searchParams.append("CTX_AREA_FK200", "");

    const resp = await fetch(url.toString(), {
      method: "GET",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": `Bearer ${accessToken}`,
        "appkey": CONFIG.KIS_APP_KEY,
        "appsecret": CONFIG.KIS_APP_SECRET,
        "tr_id": "JTTT3001R",
      },
    });

    const data = await resp.json();
    
    if (data.rt_cd !== "0") {
      return [];
    }

    return data.output || [];
  } catch (e) {
    log("ERROR", "Failed to fetch KIS executions", { error: e.message });
    return [];
  }
}

async function syncCostBasis(kisPositions) {
  const changes = [];
  // 현재 cost_basis 조회
  const costBasisData = await redis.hgetall(CONFIG.COST_BASIS_KEY);
  
  // KIS 잔고 기준으로 동기화
  const allSymbols = new Set([
    ...Object.keys(kisPositions),
    ...Object.keys(costBasisData),
  ]);

  for (const symbol of allSymbols) {
    const kisPos = kisPositions[symbol] || { qty: 0, avg_px: 0 };
    let currentCostBasis = { qty: 0, avg_px: 0 };
    
    try {
      if (costBasisData[symbol]) {
        currentCostBasis = JSON.parse(costBasisData[symbol]);
      }
    } catch (e) {}

    // 불일치 감지
    if (currentCostBasis.qty !== kisPos.qty) {
      log("WARN", "Cost basis mismatch detected", {
        symbol,
        system_qty: currentCostBasis.qty,
        kis_qty: kisPos.qty,
        diff: kisPos.qty - currentCostBasis.qty,
      });

      // 동기화
      const newCostBasis = { qty: kisPos.qty, avg_px: kisPos.avg_px || currentCostBasis.avg_px };
      await redis.hset(CONFIG.COST_BASIS_KEY, symbol, JSON.stringify(newCostBasis));
      changes.push({
        symbol,
        from: currentCostBasis,
        to: newCostBasis,
        reason: "kis_balance_sync",
      });

      // 감사 로그
      await redis.xadd(CONFIG.AUDIT_KEY, "*",
        "payload", JSON.stringify({
          kind: "BALANCE_SYNC",
          symbol,
          from_qty: currentCostBasis.qty,
          to_qty: kisPos.qty,
          from_avg_px: currentCostBasis.avg_px,
          to_avg_px: newCostBasis.avg_px,
          ts: new Date().toISOString(),
        })
      );
    }
  }

  return changes;
}


// [Batch A] CAS(WATCH-MULTI) 기반 execution state 업데이트
async function withExecutionStateCAS(mutate, maxRetries = 5) {
  for (let attempt = 0; attempt < maxRetries; attempt += 1) {
    await redis.watch(CONFIG.EXECUTION_POSITION_STATE_KEY);
    const raw = await redis.get(CONFIG.EXECUTION_POSITION_STATE_KEY);
    const current = raw ? JSON.parse(raw) : { version: 0, positions: {} };
    const next = await mutate(JSON.parse(JSON.stringify(current)));
    next.version = (current.version || 0) + 1;
    next.updated_at = new Date().toISOString();
    const multi = redis.multi();
    multi.set(CONFIG.EXECUTION_POSITION_STATE_KEY, JSON.stringify(next));
    const res = await multi.exec();
    if (res !== null) return next;
    log("WARN", "CAS_RETRY", { attempt, key: CONFIG.EXECUTION_POSITION_STATE_KEY });
  }
  throw new Error("execution state CAS conflict exceeded max retries");
}

async function syncTargetPositions(kisPositions) {
  try {
    const targetPosData = await redis.get(CONFIG.EXECUTION_POSITION_STATE_KEY);
    if (!targetPosData) return;

    const targetPos = JSON.parse(targetPosData);
    let changed = false;

    for (const symbol of Object.keys(targetPos)) {
      const kisQty = kisPositions[symbol]?.qty || 0;
      const currentShares = targetPos[symbol].current_shares || 0;

      if (currentShares !== kisQty) {
        log("INFO", "Target position synced", {
          symbol,
          from: currentShares,
          to: kisQty,
        });
        targetPos[symbol].current_shares = kisQty;
        targetPos[symbol].last_sync = new Date().toISOString();
        changed = true;
      }
    }

    if (changed) {
      await redis.set(CONFIG.EXECUTION_POSITION_STATE_KEY, JSON.stringify(targetPos));
    }
  } catch (e) {
    log("ERROR", "Failed to sync target positions", { error: e.message });
  }
}

// v1.1.0: 포지션 스냅샷 저장 (orchestrator용)
async function savePositionsSnapshot(kisPositions) {
  try {
    const snapshot = {
      asOf: new Date().toISOString(),
      count: Object.keys(kisPositions).length,
      positions: {},
    };

    let totalValue = 0;
    for (const [symbol, pos] of Object.entries(kisPositions)) {
      const costBasis = pos.qty * pos.avg_px;
      const pnlPct = costBasis > 0 ? ((pos.eval_amt - costBasis) / costBasis * 100) : 0;
      snapshot.positions[symbol] = {
        shares: pos.qty,
        avgPrice: pos.avg_px,
        marketValue: pos.eval_amt,
        pnl: pos.pnl,
        pnlPct: pnlPct,
      };
      totalValue += pos.eval_amt || 0;
    }
    snapshot.totalMarketValue = totalValue;

    // FIX v1.1.0: No TTL — persist last valid positions forever
    // Previous bug: 5min TTL caused positions to disappear after market close
    // [P3-FIX] Guard against saving empty positions (token failure → empty API response)
    const posCount = snapshot.positions ? Object.keys(snapshot.positions).length : 0;
    if (posCount === 0) {
      log("WARN", "Empty positions detected — skipping snapshot save to prevent data loss", {
        key: CONFIG.POSITIONS_KEY, totalValue: snapshot.totalMarketValue
      });
    } else {
      await redis.set(CONFIG.POSITIONS_KEY, JSON.stringify(snapshot));
    }

    // --- Stage1 PR: broker positions hash cache (truth) ---
    // Key: kis:broker:positions (hash)
    // Field: SYMBOL, Value: JSON {qty, avg_price, current_price, asof_ts}
    try {
      const outKey = "kis:broker:positions";
      const asofTs = Date.now();
      const map = {};
      if (snapshot.positions && typeof snapshot.positions === "object") {
        for (const [sym, p] of Object.entries(snapshot.positions)) {
          const s = String(sym || "").trim().toUpperCase();
          if (!s) continue;
          const qty = Number(p.shares ?? p.qty ?? 0);
          const avg = Number(p.avgPrice ?? p.avg_price ?? 0);
          const cur = Number(p.marketValue && p.shares ? (p.marketValue / p.shares) : 0);
          map[s] = JSON.stringify({ qty, avg_price: avg, current_price: cur, asof_ts: asofTs });
        }
      }
      // Also try kisPositions (original KIS data with exact avg_px)
      if (typeof kisPositions === "object") {
        for (const [sym, pos] of Object.entries(kisPositions)) {
          const s = String(sym || "").trim().toUpperCase();
          if (!s) continue;
          const qty = Number(pos.qty ?? 0);
          const avg = Number(pos.avg_px ?? pos.avg_price ?? 0);
          // [P2-FIX] Calculate current_price from eval_amt/qty when current_price is missing
          let cur = Number(pos.current_price ?? pos.cur_price ?? 0);
          if (cur === 0 && qty > 0) {
            const evalAmt = Number(pos.eval_amt ?? pos.marketValue ?? 0);
            if (evalAmt > 0) {
              cur = evalAmt / qty;
            }
          }
          const sellable = Number(pos.sellable_qty ?? 0);
          map[s] = JSON.stringify({ qty, avg_price: avg, current_price: cur, sellable_qty: sellable, asof_ts: asofTs });
        }
      }
      const pipe = redis.pipeline();
      pipe.del(outKey);
      if (Object.keys(map).length > 0) {
        pipe.hset(outKey, map);
      }
      // V237-FIX: TTL removed — kis:broker:positions is truth, must not expire
      // pipe.expire(outKey, 300); // 5m TTL
      const nowIso = new Date().toISOString();
      pipe.set("kis:broker:positions_sync_status", JSON.stringify({
        schema: "KIS_BROKER_POS_SYNC_V1",
        last_sync_ts: new Date(asofTs).toISOString(), // [SPOF-3A] executor FreshnessGuard용
        ok: true,
        asof_ts: asofTs,
        n: Object.keys(map).length,
        source: "KIS_API_TTTS3012R->emarkos:v1:positions",
      }));  // V237-FIX: TTL removed
      // [v1.3.0] Write freshness timestamps for orchestrator LATENCY_GUARD
      pipe.set("kis:broker:positions:ts", nowIso);
      pipe.set("kis:broker:positions:updated_at", nowIso);
      pipe.set("emarkos:v1:positions:ts", nowIso);
      await pipe.exec();
      log("INFO", "Broker positions hash cache updated", { key: outKey, n: Object.keys(map).length, ts: nowIso });
    } catch (e) {
      log("WARN", "Failed to update broker positions hash cache", { error: e.message });
    }
    
    log("INFO", "Positions snapshot saved", {
      key: CONFIG.POSITIONS_KEY,
      count: snapshot.count,
      totalValue: totalValue.toFixed(2),
    });
  } catch (e) {
    log("ERROR", "Failed to save positions snapshot", { error: e.message });
  }
}

async function runSyncCycle() {
  if (syncRunning) {
    log("WARN", "BALANCE_SYNC_OVERLAP_SKIPPED");
    return;
  }
  syncRunning = true;
  const cycleId = `sync-${Date.now()}`;
  const nowIso = new Date().toISOString();
  log("INFO", "=== Balance Sync Cycle Start ===", { cycle_id: cycleId });

  try {
    let kisResult = null;
    try {
      kisResult = await fetchKISBalance();
    } catch (fetchErr) {
      log("ERROR", "fetchKISBalance threw", { error: fetchErr.message });
      return;
    }
    if (!kisResult || typeof kisResult !== "object") {
      log("WARN", "Skipping sync cycle - no KIS data", { kisResult: String(kisResult) });
      return;
    }
    const { positions: kisPositions, output2 } = kisResult;
    if (!kisPositions || typeof kisPositions !== "object") {
      log("WARN", "Skipping sync cycle - invalid positions data");
      return;
    }

    await syncCashBalance(output2);

    log("INFO", "KIS positions fetched", {
      count: Object.keys(kisPositions).length,
      positions: kisPositions,
    });

    await savePositionsSnapshot(kisPositions);
    const costBasisChanges = await syncCostBasis(kisPositions);
    await syncTargetPositions(kisPositions);

    const executions = await fetchKISExecutions();
    if (executions.length > 0) {
      log("INFO", "Today's executions", { count: executions.length });
    }

    try {
      await redis.multi()
        .set("kis:cash:usd:ts", nowIso)
        .set("kis:balance-sync:heartbeat", String(Math.floor(Date.now() / 1000)), "EX", 180)
        .exec();
    } catch (e) {}

    log("INFO", "=== Balance Sync Cycle Complete ===", {
      cycle_id: cycleId,
      changes: costBasisChanges.length,
    });
  } finally {
    syncRunning = false;
  }
}

// 메인 루프
async function main() {
  requireEnv("KIS_APP_KEY", CONFIG.KIS_APP_KEY);
  requireEnv("KIS_APP_SECRET", CONFIG.KIS_APP_SECRET);
  requireEnv("KIS_ACCOUNT_NO", CONFIG.KIS_ACCOUNT_NO);

  log("INFO", "KIS Balance Sync Service started", {
    version: "1.2.0",
    sync_interval_ms: CONFIG.SYNC_INTERVAL_MS,
    account: `***${CONFIG.KIS_ACCOUNT_NO.slice(-4)}`,
    positions_key: CONFIG.POSITIONS_KEY,
  });

  while (true) {
    await runSyncCycle();
    await new Promise((resolve) => setTimeout(resolve, CONFIG.SYNC_INTERVAL_MS));
  }
}

main().catch((e) => {
  log("FATAL", "Service crashed", { error: e.message });
  process.exit(1);
});


// ============================================================
// v1.2.0: Fetch buying power via TTTS3007R (includes KRW→USD conversion)
// ============================================================
async function fetchKISBuyingPower() {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    log("WARN", "No access token for buying power query");
    return null;
  }
  try {
    const url = new URL(`${CONFIG.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-psamount`);
    url.searchParams.append("CANO", CONFIG.KIS_ACCOUNT_NO);
    url.searchParams.append("ACNT_PRDT_CD", CONFIG.KIS_ACCOUNT_PROD_CD);
    url.searchParams.append("OVRS_EXCG_CD", "NASD");
    url.searchParams.append("OVRS_ORD_UNPR", "100");
    url.searchParams.append("ITEM_CD", "AAPL");
    const resp = await fetch(url.toString(), {
      method: "GET",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": `Bearer ${accessToken}`,
        "appkey": CONFIG.KIS_APP_KEY,
        "appsecret": CONFIG.KIS_APP_SECRET,
        "tr_id": "TTTS3007R",
      },
    });
    const data = await resp.json();
    if (data.rt_cd !== "0" || !data.output) {
      log("WARN", "KIS buying power API failed", { rt_cd: data.rt_cd, msg: data.msg1 });
      return null;
    }
    const out = data.output;
    const result = {
      ord_psbl_frcr_amt: parseFloat(out.ord_psbl_frcr_amt || "0"),      // USD cash only
      echm_af_ord_psbl_amt: parseFloat(out.echm_af_ord_psbl_amt || "0"), // USD + KRW converted
      ovrs_ord_psbl_amt: parseFloat(out.ovrs_ord_psbl_amt || "0"),       // overseas order possible
      exrt: parseFloat(out.exrt || "0"),                                  // exchange rate KRW/USD
      frcr_ord_psbl_amt1: parseFloat(out.frcr_ord_psbl_amt1 || "0"),    // foreign currency orderable 1
    };
    log("INFO", "KIS buying power fetched", {
      usd_cash: result.ord_psbl_frcr_amt,
      total_with_krw: result.echm_af_ord_psbl_amt,
      exrt: result.exrt,
      ovrs_ord_psbl: result.ovrs_ord_psbl_amt,
    });
    return result;
  } catch (e) {
    log("ERROR", "Failed to fetch KIS buying power", { error: e.message });
    return null;
  }
}

// ============================================================
// Cash Sync 함수들 (패치 추가)
// ============================================================

function isMarketHours() {
  const now = new Date();
  const estHour = (now.getUTCHours() - 5 + 24) % 24;
  const estMin = now.getUTCMinutes();
  const day = now.getUTCDay();
  if (day === 0 || day === 6) return false;
  const timeVal = estHour * 100 + estMin;
  return timeVal >= 800 && timeVal <= 1630;
}

async function syncCashBalance(output2) {
  try {
    // v1.2.0: Use TTTS3007R API for accurate cash (includes KRW→USD conversion)
    const buyingPower = await fetchKISBuyingPower();
    
    if (buyingPower) {
      // v1.2.0: Use echm_af_ord_psbl_amt when available (includes KRW→USD)
      // During off-market hours, echm_af may be 0 → fallback to ord_psbl_frcr_amt
      const echmAf = buyingPower.echm_af_ord_psbl_amt;
      const usdOnlyCash = buyingPower.ord_psbl_frcr_amt;
      const exrt = buyingPower.exrt;
      // Use echm_af if positive, otherwise fallback to USD-only cash
      const ovrsOrdPsbl = buyingPower.ovrs_ord_psbl_amt;
      // v1.2.2 FIX: echm_af=0일 때 ovrs_ord_psbl_amt(해외주문가능금액) 사용 (KRW입금분 포함)
      const totalCashRaw = (Number.isFinite(echmAf) && echmAf > 0) ? echmAf 
                         : (Number.isFinite(ovrsOrdPsbl) && ovrsOrdPsbl > usdOnlyCash) ? ovrsOrdPsbl 
                         : usdOnlyCash;
      const krwPortionUsd = (echmAf > 0) ? (echmAf - usdOnlyCash) : 0;

      // v1.2.1: jump smoothing (avoid open-time step change shocking IRB/SRL)
      const prevRaw = await redis.get("emarkos:v1:budget_usd").catch(() => null);
      const prevCash = Number(prevRaw || 0);
      const alpha = Number(process.env.KIS_CASH_JUMP_ALPHA || 0.5);
      const jumpMult = Number(process.env.KIS_CASH_JUMP_MULT || 1.5);
      let totalCash = totalCashRaw;
      let smoothed = false;
      if (isMarketHours() && Number.isFinite(prevCash) && prevCash > 0 && totalCashRaw > prevCash * jumpMult) {
        totalCash = prevCash + (totalCashRaw - prevCash) * Math.max(0.1, Math.min(alpha, 0.9));
        smoothed = true;
      }
      
      if (Number.isFinite(totalCashRaw) && totalCashRaw >= 0) {
        // always store raw for audit/debug
        await redis.set("emarkos:v1:budget_usd_raw", String(totalCashRaw)).catch(()=>null);

        // Store total cash (USD + KRW converted)
        await redis.set(CONFIG.CASH_KEY, totalCash.toString());
        // Also store breakdown for debugging
        await redis.set("kis:live:cash_usd_only", usdOnlyCash.toString());
        await redis.set("kis:live:cash_krw_portion_usd", krwPortionUsd.toString());
        await redis.set("kis:live:exchange_rate", exrt.toString());
        // Update budget_usd key (used by equity-calculator)
        await redis.set("emarkos:v1:budget_usd", totalCash.toString());
        
        await redis.set(CONFIG.CASH_SYNC_STATUS_KEY, JSON.stringify({
          lastSync: new Date().toISOString(),
          cash: totalCash,
          raw_cash: totalCashRaw,
          prev_cash: prevCash || null,
          smoothed,
          smooth_alpha: alpha,
          usd_only: usdOnlyCash,
          krw_portion_usd: krwPortionUsd,
          exchange_rate: exrt,
          source: "TTTS3007R_echm_af",
          status: "OK",
          marketHours: isMarketHours(),
        }));
        
        log("INFO", "Cash synced v1.2.1", {
          total_cash: totalCash,
          raw_cash: totalCashRaw,
          smoothed,
          usd_only: usdOnlyCash,
          krw_portion_usd: krwPortionUsd,
          exchange_rate: exrt,
          source: "TTTS3007R",
          marketHours: isMarketHours(),
        });
        return totalCash;
      }
    }
    
    // Fallback: try output2 from JTTT3012R (legacy behavior)
    if (output2 && typeof output2 === "object") {
      const summary = Array.isArray(output2) ? (output2[0] || {}) : output2;
      const cashStr = summary?.frcr_ord_psbl_amt
                   || summary?.dnca_tot_amt
                   || summary?.prvs_rcdl_tot_amt || null;
      if (cashStr) {
        const realCash = parseFloat(cashStr);
        if (Number.isFinite(realCash) && realCash >= 0) {
          await redis.set(CONFIG.CASH_KEY, realCash.toString());
          await redis.set(CONFIG.CASH_SYNC_STATUS_KEY, JSON.stringify({
            lastSync: new Date().toISOString(),
            cash: realCash,
            source: "JTTT3012R_output2_fallback",
            status: "FALLBACK",
            marketHours: isMarketHours(),
          }));
          log("WARN", "Cash synced via fallback (output2)", { cash: realCash });
          return realCash;
        }
      }
    }
    
    log("DEBUG", "No cash data available - keeping existing value");
    return null;
  } catch (e) {
    log("ERROR", "Cash sync failed", { error: e.message });
    return null;
  }
}
