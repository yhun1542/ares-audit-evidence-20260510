#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
ARES ULTIMATE v3.0 (Production Integrated, No Look-Ahead, Pareto-Optimized)
================================================================================

- Baseline: v10 Final (Numba-accelerated factors + backtest core)
- Fixes:
  * Remove look-ahead (NO bfill, strict lag execution)
  * Fix drawdown scaling bug (dd should DECREASE risk)
  * Transaction cost: explicit 50 bps round-trip (modeled as 25 bps one-way * turnover)
- Adds (full integration across code parts):
  1) ICIR dynamic factor weights (online, shrinkage, regime-aware)
  2) HRP portfolio construction (scipy optional, robust fallback)
  3) Walk-forward validation runner
  4) CVaR tail-risk leverage cut
  5) Volatility targeting 15% annual
  6) Enhanced regime detector: VIX level + slope + credit spread + trend
  7) Rate regime detector: TLT trend (rising-rate stress)
  8) Sector rotation: mild tilt + sector caps (optional)

NOTE: This file is delivered in 3 parts due to response size limits.
      You are reading PART 1/3.
================================================================================
"""

from __future__ import annotations

import os
import sys
import math
import sqlite3
import logging
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd

from numba import njit, prange

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=os.getenv("ARES_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ARES_Ultimate_v3")


# =============================================================================
# 0) Config
# =============================================================================

@dataclass
class AresConfig:
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"

    start: str = "2016-01-01"
    end: str = "2024-12-31"

    # Universe (can be overridden by DB-driven universe in later parts)
    universe: List[str] = field(default_factory=lambda: [
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA",
        "BRK.B","UNH","JNJ","JPM","V","PG","XOM","HD","CVX",
        "XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB",
        "SPY","QQQ","IWM","DIA","TLT","GLD"
    ])

    benchmark: str = "SPY"
    vix_table: str = "vix"  # (date, close as vix)
    prices_table: str = "daily_ohlcv"

    # Execution / Backtest
    rebalance_days: int = 10
    top_k: int = 15
    warmup_days: int = 260
    signal_lag_days: int = 1  # IMPORTANT: signals at t => weights active at t+1

    # Costs: 50 bps round-trip => 25 bps one-way
    round_trip_cost: float = 0.0050
    one_way_cost: float = 0.0025

    # Risk controls (baseline)
    max_gross_leverage: float = 2.0
    min_gross_leverage: float = 0.0

    # Drawdown control
    dd_soft_stop: float = -0.08
    dd_hard_stop: float = -0.12

    # Vol targeting / CVaR (fully implemented in later parts)
    target_vol_annual: float = 0.15
    cvar_alpha: float = 0.95
    cvar_limit_daily: float = 0.02

    # Regime thresholds (enhanced in later parts)
    vix_risk_on: float = 16.0
    vix_risk_off: float = 25.0

    # Factor horizons (baseline v10 Final style)
    mom_lookbacks: Tuple[int, ...] = (252, 126, 63)
    mom_skip: int = 21

    # Output paths
    out_dir: str = "/home/ubuntu/ares_results/ares_ultimate_v3"


# =============================================================================
# 1) Robust SQLite Loader (schema-safe)
# =============================================================================

class SQLiteLoader:
    def __init__(self, cfg: AresConfig):
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
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]

    def load_prices(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        """
        Load adjusted close if possible. NO bfill allowed.
        Returns wide df(date x symbol).
        """
        if not symbols:
            return pd.DataFrame()

        cols = set(self.table_columns(self.cfg.prices_table))
        # try candidates
        px_candidates = ["adjusted_close", "adj_close", "close"]
        px_col = None
        for c in px_candidates:
            if c in cols:
                px_col = c
                break
        if px_col is None:
            raise RuntimeError(f"Price column not found in {self.cfg.prices_table}: {px_candidates}")

        ph = ",".join(["?"] * len(symbols))
        q = f"""
            SELECT date, symbol, {px_col} AS px
            FROM {self.cfg.prices_table}
            WHERE symbol IN ({ph})
              AND date BETWEEN ? AND ?
            ORDER BY date, symbol
        """
        df = pd.read_sql_query(q, self.conn, params=[*symbols, start, end])
        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date","symbol"], keep="last")
        wide = df.pivot(index="date", columns="symbol", values="px").sort_index()

        # NO look-ahead: allow only ffill (past known prices)
        wide = wide.ffill()

        return wide

    def load_volume(self, symbols: List[str], start: str, end: str) -> pd.DataFrame:
        cols = set(self.table_columns(self.cfg.prices_table))
        if "volume" not in cols:
            return pd.DataFrame()

        ph = ",".join(["?"] * len(symbols))
        q = f"""
            SELECT date, symbol, volume
            FROM {self.cfg.prices_table}
            WHERE symbol IN ({ph})
              AND date BETWEEN ? AND ?
            ORDER BY date, symbol
        """
        df = pd.read_sql_query(q, self.conn, params=[*symbols, start, end])
        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date","symbol"], keep="last")
        wide = df.pivot(index="date", columns="symbol", values="volume").sort_index()
        wide = wide.fillna(0.0)
        return wide

    def load_vix(self, start: str, end: str) -> pd.Series:
        q = f"""
            SELECT date, close as vix
            FROM {self.cfg.vix_table}
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """
        df = pd.read_sql_query(q, self.conn, params=[start, end])
        if df.empty:
            return pd.Series(dtype=float)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date"], keep="last").set_index("date")["vix"].astype(float)
        return df.ffill()


# =============================================================================
# 2) Numba-accelerated factor primitives (from v10 Final, fixed)
# =============================================================================

@njit(parallel=True, cache=True)
def fast_momentum(prices: np.ndarray, lookback: int, skip: int) -> np.ndarray:
    """
    momentum[t] = prices[t-skip] / prices[t-lookback] - 1
    """
    n_dates, n_assets = prices.shape
    out = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback + skip, n_dates):
        for j in range(n_assets):
            p0 = prices[i - lookback, j]
            p1 = prices[i - skip, j]
            if p0 > 0.0 and p1 > 0.0:
                out[i, j] = p1 / p0 - 1.0
    return out

@njit(parallel=True, cache=True)
def fast_volatility(returns: np.ndarray, lookback: int) -> np.ndarray:
    n_dates, n_assets = returns.shape
    out = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i - lookback:i, j]
            # compute nanstd manually is heavy; use simple loop
            # but keep nan guard
            count = 0
            mean = 0.0
            for k in range(window.shape[0]):
                v = window[k]
                if not np.isnan(v):
                    mean += v
                    count += 1
            if count < max(5, lookback // 2):
                continue
            mean /= count
            var = 0.0
            for k in range(window.shape[0]):
                v = window[k]
                if not np.isnan(v):
                    d = v - mean
                    var += d * d
            var /= max(1, count - 1)
            out[i, j] = math.sqrt(var) * math.sqrt(252.0)
    return out

@njit(parallel=True, cache=True)
def fast_rank_normalize(data: np.ndarray) -> np.ndarray:
    """
    Cross-sectional rank per date -> [0,1]
    """
    n_dates, n_assets = data.shape
    out = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(n_dates):
        row = data[i, :]
        # collect valid indices
        valid_idx = np.empty(n_assets, dtype=np.int64)
        n_valid = 0
        for j in range(n_assets):
            if not np.isnan(row[j]):
                valid_idx[n_valid] = j
                n_valid += 1
        if n_valid <= 1:
            continue
        # sort valid by value
        # simple O(n^2) selection is slow; but n_assets small in practice
        # use argsort on copied values
        vals = np.empty(n_valid, dtype=np.float64)
        for k in range(n_valid):
            vals[k] = row[valid_idx[k]]
        order = np.argsort(vals)
        for rank_pos in range(n_valid):
            j = valid_idx[order[rank_pos]]
            out[i, j] = (rank_pos + 1) / n_valid
    return out


# =============================================================================
# 3) Backtest core loop (Numba) - FIXED: strict lag + DD scaling + costs
# =============================================================================

@njit(cache=True)
def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)

@njit(cache=True)
def fast_backtest_core(
    # signals[t, a] in [0,1] rank score; used only on rebalance days
    signals: np.ndarray,
    # returns[t, a] daily returns (close-to-close)
    returns: np.ndarray,
    # leverage caps per day (from regime) - applied to gross exposure
    lev_caps: np.ndarray,
    # rebal schedule: 1 if rebalance decision at day t (end of day)
    rebalance_flag: np.ndarray,
    top_k: int,
    # costs: one-way cost rate, applied to turnover when weights become active
    one_way_cost: float,
    # drawdown control
    dd_soft: float,
    dd_hard: float,
    # loss-streak control
    consec_limit: int,
    loss_scale_min: float,
    recovery_rate: float,
    # strict execution lag (1 day)
    lag_days: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns:
      portfolio_returns[t]
      gross_exposure[t]
    Convention:
      - At end of day t, if rebalance_flag[t]==1, we compute target weights W_target[t].
      - Those weights become ACTIVE at day t+lag_days.
      - PnL at day t uses ACTIVE weights decided earlier.
      - Turnover cost is charged on the day weights become active.
    """
    n_dates, n_assets = signals.shape
    port_rets = np.zeros(n_dates, dtype=np.float64)
    gross_exp = np.zeros(n_dates, dtype=np.float64)

    # store target weights by day (only meaningful on rebalance days)
    # For memory efficiency, store just "pending" and "active"
    w_active = np.zeros(n_assets, dtype=np.float64)
    w_pending = np.zeros(n_assets, dtype=np.float64)

    # queue for lag: store pending weights to activate
    # supports lag_days=1 (generalizable but keep small)
    w_queue = np.zeros((lag_days + 1, n_assets), dtype=np.float64)

    cum = 1.0
    peak = 1.0

    consecutive_losses = 0
    loss_scale = 1.0

    for t in range(n_dates):
        # ---- update drawdown ----
        if cum > peak:
            peak = cum
        dd = cum / peak - 1.0

        # dd_scale: reduce risk when dd worsens
        # soft zone: linear to 0.6, hard zone: down to 0.2
        dd_scale = 1.0
        if dd <= dd_soft:
            # dd_soft is negative
            frac = min(1.0, abs(dd) / max(1e-9, abs(dd_hard)))
            # e.g. at dd=dd_hard => frac=1
            dd_scale = 1.0 - 0.8 * frac  # to 0.2 at worst
            dd_scale = _clip(dd_scale, 0.2, 1.0)

        # ---- loss-streak scale ----
        if t > 0 and port_rets[t - 1] < -0.005:
            consecutive_losses += 1
        elif t > 0 and port_rets[t - 1] > 0.005:
            consecutive_losses = 0

        if consecutive_losses >= consec_limit:
            loss_scale = max(loss_scale_min, loss_scale - 0.15)
        else:
            loss_scale = min(1.0, loss_scale + recovery_rate)

        # ---- activate queued weights (strict lag) ----
        # shift queue
        for q in range(lag_days, 0, -1):
            for j in range(n_assets):
                w_queue[q, j] = w_queue[q - 1, j]
        # w_queue[0] will be filled by today's rebalance decision (end of day)
        # weights that become active today:
        for j in range(n_assets):
            w_active[j] = w_queue[lag_days, j]

        # ---- compute turnover cost (when weights changed vs yesterday active) ----
        # turnover = sum |w_active - w_prev_active|
        turnover = 0.0
        if t > 0:
            # yesterday active is what was active at t-1 (which was w_queue[lag_days] after shifting at t-1)
            # we don't have it explicitly, approximate by storing yesterday port weight in w_pending?:
            # We'll compute turnover vs previous day's w_active by using port_rets memory:
            # simpler: maintain previous active in w_pending.
            pass

        # We'll maintain prev active in w_pending (reuse buffer)
        if t == 0:
            for j in range(n_assets):
                w_pending[j] = 0.0
        turnover = 0.0
        for j in range(n_assets):
            turnover += abs(w_active[j] - w_pending[j])

        cost = turnover * one_way_cost

        # update prev active snapshot
        for j in range(n_assets):
            w_pending[j] = w_active[j]

        # ---- portfolio return ----
        lev = lev_caps[t] * dd_scale * loss_scale
        lev = _clip(lev, 0.0, 3.0)

        pr = 0.0
        for j in range(n_assets):
            wj = w_active[j]
            if wj != 0.0:
                rj = returns[t, j]
                if not np.isnan(rj):
                    pr += wj * rj

        # gross exposure for monitoring
        g = 0.0
        for j in range(n_assets):
            g += abs(w_active[j])
        gross_exp[t] = g * lev

        port_rets[t] = pr * lev - cost
        cum *= (1.0 + port_rets[t])

        # ---- end-of-day rebalance decision -> set w_queue[0] ----
        if rebalance_flag[t] == 1:
            # build equal weight among top_k by signal rank (long-only)
            # NOTE: signal may contain NaNs
            # select indices with largest signal
            # naive O(n log n) sort on CPU inside Numba OK for small n_assets
            idx = np.argsort(-signals[t, :])
            w_new = np.zeros(n_assets, dtype=np.float64)
            cnt = 0
            for k in range(n_assets):
                j = idx[k]
                if not np.isnan(signals[t, j]):
                    w_new[j] = 1.0 / top_k
                    cnt += 1
                    if cnt >= top_k:
                        break
            # push into queue[0]
            for j in range(n_assets):
                w_queue[0, j] = w_new[j]
        else:
            # keep prior queue[0] as 0 to indicate no new target
            for j in range(n_assets):
                w_queue[0, j] = 0.0

    return port_rets, gross_exp


# =============================================================================
# 4) Metrics / Reporting (yearly/regime detailed is in later parts)
# =============================================================================

def calc_metrics(returns: pd.Series) -> Dict[str, float]:
    r = returns.dropna()
    if len(r) < 30:
        return {"sharpe": 0.0, "mdd": 0.0, "annual_return": 0.0, "total_return": 0.0, "vol": 0.0}

    cum = (1.0 + r).cumprod()
    total_ret = float(cum.iloc[-1] - 1.0)
    n_years = len(r) / 252.0
    ann = float((1.0 + total_ret) ** (1.0 / max(1e-9, n_years)) - 1.0)
    vol = float(r.std(ddof=1) * math.sqrt(252.0))
    sharpe = float(ann / (vol + 1e-12))

    peak = cum.cummax()
    dd = cum / peak - 1.0
    mdd = float(dd.min())

    return {"sharpe": sharpe, "mdd": mdd, "annual_return": ann, "total_return": total_ret, "vol": vol}


# =============================================================================
# PART 2/3 will include:
# - EnhancedRegimeDetector + RateRegimeDetector + credit spread loader
# - ICIR online dynamic weights (regime-aware)
# - HRP optimizer + score tilt
# - SectorRotation + sector caps
# - WalkForwardTrainer (OOS 2021-2024)
# - Full production BacktestEngine + main()
# =============================================================================