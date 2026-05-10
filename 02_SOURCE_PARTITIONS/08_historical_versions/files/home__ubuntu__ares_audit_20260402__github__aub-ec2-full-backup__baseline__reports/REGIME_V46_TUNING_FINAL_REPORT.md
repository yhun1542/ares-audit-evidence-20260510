# regime_v46 튜닝 및 Go/No-Go 최종 보고서

**생성일**: 2025-12-31
**상태**: ❌ NO-GO (regime 대체 후보에서 제외)

---

## 1. 튜닝 결과 요약

### 1.1 3케이스 튜닝 결과 (T0/T1/T2 vs V5)

| Config | OOS Mean | LB Mean | LB Min | LB Drop |
|--------|----------|---------|--------|---------|
| **V5 (regime15)** | **1.7680** | **1.7927** | **1.3469** | (baseline) |
| T0 (default) | 1.4058 | 1.3122 | 0.1017 | **+0.4806** |
| T1 (exit3/expo050) | 1.4720 | 1.3245 | -0.0392 | **+0.4683** |
| T2 (slow0815/exit3/expo050) | 1.4925 | 1.3419 | -0.0392 | **+0.4508** |

### 1.2 Go/No-Go 판정

**기준**: lockbox_mean 드롭 ≤ 0.10

| Config | 드롭 | 판정 |
|--------|------|------|
| T0 (default) | 0.4806 | ❌ FAIL |
| T1 (exit3/expo050) | 0.4683 | ❌ FAIL |
| T2 (slow0815/exit3/expo050) | 0.4508 | ❌ FAIL |

**결론**: 모든 튜닝 케이스가 기준 미달 → **regime 대체 후보에서 제외**

---

## 2. CLI 파라미터 구현 완료

### 2.1 추가된 CLI 파라미터

```bash
--regime_v46_slow60        # SLOW60 threshold (default: -0.05)
--regime_v46_slow120       # SLOW120 threshold (default: -0.10)
--regime_v46_exit_confirm  # EXIT_CONFIRM days (default: 5)
--regime_v46_exposure_slow_risk  # EXPOSURE_SLOW_RISK (default: 0.35)
```

### 2.2 diagnostics 기록

```json
{
  "regime_v46": {
    "enabled": true,
    "available": true,
    "last_reason": "ok",
    "rebalance_calls": 43,
    "regime_changes": 11,
    "bull_count": 20,
    "neutral_count": 11,
    "bear_count": 4,
    "crisis_count": 0,
    "slow_risk_count": 8,
    "exposure_mult_sum": 30.6,
    "exposure_mult_avg": 0.71,
    "cfg": {
      "slow60": -0.08,
      "slow120": -0.15,
      "exit_confirm": 3,
      "exposure_slow_risk": 0.5
    }
  }
}
```

---

## 3. regime_v46 보조 트리거 차용 방안

regime_v46은 "regime 대체"로는 부적합하지만, 다음 용도로 차용 가능:

### 3.1 vix_crisis_v46 트리거

- **용도**: hedge 트리거 OR 조건에 추가
- **조건**: VIX ≥ 30 또는 VIX 변화율 ≥ 20%
- **구현 상태**: ✅ 완료 (hedge 조건에 OR로 추가됨)

### 3.2 slow_risk_signal 보조 신호

- **용도**: exposure 스케일링 보조 신호
- **조건**: cumret_60 < SLOW60 또는 cumret_120 < SLOW120
- **구현 상태**: ⏸️ 대기 (필요 시 overlay로 추가)

---

## 4. 남은 작업 현황

### 4.1 GitHub 모듈 통합 (PENDING_INTEGRATION)

| 모듈 | 상태 | CLI Flag | 예상 공수 |
|------|------|----------|-----------|
| regime_engine_v4 | PENDING | --enable_regime_engine_v4 | 1d |
| alpha_scorer_v4 | PENDING | --enable_alpha_scorer_v4 | 1d |

### 4.2 Stage3 해결 (INCONCLUSIVE)

| 모듈 | 문제 | 다음 단계 | 예상 공수 |
|------|------|-----------|-----------|
| rate_shock_gate | 피처 미구현 | DB에 yield_2y 데이터 확보 | 1d |
| macro_gate | unknown=300 | 날짜 포맷 매칭 수정 | 2d |

---

## 5. 설치된 파일

### EC2 (/home/ubuntu/AUB/)

```
baseline/ares_phase2b_v661_addon_engine_v3_integration_v46.py  # v46 CLI 통합 엔진
artifacts/regime_v46_T0_default.json                          # T0 결과
artifacts/regime_v46_T1_exit3_expo050.json                    # T1 결과
artifacts/regime_v46_T2_slow0815_exit3_expo050.json           # T2 결과
experiment_system/config/module_registry_mined_family_regime.yaml  # 업데이트된 레지스트리
```

---

## 6. 권장 다음 단계

1. **V5 유지**: 현재 프로덕션 챔피언 V5(regime15 + icir_v4 + vix_sizing + hedge crisis_only) 유지
2. **regime_engine_v4 통합**: vol_regime + macro_regime 분리 로직 통합
3. **alpha_scorer_v4 통합**: weights_from_icir() 통합
4. **Stage3 해결**: rate_shock_gate 피처 구현, macro_gate 날짜 매칭 수정
5. **vix_crisis_v46 효과 모니터링**: hedge 트리거 OR 조건 효과 확인

---

## 7. 결론

regime_v46은 로직 적용은 정상(카운터/노출 multiplier 적용 확인)이나, **성능이 V5(regime15) 대비 크게 열위**하여 regime 대체 후보에서 제외됩니다.

다만, vix_crisis_v46 트리거와 slow_risk_signal은 **보조 트리거/overlay**로 차용 가능하며, 이는 V5의 hedge 조건 강화에 활용할 수 있습니다.
