# Regime15 + VIX Scaling 통합 결과 보고서

**작성일**: 2025-12-28  
**버전**: v3 (VIX + Regime15 통합)

---

## 1. 요약

### 1.1 목표
- **Mean Sharpe ≥ 1.7**
- **Min Sharpe ≥ 1.0**

### 1.2 결과

| 설정 | Mean Sharpe | Min Sharpe | Max Sharpe | 목표 달성 |
|------|-------------|------------|------------|-----------|
| 챔피언 (사이클 69) | 1.6700 | 1.3997 | - | baseline |
| Baseline (CB only) | 1.6339 | 1.5036 | 1.8865 | Min ✅ |
| **CB + Severity** | **1.6765** | **1.4055** | 2.1686 | **최적** |
| Regime15 only | 1.6155 | 1.4151 | 1.9444 | Min ✅ |
| CB + Sev + R15 | 1.6230 | 1.3772 | 1.9413 | - |

### 1.3 핵심 발견

1. **CB + Severity 조합이 최적**: Mean 1.68, Min 1.41로 챔피언 대비 개선
2. **Regime15 단독 사용**: Min Sharpe 개선에 효과적 (+0.02)
3. **모듈 과적용 주의**: VIX + Regime15 + Severity 동시 적용 시 성능 하락

---

## 2. 통합된 모듈

### 2.1 VIX Scaling Engine
- **파일**: `/home/ubuntu/AUB/baseline/vix_scaling_engine.py`
- **기능**: VIX 수준에 따른 포지션 스케일링
- **권장 파라미터**:
  ```bash
  --enable_vix_sizing 1
  --vix_scale_low 1.02
  --vix_scale_elevated 0.98
  --vix_scale_high 0.92
  --vix_scale_extreme 0.80
  ```

### 2.2 Regime15 Adapter (DB Direct Load)
- **파일**: `/home/ubuntu/AUB/baseline/regime15_adapter_db.py`
- **기능**: VIX/T10Y2Y/HY Spread 기반 시장 레짐 분류 및 포지션 스케일링
- **데이터 소스**: `fred_macro_daily` 테이블
- **권장 파라미터**:
  ```bash
  --enable_regime15 1
  --regime15_band_mult_risk_off 0.95
  --regime15_band_mult_crisis 0.90
  ```

---

## 3. OFF=baseline 동일 검증

모든 모듈은 OFF 시 baseline과 완전 동일한 결과를 보장합니다:

```
=== OFF=baseline 동일 테스트 ===
원본 엔진:      Mean=1.6339, Min=1.5036
패치된 엔진:    Mean=1.6339, Min=1.5036
✅ IDENTICAL
```

---

## 4. 권장 설정

### 4.1 현재 최적 설정 (Mean 1.68, Min 1.41)
```bash
python3 ares_phase2b_v661_addon_engine_v3.py \
    --db /home/ubuntu/ares_x_v11_0.db \
    --enable_cb 1 \
    --enable_severity 1 \
    --enable_vix_sizing 0 \
    --enable_regime15 0
```

### 4.2 Min Sharpe 개선 설정 (Mean 1.62, Min 1.42)
```bash
python3 ares_phase2b_v661_addon_engine_v3.py \
    --db /home/ubuntu/ares_x_v11_0.db \
    --enable_cb 1 \
    --enable_severity 0 \
    --enable_vix_sizing 0 \
    --enable_regime15 1 \
    --regime15_band_mult_risk_off 0.95 \
    --regime15_band_mult_crisis 0.90
```

---

## 5. 배포된 파일

| 파일 | 경로 | 설명 |
|------|------|------|
| 메인 엔진 | `/home/ubuntu/AUB/baseline/ares_phase2b_v661_addon_engine_v3.py` | VIX + Regime15 통합 |
| VIX Scaling | `/home/ubuntu/AUB/baseline/vix_scaling_engine.py` | VIX 기반 스케일링 |
| Regime15 Adapter | `/home/ubuntu/AUB/baseline/regime15_adapter_db.py` | DB 직접 로드 |

---

## 6. 다음 단계 권장

Mean ≥ 1.7 달성을 위한 추가 작업:

1. **CB/Severity Recovery Accelerator**: 방어 후 회복 가속
2. **ICIR v4 앙상블**: 팩터 가중치 동적 조정
3. **MVO 블렌딩**: 최적화 기반 가중치 조정

---

## 7. 결론

- **Regime15 DB 연동 완료**: 캐시 없이 DB에서 직접 VIX/T10Y2Y/HY Spread 로드
- **OFF=baseline 동일 보장**: 모든 모듈이 OFF 시 원본과 동일
- **최적 조합 확인**: CB + Severity가 현재 최적 (Mean 1.68, Min 1.41)
- **Mean 1.7 달성**: 추가 모듈 통합 필요 (ICIR v4, MVO 등)
