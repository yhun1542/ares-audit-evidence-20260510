// rest_client_audit_v1.mjs
// ARES KIS US AUTOPILOT V9 REST Audit API Scaffold
//
// This module provides the missing KIS API functions identified during the
// 2026-05-07 ARES vs KIS official GitHub gap analysis.
//
// THESE FUNCTIONS ARE OBSERVATION-ONLY.
// Do NOT wire them directly into the production order sizing pipeline.
// Use them for daily reconciliation and broker-truth recovery first.

/**
 * 1. 결제기준현재잔고 (CTRP6010R)
 * 목적: 실시간 잔고와 T+2 결제 잔고 차이 감시
 * @param {KisRestClient} client 
 * @param {Object} params - { CANO, ACNT_PRDT_CD, OVRS_EXCG_CD, ... }
 */
export async function inquirePaymentStandardBalance(client, params = {}) {
  return client.get("/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance", {
    tr_id: "CTRP6010R",
    params: {
      CANO: params.CANO || "",
      ACNT_PRDT_CD: params.ACNT_PRDT_CD || "",
      OVRS_EXCG_CD: params.OVRS_EXCG_CD || "NASD",
      NATN_CD: params.NATN_CD || "840", // US
      TR_CRCY_CD: params.TR_CRCY_CD || "USD",
      INQR_DVSN: params.INQR_DVSN || "00", // 00: 전체
      WCRC_FRCR_DVSN_CD: params.WCRC_FRCR_DVSN_CD || "02", // 02: 외화
    },
    extra: {
      description: "결제기준현재잔고조회"
    }
  });
}

/**
 * 2. 기간별 거래내역 (CTOS4001R)
 * 목적: broker truth / audit ledger 보강
 * @param {KisRestClient} client 
 * @param {Object} params - { CANO, ACNT_PRDT_CD, PDNO, INQR_STRT_DT, INQR_END_DT, ... }
 */
export async function inquirePeriodTradeHistory(client, params = {}) {
  return client.get("/uapi/overseas-stock/v1/trading/inquire-period-trans", {
    tr_id: "CTOS4001R",
    params: {
      CANO: params.CANO || "",
      ACNT_PRDT_CD: params.ACNT_PRDT_CD || "",
      OVRS_EXCG_CD: params.OVRS_EXCG_CD || "NASD",
      PDNO: params.PDNO || "",
      INQR_STRT_DT: params.INQR_STRT_DT || "", // YYYYMMDD
      INQR_END_DT: params.INQR_END_DT || "",   // YYYYMMDD
      SLL_BUY_DVSN_CD: params.SLL_BUY_DVSN_CD || "00", // 00: 전체
      CCLD_NCCS_DVSN: params.CCLD_NCCS_DVSN || "00",   // 00: 전체
      CTX_AREA_FK200: params.CTX_AREA_FK200 || "",
      CTX_AREA_NK200: params.CTX_AREA_NK200 || "",
    },
    extra: {
      description: "해외주식 기간별거래내역조회"
    }
  });
}

/**
 * 3. 정정취소 가능내역 조회 (TTTS3014R)
 * 목적: cancel/replace 전 안전성 강화 (pre-check)
 * @param {KisRestClient} client 
 * @param {Object} params - { CANO, ACNT_PRDT_CD, OVRS_EXCG_CD, PDNO, ... }
 */
export async function inquireCancelReplaceableOrders(client, params = {}) {
  return client.get("/uapi/overseas-stock/v1/trading/inquire-nccs", {
    tr_id: "TTTS3014R",
    params: {
      CANO: params.CANO || "",
      ACNT_PRDT_CD: params.ACNT_PRDT_CD || "",
      OVRS_EXCG_CD: params.OVRS_EXCG_CD || "NASD",
      PDNO: params.PDNO || "",
      SORT_SQN: params.SORT_SQN || "DS", // DS: 내림차순
      CTX_AREA_FK200: params.CTX_AREA_FK200 || "",
      CTX_AREA_NK200: params.CTX_AREA_NK200 || "",
    },
    extra: {
      description: "해외주식 미체결내역조회(정정취소가능)"
    }
  });
}

/**
 * 4. 토큰 폐기 (revokeP)
 * 목적: 토큰 재발급 시 기존 토큰 잔류 리스크 축소
 * @param {KisRestClient} client 
 * @param {Object} params - { appkey, appsecret, token }
 */
export async function revokeToken(client, params = {}) {
  // token 폐기는 POST 요청이지만 TR_ID가 없음
  return client.post("/oauth2/revokeP", {
    tr_id: "REVOKE", // 더미
    hash: false,     // hashkey 불필요
    body: {
      appkey: params.appkey,
      appsecret: params.appsecret,
      token: params.token
    },
    extra: {
      description: "토큰 폐기"
    }
  });
}
