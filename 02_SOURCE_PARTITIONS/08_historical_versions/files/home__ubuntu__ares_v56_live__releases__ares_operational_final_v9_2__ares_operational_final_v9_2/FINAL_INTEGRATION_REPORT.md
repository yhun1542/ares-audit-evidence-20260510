
# Final Integration Report

## 포함된 버전 축
- V1: 아키텍처 / 상태전이 / 장전 체크 철학
- V2~V3: preopen-check, risk-arbiter, emergency-liquidator, real Redis, Lua transition
- V4: normal / reduce-only / emergency lane 분리
- V5~V6: broker adapter / KIS vendor profile
- V7~V8: 미국주식 + 미국옵션 범위 확정, KIS 계좌 분리
- V9.2: KIS 미국옵션 심볼 매퍼 최종 보정

## 제거 또는 비기본화된 항목
- 한국주식 기본 경로
- US_OPTION -> OMS 기본 경로
- 초기 V9 default_prefix=1 하드코딩

## 최종 기본 운영경로
- US_EQUITY -> KIS 해외주식 주문
- US_OPTION -> KIS 해외선물옵션 주문
