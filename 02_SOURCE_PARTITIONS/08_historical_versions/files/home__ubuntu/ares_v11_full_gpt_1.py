#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
ARES ULTIMATE v10 (Pareto-H Integrated) - Production Research/Backtest Engine
================================================================================

GOALS
- Keep v10 Final baseline spirit (Numba factor calc + regime + top-k) BUT:
  1) Strict no-lookahead: signals at t close -> positions applied from t+1 (1-day lag)
  2) Remove bfill() and any hidden lookahead in data handling
  3) Add:
     - ICIR dynamic factor weighting (walk-forward train)
     - HRP optimizer (small-N stable)
     - CVaR tail risk control
     - Volatility targeting (15% annual)
     - Enhanced regime detector (VIX slope + credit spread)
     - Rate regime detector (TLT-based)
     - Sector rotation (ETF sleeve + caps)
  4) Transaction costs: 50 bps per traded notional
  5) Detailed logs: year-by-year, regime-by-regime, turnover/costs, exposures

NOTES
- This file is intentionally long and explicit (production-style readability).
- It is split into 3 parts due to chat message size constraints.

Part 1/3:
- Imports / config
- SQLite loader (schema-robust)
- Numba factor kernels (momentum/vol/rank)
- Regime detectors (Enhanced + Rate)
- ICIR learner (walk-forward safe)

Part 2/3:
- HRP optimizer
- Risk manager (CVaR + vol targeting + dd + corr)
- Sector rotation module
- Portfolio builder

Part 3/3:
- Backtest engine (strict lag)
- Walk-forward runner (train ICIR on IS, apply OOS)
- Reporting/logging
- main()

================================================================================
"""

from __future__ import annotations

import os
import sys
import math
import json
import time
import sqlite3
import logging
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd

from numba import njit, prange

warnings.filterwarnings("ignore")

# Optional SciPy for HRP (Part 2)
try:
    from scipy.cluster.hierarchy import linkage, leaves_list
    from scipy.spatial.distance import squareform
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False


# =============================================================================
# Logging
# =============================================================================

def setup_logger(name: str = "ARES_ULTIMATE_V10", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    h = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)
    logger.propagate = False
    return logger


LOGGER = setup_logger()


# =============================================================================
# Config
# =============================================================================

@dataclass
class AresUltimateConfig:
    # -----------------------------
    # DB
    # -----------------------------
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    price_table: str = "daily_ohlcv"
    vix_table: str = "vix"
    macro_table: str = "macro_indicators"

    # -----------------------------
    # Universe (baseline v10 Final preserved)
    # -----------------------------
    universe: List[str] = field(default_factory=lambda: [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
        "BRK.B", "UNH", "JNJ", "JPM", "V", "PG", "XOM", "HD", "CVX",
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
        "SPY", "QQQ", "IWM", "DIA", "TLT", "IEF", "SHY", "GLD"
    ])

    benchmark: str = "SPY"
    tlt_symbol: str = "TLT"

    # Sector ETFs subset (for rotation sleeve)
    sector_etfs: List[str] = field(default_factory=lambda: [
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB"
    ])

    # Defensive / alt assets
    defensive_assets: List[str] = field(default_factory=lambda: [
        "SHY", "IEF", "TLT", "GLD"
    ])

    # -----------------------------
    # Execution / costs
    # -----------------------------
    start_date: str = "2016-01-01"
    end_date: str = "2024-12-31"

    warmup_days: int = 260

    rebalance_days: int = 10
    top_k: int = 15

    # IMPORTANT: strict lag
    signal_lag_days: int = 1

    # Costs: 50 bps per traded notional
    tc_per_traded_notional: float = 0.005

    # -----------------------------
    # Factors (baseline + enhancements)
    # -----------------------------
    mom_lookbacks: Tuple[int, int, int] = (252, 126, 63)
    mom_skip: int = 21

    # Reversal uses 5d return (NOT skip=0 momentum)
    rev_lookback: int = 5

    vol_lookback: int = 60

    # Rank normalize per day
    rank_epsilon: float = 1e-12

    # -----------------------------
    # Regime thresholds (baseline-like)
    # -----------------------------
    vix_bull: float = 15.0
    vix_normal: float = 18.0
    vix_caution: float = 25.0
    vix_crisis: float = 28.0

    # VIX slope
    vix_slope_days: int = 5
    vix_spike_pct: float = 0.30

    # Credit spread (optional macro series name)
    credit_spread_name: str = "credit_spread"   # must exist in macro pivot, else ignored
    credit_z_window: int = 252
    credit_high_z: float = 1.0

    # Trend
    ma_fast: int = 50
    ma_slow: int = 200

    # -----------------------------
    # Rate regime (TLT)
    # -----------------------------
    tlt_mom_days: int = 63
    tlt_ma_fast: int = 20
    tlt_ma_slow: int = 100

    # -----------------------------
    # Leverage caps by composite regime
    # -----------------------------
    lev_bull: float = 1.80
    lev_normal: float = 1.30
    lev_caution: float = 0.80
    lev_tightening: float = 0.50
    lev_crisis: float = 0.05

    # -----------------------------
    # Vol targeting (annual 15%)
    # -----------------------------
    target_vol_annual: float = 0.15
    vol_window_days: int = 60
    vol_mult_floor: float = 0.35
    vol_mult_cap: float = 1.60

    # -----------------------------
    # CVaR control
    # -----------------------------
    cvar_window: int = 60
    cvar_alpha: float = 0.95
    cvar_limit_daily: float = 0.018
    cvar_cut_floor: float = 0.20

    # -----------------------------
    # Drawdown / stability control (kept from baseline spirit)
    # -----------------------------
    dd_threshold: float = -0.12
    dd_recover_step: float = 0.05

    consec_loss_trigger: int = 3
    consec_loss_min_scale: float = 0.45
    consec_loss_recover: float = 0.05

    # -----------------------------
    # ICIR dynamic weights
    # -----------------------------
    ic_lookahead_days: int = 21
    icir_lookback: int = 126
    icir_min_obs: int = 20
    icir_shrinkage: float = 0.35

    # -----------------------------
    # Output
    # -----------------------------
    out_dir: str = "/home/ubuntu/ares_results/ares_ultimate_v10"
    log_daily_csv: str = "daily_log.csv"
    log_trades_json: str = "trade_log.json"
    save_equity_csv: str = "equity_curve.csv"


# =============================================================================
# Data Layer (SQLite, schema-robust)
# =============================================================================

class SQLiteLoader:
    def __init__(self, cfg: AresUltimateConfig):
        self.cfg = cfg
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        if self.conn is None:
            self.conn = sqlite3.connect(self.cfg.db_path)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def table_columns(self, table: str) -> List[str]:
        self.connect()
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        rows = cur.fetchall()
        return [r[1] for r in rows]

    def _pick_price_column(self) -> str:
        cols = set(self.table_columns(self.cfg.price_table))
        # common variants
        for c in ("adjusted_close", "adj_close", "adjClose", "close"):
            if c in cols:
                return c
        return "close"

    def load_prices(
        self,
        symbols: List[str],
        start: str,
        end: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns:
            close_wide: index=date, columns=symbol
            vol_wide:   index=date, columns=symbol (shares volume, if available else 0)
        """
        if not symbols:
            return pd.DataFrame(), pd.DataFrame()

        self.connect()

        cols = set(self.table_columns(self.cfg.price_table))
        price_col = self._pick_price_column()
        has_volume = "volume" in cols

        ph = ",".join(["?"] * len(symbols))
        q = f"""
            SELECT date, symbol,
                   {price_col} AS px,
                   {("volume" if has_volume else "NULL")} AS volume
            FROM {self.cfg.price_table}
            WHERE symbol IN ({ph})
              AND date BETWEEN ? AND ?
            ORDER BY date, symbol
        """
        df = pd.read_sql_query(q, self.conn, params=[*symbols, start, end])
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date", "symbol"], keep="last")

        close_wide = df.pivot(index="date", columns="symbol", values="px").sort_index()
        vol_wide = df.pivot(index="date", columns="symbol", values="volume").sort_index()

        # STRICT: NO bfill (lookahead). Only ffill.
        close_wide = close_wide.ffill()
        vol_wide = vol_wide.fillna(0.0)

        return close_wide, vol_wide

    def load_vix(
        self,
        start: str,
        end: str,
    ) -> pd.Series:
        self.connect()
        cols = set(self.table_columns(self.cfg.vix_table))
        close_col = "close" if "close" in cols else ("vix" if "vix" in cols else "close")

        q = f"""
            SELECT date, {close_col} AS vix
            FROM {self.cfg.vix_table}
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """
        df = pd.read_sql_query(q, self.conn, params=[start, end])
        if df.empty:
            return pd.Series(dtype=float)

        df["date"] = pd.to_datetime(df["date"])
        s = df.drop_duplicates(subset=["date"], keep="last").set_index("date")["vix"].astype(float)
        return s.ffill()

    def load_macro_pivot(
        self,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """
        Expected schema:
        macro_indicators(indicator_id, date, value, indicator_name)
        If not present, return empty DF.
        """
        try:
            self.connect()
            cols = set(self.table_columns(self.cfg.macro_table))
            needed = {"date", "value", "indicator_name"}
            if not needed.issubset(cols):
                return pd.DataFrame()

            q = f"""
                SELECT date, value, indicator_name
                FROM {self.cfg.macro_table}
                WHERE date BETWEEN ? AND ?
                ORDER BY date
            """
            df = pd.read_sql_query(q, self.conn, params=[start, end])
            if df.empty:
                return pd.DataFrame()

            df["date"] = pd.to_datetime(df["date"])
            piv = df.pivot_table(index="date", columns="indicator_name", values="value", aggfunc="last").sort_index()
            return piv.ffill()
        except Exception:
            return pd.DataFrame()


# =============================================================================
# Numba Kernels (Factors + Rank)
# =============================================================================

@njit(parallel=True, cache=True)
def nb_pct_change(prices: np.ndarray) -> np.ndarray:
    n, m = prices.shape
    out = np.zeros((n, m), dtype=np.float64)
    for i in prange(1, n):
        for j in range(m):
            p0 = prices[i - 1, j]
            p1 = prices[i, j]
            if p0 > 0.0 and np.isfinite(p0) and np.isfinite(p1):
                out[i, j] = (p1 / p0) - 1.0
            else:
                out[i, j] = 0.0
    return out


@njit(parallel=True, cache=True)
def nb_momentum(prices: np.ndarray, lookback: int, skip: int) -> np.ndarray:
    n, m = prices.shape
    out = np.full((n, m), np.nan, dtype=np.float64)
    for i in prange(lookback + skip, n):
        for j in range(m):
            p_now = prices[i - skip, j]
            p_then = prices[i - lookback - skip, j]
            if p_now > 0.0 and p_then > 0.0 and np.isfinite(p_now) and np.isfinite(p_then):
                out[i, j] = (p_now / p_then) - 1.0
    return out


@njit(parallel=True, cache=True)
def nb_rolling_vol(returns: np.ndarray, lookback: int) -> np.ndarray:
    n, m = returns.shape
    out = np.full((n, m), np.nan, dtype=np.float64)
    for i in prange(lookback, n):
        for j in range(m):
            s = 0.0
            s2 = 0.0
            cnt = 0
            for k in range(i - lookback, i):
                r = returns[k, j]
                if np.isfinite(r):
                    s += r
                    s2 += r * r
                    cnt += 1
            if cnt >= max(10, lookback // 2):
                mean = s / cnt
                var = (s2 / cnt) - mean * mean
                if var < 0.0:
                    var = 0.0
                out[i, j] = math.sqrt(var) * math.sqrt(252.0)
    return out


@njit(parallel=True, cache=True)
def nb_rank01(x: np.ndarray) -> np.ndarray:
    """
    Cross-sectional rank normalize per row to [0,1].
    NaNs remain NaN.
    """
    n, m = x.shape
    out = np.full((n, m), np.nan, dtype=np.float64)
    for i in prange(n):
        # collect valid
        tmp = np.empty(m, dtype=np.float64)
        idx = np.empty(m, dtype=np.int64)
        cnt = 0
        for j in range(m):
            v = x[i, j]
            if np.isfinite(v):
                tmp[cnt] = v
                idx[cnt] = j
                cnt += 1
        if cnt <= 1:
            continue
        # argsort
        order = np.argsort(tmp[:cnt])
        # assign ranks
        for r in range(cnt):
            j = idx[order[r]]
            out[i, j] = (r + 1.0) / cnt
    return out


@njit(cache=True)
def nb_clip(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


# =============================================================================
# Regime Detection (Enhanced + Rate)
# =============================================================================

class EnhancedRegimeDetector:
    """
    Composite regime:
    - Base VIX level buckets (BULL/NORMAL/CAUTION/TIGHTENING/CRISIS)
    - VIX slope spike detection
    - Trend filter (SPY > MA200, MA50>MA200)
    - Optional credit spread z-score
    """

    def __init__(self, cfg: AresUltimateConfig):
        self.cfg = cfg
        self.state: str = "NORMAL"
        self.crisis_days: int = 0
        self.recovery_days: int = 0

    def _trend_flags(self, spy: pd.Series) -> Tuple[bool, bool]:
        if spy is None or spy.dropna().shape[0] < self.cfg.ma_slow + 5:
            return True, True
        px = spy.iloc[-1]
        ma200 = spy.rolling(self.cfg.ma_slow).mean().iloc[-1]
        ma50 = spy.rolling(self.cfg.ma_fast).mean().iloc[-1]
        trend_up = bool(px > ma200) if np.isfinite(ma200) else True
        short_up = bool(px > ma50) if np.isfinite(ma50) else True
        return trend_up, short_up

    def _credit_z(self, credit: pd.Series) -> float:
        if credit is None or credit.dropna().shape[0] < 60:
            return 0.0
        s = credit.dropna()
        w = min(len(s), self.cfg.credit_z_window)
        x = s.iloc[-w:]
        mu = float(x.mean())
        sd = float(x.std(ddof=1))
        if not np.isfinite(sd) or sd < 1e-12:
            return 0.0
        return float((x.iloc[-1] - mu) / sd)

    def detect(
        self,
        dt: pd.Timestamp,
        vix: pd.Series,
        spy: pd.Series,
        credit_spread: Optional[pd.Series],
    ) -> Dict[str, Any]:
        """
        Uses information up to dt (inclusive).
        Return dict:
          - regime
          - base_leverage
          - diagnostics
        """
        c = self.cfg

        vix_hist = vix.loc[:dt].dropna() if vix is not None else pd.Series(dtype=float)
        vix_val = float(vix_hist.iloc[-1]) if len(vix_hist) else 20.0

        # VIX slope
        vix_slope = 0.0
        vix_spike = False
        if len(vix_hist) >= c.vix_slope_days + 1:
            v0 = float(vix_hist.iloc[-c.vix_slope_days - 1])
            v1 = float(vix_hist.iloc[-1])
            if v0 > 0:
                vix_slope = (v1 / v0) - 1.0
            vix_spike = vix_slope > c.vix_spike_pct

        # Trend
        spy_hist = spy.loc[:dt].dropna() if spy is not None else pd.Series(dtype=float)
        trend_up, short_up = self._trend_flags(spy_hist)

        # Credit z
        cz = 0.0
        if credit_spread is not None and not credit_spread.empty:
            cz = self._credit_z(credit_spread.loc[:dt])

        # Crisis/recovery counters (baseline-like but enhanced)
        crisis_signal = (vix_val >= c.vix_crisis) or vix_spike or (cz >= c.credit_high_z)
        recovery_signal = (vix_val < c.vix_normal) and trend_up and (cz < 0.5) and (not vix_spike)

        if crisis_signal:
            self.crisis_days += 1
            self.recovery_days = 0
        elif recovery_signal:
            self.recovery_days += 1
            self.crisis_days = 0
        else:
            self.crisis_days = max(0, self.crisis_days - 1)
            self.recovery_days = max(0, self.recovery_days - 1)

        # Determine regime
        if self.crisis_days >= 2:
            self.state = "CRISIS"
        elif self.recovery_days >= 5 and self.state == "CRISIS":
            self.state = "NORMAL"
        elif self.state != "CRISIS":
            if (cz >= c.credit_high_z) and (vix_val >= c.vix_normal):
                self.state = "TIGHTENING"
            elif vix_val >= c.vix_caution or (not trend_up):
                self.state = "CAUTION"
            elif (vix_val < c.vix_bull) and trend_up and short_up:
                self.state = "BULL"
            else:
                self.state = "NORMAL"

        # Base leverage mapping
        lev_map = {
            "BULL": c.lev_bull,
            "NORMAL": c.lev_normal,
            "CAUTION": c.lev_caution,
            "TIGHTENING": c.lev_tightening,
            "CRISIS": c.lev_crisis,
        }
        base_lev = float(lev_map.get(self.state, c.lev_normal))

        # Extra cut: if trend_down and not crisis, halve leverage
        if (not trend_up) and self.state not in ("CRISIS", "TIGHTENING"):
            base_lev *= 0.5

        return {
            "regime": self.state,
            "base_leverage": base_lev,
            "vix": vix_val,
            "vix_slope": float(vix_slope),
            "vix_spike": float(1.0 if vix_spike else 0.0),
            "trend_up": float(1.0 if trend_up else 0.0),
            "short_trend_up": float(1.0 if short_up else 0.0),
            "credit_z": float(cz),
            "crisis_days": float(self.crisis_days),
            "recovery_days": float(self.recovery_days),
        }


class RateRegimeDetector:
    """
    Detect rate hiking / duration stress regime using TLT.
    Output:
      - RATE_UP: TLT weak / below MA / negative momentum
      - RATE_DOWN: TLT strong
      - RATE_FLAT: neutral
    """

    def __init__(self, cfg: AresUltimateConfig):
        self.cfg = cfg
        self.state: str = "RATE_FLAT"

    def detect(self, dt: pd.Timestamp, tlt: pd.Series) -> Dict[str, Any]:
        c = self.cfg
        if tlt is None or tlt.dropna().shape[0] < max(c.tlt_ma_slow, c.tlt_mom_days) + 5:
            self.state = "RATE_FLAT"
            return {"rate_regime": self.state, "tlt_mom": 0.0, "tlt_ma_gap": 0.0}

        h = tlt.loc[:dt].dropna()
        if len(h) < max(c.tlt_ma_slow, c.tlt_mom_days) + 2:
            self.state = "RATE_FLAT"
            return {"rate_regime": self.state, "tlt_mom": 0.0, "tlt_ma_gap": 0.0}

        px = float(h.iloc[-1])
        ma_fast = float(h.rolling(c.tlt_ma_fast).mean().iloc[-1])
        ma_slow = float(h.rolling(c.tlt_ma_slow).mean().iloc[-1])

        mom = 0.0
        if len(h) >= c.tlt_mom_days + 1:
            p0 = float(h.iloc[-c.tlt_mom_days - 1])
            if p0 > 0:
                mom = (px / p0) - 1.0

        ma_gap = 0.0
        if np.isfinite(ma_slow) and ma_slow > 0:
            ma_gap = (px / ma_slow) - 1.0

        # Decision
        if (mom < -0.02) and (px < ma_slow) and (ma_fast < ma_slow):
            self.state = "RATE_UP"
        elif (mom > 0.02) and (px > ma_slow) and (ma_fast > ma_slow):
            self.state = "RATE_DOWN"
        else:
            self.state = "RATE_FLAT"

        return {
            "rate_regime": self.state,
            "tlt_mom": float(mom),
            "tlt_ma_gap": float(ma_gap),
        }


# =============================================================================
# ICIR Learner (walk-forward safe)
# =============================================================================

@njit(cache=True)
def nb_spearman_corr(x: np.ndarray, y: np.ndarray) -> float:
    """
    Spearman correlation via rank then Pearson.
    Assumes x,y length n, finite entries filtered outside.
    Ties not handled perfectly (acceptable for production robustness here).
    """
    n = x.shape[0]
    if n < 5:
        return np.nan

    # rank x
    ox = np.argsort(x)
    rx = np.empty(n, dtype=np.float64)
    for i in range(n):
        rx[ox[i]] = i + 1.0

    # rank y
    oy = np.argsort(y)
    ry = np.empty(n, dtype=np.float64)
    for i in range(n):
        ry[oy[i]] = i + 1.0

    mx = rx.mean()
    my = ry.mean()
    sx = 0.0
    sy = 0.0
    sxy = 0.0
    for i in range(n):
        dx = rx[i] - mx
        dy = ry[i] - my
        sxy += dx * dy
        sx += dx * dx
        sy += dy * dy
    if sx <= 1e-12 or sy <= 1e-12:
        return 0.0
    return sxy / math.sqrt(sx * sy)


class ICIRDynamicWeighter:
    """
    Maintains IC history per factor per regime.
    Produces positive-only ICIR weights with shrinkage to equal-weight.
    """

    def __init__(self, cfg: AresUltimateConfig):
        self.cfg = cfg
        self.ic_hist: Dict[str, Dict[str, List[Tuple[pd.Timestamp, float]]]] = {}
        # ic_hist[regime][factor] -> list of (date, ic)

    def update(
        self,
        dt: pd.Timestamp,
        regime: str,
        factor_xs: Dict[str, pd.Series],
        fwd_ret: pd.Series,
    ) -> None:
        c = self.cfg
        if regime not in self.ic_hist:
            self.ic_hist[regime] = {}

        for fname, xs in factor_xs.items():
            common = xs.index.intersection(fwd_ret.index)
            if len(common) < c.icir_min_obs:
                continue
            x = xs.loc[common].astype(float).values
            y = fwd_ret.loc[common].astype(float).values

            # filter finite
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() < c.icir_min_obs:
                continue

            ic = float(nb_spearman_corr(x[mask], y[mask]))
            if not np.isfinite(ic):
                ic = 0.0

            self.ic_hist[regime].setdefault(fname, []).append((dt, ic))

            # keep bounded history
            if len(self.ic_hist[regime][fname]) > c.icir_lookback * 2:
                self.ic_hist[regime][fname] = self.ic_hist[regime][fname][-c.icir_lookback * 2:]

    def _icir(self, regime: str, fname: str) -> float:
        c = self.cfg
        arr = self.ic_hist.get(regime, {}).get(fname, [])
        if len(arr) < c.icir_min_obs:
            return 0.0
        recent = arr[-c.icir_lookback:]
        ics = np.array([v for _, v in recent], dtype=float)
        if ics.shape[0] < c.icir_min_obs:
            return 0.0
        mu = float(np.mean(ics))
        sd = float(np.std(ics, ddof=1))
        if not np.isfinite(sd) or sd < 1e-12:
            return max(0.0, mu) * 10.0
        return max(0.0, mu / sd)

    def weights(
        self,
        regime: str,
        factor_names: List[str],
    ) -> Dict[str, float]:
        c = self.cfg
        if not factor_names:
            return {}

        raw = {f: self._icir(regime, f) for f in factor_names}
        s = float(sum(raw.values()))
        if s <= 1e-12:
            eq = 1.0 / len(factor_names)
            return {f: eq for f in factor_names}

        w = {f: raw[f] / s for f in factor_names}

        # shrinkage to equal
        eq = 1.0 / len(factor_names)
        w2 = {}
        for f in factor_names:
            w2[f] = (1.0 - c.icir_shrinkage) * w[f] + c.icir_shrinkage * eq

        s2 = float(sum(w2.values()))
        return {f: (w2[f] / s2) for f in factor_names}


# =============================================================================
# Factor Names / Menus (baseline + regime/rate/sector aware)
# =============================================================================

FACTOR_LIST = [
    "mom_12_1",
    "mom_6_1",
    "mom_3_1",
    "reversal_5d",
    "low_vol",
]

REGIME_FACTOR_MENU = {
    "BULL":       ["mom_12_1", "mom_6_1", "mom_3_1", "low_vol"],
    "NORMAL":     ["mom_12_1", "mom_6_1", "low_vol"],
    "CAUTION":    ["mom_12_1", "mom_6_1", "low_vol", "reversal_5d"],
    "TIGHTENING": ["low_vol", "reversal_5d", "mom_12_1"],
    "CRISIS":     ["low_vol", "reversal_5d"],
}


# =============================================================================
# End of Part 1/3
# =============================================================================
# Part 2/3 will include:
# - HRP optimizer
# - CVaR/VolTarget risk manager
# - Sector rotation
# - Portfolio builder (top-k + HRP tilt + caps)
#
# Part 3/3 will include:
# - Backtest engine (strict lag, 50bps costs)
# - Walk-forward validation (train ICIR on IS, test OOS)
# - Detailed logging + main()