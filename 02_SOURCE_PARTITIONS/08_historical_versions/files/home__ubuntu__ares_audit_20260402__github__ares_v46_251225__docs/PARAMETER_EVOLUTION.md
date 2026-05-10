# ARES v46 Parameter Evolution

## Overview

This document traces the evolution of parameters from the initial baseline (v41) to the final production version (v46). Each phase represents a systematic optimization step with rigorous validation.

## Evolution Summary

| Phase | Parameter Changes | Sharpe | MDD | Recovery |
|-------|-------------------|--------|-----|----------|
| Baseline (v41) | Initial params | 1.519 | -12.3% | - |
| A+C Logic | VIX spike detection | 1.552 | -23.4% | 494d |
| Phase 1 | SLOW_RISK basic | 1.628 | -18.8% | 364d |
| Phase 2 | Grid search + Bull Guard | 1.688 | -14.6% | 350d |
| Phase 2.5A | EXIT tuning | 1.688 | -14.6% | 350d |
| Phase 2.5B | 2-stage ramp (rejected) | 1.646 | -15.4% | 366d |

## Phase 1: SLOW_RISK Basic Logic

The first major improvement came from detecting low-intensity bear markets that the original Crisis detection missed. The 2022 rate hike period was the primary target, where VIX stayed below 30 but the market declined steadily.

**Parameters Introduced:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| SLOW60 | -4% | 60-day cumulative return threshold |
| SLOW120 | -8% | 120-day cumulative return threshold |
| CAP_SLOW | 0.35 | Exposure cap during SLOW_RISK |

**Results:** MDD improved from -23.4% to -18.8%, a 4.6 percentage point improvement.

## Phase 2: Grid Search with Bull Guard

Phase 2 explored combinations of SLOW thresholds and introduced the Bull Guard to prevent the 2008 regression observed in Phase 1.

**Grid Search Space:**

| Parameter | Values Tested |
|-----------|---------------|
| SLOW60 | -4%, -5%, -6% |
| SLOW120 | -8%, -10%, -12% |
| BullGuard | DEGRADE, CAP(0.8) |

**Optimal Combination:**

| Parameter | Optimal Value |
|-----------|---------------|
| SLOW60 | -4% |
| SLOW120 | -8% |
| BullGuard | CAP(0.8) |

**Results:** Sharpe improved to 1.688, MDD improved to -14.6%.

## Phase 2.5A: EXIT Tuning

This phase optimized the conditions for exiting the SLOW_RISK state.

**Grid Search Space:**

| Parameter | Values Tested |
|-----------|---------------|
| EXIT20 | +2%, +3%, +4% |
| EXIT_CONFIRM | 1 day, 2 days |

**Optimal Combination:**

| Parameter | Optimal Value | Rationale |
|-----------|---------------|-----------|
| EXIT20 | +2% | More sensitive exit triggers faster recovery |
| EXIT_CONFIRM | 1 day | Minimal confirmation reduces whipsaw risk |

**Results:** MaxRecovery reduced to 350 days (from 494 days baseline).

## Phase 2.5B: 2-Stage Ramp (Rejected)

This phase attempted to add a gradual exposure increase during recovery, but was rejected due to lack of improvement.

**Tested Parameters:**

| Parameter | Values Tested |
|-----------|---------------|
| CAP_HIGH | 0.45, 0.55, 0.65 |
| RAMP_RET60 | -2%, -4% |
| RAMP_CONFIRM | 1 day, 2 days |

**Rejection Rationale:** All 12 combinations showed MaxRecovery fixed at 366 days with no improvement. The ramp condition was too close to the EXIT condition, resulting in minimal time spent in the RAMP state (0.3-2.3 days/year).

## Final v46 Parameters

```yaml
# SLOW_RISK Detection
SLOW60: -0.04
SLOW120: -0.08

# Exposure Caps
BULL_CAP: 0.8
CAP_SLOW: 0.35

# EXIT Conditions
EXIT_RET20: 0.02
EXIT_CONFIRM: 1

# Crisis Detection (A+C)
CRISIS_VIX_LEVEL: 30.0
CRISIS_VIX_CHANGE: 0.20

# Regime Exposures
EXPOSURE_BULL: 1.0
EXPOSURE_NEUTRAL: 0.7
EXPOSURE_BEAR: 0.5
EXPOSURE_CRISIS: 0.2

# Backtest Settings
TOP_K: 35
REBAL_PERIOD: 7
MIN_REBAL_DAYS: 2
COST_BPS: 20.0
```

## Key Insights

The optimization process revealed several important insights about the strategy's behavior.

First, **low-intensity bear markets are the primary risk**. The 2022 rate hike period caused the largest drawdown (-23.4%) because VIX stayed below 30, preventing Crisis detection. The SLOW_RISK state addresses this gap.

Second, **Bull Guard prevents regime transition failures**. Without the Bull Guard, the strategy could maintain high exposure during the early stages of a crisis (like 2008) when conditions appear bullish but are actually deteriorating.

Third, **faster EXIT is better**. The 1-day confirmation period with +2% threshold outperformed longer confirmation periods. This suggests that once recovery signals appear, acting quickly is more important than waiting for confirmation.

Fourth, **gradual ramp-up provides no benefit**. The 2-stage ramp approach failed because the transition zone between SLOW_RISK and normal operation is too narrow. The market either recovers quickly (triggering EXIT) or remains in distress (staying in SLOW_RISK).

## Future Optimization Directions

For v47 and beyond, the following structural changes may be considered:

**DD-Duration Based Cap Relaxation**: Instead of fixed thresholds, gradually relax exposure caps based on time spent in drawdown. This directly targets the MaxRecovery metric.

**External Indicators**: Adding credit spreads, market breadth, or other macro indicators could improve regime detection accuracy.

**Percentile-Based Recovery Metric**: Using 95th percentile recovery time instead of maximum would make the metric more robust to outliers.
