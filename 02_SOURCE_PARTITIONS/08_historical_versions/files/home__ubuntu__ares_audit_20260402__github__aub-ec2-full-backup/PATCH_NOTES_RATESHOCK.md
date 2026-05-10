# RateShockGate Patch Notes

**Date**: 2025-12-30
**Version**: v661-rateshock-v1

---

## Summary

RateShockGate 기능 구현 및 캘리브레이션 완료. 2년물 국채 금리 변화(d2y)를 기반으로 급격한 금리 변동 시 포지션 스케일을 조정하는 리스크 관리 게이트.

---

## Files Modified

### 1. ares_phase2b_v661_addon_engine_v3_integration.py

**Location**: `baseline/`

**Changes**:
- Line 509: `enable_rate_shock_gate` 조건 추가 (bypass 조건)
- Line 519: `enable_rate_shock_gate` 조건 추가 (early return 방지)
- Line 598-622: RateShockGate 훅 구현

```python
# Line 509
if not self.config.get('enable_rate_shock_gate', False):
    return super()._build_portfolio(...)

# Line 598-622
if self.config.get('enable_rate_shock_gate', False):
    d2y_5d = regime_features.get('d2y_5d_bp', 0.0)
    d2y_20d = regime_features.get('d2y_20d_bp', 0.0)
    
    thr_5d = self.config.get('rate_shock_gate_config', {}).get('thr_2y_5d_bp', 80)
    thr_20d = self.config.get('rate_shock_gate_config', {}).get('thr_2y_20d_bp', 150)
    min_scale = self.config.get('rate_shock_gate_config', {}).get('min_scale', 0.85)
    
    if abs(d2y_5d) > thr_5d or abs(d2y_20d) > thr_20d:
        weights = weights * min_scale
```

### 2. ares_phase2b_v661_production_hotfix_compat.py

**Location**: `baseline/`

**Changes**:
- Line 803-830: `_compute_regime_features()`에 d2y 피처 추가

```python
# Line 803-830
from production_modules.features.rateshock_features import RateShockFeatures

def _compute_regime_features(self, trading_calendar):
    features = super()._compute_regime_features(trading_calendar)
    
    # Add d2y features
    rs_features = RateShockFeatures()
    d2y_df = rs_features.compute_features(trading_calendar)
    
    features['d2y_5d_bp'] = d2y_df['d2y_5d_bp']
    features['d2y_20d_bp'] = d2y_df['d2y_20d_bp']
    
    return features
```

### 3. production_modules/features/rateshock_features.py (NEW)

**Location**: `production_modules/features/`

**Description**: DGS2 데이터 로더 및 d2y 피처 계산 모듈

**Key Functions**:
- `load_dgs2_from_db()`: PostgreSQL에서 DGS2 데이터 로드
- `compute_features()`: d2y_5d_bp, d2y_20d_bp 계산

```python
class RateShockFeatures:
    def load_dgs2_from_db(self):
        query = """
        SELECT date, value 
        FROM fred_macro_daily 
        WHERE series_id = 'DGS2'
        ORDER BY date
        """
        return pd.read_sql(query, self.engine)
    
    def compute_features(self, trading_calendar):
        dgs2 = self.load_dgs2_from_db()
        dgs2 = dgs2.reindex(trading_calendar).ffill()
        
        d2y_5d = dgs2['value'].diff(5) * 100  # basis points
        d2y_20d = dgs2['value'].diff(20) * 100
        
        return pd.DataFrame({
            'd2y_5d_bp': d2y_5d,
            'd2y_20d_bp': d2y_20d
        })
```

### 4. baseline/rate_shock_gate.py (NEW)

**Location**: `baseline/`

**Description**: RateShockGate 클래스 (standalone 구현)

---

## Configuration

### Recommended Settings (freq_3pct_scale80)

```python
config = {
    'enable_rate_shock_gate': True,
    'rate_shock_gate_config': {
        'thr_2y_5d_bp': 28,      # 5일 변화 임계값 (bp)
        'thr_2y_20d_bp': 60,     # 20일 변화 임계값 (bp)
        'min_scale': 0.80,       # 트리거 시 스케일 팩터
        'hold_days': 5           # 트리거 유지 기간
    }
}
```

### Original Settings (NOT RECOMMENDED)

```python
# 원래 임계값 - 발동 확률 0.03%로 비현실적
config = {
    'rate_shock_gate_config': {
        'thr_2y_5d_bp': 80,
        'thr_2y_20d_bp': 150,
    }
}
```

---

## Performance Impact

| Setting | Mean Sharpe | Min Sharpe | Delta |
|---------|-------------|------------|-------|
| Baseline | 1.4985 | 0.0944 | - |
| freq_3pct_scale80 | 1.5087 | 0.1355 | +0.68% |

**Min Sharpe 개선**: +43.5% (0.0944 → 0.1355)

---

## Testing

### Verification Script

```bash
python scripts/verify_rateshock_features.py
```

### Expected Output

```
d2y_5d_bp: 90-94% non-zero values
d2y_20d_bp: 90-94% non-zero values
RateShockGate trigger simulation: ~3% at thr=28/60bp
```

---

## Known Issues

1. **원래 임계값(80/150bp)은 비현실적**: 발동 확률 0.03%
2. **Fold16(2021-2022)에서도 원래 임계값으로 트리거 0회**
3. **hold_days 최적화 필요**: 현재 5일 고정, 3~10일 범위 테스트 권장

---

## Rollback

RateShockGate를 비활성화하려면:

```python
config = {
    'enable_rate_shock_gate': False
}
```
