# AUB 다운스트림 필드 계약 (Spec) v1.2

**문서 버전:** 1.2  
**최종 업데이트:** 2026-01-05

---

## Changelog

| 버전 | 변경 |
|------|------|
| **v1.2** | (A) `rebalancing_log.items.required`에 `date` 추가 |
| | (B) turnover 관련 필드에 `minimum: 0` 추가 |
| | (C) `metrics_reliable=false`일 때 `rebalancing_log` 비어도 합법 명시 |
| | (D) `config_fingerprint.max_turnover`에서 default 제거 (required 강제) |
| | (E) `known_data_issues.items.required`에 `status` 추가 |
| **v1.1** | `metrics_reliable` required, `known_data_issues.description` 추가, `max_turnover` 추가 |
| **v1.0** | 초기 버전 |

---

## 1. 개요

이 문서는 AUB (Automated Universal Backtest) 시스템의 **다운스트림 데이터 계약**을 정의합니다. 모든 자동화 파이프라인, 보고서, 대시보드는 이 문서에 정의된 필드와 스키마를 기준으로 데이터를 소비해야 합니다.

**핵심 원칙:**
- **단일 진실(SoT):** `config_fingerprint.flags_addon`이 유일한 설정 진실입니다.
- **신뢰성:** `summary.metrics_reliable=false`이거나 `known_data_issues`에 OPEN 항목이 있으면, 다운스트림은 "재실행 필요" 상태로 분기해야 합니다.
- **일관성:** `metrics_reliable=true`일 때만 `summary` KPI는 `rebalancing_log`로부터 재계산 가능해야 합니다.
- **범위 검증:** turnover 관련 필드는 `minimum: 0`을 보장합니다.
- **엄격한 스키마:** 이 문서에 정의되지 않은 필드는 사용하지 않습니다.

---

## 2. 필드 계약 10줄 요약

1.  **Addon 진실(SoT):** `config_fingerprint.flags_addon` **만**을 애드온 on/off 판정의 단일 진실로 사용한다.
2.  **Deprecated:** `scenario.flags`, `scenario_addon.flags`는 **신뢰 불가/폐기(deprecated)**로 간주하고 다운스트림에서 읽지 않는다.
3.  **필수 메타:** `config_fingerprint.{db,n_folds,rebal_period,cost_bps,max_turnover}`는 재현성 기본 키로 **필수**다.
4.  **성과 요약:** `summary.{mean_sharpe,min_sharpe,pos_folds,oos_is_ratio}`는 **필수**이며 보고서/대시보드의 1차 지표로 사용한다.
5.  **Fold 벡터:** `oos.fold_sharpes`(또는 동등 필드)는 길이=`n_folds`를 **보장**해야 하며, 누락 시 실패로 처리한다.
6.  **Turnover 정의:** 리밸런싱마다 `turnover_raw = 0.5*Σ|w_new - w_prev|`를 기록한다(스로틀 적용 **전**, `minimum: 0`).
7.  **Turnover capped:** 스로틀 적용 후 `turnover_capped`를 기록하며, 항상 `turnover_capped <= max_turnover`를 만족해야 한다(`minimum: 0`).
8.  **Throttle KPI:** 리밸런싱마다 `throttle_hit = (turnover_raw > max_turnover)`를 기록하고, `throttle_hit_rate = mean(throttle_hit)`를 `summary`에 집계한다.
9.  **운영 게이트용 집계:** `summary.{turnover_mean,turnover_p95,turnover_max,throttle_hit_rate,throttle_hit_count,metrics_reliable}`는 **필수**(0.0 고정이면 "측정 불가"로 실패 처리).
10. **데이터 품질 플래그:** 계측/로깅이 비활성화된 런은 `summary.metrics_reliable=false`(또는 `known_data_issues[]`에 코드+설명+상태)로 명시하고, 다운스트림은 이를 보고 자동으로 "재실행 필요" 상태로 분기한다.

---

## 3. 일관성 규칙 (Consistency Rules)

> **중요:** 아래 규칙은 `metrics_reliable=true`일 때만 적용됩니다. `metrics_reliable=false`일 때는 `rebalancing_log`가 비어있어도 합법입니다.

| 규칙 | 설명 |
|------|------|
| `summary.turnover_mean` | `= mean(rebalancing_log[*].turnover_capped)` |
| `summary.turnover_max` | `= max(rebalancing_log[*].turnover_capped)` |
| `summary.throttle_hit_rate` | `= mean(rebalancing_log[*].throttle_hit)` |
| `summary.throttle_hit_count` | `= sum(rebalancing_log[*].throttle_hit)` |
| `len(oos.fold_sharpes)` | `== config_fingerprint.n_folds` |
| `turnover_capped` | `<= config_fingerprint.max_turnover` (항상) |

---

## 4. Required 필드 요약

| 객체 | Required 필드 |
|------|---------------|
| `config_fingerprint` | `flags_addon`, `db`, `n_folds`, `rebal_period`, `cost_bps`, `max_turnover` |
| `summary` | `mean_sharpe`, `min_sharpe`, `pos_folds`, `oos_is_ratio`, `turnover_mean`, `turnover_p95`, `turnover_max`, `throttle_hit_rate`, `throttle_hit_count`, `metrics_reliable` |
| `oos` | `fold_sharpes` |
| `rebalancing_log[*]` | `date`, `turnover_raw`, `turnover_capped`, `throttle_hit` |
| `known_data_issues[*]` | `code`, `description`, `status` |

---

## 5. 범위 제약 (Minimum/Maximum)

| 필드 | minimum | maximum |
|------|---------|---------|
| `config_fingerprint.n_folds` | 1 | - |
| `config_fingerprint.rebal_period` | 1 | - |
| `config_fingerprint.cost_bps` | 0 | - |
| `config_fingerprint.max_turnover` | 0 | 1 |
| `summary.pos_folds` | 0 | - |
| `summary.turnover_mean` | 0 | - |
| `summary.turnover_p95` | 0 | - |
| `summary.turnover_max` | 0 | - |
| `summary.throttle_hit_rate` | 0 | 1 |
| `summary.throttle_hit_count` | 0 | - |
| `rebalancing_log[*].turnover_raw` | 0 | - |
| `rebalancing_log[*].turnover_capped` | 0 | - |

---

## 6. 다운스트림 분기 로직

```python
def should_rerun(payload):
    """다운스트림이 '재실행 필요' 상태로 분기해야 하는지 판단"""
    
    # 1. metrics_reliable 체크
    if not payload.get("summary", {}).get("metrics_reliable", True):
        return True, "metrics_reliable=false"
    
    # 2. known_data_issues 체크 (OPEN 상태)
    issues = payload.get("known_data_issues", [])
    open_issues = [i for i in issues if i.get("status") == "OPEN"]
    if open_issues:
        return True, f"OPEN issues: {[i['code'] for i in open_issues]}"
    
    # 3. turnover 측정 불가 체크
    summary = payload.get("summary", {})
    if summary.get("turnover_mean", 0) == 0 and summary.get("turnover_max", 0) == 0:
        return True, "turnover 측정 불가 (0.0 고정)"
    
    return False, "OK"
```

---

## 7. 예시 Payload (v86_clean_summary.json 기준)

```json
{
  "config_fingerprint": {
    "flags_addon": {
      "enable_regime_v11": 1,
      "enable_icir_v4": 1,
      "icir_blend_ratio": 0.52,
      "enable_regime15": 1,
      "enable_vix_sizing": 1,
      "enable_hedge": 1,
      "cost_bps": 18
    },
    "db": "ares_x_v11_0.db",
    "n_folds": 20,
    "rebal_period": 15,
    "cost_bps": 18,
    "max_turnover": 0.3
  },
  "summary": {
    "mean_sharpe": 2.0131,
    "min_sharpe": 0.204,
    "pos_folds": 20,
    "oos_is_ratio": 1.2165,
    "turnover_mean": 0.0,
    "turnover_p95": 0.0,
    "turnover_max": 0.0,
    "throttle_hit_rate": 0.0,
    "throttle_hit_count": 0,
    "metrics_reliable": false
  },
  "oos": {
    "fold_sharpes": [1.9047, 1.2486, 2.2475, 2.1013, 3.0943, 2.8697, 1.4677, 1.8562, 3.6459, 3.2885, 3.0244, 1.3806, 1.5074, 2.061, 1.8698, 0.204, 1.8835, 2.3091, 1.3946, 0.9026]
  },
  "rebalancing_log": [],
  "known_data_issues": [
    {
      "code": "KDI-001",
      "description": "scenario.flags 불일치",
      "status": "MITIGATED"
    },
    {
      "code": "KDI-002",
      "description": "turnover 로깅 0",
      "status": "OPEN"
    }
  ]
}
```

> **Note:** `metrics_reliable=false`이므로 `rebalancing_log: []`는 합법입니다. 다운스트림은 "재실행 필요" 상태로 분기합니다.

---

**문서 작성:** Manus AI  
**최종 검토:** 2026-01-05
