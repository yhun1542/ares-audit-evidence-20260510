# GitHub 신규 모듈 엔진 통합 계획서

**생성일**: 2025-12-31  
**목적**: GitHub에서 발견된 신규 모듈들을 EC2 엔진에 통합하여 Family Tournament 평가 가능 상태로 만들기

---

## 1. 통합 대상 모듈 (우선순위순)

### 1.1 High Priority (Regime Family)

| 모듈 | 소스 | 특징 | 통합 난이도 |
|------|------|------|------------|
| **regime_v46** | ares_v46_251225 | 5-state regime + hysteresis | 중 |
| **regime_engine_v4** | ares-x-v4-production-final | vol_regime + macro_regime 분리 | 중 |
| **intraday_regime** | ares-x-v4-production-final | 실시간 레짐 감지 | 상 |

### 1.2 Medium Priority (ICIR/VIX Family)

| 모듈 | 소스 | 특징 | 통합 난이도 |
|------|------|------|------------|
| **alpha_scorer_v4** | ares-x-v4-production-final | weights_from_icir() 통합 | 중 |
| **vix_crisis_v46** | ares_v46_251225 | VIX >= 30 Crisis 감지 | 하 |

### 1.3 Low Priority (기존 EC2 모듈)

| 모듈 | 상태 | 비고 |
|------|------|------|
| regime_v11 | 엔진에 존재, flag만 추가 | 하 |
| icir_ensemble | 엔진에 존재, flag만 추가 | 하 |
| hedge_overlay | 엔진에 존재, flag만 추가 | 하 |

---

## 2. 통합 요구사항 (모든 모듈 공통)

### 2.1 CLI Enable Flag

```python
parser.add_argument("--enable_<module>", type=int, default=0,
                    help="Enable <module> (0=off, 1=on)")
```

### 2.2 Diagnostics Addon 기록

```python
# folds[].diagnostics.addon.<module> 구조
{
    "enabled": True/False,
    "available": True/False,
    "last_reason": "ok" | "missing_integration" | "missing_data" | "missing_features",
    "rebalance_calls": int,
    # 모듈별 핵심 카운터
    "risk_on": int,
    "risk_off": int,
    "regime_changes": int,
    # ...
}
```

### 2.3 Available/Last_Reason 규칙

| 상태 | available | last_reason |
|------|-----------|-------------|
| 통합 완료, 정상 동작 | True | "ok" |
| 통합 전 | False | "missing_integration" |
| 데이터 없음 | False | "missing_data" |
| 피처 없음 | False | "missing_features" |

---

## 3. Regime Provider 인터페이스

regime_v46, regime_engine_v4, intraday_regime은 **동일한 출력 스키마**를 따라야 함:

```python
class RegimeProvider:
    """Regime Provider 인터페이스"""
    
    def get_regime(self, date: str, features: dict) -> dict:
        """
        Returns:
            {
                "regime": "BULL" | "NEUTRAL" | "BEAR" | "CRISIS" | "SLOW_RISK",
                "risk_on": bool,
                "risk_off": bool,
                "transition": bool,
                "crisis": bool,
                "exposure_mult": float,  # 0.0 ~ 1.0
                "confidence": float,     # 0.0 ~ 1.0
            }
        """
        raise NotImplementedError
    
    def get_diagnostics(self) -> dict:
        """
        Returns:
            {
                "regime_changes": int,
                "risk_on_count": int,
                "risk_off_count": int,
                "crisis_count": int,
                "avg_exposure_mult": float,
            }
        """
        raise NotImplementedError
```

### 3.1 Mutual Exclusive 규칙

```python
# 동시에 enable 금지
if sum([args.enable_regime15, args.enable_regime_v46, 
        args.enable_regime_engine_v4, args.enable_intraday_regime]) > 1:
    raise ValueError("Only one regime provider can be enabled at a time")
```

---

## 4. 통합 작업 순서

### Phase 1: regime_v46 통합 (1일)

1. `github_scan/ares_v46_251225`에서 regime 로직 추출
2. `RegimeProvider` 인터페이스로 래핑
3. `--enable_regime_v46` flag 추가
4. diagnostics.addon.regime_v46 기록 구현
5. 단위 테스트 (Fold 1-3)

### Phase 2: vix_crisis_v46 통합 (0.5일)

1. VIX >= 30 또는 일간 변화율 >= 20% 시 Crisis 감지 로직
2. `--enable_vix_crisis_v46` flag 추가
3. vix_sizing과 mutual exclusive 또는 보완 관계 정의

### Phase 3: regime_engine_v4 통합 (1일)

1. vol_regime + macro_regime 분리 구조 구현
2. `RegimeProvider` 인터페이스 준수
3. `--enable_regime_engine_v4` flag 추가

### Phase 4: alpha_scorer_v4 통합 (1일)

1. weights_from_icir() 메서드 추출
2. icir_v4와 호환/대체 관계 정의
3. `--enable_alpha_scorer_v4` flag 추가

### Phase 5: 기존 EC2 모듈 flag 추가 (0.5일)

1. regime_v11, icir_ensemble, hedge_overlay
2. 이미 엔진에 로직이 있으면 flag만 연결

---

## 5. 테스트 계획

### 5.1 단위 테스트

```bash
# 각 모듈별 단일 Fold 테스트
python3 ares_phase2b_v661_addon_engine_v3_integration.py \
  --enable_regime_v46=1 --enable_regime15=0 \
  --fold_start=1 --fold_end=1 \
  --output artifacts/test_regime_v46_fold1.json
```

### 5.2 Family Tournament 테스트

```bash
# Regime Family 토너먼트
python3 experiment_system/tools/aub_autopilot.py \
  --registry experiment_system/config/module_registry_mined_family_regime.yaml \
  --run --select-best
```

### 5.3 통합 테스트

```bash
# 전체 20 Fold 테스트
python3 experiment_system/tools/aub_autopilot.py \
  --registry experiment_system/config/module_registry_mined_family.yaml \
  --run --select-best
```

---

## 6. 예상 일정

| Phase | 작업 | 예상 소요 |
|-------|------|----------|
| 1 | regime_v46 통합 | 1일 |
| 2 | vix_crisis_v46 통합 | 0.5일 |
| 3 | regime_engine_v4 통합 | 1일 |
| 4 | alpha_scorer_v4 통합 | 1일 |
| 5 | 기존 EC2 모듈 flag | 0.5일 |
| 6 | Family Tournament 실행 | 0.5일 |
| **합계** | | **4.5일** |

---

## 7. 성공 조건

1. **모든 신규 모듈**이 `--enable_<module>` flag로 활성화 가능
2. **diagnostics.addon.<module>**에 enabled/available/last_reason 기록
3. **Regime Family 토너먼트**에서 regime_v46 vs regime15 비교 가능
4. **PASS 모듈만** V5에 통합 (가이드라인 게이트 통과)

---

## 8. 참고: Best-of Leaderboard 이상치 분석

### FULL_COMBO_TEST.json (제외 대상)

- **Fold 17**: Sharpe 17.30 (ANOMALY > 10)
- **Fold 1, 2, 8**: Sharpe 0 (거래 없음)
- **원인**: avg_exposure 0.002 (거의 거래 없음), regime15/icir_v4/vix_sizing 모두 OFF
- **결론**: 비정상 설정으로 인한 이상치 → 후보에서 제외

### C002_regime15_icir.json (제외 대상)

- **OOS Sharpe Mean**: 5,000,000,002.54 (명백한 오류)
- **원인**: 데이터 오류 또는 0으로 나누기 발생
- **결론**: 데이터 오류 → 후보에서 제외

### 신뢰할 수 있는 후보

| Rank | 파일 | LB_Mean | LB_Min | 상태 |
|------|------|---------|--------|------|
| 1 | champion_v3_20251231.json | 1.84 | 1.19 | ✅ 검증됨 |
| 2 | champion_v5_20251231.json | 1.79 | 1.35 | ✅ 현재 프로덕션 |
| 3 | champion_v4_20251231.json | 1.87 | 1.32 | ✅ 검증됨 |
