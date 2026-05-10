# AUB 최종 상속 문서 (V3 → V5)

**버전**: V5 (Crisis-Only Hedge Hybrid)
**생성일**: 2025-12-31
**작성자**: Manus AI

---

## 1. 최종 결론: Champion V5

**Champion V5**는 V3(항상 헤지)의 **방어력**과 V4(항상 무헤지)의 **평시 성과**를 결합한 하이브리드 전략으로, 두 버전의 단점을 모두 보완하여 최종 챔피언으로 확정되었습니다.

| 항목 | 값 |
|------|-----|
| **hedge_mode** | `crisis_only` |
| **hedge_weight_on** | 0.10 (10%) |
| **hedge_enter_days** | 1 (진입 확인) |
| **hedge_exit_days** | 3 (탈출 확인) |

---

## 2. 진화 과정 (V3 → V4 → V5)

### 2.1. 문제 정의: V3의 최악 Fold (-0.2508)

- **현상**: Champion V3의 20개 Fold 중 **Fold 16**에서 Sharpe **-0.2508** 기록.
- **초기 가설**: ICIR v4의 MR(Mean Reversion) 비중이 49.7%까지 과집중된 것이 원인.

### 2.2. 가설 검증 ①: MR 비중 제한 (실패)

- **실험**: MR 비중 상한(cap)을 0.45, 0.40, 0.35로 설정하여 테스트.
- **결과**: MR 비중은 제한되었으나, **Fold 16의 Sharpe는 개선되지 않음**.
- **결론**: MR 과집중은 ‘증상’일 뿐, 근본 원인이 아님.

### 2.3. 가설 검증 ②: 헤지 OFF (V4의 탄생)

- **실험**: 헤지를 끄는 `hedge_off` (V4) 테스트.
- **결과**: **Fold 16 Sharpe가 -0.25 → -0.01로 96% 개선**. Lockbox Mean/Min도 모두 상승.
- **결론**: Fold 16 악화의 **주범은 ‘헤지 손실’**이었음. V4가 새로운 챔피언 후보로 부상.

### 2.4. 스트레스 테스트: V4의 약점 발견

- **실험**: V4를 2020년 크래시(Fold 14) 구간에서 테스트.
- **결과**: V4의 MDD가 **-12.4%**로, V3(-5.4%) 대비 **2배 이상 악화**.
- **결론**: 헤지는 평시에는 손실을 유발하지만, **급락장에서는 필수적인 방어 기제**.

### 2.5. 최종 해결책: V5 (Crisis-Only Hedge)

- **실험**: 평시에는 헤지를 끄고(V4), 위기 상황(Crisis/Risk-Off)에서만 헤지를 켜는(V3) 하이브리드 모델(V5) 개발.
- **결과**: V5는 **Fold 16을 양수(+)로 전환**하고, **2020년 MDD를 -10.0%로 방어**하며, **Lockbox Min을 개선**하는 최적의 균형점을 찾음.
- **안정화**: 헤지 on/off가 너무 잦아지는 것을 막기 위해 **히스테리시스(진입 1일, 탈출 3일)** 로직 추가.

---

## 3. 최종 성능 비교

| 설정 | Tuning Mean | Tuning Min | Lockbox Mean | Lockbox Min | 2020 MDD |
|------|-------------|------------|--------------|-------------|----------|
| V3 (Always On) | 1.9253 | -0.2508 | 1.8437 | 1.1937 | **-5.4%** |
| V4 (Always Off) | 1.8833 | -0.0090 | **1.8747** | 1.3213 | -12.4% |
| **V5 (Hybrid)** | 1.7618 | **+0.0508** | 1.7927 | **1.3469** | **-10.0%** |

---

## 4. 실행 커맨드 (V5)

```bash
cd /home/ubuntu/AUB && python3 baseline/ares_phase2b_v661_addon_engine_v3_integration.py \
  --db /home/ubuntu/ares_x_v11_0.db \
  --policy_json baseline/v661_compat_policy_notrade_only.json \
  --cost_bps 4 --per_asset_cap 0.1 --rebal_period 15 --top_k 10 --n_folds 20 \
  --enable_cb 1 --enable_throttle 1 --enable_compat 1 --enable_no_trade 1 --enable_severity 0 \
  --enable_icir_v4 1 --enable_regime15 1 --enable_vix_sizing 1 \
  --regime15_band_mult_crisis 0.86 --regime15_band_mult_risk_off 0.78 \
  --enable_hedge 1 --hedge_mode crisis_only --hedge_weight_on 0.10 \
  --hedge_enter_days 1 --hedge_exit_days 3 \
  --output artifacts/champion_v5.json
```

---

## 5. Shadow 관측치 및 다음 단계

### Shadow 관측치 (V3 vs V5 병행)

1. **regime15**: risk_on/off/transition/crisis 카운트
2. **vix_sizing**: scale_avg, clamp_events
3. **icir_v4**: applied_count, effect_avg, weights_last
4. **비용**: pnl_cost_sum
5. **노출/헤지**: avg_exposure / avg_hedge
6. **최대 손실일**: MDD 구간에서 반응
7. **hedge_on_days**: 헤지가 켜진 총 일수
8. **hedge_switch_count**: 헤지 on/off 전환 횟수

### 다음 단계

1. **Shadow 모드 1-2주**: V3 vs V5 병행 모니터링 후 최종 결정.
2. **INCONCLUSIVE 모듈 해결**: `rate_shock_gate`, `macro_gate` 데이터/피처 구현 후 재평가.
3. **정식 배포**: Shadow 통과 후 V5로 전환.

## Candidate Promotion (20251231_032633) (DROP=0.03)
Base=baseline/champion_v5_20251231.json | fold14_mdd=-0.09967681767616697 | lock_mean=1.7927214375260865 | lock_min=1.3469234262212157 | worst_mdd=-0.11181278318368602

|candidate|fold14_mdd|lock_mean|lock_min|worst_mdd|dest|
|---|---:|---:|---:|---:|---|
| V5_hybrid_A.json | -0.09967681767616697 | 1.788797816027686 | 1.3469234262212157 | -0.11181278318368602 | baseline/candidate_20251231_032633_V5_hybrid_A.json |
| V5_hysteresis.json | -0.09967681767616697 | 1.7927214375260865 | 1.3469234262212157 | -0.11181278318368602 | baseline/candidate_20251231_032633_V5_hysteresis.json |

## Autopilot Promotion (20251231_054005)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_weight_015 | FAIL | 1.6889 | 0.0908 | 1.6903 | 1.3175 | -8.73% | -10.37% | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hedge_weight_020 | FAIL | 1.6092 | 0.0866 | 1.5853 | 0.9807 | -7.49% | -9.57% | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | baseline/candidate_20251231_054005_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | baseline/candidate_20251231_054005_exp_hedge_exit_7.json | - |
| exp_regime_riskoff_070 | FAIL | 1.8043 | 0.0503 | 1.7633 | 1.2489 | -9.97% | -11.22% | - | lockbox_min_worse,worst_mdd_worse |
| exp_regime_riskoff_085 | FAIL | 1.7260 | 0.0450 | 1.7060 | 1.1609 | -9.97% | -11.26% | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse |

## Autopilot Promotion (20251231_100850)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_rate_shock_shadow | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |
| exp_rate_shock_apply | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |

## Autopilot Promotion (20251231_101928)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_rate_shock_shadow | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |
| exp_rate_shock_apply | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |

## Autopilot Promotion (20251231_102236)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_rate_shock_shadow | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |
| exp_rate_shock_apply | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |

## Autopilot Promotion (20251231_102425)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_rate_shock_shadow | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |
| exp_rate_shock_apply | INCONCLUSIVE | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | - | rate_shock_gate_triggers<10 |

## Autopilot Promotion (20251231_102531)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_rate_shock_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_102531_exp_rate_shock_shadow.json | - |
| exp_rate_shock_apply | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_102531_exp_rate_shock_apply.json | - |

## Autopilot Promotion (20251231_152102)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_152102_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_152102_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_152102_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_162949)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_162949_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_162949_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_162949_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_163542)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_163542_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_163542_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_163542_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_164134)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_164134_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_164134_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_164134_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_164727)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_164727_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_164727_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_164727_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_165319)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_165319_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_165319_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_165319_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_165912)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_165912_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_165912_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_165912_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_170505)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_170505_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_170505_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_170505_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_171058)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_171058_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_171058_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_171058_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_171650)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_171650_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_171650_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_171650_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_172243)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_172243_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_172243_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_172243_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_172835)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_172835_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_172835_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_172835_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_173428)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_173428_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_173428_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_173428_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_174021)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_174021_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_174021_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_174021_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_174613)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_174613_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_174613_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_174613_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_175206)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_175206_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_175206_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_175206_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_175759)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_175759_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_175759_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_175759_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_180351)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_180351_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_180351_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_180351_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_180944)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_180944_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_180944_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_180944_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_181539)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_181539_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_181539_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_181539_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_182132)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_182132_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_182132_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_182132_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_182725)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_182725_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_182725_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_182725_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_183317)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_183317_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_183317_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_183317_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_183909)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_183909_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_183909_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_183909_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_184502)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_184502_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_184502_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_184502_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_185054)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_185054_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_185054_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_185054_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_185647)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_185647_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_185647_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_185647_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_190239)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_190239_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_190239_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_190239_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_190832)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_190832_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_190832_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_190832_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_191425)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_191425_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_191425_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_191425_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_192017)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_192017_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_192017_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_192017_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_192610)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_192610_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_192610_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_192610_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_193202)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_193202_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_193202_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_193202_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_193755)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_193755_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_193755_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_193755_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_194347)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_194347_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_194347_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_194347_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_194940)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_194940_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_194940_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_194940_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_195533)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_195533_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_195533_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_195533_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_200125)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_200125_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_200125_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_200125_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_200717)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_200717_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_200717_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_200717_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_201310)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_201310_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_201310_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_201310_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_201903)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_201903_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_201903_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_201903_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_202456)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_202456_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_202456_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_202456_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_203048)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_203048_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_203048_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_203048_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_203641)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_203641_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_203641_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_203641_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_204242)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_204242_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_204242_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_204242_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_204834)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_204834_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_204834_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_204834_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_205427)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_205427_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_205427_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_205427_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_210020)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210020_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210020_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210020_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_210019)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210019_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210019_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210019_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_210612)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210612_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210612_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_210612_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_211204)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_211204_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_211204_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_211204_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_211757)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_211757_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_211757_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_211757_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_212349)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_212349_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_212349_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_212349_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_212942)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_212942_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_212942_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_212942_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_213535)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_213535_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_213535_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_213535_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_214127)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214127_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214127_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214127_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_214619)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214619_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214619_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214619_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_214950)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214950_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214950_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_214950_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_215007)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215007_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215007_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215007_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_215637)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215637_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215637_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215637_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |

## Autopilot Promotion (20251231_215748)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215748_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215748_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215748_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_215859)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215859_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215859_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_215859_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |

## Autopilot Promotion (20251231_220039)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220039_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220039_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220039_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |

## Autopilot Promotion (20251231_220227)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220227_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220227_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220227_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220227_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220227_exp_finetune_v6.json | - |

## Autopilot Promotion (20251231_220523)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220523_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220523_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220523_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220523_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220523_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_220523_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_221328)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_221328_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_221328_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_221328_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_221328_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_221328_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_221328_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_222127)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222127_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222127_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222127_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222127_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222127_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222127_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_222922)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222922_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222922_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222922_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222922_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222922_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_222922_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_223734)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_223734_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_223734_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_223734_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_223734_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_223734_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_223734_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_224544)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_224544_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_224544_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_224544_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_224544_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_224544_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_224544_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_225347)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_225347_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_225347_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_225347_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_225347_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_225347_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_225347_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_230156)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_230156_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_230156_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_230156_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_230156_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_230156_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_230156_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_231013)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231013_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231013_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231013_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231013_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231013_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231013_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_231819)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231819_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231819_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231819_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231819_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231819_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_231819_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_232629)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_232629_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_232629_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_232629_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_232629_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_232629_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_232629_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_233447)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_233447_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_233447_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_233447_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_233447_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_233447_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_233447_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_234249)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_234249_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_234249_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_234249_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_234249_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_234249_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_234249_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_235101)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235101_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235101_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235101_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235101_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235101_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235101_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20251231_235903)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235903_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235903_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235903_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235903_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235903_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20251231_235903_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_000704)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_000704_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_000704_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_000704_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_000704_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_000704_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_000704_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_001511)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_001511_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_001511_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_001511_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_001511_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_001511_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_001511_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_002335)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_002335_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_002335_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_002335_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_002335_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_002335_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_002335_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_003135)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003135_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003135_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003135_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003135_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003135_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003135_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_003941)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003941_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003941_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003941_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003941_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003941_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_003941_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_004801)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_004801_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_004801_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_004801_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_004801_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_004801_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_004801_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_005604)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_005604_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_005604_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_005604_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_005604_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_005604_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_005604_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_010354)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_010354_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_010354_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_010354_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_010354_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_010354_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_010354_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_011211)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_011211_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_011211_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_011211_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_011211_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_011211_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_011211_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_012005)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_012005_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_012005_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_012005_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_012005_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_012005_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_012005_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_033353)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_mrd_only | FAIL | 1.5932 | 0.0100 | 1.7547 | 1.1643 | -14.12% | -16.51% | 0.0345 | - | lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_ars_only | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_ars_only.json | - |
| exp_fsa_only | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_fsa_only.json | - |
| exp_mrd_ars | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_mrd_ars.json | - |
| exp_mrd_fsa | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_mrd_fsa.json | - |
| exp_mrd_ars_fsa_full | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_033353_exp_mrd_ars_fsa_full.json | - |

## Autopilot Promotion (20260101_033549)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_034851)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_hedge_exit_3 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_034851_exp_hedge_exit_3.json | - |
| exp_hedge_exit_5 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_034851_exp_hedge_exit_5.json | - |
| exp_hedge_exit_7 | PASS | 1.7579 | 0.0508 | 1.7803 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_034851_exp_hedge_exit_7.json | - |
| exp_hedge_enter_2 | FAIL | 1.7602 | 0.0291 | 1.7917 | 1.3469 | -9.97% | -11.35% | 0.0242 | - | worst_mdd_worse |
| exp_fold16_fix_v2 | FAIL | 1.5892 | 0.2071 | 1.4743 | 0.7172 | -7.92% | -8.62% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_sharpe3_integrated | FAIL | 1.4855 | -0.2630 | 1.4557 | 0.7017 | -11.04% | -11.04% | 0.0345 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_rebal_5d_hedge_12 | FAIL | 1.6321 | 0.0672 | 1.6035 | 1.0308 | -8.67% | -10.86% | 0.0411 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v1 | FAIL | 1.5630 | 0.2570 | 1.4062 | 0.5797 | -14.20% | -14.20% | 0.0283 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_hybrid_v2 | FAIL | 1.5587 | 0.2029 | 1.4152 | 0.8854 | -9.34% | -9.34% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_hybrid_v3 | FAIL | 1.5777 | 0.0808 | 1.4886 | 0.8711 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v1 | FAIL | 1.5000 | -0.1219 | 1.5888 | 0.8186 | -11.49% | -13.07% | 0.0249 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_finetune_v2 | FAIL | 1.7797 | 0.1500 | 1.3628 | 0.2457 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v3 | FAIL | 1.5749 | 0.0808 | 1.4869 | 0.8644 | -10.71% | -10.71% | 0.0260 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse |
| exp_finetune_v4 | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_034851_exp_finetune_v4.json | - |
| exp_finetune_v5 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_finetune_v6 | PASS | 1.7621 | 0.0508 | 1.7937 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_034851_exp_finetune_v6.json | - |
| exp_lockbox_v1 | PASS | 1.7593 | 0.0508 | 1.7928 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260101_034851_exp_lockbox_v1.json | - |
| exp_lockbox_v2 | FAIL | 1.7476 | 0.0585 | 1.7749 | 1.3415 | -9.72% | -11.02% | 0.0242 | - | lockbox_min_worse |
| exp_lockbox_v3 | FAIL | 1.7786 | 0.1500 | 1.3631 | 0.2470 | -9.50% | -10.72% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse |
| exp_mrd_only | FAIL | 1.7963 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_ars_only | PASS | 1.6676 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0239 | baseline/candidate_20260101_034851_exp_ars_only.json | - |
| exp_fsa_only | FAIL | 1.7963 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_mrd_ars | PASS | 1.6676 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0239 | baseline/candidate_20260101_034851_exp_mrd_ars.json | - |
| exp_mrd_fsa | FAIL | 1.7963 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse |
| exp_mrd_ars_fsa_full | PASS | 1.6676 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0239 | baseline/candidate_20260101_034851_exp_mrd_ars_fsa_full.json | - |

## Autopilot Promotion (20260101_043547)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_100747)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 0.9152 | -1.8359 | 1.6085 | 0.1107 | -5.56% | -12.88% | 0.0695 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse |

## Autopilot Promotion (20260101_101003)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 0.9152 | -1.8359 | 1.6085 | 0.1107 | -5.56% | -12.88% | 0.0695 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse |

## Autopilot Promotion (20260101_101112)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0251 | -1.7383 | 1.6209 | 0.1115 | -5.56% | -8.85% | 0.0722 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_103008)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0552 | -1.3492 | 1.3388 | -0.0534 | -4.95% | -8.24% | 0.0590 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_103101)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0552 | -1.3492 | 1.3388 | -0.0534 | -4.95% | -8.24% | 0.0590 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260101_104603)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.0824 | -1.3492 | 1.3388 | -0.0534 | -4.95% | -8.24% | 0.0589 | baseline/candidate_20260101_104603_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_110255)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.0824 | -1.3492 | 1.3388 | -0.0534 | -4.95% | -8.24% | 0.0589 | baseline/candidate_20260101_110255_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_110258)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_111646)
Base=baseline/champion_v5_20251231.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.3348 | -0.4531 | 1.9868 | 1.1420 | -0.70% | -10.58% | 0.0190 | - | lockbox_min_worse |
| exp_fsa_only | FAIL | 2.3164 | -0.9071 | 1.9911 | 1.0992 | -0.70% | -7.48% | 0.0151 | - | lockbox_min_worse |
| exp_mrd_only | FAIL | 2.3348 | -0.4531 | 1.9868 | 1.1420 | -0.70% | -10.58% | 0.0190 | - | lockbox_min_worse |
| exp_mrd_fsa | FAIL | 2.3164 | -0.9071 | 1.9911 | 1.0992 | -0.70% | -7.48% | 0.0151 | - | lockbox_min_worse |

## Autopilot Promotion (20260101_112934)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.3348 | -0.4531 | 1.9868 | 1.1420 | -0.70% | -10.58% | 0.0190 | - | lockbox_min_worse |
| exp_fsa_only | FAIL | 2.3157 | -0.9062 | 1.9808 | 1.0992 | -0.70% | -7.48% | 0.0149 | - | lockbox_min_worse |
| exp_mrd_only | FAIL | 2.3348 | -0.4531 | 1.9868 | 1.1420 | -0.70% | -10.58% | 0.0190 | - | lockbox_min_worse |
| exp_mrd_fsa | FAIL | 2.3157 | -0.9062 | 1.9808 | 1.0992 | -0.70% | -7.48% | 0.0149 | - | lockbox_min_worse |

## Autopilot Promotion (20260101_120235)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.3348 | -0.4531 | 1.9868 | 1.1420 | -0.70% | -10.58% | 0.0190 | - | lockbox_min_worse |
| exp_fsa_only | FAIL | 2.3157 | -0.9062 | 1.9808 | 1.0992 | -0.70% | -7.48% | 0.0149 | - | lockbox_min_worse |
| exp_mrd_only | FAIL | 2.3348 | -0.4531 | 1.9868 | 1.1420 | -0.70% | -10.58% | 0.0190 | - | lockbox_min_worse |
| exp_mrd_fsa | FAIL | 2.3157 | -0.9062 | 1.9808 | 1.0992 | -0.70% | -7.48% | 0.0149 | - | lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 2.3157 | -0.9062 | 1.9808 | 1.0992 | -0.70% | -7.48% | 0.0149 | - | lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 2.3157 | -0.9062 | 1.9808 | 1.0992 | -0.70% | -7.48% | 0.0149 | - | lockbox_min_worse |

## Autopilot Promotion (20260101_120838)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.0824 | -1.3492 | 1.3388 | -0.0534 | -4.95% | -8.24% | 0.0589 | baseline/candidate_20260101_120838_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_120841)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_131521)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.1125 | -1.3492 | 1.3221 | -0.0777 | -4.95% | -8.24% | 0.0590 | - | lockbox_min_worse |

## Autopilot Promotion (20260101_131524)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_142025)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.1125 | -1.3492 | 1.3221 | -0.0777 | -4.95% | -8.24% | 0.0590 | - | lockbox_min_worse |

## Autopilot Promotion (20260101_142028)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_153135)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.1125 | -1.3492 | 1.3221 | -0.0777 | -4.95% | -8.24% | 0.0590 | - | lockbox_min_worse |

## Autopilot Promotion (20260101_153138)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_163227)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.1614 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -8.09% | 0.0571 | baseline/candidate_20260101_163227_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_163230)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_173801)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4147 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0482 | baseline/candidate_20260101_173801_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_173804)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_184337)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4147 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0482 | baseline/candidate_20260101_184337_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_184341)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_194908)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4147 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0482 | baseline/candidate_20260101_194908_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_194911)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_205343)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4147 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0482 | baseline/candidate_20260101_205343_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_205346)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_210020)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4147 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0482 | baseline/candidate_20260101_210020_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_215918)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4049 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0483 | baseline/candidate_20260101_215918_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_215921)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260101_230410)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4049 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0483 | baseline/candidate_20260101_230410_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260101_230413)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_001102)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.4049 | -1.4968 | 1.5542 | 0.3340 | -4.15% | -4.83% | 0.0483 | baseline/candidate_20260102_001102_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260102_001105)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_011728)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | PASS | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | baseline/candidate_20260102_011728_exp_mrd_fsa.json | - |

## Autopilot Promotion (20260102_011732)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_022243)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_022246)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_032927)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_032930)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_043436)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_043439)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_054016)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_054019)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_064627)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_064630)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_075240)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_075243)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_082211)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse |
| consensus_20260102_082210_3 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse |
| consensus_20260102_082210_5 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse |

## Autopilot Promotion (20260102_084021)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7975,cand_lock_min=0.7630,lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7205,cand_lock_min=0.6611,lockbox_mean_drop,lockbox_min_worse |
| exp_cost_model_5bp_turnover_cap | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_cost_model_10bp_turnover_cap | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |

## Autopilot Promotion (20260102_085101)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7975,cand_lock_min=0.7630,lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7205,cand_lock_min=0.6611,lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_085916)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | base_lock_mean=1.7925,base_lock_min=1.1337,cand_lock_mean=1.5186,cand_lock_min=0.3340,lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_085844)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7975,cand_lock_min=0.7630,lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7205,cand_lock_min=0.6611,lockbox_mean_drop,lockbox_min_worse |
| exp_cost_sweep_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_fix_rate_macro_gates_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |

## Autopilot Promotion (20260102_085919)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7975,cand_lock_min=0.7630,lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7205,cand_lock_min=0.6611,lockbox_mean_drop,lockbox_min_worse |
| exp_cost_sweep_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_fix_rate_macro_gates_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |

## Autopilot Promotion (20260102_090751)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.8437,cand_lock_min=1.1937,lockbox_min_worse |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=2.6622,cand_lock_min=0.9804,lockbox_min_worse |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7939,cand_lock_min=0.8452,lockbox_min_worse |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7975,cand_lock_min=0.7630,lockbox_min_worse |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | base_lock_mean=1.7927,base_lock_min=1.3469,cand_lock_mean=1.7205,cand_lock_min=0.6611,lockbox_mean_drop,lockbox_min_worse |

## Autopilot Promotion (20260102_091711)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_fix_rate_macro_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_fix_rate_macro_v2_strict | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_093236)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_094913)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| rebalance_log_dualformat_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| rebalance_pinpoint_path_guard | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_094938)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5186 cand_min=0.3340 |
| rebalance_turnover_cost_breakdown | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| cost_stress_0_15_30bp | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| cost_stress_1x_2x | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| leakage_sentinel_shift_p1_p3 | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| leakage_sentinel_lag_m1_m3 | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| universe_asof_lock | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| defensive_hedge_band_tune_v1 | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| rate_shock_gate_features_v1 | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| macro_gate_calendar_match_v1 | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| P0_log_chain_fix__jsonl_and_mdd_required | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| P0_log_chain_fix__champion_gate_restore | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |

## Autopilot Promotion (20260102_095357)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5186 cand_min=0.3340 |
| exp_yield_curve_momtilt | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| exp_cs_lowvol_60d | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |
| exp_rate_shock_gate | FAIL | 1.0460 | -1.3492 | 1.3295 | -0.0534 | -4.95% | -9.02% | 0.0563 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3295 cand_min=-0.0534 |

## Autopilot Promotion (20260102_095757)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_vol_cond_mom_20d | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_vix_momentum | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_102926)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_short_reversal_3d | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_103814)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_rate_shock_features_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_105355)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_110212)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_yield_momtilt | FAIL | 1.9135 | -0.0825 | 1.8334 | 1.1884 | -5.44% | -9.00% | 0.0258 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8334 cand_min=1.1884 |
| exp_yield_curve_regime | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_110617)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5186 cand_min=0.3340 |
| exp_cs_momentum_20d | FAIL | 1.0548 | -1.2765 | 1.3628 | -0.0142 | -4.81% | -8.98% | 0.0539 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3628 cand_min=-0.0142 |
| exp_vol_cond_momentum | FAIL | 1.0535 | -1.4089 | 1.3113 | -0.0647 | -5.00% | -8.86% | 0.0550 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3113 cand_min=-0.0647 |
| exp_cs_reversal_5d | FAIL | 0.9846 | -1.6772 | 1.3505 | -0.0962 | -5.52% | -9.52% | 0.0603 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3505 cand_min=-0.0962 |
| exp_cs_lowvol_60d | FAIL | 1.0363 | -1.3073 | 1.3323 | -0.0796 | -5.02% | -9.24% | 0.0573 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3323 cand_min=-0.0796 |
| exp_dd_recovery | FAIL | 1.0406 | -1.3156 | 1.3332 | -0.0478 | -5.00% | -9.35% | 0.0568 | - | lockbox_mean_drop,lockbox_min_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3332 cand_min=-0.0478 |

## Autopilot Promotion (20260102_110757)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5186 cand_min=0.3340 |

## Autopilot Promotion (20260102_110800)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_111031)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_111910)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_rate_shock_features_d2y_bp_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_112906)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5186 cand_min=0.3340 |
| exp_cs_reversal_5d_t003_clip1 | FAIL | 1.0288 | -1.5070 | 1.3304 | -0.1018 | -5.24% | -9.05% | 0.0581 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3304 cand_min=-0.1018 |
| exp_cs_momentum_20d_t003_clip1 | FAIL | 1.0638 | -1.3504 | 1.3153 | -0.1158 | -4.94% | -8.86% | 0.0553 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3153 cand_min=-0.1158 |
| exp_cs_lowvol_60d_t005_clip1 | FAIL | 1.0521 | -1.3242 | 1.3177 | -0.0937 | -4.98% | -8.99% | 0.0572 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.3177 cand_min=-0.0937 |

## Autopilot Promotion (20260102_113640)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_114510)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_rate_shock_features_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_115338)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_120259)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_cs_momentum_20d_t003_clip1 | FAIL | 1.6529 | -0.0242 | 1.4966 | 1.0551 | -16.71% | -16.71% | 0.0273 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4966 cand_min=1.0551 |
| exp_cs_reversal_5d_t003_clip1 | FAIL | 1.6383 | 0.0040 | 1.4022 | 0.9048 | -18.07% | -18.07% | 0.0284 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4022 cand_min=0.9048 |
| exp_cs_lowvol_60d_t005_clip1 | FAIL | 1.6314 | -0.0757 | 1.4756 | 1.0331 | -17.65% | -17.65% | 0.0277 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4756 cand_min=1.0331 |

## Autopilot Promotion (20260102_120256)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_120425)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_cs_momentum_20d_t002_clip1 | FAIL | 1.6290 | -0.0200 | 1.4972 | 1.0022 | -17.83% | -17.83% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4972 cand_min=1.0022 |
| exp_cs_reversal_5d_t002_clip1 | FAIL | 1.6311 | -0.0454 | 1.4178 | 0.9086 | -18.02% | -18.02% | 0.0282 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4178 cand_min=0.9086 |
| exp_cs_lowvol_60d_t002_clip1 | FAIL | 1.6370 | -0.0630 | 1.4623 | 0.9868 | -17.83% | -17.83% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4623 cand_min=0.9868 |

## Autopilot Promotion (20260102_121008)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.3676 | -1.4174 | 1.5186 | 0.3340 | -4.10% | -4.23% | 0.0494 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5186 cand_min=0.3340 |

## Autopilot Promotion (20260102_121011)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260102_121206)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |
| exp_rate_shock_d2y_features_v1 | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |

## Autopilot Promotion (20260102_121249)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_cs_momentum_t002_regime_cond | FAIL | 1.6290 | -0.0200 | 1.4972 | 1.0022 | -17.83% | -17.83% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4972 cand_min=1.0022 |
| exp_cs_lowvol_t002_regime_cond | FAIL | 1.6370 | -0.0630 | 1.4623 | 0.9868 | -17.83% | -17.83% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4623 cand_min=0.9868 |

## Autopilot Promotion (20260102_122111)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_122202)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_yield_curve_regime_t015 | FAIL | 1.6311 | 0.0292 | 1.4741 | 1.0437 | -15.62% | -15.62% | 0.0279 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4741 cand_min=1.0437 |
| exp_yield_curve_regime_t010 | FAIL | 1.6404 | 0.0372 | 1.4716 | 1.0413 | -15.63% | -15.63% | 0.0278 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4716 cand_min=1.0413 |
| exp_yield_curve_momtilt_t010 | FAIL | 1.6128 | 0.0180 | 1.4761 | 1.0799 | -17.93% | -17.93% | 0.0290 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4761 cand_min=1.0799 |

## Autopilot Promotion (20260102_122936)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.9253 | -0.2508 | 1.8437 | 1.1937 | -5.44% | -10.58% | 0.0242 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.8437 cand_min=1.1937 |
| exp_macro_alpha_apply_scale | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_fsa_only | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_mrd_only | FAIL | 2.0454 | -0.2771 | 2.6622 | 0.9804 | -0.69% | -7.22% | 0.0183 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=2.6622 cand_min=0.9804 |
| exp_mrd_fsa | FAIL | 1.7752 | -0.2844 | 1.7939 | 0.8452 | -3.95% | -7.00% | 0.0197 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7939 cand_min=0.8452 |
| exp_fsa_only_scale_080 | FAIL | 1.6990 | -0.2844 | 1.7975 | 0.7630 | -3.95% | -7.12% | 0.0199 | - | lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7975 cand_min=0.7630 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.6768 | -0.2844 | 1.7205 | 0.6611 | -3.95% | -7.13% | 0.0202 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7205 cand_min=0.6611 |

## Autopilot Promotion (20260102_123225)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_vol_cond_momentum_t010 | FAIL | 1.6471 | -0.0193 | 1.4928 | 1.0471 | -17.93% | -17.93% | 0.0272 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4928 cand_min=1.0471 |
| exp_dd_recovery_t010 | FAIL | 1.6154 | -0.0306 | 1.4494 | 0.9794 | -17.93% | -17.93% | 0.0273 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4494 cand_min=0.9794 |

## Autopilot Promotion (20260102_123347)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_vix_carry_w015 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_ts_momentum_w020 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_vix_carry_ts_mom_combo | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |

## Autopilot Promotion (20260102_124601)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_124601_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_125339)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_vix_carry_w015 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_ts_momentum_w020 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_combo_vix_tsm | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |

## Autopilot Promotion (20260102_125437)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_125437_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_130047)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_vix_carry_w015 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_ts_momentum_w020 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_combo_vix_tsm | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |

## Autopilot Promotion (20260102_130327)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_130327_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_131143)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_131143_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_131229)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_131229_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_131319)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_vix_carry_w015 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_ts_momentum_w020 | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |
| exp_combo_vix_tsm | FAIL | 1.6228 | -0.0309 | 1.5310 | 0.9887 | -17.93% | -17.93% | 0.0275 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.5310 cand_min=0.9887 |

## Autopilot Promotion (20260102_131958)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_131958_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_133716)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_133716_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_134530)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_134530_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_141844)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_141844_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_142730)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_142730_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_144428)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_144428_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_145256)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_145256_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_150100)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_150100_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_151701)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_151701_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_152056)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_152056_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260102_152557)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | PASS | 1.7618 | 0.0508 | 1.7927 | 1.3469 | -9.97% | -11.18% | 0.0242 | baseline/candidate_20260102_152557_exp_macro_alpha_shadow.json | - |
| exp_macro_alpha_apply_scale | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_only | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_mrd_fsa | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_080 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7960 | 0.0003 | 1.7284 | 1.2587 | -10.04% | -11.40% | 0.0239 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7284 cand_min=1.2587 |

## Autopilot Promotion (20260103_000357)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_013553)
Base=baseline/champion_track_baseline.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.7183 cand_min=1.2927 |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.6785 cand_min=1.1971 |
| exp_fsa_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.6785 cand_min=1.1971 |
| exp_mrd_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.6785 cand_min=1.1971 |
| exp_mrd_fsa | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.6785 cand_min=1.1971 |
| exp_fsa_only_scale_080 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.6785 cand_min=1.1971 |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=1.7927 base_min=1.3469 cand_mean=1.6785 cand_min=1.1971 |

## Autopilot Promotion (20260103_022201)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_033608)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_064745)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_085058)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_095509)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_102013)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_fsa | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_080 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_102054)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_fsa | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_080 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_105934)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_112204)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_fsa | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_080 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_112243)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_only | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_mrd_fsa | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_080 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |
| exp_fsa_only_scale_090_hedge_025 | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_120324)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_122356)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_122407)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_130724)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_132555)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_132607)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_141122)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_142751)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_142802)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_151400)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_152857)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_152909)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_161814)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_163844)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_172244)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_172249)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_173349)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_173401)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_182725)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_182730)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_183427)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_183440)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_193139)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_193144)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_193600)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_193613)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_203611)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_203616)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260103_204002)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_204015)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_210000)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_213845)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_213850)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_214012)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_214025)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_224250)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_224256)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260103_224422)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_224434)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7350 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260103_231513)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_tail_guard_scale_0.6 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_scale_0.4 | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_21 | FAIL | 1.0391 | -1.7759 | 1.0582 | -0.5549 | -4.69% | -5.55% | 0.0376 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.0582 cand_min=-0.5549,lock_mean_expw: base=1.5213 cand=1.0539,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_8 | FAIL | 0.8551 | -1.7092 | 1.3701 | 0.0230 | -5.35% | -9.29% | 0.0709 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3701 cand_min=0.0230,lock_mean_expw: base=1.5213 cand=1.3634,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_relax_no_trade | FAIL | 0.8426 | -1.9485 | 1.2410 | -0.1384 | -5.68% | -8.81% | 0.1156 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.2410 cand_min=-0.1384,lock_mean_expw: base=1.5213 cand=1.2286,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_tail_guard_0.6_cbfull | FAIL | 1.0502 | -1.4597 | 1.4104 | -0.0611 | -5.10% | -9.29% | 0.0570 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4104 cand_min=-0.0611,lock_mean_expw: base=1.5213 cand=1.4237,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_rebal_period_30 | FAIL | 1.1433 | -1.3176 | 1.3113 | 0.2494 | -3.72% | -4.92% | 0.0313 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3113 cand_min=0.2494,lock_mean_expw: base=1.5213 cand=1.3488,lock_lowexp_hi_sharpe_folds=[] |
| exp_top_k_12 | FAIL | 1.0078 | -1.5402 | 1.3800 | -0.0470 | -4.84% | -9.42% | 0.0484 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.3800 cand_min=-0.0470,lock_mean_expw: base=1.5213 cand=1.3819,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |
| exp_per_asset_cap_008 | FAIL | 1.0635 | -1.4263 | 1.4213 | 0.0166 | -4.90% | -9.09% | 0.0552 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4213 cand_min=0.0166,lock_mean_expw: base=1.5213 cand=1.4304,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_025928)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_035717)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_035755)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_035833)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_040233)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_040243)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_040444)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_040523)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_040601)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_040809)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.5213 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_040814)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_041347)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_041425)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_041503)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_042114)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_042152)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_042231)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_042842)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_042920)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_042959)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_043610)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_043648)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_043726)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_alpha_01_mvo | FAIL | 1.4935 | 0.0000 | 1.6290 | 1.0095 | -10.25% | -11.61% | 0.0204 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6290 cand_min=1.0095,lock_mean_expw: base=1.5213 cand=1.5641,lock_lowexp_hi_sharpe_folds=[] |
| exp_alpha_02_pead | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_03_orchestrator | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_04_severity | FAIL | 1.3384 | -0.0202 | 1.4904 | 0.0000 | -10.57% | -11.06% | 0.0132 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4904 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6329,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_05_macro_gate | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_06_regime_v11 | FAIL | 1.4002 | 0.0000 | 1.4785 | 0.0000 | -10.57% | -11.25% | 0.0131 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4785 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6209,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_07_mrd | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |
| exp_alpha_08_impact | FAIL | 1.3599 | 0.0000 | 1.4905 | 0.0000 | -10.57% | -11.25% | 0.0136 | - | lockbox_mean_drop,lockbox_min_worse,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.4905 cand_min=0.0000,lock_mean_expw: base=1.5213 cand=1.6331,lock_lowexp_hi_sharpe_folds=[],inactive_folds_too_many |

## Autopilot Promotion (20260104_044500)
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

## Autopilot Promotion (20260104_044538)
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

## Autopilot Promotion (20260104_044617)
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

## Autopilot Promotion (20260104_045228)
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

## Autopilot Promotion (20260104_045306)
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

## Autopilot Promotion (20260104_045344)
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

## Autopilot Promotion (20260104_045955)
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

## Autopilot Promotion (20260104_050034)
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

## Autopilot Promotion (20260104_050112)
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

## Autopilot Promotion (20260104_050522)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_050532)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_050723)
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

## Autopilot Promotion (20260104_050802)
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

## Autopilot Promotion (20260104_050840)
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

## Autopilot Promotion (20260104_051035)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_051039)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_051612)
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

## Autopilot Promotion (20260104_051650)
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

## Autopilot Promotion (20260104_051729)
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

## Autopilot Promotion (20260104_052340)
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

## Autopilot Promotion (20260104_052418)
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

## Autopilot Promotion (20260104_052456)
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

## Autopilot Promotion (20260104_053108)
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

## Autopilot Promotion (20260104_053146)
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

## Autopilot Promotion (20260104_053224)
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

## Autopilot Promotion (20260104_053835)
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

## Autopilot Promotion (20260104_053913)
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

## Autopilot Promotion (20260104_053952)
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

## Autopilot Promotion (20260104_054719)
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

## Autopilot Promotion (20260104_054758)
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

## Autopilot Promotion (20260104_054836)
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

## Autopilot Promotion (20260104_055447)
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

## Autopilot Promotion (20260104_055525)
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

## Autopilot Promotion (20260104_055604)
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

## Autopilot Promotion (20260104_060215)
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

## Autopilot Promotion (20260104_060253)
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

## Autopilot Promotion (20260104_060331)
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

## Autopilot Promotion (20260104_060554)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_060605)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_060943)
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

## Autopilot Promotion (20260104_061021)
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

## Autopilot Promotion (20260104_061059)
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

## Autopilot Promotion (20260104_061336)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_061340)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_061913)
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

## Autopilot Promotion (20260104_061952)
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

## Autopilot Promotion (20260104_062030)
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

## Autopilot Promotion (20260104_062641)
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

## Autopilot Promotion (20260104_062720)
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

## Autopilot Promotion (20260104_062758)
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

## Autopilot Promotion (20260104_063409)
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

## Autopilot Promotion (20260104_063447)
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

## Autopilot Promotion (20260104_063526)
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

## Autopilot Promotion (20260104_064136)
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

## Autopilot Promotion (20260104_064215)
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

## Autopilot Promotion (20260104_064253)
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

## Autopilot Promotion (20260104_065009)
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

## Autopilot Promotion (20260104_065047)
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

## Autopilot Promotion (20260104_065125)
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

## Autopilot Promotion (20260104_065736)
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

## Autopilot Promotion (20260104_065815)
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

## Autopilot Promotion (20260104_065853)
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

## Autopilot Promotion (20260104_070504)
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

## Autopilot Promotion (20260104_070606)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_070543)
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

## Autopilot Promotion (20260104_070617)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_070621)
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

## Autopilot Promotion (20260104_071233)
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

## Autopilot Promotion (20260104_071311)
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

## Autopilot Promotion (20260104_071349)
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

## Autopilot Promotion (20260104_071549)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_071554)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_072126)
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

## Autopilot Promotion (20260104_072205)
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

## Autopilot Promotion (20260104_072243)
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

## Autopilot Promotion (20260104_072854)
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

## Autopilot Promotion (20260104_072933)
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

## Autopilot Promotion (20260104_073011)
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

## Autopilot Promotion (20260104_073622)
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

## Autopilot Promotion (20260104_073700)
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

## Autopilot Promotion (20260104_073739)
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

## Autopilot Promotion (20260104_074350)
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

## Autopilot Promotion (20260104_074428)
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

## Autopilot Promotion (20260104_074506)
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

## Autopilot Promotion (20260104_075255)
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

## Autopilot Promotion (20260104_075333)
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

## Autopilot Promotion (20260104_075411)
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

## Autopilot Promotion (20260104_080022)
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

## Autopilot Promotion (20260104_080100)
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

## Autopilot Promotion (20260104_080139)
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

## Autopilot Promotion (20260104_080750)
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

## Autopilot Promotion (20260104_080828)
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

## Autopilot Promotion (20260104_080916)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_080926)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_080906)
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

## Autopilot Promotion (20260104_081518)
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

## Autopilot Promotion (20260104_081556)
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

## Autopilot Promotion (20260104_081634)
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

## Autopilot Promotion (20260104_081848)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_081852)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_082425)
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

## Autopilot Promotion (20260104_082503)
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

## Autopilot Promotion (20260104_082542)
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

## Autopilot Promotion (20260104_083153)
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

## Autopilot Promotion (20260104_083231)
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

## Autopilot Promotion (20260104_083309)
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

## Autopilot Promotion (20260104_083920)
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

## Autopilot Promotion (20260104_083959)
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

## Autopilot Promotion (20260104_084037)
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

## Autopilot Promotion (20260104_084648)
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

## Autopilot Promotion (20260104_084726)
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

## Autopilot Promotion (20260104_084805)
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

## Autopilot Promotion (20260104_085555)
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

## Autopilot Promotion (20260104_085633)
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

## Autopilot Promotion (20260104_085711)
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

## Autopilot Promotion (20260104_090323)
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

## Autopilot Promotion (20260104_090401)
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

## Autopilot Promotion (20260104_090439)
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

## Autopilot Promotion (20260104_091040)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_091051)
Base=baseline/champion_prodstart_A_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_macro_alpha_shadow | FAIL | 1.6728 | -0.0009 | 1.7183 | 1.2927 | -10.69% | -11.49% | 0.0248 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.7183 cand_min=1.2927,lock_mean_expw: base=1.5213 cand=1.7714,lock_lowexp_hi_sharpe_folds=[] |
| exp_macro_alpha_apply_scale | FAIL | 1.7354 | -0.0807 | 1.6785 | 1.1971 | -10.80% | -11.75% | 0.0245 | - | lockbox_mean_drop,fold14_mdd_worse,worst_mdd_worse,lockbox_base_mean=4.0109 base_min=0.7355 cand_mean=1.6785 cand_min=1.1971,lock_mean_expw: base=1.5213 cand=1.7341,lock_lowexp_hi_sharpe_folds=[] |

## Autopilot Promotion (20260104_091050)
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

## Autopilot Promotion (20260104_091129)
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

## Autopilot Promotion (20260104_091207)
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

## Autopilot Promotion (20260104_091818)
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

## Autopilot Promotion (20260104_091856)
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

## Autopilot Promotion (20260104_091934)
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

## Autopilot Promotion (20260104_092141)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| exp_mrd_fsa | FAIL | 1.0199 | -1.4044 | 1.4713 | 0.1180 | -4.27% | -7.09% | 0.0449 | - | lockbox_mean_drop,lockbox_min_worse,lockbox_base_mean=1.7925 base_min=1.1337 cand_mean=1.4713 cand_min=0.1180,lock_mean_expw: base=1.8816 cand=1.3892,lock_lowexp_hi_sharpe_folds=[],positive_folds_active_low |

## Autopilot Promotion (20260104_092146)
Base=baseline/champion_v5_latest.json

|candidate|verdict|tuning_mean|tuning_min|lock_mean|lock_min|fold14_mdd|worst_mdd|cost|dest|reasons|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|

## Autopilot Promotion (20260104_092719)
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

## Autopilot Promotion (20260104_092757)
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

## Autopilot Promotion (20260104_092835)
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

## Autopilot Promotion (20260104_093446)
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

## Autopilot Promotion (20260104_093525)
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

## Autopilot Promotion (20260104_093604)
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

## Autopilot Promotion (20260104_094215)
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

## Autopilot Promotion (20260104_094253)
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

## Autopilot Promotion (20260104_094331)
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

## Autopilot Promotion (20260104_094942)
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

## Autopilot Promotion (20260104_095021)
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

## Autopilot Promotion (20260104_095059)
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

## Autopilot Promotion (20260104_095842)
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

## Autopilot Promotion (20260104_095920)
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

## Autopilot Promotion (20260104_095959)
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

## Autopilot Promotion (20260104_100610)
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
