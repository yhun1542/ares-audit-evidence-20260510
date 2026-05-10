# AUB 최종 상속 문서 (One Single Truth)

**최종 업데이트**: 2026-01-05
**현재 챔피언**: V6 (Sharpe 1.88)
**작성자**: Manus AI

---

## 🏆 현재 챔피언: V6

### 핵심 성능

| 지표 | 값 | 4AI 기준 |
|------|-----|----------|
| **OOS Sharpe Mean** | **1.88** | ≥ 1.5 ✅ |
| **OOS Sharpe Min** | -0.22 | ≥ -0.3 ✅ |
| **Positive Folds** | 95% | ≥ 80% ✅ |
| **OOS/IS Ratio** | 1.22 | ≥ 0.7 ✅ |

### 검증 상태

| 검증 항목 | 상태 |
|-----------|------|
| 과적합성 | ✅ PASS |
| 룩어헤드 편향 | ✅ PASS |
| 생존 편향 | ✅ PASS |
| 비용 강건성 | ✅ PASS |

---

## 📋 최종 파라미터

```json
{
  "enable_icir_v4": 1,
  "icir_blend_ratio": 0.52,
  "enable_regime15": 1,
  "regime15_band_mult_risk_off": 0.78,
  "regime15_band_mult_crisis": 0.86,
  "enable_vix_sizing": 1,
  "vix_scale_low": 1.10,
  "vix_scale_extreme": 0.74,
  "enable_hedge": 1,
  "enable_severity": 0,
  "rebal_period": 15,
  "cost_bps": 18
}
```

---

## 🚀 실행 커맨드

```bash
cd /home/ubuntu/AUB && python3 baseline/ares_phase2b_v661_addon_engine_v3_integration.py \
  --db /home/ubuntu/ares_x_v11_0.db \
  --enable_icir_v4 1 \
  --icir_blend_ratio 0.52 \
  --enable_regime15 1 \
  --regime15_band_mult_risk_off 0.78 \
  --regime15_band_mult_crisis 0.86 \
  --enable_vix_sizing 1 \
  --vix_scale_low 1.10 \
  --vix_scale_extreme 0.74 \
  --enable_hedge 1 \
  --enable_severity 0 \
  --rebal_period 15 \
  --cost_bps 18 \
  --output results/champion_v6_result.json
```

---

## 📊 버전 히스토리

| 버전 | 날짜 | Sharpe | 주요 변경 |
|------|------|--------|-----------|
| V1 | 2025-12-26 | 1.43 | 초기 베이스라인 |
| V2 | 2025-12-28 | 1.79 | AVT 최적화 |
| V3 | 2025-12-30 | 1.81 | ICIR_v4 + Regime15 |
| V4 | 2025-12-31 | 1.81 | 헤지 모드 최적화 |
| V5 | 2025-12-31 | 1.85 | icir_blend_ratio 0.5 |
| **V6** | **2026-01-05** | **1.88** | **그리드서치 최적화** |

---

## 📁 관련 파일

| 파일 | 경로 |
|------|------|
| 챔피언 설정 (V6) | `baseline/champions/champion_v6_sharpe188.json` |
| 최신 설정 | `baseline/champions/champion_latest.json` |
| V6 상속 문서 | `baseline/AUB_CHAMPION_V6_INHERITANCE.md` |
| 검증 보고서 | `results/wf_champion_188_detailed_20260104.json` |
| 엔진 | `baseline/ares_phase2b_v661_addon_engine_v3_integration.py` |
| DB | `/home/ubuntu/ares_x_v11_0.db` |

---

## ⚠️ 주의사항

1. **DB 경로**: 반드시 `/home/ubuntu/ares_x_v11_0.db` 사용
2. **비용 설정**: 기본 18bps, 실거래 시 25-30bps 권장
3. **리밸런싱**: 15일 주기 유지
4. **헤지**: crisis_only 모드 권장

---

**END OF DOCUMENT**

## Autopilot Promotion (20260104_100648)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_100726)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_101337)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_101441)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_101416)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_101452)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_101454)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_102105)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_102143)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_102222)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_102425)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_102430)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_103003)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_103041)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_103119)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_103730)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_103809)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_103847)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_104458)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_104536)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_104614)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_105225)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_105304)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_105342)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_110130)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_110208)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_110246)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_110857)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_110935)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_111014)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_111625)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_111703)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_111810)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_111742)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_111820)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_112353)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_112431)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_112510)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_112702)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_112707)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_113239)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_113317)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_113356)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.8816 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.8816 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_122148)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_122159)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_122827)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_122831)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_132528)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_132539)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_133137)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_133141)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_142952)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_143003)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_143443)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_143447)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_153413)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_153425)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_153745)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_153749)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_163640)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_163651)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_164036)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_164041)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_173759)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_173810)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_174136)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_174141)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_183938)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_183949)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_184443)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_184447)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_194008)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_194019)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_194722)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_194726)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_204340)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_204351)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_205058)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_205103)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_210001)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_214737)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_214748)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_215355)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_215400)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_224847)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_224858)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_225542)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_225547)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_230321)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_231145)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_232146)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_233146)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_234147)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_234924)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_234934)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_235147)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_235954)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260104_235959)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_000147)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_001147)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_002148)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_003148)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_004150)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_005150)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_005313)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_005324)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_010150)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_010315)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_010320)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_011151)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_012151)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_013151)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_014152)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_015152)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_015634)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_015645)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_020154)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260105_020644)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_020649)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_021154)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_022154)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_023155)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_024155)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_025157)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_025921)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_025932)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_030157)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_030957)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_031002)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260105_031157)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_032158)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_033158)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_034200)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_035200)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_040200)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_040351)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_040402)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_041108)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_041112)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_041201)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_042201)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_043201)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_044202)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_045202)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_050204)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_050815)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_050825)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_051204)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_051600)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_051604)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_052205)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_053205)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_054206)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_055207)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_060207)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_060919)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_060930)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5724 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.5903 base_min=0.8630 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5724 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260105_061209)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_061903)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4044<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_061908)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_062210)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_063211)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_064211)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |

## Autopilot Promotion (20260105_065211)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.8816 cand=1.0539,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7759<-0.30) |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.8816 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.7092<-0.30),positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.8816 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.9485<-0.30),positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.8816 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4597<-0.30),positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.8816 cand=1.3488,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.3176<-0.30) |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.8816 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.5402<-0.30),positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.8816 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],oos_sharpe_min_too_low(-1.4263<-0.30),positive_folds_active_low |
