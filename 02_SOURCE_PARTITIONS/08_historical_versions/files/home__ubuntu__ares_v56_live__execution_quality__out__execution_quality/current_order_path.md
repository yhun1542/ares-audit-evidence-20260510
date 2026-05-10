# Current Order Path

```
order-intent-executor.mjs:4: * order_intent 스트림을 읽어서 KIS API로 실제 주문 실행
order-intent-executor.mjs:8: *  - retry_start/retry_success/retry_giveup: KIS API 호출 (토큰 발급 + 주문 실행)
order-intent-executor.mjs:11: *  - dep_call_start/dep_call_ok/dep_call_fail: KIS 토큰 발급 + 주문 API 실제 호출 증거
order-intent-executor.mjs:17: *  3. KIS 토큰 캐시 키 정합화 (JSON + legacy 동시 호환)
order-intent-executor.mjs:25: // [PATCH-P0] HTTPS Keep-Alive agent for KIS API latency reduction
order-intent-executor.mjs:62: // R14 FIX: KIS API rate limiting (max 5 calls per second)
order-intent-executor.mjs:544: throw new Error(`[STARTUP] KIS_ACCOUNT_NO is invalid: ${v || "<empty>"}`);
order-intent-executor.mjs:560: appKey: process.env.KIS_APP_KEY,
order-intent-executor.mjs:561: appSecret: process.env.KIS_APP_SECRET,
order-intent-executor.mjs:562: accountNo: process.env.KIS_ACCOUNT_NO,
order-intent-executor.mjs:575: requireEnv("KIS_APP_KEY", process.env.KIS_APP_KEY);
order-intent-executor.mjs:576: requireEnv("KIS_APP_SECRET", process.env.KIS_APP_SECRET);
order-intent-executor.mjs:577: requireEnv("KIS_ACCOUNT_NO", process.env.KIS_ACCOUNT_NO);
order-intent-executor.mjs:578: validateKisAccountNo(process.env.KIS_ACCOUNT_NO);
order-intent-executor.mjs:1035: // KIS Token
order-intent-executor.mjs:1039: // Canonical KIS token loader.
order-intent-executor.mjs:1111: log("INFO", "Requesting new KIS access token");
order-intent-executor.mjs:1120: msg: "requesting new KIS access token",
order-intent-executor.mjs:1138: // === AOPS_EVT v2: depCall 래퍼 — KIS 토큰 발급 HTTP 호출 (증명 기반) ===
order-intent-executor.mjs:1144: dep: { name: "KIS", type: "http", method: "POST", path: "/oauth2/tokenP" }
order-intent-executor.mjs:1155: msg: `KIS token request failed: ${e.message}`,
order-intent-executor.mjs:1168: msg: "KIS token response missing access_token",
order-intent-executor.mjs:1192: log("INFO", "KIS access token obtained", { expiresIn: result.expires_in });
order-intent-executor.mjs:1200: msg: "KIS access token obtained",
order-intent-executor.mjs:1207: // KIS Order Execution (AOPS_EVT instrumented)
order-intent-executor.mjs:1208: async function executeKISOrder(intent) {
order-intent-executor.mjs:1214: // KIS 해외주식(미국) 시장가(01) 미지원 → 항상 지정가(00) + aggressive pricing
order-intent-executor.mjs:1459: // === AOPS_EVT: retry_start — KIS 주문 API 호출 ===
order-intent-executor.mjs:1470: msg: `KIS order ${side} ${intent.symbol} qty=${qty} px=${px}`,
order-intent-executor.mjs:1490: // === AOPS_EVT v2: depCall 래퍼 — KIS 주문 API HTTP 호출 (증명 기반) ===
order-intent-executor.mjs:1497: dep: { name: "KIS", type: "http", method: "POST", path: "/uapi/overseas-stock/v1/trading/order", symbol: intent.symbol, side, qty }
order-intent-executor.mjs:1501: // HARDENING: Record KIS API latency
order-intent-executor.mjs:1519: log("WARN", "KIS_API_SLOW", { avg_latency_ms: newAvg, last_latency_ms: _kisApiLatencyMs, symbol: intent.symbol });
order-intent-executor.mjs:1534: // === AOPS_EVT v1: retry_giveup — KIS 주문 네트워크 실패 ===
order-intent-executor.mjs:1540: msg: `KIS order API failed: ${e.message}`,
order-intent-executor.mjs:1546: // === AOPS_EVT v1: retry_success — KIS 주문 API 응답 수신 ===
order-intent-executor.mjs:1552: msg: `KIS order API responded: rt_cd=${orderResult.rt_cd}`,
order-intent-executor.mjs:1741: await routeToDLQ(msgId, intent, "KIS_POS_DRIFT_HALT");
order-intent-executor.mjs:1879: // Stale order detected → auto-cancel via KIS API, then allow new order
order-intent-executor.mjs:1926: await new Promise(r => setTimeout(r, 300)); // brief delay for KIS processing
order-intent-executor.mjs:2174: const MAX_SLIP_BPS   = parseFloat(process.env.KIS_MAX_SLIP_BPS     || "50");
order-intent-executor.mjs:2231: // Rule 3: Clamp limit price to KIS last band
order-intent-executor.mjs:2253: const result = await executeKISOrder(intent);
order-intent-executor.mjs:2320: // [MASTER-PATCH-3B] KIS 치명적 에러 시 Auto-Blocklist (TTL 4시간) + 현금 롤백
order-intent-executor.mjs:2397: // Timeout 시 현금 롤백 안 함 — KIS에 주문이 접수됐을 수 있음
live-trading-kis.mjs:63: throw new Error(`[STARTUP] KIS_ACCOUNT_NO is invalid: ${v || "<empty>"}`);
live-trading-kis.mjs:909: KIS_BASE_URL: process.env.KIS_BASE_URL || "https://openapi.koreainvestment.com:9443",
live-trading-kis.mjs:910: KIS_APP_KEY: process.env.KIS_APP_KEY,
live-trading-kis.mjs:911: KIS_APP_SECRET: process.env.KIS_APP_SECRET,
live-trading-kis.mjs:912: KIS_ACCOUNT_NO: process.env.KIS_ACCOUNT_NO,
live-trading-kis.mjs:913: KIS_ACCOUNT_PROD_CD: process.env.KIS_ACCOUNT_PROD_CD || "01",
live-trading-kis.mjs:929: const url = new URL(CONFIG.KIS_BASE_URL + "/uapi/overseas-stock/v1/trading/inquire-psamount");
live-trading-kis.mjs:930: url.searchParams.append("CANO", CONFIG.KIS_ACCOUNT_NO);
live-trading-kis.mjs:931: url.searchParams.append("ACNT_PRDT_CD", CONFIG.KIS_ACCOUNT_PROD_CD);
live-trading-kis.mjs:940: "appkey": CONFIG.KIS_APP_KEY,
live-trading-kis.mjs:941: "appsecret": CONFIG.KIS_APP_SECRET,
live-trading-kis.mjs:956: log("INFO", "Budget USD from KIS API v1.2.0", {
live-trading-kis.mjs:971: log("WARN", "KIS API budget query failed", { rt_cd: data.rt_cd, msg: data.msg1 });
live-trading-kis.mjs:2863: log("INFO", `=== KIS Live Trading Daemon v${VERSION} (Champion+Scheduler+Governor+CrashGuard+EnhancedPI+KPI_GAP+v54Modules) Starting ===`);
algo-maker-pegger.mjs:11: *  - KIS order/cancel/inquire endpoints configurable via env
algo-maker-pegger.mjs:37: *   REDIS_URL, KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, KIS_ACCOUNT_PROD_CD
algo-maker-pegger.mjs:55: pegStream: env.PEG_REQUEST_STREAM || "stream:peg_requests",
algo-maker-pegger.mjs:56: fillsStream: env.FILLS_STREAM || "stream:fills",
algo-maker-pegger.mjs:62: // KIS API
algo-maker-pegger.mjs:63: kisBaseUrl: env.KIS_BASE_URL || "https://openapi.koreainvestment.com:9443",
algo-maker-pegger.mjs:64: kisAppKey: env.KIS_APP_KEY || "",
algo-maker-pegger.mjs:65: kisAppSecret: env.KIS_APP_SECRET || "",
algo-maker-pegger.mjs:66: kisAccountNo: env.KIS_ACCOUNT_NO || "",
algo-maker-pegger.mjs:67: kisAccountProdCd: env.KIS_ACCOUNT_PROD_CD || "01",
algo-maker-pegger.mjs:69: kisOrderPath: env.KIS_ORDER_PATH || "/uapi/overseas-stock/v1/trading/order",
algo-maker-pegger.mjs:70: kisCancelPath: env.KIS_CANCEL_PATH || "/uapi/overseas-stock/v1/trading/order-rvsecncl",
algo-maker-pegger.mjs:71: kisInquirePath: env.KIS_INQUIRE_PATH || "/uapi/overseas-stock/v1/trading/inquire-nccs",
algo-maker-pegger.mjs:73: kisTrBuy: env.KIS_TR_BUY || "TTTT1002U",
algo-maker-pegger.mjs:74: kisTrSell: env.KIS_TR_SELL || "TTTT1006U",
algo-maker-pegger.mjs:75: kisTrCancel: env.KIS_TR_CANCEL || "TTTT1004U",
algo-maker-pegger.mjs:76: kisTrInquire: env.KIS_TR_INQUIRE || "TTTT3001R",
algo-maker-pegger.mjs:385: KIS API LAYER (thin wrapper)
algo-maker-pegger.mjs:402: jlog("WARN", "KIS_TOKEN_NOT_FOUND", { msg: "No cached token in Redis" });
algo-maker-pegger.mjs:433: req.setTimeout(15000, () => { req.destroy(new Error("KIS_TIMEOUT")); });
```
