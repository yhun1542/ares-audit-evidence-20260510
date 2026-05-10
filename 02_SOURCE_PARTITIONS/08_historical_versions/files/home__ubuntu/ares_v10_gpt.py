#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
ARES v10.0 - Sharpe 3.0+ Oriented Production Engine (Walk-Forward, No Lookahead)
================================================================================

Key upgrades vs v9.7:
- Strict no-lookahead execution: signals at t close -> positions applied from t+1
- Regime stabilization: VIX + SPY realized vol + trend + hysteresis
- Regime-aware factor mix:
  * ULTRA_LOW/LOW: momentum-led
  * MODERATE: balanced momentum + quality + low-vol + beta-
  * HIGH: defensive + low-vol + reversal + risk-off sleeve
  * CRISIS: mostly risk-off / cash
- Risk-off sleeve using ETFs if available (IEF/TLT/SHY/SGOV/GLD)
- Vol targeting (EWMA/rolling) combined with regime leverage
- Correlation & turnover-aware construction
- Walk-forward ICIR weight learning with shrinkage (anti-overfit)

Hard constraints addressed:
1) No look-ahead bias (positions lagged by 1 trading day)
2) Walk-forward validation built-in
3) Realistic transaction cost: 0.2% + slippage 0.1% => 0.3% of turnover
4) OOS validation required for 2021-2024 (default runner)
5) Regime robustness tooling + risk-off sleeve for HIGH/CRISIS

================================================================================
"""

from __future__ import annotations

import os
import sys
import json
import math
import time
import sqlite3
import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Iterable, Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_LEVEL = os.getenv("ARES_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ARES_v10.0")


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
def safe_float(x: Any, default: float = np.nan) -> float:
    try:
        return float(x)
    except Exception:
        return default


def annualized_sharpe(returns: pd.Series, rf_annual: float = 0.05) -> float:
    r = returns.dropna()
    if len(r) < 20:
        return 0.0
    rf_daily = rf_annual / 252.0
    ex = r - rf_daily
    sd = ex.std(ddof=1)
    if sd <= 1e-12:
        return 0.0
    return float(np.sqrt(252.0) * ex.mean() / sd)


def annualized_sortino(returns: pd.Series, rf_annual: float = 0.05) -> float:
    r = returns.dropna()
    if len(r) < 20:
        return 0.0
    rf_daily = rf_annual / 252.0
    ex = r - rf_daily
    downside = ex[ex < 0]
    dd = downside.std(ddof=1)
    if dd <= 1e-12:
        return 0.0
    return float(np.sqrt(252.0) * ex.mean() / dd)


def max_drawdown(cum: pd.Series) -> float:
    if cum.empty:
        return 0.0
    peak = cum.cummax()
    dd = (cum / peak) - 1.0
    return float(dd.min())


def winsorize_series(s: pd.Series, pct: float = 0.01) -> pd.Series:
    if s.empty:
        return s
    lo = np.nanpercentile(s.values.astype(float), pct * 100.0)
    hi = np.nanpercentile(s.values.astype(float), (1.0 - pct) * 100.0)
    return s.clip(lower=lo, upper=hi)


def zscore_xs(s: pd.Series, eps: float = 1e-12) -> pd.Series:
    s2 = s.astype(float)
    mu = np.nanmean(s2.values)
    sd = np.nanstd(s2.values)
    if not np.isfinite(sd) or sd < eps:
        return pd.Series(0.0, index=s.index)
    return (s2 - mu) / sd


def rank_xs_to_unit(s: pd.Series) -> pd.Series:
    """Rank normalize to [-1, 1]."""
    if s.empty:
        return s
    r = s.rank(pct=True)
    return r * 2.0 - 1.0


def softmax(x: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    x = np.asarray(x, dtype=float) / max(temperature, 1e-12)
    x = x - np.nanmax(x)
    e = np.exp(np.nan_to_num(x, nan=-50.0))
    denom = e.sum()
    if denom <= 1e-12:
        return np.ones_like(e) / len(e)
    return e / denom


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
@dataclass
class AresV10Config:
    # DB
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    price_table: str = "daily_ohlcv"
    vix_symbol: str = "VIX"
    benchmark_symbol: str = "SPY"

    # Universe
    core_equity_universe: List[str] = field(default_factory=lambda: [
        # Tech
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","AVGO","TSLA","AMD","ADBE","CRM","QCOM","TXN",
        # Fin
        "JPM","BAC","GS","MS","BLK","SCHW","AXP","V","MA",
        # Health
        "UNH","JNJ","LLY","MRK","ABBV","TMO","ABT","DHR",
        # Staples/Defensive-ish large caps (if present)
        "PG","KO","PEP","COST","WMT","MCD",
    ])

    # Risk-off sleeve ETFs (used if present in DB)
    risk_off_universe: List[str] = field(default_factory=lambda: [
        "SHY","IEF","TLT","SGOV","BIL","GLD"
    ])

    # Sector map optional (used for caps if symbol exists)
    sector_map: Dict[str, str] = field(default_factory=dict)
    max_sector_weight: float = 0.45

    # Rebalance & execution
    rebalance_days: int = 21
    warmup_days: int = 260
    signal_lag_days: int = 1  # IMPORTANT: apply weights from next trading day (no lookahead)

    # Costs
    commission: float = 0.002  # 0.2%
    slippage: float = 0.001    # 0.1%
    high_vol_cost_mult: float = 1.15  # optional: HIGH regime cost multiplier
    crisis_cost_mult: float = 1.30    # optional: CRISIS regime cost multiplier

    # Regime model
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 19.0
    vix_high: float = 23.0
    vix_crisis: float = 28.0

    hysteresis_days: int = 5  # min days to keep a regime before switching unless extreme
    spy_ma_fast: int = 50
    spy_ma_slow: int = 200
    spy_rv_window: int = 21

    # Base regime leverage (pre vol-targeting)
    lev_ultra_low: float = 1.35
    lev_low: float = 0.95
    lev_moderate: float = 0.85
    lev_high: float = 0.45
    lev_crisis: float = 0.15

    # Vol targeting
    target_vol_annual: float = 0.12
    vol_ewma_lambda: float = 0.94
    vol_floor: float = 0.05
    vol_cap: float = 0.25
    leverage_cap: float = 1.50
    leverage_floor: float = 0.00

    # Selection & weighting
    n_equities: int = 10
    n_risk_off: int = 2
    max_position: float = 0.18
    min_position: float = 0.00

    # Liquidity filter (ADV$)
    adv_window: int = 20
    min_adv_dollars: float = 5e7  # $50M average dollar volume

    # Feature settings
    vol_window_short: int = 20
    vol_window_long: int = 60
    beta_window: int = 60
    corr_window: int = 60
    rsi_window: int = 14
    reversal_window: int = 5

    winsorize_pct: float = 0.01

    # Risk controls
    dd_halt: float = -0.08
    dd_stop: float = -0.12
    dd_recover: float = 0.04
    dd_scale_halt: float = 0.60
    dd_scale_stop: float = 0.30

    # Correlation control
    max_avg_pairwise_corr: float = 0.60  # if above, reduce equity sleeve exposure
    corr_penalty_scale: float = 0.35

    # Turnover controls (regime-aware multipliers)
    position_buffer: float = 0.04
    max_turnover_per_rebal: float = 0.55
    max_turnover_high_mult: float = 0.70  # HIGH regime: stricter turnover cap
    max_turnover_crisis_mult: float = 0.55

    # Walk-forward ICIR training
    ic_lookahead_days: int = 21    # evaluate forward returns over 1 month
    ic_min_obs: int = 20
    ic_decay_halflife_months: int = 12
    ic_shrinkage: float = 0.35
    ic_min_abs_mean: float = 0.01

    # Reporting / outputs
    out_dir: str = "/home/ubuntu/ares_v10_outputs"


# -----------------------------------------------------------------------------
# SQLite Data Access (robust to schema differences)
# -----------------------------------------------------------------------------
class SQLiteMarketData:
    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg
        self._conn: Optional[sqlite3.Connection] = None

    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.cfg.db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def table_columns(self, table: str) -> List[str]:
        c = self.conn()
        cur = c.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]

    def load_prices(
        self,
        symbols: List[str],
        start: str,
        end: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns:
            close_wide: DataFrame(index=date, columns=symbol) adjusted_close if available else close
            volume_wide: DataFrame(index=date, columns=symbol)
        """
        if not symbols:
            return pd.DataFrame(), pd.DataFrame()

        cols = set(self.table_columns(self.cfg.price_table))
        has_adj = "adjusted_close" in cols
        has_vol = "volume" in cols
        close_col = "adjusted_close" if has_adj else "close"

        ph = ",".join(["?"] * len(symbols))
        q = f"""
            SELECT date, symbol,
                   close as raw_close,
                   {close_col} as px,
                   {('volume' if has_vol else 'NULL')} as volume
            FROM {self.cfg.price_table}
            WHERE symbol IN ({ph})
              AND date BETWEEN ? AND ?
            ORDER BY date, symbol
        """
        df = pd.read_sql_query(q, self.conn(), params=[*symbols, start, end])
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date", "symbol"], keep="last")

        close_wide = df.pivot(index="date", columns="symbol", values="px").sort_index()
        vol_wide = df.pivot(index="date", columns="symbol", values="volume").sort_index()

        close_wide = close_wide.ffill()
        vol_wide = vol_wide.fillna(0.0)

        return close_wide, vol_wide

    def load_single_series(self, symbol: str, start: str, end: str) -> pd.Series:
        close_wide, _ = self.load_prices([symbol], start, end)
        if close_wide.empty or symbol not in close_wide.columns:
            return pd.Series(dtype=float)
        return close_wide[symbol].dropna().astype(float)


# -----------------------------------------------------------------------------
# Regime Model (VIX + SPY trend + realized vol + hysteresis)
# -----------------------------------------------------------------------------
class RegimeModel:
    REGIMES = ["ULTRA_LOW", "LOW", "MODERATE", "HIGH", "CRISIS"]

    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg
        self.current_regime: Optional[str] = None
        self.days_in_regime: int = 0

    def _vix_bucket(self, vix: float) -> str:
        c = self.cfg
        if vix < c.vix_ultra_low:
            return "ULTRA_LOW"
        if vix < c.vix_low:
            return "LOW"
        if vix < c.vix_moderate:
            return "MODERATE"
        if vix < c.vix_high:
            return "HIGH"
        if vix < c.vix_crisis:
            return "HIGH"  # pre-crisis is still HIGH
        return "CRISIS"

    def _trend_state(self, spy: pd.Series) -> Dict[str, float]:
        """Returns trend metrics computed on available history (no future)."""
        out = {"trend_score": 0.0, "is_bear": 0.0}
        if spy is None or spy.dropna().shape[0] < self.cfg.spy_ma_slow + 5:
            return out
        ma_fast = spy.rolling(self.cfg.spy_ma_fast).mean()
        ma_slow = spy.rolling(self.cfg.spy_ma_slow).mean()
        px = spy.iloc[-1]
        tf = ma_fast.iloc[-1]
        ts = ma_slow.iloc[-1]
        trend_score = 0.0
        if np.isfinite(ts) and px > ts:
            trend_score += 0.6
        else:
            trend_score -= 0.6
        if np.isfinite(tf) and np.isfinite(ts) and tf > ts:
            trend_score += 0.4
        else:
            trend_score -= 0.4
        out["trend_score"] = float(np.clip(trend_score, -1.0, 1.0))
        out["is_bear"] = 1.0 if (np.isfinite(ts) and px < ts) else 0.0
        return out

    def _realized_vol_state(self, spy: pd.Series) -> float:
        if spy is None or spy.dropna().shape[0] < self.cfg.spy_rv_window + 5:
            return 0.15
        r = spy.pct_change().dropna()
        rv = r.iloc[-self.cfg.spy_rv_window:].std(ddof=1) * np.sqrt(252.0)
        if not np.isfinite(rv):
            return 0.15
        return float(np.clip(rv, 0.05, 0.60))

    def detect(self, vix_value: float, spy_hist: pd.Series) -> Tuple[str, float, Dict[str, float]]:
        """
        Returns:
            regime, base_leverage (pre vol-targeting), diagnostics
        """
        vix_value = safe_float(vix_value, 20.0)
        vix_reg = self._vix_bucket(vix_value)
        trend = self._trend_state(spy_hist)
        rv = self._realized_vol_state(spy_hist)

        # Combine: If trend is strongly bearish and rv high -> shift one notch risk-off
        reg = vix_reg
        if trend["trend_score"] < -0.6 and rv > 0.22 and reg in ("LOW", "MODERATE"):
            reg = "HIGH"
        if trend["trend_score"] < -0.8 and rv > 0.28:
            reg = "CRISIS"

        # Hysteresis: prevent frequent switching unless CRISIS or big VIX jump
        if self.current_regime is None:
            self.current_regime = reg
            self.days_in_regime = 1
        else:
            if reg == self.current_regime:
                self.days_in_regime += 1
            else:
                allow = False
                if reg == "CRISIS" or self.current_regime == "CRISIS":
                    allow = True
                if vix_value >= self.cfg.vix_crisis:
                    allow = True
                if self.days_in_regime >= self.cfg.hysteresis_days:
                    allow = True

                if allow:
                    self.current_regime = reg
                    self.days_in_regime = 1
                else:
                    reg = self.current_regime
                    self.days_in_regime += 1

        lev_map = {
            "ULTRA_LOW": self.cfg.lev_ultra_low,
            "LOW": self.cfg.lev_low,
            "MODERATE": self.cfg.lev_moderate,
            "HIGH": self.cfg.lev_high,
            "CRISIS": self.cfg.lev_crisis,
        }
        base_lev = float(lev_map.get(self.current_regime, 0.7))

        diagnostics = {
            "vix": vix_value,
            "vix_bucket": vix_reg,
            "regime": self.current_regime,
            "days_in_regime": float(self.days_in_regime),
            "trend_score": trend["trend_score"],
            "is_bear": trend["is_bear"],
            "spy_rv": rv,
        }
        return self.current_regime, base_lev, diagnostics


# -----------------------------------------------------------------------------
# Feature Engineering (daily, no lookahead by construction)
# -----------------------------------------------------------------------------
class FeatureEngine:
    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg

    @staticmethod
    def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
        delta = close.diff()
        up = delta.clip(lower=0.0)
        down = (-delta).clip(lower=0.0)
        roll_up = up.ewm(alpha=1.0/window, adjust=False).mean()
        roll_down = down.ewm(alpha=1.0/window, adjust=False).mean()
        rs = roll_up / (roll_down + 1e-12)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    def compute_features(
        self,
        close: pd.DataFrame,
        volume: pd.DataFrame,
        benchmark: pd.Series,
    ) -> Dict[str, pd.DataFrame]:
        """
        Compute panel features for all symbols across all dates.
        All features are computed from historical values only (rolling/shift).
        """
        cfg = self.cfg
        close = close.astype(float)
        volume = volume.astype(float)

        ret1 = close.pct_change()
        ret5 = close.pct_change(cfg.reversal_window)
        ret21 = close.pct_change(21)
        ret63 = close.pct_change(63)
        ret252 = close.pct_change(252)

        mom_12_1 = (1.0 + ret252) / (1.0 + ret21) - 1.0  # 12-1
        reversal_5d = -ret5

        vol20 = ret1.rolling(cfg.vol_window_short).std(ddof=1) * np.sqrt(252.0)
        vol60 = ret1.rolling(cfg.vol_window_long).std(ddof=1) * np.sqrt(252.0)
        vol_ratio = (vol20 / (vol60.replace(0.0, np.nan))).replace([np.inf, -np.inf], np.nan)

        # ADV$ proxy: close * volume rolling mean
        dollar_vol = (close * volume).rolling(cfg.adv_window).mean()

        # Beta & Corr vs benchmark (rolling)
        bench_ret = benchmark.pct_change()
        beta = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        corr = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        for sym in close.columns:
            sret = ret1[sym]
            df = pd.concat([sret, bench_ret], axis=1).dropna()
            if df.empty:
                continue
            s = df.iloc[:, 0]
            b = df.iloc[:, 1]
            cov = s.rolling(cfg.beta_window).cov(b)
            var = b.rolling(cfg.beta_window).var()
            beta[sym] = cov / (var + 1e-12)
            corr[sym] = s.rolling(cfg.corr_window).corr(b)

        # RSI
        rsi = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        for sym in close.columns:
            rsi[sym] = self._rsi(close[sym], cfg.rsi_window)

        # Drawdown 60d
        roll_max = close.rolling(cfg.vol_window_long).max()
        dd60 = (close / (roll_max + 1e-12)) - 1.0

        feats = {
            "mom_12_1": mom_12_1,
            "mom_63": ret63,
            "mom_21": ret21,
            "reversal_5d": reversal_5d,
            "vol20": vol20,
            "vol60": vol60,
            "vol_ratio": vol_ratio,
            "beta60": beta,
            "corr60": corr,
            "rsi14": rsi,
            "dd60": dd60,
            "adv_dollar20": dollar_vol,
        }
        return feats

    def cross_sectional_standardize(
        self,
        feat_panel: Dict[str, pd.DataFrame],
        skip_cols: Optional[Iterable[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Standardize each feature cross-sectionally per date (winsorize + z + rank).
        Returns both z and rank versions for scoring flexibility.
        """
        cfg = self.cfg
        skip_cols = set(skip_cols or [])
        out: Dict[str, pd.DataFrame] = {}

        for name, df in feat_panel.items():
            if name in skip_cols:
                out[name] = df.copy()
                continue

            z_df = pd.DataFrame(index=df.index, columns=df.columns, dtype=float)
            rk_df = pd.DataFrame(index=df.index, columns=df.columns, dtype=float)

            for dt in df.index:
                xs = df.loc[dt]
                xs = winsorize_series(xs, cfg.winsorize_pct)
                z_df.loc[dt] = zscore_xs(xs)
                rk_df.loc[dt] = rank_xs_to_unit(xs)

            out[f"{name}_z"] = z_df
            out[f"{name}_rk"] = rk_df

        return out


# -----------------------------------------------------------------------------
# ICIR-based Factor Weights (Walk-forward, shrinkage)
# -----------------------------------------------------------------------------
class ICIRWeighter:
    """
    Monthly IC/ICIR weight learner with:
    - Exponential decay
    - Minimum abs IC mean threshold
    - Shrinkage toward equal weight
    """

    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg
        self.ic_history: Dict[str, List[float]] = {}
        self.dates: List[pd.Timestamp] = []

    def update(self, dt: pd.Timestamp, factor_xs: Dict[str, pd.Series], fwd_returns: pd.Series) -> None:
        """
        factor_xs: {factor_name: Series(index=symbol)}
        fwd_returns: Series(index=symbol) forward return over ic_lookahead_days, computed without leakage
        """
        self.dates.append(dt)
        for fname, xs in factor_xs.items():
            common = xs.index.intersection(fwd_returns.index)
            if len(common) < self.cfg.ic_min_obs:
                continue
            ic = xs.loc[common].corr(fwd_returns.loc[common], method="spearman")
            if not np.isfinite(ic):
                ic = 0.0
            self.ic_history.setdefault(fname, []).append(float(ic))

    def _exp_decay_weights(self, n: int, halflife_months: int) -> np.ndarray:
        if n <= 0:
            return np.array([])
        lam = math.log(2.0) / max(1.0, float(halflife_months))
        w = np.exp(-lam * np.arange(n)[::-1])
        w = w / (w.sum() + 1e-12)
        return w

    def compute_weights(self, candidate_factors: List[str]) -> Dict[str, float]:
        cfg = self.cfg
        icir: Dict[str, float] = {}

        for f in candidate_factors:
            ics = self.ic_history.get(f, [])
            if len(ics) < 6:
                icir[f] = 0.0
                continue
            arr = np.array(ics, dtype=float)
            w = self._exp_decay_weights(len(arr), cfg.ic_decay_halflife_months)
            mu = float(np.sum(w * arr))
            var = float(np.sum(w * (arr - mu) ** 2))
            sd = float(np.sqrt(max(var, 1e-12)))
            score = mu / sd
            if abs(mu) < cfg.ic_min_abs_mean:
                score = 0.0
            icir[f] = max(0.0, score)

        total = sum(icir.values())
        if total <= 1e-12:
            # fallback: equal
            k = max(1, len(candidate_factors))
            return {f: 1.0 / k for f in candidate_factors}

        raw = {f: icir[f] / total for f in candidate_factors}
        # shrinkage toward equal
        k = max(1, len(candidate_factors))
        eq = 1.0 / k
        shr = {}
        for f in candidate_factors:
            shr[f] = (1.0 - cfg.ic_shrinkage) * raw[f] + cfg.ic_shrinkage * eq

        # normalize
        s = sum(shr.values())
        if s <= 1e-12:
            return {f: 1.0 / k for f in candidate_factors}
        return {f: shr[f] / s for f in candidate_factors}


# -----------------------------------------------------------------------------
# Portfolio Construction
# -----------------------------------------------------------------------------
class PortfolioConstructor:
    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg
        self.peak: float = 1.0
        self.halt_level: int = 0

    def apply_drawdown_control(self, w: pd.Series, nav: float) -> pd.Series:
        cfg = self.cfg
        if nav > self.peak:
            self.peak = nav
            self.halt_level = 0

        dd = nav / (self.peak + 1e-12) - 1.0
        if dd < cfg.dd_stop:
            self.halt_level = 2
        elif dd < cfg.dd_halt:
            self.halt_level = 1
        else:
            # recovery
            rec = nav / (self.peak * (1.0 + cfg.dd_halt)) - 1.0
            if rec > cfg.dd_recover:
                self.halt_level = max(0, self.halt_level - 1)

        if self.halt_level == 2:
            return w * cfg.dd_scale_stop
        if self.halt_level == 1:
            return w * cfg.dd_scale_halt
        return w

    def apply_buffer(self, new_w: pd.Series, old_w: pd.Series) -> pd.Series:
        cfg = self.cfg
        all_syms = sorted(set(new_w.index).union(set(old_w.index)))
        out = old_w.reindex(all_syms).fillna(0.0)
        nw = new_w.reindex(all_syms).fillna(0.0)

        for s in all_syms:
            if abs(nw[s] - out[s]) > cfg.position_buffer:
                out[s] = nw[s]
        return out

    def limit_turnover(self, new_w: pd.Series, old_w: pd.Series, max_turnover: float) -> pd.Series:
        all_syms = sorted(set(new_w.index).union(set(old_w.index)))
        nw = new_w.reindex(all_syms).fillna(0.0)
        ow = old_w.reindex(all_syms).fillna(0.0)
        t = float((nw - ow).abs().sum())
        if t <= max_turnover + 1e-12:
            return nw
        blend = max_turnover / (t + 1e-12)
        return ow * (1.0 - blend) + nw * blend

    def sector_cap(self, w: pd.Series) -> pd.Series:
        cfg = self.cfg
        if not cfg.sector_map:
            return w

        # cap each sector to max_sector_weight by proportional scaling inside sector
        out = w.copy()
        sector_weights: Dict[str, float] = {}
        for sym, wt in out.items():
            sec = cfg.sector_map.get(sym, "Other")
            sector_weights[sec] = sector_weights.get(sec, 0.0) + float(wt)

        for sec, sw in sector_weights.items():
            if sw > cfg.max_sector_weight:
                scale = cfg.max_sector_weight / (sw + 1e-12)
                for sym in out.index:
                    if cfg.sector_map.get(sym, "Other") == sec:
                        out.loc[sym] = out.loc[sym] * scale
        return out

    def correlation_penalty_equity_exposure(
        self,
        equity_weights: pd.Series,
        returns_window: pd.DataFrame,
    ) -> float:
        """
        Compute avg pairwise correlation of equity sleeve and return an exposure multiplier.
        If avg corr high -> reduce sleeve exposure.
        """
        cfg = self.cfg
        syms = [s for s in equity_weights.index if equity_weights.get(s, 0.0) > 1e-8]
        if len(syms) < 3:
            return 1.0
        sub = returns_window[syms].dropna()
        if sub.shape[0] < 20:
            return 1.0
        corr = sub.corr().values
        # avg off-diagonal
        n = corr.shape[0]
        avg = (corr.sum() - np.trace(corr)) / (n * (n - 1) + 1e-12)
        avg = float(np.clip(avg, -1.0, 1.0))

        if avg <= cfg.max_avg_pairwise_corr:
            return 1.0

        # linear penalty
        excess = avg - cfg.max_avg_pairwise_corr
        mult = 1.0 - cfg.corr_penalty_scale * (excess / max(1e-6, (1.0 - cfg.max_avg_pairwise_corr)))
        return float(np.clip(mult, 0.40, 1.0))

    def build_weights(
        self,
        dt: pd.Timestamp,
        regime: str,
        base_leverage: float,
        equity_candidates: pd.Index,
        risk_off_candidates: pd.Index,
        scores: pd.Series,
        vol20: pd.Series,
        adv: pd.Series,
        spy_returns_window: pd.Series,
        full_returns_window: pd.DataFrame,
        nav: float,
    ) -> Tuple[pd.Series, Dict[str, float]]:
        """
        Build target weights for dt (to be applied from dt+1).
        """
        cfg = self.cfg
        diag: Dict[str, float] = {}

        # Liquidity filter
        liquid = adv[adv >= cfg.min_adv_dollars].index
        eq_pool = equity_candidates.intersection(liquid)
        ro_pool = risk_off_candidates  # ETFs usually liquid; keep

        # Regime sleeve mix
        if regime in ("ULTRA_LOW", "LOW", "MODERATE"):
            equity_share = 0.90 if regime != "MODERATE" else 0.80
            risk_off_share = 0.10 if regime != "MODERATE" else 0.20
        elif regime == "HIGH":
            equity_share = 0.55
            risk_off_share = 0.45
        else:  # CRISIS
            equity_share = 0.20
            risk_off_share = 0.80

        diag["equity_share_pre"] = float(equity_share)
        diag["risk_off_share_pre"] = float(risk_off_share)

        # Select equities by score
        eq_scores = scores.reindex(eq_pool).dropna().sort_values(ascending=False)
        eq_selected = eq_scores.head(cfg.n_equities).index.tolist()

        # Select risk-off (if no risk-off instruments exist, shift to cash by leaving weights unused)
        ro_selected: List[str] = []
        if len(ro_pool) > 0 and risk_off_share > 1e-6:
            # For risk-off choose low volatility / positive momentum relative to others (simple)
            ro_sc = scores.reindex(ro_pool).dropna()
            if ro_sc.empty:
                ro_selected = ro_pool[: cfg.n_risk_off].tolist()
            else:
                ro_selected = ro_sc.sort_values(ascending=False).head(cfg.n_risk_off).index.tolist()

        # Base weights inside sleeves
        w = pd.Series(0.0, index=scores.index, dtype=float)

        # Equity sleeve: inverse-vol * score-softmax
        if eq_selected:
            eq_vol = vol20.reindex(eq_selected).replace([np.inf, -np.inf], np.nan).fillna(0.25)
            eq_sc = eq_scores.reindex(eq_selected).fillna(0.0)
            sc_vec = eq_sc.values.astype(float)
            sm = softmax(sc_vec, temperature=1.25 if regime in ("HIGH", "CRISIS") else 1.0)
            invv = 1.0 / (eq_vol.values + 0.10)
            invv = invv / (invv.sum() + 1e-12)
            raw = 0.55 * sm + 0.45 * invv
            raw = raw / (raw.sum() + 1e-12)
            for i, sym in enumerate(eq_selected):
                w.loc[sym] = equity_share * raw[i]

        # Risk-off sleeve: equal weight
        if ro_selected:
            ro_w = risk_off_share / max(1, len(ro_selected))
            for sym in ro_selected:
                w.loc[sym] = w.loc[sym] + ro_w

        # Apply base leverage (pre vol targeting)
        w = w * base_leverage

        # Vol targeting based on benchmark realized vol proxy
        # (robust & cheap; alternative: portfolio EWMA vol)
        if spy_returns_window.dropna().shape[0] >= 30:
            rv = float(spy_returns_window.dropna().std(ddof=1) * np.sqrt(252.0))
        else:
            rv = 0.16
        rv = float(np.clip(rv, cfg.vol_floor, cfg.vol_cap))
        vol_mult = cfg.target_vol_annual / (rv + 1e-12)
        lev = float(np.clip(base_leverage * vol_mult, cfg.leverage_floor, cfg.leverage_cap))

        # rescale to lev while keeping composition
        sabs = float(w.abs().sum())
        if sabs > 1e-12:
            w = w * (lev / sabs)

        # Correlation penalty on equity sleeve in HIGH/CRISIS
        if regime in ("HIGH", "CRISIS") and eq_selected:
            eq_w = w.reindex(eq_selected).fillna(0.0)
            corr_mult = self.correlation_penalty_equity_exposure(eq_w, full_returns_window)
            diag["corr_mult"] = float(corr_mult)
            # reduce equities, shift to risk-off/cash (cash = uninvested)
            for sym in eq_selected:
                w.loc[sym] = w.loc[sym] * corr_mult

        # Caps
        w = w.clip(lower=cfg.min_position, upper=cfg.max_position)
        w = self.sector_cap(w)

        # Drawdown control
        w = self.apply_drawdown_control(w, nav)

        diag["target_abs_exposure"] = float(w.abs().sum())
        diag["lev_base"] = float(base_leverage)
        diag["lev_after_vol_target"] = float(lev)
        diag["spy_rv_used"] = float(rv)
        diag["n_eq"] = float(len(eq_selected))
        diag["n_ro"] = float(len(ro_selected))
        return w, diag


# -----------------------------------------------------------------------------
# Strategy: regime-aware factor scoring (with learned weights)
# -----------------------------------------------------------------------------
class RegimeAwareScorer:
    """
    Build cross-sectional score per date for the whole tradable set.
    Uses:
    - Predefined factor set (z/rank panels)
    - Regime-specific candidate factors
    - Walk-forward learned ICIR weights (optional, recommended)
    """

    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg

        # Default factor menus (names refer to standardized keys from FeatureEngine)
        self.factor_menu = {
            "ULTRA_LOW": ["mom_12_1_z", "mom_63_z", "vol20_z"],  # vol20_z will be NEGatively used below
            "LOW":       ["mom_12_1_z", "mom_63_z", "vol20_z", "beta60_z"],
            "MODERATE":  ["mom_12_1_z", "mom_63_z", "vol20_z", "beta60_z", "dd60_z", "vol_ratio_z"],
            "HIGH":      ["reversal_5d_z", "vol20_z", "beta60_z", "dd60_z", "rsi14_z", "corr60_z"],
            "CRISIS":    ["reversal_5d_z", "vol20_z", "beta60_z", "dd60_z", "corr60_z"],
        }

        # Signs (some factors should be minimized)
        self.factor_sign = {
            "vol20_z": -1.0,
            "vol60_z": -1.0,
            "beta60_z": -1.0,
            "corr60_z": -1.0,
            "dd60_z":  1.0,   # dd is negative; higher (less negative) is better
            "rsi14_z": 0.0,   # handled specially (prefer mid-range in HIGH)
        }

    def _rsi_preference(self, rsi_z: pd.Series, raw_rsi: pd.Series, regime: str) -> pd.Series:
        """
        HIGH/CRISIS: prefer oversold rebound setups but avoid catching falling knives:
        - target around RSI ~ 30-40 -> create a hump-shaped score
        """
        if raw_rsi is None or raw_rsi.empty:
            return rsi_z.fillna(0.0)

        if regime in ("HIGH", "CRISIS"):
            r = raw_rsi.clip(0, 100).astype(float)
            # hump around 35
            score = -((r - 35.0) / 18.0) ** 2 + 1.0
            return zscore_xs(score).reindex(rsi_z.index).fillna(0.0)
        else:
            # in calm regimes, mild trend-following: prefer stronger RSI
            return rsi_z.fillna(0.0)

    def score_date(
        self,
        dt: pd.Timestamp,
        regime: str,
        standardized_panels: Dict[str, pd.DataFrame],
        raw_panels: Dict[str, pd.DataFrame],
        learned_weights: Optional[Dict[str, float]] = None,
    ) -> pd.Series:
        """
        Produce a score Series(index=symbol) for date dt.
        """
        factors = self.factor_menu.get(regime, [])
        if not factors:
            return pd.Series(0.0, index=standardized_panels[next(iter(standardized_panels))].columns)

        # If no learned weights provided, equal weights
        if learned_weights is None:
            learned_weights = {f: 1.0 / len(factors) for f in factors}
        else:
            # restrict to menu and re-normalize
            lw = {f: learned_weights.get(f, 0.0) for f in factors}
            s = sum(lw.values())
            if s <= 1e-12:
                learned_weights = {f: 1.0 / len(factors) for f in factors}
            else:
                learned_weights = {f: lw[f] / s for f in factors}

        # Build combined score
        any_panel = standardized_panels[factors[0]]
        syms = any_panel.columns
        sc = pd.Series(0.0, index=syms, dtype=float)

        # raw RSI for special handling
        raw_rsi = raw_panels.get("rsi14", pd.DataFrame()).loc[dt] if "rsi14" in raw_panels else None

        for f in factors:
            df = standardized_panels.get(f)
            if df is None or dt not in df.index:
                continue
            xs = df.loc[dt].copy()

            if f == "rsi14_z":
                xs = self._rsi_preference(xs, raw_rsi, regime)

            sign = self.factor_sign.get(f, 1.0)
            if sign == 0.0:
                sign = 1.0

            sc = sc + sign * float(learned_weights.get(f, 0.0)) * xs.fillna(0.0)

        sc = sc.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return sc


# -----------------------------------------------------------------------------
# Backtester (strict no-lookahead with signal lag)
# -----------------------------------------------------------------------------
class AresV10Backtester:
    VERSION = "10.0"

    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg
        self.data = SQLiteMarketData(cfg)

        self.regime_model = RegimeModel(cfg)
        self.fe = FeatureEngine(cfg)
        self.scorer = RegimeAwareScorer(cfg)
        self.pc = PortfolioConstructor(cfg)

        self.reset()

    def reset(self) -> None:
        self.nav: float = 1.0
        self.nav_series: List[float] = [1.0]
        self.positions: pd.Series = pd.Series(dtype=float)       # active positions applied on current day
        self.pending_positions: pd.Series = pd.Series(dtype=float)  # decided at end of day for next day
        self.last_rebal_dt: Optional[pd.Timestamp] = None

        self.trade_log: List[Dict[str, Any]] = []
        self.daily_log: List[Dict[str, Any]] = []
        self.regime_returns: Dict[str, List[float]] = {r: [] for r in RegimeModel.REGIMES}

    def _effective_cost_rate(self, regime: str) -> float:
        base = self.cfg.commission + self.cfg.slippage
        if regime == "HIGH":
            return base * self.cfg.high_vol_cost_mult
        if regime == "CRISIS":
            return base * self.cfg.crisis_cost_mult
        return base

    def load_all_data(self, start: str, end: str) -> Dict[str, Any]:
        cfg = self.cfg

        # Universe: include benchmark, vix, risk-off ETFs
        symbols = sorted(set(cfg.core_equity_universe + cfg.risk_off_universe + [cfg.benchmark_symbol, cfg.vix_symbol]))
        close, volume = self.data.load_prices(symbols, start, end)

        # Separate series
        spy = close[cfg.benchmark_symbol].dropna() if cfg.benchmark_symbol in close.columns else pd.Series(dtype=float)
        vix = close[cfg.vix_symbol].dropna() if cfg.vix_symbol in close.columns else pd.Series(dtype=float)

        # Tradable: intersection of columns
        equities = [s for s in cfg.core_equity_universe if s in close.columns]
        risk_off = [s for s in cfg.risk_off_universe if s in close.columns]

        payload = {
            "close": close,
            "volume": volume,
            "spy": spy,
            "vix": vix,
            "equities": equities,
            "risk_off": risk_off,
        }
        return payload

    def run_backtest(
        self,
        start: str,
        end: str,
        learned_weights_by_regime: Optional[Dict[str, Dict[str, float]]] = None,
        save_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        learned_weights_by_regime: regime -> {factor_name: weight}
        factor_name must match standardized keys e.g. "mom_12_1_z"
        """
        self.reset()
        cfg = self.cfg

        md = self.load_all_data(start, end)
        close: pd.DataFrame = md["close"]
        volume: pd.DataFrame = md["volume"]
        spy: pd.Series = md["spy"]
        vix: pd.Series = md["vix"]
        equities: List[str] = md["equities"]
        risk_off: List[str] = md["risk_off"]

        if close.empty or spy.empty:
            raise RuntimeError("Missing price data for backtest (close/SPY empty).")

        # Align index (trading days)
        dates = close.index.intersection(spy.index)
        if not vix.empty:
            dates = dates.intersection(vix.index)
        dates = dates.sort_values()

        close = close.reindex(dates).ffill()
        volume = volume.reindex(dates).fillna(0.0)
        spy = spy.reindex(dates).ffill()
        vix = vix.reindex(dates).ffill() if not vix.empty else pd.Series(20.0, index=dates)

        # Feature compute
        raw_feats = self.fe.compute_features(close, volume, spy)
        std_feats = self.fe.cross_sectional_standardize(raw_feats)

        # Returns for simulation
        ret1 = close.pct_change().fillna(0.0)

        # Precompute windows for speed
        adv = raw_feats["adv_dollar20"].fillna(0.0)
        vol20 = raw_feats["vol20"].replace([np.inf, -np.inf], np.nan).fillna(0.25)

        # Initialize positions on first tradable day after warmup
        warm = cfg.warmup_days
        if len(dates) <= warm + 5:
            raise RuntimeError("Not enough history for warmup.")

        # Active positions start at zero; we decide pending at first rebalance
        all_syms = close.columns
        self.positions = pd.Series(0.0, index=all_syms, dtype=float)
        self.pending_positions = self.positions.copy()

        for i in range(warm + 1, len(dates)):
            dt = dates[i]

            # 1) Apply pending positions decided at dt-1 close (strict lag)
            if cfg.signal_lag_days == 1:
                self.positions = self.pending_positions.copy()

            # 2) Realize PnL for dt (close-to-close from dt-1 to dt)
            prev_dt = dates[i - 1]
            day_ret_vec = ret1.loc[dt].reindex(all_syms).fillna(0.0)
            gross = float((self.positions.reindex(all_syms).fillna(0.0) * day_ret_vec).sum())

            # Daily log placeholder (regime is determined by info up to prev_dt for execution realism)
            # We use VIX/spy history up to prev_dt (no lookahead inside day).
            vix_val_exec = float(vix.loc[prev_dt]) if prev_dt in vix.index else float(vix.iloc[:i].iloc[-1])
            spy_hist_exec = spy.loc[:prev_dt]
            regime_exec, base_lev_exec, diag_exec = self.regime_model.detect(vix_val_exec, spy_hist_exec)

            # 3) Costs: only on rebalance days (when pending_positions changes at prev close)
            cost = 0.0
            turnover = 0.0
            # We record turnover when we *set* pending_positions (end-of-day). Here, we apply cost from last decision.
            # For simplicity and strictness, we charge cost on the day positions become active:
            # turnover between current active positions and yesterday's active positions.
            prev_pos = self.trade_log[-1]["active_positions"] if self.trade_log else {s: 0.0 for s in all_syms}
            prev_pos_s = pd.Series(prev_pos).reindex(all_syms).fillna(0.0)
            turnover = float((self.positions - prev_pos_s).abs().sum())
            cost_rate = self._effective_cost_rate(regime_exec)
            cost = turnover * cost_rate
            net = gross - cost

            self.nav *= (1.0 + net)
            self.nav_series.append(self.nav)

            self.regime_returns.setdefault(regime_exec, []).append(net)

            self.daily_log.append({
                "date": str(dt.date()),
                "regime_exec": regime_exec,
                "nav": self.nav,
                "gross_ret": gross,
                "cost": cost,
                "turnover": turnover,
                **diag_exec,
            })

            # 4) End-of-day rebalance decision (positions for dt+1)
            should_rebal = False
            if self.last_rebal_dt is None:
                should_rebal = True
            else:
                # trading-day based: approximate by counting days
                if (dt - self.last_rebal_dt).days >= cfg.rebalance_days:
                    should_rebal = True

            if should_rebal:
                # Determine regime for decision using info up to dt (close) but apply at dt+1
                vix_val = float(vix.loc[dt]) if dt in vix.index else vix_val_exec
                spy_hist = spy.loc[:dt]
                regime, base_lev, diag = self.regime_model.detect(vix_val, spy_hist)

                # learned weights per regime (optional)
                lw = learned_weights_by_regime.get(regime, None) if learned_weights_by_regime else None

                # Scores as of dt (cross-section)
                score = self.scorer.score_date(
                    dt=dt,
                    regime=regime,
                    standardized_panels=std_feats,
                    raw_panels=raw_feats,
                    learned_weights=lw,
                )

                # Candidates indexes
                eq_idx = pd.Index(equities)
                ro_idx = pd.Index(risk_off)

                # windows for correlation penalty
                window_returns = ret1.loc[:dt].iloc[-self.cfg.corr_window:].reindex(columns=all_syms).fillna(0.0)
                spy_window = spy.pct_change().loc[:dt].iloc[-self.cfg.spy_rv_window * 3:].fillna(0.0)

                # Build pending target weights
                adv_dt = adv.loc[dt] if dt in adv.index else adv.iloc[:i].iloc[-1]
                vol20_dt = vol20.loc[dt] if dt in vol20.index else vol20.iloc[:i].iloc[-1]

                target_w, wdiag = self.pc.build_weights(
                    dt=dt,
                    regime=regime,
                    base_leverage=base_lev,
                    equity_candidates=eq_idx,
                    risk_off_candidates=ro_idx,
                    scores=score,
                    vol20=vol20_dt,
                    adv=adv_dt,
                    spy_returns_window=spy_window,
                    full_returns_window=window_returns,
                    nav=self.nav,
                )

                # Apply buffer & turnover cap (regime-aware)
                max_to = cfg.max_turnover_per_rebal
                if regime == "HIGH":
                    max_to *= cfg.max_turnover_high_mult
                if regime == "CRISIS":
                    max_to *= cfg.max_turnover_crisis_mult

                buffered = self.pc.apply_buffer(target_w, self.pending_positions)
                limited = self.pc.limit_turnover(buffered, self.pending_positions, max_to)

                self.pending_positions = limited.reindex(all_syms).fillna(0.0)
                self.last_rebal_dt = dt

                self.trade_log.append({
                    "date": str(dt.date()),
                    "regime_decision": regime,
                    "vix_decision": vix_val,
                    "base_leverage": base_lev,
                    "weights_diag": wdiag,
                    "target_positions": target_w.to_dict(),
                    "pending_positions": self.pending_positions.to_dict(),
                    "active_positions": self.positions.to_dict(),
                })
            else:
                # still log active positions snapshot for turnover computation next day
                self.trade_log.append({
                    "date": str(dt.date()),
                    "regime_decision": None,
                    "vix_decision": None,
                    "base_leverage": None,
                    "weights_diag": {},
                    "target_positions": {},
                    "pending_positions": self.pending_positions.to_dict(),
                    "active_positions": self.positions.to_dict(),
                })

        # Metrics
        nav_s = pd.Series(self.nav_series, index=dates[warm:])  # aligned length approx
        rets = nav_s.pct_change().dropna()

        cum = (1.0 + rets).cumprod()
        res = {
            "version": self.VERSION,
            "period": f"{start} -> {end}",
            "final_nav": float(self.nav),
            "total_return": float(self.nav - 1.0),
            "ann_return": float((self.nav ** (252.0 / max(1.0, len(rets))) - 1.0)) if len(rets) > 5 else 0.0,
            "sharpe": annualized_sharpe(rets),
            "sortino": annualized_sortino(rets),
            "mdd": max_drawdown(cum),
            "avg_daily_ret": float(rets.mean()) if len(rets) else 0.0,
            "vol_annual": float(rets.std(ddof=1) * np.sqrt(252.0)) if len(rets) else 0.0,
            "avg_turnover": float(pd.Series([d.get("turnover", 0.0) for d in self.daily_log]).mean()) if self.daily_log else 0.0,
            "total_cost": float(pd.Series([d.get("cost", 0.0) for d in self.daily_log]).sum()) if self.daily_log else 0.0,
            "regime_performance": {},
        }

        # Regime stats
        for reg, arr in self.regime_returns.items():
            if not arr:
                continue
            rs = pd.Series(arr)
            res["regime_performance"][reg] = {
                "days": int(len(rs)),
                "ann_return": float(rs.mean() * 252.0),
                "sharpe": annualized_sharpe(rs, rf_annual=0.05),
                "win_rate": float((rs > 0).mean()),
            }

        # Save artifacts
        if save_path:
            ensure_dir(os.path.dirname(save_path))
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved results: {save_path}")

            # also save logs
            out_dir = os.path.dirname(save_path)
            pd.DataFrame(self.daily_log).to_csv(os.path.join(out_dir, "daily_log.csv"), index=False)
            with open(os.path.join(out_dir, "trade_log.json"), "w", encoding="utf-8") as f:
                json.dump(self.trade_log, f, indent=2, ensure_ascii=False)

        return res


# -----------------------------------------------------------------------------
# Walk-forward Trainer (ICIR weights per regime)
# -----------------------------------------------------------------------------
class WalkForwardICIRTrainer:
    """
    Train regime-aware factor weights on IS, then apply on OOS.

    Training protocol:
    - Monthly sampling of dates (rebalance frequency)
    - For each training date t:
        * compute factor xs at t (no lookahead)
        * compute forward return over next ic_lookahead_days (using close[t+1]..close[t+k]) (no leakage in training)
        * update IC history
    - Compute ICIR weights for each regime separately
    """

    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg
        self.data = SQLiteMarketData(cfg)
        self.fe = FeatureEngine(cfg)
        self.scorer = RegimeAwareScorer(cfg)

    def train(
        self,
        start: str,
        end: str,
    ) -> Dict[str, Dict[str, float]]:
        cfg = self.cfg

        symbols = sorted(set(cfg.core_equity_universe + cfg.risk_off_universe + [cfg.benchmark_symbol, cfg.vix_symbol]))
        close, volume = self.data.load_prices(symbols, start, end)
        if close.empty or cfg.benchmark_symbol not in close.columns:
            raise RuntimeError("Training data missing close/SPY.")

        spy = close[cfg.benchmark_symbol].ffill()
        vix = close[cfg.vix_symbol].ffill() if cfg.vix_symbol in close.columns else pd.Series(20.0, index=close.index)

        dates = close.index.intersection(spy.index).intersection(vix.index).sort_values()
        close = close.reindex(dates).ffill()
        volume = volume.reindex(dates).fillna(0.0)
        spy = spy.reindex(dates).ffill()
        vix = vix.reindex(dates).ffill()

        # Features
        raw_feats = self.fe.compute_features(close, volume, spy)
        std_feats = self.fe.cross_sectional_standardize(raw_feats)
        ret1 = close.pct_change().fillna(0.0)

        # Use rebalance grid: every rebalance_days trading days after warmup
        warm = cfg.warmup_days
        rebal_dates = []
        for i in range(warm, len(dates) - cfg.ic_lookahead_days - 2, cfg.rebalance_days):
            rebal_dates.append(dates[i])

        # Weighter per regime
        weighters: Dict[str, ICIRWeighter] = {r: ICIRWeighter(cfg) for r in RegimeModel.REGIMES}
        regime_model = RegimeModel(cfg)  # separate state

        # factor candidates per regime are from scorer.menu
        factor_menu = self.scorer.factor_menu

        for dt in rebal_dates:
            # regime at dt using information up to dt (training is okay; no forward data used for regime)
            reg, _, _ = regime_model.detect(float(vix.loc[dt]), spy.loc[:dt])

            # forward return over next ic_lookahead_days, starting from dt+1 close to dt+k close
            idx = dates.get_indexer([dt])[0]
            fwd_end = idx + cfg.ic_lookahead_days
            if fwd_end >= len(dates) - 1:
                continue
            dt1 = dates[idx + 1]
            dtk = dates[fwd_end]

            px0 = close.loc[dt1]
            px1 = close.loc[dtk]
            fwd_ret = (px1 / (px0 + 1e-12) - 1.0).replace([np.inf, -np.inf], np.nan)

            # factor xs: we use standardized factor panels at dt
            factor_list = factor_menu.get(reg, [])
            factor_xs: Dict[str, pd.Series] = {}
            for f in factor_list:
                df = std_feats.get(f)
                if df is None or dt not in df.index:
                    continue
                factor_xs[f] = df.loc[dt].astype(float)

            weighters[reg].update(dt, factor_xs, fwd_ret)

        # compute weights per regime
        learned: Dict[str, Dict[str, float]] = {}
        for reg in RegimeModel.REGIMES:
            candidate = factor_menu.get(reg, [])
            learned[reg] = weighters[reg].compute_weights(candidate)

        return learned


# -----------------------------------------------------------------------------
# Walk-forward Runner (OOS 2021-2024 mandatory)
# -----------------------------------------------------------------------------
class AresV10WalkForwardRunner:
    def __init__(self, cfg: AresV10Config):
        self.cfg = cfg

    def run_oos_2021_2024(
        self,
        is_start: str = "2016-01-01",
        is_end: str = "2020-12-31",
        oos_start: str = "2021-01-01",
        oos_end: str = "2024-12-31",
        retrain_yearly: bool = True,
    ) -> Dict[str, Any]:
        """
        Yearly walk-forward:
        - Train on (is_start .. prev_year_end)
        - Test on next year
        Combine 2021-2024.
        """
        cfg = self.cfg
        ensure_dir(cfg.out_dir)

        results: Dict[str, Any] = {
            "engine_version": "10.0",
            "oos_period": f"{oos_start} -> {oos_end}",
            "folds": [],
            "combined": {},
        }

        if not retrain_yearly:
            # single train then full OOS
            trainer = WalkForwardICIRTrainer(cfg)
            learned = trainer.train(is_start, is_end)
            bt = AresV10Backtester(cfg)
            out_path = os.path.join(cfg.out_dir, "oos_2021_2024_single.json")
            r = bt.run_backtest(oos_start, oos_end, learned_weights_by_regime=learned, save_path=out_path)
            results["folds"].append({"train": [is_start, is_end], "test": [oos_start, oos_end], "metrics": r})
            results["combined"] = r
            with open(os.path.join(cfg.out_dir, "walkforward_summary.json"), "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            return results

        # yearly folds
        years = [2021, 2022, 2023, 2024]
        fold_navs: List[pd.Series] = []
        fold_daily: List[pd.DataFrame] = []
        combined_metrics: List[Dict[str, Any]] = []

        for y in years:
            train_end = f"{y-1}-12-31"
            test_start = f"{y}-01-01"
            test_end = f"{y}-12-31"
            if pd.to_datetime(test_end) > pd.to_datetime(oos_end):
                test_end = oos_end

            trainer = WalkForwardICIRTrainer(cfg)
            learned = trainer.train(is_start, train_end)

            bt = AresV10Backtester(cfg)
            out_path = os.path.join(cfg.out_dir, f"oos_fold_{y}.json")
            r = bt.run_backtest(test_start, test_end, learned_weights_by_regime=learned, save_path=out_path)

            results["folds"].append({
                "year": y,
                "train": [is_start, train_end],
                "test": [test_start, test_end],
                "learned_weights": learned,
                "metrics": r,
            })
            combined_metrics.append(r)

        # Combine fold metrics approximately (by concatenating daily logs is ideal; we at least summarize)
        # We re-run a final backtest using weights trained up to 2020 for full 2021-2024 as a reference too.
        ref_trainer = WalkForwardICIRTrainer(cfg)
        ref_learned = ref_trainer.train(is_start, "2020-12-31")
        ref_bt = AresV10Backtester(cfg)
        ref_path = os.path.join(cfg.out_dir, "oos_2021_2024_reference.json")
        ref_res = ref_bt.run_backtest(oos_start, oos_end, learned_weights_by_regime=ref_learned, save_path=ref_path)
        results["combined"] = ref_res

        with open(os.path.join(cfg.out_dir, "walkforward_summary.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        return results


# -----------------------------------------------------------------------------
# CLI / Main
# -----------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ARES v10.0 Production Walk-Forward Backtest (No Lookahead)")
    parser.add_argument("--db-path", type=str, default=None, help="SQLite DB path")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory")
    parser.add_argument("--mode", type=str, default="walkforward", choices=["walkforward", "single"], help="Run mode")
    parser.add_argument("--start", type=str, default="2020-01-01")
    parser.add_argument("--end", type=str, default="2024-12-31")
    parser.add_argument("--oos-start", type=str, default="2021-01-01")
    parser.add_argument("--oos-end", type=str, default="2024-12-31")
    parser.add_argument("--is-start", type=str, default="2016-01-01")
    parser.add_argument("--is-end", type=str, default="2020-12-31")
    parser.add_argument("--retrain-yearly", action="store_true", help="Yearly walk-forward retraining")
    args = parser.parse_args(argv)

    cfg = AresV10Config()
    if args.db_path:
        cfg.db_path = args.db_path
    if args.out_dir:
        cfg.out_dir = args.out_dir
    ensure_dir(cfg.out_dir)

    logger.info("============================================================")
    logger.info("ARES v10.0 - Production Walk-Forward (No Lookahead)")
    logger.info("============================================================")
    logger.info(f"DB: {cfg.db_path}")
    logger.info(f"OUT: {cfg.out_dir}")
    logger.info(f"Mode: {args.mode}")

    if args.mode == "single":
        bt = AresV10Backtester(cfg)
        out_path = os.path.join(cfg.out_dir, "single_backtest.json")
        res = bt.run_backtest(args.start, args.end, learned_weights_by_regime=None, save_path=out_path)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    runner = AresV10WalkForwardRunner(cfg)
    wf = runner.run_oos_2021_2024(
        is_start=args.is_start,
        is_end=args.is_end,
        oos_start=args.oos_start,
        oos_end=args.oos_end,
        retrain_yearly=args.retrain_yearly,
    )
    print(json.dumps(wf["combined"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
