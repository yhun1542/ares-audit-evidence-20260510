# Symbol Integrity Spec

## 상태
모든 tradable symbol은 아래 셋 중 하나다.
- ACTIVE
- QUARANTINED
- DEPRECATED

## 필수 컬럼
- symbol
- asset_type
- coverage_status
- first_available_date
- last_seen_date
- missing_ratio_252d
- owner
- reason
- eta

## 정책
- ACTIVE: 주문 가능
- QUARANTINED: 주문 금지, owner/reason/eta 필수
- DEPRECATED: universe 제외, 히스토리 유지

## 금지 정책
- silent drop 금지
- owner 없는 quarantine 금지
- 결측률 단일 기준으로 신규 종목 제거 금지
