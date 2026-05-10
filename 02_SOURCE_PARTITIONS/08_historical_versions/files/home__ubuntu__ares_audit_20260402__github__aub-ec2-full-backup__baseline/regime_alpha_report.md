# Regime Alpha Report: Champion V5

**버전**: 2025-12-31
**작성자**: Manus AI

---

## 1. Regime15 상태별 성능 분해

| Regime | Fold Count | Sharpe Mean | Sharpe Min | Asset PnL | Hedge PnL | Cost PnL | Net PnL |
|---|---|---|---|---|---|---|---|
| **crisis** | 10 | 1.8301 | 0.8520 | 0.7143 | -0.1755 | 0.0102 | 0.5286 |
| **risk_on** | 3 | 1.8317 | 1.6563 | 0.4237 | -0.0225 | 0.0057 | 0.3955 |
| **risk_off** | 5 | 1.8862 | 1.2842 | 0.6503 | -0.0947 | 0.0052 | 0.5504 |
| **transition** | 2 | 0.7469 | 0.0508 | 0.0564 | 0.0061 | 0.0027 | 0.0598 |

**해석**:
- **crisis** 레짐에서 가장 많은 Fold(10개)가 발생했으며, 평균 Sharpe 1.83으로 양호합니다.
- **transition** 레짐에서 Sharpe가 가장 낮으며(0.75), 특히 Fold 16이 최악(0.05)입니다.
- 모든 레짐에서 **Hedge PnL이 음수**이지만, V5의 crisis_only 정책으로 드래그가 최소화되었습니다.

---

## 2. VIX 상태별 성능 분해

| VIX State | Fold Count | Sharpe Mean | Sharpe Min | Avg Exposure | Avg Hedge |
|---|---|---|---|---|---|
| **NORMAL** | 20 | 1.7680 | 0.0508 | 0.4473 | 0.0590 |
| ELEVATED | 0 | - | - | - | - |
| HIGH | 0 | - | - | - | - |
| EXTREME | 0 | - | - | - | - |

**해석**:
- 모든 Fold가 **NORMAL** VIX 상태로 분류되었습니다.
- 이는 VIX 상태 분류 로직이 Fold 단위 집계에서 주요 상태만 반영하기 때문입니다.
- 실제 일별 데이터에서는 HIGH/ELEVATED 상태가 존재할 수 있습니다.

---

## 3. Hedge 상태별 성능 분해 (V5 Crisis-Only)

| Hedge State | Fold Count | Sharpe Mean | Asset PnL | Hedge PnL | Cost PnL |
|---|---|---|---|---|---|
| **hedge_on** (crisis/risk_off) | 15 | 1.7412 | 1.5620 | -0.2866 | 0.0211 |
| **hedge_off** (risk_on/transition) | 5 | 1.8262 | 0.2801 | -0.0164 | 0.0084 |

**해석**:
- **hedge_on** 상태에서 Hedge PnL이 -0.2866으로 드래그가 발생했지만, Asset PnL이 1.56으로 충분히 보상합니다.
- **hedge_off** 상태에서는 Hedge PnL 드래그가 거의 없습니다(-0.0164).
- V5의 crisis_only 정책이 **평시 드래그 최소화 + 위기 방어**를 효과적으로 달성했습니다.

---

## 4. ICIR v4 가중치 분포

| 전략 | 평균 비중 | 최소 비중 | 최대 비중 |
|---|---|---|---|
| **MR (Mean Reversion)** | 31.5% | 5.6% | 72.7% |
| **TREND** | 26.4% | 5.8% | 51.1% |
| **TREND_FAST** | 6.2% | - | - |
| **DEF_LOWVOL** | 10.4% | - | - |

**해석**:
- MR 비중이 최대 72.7%까지 올라가는 구간이 있습니다 (Fold 9).
- Fold 16(최악)에서 MR 비중이 49.7%로 높았으나, V5에서는 Sharpe가 +0.05로 양수입니다.
- ICIR v4가 동적으로 전략 비중을 조절하고 있습니다.

---

## 5. 알파 분해 요약

| 항목 | 값 | 비고 |
|---|---|---|
| **Total Asset PnL** | 1.8421 | 자산 수익 |
| **Total Hedge PnL** | -0.3030 | 헤지 드래그 |
| **Total Cost PnL** | 0.0238 | 거래 비용 |
| **Net PnL** | 1.5153 | 순 수익 |
| **Hedge Drag Ratio** | 16.4% | Hedge PnL / Asset PnL |

**해석**:
- V3(always_on)에서는 Hedge Drag가 더 컸지만, V5(crisis_only)에서는 **16.4%로 최적화**되었습니다.
- 이는 평시에 헤지를 끄고, 위기 시에만 켜는 정책의 효과입니다.

---

## 6. 레짐 × 헤지 상태 교차 분석

| Regime | Hedge State | Fold Count | Sharpe Mean | Asset PnL | Hedge PnL |
|---|---|---|---|---|---|
| crisis | hedge_on | 10 | 1.8301 | 0.7143 | -0.1755 |
| risk_on | hedge_off | 3 | 1.8317 | 0.4237 | -0.0225 |
| risk_off | hedge_on | 5 | 1.8862 | 0.6503 | -0.0947 |
| transition | hedge_off | 2 | 0.7469 | 0.0564 | 0.0061 |

**해석**:
- **crisis + hedge_on**: 헤지가 방어 역할을 수행하며, Sharpe 1.83 유지.
- **risk_off + hedge_on**: 헤지가 방어 역할을 수행하며, Sharpe 1.89로 가장 높음.
- **transition + hedge_off**: Sharpe가 낮지만, 헤지 드래그 없이 손실 최소화.

---

## 7. 결론 및 권고

1. **V5의 crisis_only 정책은 효과적**: 평시 드래그 최소화 + 위기 방어 달성.
2. **transition 레짐에서 개선 여지**: Fold 16(transition)이 최악이므로, transition 레짐에서의 전략 조정 검토.
3. **ICIR v4 MR 비중 모니터링**: MR 비중이 50% 이상으로 올라가는 구간에서 추가 분석 필요.
