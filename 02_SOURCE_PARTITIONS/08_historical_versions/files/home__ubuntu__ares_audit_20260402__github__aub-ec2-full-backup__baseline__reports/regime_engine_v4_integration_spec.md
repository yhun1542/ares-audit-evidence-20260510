# regime_engine_v4 통합 명세

## 개요
- **소스**: GitHub ares-x-v4-production-final
- **특징**: vol_regime + macro_regime 분리, 5-state regime
- **상태**: PENDING_INTEGRATION

## CLI Flag
```
--enable_regime_engine_v4 (0/1)
```

## Diagnostics Schema
```yaml
diagnostics.addon.regime_engine_v4:
  enabled: bool
  available: bool
  last_reason: str  # ok, mutual_exclusive, missing_data
  rebalance_calls: int
  regime_changes: int
  state_counts:
    risk_on: int
    risk_off: int
    transition: int
    crisis: int
    slow_risk: int
  exposure_mult_sum: float
  exposure_mult_avg: float
```

## Mutual Exclusive
- regime15, regime_v46, regime_engine_v4 중 하나만 enable 가능
- 동시 enable 시 last_reason='mutual_exclusive'

## 통합 작업
1. CLI argparse에 --enable_regime_engine_v4 추가
2. compute_regime_engine_v4() 함수 구현
3. _build_portfolio에서 호출 및 exposure_mult 적용
4. diagnostics 기록

## 예상 공수: 1일
