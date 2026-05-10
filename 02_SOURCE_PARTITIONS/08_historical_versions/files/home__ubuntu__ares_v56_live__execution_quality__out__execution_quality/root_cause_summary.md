# Root Cause Summary

## 관측
- mean_slippage_bps: None
- p50_slippage_bps: None
- p95_slippage_bps: None
- count: 378

## 우선순위 가설
1. direct marketable execution 비중 과다
2. quote freshness 검증 미흡
3. spread gate 부재 또는 느슨함
4. maker-first routing 미적용
5. requote / escape 정책이 primary path가 아님

## 즉시 작업
- execution_router_policy.json 생성 (완료)
- order-intent-executor -> router 위임 패치 (예정)
- pegger shadow 모드 연결 (완료 - v2.1)
- fill/quote telemetry schema 고정 (완료 - cost_collector + quality_consumer)
