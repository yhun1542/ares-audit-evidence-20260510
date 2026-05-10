#!/usr/bin/env node
// scripts/smoke_test.mjs — 배포 전 기본 동작 검증
// 실행: KIS_ENV=paper node scripts/smoke_test.mjs AAPL
//      (실전 테스트는 ARES_PROD_ACK=yes-i-know-its-live 필요)
import UnifiedKisBroker from "../index.mjs";

const symbol = process.argv[2] || "AAPL";

(async () => {
  const broker = new UnifiedKisBroker();
  try {
    console.log("[1] 토큰 발급 + WS 접속 중…");
    await broker.connect();

    console.log("[2] 잔고·예수금 확인");
    console.log("  USD cash available:", broker.getUsdCashAvailable());
    console.log("  stock positions   :", broker.getAllStockPositions().length);
    console.log("  option positions  :", broker.getAllOptionPositions().length);
    console.log("  open orders       :", broker.getOpenOrders().length);

    console.log("[3] 시세 조회:", symbol);
    const pr = await broker.stock.price(symbol);
    console.log("  현재가 output:", pr.data?.output?.last);

    const ask = await broker.stock.asking(symbol);
    console.log("  매수1호가/매도1호가:", ask?.output?.pbid1, "/", ask?.output?.pask1);

    console.log("[4] 실시간 구독 (10초)");
    broker.ws.on("tick", ev => {
      if (ev.kind === "STOCK_CCNL") console.log(" tick", ev.symbol, ev.last);
    });
    broker.subscribeRealtime(symbol);
    await new Promise(r => setTimeout(r, 10_000));

    console.log("[5] 종료");
    await broker.disconnect();
    process.exit(0);
  } catch (e) {
    console.error("SMOKE FAIL:", e.message);
    try { await broker.disconnect(); } catch (_) {}
    process.exit(1);
  }
})();
