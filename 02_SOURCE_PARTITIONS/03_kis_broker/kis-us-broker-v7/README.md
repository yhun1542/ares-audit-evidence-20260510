# kis-us-broker-v7 — ARES KIS US Broker (First-Principles Rebuild)

## 핵심 특징

1. **공식 TR_ID 100% 준수**: ARES의 존재하지 않는 `JTTT*` 코드를 공식 `TTTT*/TTTS*/CTRP*/OTFM*/HHDF*`로 전수 교정.
2. **Hashkey 자동 주입**: 모든 POST 주문 API 호출에 `POST /uapi/hashkey` 서명을 자동 계산해 `hashkey` 헤더를 포함.
3. **Secret 평문 금지**: 환경변수 또는 `/etc/ares/kis.env` (권한 0600) 만 허용. `ARES_PROD_ACK=yes-i-know-its-live` 환경 가드 내장.
4. **단일 토큰 매니저**: 동일 AppKey의 1분 내 중복 발급을 차단해 KIS 정책에 부합.
5. **토큰 버킷 Rate Limiter**: 실전 18 TPS / 모의 2 TPS 전역 제한.
6. **주식 + 옵션 통합**: 미국옵션(해외선물옵션) 신규 구현 — 주문(OTFM3001U), 예수금(OTFM1411R), 미결제(OTFM1412R), 증거금(OTFM3115R), 시세(HHDFO*).
7. **WebSocket 실시간 6종**: HDFSASP0(호가) / HDFSCNT0(체결) / HDFFF010(옵션호가) / HDFFF020(옵션체결) / H0GSCNI0(내 주식체결) / HDFFF2C0(내 옵션체결). AES256-CBC 복호화 내장.
8. **Aggressive Limit MARKET**: KIS 해외주식이 순수 시장가를 지원하지 않는 점을 감안, `orderType="MARKET"` 요청 시 실시간 1호가 + 5 tick 돌파 지정가로 자동 변환.
9. **거래소 자동 판별**: AAPL→NAS, IBM→NYS 등 24h 캐시 + 실시간 탐지. 하드코딩 whitelist 제거.

## 설치 (EC2)

```bash
# 1) 코드 배치
cd /home/ubuntu/aub-trading-system
git clone <repo> kis-us-broker-v7   # 또는 SCP로 업로드
cd kis-us-broker-v7 && pnpm install

# 2) 시크릿 분리 (평문 삭제)
sudo mkdir -p /etc/ares
sudo tee /etc/ares/kis.env > /dev/null <<'EOF'
KIS_APP_KEY=PS50...
KIS_APP_SECRET=p+gjjMQi...
KIS_ACCOUNT_NO=81378930
KIS_ACCOUNT_PROD_CODE=01
KIS_HTS_ID=your_hts_id
KIS_ENV=real        # 또는 paper
EOF
sudo chmod 600 /etc/ares/kis.env
sudo chown ubuntu:ubuntu /etc/ares/kis.env

# 3) 기존 kis_env.config.cjs 의 하드코딩 제거 후 재시작
rm -f /home/ubuntu/aub-trading-system/kis_env.config.cjs.bak
mv /home/ubuntu/aub-trading-system/kis_env.config.cjs /home/ubuntu/aub-trading-system/kis_env.config.cjs.bak

# 4) 페이퍼 모드 스모크
KIS_ENV=paper node scripts/smoke_test.mjs AAPL

# 5) 실전 모드 스모크(명시적 동의)
ARES_PROD_ACK=yes-i-know-its-live node scripts/smoke_test.mjs AAPL
```

## API 요약

```js
import UnifiedKisBroker from "./index.mjs";
const broker = new UnifiedKisBroker({ logger });
await broker.connect();                              // 토큰+WS 접속+스냅샷

// 조회
broker.getUsdCashAvailable();                        // 가용 달러 현금
broker.getAllStockPositions();                       // 보유 미국주식(수량·평균단가·손익)
broker.getOptionPosition("1AALN25 C10.0");           // 옵션 포지션 1건
broker.getOpenOrders();                              // 미체결 주문

// 시세 (REST)
await broker.stock.price("AAPL");
await broker.stock.asking("AAPL");
await broker.option.price("1AALN25 C10.0");

// 주문
await broker.stock.placeOrder({
  symbol: "AAPL", side: "BUY", quantity: 1,
  orderType: "LIMIT", price: 190.00
});
await broker.stock.placeOrder({
  symbol: "AAPL", side: "BUY", quantity: 1,
  orderType: "MARKET"   // 자동으로 공격적 지정가로 변환
});

// 옵션 주문
await broker.option.placeOrder({
  pdno: "1AALN25 C10.0", side: "BUY", quantity: 1,
  priceType: "LIMIT", price: 2.5
});

// 실시간 구독
broker.subscribeRealtime("AAPL");                    // 호가+체결
broker.ws.subscribeOptionPrice("1AALN25 C10.0");
broker.on("fill", e => console.log("FILL", e));
```

## 교체 절차 (기존 ARES와 통합)

1. `order-intent-executor.mjs`, `order_retry_worker_v1.mjs`에서 `kisConnector.placeOrder` 호출을 `broker.stock.placeOrder`로 치환.
2. `kis-balance-sync.mjs` → `broker.snapshot()` 및 `broker.ws`(HDFSCNT0) 스트림으로 교체, `JTTT3012R` 경로는 Deprecated 처리.
3. `kis-token-service.mjs`를 제거하고, 모든 서비스가 동일한 `TokenManager` 파일 캐시 공유.
4. 5분간 페이퍼 모드 병행 실행 후 실전 전환 (paper/real 환경변수만 스위치).

## 결함 교정 추적표 (원본 vs 신규)

| 항목 | 기존 ARES | kis-us-broker-v7 |
|---|---|---|
| 미국 매수 TR_ID | `JTTT1002U` (없는 코드) | `TTTT1002U` / `VTTT1002U` |
| 미국 매도 TR_ID | `JTTT1006U` (없는 코드) | `TTTT1006U` / `VTTT1006U` |
| 잔고 TR_ID | `JTTT3012R` (없는 코드) | `TTTS3012R` |
| Hashkey | 없음 | 자동 주입 |
| Secret | 평문 하드코딩 | 0600 파일 + env |
| MARKET 주문 | `"00"`+`price` 그대로 | Aggressive Limit 자동 변환 |
| 시장 분류 | `NYSE_SYMBOLS` 20종 whitelist | price API 실탐색 + 24h 캐시 |
| 옵션 지원 | **없음** | 전면 구현 |
| 실시간 시세 | 폴링만 | WS 6채널 |
| 체결통보 | H0GSCNI0 단일 | 주식·옵션 양쪽 |
| 토큰 관리 | 서비스별 분산 | 단일 파일 SSOT + 60s 쿨다운 |
| Rate limit | 부분 적용 | 전역 토큰버킷 |
| TR_ID 상수 | 곳곳 하드코딩 | `core/tr_ids.mjs` 단일 소스 |
