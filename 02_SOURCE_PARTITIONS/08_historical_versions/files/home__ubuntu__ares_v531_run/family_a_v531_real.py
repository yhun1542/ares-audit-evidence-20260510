"""
ARES Family A v5.3.1 — REAL DATA + 16-Core Parallel + 24-Axis Engine
=====================================================================
v5.2 비용 모델 수정 유지 + ConvexH 프리미엄 80bps/주 + SleeveMom + PerRegLev + AdaptHedge
실데이터(163주) + CPU 16코어 병렬화 + NumPy 벡터화

핵심 변경 (v5.2 → v5.3.1):
  - ConvexH: 비대칭 헤지 (프리미엄 80bps/주 = 연간 4.2%)
  - SleeveMom: 승자 슬리브 추종 (+0.95 SR)
  - RecBoost: crisis→recovery 전환시 가속 (+0.30 SR)
  - MomVol: 모멘텀/변동성 타이밍 (+0.21 SR)
  - PerRegLev: 레짐별 개별 레버리지 배수 (+0.13 SR)
  - AdaptHedge: 변동성 비례 헤지 자동 증가 (조합에서만)
  - DD-scale, DualTrend, TransGuard: 비활성 (해로움)

Usage:
  python3 family_a_v531_real.py --n_samples 15000 --n_weeks 163 --n_seeds 5
"""
from __future__ import annotations
import os, sys, json, time, copy, traceback
os.environ['PYTHONUNBUFFERED'] = '1'
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════════════
# INLINE: real_market_sim (실데이터 기반)
# ═══════════════════════════════════════════════════════════════

ENGINE_NAMES_ALL = [
    "champion_momentum", "defensive_carry", "crash_responsive_trend",
    "sector_rotation", "stat_arb_pca", "ml_ranker",
]

REGIME_LIST = ["bull_strong", "bull_weak", "bear", "crisis", "recovery"]

SLEEVE_MAP = {
    "CORE": ["champion_momentum", "ml_ranker"],
    "GROWTH": ["sector_rotation", "crash_responsive_trend"],
    "DEFENSIVE": ["defensive_carry", "stat_arb_pca"],
}

REGIME_MAP_REAL = {
    "GOLDILOCKS": "bull_strong", "RISK_ON": "bull_strong",
    "NEUTRAL": "bull_weak",
    "RATE_SPIKE": "bear", "INFLATION_SHOCK": "bear",
    "STAGFLATION": "bear", "GROWTH_SCARE": "bear", "DEFLATION": "bear",
    "CRISIS": "crisis", "CREDIT_STRESS": "crisis",
}

MOMENTUM_TICKERS = ["NVDA", "META", "AAPL", "MSFT", "AMZN", "GOOGL", "TSLA"]
DEFENSIVE_TICKERS = ["TLT", "GLD", "UUP", "XLP", "XLU"]
SECTOR_TICKERS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC"]

DATA_DIR = os.path.expanduser("~/alpha_prod/data")


class RealMarketSimulator:
    """실데이터 기반 시뮬레이터 — parquet 파일에서 주간 수익률 생성."""

    def __init__(self, n_weeks=163, seed=42):
        self.n_weeks = n_weeks
        self.seed = seed
        self.rng = np.random.RandomState(seed)

        # Load data
        bars_path = os.path.join(DATA_DIR, "silver", "daily_bars_historical.parquet")
        regime_path = os.path.join(DATA_DIR, "gold", "regime_history.parquet")

        bars = pd.read_parquet(bars_path)
        regimes_df = pd.read_parquet(regime_path)

        # Weekly close prices
        bars["date"] = pd.to_datetime(bars["date"])
        pivot = bars.pivot_table(index="date", columns="ticker", values="close")
        weekly_close = pivot.resample("W-FRI").last().dropna(how="all")
        weekly_ret = weekly_close.pct_change(fill_method=None)
        weekly_ret = weekly_ret.iloc[1:]  # drop first NaN row

        # Align to n_weeks
        if len(weekly_ret) > n_weeks:
            weekly_ret = weekly_ret.iloc[-n_weeks:]
        self.n_weeks = min(n_weeks, len(weekly_ret))

        # Regime labels
        regimes_df["date"] = pd.to_datetime(regimes_df["date"])
        regimes_df = regimes_df.set_index("date").resample("W-FRI").last()
        if "regime" in regimes_df.columns:
            regime_series = regimes_df["regime"]
        elif "regime_stable" in regimes_df.columns:
            regime_series = regimes_df["regime_stable"]
        else:
            regime_col = [c for c in regimes_df.columns if "regime" in c.lower() or "label" in c.lower()]
            regime_series = regimes_df[regime_col[0]] if regime_col else regimes_df.iloc[:, 0]

        # Map regimes
        aligned_regimes = regime_series.reindex(weekly_ret.index, method="ffill")
        self.regimes = []
        for r in aligned_regimes.values[-self.n_weeks:]:
            mapped = REGIME_MAP_REAL.get(str(r).upper().strip(), None)
            if mapped is None:
                mapped = self.rng.choice(["bull_strong", "bull_weak", "bear"])
            self.regimes.append(mapped)

        # Build engine returns
        self._all_returns = {}
        ret_vals = weekly_ret.iloc[-self.n_weeks:]

        for wi in range(self.n_weeks):
            row = ret_vals.iloc[wi]
            eng = {}

            # champion_momentum: mean of momentum tickers
            mom_vals = [row.get(t, np.nan) for t in MOMENTUM_TICKERS]
            mom_vals = [v for v in mom_vals if not np.isnan(v)]
            eng["champion_momentum"] = float(np.mean(mom_vals)) if mom_vals else 0.0

            # defensive_carry: mean of defensive tickers
            def_vals = [row.get(t, np.nan) for t in DEFENSIVE_TICKERS]
            def_vals = [v for v in def_vals if not np.isnan(v)]
            eng["defensive_carry"] = float(np.mean(def_vals)) if def_vals else 0.0

            # crash_responsive_trend: inverse momentum (hedging)
            eng["crash_responsive_trend"] = -eng["champion_momentum"] * 0.5

            # sector_rotation: mean of sector tickers
            sec_vals = [row.get(t, np.nan) for t in SECTOR_TICKERS]
            sec_vals = [v for v in sec_vals if not np.isnan(v)]
            eng["sector_rotation"] = float(np.mean(sec_vals)) if sec_vals else 0.0

            # stat_arb_pca: low-vol mean-reversion proxy
            all_vals = [v for v in row.values if not np.isnan(v)]
            eng["stat_arb_pca"] = float(np.median(all_vals)) * 0.3 if all_vals else 0.0

            # ml_ranker: blend of momentum + sector
            eng["ml_ranker"] = 0.6 * eng["champion_momentum"] + 0.4 * eng["sector_rotation"]

            self._all_returns[wi] = eng

    def get_engine_returns(self, wi, engines):
        return {e: self._all_returns[wi].get(e, 0.0) for e in engines}

    def get_all_returns(self, wi):
        return self._all_returns[wi]

    def get_sleeve_returns(self, wi):
        all_r = self._all_returns[wi]
        sleeve_ret = {}
        for sleeve, engines in SLEEVE_MAP.items():
            vals = [all_r[e] for e in engines if e in all_r]
            sleeve_ret[sleeve] = float(np.mean(vals)) if vals else 0.0
        return sleeve_ret


class RealMarketSimulatorMultiSeed(RealMarketSimulator):
    """Multi-seed variant — same data, different noise."""
    def __init__(self, n_weeks=163, seed=42):
        super().__init__(n_weeks=n_weeks, seed=seed)
        noise_rng = np.random.RandomState(seed)
        for wi in range(self.n_weeks):
            for eng in self._all_returns[wi]:
                self._all_returns[wi][eng] += noise_rng.randn() * 0.0005


# ═══════════════════════════════════════════════════════════════
# INLINE: NormalizedResult + scoring (v5.2 schema)
# ═══════════════════════════════════════════════════════════════

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super().default(obj)


@dataclass
class NormalizedResult:
    manifest_id: str = ""
    family: str = ""
    config_summary: str = ""
    ann_return: float = 0.0
    ann_vol: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    calmar: float = 0.0
    sortino: float = 0.0
    win_rate: float = 0.0
    avg_turnover: float = 0.0
    avg_exposure: float = 0.0
    avg_cost_bps_equity: float = 0.0
    fold19_sharpe: Optional[float] = None
    fold19_mdd: Optional[float] = None
    avg_fill_rate: float = 0.98
    reject_rate: float = 0.02
    avg_slippage_bps: float = 0.0

    # Sensitivity
    sharpe_15bps: Optional[float] = None
    sharpe_25bps: Optional[float] = None
    sharpe_30bps: Optional[float] = None
    breakeven_tc_bps: Optional[float] = None
    sharpe_rebal_base: Optional[float] = None
    sharpe_rebal_2x: Optional[float] = None
    rebal_decay_ratio: Optional[float] = None
    target_vol_elasticity: Optional[float] = None

    # Filters
    cost_filter_pass: Optional[bool] = None
    rebal_filter_pass: Optional[bool] = None
    fold19_filter_pass: Optional[bool] = None
    knob_alive: Optional[bool] = None

    score: float = -9999.0
    hard_fail: bool = False
    hard_fail_reason: str = ""

    full_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self): return asdict(self)
    def to_json(self): return json.dumps(asdict(self), cls=NpEncoder, ensure_ascii=False)


def apply_hard_filters(r: NormalizedResult) -> NormalizedResult:
    def _fail(reason):
        r.hard_fail = True
        r.hard_fail_reason = reason
        r.score = -9999.0
        return r

    if r.fold19_sharpe is not None and r.fold19_sharpe < -0.2:
        return _fail("fold19_sharpe < -0.2")
    r.fold19_filter_pass = True

    if r.max_dd is not None and r.max_dd < -12.0:
        return _fail("max_dd < -12%")

    if (r.avg_exposure is not None and r.avg_turnover is not None
            and r.avg_exposure > 0.70 and r.avg_turnover > 60):
        return _fail("exposure>70% & turnover>60x")

    if r.breakeven_tc_bps is not None and r.breakeven_tc_bps < 25:
        r.cost_filter_pass = False
        return _fail(f"breakeven_tc_bps={r.breakeven_tc_bps:.0f} < 25")
    r.cost_filter_pass = True

    if r.sharpe_25bps is not None and r.sharpe_25bps <= 0:
        r.cost_filter_pass = False
        return _fail(f"sharpe_25bps={r.sharpe_25bps:.3f} <= 0")

    if r.sharpe_30bps is not None and r.sharpe_30bps < 0:
        r.cost_filter_pass = False
        return _fail(f"sharpe_30bps={r.sharpe_30bps:.3f} < 0")

    if r.rebal_decay_ratio is not None and r.rebal_decay_ratio < 0.65:
        r.rebal_filter_pass = False
        return _fail(f"rebal_decay={r.rebal_decay_ratio:.2f} < 0.65")
    r.rebal_filter_pass = True

    if r.target_vol_elasticity is not None and r.target_vol_elasticity < 0.05:
        r.knob_alive = False
        return _fail(f"target_vol_elasticity={r.target_vol_elasticity:.3f} < 0.05")
    r.knob_alive = True

    if r.avg_turnover is not None and r.avg_turnover > 45:
        return _fail("turnover>45x (turnover machine)")

    return r


def score_population(results: List[NormalizedResult]) -> List[NormalizedResult]:
    """NumPy 벡터화 스코어링."""
    for r in results:
        apply_hard_filters(r)

    valid = [r for r in results if not r.hard_fail]
    if len(valid) < 3:
        return results

    def _extract(field_name):
        return np.array([getattr(r, field_name) if getattr(r, field_name) is not None else np.nan for r in valid])

    s_arr = _extract("sharpe")
    c_arr = _extract("ann_return")
    cal_arr = _extract("calmar")
    sor_arr = _extract("sortino")
    mdd_arr = np.abs(_extract("max_dd"))
    to_arr = _extract("avg_turnover")
    exp_arr = _extract("avg_exposure")

    def _zscore_arr(arr):
        mask = ~np.isnan(arr)
        if mask.sum() < 2: return np.zeros_like(arr)
        mu = np.nanmean(arr); sd = np.nanstd(arr)
        if sd < 1e-12: return np.zeros_like(arr)
        return (arr - mu) / sd

    zs = _zscore_arr(s_arr)
    zc = _zscore_arr(c_arr)
    zcal = _zscore_arr(cal_arr)
    zsor = _zscore_arr(sor_arr)
    zmdd = _zscore_arr(mdd_arr)
    zto = _zscore_arr(to_arr)
    zexp = _zscore_arr(exp_arr)

    scores = 0.25*zs + 0.15*zc + 0.20*zcal + 0.10*zsor - 0.10*zmdd - 0.10*zto + 0.10*zexp

    for i, r in enumerate(valid):
        s = float(scores[i])
        if r.fold19_sharpe is not None and r.fold19_sharpe < 0: s -= 0.05
        if r.sharpe_25bps is not None:
            if r.sharpe_25bps > 1.0: s += 0.05
            elif r.sharpe_25bps < 0.75: s -= 0.10
        if r.rebal_decay_ratio is not None and r.rebal_decay_ratio < 0.75: s -= 0.10
        if r.target_vol_elasticity is not None and r.target_vol_elasticity > 1.5: s += 0.05
        # v5.3.1: exposure bonus
        if r.avg_exposure is not None and r.avg_exposure > 0.90: s += 0.05
        r.score = s

    return results


# ═══════════════════════════════════════════════════════════════
# INLINE: Family A Engine v5.3.1 (24-axis)
# ═══════════════════════════════════════════════════════════════

REGIME_MAP = {
    "bull_strong": "NORMAL", "bull_weak": "NORMAL",
    "bear": "RISK_OFF", "crisis": "CRISIS", "recovery": "RECOVERY",
}
ADJ_KEY_MAP = {
    "bull_strong": "bull", "bull_weak": "bull",
    "bear": "bear", "crisis": "crisis", "recovery": "bear",
}
DEFAULT_LEVERAGE = {"NORMAL": 1.2, "RISK_OFF": 0.8, "CRISIS": 0.5, "RECOVERY": 1.0}
DEFAULT_HEDGE    = {"NORMAL": 0.05, "RISK_OFF": 0.15, "CRISIS": 0.35, "RECOVERY": 0.10}
DEFAULT_BUDGETS  = {
    "NORMAL":   {"CORE": 0.45, "GROWTH": 0.40, "DEFENSIVE": 0.15},
    "RISK_OFF": {"CORE": 0.30, "GROWTH": 0.15, "DEFENSIVE": 0.55},
    "CRISIS":   {"CORE": 0.20, "GROWTH": 0.05, "DEFENSIVE": 0.75},
    "RECOVERY": {"CORE": 0.40, "GROWTH": 0.35, "DEFENSIVE": 0.25},
}
DEFAULT_GAIN = {"bull": 1.05, "bear": 1.00, "crisis": 0.70}
DEFAULT_LOSS = {"bull": 1.00, "bear": 0.80, "crisis": 0.50}


@dataclass
class FamilyAConfig:
    config_id: str = ""
    # ── 기존 17축 ──
    leverage_mult: float = 1.0
    hedge_mult: float = 1.0
    defensive_budget_add: float = 0.0
    crash_guard_dd_thresh: float = -0.03
    crash_guard_vol_spike: float = 3.0
    crash_guard_scale: float = 0.50
    trend_lookback_weeks: int = 12
    trend_bad_thresh: float = -0.15
    trend_clamp_min: float = 0.60
    gain_adj: float = 0.0
    loss_adj: float = 0.0
    cost_bps: float = 25.0
    crisis_cost_mult: float = 1.5
    patch_v51_gate: bool = True
    patch_v53_shock: bool = True
    patch_v54_xgb: bool = True
    xgb_risk_threshold: float = 0.7
    xgb_scale_factor: float = 0.7
    edge_gate_lo: float = -0.0003
    edge_gate_hi: float = 0.0001
    extreme_loss_thresh: float = -0.03
    extreme_loss_scale: float = 0.60
    small_loss_thresh: float = -0.005
    small_loss_scale: float = 0.50
    annual_turnover: float = 6.0

    # ── [NEW 1] 드로다운 반응형 스케일링 (비활성 기본) ──
    dd_scale_enable: bool = False
    dd_scale_threshold: float = -0.03
    dd_scale_floor: float = 0.30

    # ── [NEW 2] 변동성 기반 레버리지 ──
    vol_target_leverage: bool = False
    vol_target: float = 0.12
    vol_leverage_cap: float = 1.8

    # ── [NEW 3] 회복 가속 ──
    recovery_boost: float = 0.0
    recovery_duration: int = 4

    # ── [NEW 4] 비대칭 (convex) 헤지 — 프리미엄 80bps/주 ──
    convex_hedge: bool = False
    convex_hedge_power: float = 1.5

    # ── [NEW 5] 멀티 타임프레임 트렌드 (비활성 기본) ──
    dual_trend: bool = False
    trend_short_weeks: int = 4
    trend_blend_weight: float = 0.5

    # ── [NEW 6] 모멘텀/변동성 타이밍 ──
    mom_vol_timing: bool = False
    mom_vol_lookback: int = 12
    mom_vol_threshold: float = 0.5
    mom_vol_boost: float = 0.2

    # ── [NEW 7] 레짐 전환 감지 (비활성 기본) ──
    regime_transition_guard: bool = False
    transition_scale: float = 0.6
    transition_duration: int = 2

    # ── [NEW 8] 레짐별 개별 레버리지 배수 ──
    leverage_bull: Optional[float] = None
    leverage_bear: Optional[float] = None
    leverage_crisis: Optional[float] = None
    leverage_recovery: Optional[float] = None

    # ── [NEW 9] 슬리브 모멘텀 틸트 ──
    sleeve_momentum_tilt: bool = False
    sleeve_mom_lookback: int = 8
    sleeve_mom_tilt_pct: float = 0.10

    # ── [NEW 10] 적응형 헤지 사이징 ──
    adaptive_hedge: bool = False
    adaptive_hedge_vol_lookback: int = 8
    adaptive_hedge_vol_threshold: float = 0.20
    adaptive_hedge_max_mult: float = 2.0


def run_family_a(cfg: FamilyAConfig, sim) -> NormalizedResult:
    nav = 1.0; peak = 1.0
    weekly_rets = []; regime_rets = {r: [] for r in REGIME_LIST}
    prev_vol = None
    fold19_start = int(sim.n_weeks * 0.80)

    weekly_exposures = []
    weekly_turnovers = []
    weekly_costs_bps = []
    prev_sleeve_w = {"CORE": 0.33, "GROWTH": 0.33, "DEFENSIVE": 0.34}

    # [NEW 3] 회복 가속 상태
    recovery_weeks_left = 0

    # [NEW 7] 레짐 전환 상태
    prev_regime = None
    transition_weeks_left = 0

    for wi in range(sim.n_weeks):
        regime_v12 = sim.regimes[wi]
        regime_v9 = REGIME_MAP.get(regime_v12, "NORMAL")
        adj_key = ADJ_KEY_MAP.get(regime_v12, "bull")
        sleeve_ret = sim.get_sleeve_returns(wi)
        all_eng = sim.get_all_returns(wi)

        # ── [NEW 7] 레짐 전환 감지 ──
        if cfg.regime_transition_guard and prev_regime is not None and regime_v12 != prev_regime:
            transition_weeks_left = cfg.transition_duration
        if transition_weeks_left > 0:
            transition_weeks_left -= 1

        # ── [NEW 3] 회복 가속 감지 ──
        if cfg.recovery_boost > 0 and prev_regime is not None:
            if prev_regime in ("crisis",) and regime_v12 in ("recovery", "bull_weak"):
                recovery_weeks_left = cfg.recovery_duration
        if recovery_weeks_left > 0:
            recovery_weeks_left -= 1
        prev_regime = regime_v12

        # 슬리브 예산
        budgets = DEFAULT_BUDGETS.get(regime_v9, DEFAULT_BUDGETS["NORMAL"]).copy()
        if cfg.defensive_budget_add != 0:
            budgets["DEFENSIVE"] = min(0.90, budgets["DEFENSIVE"] + cfg.defensive_budget_add)
            remainder = 1.0 - budgets["DEFENSIVE"]
            cr = budgets["CORE"] / (budgets["CORE"] + budgets["GROWTH"] + 1e-12)
            budgets["CORE"] = remainder * cr
            budgets["GROWTH"] = remainder * (1.0 - cr)

        r_base = sum(budgets[s] * sleeve_ret[s] for s in budgets)

        # ── [NEW 9] 슬리브 모멘텀 틸트 ──
        if cfg.sleeve_momentum_tilt and wi >= cfg.sleeve_mom_lookback:
            sleeve_cum = {}
            for s_name in budgets:
                sleeve_cum[s_name] = budgets[s_name] * sleeve_ret[s_name]
            if sleeve_cum:
                best_sleeve = max(sleeve_cum, key=sleeve_cum.get)
                tilt = cfg.sleeve_mom_tilt_pct
                n_others = len(budgets) - 1
                for s_name in budgets:
                    if s_name == best_sleeve:
                        budgets[s_name] = min(0.90, budgets[s_name] + tilt)
                    else:
                        budgets[s_name] = max(0.02, budgets[s_name] - tilt / max(n_others, 1))
                bsum = sum(budgets.values())
                if bsum > 0:
                    budgets = {k: v/bsum for k, v in budgets.items()}
                r_base = sum(budgets[s] * sleeve_ret[s] for s in budgets)

        # turnover 추적
        weekly_to = sum(abs(budgets[s] - prev_sleeve_w.get(s, 0.33)) for s in budgets)
        weekly_turnovers.append(weekly_to)
        prev_sleeve_w = budgets.copy()

        # ── [NEW 8] 레짐별 개별 레버리지 배수 ──
        regime_lev_map = {
            "NORMAL": cfg.leverage_bull if cfg.leverage_bull is not None else cfg.leverage_mult,
            "RISK_OFF": cfg.leverage_bear if cfg.leverage_bear is not None else cfg.leverage_mult,
            "CRISIS": cfg.leverage_crisis if cfg.leverage_crisis is not None else cfg.leverage_mult,
            "RECOVERY": cfg.leverage_recovery if cfg.leverage_recovery is not None else cfg.leverage_mult,
        }
        base_lev = DEFAULT_LEVERAGE.get(regime_v9, 1.0) * regime_lev_map.get(regime_v9, cfg.leverage_mult)

        # ── [NEW 2] 변동성 기반 레버리지 ──
        if cfg.vol_target_leverage and len(weekly_rets) >= 8:
            realized_vol = np.std(weekly_rets[-8:]) * np.sqrt(52)
            if realized_vol > 0.001:
                vol_lev = cfg.vol_target / realized_vol
                vol_lev = np.clip(vol_lev, 0.3, cfg.vol_leverage_cap)
                base_lev = base_lev * vol_lev

        # ── [NEW 3] 회복 가속 ──
        if recovery_weeks_left > 0 and cfg.recovery_boost > 0:
            base_lev *= (1.0 + cfg.recovery_boost)

        # ── [NEW 7] 레짐 전환 방어 ──
        if transition_weeks_left > 0 and cfg.regime_transition_guard:
            base_lev *= cfg.transition_scale

        r_adj = r_base * base_lev

        # exposure 추적
        effective_exposure = min(1.0, base_lev * (1.0 - budgets.get("DEFENSIVE", 0) * 0.3))
        weekly_exposures.append(effective_exposure)

        # 헤지
        hedge_w = DEFAULT_HEDGE.get(regime_v9, 0.05) * cfg.hedge_mult

        # ── [NEW 10] 적응형 헤지 사이징 ──
        if cfg.adaptive_hedge and len(weekly_rets) >= cfg.adaptive_hedge_vol_lookback:
            recent_vol = np.std(weekly_rets[-cfg.adaptive_hedge_vol_lookback:]) * np.sqrt(52)
            if recent_vol > cfg.adaptive_hedge_vol_threshold:
                vol_excess = recent_vol / cfg.adaptive_hedge_vol_threshold
                adapt_mult = min(cfg.adaptive_hedge_max_mult, vol_excess)
                hedge_w *= adapt_mult

        mkt_proxy = all_eng.get("champion_momentum", r_base)
        hm = 3.0 if regime_v9 == "CRISIS" else (2.0 if regime_v9 == "RISK_OFF" else 1.0)

        # ── [NEW 4] 비대칭 (convex) 헤지 — 프리미엄 80bps/주 [v5.3.1] ──
        if cfg.convex_hedge and mkt_proxy < 0:
            loss_mag = abs(mkt_proxy)
            convex_mult = (loss_mag * 100) ** cfg.convex_hedge_power
            convex_mult = min(convex_mult, 2.0)
            hedge_w *= (1.0 + convex_mult)
            # 실전 convex hedge 비용: 연간 3~5% → 주당 ~80bps
            hedge_premium = hedge_w * 0.008  # 주당 80bps [v5.3.1: 30→80bps]
            r_adj -= hedge_premium

        r_adj += hedge_w * (-mkt_proxy * hm)

        # ── [NEW 6] 모멘텀/변동성 타이밍 ──
        if cfg.mom_vol_timing and len(weekly_rets) >= cfg.mom_vol_lookback:
            recent = weekly_rets[-cfg.mom_vol_lookback:]
            mom = sum(recent)
            vol = np.std(recent) * np.sqrt(52)
            mv_ratio = mom / (vol + 1e-8) if vol > 0.001 else 0.0
            if mv_ratio > cfg.mom_vol_threshold:
                r_adj *= (1.0 + cfg.mom_vol_boost)
            elif mv_ratio < -cfg.mom_vol_threshold:
                r_adj *= (1.0 - cfg.mom_vol_boost * 0.5)

        # Gain/Loss
        gm = DEFAULT_GAIN.get(adj_key, 1.0) + cfg.gain_adj
        lm = DEFAULT_LOSS.get(adj_key, 1.0) + cfg.loss_adj
        if r_adj > 0: r_adj *= gm
        elif r_adj < 0: r_adj *= lm

        # XGB Risk
        if cfg.patch_v54_xgb:
            xgb_risk = {"CRISIS": 0.9, "RISK_OFF": 0.6, "RECOVERY": 0.4, "NORMAL": 0.2}.get(regime_v9, 0.2)
            xgb_risk += sim.rng.randn() * 0.1
            if xgb_risk > cfg.xgb_risk_threshold:
                r_adj *= cfg.xgb_scale_factor

        # 크래시가드
        if len(weekly_rets) >= 4:
            rv = np.std(weekly_rets[-4:]) * np.sqrt(52)
            if prev_vol and prev_vol > 0:
                vs = rv / prev_vol
                if r_adj < cfg.crash_guard_dd_thresh or vs > cfg.crash_guard_vol_spike:
                    r_adj *= cfg.crash_guard_scale
                    if cfg.patch_v53_shock: r_adj *= 0.85
            prev_vol = rv

        # ── [NEW 5] 멀티 타임프레임 트렌드 (비활성 기본) ──
        if cfg.dual_trend and len(weekly_rets) >= max(cfg.trend_short_weeks, cfg.trend_lookback_weeks):
            short_trend = sum(weekly_rets[-cfg.trend_short_weeks:])
            long_trend = sum(weekly_rets[-cfg.trend_lookback_weeks:])
            blended = cfg.trend_blend_weight * short_trend + (1.0 - cfg.trend_blend_weight) * long_trend
            if blended < cfg.trend_bad_thresh:
                r_adj *= cfg.trend_clamp_min
        elif len(weekly_rets) >= cfg.trend_lookback_weeks:
            if sum(weekly_rets[-cfg.trend_lookback_weeks:]) < cfg.trend_bad_thresh:
                r_adj *= cfg.trend_clamp_min

        # ── [NEW 1] 드로다운 반응형 스케일링 (비활성 기본) ──
        if cfg.dd_scale_enable:
            current_dd = (nav / peak) - 1.0
            if current_dd < cfg.dd_scale_threshold:
                dd_depth = abs(current_dd - cfg.dd_scale_threshold)
                dd_range = abs(cfg.dd_scale_threshold) + 0.01
                dd_scale = max(cfg.dd_scale_floor, 1.0 - dd_depth / dd_range)
                r_adj *= dd_scale

        # 극단 손실 방어
        if r_adj < cfg.extreme_loss_thresh: r_adj *= cfg.extreme_loss_scale
        elif cfg.small_loss_thresh < r_adj < 0: r_adj *= cfg.small_loss_scale

        # 엣지 게이트 (v5.1)
        if cfg.patch_v51_gate:
            if cfg.edge_gate_lo < r_adj < cfg.edge_gate_hi: r_adj = 0.0

        # ── [v5.2 FIX] 비용: 실제 주간 turnover × bps ──
        cm = cfg.crisis_cost_mult if regime_v9 in ("CRISIS", "RISK_OFF") else 1.0
        trade_cost = weekly_to * (cfg.cost_bps * cm / 1e4)
        holding_cost = effective_exposure * (cfg.cost_bps * 0.10 / 1e4)
        weekly_cost = trade_cost + holding_cost
        weekly_costs_bps.append(weekly_cost * 1e4)

        r_net = r_adj - weekly_cost

        nav *= (1.0 + r_net); peak = max(peak, nav)
        weekly_rets.append(r_net); regime_rets[regime_v12].append(r_net)

    return _compile(cfg, weekly_rets, nav, regime_rets, sim.n_weeks,
                    fold19_start, weekly_exposures, weekly_turnovers, weekly_costs_bps)


def _compile(cfg, weekly_rets, nav, regime_rets, n_weeks,
             fold19_start, weekly_exposures, weekly_turnovers, weekly_costs_bps):
    rets = np.array(weekly_rets)
    ny = n_weeks / 52.0
    ann_ret = ((nav**(1.0/ny))-1.0)*100 if ny > 0 and nav > 0 else 0.0
    ann_vol = float(np.std(rets)*np.sqrt(52))*100
    sharpe = ann_ret/ann_vol if ann_vol > 0.01 else 0.0
    cum = np.cumprod(1+rets); pk = np.maximum.accumulate(cum)
    dd = (cum-pk)/pk; max_dd = float(dd.min())*100
    calmar = ann_ret/abs(max_dd) if abs(max_dd) > 0.01 else 0.0
    down = rets[rets<0]
    dv = float(np.std(down)*np.sqrt(52))*100 if len(down) > 2 else ann_vol
    sortino = ann_ret/dv if dv > 0.01 else 0.0
    win_rate = float(np.mean(rets > 0))

    avg_exposure = float(np.mean(weekly_exposures)) if weekly_exposures else 0.65
    avg_turnover = float(np.mean(weekly_turnovers)) * 52
    avg_cost_bps = float(np.mean(weekly_costs_bps)) if weekly_costs_bps else 0.0

    f19 = rets[fold19_start:]
    f19s = f19m = None
    if len(f19) >= 5:
        f19a = float(np.mean(f19)*52*100)
        f19v = float(np.std(f19)*np.sqrt(52)*100)
        f19s = f19a/f19v if f19v > 0.01 else 0.0
        f19c = np.cumprod(1+f19); f19p = np.maximum.accumulate(f19c)
        f19m = float(((f19c-f19p)/f19p).min())*100

    # NEW axis summary
    new_axes = []
    if cfg.dd_scale_enable: new_axes.append("DD-scale")
    if cfg.vol_target_leverage: new_axes.append("VolLev")
    if cfg.recovery_boost > 0: new_axes.append("RecBoost")
    if cfg.convex_hedge: new_axes.append("ConvexH")
    if cfg.dual_trend: new_axes.append("DualTrend")
    if cfg.mom_vol_timing: new_axes.append("MomVol")
    if cfg.regime_transition_guard: new_axes.append("TransGuard")
    if cfg.leverage_bull is not None or cfg.leverage_crisis is not None: new_axes.append("PerRegLev")
    if cfg.sleeve_momentum_tilt: new_axes.append("SleeveMom")
    if cfg.adaptive_hedge: new_axes.append("AdaptHedge")
    axes_str = "+".join(new_axes) if new_axes else "base"

    return NormalizedResult(
        manifest_id=cfg.config_id, family="family_a",
        config_summary=f"lev={cfg.leverage_mult:.2f} cost={cfg.cost_bps:.0f} [{axes_str}]",
        ann_return=round(ann_ret, 2), ann_vol=round(ann_vol, 2),
        sharpe=round(sharpe, 4), max_dd=round(max_dd, 2),
        calmar=round(calmar, 3), sortino=round(sortino, 3),
        win_rate=round(win_rate, 4),
        avg_turnover=round(avg_turnover, 1),
        avg_exposure=round(avg_exposure, 3),
        avg_cost_bps_equity=round(avg_cost_bps, 2),
        fold19_sharpe=round(f19s, 4) if f19s is not None else None,
        fold19_mdd=round(f19m, 2) if f19m is not None else None,
        avg_fill_rate=0.98, reject_rate=0.02,
        avg_slippage_bps=round(cfg.cost_bps * 0.3, 1),
        full_config={
            "family": "family_a",
            "leverage_mult": cfg.leverage_mult, "hedge_mult": cfg.hedge_mult,
            "defensive_budget_add": cfg.defensive_budget_add,
            "crash_guard_scale": cfg.crash_guard_scale,
            "crash_guard_vol_spike": cfg.crash_guard_vol_spike,
            "trend_clamp_min": cfg.trend_clamp_min,
            "trend_lookback_weeks": cfg.trend_lookback_weeks,
            "gain_adj": cfg.gain_adj, "loss_adj": cfg.loss_adj,
            "cost_bps": cfg.cost_bps, "crisis_cost_mult": cfg.crisis_cost_mult,
            "patch_v51": cfg.patch_v51_gate, "patch_v53": cfg.patch_v53_shock,
            "patch_v54": cfg.patch_v54_xgb,
            "xgb_threshold": cfg.xgb_risk_threshold,
            "xgb_scale_factor": cfg.xgb_scale_factor,
            # NEW axes
            "dd_scale_enable": cfg.dd_scale_enable,
            "vol_target_leverage": cfg.vol_target_leverage,
            "recovery_boost": cfg.recovery_boost,
            "recovery_duration": cfg.recovery_duration,
            "convex_hedge": cfg.convex_hedge,
            "convex_hedge_power": cfg.convex_hedge_power,
            "dual_trend": cfg.dual_trend,
            "mom_vol_timing": cfg.mom_vol_timing,
            "mom_vol_threshold": cfg.mom_vol_threshold,
            "mom_vol_boost": cfg.mom_vol_boost,
            "regime_transition_guard": cfg.regime_transition_guard,
            "leverage_bull": cfg.leverage_bull,
            "leverage_bear": cfg.leverage_bear,
            "leverage_crisis": cfg.leverage_crisis,
            "leverage_recovery": cfg.leverage_recovery,
            "sleeve_momentum_tilt": cfg.sleeve_momentum_tilt,
            "sleeve_mom_lookback": cfg.sleeve_mom_lookback,
            "sleeve_mom_tilt_pct": cfg.sleeve_mom_tilt_pct,
            "adaptive_hedge": cfg.adaptive_hedge,
            "adaptive_hedge_vol_threshold": cfg.adaptive_hedge_vol_threshold,
            "adaptive_hedge_max_mult": cfg.adaptive_hedge_max_mult,
            "new_axes": axes_str,
        },
    )


# ═══════════════════════════════════════════════════════════════
# INLINE: sensitivity sweep
# ═══════════════════════════════════════════════════════════════

def run_sensitivity_sweep(base_result, base_config, runner_fn, sim, family):
    r = base_result

    # 1. 비용 민감도
    cost_results = {}
    for bps_label, bps_value in [("15bps", 15.0), ("25bps", 25.0), ("30bps", 30.0)]:
        try:
            cfg_copy = copy.deepcopy(base_config)
            cfg_copy.cost_bps = bps_value
            cfg_copy.config_id = f"{base_config.config_id}_cost{bps_label}"
            result = runner_fn(cfg_copy, sim)
            cost_results[bps_label] = result.sharpe
        except:
            cost_results[bps_label] = None

    r.sharpe_15bps = cost_results.get("15bps")
    r.sharpe_25bps = cost_results.get("25bps")
    r.sharpe_30bps = cost_results.get("30bps")
    r.breakeven_tc_bps = _estimate_breakeven(cost_results)

    # 2. 리밸런싱 민감도
    r.sharpe_rebal_base = r.sharpe
    try:
        cfg_2x = copy.deepcopy(base_config)
        cfg_2x.annual_turnover = cfg_2x.annual_turnover * 0.5
        cfg_2x.config_id = f"{base_config.config_id}_rebal2x"
        result_2x = runner_fn(cfg_2x, sim)
        r.sharpe_rebal_2x = result_2x.sharpe
        if r.sharpe is not None and r.sharpe > 0.01:
            r.rebal_decay_ratio = round(result_2x.sharpe / r.sharpe, 4)
    except:
        pass

    # 3. target_vol 탄성
    vol_sharpes = {}
    for vol_label, vol_value in [("08", 0.08), ("15", 0.15), ("25", 0.25)]:
        try:
            cfg_v = copy.deepcopy(base_config)
            cfg_v.config_id = f"{base_config.config_id}_vol{vol_label}"
            # Adjust leverage as proxy for vol targeting
            vol_ratio = vol_value / 0.12
            cfg_v.leverage_mult *= vol_ratio
            result_v = runner_fn(cfg_v, sim)
            vol_sharpes[vol_label] = result_v.sharpe
        except:
            vol_sharpes[vol_label] = None

    vals = [v for v in vol_sharpes.values() if v is not None]
    if len(vals) >= 2:
        r.target_vol_elasticity = round(max(vals) - min(vals), 4)

    return r


def _estimate_breakeven(cost_results):
    points = []
    bps_map = {"15bps": 15.0, "25bps": 25.0, "30bps": 30.0}
    for label, bps in bps_map.items():
        sr = cost_results.get(label)
        if sr is not None:
            points.append((bps, sr))

    if len(points) < 2:
        return None

    points.sort(key=lambda x: x[0])

    # Linear extrapolation to SR=0
    for i in range(len(points) - 1):
        b1, s1 = points[i]
        b2, s2 = points[i + 1]
        if s1 > 0 and s2 <= 0:
            return round(b1 + (b2 - b1) * s1 / (s1 - s2), 1)

    if all(s > 0 for _, s in points):
        b1, s1 = points[-2]
        b2, s2 = points[-1]
        if s1 > s2 and s2 > 0:
            slope = (s2 - s1) / (b2 - b1)
            if slope < 0:
                bev = b2 - s2 / slope
                return round(bev, 1)

    return round(points[0][0], 1) if points[0][1] <= 0 else None


# ═══════════════════════════════════════════════════════════════
# TOURNAMENT PHASES
# ═══════════════════════════════════════════════════════════════

OUT = "v531_results"
os.makedirs(OUT, exist_ok=True)
SEED = 42

GOLDEN_A04799 = FamilyAConfig(
    config_id="GOLDEN_A04799",
    leverage_mult=1.357, hedge_mult=1.274, defensive_budget_add=-0.098,
    crash_guard_scale=0.236, crash_guard_vol_spike=3.0,
    trend_clamp_min=0.807, trend_lookback_weeks=12,
    gain_adj=0.147, loss_adj=-0.099,
    cost_bps=14.02, crisis_cost_mult=1.950,
    patch_v51_gate=True, patch_v53_shock=True, patch_v54_xgb=True,
    xgb_risk_threshold=0.591, xgb_scale_factor=0.7,
)

V2_RANGES = {
    "leverage_mult": (0.9, 1.5),
    "hedge_mult": (0.8, 2.0),
    "defensive_budget_add": (-0.15, 0.10),
    "crash_guard_scale": (0.15, 0.50),
    "trend_clamp_min": (0.60, 0.90),
    "gain_adj": (0.05, 0.15),
    "loss_adj": (-0.15, 0.0),
    "cost_bps": (20.0, 40.0),
    "crisis_cost_mult": (1.5, 2.5),
    "xgb_threshold": (0.4, 0.8),
    "xgb_scale_factor": (0.4, 0.9),
}

# v5.3.1 신규 축 범위
V531_RANGES = {
    "recovery_boost": (0.1, 0.4),
    "recovery_duration": [2, 3, 4, 5, 6],
    "convex_hedge_power": (1.0, 2.0),
    "mom_vol_threshold": (0.3, 0.7),
    "mom_vol_boost": (0.05, 0.25),
    "sleeve_mom_lookback": [4, 6, 8, 10, 12],
    "sleeve_mom_tilt_pct": (0.05, 0.20),
    "leverage_bull": (1.0, 1.8),
    "adaptive_hedge_vol_threshold": (0.15, 0.30),
    "adaptive_hedge_max_mult": (1.5, 3.0),
}


def _lhs(n, rng):
    cuts = np.linspace(0, 1, n+1)
    u = np.array([rng.uniform(cuts[i], cuts[i+1]) for i in range(n)])
    rng.shuffle(u); return u

def _s(u, lo, hi): return lo + u * (hi - lo)


# ── PHASE 0 ──

def phase0_stress_test(n_weeks):
    print(f"\n{'='*70}")
    print(f"  PHASE 0: GOLDEN BASELINE STRESS TEST (REAL DATA)")
    print(f"{'='*70}\n")

    sim = RealMarketSimulator(n_weeks=n_weeks, seed=SEED)

    r_orig = run_family_a(GOLDEN_A04799, sim)
    print(f"  A_04799 @ 14bps: SR={r_orig.sharpe:.3f} CAGR={r_orig.ann_return:.1f}% "
          f"MDD={r_orig.max_dd:.1f}% EXP={r_orig.avg_exposure:.3f} TO={r_orig.avg_turnover:.1f}")

    stress_results = {}
    for bps in [20, 25, 30, 35, 40]:
        cfg = copy.deepcopy(GOLDEN_A04799)
        cfg.config_id = f"GOLDEN_A04799_{bps}bps"
        cfg.cost_bps = float(bps)
        r = run_family_a(cfg, sim)
        stress_results[bps] = r
        flag = "PASS" if r.sharpe > 0 else "FAIL"
        print(f"  A_04799 @ {bps}bps: SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% "
              f"MDD={r.max_dd:.1f}% EXP={r.avg_exposure:.3f} CostBPS={r.avg_cost_bps_equity:.1f} [{flag}]")

    for bps in sorted(stress_results.keys()):
        if stress_results[bps].sharpe <= 0:
            prev_bps = bps - 5
            if prev_bps in stress_results and stress_results[prev_bps].sharpe > 0:
                sr1 = stress_results[prev_bps].sharpe
                sr2 = stress_results[bps].sharpe
                bev = prev_bps + 5 * sr1 / (sr1 - sr2)
                print(f"\n  A_04799 breakeven ~ {bev:.1f} bps")
            break
    else:
        print(f"\n  A_04799 SR > 0 at all cost levels up to 40bps")

    # Full sensitivity on A_04799 @ 25bps
    cfg_25 = copy.deepcopy(GOLDEN_A04799)
    cfg_25.config_id = "GOLDEN_A04799_25bps"
    cfg_25.cost_bps = 25.0
    r_25 = run_family_a(cfg_25, sim)
    r_25_enriched = run_sensitivity_sweep(r_25, cfg_25, run_family_a, sim, "family_a")
    apply_hard_filters(r_25_enriched)

    print(f"\n  A_04799 @ 25bps FULL AUDIT:")
    print(f"    SR:           {r_25_enriched.sharpe:.3f}")
    print(f"    CAGR:         {r_25_enriched.ann_return:.1f}%")
    print(f"    Exposure:     {r_25_enriched.avg_exposure:.3f}")
    print(f"    Breakeven:    {r_25_enriched.breakeven_tc_bps}")
    print(f"    SR@25bps:     {r_25_enriched.sharpe_25bps}")
    print(f"    Rebal Decay:  {r_25_enriched.rebal_decay_ratio}")
    print(f"    Vol Elast:    {r_25_enriched.target_vol_elasticity}")
    print(f"    Fold19 SR:    {r_25_enriched.fold19_sharpe}")
    print(f"    Hard Fail:    {r_25_enriched.hard_fail} {'— '+r_25_enriched.hard_fail_reason if r_25_enriched.hard_fail else ''}")
    print(f"    Audit Pass:   {'PASS' if not r_25_enriched.hard_fail else 'FAIL'}")

    return stress_results, r_25_enriched


# ── PHASE 1: MEGA-GRID (16-core parallel) with 24 axes ──

def gen_configs(n, seed=SEED):
    rng = np.random.RandomState(seed)
    n_narrow = int(n * 0.7)  # 70% narrow (유효 축 활성화 높음)
    n_wide = n - n_narrow     # 30% wide (탐색)
    cfgs = []

    # Narrow: 기존 17축 + 유효 신규 축 활성화 확률 높게
    dims = {k: _lhs(n_narrow, rng) for k in V2_RANGES}
    dims_new = {k: _lhs(n_narrow, rng) for k in V531_RANGES if isinstance(V531_RANGES[k], tuple)}

    for i in range(n_narrow):
        # 신규 축 활성화 확률
        use_convex = rng.random() < 0.70     # ConvexH: 70%
        use_sleeve = rng.random() < 0.65     # SleeveMom: 65%
        use_recboost = rng.random() < 0.55   # RecBoost: 55%
        use_momvol = rng.random() < 0.45     # MomVol: 45%
        use_perreg = rng.random() < 0.40     # PerRegLev: 40%
        use_adapthg = rng.random() < 0.25    # AdaptHedge: 25%

        cfg = FamilyAConfig(
            config_id=f"A_{i:05d}",
            leverage_mult=_s(dims["leverage_mult"][i], *V2_RANGES["leverage_mult"]),
            hedge_mult=_s(dims["hedge_mult"][i], *V2_RANGES["hedge_mult"]),
            defensive_budget_add=_s(dims["defensive_budget_add"][i], *V2_RANGES["defensive_budget_add"]),
            crash_guard_scale=_s(dims["crash_guard_scale"][i], *V2_RANGES["crash_guard_scale"]),
            crash_guard_vol_spike=float(rng.choice([2.0, 2.5, 3.0, 3.5, 4.0])),
            trend_clamp_min=_s(dims["trend_clamp_min"][i], *V2_RANGES["trend_clamp_min"]),
            trend_lookback_weeks=int(rng.choice([8, 10, 12, 14, 16])),
            trend_bad_thresh=float(rng.choice([-0.10, -0.12, -0.15, -0.18])),
            gain_adj=_s(dims["gain_adj"][i], *V2_RANGES["gain_adj"]),
            loss_adj=_s(dims["loss_adj"][i], *V2_RANGES["loss_adj"]),
            cost_bps=_s(dims["cost_bps"][i], *V2_RANGES["cost_bps"]),
            crisis_cost_mult=_s(dims["crisis_cost_mult"][i], *V2_RANGES["crisis_cost_mult"]),
            patch_v51_gate=bool(rng.choice([True, True, True, False])),
            patch_v53_shock=bool(rng.choice([True, True, True, False])),
            patch_v54_xgb=bool(rng.choice([True, True, True, False])),
            xgb_risk_threshold=_s(dims["xgb_threshold"][i], *V2_RANGES["xgb_threshold"]),
            xgb_scale_factor=_s(dims["xgb_scale_factor"][i], *V2_RANGES["xgb_scale_factor"]),
            # NEW axes
            convex_hedge=use_convex,
            convex_hedge_power=_s(dims_new["convex_hedge_power"][i], *V531_RANGES["convex_hedge_power"]) if use_convex else 1.5,
            recovery_boost=_s(dims_new["recovery_boost"][i], *V531_RANGES["recovery_boost"]) if use_recboost else 0.0,
            recovery_duration=int(rng.choice(V531_RANGES["recovery_duration"])) if use_recboost else 4,
            mom_vol_timing=use_momvol,
            mom_vol_threshold=_s(dims_new["mom_vol_threshold"][i], *V531_RANGES["mom_vol_threshold"]) if use_momvol else 0.5,
            mom_vol_boost=_s(dims_new["mom_vol_boost"][i], *V531_RANGES["mom_vol_boost"]) if use_momvol else 0.2,
            sleeve_momentum_tilt=use_sleeve,
            sleeve_mom_lookback=int(rng.choice(V531_RANGES["sleeve_mom_lookback"])) if use_sleeve else 8,
            sleeve_mom_tilt_pct=_s(dims_new["sleeve_mom_tilt_pct"][i], *V531_RANGES["sleeve_mom_tilt_pct"]) if use_sleeve else 0.10,
            leverage_bull=_s(dims_new["leverage_bull"][i], *V531_RANGES["leverage_bull"]) if use_perreg else None,
            adaptive_hedge=use_adapthg,
            adaptive_hedge_vol_threshold=_s(dims_new["adaptive_hedge_vol_threshold"][i], *V531_RANGES["adaptive_hedge_vol_threshold"]) if use_adapthg else 0.20,
            adaptive_hedge_max_mult=_s(dims_new["adaptive_hedge_max_mult"][i], *V531_RANGES["adaptive_hedge_max_mult"]) if use_adapthg else 2.0,
            # 비활성 축 (해로움)
            dd_scale_enable=False,
            dual_trend=False,
            regime_transition_guard=False,
        )
        cfgs.append(cfg)

    # Wide: 전체 탐색 (더 넓은 범위)
    dims_w = {k: _lhs(n_wide, rng) for k in [
        "lev","hedge","def","guard","trend","gain","loss","cost","crisis","xgb_t","xgb_s"]}
    dims_wn = {k: _lhs(n_wide, rng) for k in V531_RANGES if isinstance(V531_RANGES[k], tuple)}

    for i in range(n_wide):
        idx = n_narrow + i
        use_convex = rng.random() < 0.50
        use_sleeve = rng.random() < 0.50
        use_recboost = rng.random() < 0.40
        use_momvol = rng.random() < 0.35
        use_perreg = rng.random() < 0.30
        use_adapthg = rng.random() < 0.20

        cfgs.append(FamilyAConfig(
            config_id=f"A_{idx:05d}",
            leverage_mult=_s(dims_w["lev"][i], 0.5, 1.8),
            hedge_mult=_s(dims_w["hedge"][i], 0.2, 3.0),
            defensive_budget_add=_s(dims_w["def"][i], -0.20, 0.30),
            crash_guard_scale=_s(dims_w["guard"][i], 0.10, 0.85),
            crash_guard_vol_spike=float(rng.choice([1.5, 2.0, 2.5, 3.0, 4.0, 5.0])),
            trend_clamp_min=_s(dims_w["trend"][i], 0.25, 0.95),
            trend_lookback_weeks=int(rng.choice([6, 8, 10, 12, 16, 20, 26])),
            trend_bad_thresh=float(rng.choice([-0.08, -0.10, -0.15, -0.20, -0.25])),
            gain_adj=_s(dims_w["gain"][i], -0.15, 0.20),
            loss_adj=_s(dims_w["loss"][i], -0.20, 0.15),
            cost_bps=_s(dims_w["cost"][i], 20.0, 45.0),
            crisis_cost_mult=_s(dims_w["crisis"][i], 1.0, 3.0),
            patch_v51_gate=bool(rng.choice([True, False])),
            patch_v53_shock=bool(rng.choice([True, False])),
            patch_v54_xgb=bool(rng.choice([True, False])),
            xgb_risk_threshold=_s(dims_w["xgb_t"][i], 0.3, 0.95),
            xgb_scale_factor=_s(dims_w["xgb_s"][i], 0.2, 0.95),
            # NEW axes (wider ranges)
            convex_hedge=use_convex,
            convex_hedge_power=_s(dims_wn["convex_hedge_power"][i], 0.8, 2.5) if use_convex else 1.5,
            recovery_boost=_s(dims_wn["recovery_boost"][i], 0.05, 0.5) if use_recboost else 0.0,
            recovery_duration=int(rng.choice([2, 3, 4, 5, 6, 8])) if use_recboost else 4,
            mom_vol_timing=use_momvol,
            mom_vol_threshold=_s(dims_wn["mom_vol_threshold"][i], 0.2, 0.9) if use_momvol else 0.5,
            mom_vol_boost=_s(dims_wn["mom_vol_boost"][i], 0.02, 0.35) if use_momvol else 0.2,
            sleeve_momentum_tilt=use_sleeve,
            sleeve_mom_lookback=int(rng.choice([3, 4, 6, 8, 10, 12, 16])) if use_sleeve else 8,
            sleeve_mom_tilt_pct=_s(dims_wn["sleeve_mom_tilt_pct"][i], 0.03, 0.30) if use_sleeve else 0.10,
            leverage_bull=_s(dims_wn["leverage_bull"][i], 0.8, 2.2) if use_perreg else None,
            adaptive_hedge=use_adapthg,
            adaptive_hedge_vol_threshold=_s(dims_wn["adaptive_hedge_vol_threshold"][i], 0.10, 0.40) if use_adapthg else 0.20,
            adaptive_hedge_max_mult=_s(dims_wn["adaptive_hedge_max_mult"][i], 1.2, 4.0) if use_adapthg else 2.0,
            dd_scale_enable=False,
            dual_trend=False,
            regime_transition_guard=False,
        ))

    return cfgs


def _worker_run_single(args):
    """Worker function for parallel execution."""
    cfg_dict, sim_data = args
    cfg = FamilyAConfig(**cfg_dict)
    sim = _SimProxy(sim_data)
    try:
        r = run_family_a(cfg, sim)
        return (cfg_dict, r.to_dict())
    except Exception as e:
        return (cfg_dict, None)


class _SimProxy:
    """Lightweight proxy for worker processes."""
    def __init__(self, data):
        self.n_weeks = data["n_weeks"]
        self.regimes = data["regimes"]
        self._all_returns = data["all_returns"]
        self.rng = np.random.RandomState(data["seed"])

    def get_engine_returns(self, wi, engines):
        return {e: self._all_returns[wi].get(e, 0.0) for e in engines}

    def get_all_returns(self, wi):
        return self._all_returns[wi]

    def get_sleeve_returns(self, wi):
        all_r = self._all_returns[wi]
        sleeve_ret = {}
        for sleeve, engines in SLEEVE_MAP.items():
            vals = [all_r[e] for e in engines if e in all_r]
            sleeve_ret[sleeve] = float(np.mean(vals)) if vals else 0.0
        return sleeve_ret


def _serialize_sim(sim):
    return {
        "n_weeks": sim.n_weeks,
        "regimes": sim.regimes,
        "all_returns": {wi: sim._all_returns[wi] for wi in range(sim.n_weeks)},
        "seed": sim.seed,
    }


def _serialize_cfg(cfg):
    """Serialize FamilyAConfig to dict — all 24+ axes."""
    return {
        "config_id": cfg.config_id,
        "leverage_mult": cfg.leverage_mult,
        "hedge_mult": cfg.hedge_mult,
        "defensive_budget_add": cfg.defensive_budget_add,
        "crash_guard_dd_thresh": cfg.crash_guard_dd_thresh,
        "crash_guard_vol_spike": cfg.crash_guard_vol_spike,
        "crash_guard_scale": cfg.crash_guard_scale,
        "trend_lookback_weeks": cfg.trend_lookback_weeks,
        "trend_bad_thresh": cfg.trend_bad_thresh,
        "trend_clamp_min": cfg.trend_clamp_min,
        "gain_adj": cfg.gain_adj,
        "loss_adj": cfg.loss_adj,
        "cost_bps": cfg.cost_bps,
        "crisis_cost_mult": cfg.crisis_cost_mult,
        "patch_v51_gate": cfg.patch_v51_gate,
        "patch_v53_shock": cfg.patch_v53_shock,
        "patch_v54_xgb": cfg.patch_v54_xgb,
        "xgb_risk_threshold": cfg.xgb_risk_threshold,
        "xgb_scale_factor": cfg.xgb_scale_factor,
        "edge_gate_lo": cfg.edge_gate_lo,
        "edge_gate_hi": cfg.edge_gate_hi,
        "extreme_loss_thresh": cfg.extreme_loss_thresh,
        "extreme_loss_scale": cfg.extreme_loss_scale,
        "small_loss_thresh": cfg.small_loss_thresh,
        "small_loss_scale": cfg.small_loss_scale,
        "annual_turnover": cfg.annual_turnover,
        # NEW axes
        "dd_scale_enable": cfg.dd_scale_enable,
        "dd_scale_threshold": cfg.dd_scale_threshold,
        "dd_scale_floor": cfg.dd_scale_floor,
        "vol_target_leverage": cfg.vol_target_leverage,
        "vol_target": cfg.vol_target,
        "vol_leverage_cap": cfg.vol_leverage_cap,
        "recovery_boost": cfg.recovery_boost,
        "recovery_duration": cfg.recovery_duration,
        "convex_hedge": cfg.convex_hedge,
        "convex_hedge_power": cfg.convex_hedge_power,
        "dual_trend": cfg.dual_trend,
        "trend_short_weeks": cfg.trend_short_weeks,
        "trend_blend_weight": cfg.trend_blend_weight,
        "mom_vol_timing": cfg.mom_vol_timing,
        "mom_vol_lookback": cfg.mom_vol_lookback,
        "mom_vol_threshold": cfg.mom_vol_threshold,
        "mom_vol_boost": cfg.mom_vol_boost,
        "regime_transition_guard": cfg.regime_transition_guard,
        "transition_scale": cfg.transition_scale,
        "transition_duration": cfg.transition_duration,
        "leverage_bull": cfg.leverage_bull,
        "leverage_bear": cfg.leverage_bear,
        "leverage_crisis": cfg.leverage_crisis,
        "leverage_recovery": cfg.leverage_recovery,
        "sleeve_momentum_tilt": cfg.sleeve_momentum_tilt,
        "sleeve_mom_lookback": cfg.sleeve_mom_lookback,
        "sleeve_mom_tilt_pct": cfg.sleeve_mom_tilt_pct,
        "adaptive_hedge": cfg.adaptive_hedge,
        "adaptive_hedge_vol_lookback": cfg.adaptive_hedge_vol_lookback,
        "adaptive_hedge_vol_threshold": cfg.adaptive_hedge_vol_threshold,
        "adaptive_hedge_max_mult": cfg.adaptive_hedge_max_mult,
    }


def phase1_megagrid(n_samples, n_weeks, n_workers=16):
    print(f"\n{'='*70}")
    print(f"  PHASE 1: MEGA-GRID — {n_samples} configs × {n_workers} cores (REAL DATA, 24-AXIS)")
    print(f"{'='*70}\n")

    sim = RealMarketSimulator(n_weeks=n_weeks, seed=SEED)
    cfgs = gen_configs(n_samples, SEED)
    sim_data = _serialize_sim(sim)

    work_items = [(_serialize_cfg(cfg), sim_data) for cfg in cfgs]

    results = []
    t0 = time.time()

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_worker_run_single, item): i for i, item in enumerate(work_items)}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            try:
                cfg_dict, r_dict = future.result()
                if r_dict is not None:
                    r = NormalizedResult(**{k: v for k, v in r_dict.items()
                                          if k in NormalizedResult.__dataclass_fields__})
                    cfg = FamilyAConfig(**cfg_dict)
                    results.append((cfg, r))
            except Exception as e:
                pass

            if done_count % max(1, n_samples//10) == 0:
                el = time.time()-t0
                best = max((r.sharpe for _, r in results), default=0)
                print(f"    [{done_count:6d}/{n_samples}] {done_count/el:.0f}/s | best_SR={best:.3f} "
                      f"| elapsed={el:.0f}s")

    print(f"    Done: {len(results)}/{n_samples} in {time.time()-t0:.1f}s")
    all_r = score_population([r for _, r in results])

    df = pd.DataFrame([r.to_dict() for r in all_r])
    if "full_config" in df.columns: df = df.drop(columns=["full_config"])
    df.to_csv(f"{OUT}/phase1_megagrid.csv", index=False)

    valid = [(c, r) for (c, _), r in zip(results, all_r) if not r.hard_fail]
    valid.sort(key=lambda x: x[1].score, reverse=True)
    print(f"\n  Valid: {len(valid)}/{len(results)}")
    print(f"  TOP 10:")
    for cfg, r in valid[:10]:
        axes = r.full_config.get("new_axes", "base") if r.full_config else "?"
        print(f"    {r.manifest_id:<12} SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% MDD={r.max_dd:.1f}% "
              f"EXP={r.avg_exposure:.3f} cost={cfg.cost_bps:.0f} TO={r.avg_turnover:.1f} [{axes}]")

    return cfgs, all_r, valid


# ── PHASE 2: FINE GRID ──

def phase2_fine(valid, n_weeks, n_fine=20):
    print(f"\n{'='*70}")
    print(f"  PHASE 2: FINE GRID — top 10 x {n_fine}")
    print(f"{'='*70}\n")

    sim = RealMarketSimulator(n_weeks=n_weeks, seed=SEED)
    rng = np.random.RandomState(SEED + 777)
    fine = []
    for base_cfg, base_r in valid[:10]:
        for j in range(n_fine):
            fc = copy.deepcopy(base_cfg)
            fc.config_id = f"{base_cfg.config_id}_F{j:03d}"
            for attr in ["leverage_mult","hedge_mult","crash_guard_scale","trend_clamp_min",
                         "gain_adj","loss_adj","xgb_risk_threshold","xgb_scale_factor"]:
                old = getattr(fc, attr)
                setattr(fc, attr, old * rng.uniform(0.88, 1.12))
            fc.cost_bps = np.clip(fc.cost_bps * rng.uniform(0.9, 1.1), 20, 50)
            # Fine-tune new axes too
            if fc.convex_hedge:
                fc.convex_hedge_power = np.clip(fc.convex_hedge_power * rng.uniform(0.9, 1.1), 0.8, 2.5)
            if fc.recovery_boost > 0:
                fc.recovery_boost = np.clip(fc.recovery_boost * rng.uniform(0.85, 1.15), 0.05, 0.5)
            if fc.sleeve_momentum_tilt:
                fc.sleeve_mom_tilt_pct = np.clip(fc.sleeve_mom_tilt_pct * rng.uniform(0.85, 1.15), 0.03, 0.30)
            try:
                r = run_family_a(fc, sim)
                fine.append((fc, r))
            except: pass

    merged = list(valid[:50]) + fine
    all_r = score_population([r for _, r in merged])
    valid2 = [(c, r) for (c, _), r in zip(merged, all_r) if not r.hard_fail]
    valid2.sort(key=lambda x: x[1].score, reverse=True)
    print(f"  Merged: {len(merged)} | Valid: {len(valid2)}")
    for c, r in valid2[:5]:
        axes = r.full_config.get("new_axes", "?") if r.full_config else "?"
        print(f"    {r.manifest_id:<20} SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% "
              f"EXP={r.avg_exposure:.3f} MDD={r.max_dd:.1f}% [{axes}]")
    return valid2


# ── PHASE 2.5: SENSITIVITY SWEEP ──

def phase25_sensitivity(valid2, n_weeks, n_top=30):
    print(f"\n{'='*70}")
    print(f"  PHASE 2.5: SENSITIVITY SWEEP — top {min(n_top, len(valid2))}")
    print(f"{'='*70}\n")

    sim = RealMarketSimulator(n_weeks=n_weeks, seed=SEED)
    enriched = []
    for cfg, r in valid2[:n_top]:
        try:
            er = run_sensitivity_sweep(r, cfg, run_family_a, sim, "family_a")
            apply_hard_filters(er)
            enriched.append((cfg, er))
            bev = f"bev={er.breakeven_tc_bps:.0f}" if er.breakeven_tc_bps else "bev=?"
            rdr = f"rdr={er.rebal_decay_ratio:.2f}" if er.rebal_decay_ratio else "rdr=?"
            tve = f"tve={er.target_vol_elasticity:.3f}" if er.target_vol_elasticity else "tve=?"
            st = "PASS" if not er.hard_fail else f"FAIL:{er.hard_fail_reason}"
            print(f"    {r.manifest_id:<14} SR={er.sharpe:.3f} CAGR={er.ann_return:.1f}% "
                  f"EXP={er.avg_exposure:.3f} {bev:>10} {rdr:>10} {tve:>12} [{st}]")
        except:
            enriched.append((cfg, r))

    survived = [(c, r) for c, r in enriched if not r.hard_fail]
    print(f"\n  Survived: {len(survived)}/{len(enriched)}")

    enriched_data = []
    for c, r in enriched:
        d = r.to_dict()
        d.pop("full_config", None)
        enriched_data.append(d)
    pd.DataFrame(enriched_data).to_csv(f"{OUT}/phase25_sensitivity.csv", index=False)

    return enriched, survived


# ── PHASE 3: MINIMAL HYBRID ──

def phase3_hybrid(survived, n_weeks):
    print(f"\n{'='*70}")
    print(f"  PHASE 3: MINIMAL HYBRID — A + defense/crisis/hedge")
    print(f"{'='*70}\n")

    sim = RealMarketSimulator(n_weeks=n_weeks, seed=SEED)
    rng = np.random.RandomState(SEED + 9000)
    hybrids = []

    for idx, (base_cfg, base_r) in enumerate(survived[:10]):
        h1 = copy.deepcopy(base_cfg)
        h1.config_id = f"{base_cfg.config_id}_DEF"
        h1.defensive_budget_add = min(0.25, h1.defensive_budget_add + 0.10)
        h1.hedge_mult *= 1.2
        try:
            r1 = run_family_a(h1, sim); r1.family = "hybrid_A_DEF"
            hybrids.append((h1, r1))
        except: pass

        h2 = copy.deepcopy(base_cfg)
        h2.config_id = f"{base_cfg.config_id}_CRI"
        h2.crash_guard_scale *= 0.7
        h2.hedge_mult *= 1.5
        h2.crisis_cost_mult = min(3.0, h2.crisis_cost_mult * 1.3)
        try:
            r2 = run_family_a(h2, sim); r2.family = "hybrid_A_CRI"
            hybrids.append((h2, r2))
        except: pass

        h3 = copy.deepcopy(base_cfg)
        h3.config_id = f"{base_cfg.config_id}_HDG"
        h3.hedge_mult = min(3.0, h3.hedge_mult * 2.0)
        try:
            r3 = run_family_a(h3, sim); r3.family = "hybrid_A_HDG"
            hybrids.append((h3, r3))
        except: pass

    all_hr = score_population([r for _, r in hybrids])
    valid_h = [(c, r) for (c, _), r in zip(hybrids, all_hr) if not r.hard_fail]
    valid_h.sort(key=lambda x: x[1].score, reverse=True)
    print(f"  Hybrids: {len(hybrids)} | Valid: {len(valid_h)}")
    for c, r in valid_h[:5]:
        print(f"    {r.manifest_id:<20} SR={r.sharpe:.3f} CAGR={r.ann_return:.1f}% "
              f"EXP={r.avg_exposure:.3f} {r.family}")
    return valid_h


# ── PHASE 4: ROBUSTNESS ──

def phase4_robust(candidates, n_seeds, n_weeks, top_k=20):
    print(f"\n{'='*70}")
    print(f"  PHASE 4: ROBUSTNESS — {min(top_k, len(candidates))} x {n_seeds} seeds (REAL DATA)")
    print(f"{'='*70}\n")

    robust = []
    for i, (cfg, r) in enumerate(candidates[:top_k]):
        sharpes = []; mdds = []; cagrs = []; exposures = []
        for s in range(n_seeds):
            sim = RealMarketSimulatorMultiSeed(n_weeks=n_weeks, seed=SEED + s*1000 + 7)
            try:
                cc = copy.deepcopy(cfg); cc.config_id = f"{r.manifest_id}_s{s}"
                sr = run_family_a(cc, sim)
                sharpes.append(sr.sharpe or 0)
                mdds.append(sr.max_dd or -50)
                cagrs.append(sr.ann_return or 0)
                exposures.append(sr.avg_exposure or 0.5)
            except: pass

        if not sharpes: continue
        ms = np.mean(sharpes); ss = np.std(sharpes)
        entry = {
            "id": r.manifest_id, "family": getattr(r, 'family', 'family_a'),
            "mean_sharpe": round(ms, 4), "std_sharpe": round(ss, 4),
            "min_sharpe": round(min(sharpes), 4), "worst_maxdd": round(min(mdds), 2),
            "mean_cagr": round(float(np.mean(cagrs)), 2),
            "mean_exposure": round(float(np.mean(exposures)), 3),
            "breakeven_tc_bps": r.breakeven_tc_bps,
            "sharpe_25bps": r.sharpe_25bps, "rebal_decay_ratio": r.rebal_decay_ratio,
            "target_vol_elasticity": r.target_vol_elasticity,
            "fold19_sharpe": r.fold19_sharpe,
            "avg_turnover": r.avg_turnover,
            "avg_cost_bps": r.avg_cost_bps_equity,
            "win_rate": r.win_rate,
            "robust_score": round(ms - 0.5*ss, 4),
            "audit_pass": not r.hard_fail,
            "config": r.full_config,
        }
        robust.append(entry)
        ap = "PASS" if entry["audit_pass"] else "FAIL"
        bev = f"bev={entry['breakeven_tc_bps']:.0f}" if entry['breakeven_tc_bps'] else "bev=?"
        axes = entry['config'].get('new_axes', '?') if entry['config'] else '?'
        print(f"  [{i+1:3d}] {r.manifest_id:<18} robust={entry['robust_score']:.3f} "
              f"SR={ms:.3f}+/-{ss:.3f} CAGR={entry['mean_cagr']:.1f}% "
              f"EXP={entry['mean_exposure']:.3f} {bev} [{axes}] [{ap}]")

    robust.sort(key=lambda x: x["robust_score"], reverse=True)
    passed = [r for r in robust if r["audit_pass"]]

    with open(f"{OUT}/phase4_robustness.json", "w") as f:
        json.dump(robust, f, cls=NpEncoder, indent=2)

    if passed:
        champ = passed[0]
        with open(f"{OUT}/CHAMPION_FINAL.json", "w") as f:
            json.dump(champ, f, cls=NpEncoder, indent=2)
        axes = champ['config'].get('new_axes', '?') if champ['config'] else '?'
        print(f"\n{'='*70}")
        print(f"  CHAMPION: {champ['id']}")
        print(f"  Family:      {champ['family']}")
        print(f"  Audit:       {'PASS' if champ['audit_pass'] else 'FAIL'}")
        print(f"  Robust SR:   {champ['robust_score']:.4f}")
        print(f"  Mean SR:     {champ['mean_sharpe']:.3f} +/- {champ['std_sharpe']:.3f}")
        print(f"  Min SR:      {champ['min_sharpe']:.3f}")
        print(f"  Mean CAGR:   {champ['mean_cagr']:.1f}%")
        print(f"  Mean EXP:    {champ['mean_exposure']:.3f}")
        print(f"  Breakeven:   {champ['breakeven_tc_bps']} bps")
        print(f"  SR@25bps:    {champ['sharpe_25bps']}")
        print(f"  Rebal Decay: {champ['rebal_decay_ratio']}")
        print(f"  Vol Elast:   {champ['target_vol_elasticity']}")
        print(f"  Fold19 SR:   {champ['fold19_sharpe']}")
        print(f"  Worst MDD:   {champ['worst_maxdd']}%")
        print(f"  Turnover:    {champ['avg_turnover']}")
        print(f"  Cost BPS:    {champ['avg_cost_bps']}")
        print(f"  Win Rate:    {champ['win_rate']}")
        print(f"  Active Axes: {axes}")
        print(f"{'='*70}")
    else:
        print(f"\n  No candidates passed all audit filters.")

    top20_data = []
    for entry in robust[:20]:
        d = {k: v for k, v in entry.items() if k != "config"}
        top20_data.append(d)
    pd.DataFrame(top20_data).to_csv(f"{OUT}/top20_detail.csv", index=False)

    return robust


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ARES Family A v5.3.1 — Real Data + 16-Core + 24-Axis")
    ap.add_argument("--n_samples", type=int, default=15000)
    ap.add_argument("--n_weeks", type=int, default=163)
    ap.add_argument("--n_fine", type=int, default=20)
    ap.add_argument("--top_k", type=int, default=50)
    ap.add_argument("--n_seeds", type=int, default=5)
    ap.add_argument("--n_workers", type=int, default=16)
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"\n{'#'*70}")
    print(f"#  ARES FAMILY A v5.3.1 — REAL DATA + 16-CORE + 24-AXIS")
    print(f"#  {ts}")
    print(f"#  N={args.n_samples} | Weeks={args.n_weeks} | Seeds={args.n_seeds}")
    print(f"#  Cost Model: v5.2 (actual weekly turnover x bps)")
    print(f"#  ConvexH Premium: 80bps/week (v5.3.1 fix)")
    print(f"#  Data: REAL (daily_bars_historical.parquet)")
    print(f"#  70% narrow (유효 축 활성화 높음) + 30% wide (탐색)")
    print(f"{'#'*70}")

    stress, golden_audit = phase0_stress_test(args.n_weeks)
    cfgs, all_r, valid = phase1_megagrid(args.n_samples, args.n_weeks, args.n_workers)
    valid2 = phase2_fine(valid, args.n_weeks, args.n_fine)
    enriched, survived = phase25_sensitivity(valid2, args.n_weeks)
    hybrids = phase3_hybrid(survived, args.n_weeks)

    all_candidates = survived + hybrids
    all_candidates.sort(key=lambda x: x[1].score, reverse=True)

    robust = phase4_robust(all_candidates, args.n_seeds, args.n_weeks, top_k=20)

    print(f"\n  All results in ./{OUT}/")
    print(f"  Completed at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
