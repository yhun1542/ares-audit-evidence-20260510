// stock/stock_api.mjs — 미국 해외주식 REST API 래퍼
// 공식 TR_ID 100% 준수, Hashkey 자동 주입, 거래소 자동 판별

import {
  STOCK_TR, STOCK_QUOTE_TR, ORD_DVSN, MODE, stockOrderTr
} from "../core/tr_ids.mjs";
import { resolveExchange, quoteToOrderExch } from "../util/symbol_router.mjs";

export class KisStockApi {
  constructor({ env, rest, logger = console }) {
    this.env = env;
    this.rest = rest;
    this.logger = logger;
    this.mode = env.isPaper ? MODE.PAPER : MODE.REAL;
  }

  // ============================== 주문 ==============================

  /**
   * 미국 매수/매도 주문
   * @param {Object} req - {symbol, side, quantity, price, orderType?, orderDvsn?}
   *   side: "BUY" | "SELL"
   *   orderType: "LIMIT" | "MARKET" (MARKET = aggressive limit 자동 변환)
   *   orderDvsn: "LIMIT"|"MOO"|"LOO"|"MOC"|"LOC" 직접 지정 가능
   */
  async placeOrder(req) {
    const { symbol, side, quantity, price, orderType = "LIMIT", orderDvsn, excd,
            clientOrderId, idempotencyKey } = req;
    // v7.3 C4 (GPT+Claude): the caller is *required* to pass a stable
    // clientOrderId (a.k.a. idempotencyKey) so the request can be safely
    // retried by the UNKNOWN_SUBMISSION reconciler without creating duplicate
    // orders. If the caller does not supply one, generate a UUID v4 here so
    // at least the audit trail captures a per-request identity.
    const reqId = String(idempotencyKey || clientOrderId ||
      (globalThis.crypto?.randomUUID?.() || ("ord-" + Date.now() + "-" + Math.random().toString(16).slice(2))));
    if (!symbol || !side || !quantity) throw new Error("placeOrder: symbol/side/quantity 필수");
    // v7.2 round-4 (Claude): symbol format validation — KIS US tickers are
    // 1..10 uppercase letters/digits, optionally with dot or dash for share
    // classes (e.g. BRK.B) or warrants. Reject anything outside this window
    // so callers get a clear early error instead of a cryptic KIS rt_cd.
    const symUpper = String(symbol).toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9.\-]{0,11}$/.test(symUpper)) {
      throw new Error(`placeOrder: invalid symbol format '${symbol}' (expected [A-Z0-9.-]{1,12})`);
    }
    // v7.2 round-2: hardened numeric validation (Grok/Claude flagged placeOrder
    // accepting NaN/negative/zero inputs that KIS rejects with cryptic codes).
    const qtyNum = Number(quantity);
    if (!Number.isFinite(qtyNum) || qtyNum <= 0 || Math.floor(qtyNum) !== qtyNum) {
      throw new Error(`placeOrder: quantity must be positive integer, got=${quantity}`);
    }
    if (side !== "BUY" && side !== "SELL") {
      throw new Error(`placeOrder: side must be BUY|SELL, got=${side}`);
    }
    if (orderType === "LIMIT") {
      const priceNum = Number(price);
      if (!Number.isFinite(priceNum) || priceNum <= 0) {
        throw new Error(`placeOrder: LIMIT order requires positive price, got=${price}`);
      }
    }

    // v7.3 (GPT): resolveExchange now throws ExchangeResolutionError on
    // failure instead of silently defaulting to NAS. Let it propagate so
    // placeOrder fails closed rather than routing NYSE/AMEX symbols to NASD.
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    const exchOrder = quoteToOrderExch(excdQuote);

    // ORD_DVSN 매핑
    let ordDvsn = ORD_DVSN.LIMIT;
    if (orderDvsn && ORD_DVSN[orderDvsn]) ordDvsn = ORD_DVSN[orderDvsn];

    // 가격 결정 (MARKET 처리)
    let ovrsOrdUnpr = String(price || 0);
    if (orderType === "MARKET") {
      // Aggressive Limit: 매수=매도1호가+슬리피지, 매도=매수1호가-슬리피지
      // v7.3 (Claude #3): asking() now always returns the rest_client
      // wrapper { ok, status, data, ... }. The previous `quote.output`
      // access was ambiguous and could silently miss the ask book when
      // the wrapper was returned.
      const quoteRes = await this.asking(symbol, excdQuote).catch(() => null);
      const qOut = (quoteRes && quoteRes.ok && quoteRes.data && quoteRes.data.output) || null;
      if (qOut) {
        const ask = Number(qOut.pask1 || qOut.ask1 || 0);
        const bid = Number(qOut.pbid1 || qOut.bid1 || 0);
        const tick = 0.01;
        if (side === "BUY" && ask > 0)  ovrsOrdUnpr = (ask + tick * 5).toFixed(2);
        if (side === "SELL" && bid > 0) ovrsOrdUnpr = Math.max(tick, bid - tick * 5).toFixed(2);
      } else {
        // v7.2 round-2: fail-fast on missing quotes — Claude flagged that
        // silently using last±1% when asking is unavailable is unsafe for
        // thin books. Allow explicit override via env for power users.
        const allowLastFallback = process.env.ARES_ALLOW_LAST_AS_MARKET === "1";
        const pr = await this.price(symbol, excdQuote).catch(() => null);
        const last = Number(pr?.data?.output?.last || 0);
        if (allowLastFallback && last > 0) {
          ovrsOrdUnpr = (side === "BUY" ? last * 1.01 : last * 0.99).toFixed(2);
        } else {
          throw new Error(`MARKET 주문 시 호가 조회 실패 (ARES_ALLOW_LAST_AS_MARKET=1 for last±1% fallback): ${symbol}`);
        }
      }
      ordDvsn = ORD_DVSN.LIMIT;  // 공격적 지정가로 전송
    }

    const tr_id = stockOrderTr(side, this.mode);
    const body = {
      CANO: this.env.accountNo,
      ACNT_PRDT_CD: this.env.accountProdCode,
      OVRS_EXCG_CD: exchOrder,
      PDNO: String(symbol).toUpperCase(),
      ORD_QTY: String(quantity),
      OVRS_ORD_UNPR: ovrsOrdUnpr,
      ORD_SVR_DVSN_CD: "0",
      ORD_DVSN: ordDvsn,
      SLL_TYPE: side === "SELL" ? "00" : "",  // 매도 시 '00'(일반 매도) 필수
    };

    this.logger.info && this.logger.info("KIS_ORDER_SEND", {
      tr_id, side, symbol, qty: quantity, price: ovrsOrdUnpr, excg: exchOrder, dvsn: ordDvsn, mode: this.mode
    });

    const r = await this.rest.post("/uapi/overseas-stock/v1/trading/order", {
      tr_id, body, hash: true, idempotencyKey: reqId
    });

    if (!r.ok) {
      // v7.3 C4: distinguish AMBIGUOUS outcomes (network/5xx/body-read) from
      // REJECTED (KIS parsed and said no). AMBIGUOUS results must go through
      // the reconciliation loop (openOrders + filledOrders + by clientOrderId)
      // before the caller decides whether to retry. Returning REJECTED here
      // when the truth is unknown is the pre-v7.3 bug that 4-AI round-1
      // flagged as C4.
      if (r.ambiguous) {
        this.logger.error && this.logger.error("KIS_ORDER_AMBIGUOUS", {
          symbol, side, qty: quantity, reqId,
          status: r.status, error: r.error, message: r.message,
        });
        return {
          ok: false,
          status: "UNKNOWN_SUBMISSION",
          ambiguous: true,
          brokerOrderId: null,
          clientOrderId: reqId,
          reqId,
          error: r.error || "NETWORK",
          msg_cd: r.data?.msg_cd || null,
          message: r.message || r.data?.msg1 || "ambiguous submission",
          raw: r.data,
          // caller hint: reconciler should look up by clientOrderId=reqId
          // in openOrders/filledOrders within RECONCILE_WINDOW_MS (see
          // broker server.mjs) before attempting a re-submission.
          reconcile: {
            byClientOrderId: reqId,
            byAccountSymbolSide: { symbol, side, quantity, excd: exchOrder },
          },
        };
      }
      this.logger.warn && this.logger.warn("KIS_ORDER_REJECT", {
        rt_cd: r.data?.rt_cd, msg_cd: r.data?.msg_cd, msg1: r.data?.msg1, reqId,
      });
      return {
        ok: false,
        status: "REJECTED",
        ambiguous: false,
        brokerOrderId: null,
        clientOrderId: reqId,
        reqId,
        msg_cd: r.data?.msg_cd, message: r.data?.msg1 || "unknown",
        raw: r.data
      };
    }

    const out = r.data.output || {};
    return {
      ok: true,
      status: "SUBMITTED",
      ambiguous: false,
      brokerOrderId: out.ODNO,
      clientOrderId: reqId,
      reqId,
      orderTime: out.ORD_TMD,
      exchange: exchOrder,
      tr_id,
      raw: r.data
    };
  }

  /** 정정·취소 (RVSE_CNCL_DVSN_CD 01=정정, 02=취소) */
  async reviseOrCancel({ symbol, origOrderNo, rvseCnclDvsn = "02", quantity = 0, price = 0, excd }) {
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    const exchOrder = quoteToOrderExch(excdQuote);
    const tr_id = this.mode === MODE.PAPER ? STOCK_TR.ORDER_RVSCNCL_PAPER : STOCK_TR.ORDER_RVSCNCL_REAL;
    const body = {
      CANO: this.env.accountNo,
      ACNT_PRDT_CD: this.env.accountProdCode,
      OVRS_EXCG_CD: exchOrder,
      PDNO: String(symbol).toUpperCase(),
      ORGN_ODNO: String(origOrderNo),
      RVSE_CNCL_DVSN_CD: rvseCnclDvsn,
      ORD_QTY: String(quantity || 0),
      OVRS_ORD_UNPR: String(price || 0),
      MGCO_APTM_ODNO: "",
      ORD_SVR_DVSN_CD: "0",
    };
    const r = await this.rest.post("/uapi/overseas-stock/v1/trading/order-rvsecncl", {
      tr_id, body, hash: true
    });
    return r;
  }

  // ============================== 잔고·예수금 ==============================

  /**
   * 보유 주식 + 평균단가 + 평가손익 (거래소 전체)
   * TR: TTTS3012R (실전) / VTTS3012R (모의)
   */
  async positions({ currency = "USD", excg = "NASD" } = {}) {
    const tr_id = this.mode === MODE.PAPER ? STOCK_TR.INQUIRE_BALANCE_PAPER : STOCK_TR.INQUIRE_BALANCE_REAL;
    // 전체 보기 위해 3거래소 순회
    const exchanges = excg === "ALL" ? ["NASD", "NYSE", "AMEX"] : [excg];
    const positions = [];
    for (const ex of exchanges) {
      let ctx_fk = "", ctx_nk = "", cont = "";
      do {
        const r = await this.rest.get("/uapi/overseas-stock/v1/trading/inquire-balance", {
          tr_id,
          params: {
            CANO: this.env.accountNo,
            ACNT_PRDT_CD: this.env.accountProdCode,
            OVRS_EXCG_CD: ex,
            TR_CRCY_CD: currency,
            CTX_AREA_FK200: ctx_fk, CTX_AREA_NK200: ctx_nk,
          },
          extra: cont ? { tr_cont: cont } : {},
        });
        // v7.3 (Claude #1 + GPT #1): FAIL-CLOSED on any paging/exchange
        // failure. Previously we silently broke and returned `{ok:true}`
        // with whatever we had, which allowed snapshot() to wipe the
        // in-memory position map on a transient KIS/Redis blip.
        if (!r.ok) {
          return {
            ok: false,
            partial: positions.length > 0,
            positions,
            failedExchange: ex,
            failedPage: { ctx_fk, ctx_nk, cont },
            status: r.status,
            raw: r,
          };
        }
        for (const it of (r.data.output1 || [])) {
          positions.push({
            symbol: (it.ovrs_pdno || "").trim(),
            quantity: Number(it.ovrs_cblc_qty || 0),
            orderableQty: Number(it.ord_psbl_qty || 0),
            avgPrice: Number(it.pchs_avg_pric || 0),
            nowPrice: Number(it.now_pric2 || 0),
            evaluation: Number(it.ovrs_stck_evlu_amt || 0),
            pnl: Number(it.frcr_evlu_pfls_amt || 0),
            pnlRate: Number(it.evlu_pfls_rt || 0),
            exchange: it.ovrs_excg_cd || ex,
            raw: it
          });
        }
        ctx_fk = r.data.ctx_area_fk200 || "";
        ctx_nk = r.data.ctx_area_nk200 || "";
        cont = r.headers?.tr_cont || "";
      } while (cont === "F" || cont === "M");
    }
    return { ok: true, positions };
  }

  /**
   * 체결기준 현재잔고 — 달러/원화 예수금·종목별 평가손익 종합
   * TR: CTRP6504R
   */
  async presentBalance({ currency = "02", wcrcFrcrDvsn = "02", natnCd = "840" } = {}) {
    const tr_id = this.mode === MODE.PAPER ? STOCK_TR.INQUIRE_PRESENT_BALANCE_PAPER : STOCK_TR.INQUIRE_PRESENT_BALANCE_REAL;
    const r = await this.rest.get("/uapi/overseas-stock/v1/trading/inquire-present-balance", {
      tr_id,
      params: {
        CANO: this.env.accountNo,
        ACNT_PRDT_CD: this.env.accountProdCode,
        WCRC_FRCR_DVSN_CD: wcrcFrcrDvsn,  // 01:원화, 02:외화
        NATN_CD: natnCd,                   // 840:미국
        TR_MKET_CD: "00",                  // 전체
        INQR_DVSN_CD: "00",                // 전체
      },
    });
    if (!r.ok) return { ok: false, raw: r.data };

    // output1: 보유 종목, output2: 통화별 예수금, output3: 종합
    const deposits = {};
    for (const d of (r.data.output2 || [])) {
      const cur = (d.crcy_cd || "").toUpperCase();
      if (cur) {
        deposits[cur] = {
          cash: Number(d.frcr_dncl_amt_2 || d.frcr_dncl_amt1 || 0),
          buyingPower: Number(d.frcr_buy_amt_smtl || d.frcr_drwg_psbl_amt_1 || 0),
          exchangeRate: Number(d.frst_bltn_exrt || 0),
        };
      }
    }
    const summary = r.data.output3 || {};
    return {
      ok: true,
      deposits,                               // { USD: { cash, buyingPower }, KRW: {...} }
      totalKrwEval: Number(summary.tot_evlu_pfls_amt || 0),
      totalAsset:   Number(summary.tot_asst_amt || 0),
      totalPnl:     Number(summary.evlu_pfls_amt_smtl || 0),
      positions: (r.data.output1 || []).map(it => ({
        symbol: (it.pdno || "").trim(),
        quantity: Number(it.cblc_qty13 || it.ord_psbl_qty1 || 0),
        avgPrice: Number(it.avg_unpr3 || 0),
        nowPrice: Number(it.ovrs_now_pric1 || 0),
        evaluation: Number(it.frcr_evlu_amt2 || 0),
        pnl: Number(it.evlu_pfls_amt2 || 0),
        exchange: it.ovrs_excg_cd || "",
      })),
      raw: r.data
    };
  }

  /**
   * 해외 통화별 증거금/예수금 (달러 전용 빠른 조회)
   * TR: TTTC2101R
   */
  async foreignMargin() {
    const r = await this.rest.get("/uapi/overseas-stock/v1/trading/foreign-margin", {
      tr_id: STOCK_TR.FOREIGN_MARGIN,
      params: {
        CANO: this.env.accountNo,
        ACNT_PRDT_CD: this.env.accountProdCode,
      },
    });
    if (!r.ok) return { ok: false, raw: r.data };
    // FMFIX_20260420: KIS TTTC2101R returns USD rows duplicated per natn_name; the
    // canonical US deposit row is natn_name === "미국". Field names are also
    // `frcr_dncl_amt1` and `frcr_gnrl_ord_psbl_amt` (the previous code matched
    // shorter variants that never appear in the live response, yielding 0).
    const rows = (r.data.output || []).filter(x => (x.crcy_cd || "").toUpperCase() === "USD");
    const usd = rows.find(x => (x.natn_name || "").includes("미국")) || rows[0];
    return {
      ok: true,
      usdCash:      Number(usd?.frcr_dncl_amt1         || usd?.frcr_dncl_amt      || 0),
      usdOrderable: Number(usd?.frcr_gnrl_ord_psbl_amt || usd?.frcr_drwg_psbl_amt || 0),
      raw: r.data
    };
  }

  /**
   * 매수가능금액/수량
   * TR: TTTS3007R (실전) / VTTS3007R
   */
  async psAmount({ symbol, excd, price }) {
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    const exchOrder = quoteToOrderExch(excdQuote);
    const tr_id = this.mode === MODE.PAPER ? STOCK_TR.INQUIRE_PSAMOUNT_PAPER : STOCK_TR.INQUIRE_PSAMOUNT_REAL;
    const r = await this.rest.get("/uapi/overseas-stock/v1/trading/inquire-psamount", {
      tr_id,
      params: {
        CANO: this.env.accountNo,
        ACNT_PRDT_CD: this.env.accountProdCode,
        OVRS_EXCG_CD: exchOrder,
        OVRS_ORD_UNPR: String(price || 0),
        ITEM_CD: String(symbol).toUpperCase(),
      },
    });
    if (!r.ok) return { ok: false, raw: r.data };
    const o = r.data.output || {};
    return {
      ok: true,
      maxBuyQty: Number(o.max_ord_psbl_qty || 0),
      maxBuyAmount: Number(o.ord_psbl_frcr_amt || 0),
      usdCash: Number(o.frcr_ord_psbl_amt1 || 0),
      raw: r.data
    };
  }

  // ============================== 체결·미체결 ==============================

  /** 미체결 주문 (TR: TTTS3018R) */
  // v7.3 (GPT): `excg` now accepts "ALL" to scan NASD+NYSE+AMEX, matching
  // positions(). Previously the default silently excluded NYSE/AMEX open
  // orders and force-sell sweepers only saw NASD tickets.
  async openOrders({ excg = "NASD", sortBy = "DS" } = {}) {
    const exchanges = excg === "ALL" ? ["NASD", "NYSE", "AMEX"] : [excg];
    const rows = [];
    for (const ex of exchanges) {
    let ctx_fk = "", ctx_nk = "", cont = "";
    do {
      const r = await this.rest.get("/uapi/overseas-stock/v1/trading/inquire-nccs", {
        tr_id: STOCK_TR.INQUIRE_NCCS,
        params: {
          CANO: this.env.accountNo, ACNT_PRDT_CD: this.env.accountProdCode,
          OVRS_EXCG_CD: ex, SORT_SQN: sortBy, CTX_AREA_FK200: ctx_fk, CTX_AREA_NK200: ctx_nk,
        },
        extra: cont ? { tr_cont: cont } : {},
      });
      // v7.3 (Claude #1 + GPT #1): FAIL-CLOSED on any paging/exchange
      // failure so downstream snapshot() never overwrites a known-good
      // openOrders map with a silently-partial result.
      if (!r.ok) {
        return {
          ok: false,
          partial: rows.length > 0,
          orders: rows,
          failedExchange: ex,
          failedPage: { ctx_fk, ctx_nk, cont },
          status: r.status,
          raw: r,
        };
      }
      for (const it of (r.data.output || [])) {
        rows.push({
          orderNo: it.odno,
          symbol: (it.pdno || "").trim(),
          side: it.sll_buy_dvsn_cd === "02" ? "BUY" : "SELL",
          quantity: Number(it.ft_ord_qty || it.ord_qty || 0),
          filledQty: Number(it.ft_ccld_qty || 0),
          remainQty: Number(it.nccs_qty || 0),
          price: Number(it.ft_ord_unpr3 || it.ovrs_ord_unpr || 0),
          orderTime: it.ord_tmd,
          exchange: it.ovrs_excg_cd,
          raw: it
        });
      }
      ctx_fk = r.data.ctx_area_fk200 || ""; ctx_nk = r.data.ctx_area_nk200 || "";
      cont = r.headers?.tr_cont || "";
    } while (cont === "F" || cont === "M");
    }
    return { ok: true, orders: rows };
  }

  /** 체결내역 (기간) TR: TTTS3035R */
  async filledOrders({ excg = "NASD", startDt, endDt, symbol = "", sortBy = "DS" }) {
    const tr_id = this.mode === MODE.PAPER ? STOCK_TR.INQUIRE_CCNL_PAPER : STOCK_TR.INQUIRE_CCNL_REAL;
    const rows = [];
    let ctx_fk = "", ctx_nk = "", cont = "";
    do {
      const r = await this.rest.get("/uapi/overseas-stock/v1/trading/inquire-ccnl", {
        tr_id,
        params: {
          CANO: this.env.accountNo, ACNT_PRDT_CD: this.env.accountProdCode,
          OVRS_EXCG_CD: excg, PDNO: symbol,
          ORD_STRT_DT: startDt, ORD_END_DT: endDt,
          SLL_BUY_DVSN: "00", CCLD_NCCS_DVSN: "00", SORT_SQN: sortBy,
          ORD_DT: "", ORD_GNO_BRNO: "", ODNO: "",
          CTX_AREA_FK200: ctx_fk, CTX_AREA_NK200: ctx_nk,
        },
        extra: cont ? { tr_cont: cont } : {},
      });
      // v7.3 (Claude #1 + GPT #1): FAIL-CLOSED — do not silently
      // collapse partial fills into a "success".
      if (!r.ok) {
        return {
          ok: false,
          partial: rows.length > 0,
          fills: rows,
          failedPage: { ctx_fk, ctx_nk, cont },
          status: r.status,
          raw: r,
        };
      }
      for (const it of (r.data.output || [])) rows.push(it);
      ctx_fk = r.data.ctx_area_fk200 || ""; ctx_nk = r.data.ctx_area_nk200 || "";
      cont = r.headers?.tr_cont || "";
    } while (cont === "F" || cont === "M");
    return { ok: true, fills: rows };
  }

  // ============================== 시세 ==============================

  /** 현재가 (TR: HHDFS00000300) */
  async price(symbol, excd) {
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    return this.rest.get("/uapi/overseas-price/v1/quotations/price", {
      tr_id: STOCK_QUOTE_TR.PRICE,
      params: { AUTH: "", EXCD: excdQuote, SYMB: String(symbol).toUpperCase() },
    });
  }

  /** 호가 10단계 (TR: HHDFS76200100) */
  // v7.3 (Claude #3): always return the rest_client wrapper. The previous
  // `r.data || r` made the return shape undefined — sometimes `{output, ...}`
  // and sometimes `{ok, status, data, ...}` — which silently broke the
  // MARKET order pricing path when the wrapper branch fired.
  async asking(symbol, excd) {
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    return this.rest.get("/uapi/overseas-price/v1/quotations/inquire-asking-price", {
      tr_id: STOCK_QUOTE_TR.ASKING_PRICE,
      params: { AUTH: "", EXCD: excdQuote, SYMB: String(symbol).toUpperCase() },
    });
  }

  /** 상세 현재가 (52주고가, EPS, 시총 포함) TR: HHDFS76200200 */
  async priceDetail(symbol, excd) {
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    return this.rest.get("/uapi/overseas-price/v1/quotations/price-detail", {
      tr_id: STOCK_QUOTE_TR.PRICE_DETAIL,
      params: { AUTH: "", EXCD: excdQuote, SYMB: String(symbol).toUpperCase() },
    });
  }

  /** 분봉 차트 TR: HHDFS76950200 */
  async minuteChart(symbol, { excd, nmin = 1, keyb = "", pwdata = "0" } = {}) {
    const excdQuote = excd || await resolveExchange(symbol, this.rest);
    return this.rest.get("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice", {
      tr_id: STOCK_QUOTE_TR.MINUTE_CHART_ITEM,
      params: {
        AUTH: "", EXCD: excdQuote, SYMB: String(symbol).toUpperCase(),
        NMIN: String(nmin), PINC: "1", NEXT: "", NREC: "120", FILL: "", KEYB: keyb,
      },
    });
  }

  /** 종목 기본정보 (섹터/시총/업종) TR: CTPF1702R */
  async searchInfo(symbol) {
    return this.rest.get("/uapi/overseas-price/v1/quotations/search-info", {
      tr_id: STOCK_QUOTE_TR.SEARCH_INFO,
      params: { PDNO: String(symbol).toUpperCase(), PRDT_TYPE_CD: "512" },
    });
  }
}
