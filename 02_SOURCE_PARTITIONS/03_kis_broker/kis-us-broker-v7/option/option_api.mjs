// option/option_api.mjs — 미국옵션(해외선물옵션) REST API 래퍼 (ARES 신규)
// 종목코드 규칙: OVRS_FUTR_FX_PDNO
//   예) 1AALN25 C10.0    — AAPL 2025-07 C10.00 콜
//   예) 6EZ25 P1.05      — CME Euro FX 선물 12월 P1.05
// 시세는 `sCalcDesz`(계산 소수점)에 따라 환산 필수 (종목마스터 로드 후 처리)

import { OPTION_TR, OPTION_QUOTE_TR, OPT_PRIC_DVSN, OPT_CCLD_CNDT } from "../core/tr_ids.mjs";

export class KisOptionApi {
  constructor({ env, rest, logger = console }) {
    this.env = env;
    this.rest = rest;
    this.logger = logger;
    // 옵션 전용 계좌상품코드(보통 "08") — 환경변수로 오버라이드 가능
    this.optProdCd = process.env.KIS_OPTION_PROD_CODE || "08";
  }

  // ============================== 주문 ==============================

  /**
   * 미국옵션 주문
   * @param {Object} req
   *   pdno          : OVRS_FUTR_FX_PDNO (예: "1AALN25 C10.0")
   *   side          : "BUY" | "SELL"
   *   quantity      : 계약수 (integer)
   *   price         : 지정가
   *   stopPrice     : STOP 주문 시
   *   priceType     : "LIMIT" | "MARKET" | "STOP" | "STOP_LIMIT"
   *   timeInForce   : "DAY" | "GTD" | "MKT" (기본 DAY)
   *   gtdDate       : GTD 만기 일자 (YYYYMMDD)
   *   hedgeYn       : "Y" | "N" (기본 "N")
   */
  async placeOrder(req) {
    const {
      pdno, side, quantity, price = 0, stopPrice = 0,
      priceType = "LIMIT", timeInForce = "DAY", gtdDate = "", hedgeYn = "N",
      clientOrderId, idempotencyKey
    } = req;
    // v7.3 C4: see stock_api.mjs for rationale. Every options order gets a
    // client-supplied or auto-generated reqId so UNKNOWN_SUBMISSION outcomes
    // can be reconciled without risking duplicate contracts.
    const reqId = String(idempotencyKey || clientOrderId ||
      (globalThis.crypto?.randomUUID?.() || ("opt-" + Date.now() + "-" + Math.random().toString(16).slice(2))));
    if (!pdno || !side || !quantity) throw new Error("옵션 주문: pdno/side/quantity 필수");
    // v7.2 round-2: hardened numeric validation (Grok/Claude round-1 finding).
    const qtyNum = Number(quantity);
    if (!Number.isFinite(qtyNum) || qtyNum <= 0 || Math.floor(qtyNum) !== qtyNum) {
      throw new Error(`옵션 주문: quantity must be positive integer, got=${quantity}`);
    }
    if (side !== "BUY" && side !== "SELL") {
      throw new Error(`옵션 주문: side must be BUY|SELL, got=${side}`);
    }
    // v7.2 round-4 (Grok): explicitly enumerate accepted priceTypes so an
    // unknown value is rejected early. MARKET and STOP accept price=0 (ignored
    // downstream); LIMIT/STOP_LIMIT require a positive limit price.
    const VALID_PRICE_TYPES = new Set(["LIMIT", "MARKET", "STOP", "STOP_LIMIT"]);
    if (!VALID_PRICE_TYPES.has(priceType)) {
      throw new Error(`옵션 주문: priceType must be one of ${[...VALID_PRICE_TYPES].join("|")}, got=${priceType}`);
    }
    if (priceType === "LIMIT" || priceType === "STOP_LIMIT") {
      const pn = Number(price);
      if (!Number.isFinite(pn) || pn <= 0) {
        throw new Error(`옵션 주문: ${priceType} requires positive price, got=${price}`);
      }
    }
    if (priceType === "STOP" || priceType === "STOP_LIMIT") {
      const sp = Number(stopPrice);
      if (!Number.isFinite(sp) || sp <= 0) {
        throw new Error(`옵션 주문: ${priceType} requires positive stopPrice, got=${stopPrice}`);
      }
    }
    if (gtdDate && !/^\d{8}$/.test(String(gtdDate))) {
      throw new Error(`옵션 주문: gtdDate must be YYYYMMDD, got=${gtdDate}`);
    }

    const pricDvsn = OPT_PRIC_DVSN[priceType] || OPT_PRIC_DVSN.LIMIT;
    const ccldCndt = OPT_CCLD_CNDT[timeInForce] || OPT_CCLD_CNDT.DAY;

    const body = {
      CANO: this.env.accountNo,
      ACNT_PRDT_CD: this.optProdCd,
      OVRS_FUTR_FX_PDNO: pdno,
      SLL_BUY_DVSN_CD: side === "BUY" ? "02" : "01",
      FM_LQD_USTL_CCLD_DT: "",
      FM_LQD_USTL_CCNO: "",
      PRIC_DVSN_CD: pricDvsn,
      FM_LIMIT_ORD_PRIC: priceType === "MARKET" ? "0" : String(price),
      FM_STOP_ORD_PRIC: stopPrice ? String(stopPrice) : "0",
      FM_ORD_QTY: String(quantity),
      FM_LQD_LMT_ORD_PRIC: "0",
      FM_LQD_STOP_ORD_PRIC: "0",
      CCLD_CNDT_CD: ccldCndt,
      CPLX_ORD_DVSN_CD: "0",
      ECIS_RSVN_ORD_YN: gtdDate ? "Y" : "N",
      FM_HDGE_ORD_SCRN_YN: hedgeYn,
      // GTD 만기일 필드는 KIS 스펙상 FM_RSVN_ORD_END_DT 를 쓰나 공식 샘플 기준 아래 키 사용
      OVRS_RSVN_ORD_END_DT: gtdDate,
    };

    this.logger.info && this.logger.info("KIS_OPT_ORDER_SEND", {
      pdno, side, qty: quantity, priceType, price, tif: timeInForce
    });

    const r = await this.rest.post("/uapi/overseas-futureoption/v1/trading/order", {
      tr_id: OPTION_TR.ORDER, body, hash: true, idempotencyKey: reqId
    });
    if (!r.ok) {
      // v7.3 C4: separate AMBIGUOUS from REJECTED. Options reconciliation
      // queries both open (OTFM3116R) and filled (OTFM3117R) by
      // clientOrderId=reqId within RECONCILE_WINDOW_MS before deciding.
      if (r.ambiguous) {
        this.logger.error && this.logger.error("KIS_OPT_ORDER_AMBIGUOUS", {
          pdno, side, qty: quantity, reqId,
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
          reconcile: {
            byClientOrderId: reqId,
            byAccountPdnoSide: { pdno, side, quantity },
          },
        };
      }
      return { ok: false, status: "REJECTED", ambiguous: false,
        brokerOrderId: null, clientOrderId: reqId, reqId,
        msg_cd: r.data?.msg_cd, message: r.data?.msg1, raw: r.data };
    }
    const out = r.data.output || {};
    return {
      ok: true, status: "SUBMITTED", ambiguous: false,
      brokerOrderId: out.ODNO, clientOrderId: reqId, reqId,
      orderTime: out.ORD_TMD, raw: r.data
    };
  }

  async cancelOrder({ pdno, origOrderNo }) {
    const body = {
      CANO: this.env.accountNo,
      ACNT_PRDT_CD: this.optProdCd,
      OVRS_FUTR_FX_PDNO: pdno,
      ORGN_ODNO: String(origOrderNo),
      FM_ORD_QTY: "0",
    };
    return this.rest.post("/uapi/overseas-futureoption/v1/trading/order-rvsecncl", {
      tr_id: OPTION_TR.ORDER_CANCEL, body, hash: true
    });
  }

  /**
   * v7.1 신규: 옵션 주문 정정(modify) — OTFM3002U
   */
  async modifyOrder({ pdno, origOrderNo, quantity, price = 0, stopPrice = 0, priceType = "LIMIT" }) {
    const pricDvsn = OPT_PRIC_DVSN[priceType] || OPT_PRIC_DVSN.LIMIT;
    const body = {
      CANO: this.env.accountNo,
      ACNT_PRDT_CD: this.optProdCd,
      OVRS_FUTR_FX_PDNO: pdno,
      ORGN_ODNO: String(origOrderNo),
      PRIC_DVSN_CD: pricDvsn,
      FM_LIMIT_ORD_PRIC: priceType === "MARKET" ? "0" : String(price),
      FM_STOP_ORD_PRIC: stopPrice ? String(stopPrice) : "0",
      FM_ORD_QTY: String(quantity),
    };
    return this.rest.post("/uapi/overseas-futureoption/v1/trading/order-rvsecncl", {
      tr_id: OPTION_TR.ORDER_MODIFY, body, hash: true
    });
  }

  // ============================== 잔고·증거금 ==============================

  /**
   * 달러 예수금/증거금 현황  TR: OTFM1411R
   * 출력 핵심: frcr_dncl_amt(예수금), mgna(증거금), ord_psbl_cash(주문가능현금)
   */
  async deposit({ currency = "USD" } = {}) {
    const r = await this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-deposit", {
      tr_id: OPTION_TR.INQUIRE_DEPOSIT,
      params: {
        CANO: this.env.accountNo,
        ACNT_PRDT_CD: this.optProdCd,
        CRCY_CD: currency,
        INQR_DT: yyyymmdd(),
      },
    });
    if (!r.ok) return { ok: false, raw: r.data };
    const o = r.data.output2 || r.data.output || {};
    return {
      ok: true,
      currency,
      cashDeposit:    Number(o.frcr_dncl_amt || o.dncl_amt || 0),
      usedMargin:     Number(o.tot_mgna || o.mgna || 0),
      orderableCash:  Number(o.ord_psbl_cash_amt || o.ord_psbl_cash || 0),
      withdrawable:   Number(o.frcr_drwg_psbl_amt || 0),
      raw: r.data
    };
  }

  /** 증거금 상세 (초기/유지) TR: OTFM3115R */
  async marginDetail() {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/margin-detail", {
      tr_id: OPTION_TR.MARGIN_DETAIL,
      params: { CANO: this.env.accountNo, ACNT_PRDT_CD: this.optProdCd },
    });
  }

  /** 주문가능 수량/금액 TR: OTFM3304R */
  async psAmount({ pdno, price = 0, side = "BUY" }) {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-psamount", {
      tr_id: OPTION_TR.INQUIRE_PSAMOUNT,
      params: {
        CANO: this.env.accountNo,
        ACNT_PRDT_CD: this.optProdCd,
        OVRS_FUTR_FX_PDNO: pdno,
        FM_ORD_PRIC: String(price),
        SLL_BUY_DVSN_CD: side === "BUY" ? "02" : "01",
      },
    });
  }

  /** 미결제(포지션) TR: OTFM1412R */
  async openPositions({ currency = "USD" } = {}) {
    const r = await this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-unpd", {
      tr_id: OPTION_TR.INQUIRE_UNPD,
      params: {
        CANO: this.env.accountNo,
        ACNT_PRDT_CD: this.optProdCd,
        CRCY_CD: currency,
      },
    });
    if (!r.ok) return { ok: false, raw: r.data };
    const positions = (r.data.output1 || r.data.output || []).map(it => ({
      pdno: (it.ovrs_futr_fx_pdno || it.pdno || "").trim(),
      quantity: Number(it.cblc_qty13 || it.lqd_psbl_qty || it.cblc_qty || 0),
      side: it.sll_buy_dvsn_cd === "01" ? "SELL" : "BUY",
      avgPrice: Number(it.avg_unpr || it.pchs_avg_pric || 0),
      currentPrice: Number(it.now_pric2 || it.ovrs_now_pric || 0),
      evaluation: Number(it.frcr_evlu_amt2 || it.evlu_amt || 0),
      pnl: Number(it.evlu_pfls_amt || 0),
      raw: it
    }));
    return { ok: true, positions, raw: r.data };
  }

  /** 당일 체결 TR: OTFM3116R */
  async todayFills() {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-ccld", {
      tr_id: OPTION_TR.INQUIRE_CCLD,
      params: { CANO: this.env.accountNo, ACNT_PRDT_CD: this.optProdCd },
    });
  }

  /** 당일 주문 TR: OTFM3120R (미체결 포함) */
  async todayOrders() {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-daily-order", {
      tr_id: OPTION_TR.INQUIRE_DAILY_ORD,
      params: {
        CANO: this.env.accountNo, ACNT_PRDT_CD: this.optProdCd,
        INQR_STRT_DT: yyyymmdd(), INQR_END_DT: yyyymmdd(),
      },
    });
  }

  /** v7.1 신규: 기간체결 TR: OTFM3118R */
  async periodFills({ startDate, endDate }) {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-period-ccld", {
      tr_id: OPTION_TR.INQUIRE_PERIOD_CCLD,
      params: {
        CANO: this.env.accountNo, ACNT_PRDT_CD: this.optProdCd,
        INQR_STRT_DT: startDate, INQR_END_DT: endDate,
      },
    });
  }

  /** v7.1 신규: 일별 체결 TR: OTFM3122R */
  async dailyFills({ date }) {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-daily-ccld", {
      tr_id: OPTION_TR.INQUIRE_DAILY_CCLD,
      params: {
        CANO: this.env.accountNo, ACNT_PRDT_CD: this.optProdCd,
        INQR_DT: date || yyyymmdd(),
      },
    });
  }

  /** v7.1 신규: 기간산열 TR: OTFM3114R */
  async periodTrans({ startDate, endDate }) {
    return this.rest.get("/uapi/overseas-futureoption/v1/trading/inquire-period-trans", {
      tr_id: OPTION_TR.INQUIRE_PERIOD_TRANS,
      params: {
        CANO: this.env.accountNo, ACNT_PRDT_CD: this.optProdCd,
        INQR_STRT_DT: startDate, INQR_END_DT: endDate,
      },
    });
  }

  /** v7.1 신규: 상품별 장시간 TR: OTFM2229R */
  async marketTime({ pdno }) {
    return this.rest.get("/uapi/overseas-futureoption/v1/quotations/market-time", {
      tr_id: OPTION_TR.MARKET_TIME,
      params: { SRS_CD: pdno },
    });
  }

  // ============================== 시세 ==============================

  /** 옵션 현재가 TR: HHDFO55010000 */
  async price(pdno, { srsCd } = {}) {
    return this.rest.get("/uapi/overseas-futureoption/v1/quotations/opt-price", {
      tr_id: OPTION_QUOTE_TR.OPT_PRICE,
      params: { SRS_CD: srsCd || pdno },
    });
  }

  /** 옵션 호가 10단계 TR: HHDFO86000000 */
  async asking(pdno, { srsCd } = {}) {
    return this.rest.get("/uapi/overseas-futureoption/v1/quotations/opt-asking-price", {
      tr_id: OPTION_QUOTE_TR.OPT_ASKING_PRICE,
      params: { SRS_CD: srsCd || pdno },
    });
  }

  /** 옵션 상세 TR: HHDFO55010100 */
  async detail(pdno) {
    return this.rest.get("/uapi/overseas-futureoption/v1/quotations/opt-detail", {
      tr_id: OPTION_QUOTE_TR.OPT_DETAIL,
      params: { SRS_CD: pdno },
    });
  }

  /** 옵션 상품 기본정보 TR: HHDFO55200000 */
  async productInfo(pdno) {
    return this.rest.get("/uapi/overseas-futureoption/v1/quotations/search-opt-detail", {
      tr_id: OPTION_QUOTE_TR.SEARCH_OPT_DETAIL,
      params: { SRS_CD: pdno },
    });
  }
}

function yyyymmdd(d = new Date()) {
  const z = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}`;
}
