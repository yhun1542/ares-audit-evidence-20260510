
# ARES Scaffold Final v9.2

최종 통합본입니다. 목적은 **V1~V9.2에서 살아남아야 할 것만 남기고**, 중간 패치 체인을 끝내는 것입니다.

## 최종 범위

- 상품 범위: `US_EQUITY`, `US_OPTION`
- 실거래 vendor: `KIS`
- 한국주식 경로 제거
- 미국옵션도 `KIS 해외선물옵션 주문` 경로로 통합
- `US_EQUITY` 와 `US_OPTION` 은 같은 KIS adapter 안에서 **route profile / account env** 로 분리

## 유지된 핵심 구조

- Risk Arbiter 단일 state writer
- Lua 기반 원자적 상태 전이
- preopen gate / recovery validating / blocked heartbeat 유지
- SELL_ONLY / FLAT_ONLY 에서 normal lane SELL 차단
- reduce-only / emergency lane 분리
- real Redis / memory Redis 이중 지원
- KIS vendor adapter + Hashkey 경로
- KIS 미국옵션 심볼 매퍼 v9.2
  - 동적 prefix 계산
  - Thursday-shift
  - 32자 길이 제한
  - regex 강화
  - 날짜/strike 검증
  - synthetic fallback 제어

## 현재 계약 기본값

- `broker.mode = kis`
- `US_EQUITY -> broker.kis.route_profiles.us_equity_order`
- `US_OPTION -> broker.kis.route_profiles.us_option_order`

## 필수 환경변수

```bash
export ARES_KIS_APP_KEY=...
export ARES_KIS_APP_SECRET=...
export ARES_KIS_ACCESS_TOKEN=...
export ARES_KIS_PERSONALSECKEY=...

export ARES_KIS_STOCK_CANO=12345678
export ARES_KIS_STOCK_ACNT_PRDT_CD=01

export ARES_KIS_FUTOPT_CANO=87654321
export ARES_KIS_FUTOPT_ACNT_PRDT_CD=08
```

## 빠른 검증

```bash
npm run generate:pm2
npm run typecheck
npm run resilience:test
npm run lane:smoke
npm run vendor:smoke
npm run kis-option-symbol:smoke
```

## 비고

- `paper`, `http`, `routing`, `oms` 관련 코드 파일은 일부 남아 있을 수 있으나 **최종 contract 기본경로는 KIS only** 입니다.
- 운영 기준 판단은 `config/ares.contract.yaml` 을 우선합니다.


## Operational closure

This integrated bundle includes the ops closure assets under `ops/checklists`, `ops/runbooks`, `ops/monitoring`, `data`, and `docs/audits`.

Use `npm run verify:operational` as the top-level local verification entrypoint.
