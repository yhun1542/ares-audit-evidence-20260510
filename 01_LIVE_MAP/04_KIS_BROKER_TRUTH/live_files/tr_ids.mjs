// core/tr_ids.mjs — 공식 open-trading-api에서 검증된 TR_ID 상수 테이블
// 근거: examples_user/overseas_stock/overseas_stock_functions.py 및
//       examples_user/overseas_futureoption/overseas_futureoption_functions.py (grep 결과)

export const MODE = { REAL: "real", PAPER: "paper" };

/** 해외주식(미국) REST TR_ID */
export const STOCK_TR = {
  // 주문
  ORDER_BUY_REAL:   "TTTT1002U",   ORDER_BUY_PAPER:   "VTTT1002U",
  ORDER_SELL_REAL:  "TTTT1006U",   ORDER_SELL_PAPER:  "VTTT1006U",
  ORDER_RVSCNCL_REAL:"TTTT1004U",  ORDER_RVSCNCL_PAPER:"VTTT1004U",

  // 예약주문 (미국)
  ORDER_RESV_BUY_REAL:  "TTTT3014U", ORDER_RESV_BUY_PAPER:  "VTTT3014U",
  ORDER_RESV_SELL_REAL: "TTTT3016U", ORDER_RESV_SELL_PAPER: "VTTT3016U",
  ORDER_RESV_CNCL_REAL: "TTTT3017U", ORDER_RESV_CNCL_PAPER: "VTTT3017U",
  ORDER_RESV_LIST_REAL: "TTTT3039R",

  // 주간거래(Extended-hours)
  DAYTIME_BUY:   "TTTS6036U",
  DAYTIME_SELL:  "TTTS6037U",
  DAYTIME_RVSCNCL:"TTTS6038U",

  // 계좌·잔고
  INQUIRE_BALANCE_REAL:        "TTTS3012R", INQUIRE_BALANCE_PAPER:   "VTTS3012R",
  INQUIRE_PRESENT_BALANCE_REAL:"CTRP6504R", INQUIRE_PRESENT_BALANCE_PAPER:"VTRP6504R",
  INQUIRE_PAYMT_BALANCE:       "CTRP6010R",
  FOREIGN_MARGIN:              "TTTC2101R",     // 통화별 예수금(달러/HKD/JPY/CNY)
  INQUIRE_PSAMOUNT_REAL:       "TTTS3007R", INQUIRE_PSAMOUNT_PAPER:  "VTTS3007R",

  // 체결·미체결
  INQUIRE_NCCS:       "TTTS3018R",  // 미체결
  INQUIRE_CCNL_REAL:  "TTTS3035R", INQUIRE_CCNL_PAPER: "VTTS3035R",
  INQUIRE_PERIOD_PROFIT: "TTTS3039R",
  INQUIRE_PERIOD_TRANS:  "CTOS4001R",

  // 알고 주문
  ALGO_ORDNO:       "TTTS6058R",
  ALGO_INQUIRE_CCNL:"TTTS6059R",
};

/** 해외주식(미국) 시세 TR_ID */
export const STOCK_QUOTE_TR = {
  PRICE:                  "HHDFS00000300",  // 현재체결가
  PRICE_DETAIL:           "HHDFS76200200",
  ASKING_PRICE:           "HHDFS76200100",
  QUOT_CCNL:              "HHDFS76200300",
  DAILYPRICE:             "HHDFS76240000",
  DAILY_CHART:            "FHKST03030100",
  MINUTE_CHART_ITEM:      "HHDFS76950200",
  MINUTE_CHART_INDEX:     "FHKST03030200",
  SEARCH:                 "HHDFS76410000",
  SEARCH_INFO:            "CTPF1702R",
  INDUSTRY_PRICE:         "HHDFS76370100",
  INDUSTRY_THEME:         "HHDFS76370000",
  NEWS_TITLE:             "HHPSTH60100C1",
  BRKNEWS_TITLE:          "FHKST01011801",
  COUNTRIES_HOLIDAY:      "CTOS5011R",
  PERIOD_RIGHTS:          "CTRGT011R",
  RIGHTS_BY_ICE:          "HHDFS78330900",
  COLABLE_BY_COMPANY:     "CTLN4050R",   // 공매도 가능
};

/** 해외주식 순위 TR_ID */
export const STOCK_RANK_TR = {
  VOLUME:         "HHDFS76310010",
  TRADE_PBMN:     "HHDFS76320010",
  TRADE_GROWTH:   "HHDFS76330000",
  TRADE_TURNOVER: "HHDFS76340000",
  MARKET_CAP:     "HHDFS76350100",
  VOLUME_SURGE:   "HHDFS76270000",
  VOLUME_POWER:   "HHDFS76280000",
  PRICE_FLUCT:    "HHDFS76260000",
  NEW_HIGHLOW:    "HHDFS76300000",
  UPDOWN_RATE:    "HHDFS76290000",
};

/** 해외옵션(미국선물옵션 포함) TR_ID */
export const OPTION_TR = {
  ORDER:             "OTFM3001U",
  ORDER_MODIFY:      "OTFM3002U",
  ORDER_CANCEL:      "OTFM3003U",
  INQUIRE_UNPD:      "OTFM1412R",   // 미결제
  INQUIRE_DAILY_ORD: "OTFM3120R",
  INQUIRE_CCLD:      "OTFM3116R",   // 당일체결
  INQUIRE_DAILY_CCLD:"OTFM3122R",
  INQUIRE_PERIOD_CCLD:"OTFM3118R",
  INQUIRE_DEPOSIT:   "OTFM1411R",   // 달러 예수금/증거금
  MARGIN_DETAIL:     "OTFM3115R",
  INQUIRE_PSAMOUNT:  "OTFM3304R",
  INQUIRE_PERIOD_TRANS:"OTFM3114R",
  MARKET_TIME:       "OTFM2229R",
};

/** 해외옵션 시세 TR_ID */
export const OPTION_QUOTE_TR = {
  OPT_PRICE:             "HHDFO55010000",
  OPT_DETAIL:            "HHDFO55010100",
  OPT_ASKING_PRICE:      "HHDFO86000000",
  OPT_TICK_CCNL:         "HHDFO55020200",
  OPT_MINUTE_CHART:      "HHDFO55020100",
  OPT_DAILY_CCNL:        "HHDFO55020100",
  OPT_WEEKLY_CCNL:       "HHDFO55020000",
  OPT_MONTHLY_CCNL:      "HHDFO55020300",
  SEARCH_OPT_DETAIL:     "HHDFO55200000",
};

/** WebSocket TR_ID별 필드 수 (다건 프레임 분할용) — v7.1 */
export const WS_FIELD_COUNT = {
  HDFSCNT0: 25,  // 미국 실시간 체결(지연) - 공식 26필드
  HDFSASP0: 16,  // 미국 1호가
  HDFSASP1: 16,
  HDFFF020: 30,  // 해외선물옵션 체결
  HDFFF010: 24,  // 해외선물옵션 호가
  H0GSCNI0: 25,  // 내 주식 체결통보
  H0GSCNI9: 25,
  HDFFF1C0: 24,  // 옵션 주문 접수
  HDFFF2C0: 26,  // 옵션 체결 통보
};

/** WebSocket 실시간 TR_ID */
export const WS_TR = {
  STOCK_ASKING:          "HDFSASP0",   // 미국 1호가 무료, 아시아 유료
  STOCK_ASKING_DELAYED:  "HDFSASP1",
  STOCK_CCNL:            "HDFSCNT0",   // 미국 실시간/지연 체결
  STOCK_FILL_NOTICE_REAL:"H0GSCNI0",   // 내 체결 통보 (실전)
  STOCK_FILL_NOTICE_PAPER:"H0GSCNI9",

  OPT_ASKING:            "HDFFF010",   // 해외선물옵션 실시간 호가
  OPT_CCNL:              "HDFFF020",   // 해외선물옵션 실시간 체결
  OPT_ORDER_NOTICE:      "HDFFF1C0",   // 내 주문 접수
  OPT_FILL_NOTICE:       "HDFFF2C0",   // 내 체결 통보
};

/** 거래소/심볼 prefix 매핑 */
export const EXCH = {
  // 주문 API 용 (OVRS_EXCG_CD) - 4글자
  ORDER_NASD: "NASD",
  ORDER_NYSE: "NYSE",
  ORDER_AMEX: "AMEX",
  // 시세 API 용 (EXCD) - 3글자
  QUOTE_NAS: "NAS",
  QUOTE_NYS: "NYS",
  QUOTE_AMS: "AMS",
  // WebSocket 심볼 prefix
  WS_NASD_REGULAR:    "DNAS",
  WS_NYSE_REGULAR:    "DNYS",
  WS_AMEX_REGULAR:    "DAMS",
  WS_NASD_EXTENDED:   "RBAQ",   // 미국 주간거래
  WS_NYSE_EXTENDED:   "RBAY",
  WS_AMEX_EXTENDED:   "RBAM",
};

/** ORD_DVSN 매핑 (미국 해외주식) */
export const ORD_DVSN = {
  LIMIT:  "00",
  MOO:    "31",   // Market On Open
  LOO:    "32",   // Limit On Open
  MOC:    "33",   // Market On Close
  LOC:    "34",   // Limit On Close
};

/** 옵션 주문 PRIC_DVSN_CD */
export const OPT_PRIC_DVSN = {
  LIMIT:      "1",
  MARKET:     "2",
  STOP:       "3",
  STOP_LIMIT: "4",
};

/** 옵션 체결조건코드 CCLD_CNDT_CD */
export const OPT_CCLD_CNDT = {
  DAY:  "6",   // EOD (일반 지정가)
  GTD:  "5",
  MKT:  "2",   // 시장가
};

/** KIS API 공통 에러코드 */
export const KIS_ERR = {
  HASHKEY_INVALID: "EGW00121",
  RATE_LIMIT:      "EGW00201",
  AUTH_FAIL:       "EGW00001",
  TR_ID_DENIED:    "OPSP0002",
  SYMBOL_BAD:      "OPSP0007",
  EXCH_BAD:        "APBK0656",
};

/**
 * 실전/모의 TR_ID 자동 선택
 */
export function pickTr(baseReal, basePaper, mode) {
  return mode === MODE.PAPER ? basePaper : baseReal;
}

/**
 * 미국 매수/매도 주문 TR_ID 선택
 */
/**
 * 전체 TR_ID 집합 (CI grep 검증용) - v7.1
 * 빌드 시 이 집합의 모든 값이 공식 open-trading-api 저장소 소스 코드에 존재해야 통과.
 */
export const ALL_TR_IDS = Object.freeze([
  ...Object.values(STOCK_TR),
  ...Object.values(STOCK_QUOTE_TR),
  ...Object.values(STOCK_RANK_TR),
  ...Object.values(OPTION_TR),
  ...Object.values(OPTION_QUOTE_TR),
  ...Object.values(WS_TR),
]);

export function stockOrderTr(side, mode) {
  const isPaper = mode === MODE.PAPER;
  if (side === "BUY")  return isPaper ? STOCK_TR.ORDER_BUY_PAPER  : STOCK_TR.ORDER_BUY_REAL;
  if (side === "SELL") return isPaper ? STOCK_TR.ORDER_SELL_PAPER : STOCK_TR.ORDER_SELL_REAL;
  throw new Error(`Invalid side: ${side}`);
}
