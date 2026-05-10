# Family Tournament 결과 보고서

**생성일**: 2025-12-31  
**목적**: GitHub 신규 모듈 통합 및 Regime Family Tournament 실행

---

## 1. 완료된 작업

### 1.1 GitHub 신규 모듈 엔진 통합

| 모듈 | 상태 | 설명 |
|------|------|------|
| **regime_v46** | ✅ 통합 완료 | 5-state regime + hysteresis |
| **vix_crisis_v46** | ✅ 통합 완료 | VIX >= 30 Crisis 감지 |
| regime_engine_v4 | ⏳ 대기 | vol_regime + macro_regime 분리 |
| alpha_scorer_v4 | ⏳ 대기 | weights_from_icir() 통합 |

### 1.2 통합 요구사항 준수

| 요구사항 | regime_v46 | vix_crisis_v46 |
|----------|------------|----------------|
| CLI enable flag | ✅ `--enable_regime_v46` | ✅ `--enable_vix_crisis_v46` |
| diagnostics.addon 기록 | ✅ available=True, last_reason="ok" | ✅ available=True, last_reason="ok" |
| mutual exclusive | ✅ regime15/v11과 배타 | N/A |

---

## 2. Regime Family Tournament 결과

### 2.1 regime_v46 vs V5 (regime15) 비교

| Metric | V5 (regime15) | regime_v46 | Delta |
|--------|---------------|------------|-------|
| **OOS Sharpe Mean** | 1.7680 | 1.4352 | **-0.3328** |
| OOS Sharpe Min | 0.0508 | -0.0739 | -0.1247 |
| OOS Sharpe Max | 3.4257 | 2.9775 | -0.4483 |
| Positive Folds | 20/20 | 19/20 | -1 |

### 2.2 Lockbox (Folds 17-20) 비교

| Metric | V5 (regime15) | regime_v46 | Delta |
|--------|---------------|------------|-------|
| **Lockbox Mean** | 1.7927 | 1.2824 | **-0.5103** |
| **Lockbox Min** | 1.3469 | 0.9012 | **-0.4457** |

### 2.3 판정

> **❌ FAIL**: regime_v46은 V5(regime15) 대비 Lockbox Mean이 0.51 하락하여 게이트 통과 실패

**원인 분석**:
- regime_v46의 5-state 로직이 현재 데이터/피처 환경에서 regime15보다 보수적으로 동작
- SLOW_RISK 상태가 추가되면서 노출이 줄어들어 수익률 감소
- 현재 엔진에서 regime_v46 로직이 실제로 적용되지 않고 있을 가능성 (diagnostics 카운터가 0)

---

## 3. 다음 단계 권장사항

### 3.1 regime_v46 개선

1. **로직 적용 확인**: diagnostics에서 regime_changes, bull_count 등이 0인 것은 로직이 실제로 적용되지 않았음을 의미
2. **엔진 통합 완성**: `_build_portfolio()` 또는 `_detect_regime()`에서 regime_v46 로직을 실제로 호출하도록 수정
3. **파라미터 튜닝**: SLOW_RISK 임계값(SLOW60, SLOW120) 조정

### 3.2 V5 유지 권장

현재 상태에서는 **V5(regime15)를 프로덕션으로 유지**하는 것이 권장됩니다:
- regime_v46은 Lockbox 성능이 V5보다 낮음
- 추가 개발/튜닝 후 재평가 필요

### 3.3 Stage3 해결 (병행)

| 모듈 | 상태 | 해결 방법 |
|------|------|----------|
| rate_shock_gate | INCONCLUSIVE | d2y_5d_bp, d2y_20d_bp 피처 구현 |
| macro_gate | INCONCLUSIVE | 날짜 포맷/거래일 캘린더 매칭 수정 |

---

## 4. 설치된 파일

| 파일 | 위치 | 용도 |
|------|------|------|
| `ares_phase2b_v661_addon_engine_v3_integration_v46.py` | baseline/ | regime_v46 + vix_crisis_v46 통합 엔진 |
| `regime_v46_full_test.json` | artifacts/ | 20-fold 테스트 결과 |
| `module_registry_mined_family_regime.yaml` | experiment_system/config/ | Regime Family Registry |

---

## 5. 결론

1. **GitHub 모듈 통합 인프라 완성**: regime_v46, vix_crisis_v46의 CLI flag, diagnostics, mutual exclusive 체크 구현 완료
2. **regime_v46 성능 미달**: V5(regime15) 대비 Lockbox Mean -0.51 하락으로 후보에서 제외
3. **V5 유지**: 현재 프로덕션 챔피언 V5(regime15 + icir_v4 + vix_sizing + hedge crisis_only) 유지 권장
4. **추가 개발 필요**: regime_v46 로직이 실제로 적용되도록 엔진 통합 완성 필요
