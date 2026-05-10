# Module Impact Report: champion_v5_20251231

**생성일**: 2025-12-31 03:56:09

## A/B 소거 기반 모듈 기여도 (V3 기준)

| 모듈 | 비교 | Tuning Mean Δ | Lockbox Mean Δ | Lockbox Min Δ | 해석 |
|---|---|---|---|---|---|
| ICIR v4 | V3 → no_icir | -0.1511 | -0.3971 | -0.2728 | 평균/락박스 성과 핵심 엔진 |
| regime15 | V3 → no_regime15 | -0.3033 | -0.1009 | -0.3568 | 평균에도 기여, lockbox min 방어 |
| vix_sizing | V3 → no_vix | -0.0490 | -0.0650 | -0.0761 | 완만하지만 일관된 방어 |
| hedge 정책 | always_on ↔ crisis_only | (정책 비교) | (정책 비교) | (정책 비교) | 조건부+히스테리시스가 정답 |

> **결론**: ICIR/regime15/vix_sizing은 성능 핵심이며, 헤지는 '항상'이 아니라 조건부+히스테리시스가 정답입니다.

## 모듈 상태

| 상태 | 모듈 |
|---|---|
| **활성 (V5 구성)** | icir_v4, regime15, vix_sizing, hedge(crisis_only+hysteresis), cb, throttle, compat, no_trade |
| **비활성/평가불가** | rate_shock_gate(피처 미구현), macro_gate(unknown=300) |
| **탈락** | mvo(성과 악화) |
