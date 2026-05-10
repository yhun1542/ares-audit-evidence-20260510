# Onepage Report: champion_v5_20251231
생성일: 2025-12-31 05:29:15

## Key Metrics
| metric | value |
|---|---:|
| tuning_mean(1-16) | 1.7618 |
| tuning_min(1-16) | 0.0508 |
| lockbox_mean(17-20) | 1.7927 |
| lockbox_min(17-20) | 1.3469 |
| fold14_mdd(2020) | -9.97% |
| worst_mdd | -11.18% |
| oos_sharpe_mean | 1.7680 |
| oos_sharpe_min | 0.0508 |
| positive_folds | 20/20 |
| oos/is ratio | 1.2443 |


## Baseline 비교
- baseline: `baseline/champion_v3_20251231.json`
- Δ lockbox_mean: -0.0510
- Δ lockbox_min: +0.1532


## Architecture (Full, V5 highlighted)
```mermaid
flowchart LR
  %% AUB Full Production Architecture (V5 highlighted)
  %% Green=ACTIVE (V5), Red=INCONCLUSIVE, Orange=FAIL, Grey=OFF/optional

  subgraph DP[DATA PLANE]
    direction LR
    MKT[시장데이터\n(가격/거래량/지수/호가)] --> ING[Ingestion\n(ETL/Streaming)]
    CORP[기업행동/캘린더\n(분할/배당/상폐)] --> ING
    MACRO[매크로\n(VIX/금리/스프레드/FX)] --> ING
    FUND[펀더멘털/실적\n(PEAD 등)] --> ING
    ALT[대체데이터\n(뉴스/심리/웹/크레딧)] --> ING
    ING --> DQ[Data Quality Gate\n(결측/정합/타임존/거래일)]
    DQ --> DB[(DB/Feature Store)]
    DQ --> FS[(Online Feature Store)]
  end

  subgraph FL[FEATURE LAYER]
    direction LR
    DB --> FB[Feature Builder\n(리서치/백테스트)]
    FS --> FBRT[Realtime Feature Builder]
    FB --> FEATS[(Feature Set)]
    FBRT --> FEATSRT[(Realtime Feature Set)]
    FEATS --> RSF[RateShock Features\n(d2y_5d_bp,d2y_20d_bp)]
    FEATS --> MGF[MacroGate Features]
    FEATS --> VIXF[VIX State Features]
    FEATS --> REGF[Regime Features]
    FEATS --> ALTF[Alpha/IC Features]
  end

  subgraph AP[ALPHA / MODEL PLANE]
    direction LR
    ALTF --> ALPHA_LIB[Alpha Library\n(룰/팩터/시그널)]
    FUND --> PEAD[PEAD Overlay]
    ALT --> ICDET[IC Signal Detection]
    MKT --> MLINF[ML Inference\n(추론 머신)]
    ALPHA_LIB --> AO[Alpha Orchestrator]
    PEAD --> AO
    ICDET --> AO
    MLINF --> AO
    AO --> RAW_SIG[Raw Signal Vector]
  end

  subgraph PC[PORTFOLIO CONSTRUCTION]
    direction LR
    RAW_SIG --> RANK[Universe Filter & Ranking]
    RANK --> BASE_W[Base Weights]
    BASE_W --> ICIR4[ICIR v4]
    ICIR4 --> W_ICIR[ICIR-adjusted Weights]
    W_ICIR --> OPTI{Optimizer}
    OPTI --> MVO[MVO]
    OPTI --> HEU[Heuristic/RP]
    MVO --> W_OPT[Optimized Weights]
    HEU --> W_OPT
    W_OPT --> PRE_RISK[Pre-Risk Target Weights]
  end

  subgraph RL[RISK / OVERLAY LAYER]
    direction LR
    PRE_RISK --> REG15[regime15]
    PRE_RISK --> VIXSZ[vix_sizing]
    PRE_RISK --> MACG[macro_gate]
    PRE_RISK --> RSG[rate_shock_gate]
    PRE_RISK --> IMP[impact]
    PRE_RISK --> THR[throttle]
    PRE_RISK --> CB[cb / circuit breaker]
    PRE_RISK --> COMPAT[compat]
    PRE_RISK --> NOTRADE[no_trade]

    REG15 --> BAND[Band Mult / Risk State]
    VIXSZ --> SCALE[VIX Scaling / Clamp]
    MACG --> MACMULT[Macro Multiplier]
    RSG --> RSMULT[RateShock Mult]
    IMP --> IMPCOST[Impact/Slippage]
    THR --> THRCTL[Rebalance Throttle]
    CB --> CBCUT[Emergency De-risk]
    COMPAT --> CPCUT[Compat Rules]
    NOTRADE --> NTCUT[No-trade Rules]

    BAND --> POST_RISK[Post-Risk Weights]
    SCALE --> POST_RISK
    MACMULT --> POST_RISK
    RSMULT --> POST_RISK
    IMPCOST --> POST_RISK
    THRCTL --> POST_RISK
    CBCUT --> POST_RISK
    CPCUT --> POST_RISK
    NTCUT --> POST_RISK
  end

  subgraph HL[HEDGE LAYER (V5)]
    direction LR
    POST_RISK --> HCTRL[Hedge Controller\n(crisis_only)]
    HCTRL --> HTRIG[Hedge Triggers\n(regime15 risk_off/transition/crisis\nOR vix HIGH/ELEVATED/EXTREME)]
    HTRIG --> HYST[Hysteresis\n(enter_days / exit_days)]
    HYST --> HWT[hedge_weight_on]
    HWT --> FINAL_W[Final Target Weights]
  end

  subgraph EXE[EXECUTION & LIVE OPS]
    direction LR
    FINAL_W --> OMS[OMS/Risk Checks]
    OMS --> ROUTER[Order Router]
    ROUTER --> IBKR[IBKR Connector]
    ROUTER --> KIS[한국투자증권 Connector]
    ROUTER --> SIM[Paper/Shadow Executor]
    IBKR --> FILL[Fills/Positions]
    KIS --> FILL
    SIM --> FILL
    FILL --> POS[Portfolio State]
    POS --> FBRT
  end

  subgraph OG[OBSERVABILITY / GOVERNANCE]
    direction LR
    FILL --> PNL[PnL Engine\n(asset/hedge/cost)]
    PNL --> MET[Metrics Store]
    MET --> REPGEN[Report Generator\n(summary/regime/module/onepage)]
    MET --> EOS[Experiment System\n(lockbox/hard gate/autopromo)]
    REPGEN --> RPTS[Reports Folder]
    EOS --> DOCS[Docs\n(Inheritance/Final)]
  end

  classDef active fill:#E6FFED,stroke:#2E7D32,stroke-width:2px,color:#1B5E20;
  classDef inconc fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#B71C1C;
  classDef fail fill:#FFF3E0,stroke:#EF6C00,stroke-width:2px,color:#E65100;
  classDef off fill:#ECEFF1,stroke:#78909C,stroke-width:1px,color:#37474F;

  %% Active (V5)
  class ICIR4,REG15,VIXSZ,HCTRL,HTRIG,HYST,HWT,CB,THR,COMPAT,NOTRADE active;

  %% Inconclusive
  class RSG,MACG inconc;

  %% Fail
  class MVO fail;

  %% Optional/off
  class AO,PEAD,ICDET,MLINF,IMP off;

```

## Module Status (auto)
| module | status | enabled | available | reason | data_dep | feature_dep |
|---|---:|---:|---:|---|---|---|
| icir_v4 | OFF | 0 | None |  | none | none |
| regime15 | OFF | 0 | None |  | VIX,Credit,Yield | none |
| vix_sizing | OFF | 0 | None |  | VIX | none |
| hedge | OFF | 0 | None |  | nan | nan |
| cb | OFF | 0 | None |  | none | none |
| throttle | OFF | 0 | None |  | none | none |
| compat | OFF | 0 | None |  | none | none |
| no_trade | OFF | 0 | None |  | none | none |
| rate_shock_gate | OFF | 0 | None |  | Yield | d2y_5d_bp,d2y_20d_bp |
| macro_gate | OFF | 0 | None |  | Macro | none |
| mvo | OFF | 0 | None |  | none | none |
| impact | OFF | 0 | None |  | nan | nan |
| severity | OFF | 0 | None |  | nan | nan |
| alpha_orchestrator | OFF | 0 | True |  | nan | nan |
| pead_overlay | OFF | 0 | True |  | nan | nan |
| regime_v11 | OFF | 0 | None |  | nan | nan |
| icir | OFF | 0 | None |  | nan | nan |


## Module Impact (if available)
(source: `/home/ubuntu/AUB/baseline/module_impact_v5.csv`)

| module | comparison | tuning_mean_delta | tuning_min_delta | lockbox_mean_delta | lockbox_min_delta | fold14_mdd_delta | worst_mdd_delta | interpretation |
|---|---|---:|---:|---:|---:|---:|---:|---|
| icir_v4 | V3 → no_icir | -0.1511 | 0.1355 | -0.3971 | -0.2728 | nan | nan | 평균/락박스 성과 핵심 엔진(끄면 lockbox 급락) |
| regime15 | V3 → no_regime15 | -0.3033 | -0.0214 | -0.1009 | -0.3568 | nan | nan | 평균에도 기여 + lockbox min 방어에도 영향 큼 |
| vix_sizing | V3 → no_vix | -0.049 | -0.0625 | -0.065 | -0.0761 | nan | nan | 완만하지만 일관된 방어/개선 |
| hedge_always_on | V3 (always_on) → V4 (always_off) | -0.042 | 0.2418 | 0.031 | 0.1276 | -7.0%p | -2.2%p | 평시 성과 개선 but 급락 방어 약화 |
| hedge_crisis_only | V4 (always_off) → V5 (crisis_only) | -0.1215 | 0.0598 | -0.082 | 0.0256 | +2.4%p | +1.6%p | 평시 드래그 최소 + 위기 시 방어 |
| hedge_hysteresis | V5 → V5_hysteresis | 0.0 | 0.0 | 0.0039 | 0.0 | 0.0%p | 0.0%p | 스위칭 노이즈 감소 |
| rate_shock_gate | INCONCLUSIVE | nan | nan | nan | nan | nan | nan | 피처 미구현(d2y_5d_bp/d2y_20d_bp) |
| macro_gate | INCONCLUSIVE | nan | nan | nan | nan | nan | nan | 데이터 매칭/로드 실패(unknown=300) |
| mvo | V3 → mvo_on | -0.25 | -0.35 | -0.18 | -0.25 | nan | nan | 성과 악화로 탈락 |
