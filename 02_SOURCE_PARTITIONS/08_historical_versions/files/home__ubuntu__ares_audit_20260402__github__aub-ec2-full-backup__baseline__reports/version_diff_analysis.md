# 버전별 소스코드 차이 분석 (3.32 vs 2.67 vs 1.88)

## 1. 버전별 성능 요약

| 버전 | Sharpe | 활성 모듈 | 문제점 |
|------|--------|----------|--------|
| 3.32 (asv5) | 2.66 | regime15, vix_scaling, alpha_scorer_v5, icir_v4 | 변동성 버그 |
| 2.67 (legacy) | 3.35 | regime15, vix_scaling, regime_v11, icir_v4 | 변동성 버그 |
| 1.88 (정상) | 1.88 | regime_v11, icir_v4 | 없음 |

## 2. 핵심 차이점: regime15 + vix_scaling

### 2.1 regime15 (regime15_adapter_db.py)

```python
# Crisis 조건: VIX PANIC 또는 Credit CRISIS
if self._vix_regime == VIXRegime.PANIC or self._credit_regime == CreditRegime.CRISIS:
    return MarketRegime.CRISIS  # band_mult = 0.3 (70% 억제)

# Risk Off 조건
if (self._vix_regime == VIXRegime.FEAR or 
    self._credit_regime == CreditRegime.STRESS or
    self._yield_regime == YieldCurveRegime.INVERTED):
    return MarketRegime.RISK_OFF  # band_mult = 0.5 (50% 억제)
```

**문제**: 백테스트 기간 동안 43회 리밸런싱 중 **100% crisis 상태** → band_mult=0.3 고정

### 2.2 vix_scaling (vix_scaling_engine.py)

```python
@dataclass
class VIXScalingConfig:
    scale_low: float = 1.10      # VIX < 12
    scale_normal: float = 1.00   # VIX 12-18
    scale_elevated: float = 0.85 # VIX 18-25
    scale_high: float = 0.65     # VIX 25-35
    scale_extreme: float = 0.40  # VIX > 35
```

**문제**: EXTREME 18회, HIGH 22회 → scale 0.4~0.84 (추가 억제)

### 2.3 결합 효과

```
최종 포지션 = 원래 포지션 × regime15_band_mult × vix_scale
           = 원래 포지션 × 0.3 × 0.4
           = 원래 포지션 × 0.12 (88% 억제)
```

**결과**: 포지션 거의 0 → 변동성 0% → Sharpe 인플레이션

## 3. 1.88 버전이 정상인 이유

```python
# 1.88 버전 활성 모듈
enable_icir_v4: True
enable_regime_v11: True  # 다른 레짐 엔진 (억제 없음)
enable_macro_gate: False
enable_mvo: False
# regime15: False (비활성)
# vix_scaling: False (비활성)
```

regime_v11은 포지션을 억제하지 않고 레짐 정보만 제공

## 4. 소스코드 비교

### 4.1 3.32 버전 실행 시 적용된 코드 경로

```python
# ares_phase2b_v661_addon_engine_v3_integration.py 라인 1320-1340
if self.regime15_adapter is not None:
    r15_result = self.regime15_adapter.pre_rebalance(dt_str)
    r15_mult = r15_result.get("band_multiplier", 1.0)
    # r15_mult = 0.3 (crisis 상태)
    
# 라인 1437-1443
if self.enable_vix_scaling:
    vix_scale = self._vix_engine.get_scale_factor(current_vix, dt_str)
    # vix_scale = 0.4~0.84 (EXTREME/HIGH 상태)
    
# 최종 포지션 계산
final_position = base_position * r15_mult * vix_scale
# final_position ≈ 0.12 * base_position
```

### 4.2 1.88 버전 실행 시 적용된 코드 경로

```python
# regime15_adapter = None (비활성)
# vix_engine = None (비활성)

# 포지션 억제 없음
final_position = base_position  # 100% 유지
```

## 5. 결론

| 요소 | 3.32/2.67 버전 | 1.88 버전 |
|------|---------------|-----------|
| regime15 | 활성 (crisis 100%) | 비활성 |
| vix_scaling | 활성 (EXTREME/HIGH 93%) | 비활성 |
| 포지션 억제 | 88% | 0% |
| 변동성 | ~0% (버그) | 정상 |
| Sharpe | 2.66~3.35 (인플레이션) | 1.88 (정상) |

**권장사항**: regime15 + vix_scaling 조합 사용 금지
