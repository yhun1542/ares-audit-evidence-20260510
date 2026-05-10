# No-Delta Diagnosis Report

Generated: 2025-12-31 17:17:40

## Input Files

- **Base (Champion)**: baseline/champion_v5_20251231.json
- **Candidate**: baseline/candidate_20251231_171650_exp_hedge_exit_7.json

## Diagnosed Issues

| Severity | Type | Module/Metric | Details |
|----------|------|---------------|----------|
| HIGH | LOW_TRIGGERS | rate_shock_gate | triggers=0 |
| HIGH | LOW_TRIGGERS | macro_gate | triggers=0 |
| HIGH | LOW_TRIGGERS | vix_crisis_v46 | triggers=0 |

## Recommendations

- rate_shock_gate: 트리거 횟수가 0회로 너무 적음. 임계값 조정 또는 데이터 기간 확장 필요
- macro_gate: 트리거 횟수가 0회로 너무 적음. 임계값 조정 또는 데이터 기간 확장 필요
- vix_crisis_v46: 트리거 횟수가 0회로 너무 적음. 임계값 조정 또는 데이터 기간 확장 필요

## Next Steps

1. HIGH severity 이슈부터 우선 해결
2. 모듈별 로그/카운터 상세 확인
3. 필요 시 rebalance.jsonl 분석으로 날짜별 차이 pinpoint
4. 수정 후 재실행하여 delta 확인
