# -*- coding: utf-8 -*-
"""
ARES v11 (upgrade of v10 Final)
Goal: Improve Sharpe toward 3.0+ by implementing:
1) ICIR 기반 동적 팩터 가중치 (regime-conditional, rolling ICIR, no look-ahead)
2) HRP 포트폴리오 최적화 (equal-weight -> HRP)
3) 개선된 레짐 감지 (VIX slope + credit spread proxy + rates up)
4) 강화된 리스크 관리 (CVaR tail-risk + volatility targeting + DD + consecutive loss defense)
5) 2022 방어 강화 (rates up detection + sector rotation tilt)

Hard requirements implemented:
- Production-level full Python code (600+ lines)
- Complete implementations (no stubs)
- Look-ahead bias prevention throughout (signal/weights use data <= t-1)
- Transaction cost 50bps (tc_total=0.005) applied by turnover
- Numba JIT optimized backtest core + several numeric utilities
"""

from __future__ import annotations

import math
import sys
import warnings
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

from numba import njit

# Optional dependencies; code includes robust fallbacks.
try:
    import yfinance as yf  # type: ignore
except Exception as e:
    yf = None

warnings.filterwarnings("ignore")


# =========================
# Logging
# =========================

def setup_logger(name: str = "ARES", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        ch = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    return logger


LOGGER = setup_logger(level=logging.INFO)


# =========================
# Config
# =========================

@dataclass(frozen=True)
class ARESConfig:
    # Universe: sector ETFs + defensive / macro proxies
    # Equities / sectors
    sector_tickers: Tuple[str, ...] = (
        "SPY",
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE", "XLC"
    )
    # Defensive / diversifiers
    defensive_tickers: Tuple[str, ...] = (
        "TLT", "IEF", "SHY", "GLD"
    )
    # Regime / macro proxies
    vix_ticker: str = "^VIX"
    credit_risk_tickers: Tuple[str, str] = ("HYG", "LQD")   # credit spread proxy: HYG vs LQD
    rates_tickers: Tuple[str, str] = ("TLT", "IEF")         # duration / rates proxy

    start: str = "2015-01-01"
    end: str = "2025-01-01"

    # Trading & selection
    rebalance_days: int = 5  # weekly
    top_k: int = 8

    # Costs
    tc_total: float = 0.005  # 50 bps total cost per full turnover

    # Factor settings (daily bars)
    # Momentum definitions: lookback and skip (to reduce microstructure / mean reversion noise)
    mom_12_1_lookback: int = 252
    mom_12_1_skip: int = 21
    mom_6_1_lookback: int = 126
    mom_6_1_skip: int = 21
    mom_3_1_lookback: int = 63
    mom_3_1_skip: int = 5

    reversal_1m: int = 21  # short-term reversal (1m)
    low_vol_window: int = 63

    # IC/ICIR settings
    ic_horizon: int = 21           # forward return horizon used for IC measurement
    icir_window: int = 252         # rolling window for ICIR
    icir_min_samples: int = 60      # minimum samples per regime-factor to trust ICIR
    icir_clip: float = 3.0         # clip ICIR range to stabilize weights
    icir_floor: float = 0.05        # minimum factor weight

    # HRP settings
    hrp_lookback: int = 252         # covariance lookback
    cov_shrink_lambda: float = 0.10 # diagonal shrinkage for stability
    min_var_floor: float = 1e-12

    # Regime detection
    spy_trend_ma: int = 200
    vix_ma: int = 50
    vix_slope_window: int = 20
    credit_z_window: int = 126
    rates_mom_window: int = 63
    rates_accel_window: int = 21

    # Risk management
    dd_threshold: float = -0.15
    dd_recovery_step: float = 0.05
    consec_limit: int = 3
    loss_scale_min: float = 0.40
    recovery_rate: float = 0.05

    # Vol targeting
    vol_window: int = 63
    target_annual_vol: float = 0.12
    vol_leverage_min: float = 0.20
    vol_leverage_max: float = 3.00

    # Tail risk (CVaR)
    cvar_window: int = 252
    cvar_alpha: float = 0.05
    cvar_limit_daily: float = -0.03  # if CVaR worse than -3% daily, scale down
    cvar_scale_min: float = 0.25

    # Base leverage caps by regime (will be further scaled by DD/loss/vol/CVaR)
    leverage_caps: Dict[str, float] = field(default_factory=lambda: {
        "CRISIS": 0.0,
        "TIGHTENING": 0.5,
        "CAUTION": 0.8,
        "NORMAL": 1.5,
        "BULL": 2.5
    })

    # Default (fallback) factor weights by regime (used when ICIR samples insufficient)
    default_regime_factor_weights: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "BULL": {"mom_12_1": 0.3, "mom_6_1": 0.3, "mom_3_1": 0.2, "low_vol": 0.2, "reversal": 0.0},
        "NORMAL": {"mom_12_1": 0.4, "mom_6_1": 0.3, "mom_3_1": 0.0, "low_vol": 0.3, "reversal": 0.0},
        "CAUTION": {"mom_12_1": 0.3, "mom_6_1": 0.2, "mom_3_1": 0.0, "low_vol": 0.4, "reversal": 0.1},
        "TIGHTENING": {"mom_12_1": 0.2, "mom_6_1": 0.0, "mom_3_1": 0.0, "low_vol": 0.5, "reversal": 0.3},
        "CRISIS": {"mom_12_1": 0.0, "mom_6_1": 0.0, "mom_3_1": 0.0, "low_vol": 0.6, "reversal": 0.4},
    })

    # Macro tilt for 2022 defense (rates up)
    # Positive tilt = more preferred when rates are rising / tightening
    rates_up_tilt: Dict[str, float] = field(default_factory=lambda: {
        "XLE": 0.60,  # Energy
        "XLF": 0.35,  # Financials
        "XLV": 0.15,  # Healthcare
        "XLI": 0.10,  # Industrials
        "XLP": 0.10,  # Staples
        "XLK": -0.55, # Tech (duration-sensitive)
        "XLY": -0.25, # Discretionary (growth beta)
        "XLU": -0.10, # Utilities (bond proxy)
        "XLRE": -0.30,# Real Estate (rates sensitive)
        "SPY": 0.00,
        "XLB": 0.10,
        "XLC": -0.10,
        "TLT": -0.50, # Long duration hurt by rates up (but may hedge equity in crises)
        "IEF": -0.15,
        "SHY": 0.10,
        "GLD": 0.10
    })

    # Rebalance schedule: anchor on first date in data (no lookahead)
    rebalance_on_first_day: bool = True


# =========================
# Utility: Metrics
# =========================

def compute_cagr(equity_curve: pd.Series, periods_per_year: int = 252) -> float:
    equity_curve = equity_curve.dropna()
    if len(equity_curve) < 2:
        return float("nan")
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0]
    years = (len(equity_curve) - 1) / periods_per_year
    if years <= 0:
        return float("nan")
    return total_return ** (1.0 / years) - 1.0


def compute_sharpe(returns: pd.Series, rf_daily: float = 0.0, periods_per_year: int = 252) -> float:
    r = returns.dropna() - rf_daily
    if r.std(ddof=1) == 0 or len(r) < 20:
        return float("nan")
    return math.sqrt(periods_per_year) * (r.mean() / r.std(ddof=1))


def compute_mdd(equity_curve: pd.Series) -> float:
    ec = equity_curve.dropna()
    if ec.empty:
        return float("nan")
    peak = ec.cummax()
    dd = ec / peak - 1.0
    return dd.min()


def yearly_performance(returns: pd.Series) -> pd.DataFrame:
    r = returns.dropna()
    if r.empty:
        return pd.DataFrame(columns=["Return", "Sharpe"])
    years = sorted(set(r.index.year))
    out = []
    for y in years:
        ry = r[r.index.year == y]
        eq = (1.0 + ry).cumprod()
        out.append((y, eq.iloc[-1] - 1.0, compute_sharpe(ry)))
    return pd.DataFrame(out, columns=["Year", "Return", "Sharpe"]).set_index("Year")


# =========================
# Data Loading
# =========================

class MarketDataLoader:
    """
    Downloads and aligns price series for tickers.
    Uses adjusted close when available (yfinance).
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger

    def _download_yf(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        if yf is None:
            raise RuntimeError("yfinance is not installed. Please install it (pip install yfinance).")
        self.log.info(f"Downloading data via yfinance: {len(tickers)} tickers")
        df = yf.download(
            tickers=tickers,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            group_by="column",
            threads=True
        )
        # yfinance format can be:
        # - Single ticker: columns like ['Open','High','Low','Close','Volume']
        # - Multi ticker: columns MultiIndex (Field, Ticker)
        if isinstance(df.columns, pd.MultiIndex):
            if ("Close" in df.columns.get_level_values(0)) or ("Adj Close" in df.columns.get_level_values(0)):
                field = "Close" if "Close" in df.columns.get_level_values(0) else "Adj Close"
                px = df[field].copy()
            else:
                # fallback: take first level
                px = df.xs(df.columns.levels[0][0], axis=1, level=0).copy()
        else:
            # single ticker
            if "Close" in df.columns:
                px = df[["Close"]].copy()
                px.columns = tickers
            elif "Adj Close" in df.columns:
                px = df[["Adj Close"]].copy()
                px.columns = tickers
            else:
                raise ValueError("Unsupported yfinance output columns for single ticker.")
        px = px.sort_index()
        # forward-fill missing, but do not backfill across start
        px = px.ffill()
        # drop rows where all missing
        px = px.dropna(how="all")
        return px

    def load(self) -> Dict[str, pd.DataFrame]:
        tickers_all = list(dict.fromkeys(list(self.cfg.sector_tickers) + list(self.cfg.defensive_tickers)))
        aux_tickers = [self.cfg.vix_ticker, *self.cfg.credit_risk_tickers, *self.cfg.rates_tickers]
        tickers_all = list(dict.fromkeys(tickers_all + aux_tickers))

        prices = self._download_yf(tickers_all, self.cfg.start, self.cfg.end)

        # Separate:
        # - tradable universe: sectors + defensive
        universe = list(dict.fromkeys(list(self.cfg.sector_tickers) + list(self.cfg.defensive_tickers)))

        px_univ = prices[universe].copy()
        vix = prices[[self.cfg.vix_ticker]].copy()
        credit = prices[list(self.cfg.credit_risk_tickers)].copy()
        rates = prices[list(self.cfg.rates_tickers)].copy()

        # Align dates: intersection on all required series for robust feature computation
        idx = px_univ.index
        idx = idx.intersection(vix.index).intersection(credit.index).intersection(rates.index)
        px_univ = px_univ.reindex(idx).ffill()
        vix = vix.reindex(idx).ffill()
        credit = credit.reindex(idx).ffill()
        rates = rates.reindex(idx).ffill()

        # basic sanity
        px_univ = px_univ.replace([np.inf, -np.inf], np.nan).ffill()
        vix = vix.replace([np.inf, -np.inf], np.nan).ffill()
        credit = credit.replace([np.inf, -np.inf], np.nan).ffill()
        rates = rates.replace([np.inf, -np.inf], np.nan).ffill()

        self.log.info(f"Loaded aligned data: {len(idx)} rows, universe={len(universe)} assets")
        return {
            "prices": px_univ,
            "vix": vix,
            "credit": credit,
            "rates": rates
        }


# =========================
# Numeric helpers (Numba)
# =========================

@njit(cache=True)
def _nanmean_1d(x: np.ndarray) -> float:
    s = 0.0
    c = 0
    for i in range(x.shape[0]):
        v = x[i]
        if not np.isnan(v):
            s += v
            c += 1
    return s / c if c > 0 else np.nan


@njit(cache=True)
def _nanstd_1d(x: np.ndarray) -> float:
    m = _nanmean_1d(x)
    if np.isnan(m):
        return np.nan
    s2 = 0.0
    c = 0
    for i in range(x.shape[0]):
        v = x[i]
        if not np.isnan(v):
            d = v - m
            s2 += d * d
            c += 1
    if c <= 1:
        return np.nan
    return math.sqrt(s2 / (c - 1))


@njit(cache=True)
def _zscore_cross_section(x: np.ndarray) -> np.ndarray:
    """
    Cross-sectional z-score for a vector with NaNs.
    """
    out = np.empty_like(x)
    m = _nanmean_1d(x)
    sd = _nanstd_1d(x)
    if np.isnan(m) or np.isnan(sd) or sd == 0.0:
        for i in range(x.shape[0]):
            out[i] = np.nan
        return out
    for i in range(x.shape[0]):
        if np.isnan(x[i]):
            out[i] = np.nan
        else:
            out[i] = (x[i] - m) / sd
    return out


@njit(cache=True)
def cross_sectional_zscores(mat: np.ndarray) -> np.ndarray:
    """
    Apply cross-sectional z-score row-wise.
    mat: (n_dates, n_assets)
    """
    n, m = mat.shape
    out = np.empty((n, m), dtype=np.float64)
    for i in range(n):
        out[i, :] = _zscore_cross_section(mat[i, :])
    return out


@njit(cache=True)
def rolling_log_return(prices: np.ndarray, lookback: int, skip: int) -> np.ndarray:
    """
    Computes log return: log(P[t-skip] / P[t-lookback-skip]) using only past info.
    Output is aligned to t (same length), but uses prices up to t-skip.
    Returns NaN for insufficient history.
    """
    n, m = prices.shape
    out = np.empty((n, m), dtype=np.float64)
    for i in range(n):
        for j in range(m):
            out[i, j] = np.nan
    for i in range(lookback + skip, n):
        i1 = i - skip
        i0 = i - lookback - skip
        for j in range(m):
            p1 = prices[i1, j]
            p0 = prices[i0, j]
            if p1 > 0 and p0 > 0 and (not np.isnan(p1)) and (not np.isnan(p0)):
                out[i, j] = math.log(p1 / p0)
            else:
                out[i, j] = np.nan
    return out


@njit(cache=True)
def rolling_simple_return(prices: np.ndarray, window: int, skip: int = 0) -> np.ndarray:
    """
    Simple return: P[t-skip]/P[t-window-skip] - 1
    """
    n, m = prices.shape
    out = np.empty((n, m), dtype=np.float64)
    for i in range(n):
        for j in range(m):
            out[i, j] = np.nan
    for i in range(window + skip, n):
        i1 = i - skip
        i0 = i - window - skip
        for j in range(m):
            p1 = prices[i1, j]
            p0 = prices[i0, j]
            if p1 > 0 and p0 > 0 and (not np.isnan(p1)) and (not np.isnan(p0)):
                out[i, j] = (p1 / p0) - 1.0
            else:
                out[i, j] = np.nan
    return out


@njit(cache=True)
def rolling_volatility(returns: np.ndarray, window: int) -> np.ndarray:
    """
    Rolling std dev of daily returns (per asset), using trailing window (t-window..t-1).
    Output aligned to t; uses only past.
    """
    n, m = returns.shape
    out = np.empty((n, m), dtype=np.float64)
    for i in range(n):
        for j in range(m):
            out[i, j] = np.nan

    for j in range(m):
        # rolling using sums
        s = 0.0
        s2 = 0.0
        cnt = 0
        buf = np.empty(window, dtype=np.float64)
        for k in range(window):
            buf[k] = np.nan

        for i in range(n):
            # add previous day's return to window
            prev = returns[i - 1, j] if i - 1 >= 0 else np.nan
            idx = (i - 1) % window if i - 1 >= 0 else 0

            # remove old
            old = buf[idx] if i - 1 >= 0 else np.nan
            if i - 1 >= 0:
                if not np.isnan(old):
                    s -= old
                    s2 -= old * old
                    cnt -= 1

                buf[idx] = prev
                if not np.isnan(prev):
                    s += prev
                    s2 += prev * prev
                    cnt += 1

            if i >= window and cnt > 1:
                mean = s / cnt
                var = (s2 - cnt * mean * mean) / (cnt - 1)
                out[i, j] = math.sqrt(var) if var > 0 else 0.0
            else:
                out[i, j] = np.nan
    return out


@njit(cache=True)
def spearman_rank_corr(x: np.ndarray, y: np.ndarray) -> float:
    """
    Spearman rank correlation for vectors with NaNs.
    Ranking implemented via argsort; O(n log n) but n is small (assets).
    """
    n = x.shape[0]
    # filter valid
    valid_idx = np.empty(n, dtype=np.int64)
    k = 0
    for i in range(n):
        if (not np.isnan(x[i])) and (not np.isnan(y[i])):
            valid_idx[k] = i
            k += 1
    if k < 3:
        return np.nan
    xx = np.empty(k, dtype=np.float64)
    yy = np.empty(k, dtype=np.float64)
    for i in range(k):
        ii = valid_idx[i]
        xx[i] = x[ii]
        yy[i] = y[ii]

    # ranks: argsort twice -> rank positions
    rx = np.empty(k, dtype=np.float64)
    ry = np.empty(k, dtype=np.float64)

    ix = np.argsort(xx)
    iy = np.argsort(yy)
    for rank in range(k):
        rx[ix[rank]] = rank + 1.0
        ry[iy[rank]] = rank + 1.0

    # Pearson corr of ranks
    mx = 0.0
    my = 0.0
    for i in range(k):
        mx += rx[i]
        my += ry[i]
    mx /= k
    my /= k

    sxx = 0.0
    syy = 0.0
    sxy = 0.0
    for i in range(k):
        dx = rx[i] - mx
        dy = ry[i] - my
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    if sxx <= 0 or syy <= 0:
        return np.nan
    return sxy / math.sqrt(sxx * syy)


# =========================
# Feature Engineering
# =========================

class FeatureEngineer:
    """
    Computes factor matrices with explicit no-lookahead:
    - All factors are shifted so that factor[t] uses prices <= t-1.
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger

    def compute_returns(self, prices: pd.DataFrame) -> pd.DataFrame:
        rets = prices.pct_change().replace([np.inf, -np.inf], np.nan)
        return rets

    def compute_factors(self, prices: pd.DataFrame, returns: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        self.log.info("Computing factors (mom_12_1, mom_6_1, mom_3_1, reversal, low_vol) with no look-ahead")
        px = prices.values.astype(np.float64)
        rets = returns.values.astype(np.float64)

        mom_12_1 = rolling_log_return(px, self.cfg.mom_12_1_lookback, self.cfg.mom_12_1_skip)
        mom_6_1 = rolling_log_return(px, self.cfg.mom_6_1_lookback, self.cfg.mom_6_1_skip)
        mom_3_1 = rolling_log_return(px, self.cfg.mom_3_1_lookback, self.cfg.mom_3_1_skip)

        # Reversal: negative of trailing 1m return (uses up to t-1 via shifting below)
        rev_1m = rolling_simple_return(px, self.cfg.reversal_1m, skip=0)
        reversal = -rev_1m

        # Low vol: negative rolling volatility (lower vol => higher score), uses only past (t-window..t-1)
        vol = rolling_volatility(rets, self.cfg.low_vol_window)
        low_vol = -vol

        # Convert to DataFrames and shift by 1 to ensure factor at t uses <= t-1
        idx = prices.index
        cols = prices.columns

        def to_df(mat: np.ndarray) -> pd.DataFrame:
            return pd.DataFrame(mat, index=idx, columns=cols)

        f = {
            "mom_12_1": to_df(mom_12_1).shift(1),
            "mom_6_1": to_df(mom_6_1).shift(1),
            "mom_3_1": to_df(mom_3_1).shift(1),
            "reversal": to_df(reversal).shift(1),
            "low_vol": to_df(low_vol).shift(1),
        }
        return f

    def zscore_factors_cross_section(self, factors: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        self.log.info("Cross-sectional z-scoring factors")
        out = {}
        for name, df in factors.items():
            mat = df.values.astype(np.float64)
            z = cross_sectional_zscores(mat)
            out[name] = pd.DataFrame(z, index=df.index, columns=df.columns)
        return out


# =========================
# Regime Detection
# =========================

class RegimeDetector:
    """
    Improved regime detection:
    - SPY trend vs 200MA
    - VIX level vs MA and slope
    - Credit risk proxy: z-score of -log(HYG/LQD) (widening => risk-off)
    - Rates up: TLT momentum + acceleration
    Produces 5 regimes: BULL, NORMAL, CAUTION, TIGHTENING, CRISIS.
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger

    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False, min_periods=span).mean()

    @staticmethod
    def _zscore_ts(series: pd.Series, window: int) -> pd.Series:
        m = series.rolling(window, min_periods=window).mean()
        s = series.rolling(window, min_periods=window).std(ddof=1)
        return (series - m) / s

    def compute_regime_features(
        self,
        prices_univ: pd.DataFrame,
        vix: pd.DataFrame,
        credit: pd.DataFrame,
        rates: pd.DataFrame
    ) -> pd.DataFrame:
        idx = prices_univ.index
        if "SPY" not in prices_univ.columns:
            raise ValueError("SPY must be in universe for regime detection.")

        spy = prices_univ["SPY"].copy()
        spy_ma = spy.rolling(self.cfg.spy_trend_ma, min_periods=self.cfg.spy_trend_ma).mean()
        spy_trend = (spy / spy_ma - 1.0).shift(1)  # only past

        v = vix.iloc[:, 0].copy()
        v_ma = v.rolling(self.cfg.vix_ma, min_periods=self.cfg.vix_ma).mean()
        v_level = (v / v_ma - 1.0).shift(1)
        v_slope = (v.diff(self.cfg.vix_slope_window) / self.cfg.vix_slope_window).shift(1)

        # Credit risk proxy
        hyg = credit[self.cfg.credit_risk_tickers[0]].copy()
        lqd = credit[self.cfg.credit_risk_tickers[1]].copy()
        # risk proxy: widening when HYG underperforms LQD => ratio down => -log(ratio) up
        credit_risk = (-np.log(hyg / lqd)).replace([np.inf, -np.inf], np.nan)
        credit_z = self._zscore_ts(credit_risk, self.cfg.credit_z_window).shift(1)

        # Rates up proxy:
        tlt = rates[self.cfg.rates_tickers[0]].copy()
        # momentum and acceleration of TLT (bond price down => rates up)
        tlt_mom = (tlt.pct_change(self.cfg.rates_mom_window)).shift(1)
        tlt_accel = (tlt.pct_change(self.cfg.rates_accel_window)).shift(1)

        feats = pd.DataFrame(index=idx)
        feats["spy_trend"] = spy_trend
        feats["vix_level"] = v_level
        feats["vix_slope"] = v_slope
        feats["credit_z"] = credit_z
        feats["tlt_mom"] = tlt_mom
        feats["tlt_accel"] = tlt_accel

        # Rates-up boolean: sustained negative bond momentum and negative acceleration
        feats["rates_up"] = ((feats["tlt_mom"] < -0.05) & (feats["tlt_accel"] < -0.01)).astype(float)

        return feats

    def classify_regime(self, feats: pd.DataFrame) -> pd.Series:
        """
        Rule-based regime mapping with hysteresis smoothing.
        Output shifted by 1 already in feats inputs; classification uses available feats at t.
        """
        self.log.info("Classifying regimes with VIX slope + credit proxy + rates-up logic (no look-ahead)")

        spy_trend = feats["spy_trend"]
        vix_level = feats["vix_level"]
        vix_slope = feats["vix_slope"]
        credit_z = feats["credit_z"]
        rates_up = feats["rates_up"]

        # Raw regime score: higher => more risk-off
        # We intentionally combine signals for robustness.
        # credit_z contributes strongly to crisis detection.
        score = (
            1.5 * credit_z.fillna(0.0)
            + 1.0 * (vix_level.fillna(0.0) * 2.0)
            + 0.7 * (vix_slope.fillna(0.0) * 10.0)
            - 1.0 * (spy_trend.fillna(0.0) * 4.0)
            + 0.8 * rates_up.fillna(0.0)
        )

        # Smooth score to avoid regime whipsaw
        smooth = score.rolling(10, min_periods=5).median()

        reg = pd.Series(index=feats.index, dtype="object")

        # Thresholds tuned to be stable across regimes (not overfit; robust transforms)
        # Crisis: very high stress
        crisis = (smooth > 2.0) | ((credit_z > 1.5) & (vix_level > 0.5)) | ((spy_trend < -0.03) & (vix_slope > 0.0) & (vix_level > 0.3))
        tightening = ((smooth > 1.0) | (rates_up > 0.0)) & (~crisis)
        caution = ((smooth > 0.4) | (vix_level > 0.15) | (credit_z > 0.5)) & (~tightening) & (~crisis)
        bull = (spy_trend > 0.03) & (vix_level < -0.10) & (credit_z < 0.0) & (~caution) & (~tightening) & (~crisis)

        reg.loc[:] = "NORMAL"
        reg.loc[caution] = "CAUTION"
        reg.loc[tightening] = "TIGHTENING"
        reg.loc[crisis] = "CRISIS"
        reg.loc[bull] = "BULL"

        # Additional hysteresis: enforce minimum persistence of 3 days to reduce churn
        reg2 = reg.copy()
        min_persist = 3
        last = None
        streak = 0
        for i, d in enumerate(reg.index):
            cur = reg.iloc[i]
            if last is None:
                last = cur
                streak = 1
                reg2.iloc[i] = cur
                continue
            if cur == last:
                streak += 1
                reg2.iloc[i] = cur
            else:
                if streak < min_persist:
                    reg2.iloc[i] = last
                    streak += 1
                else:
                    last = cur
                    streak = 1
                    reg2.iloc[i] = cur

        return reg2

    def run(
        self,
        prices_univ: pd.DataFrame,
        vix: pd.DataFrame,
        credit: pd.DataFrame,
        rates: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.Series]:
        feats = self.compute_regime_features(prices_univ, vix, credit, rates)
        regime = self.classify_regime(feats)
        return feats, regime


# =========================
# IC / ICIR Dynamic Factor Weights (Regime-conditional)
# =========================

class ICIRWeighter:
    """
    Computes regime-conditional rolling ICIR for each factor and derives dynamic weights.
    Look-ahead prevention:
    - We compute IC_end[t] = corr(factor[t-h], fwd_return(t-h+1..t)).
      This uses only returns up to t (end date) and factor known at t-h.
    - To decide weights at decision date D (used for trading day D+1), we use IC_end up to D
      but in our trading framework we rebalance at day t based on info <= t-1,
      so we use IC_end up to t-1.
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger
        self.factor_names = ["mom_12_1", "mom_6_1", "mom_3_1", "reversal", "low_vol"]
        self.regimes = ["BULL", "NORMAL", "CAUTION", "TIGHTENING", "CRISIS"]

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        x = x - np.nanmax(x)
        e = np.exp(x)
        s = np.nansum(e)
        if not np.isfinite(s) or s <= 0:
            return np.ones_like(x) / len(x)
        return e / s

    def _compute_forward_horizon_return(self, returns: pd.DataFrame, horizon: int) -> pd.DataFrame:
        """
        Forward cumulative return over horizon: (1+r_{t+1})...(1+r_{t+h}) - 1
        Align to end date t+h (we will use end date index).
        """
        # Use log-sum trick for numerical stability.
        # fwd_end[t] = exp(sum_{k=t-h+1..t} log(1+r_k)) - 1  (end-aligned)
        lr = np.log1p(returns.replace([np.inf, -np.inf], np.nan))
        # rolling sum of log returns
        roll = lr.rolling(horizon, min_periods=horizon).sum()
        fwd_end = np.expm1(roll)
        return fwd_end

    def compute_ic_end_series(
        self,
        factor_z: Dict[str, pd.DataFrame],
        returns: pd.DataFrame,
        regime: pd.Series
    ) -> Dict[str, pd.Series]:
        """
        For each factor, compute IC_end[t] at end date t:
        IC_end[t] = SpearmanCorr( factor_z[t-h], fwd_return_end[t] )
        with regime bucket assigned by regime at signal date (t-h).
        """
        self.log.info("Computing IC_end series for each factor (Spearman, end-aligned, no look-ahead)")
        h = self.cfg.ic_horizon
        fwd_end = self._compute_forward_horizon_return(returns, h)

        idx = returns.index
        n_dates = len(idx)
        n_assets = returns.shape[1]

        # Convert to numpy for numba rank-corr
        fwd_mat = fwd_end.values.astype(np.float64)
        reg_arr = regime.values.astype(object)  # keep python for bucket assignment later

        ic_end: Dict[str, pd.Series] = {}
        for fname in self.factor_names:
            fac = factor_z[fname].values.astype(np.float64)
            ic_vals = np.full(n_dates, np.nan, dtype=np.float64)

            # compute end-aligned IC:
            # for end date i, use factor at i-h
            for i in range(h, n_dates):
                x = fac[i - h, :]
                y = fwd_mat[i, :]
                ic_vals[i] = spearman_rank_corr(x, y)

            ic_end[fname] = pd.Series(ic_vals, index=idx, name=f"IC_end_{fname}")

        return ic_end

    def compute_dynamic_weights(
        self,
        ic_end: Dict[str, pd.Series],
        regime: pd.Series,
        default_weights: Dict[str, Dict[str, float]]
    ) -> pd.DataFrame:
        """
        Output:
          weights_df: index=date, columns=factors, values=dynamic weights for CURRENT regime at date t
          (to be used for signals at date t; trading uses t+1 per system shift).
        Method:
          For each date t, look back icir_window on IC_end values <= t-1
          filtered by regime at signal date (we approximate using regime at IC_end date minus horizon).
          Practically, we bucket IC_end[t] by regime[t-h] at time of IC computation.
        """
        self.log.info("Computing dynamic factor weights from rolling ICIR by regime (no look-ahead)")
        idx = regime.index
        h = self.cfg.ic_horizon

        # Precompute regime_at_signal_end[t] = regime[t-h] (signal date)
        reg_signal = regime.shift(h)

        # Build table of IC_end values by factor and date
        ic_df = pd.DataFrame({f: ic_end[f] for f in self.factor_names})
        ic_df["reg_signal"] = reg_signal

        weights = pd.DataFrame(index=idx, columns=self.factor_names, dtype=float)

        for t in range(len(idx)):
            dt = idx[t]
            # decision at dt uses only IC_end up to dt-1
            if t <= 1:
                # fallback early
                cur_reg = regime.iloc[t]
                w = default_weights.get(cur_reg, default_weights["NORMAL"])
                weights.iloc[t] = [w.get(f, 0.0) for f in self.factor_names]
                continue

            cur_reg = regime.iloc[t]
            # select historical IC_end values up to t-1 where signal regime matches current regime
            hist = ic_df.iloc[:t].copy()
            hist = hist[hist["reg_signal"] == cur_reg]
            # rolling window
            hist = hist.tail(self.cfg.icir_window)

            icir_vals = np.full(len(self.factor_names), np.nan, dtype=np.float64)
            sample_counts = np.zeros(len(self.factor_names), dtype=np.int64)

            for k, f in enumerate(self.factor_names):
                s = hist[f].dropna()
                sample_counts[k] = len(s)
                if len(s) >= self.cfg.icir_min_samples and s.std(ddof=1) > 0:
                    icir = float(s.mean() / s.std(ddof=1))
                    icir = float(np.clip(icir, -self.cfg.icir_clip, self.cfg.icir_clip))
                    icir_vals[k] = icir
                else:
                    icir_vals[k] = np.nan

            # If too sparse, fallback to defaults for this regime
            if np.sum(np.isfinite(icir_vals)) < 2:
                w0 = default_weights.get(cur_reg, default_weights["NORMAL"])
                wv = np.array([w0.get(f, 0.0) for f in self.factor_names], dtype=np.float64)
                # ensure nonnegative and normalized
                wv = np.maximum(wv, 0.0)
                s = wv.sum()
                weights.iloc[t] = (wv / s) if s > 0 else (np.ones_like(wv) / len(wv))
                continue

            # Convert ICIR to weights via softmax on positive-shifted ICIR.
            # Keep floors to avoid collapsing to single factor.
            icir_fill = np.where(np.isfinite(icir_vals), icir_vals, -0.5)
            wv = self._softmax(icir_fill)
            wv = np.maximum(wv, self.cfg.icir_floor)
            wv = wv / wv.sum()

            weights.iloc[t] = wv

        # ensure stable & normalized
        weights = weights.fillna(method="ffill").fillna(0.0)
        weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(1.0 / len(self.factor_names))
        return weights


# =========================
# HRP Optimizer (with robust fallback)
# =========================

def _cov_shrink_diagonal(cov: np.ndarray, lam: float) -> np.ndarray:
    """
    Simple diagonal shrinkage: cov' = (1-lam)*cov + lam*diag(diag(cov))
    """
    d = np.diag(np.diag(cov))
    return (1.0 - lam) * cov + lam * d


def _corr_from_cov(cov: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    d = np.sqrt(np.maximum(np.diag(cov), eps))
    denom = np.outer(d, d)
    return cov / np.maximum(denom, eps)


def _correlation_distance(corr: np.ndarray) -> np.ndarray:
    # Dist = sqrt(0.5*(1-corr)) (Lopez de Prado)
    return np.sqrt(np.maximum(0.0, 0.5 * (1.0 - corr)))


def _avg_linkage(dist: np.ndarray) -> np.ndarray:
    """
    Pure numpy average-linkage hierarchical clustering.
    Input:
      dist: (n,n) symmetric distance matrix (zeros on diagonal)
    Output:
      Z linkage matrix shape (n-1, 4) like scipy.cluster.hierarchy.linkage
      columns: [cluster1, cluster2, distance, new_cluster_size]
    Complexity: O(n^3) but n is small (top_k <= ~20).
    """
    n = dist.shape[0]
    # Active clusters: map cluster_id -> list of leaf indices
    clusters: Dict[int, List[int]] = {i: [i] for i in range(n)}
    active = list(range(n))
    next_id = n
    Z = np.zeros((n - 1, 4), dtype=np.float64)

    # Precompute distances between clusters by average of pairwise dist
    def cluster_distance(a: List[int], b: List[int]) -> float:
        s = 0.0
        c = 0
        for i in a:
            for j in b:
                s += dist[i, j]
                c += 1
        return s / c if c > 0 else 0.0

    for step in range(n - 1):
        best_i = -1
        best_j = -1
        best_d = 1e18

        # Find closest pair among active clusters
        for ii in range(len(active)):
            for jj in range(ii + 1, len(active)):
                ci = active[ii]
                cj = active[jj]
                d = cluster_distance(clusters[ci], clusters[cj])
                if d < best_d:
                    best_d = d
                    best_i = ci
                    best_j = cj

        # Merge best_i and best_j
        leaves_i = clusters[best_i]
        leaves_j = clusters[best_j]
        new_leaves = leaves_i + leaves_j
        clusters[next_id] = new_leaves

        # Record linkage
        Z[step, 0] = float(best_i)
        Z[step, 1] = float(best_j)
        Z[step, 2] = float(best_d)
        Z[step, 3] = float(len(new_leaves))

        # Update active list
        active = [c for c in active if c not in (best_i, best_j)]
        active.append(next_id)
        next_id += 1

    return Z


def _get_quasi_diag(Z: np.ndarray) -> List[int]:
    """
    Quasi-diagonalization of linkage matrix to obtain sorted leaf order.
    Compatible with scipy linkage-like Z.
    """
    Z = Z.astype(int)
    n = Z.shape[0] + 1
    # Start from last merge
    sort_ix = [int(Z[-1, 0]), int(Z[-1, 1])]

    def expand(ix: int) -> List[int]:
        if ix < n:
            return [ix]
        row = ix - n
        left = int(Z[row, 0])
        right = int(Z[row, 1])
        return expand(left) + expand(right)

    # Expand recursively
    out: List[int] = []
    for ix in sort_ix:
        out.extend(expand(ix))
    return out


def _get_ivp(cov: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    ivp = 1.0 / np.maximum(np.diag(cov), eps)
    w = ivp / ivp.sum()
    return w


def _cluster_var(cov: np.ndarray) -> float:
    w = _get_ivp(cov)
    v = float(w.T @ cov @ w)
    return max(v, 0.0)


def _hrp_recursive_bisection(cov: np.ndarray, sort_ix: List[int]) -> np.ndarray:
    """
    Compute HRP weights given covariance and quasi-diagonal sorted indices.
    """
    n = cov.shape[0]
    w = pd.Series(1.0, index=sort_ix)

    clusters = [sort_ix]
    while clusters:
        cluster = clusters.pop(0)
        if len(cluster) <= 1:
            continue
        split = len(cluster) // 2
        c1 = cluster[:split]
        c2 = cluster[split:]

        cov1 = cov[np.ix_(c1, c1)]
        cov2 = cov[np.ix_(c2, c2)]
        v1 = _cluster_var(cov1)
        v2 = _cluster_var(cov2)
        alpha = 1.0 - v1 / (v1 + v2) if (v1 + v2) > 0 else 0.5

        w[c1] *= alpha
        w[c2] *= (1.0 - alpha)

        clusters.append(c1)
        clusters.append(c2)

    # reorder back to 0..n-1
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        out[i] = float(w.get(i, 0.0))
    s = out.sum()
    return out / s if s > 0 else np.ones(n, dtype=np.float64) / n


class HRPAllocator:
    """
    Computes HRP weights for selected assets at rebalance dates.
    Includes diagonal covariance shrinkage for robustness and a pure-numpy linkage fallback.
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger
        self._use_scipy = False
        try:
            from scipy.cluster.hierarchy import linkage as scipy_linkage  # type: ignore
            from scipy.spatial.distance import squareform  # type: ignore
            self._scipy_linkage = scipy_linkage
            self._squareform = squareform
            self._use_scipy = True
        except Exception:
            self._use_scipy = False

    def hrp_weights(self, ret_window: np.ndarray) -> np.ndarray:
        """
        ret_window: (T, N) returns matrix for selected assets.
        Output: (N,) weights sum to 1.
        """
        if ret_window.shape[1] == 1:
            return np.array([1.0], dtype=np.float64)

        cov = np.cov(ret_window, rowvar=False)
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
        cov = _cov_shrink_diagonal(cov, self.cfg.cov_shrink_lambda)

        # Ensure positive diagonals
        diag = np.diag(cov).copy()
        diag = np.maximum(diag, self.cfg.min_var_floor)
        np.fill_diagonal(cov, diag)

        corr = _corr_from_cov(cov)
        corr = np.clip(corr, -1.0, 1.0)
        dist = _correlation_distance(corr)

        # Hierarchical clustering to get ordering
        if self._use_scipy:
            # SciPy linkage needs condensed distance matrix
            condensed = self._squareform(dist, checks=False)
            Z = self._scipy_linkage(condensed, method="average")
        else:
            Z = _avg_linkage(dist)

        sort_ix = _get_quasi_diag(np.asarray(Z))
        w = _hrp_recursive_bisection(cov, sort_ix)

        # Normalize and floor tiny negatives due to numerics
        w = np.maximum(w, 0.0)
        s = w.sum()
        return w / s if s > 0 else np.ones_like(w) / len(w)


# =========================
# Composite Scoring Model (Dynamic factor weights + macro tilts)
# =========================

class CompositeSignalModel:
    """
    Produces composite cross-sectional score per asset per date:
      score[t, asset] = sum_k w_factor[t,k] * z_factor[t,asset,k] + macro_tilt[t,asset]
    Macro tilt implements 2022 defense:
      - rates_up => tilt sector preferences (energy/financials up, tech down)
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger
        self.factor_names = ["mom_12_1", "mom_6_1", "mom_3_1", "reversal", "low_vol"]

    def macro_tilt(self, idx: pd.Index, columns: pd.Index, regime_feats: pd.DataFrame) -> pd.DataFrame:
        rates_up = regime_feats["rates_up"].reindex(idx).fillna(0.0)
        tilt = pd.DataFrame(0.0, index=idx, columns=columns)

        # apply per-asset tilt * rates_up intensity; shift already in feats, so no lookahead
        for c in columns:
            tilt_val = self.cfg.rates_up_tilt.get(str(c), 0.0)
            tilt[c] = tilt_val * rates_up

        # Clip macro tilt to avoid dominating factors
        tilt = tilt.clip(-0.75, 0.75)
        return tilt

    def build_scores(
        self,
        factor_z: Dict[str, pd.DataFrame],
        dynamic_factor_weights: pd.DataFrame,
        regime_feats: pd.DataFrame
    ) -> pd.DataFrame:
        self.log.info("Building composite scores from dynamic factor weights + macro tilt")
        idx = dynamic_factor_weights.index
        cols = next(iter(factor_z.values())).columns

        # align
        w = dynamic_factor_weights.reindex(idx).fillna(method="ffill").fillna(0.0)
        macro = self.macro_tilt(idx, cols, regime_feats)

        score = pd.DataFrame(0.0, index=idx, columns=cols)
        for f in self.factor_names:
            score = score.add(factor_z[f].reindex(idx).fillna(0.0).mul(w[f], axis=0), fill_value=0.0)

        score = score.add(macro, fill_value=0.0)

        # Optional mild winsorization to reduce extreme ranks that can amplify turnover
        score = score.clip(-4.0, 4.0)
        return score


# =========================
# Portfolio Construction (Top-K selection + HRP weights on selected)
# =========================

class PortfolioConstructor:
    """
    For each rebalance date t:
      - uses score at t-1 (enforced by shifting upstream) to pick top_k
      - computes HRP weights using returns history up to t-1
      - output target weights for entire universe at date t (trade at close t-1 -> hold during day t)
    """

    def __init__(self, config: ARESConfig, allocator: HRPAllocator, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.alloc = allocator
        self.log = logger

    def _get_rebalance_flags(self, idx: pd.Index) -> np.ndarray:
        n = len(idx)
        flags = np.zeros(n, dtype=np.uint8)
        if n == 0:
            return flags
        start = 0 if self.cfg.rebalance_on_first_day else 1
        for i in range(start, n, self.cfg.rebalance_days):
            flags[i] = 1
        return flags

    def build_target_weights(
        self,
        scores: pd.DataFrame,
        returns: pd.DataFrame,
        regime: pd.Series
    ) -> Tuple[pd.DataFrame, np.ndarray, pd.Series]:
        """
        Returns:
          target_weights: DataFrame (n_dates, n_assets); only meaningful on rebalance dates else zeros
          rebalance_flags: np.uint8 array
          leverage_cap_series: base cap by regime (further scaled in backtest)
        """
        self.log.info("Constructing target weights: Top-K selection + HRP optimization on rebalance dates")
        idx = scores.index
        cols = scores.columns
        n = len(idx)

        flags = self._get_rebalance_flags(idx)
        target = pd.DataFrame(0.0, index=idx, columns=cols)

        # base leverage caps by regime
        lev_caps = regime.reindex(idx).map(self.cfg.leverage_caps).fillna(self.cfg.leverage_caps["NORMAL"])

        # Precompute for speed
        ret_vals = returns.reindex(idx).values.astype(np.float64)
        score_vals = scores.reindex(idx).values.astype(np.float64)

        for i in range(n):
            if flags[i] != 1:
                continue
            if i < 2:
                continue  # insufficient history
            # Use score at i (already based on info <= i-1 due to upstream shift),
            # and HRP returns window up to i-1.
            srow = score_vals[i, :]
            valid = np.isfinite(srow)
            if valid.sum() < self.cfg.top_k:
                continue
            # select top_k
            # argsort descending, but handle NaNs by -inf
            s2 = np.where(valid, srow, -1e18)
            top_idx = np.argsort(-s2)[: self.cfg.top_k]

            # HRP covariance window: use returns up to i-1, last hrp_lookback days
            start = max(0, i - self.cfg.hrp_lookback)
            ret_window = ret_vals[start:i, :][:, top_idx]
            # drop rows with any nan in selected assets for stability
            # (HRP covariance is sensitive; remove nan rows)
            if ret_window.shape[0] < 30:
                # fallback equal weights
                w_sel = np.ones(len(top_idx), dtype=np.float64) / len(top_idx)
            else:
                mask = np.isfinite(ret_window).all(axis=1)
                ret_window = ret_window[mask]
                if ret_window.shape[0] < 30:
                    w_sel = np.ones(len(top_idx), dtype=np.float64) / len(top_idx)
                else:
                    w_sel = self.alloc.hrp_weights(ret_window)

            w_full = np.zeros(len(cols), dtype=np.float64)
            for k, j in enumerate(top_idx):
                w_full[j] = float(w_sel[k])
            target.iloc[i, :] = w_full

        return target, flags, lev_caps


# =========================
# Backtest Core (Numba)
# =========================

@njit(cache=True)
def _rolling_std_from_buffer(buf: np.ndarray, count: int) -> float:
    if count <= 1:
        return np.nan
    s = 0.0
    s2 = 0.0
    for i in range(count):
        v = buf[i]
        s += v
        s2 += v * v
    mean = s / count
    var = (s2 - count * mean * mean) / (count - 1)
    return math.sqrt(var) if var > 0 else 0.0


@njit(cache=True)
def _cvar_from_buffer(buf: np.ndarray, count: int, alpha: float) -> float:
    """
    Empirical CVaR (expected shortfall) of returns in buf (count elements),
    averaging the worst alpha fraction.
    Negative CVaR implies losses.
    """
    if count < 20:
        return np.nan
    tmp = np.empty(count, dtype=np.float64)
    for i in range(count):
        tmp[i] = buf[i]
    tmp.sort()
    k = int(math.floor(alpha * count))
    if k < 1:
        k = 1
    s = 0.0
    for i in range(k):
        s += tmp[i]
    return s / k


@njit(cache=True)
def fast_backtest_ares_v11(
    returns: np.ndarray,              # (n_dates, n_assets) daily returns
    target_weights: np.ndarray,       # (n_dates, n_assets) target weights on rebalance days, else 0
    rebalance_flags: np.ndarray,      # (n_dates,) uint8
    leverage_caps: np.ndarray,        # (n_dates,) base leverage cap by regime
    tc_total: float,
    dd_threshold: float,
    dd_recovery_step: float,
    consec_limit: int,
    loss_scale_min: float,
    recovery_rate: float,
    vol_window: int,
    target_daily_vol: float,
    vol_leverage_min: float,
    vol_leverage_max: float,
    cvar_window: int,
    cvar_alpha: float,
    cvar_limit_daily: float,
    cvar_scale_min: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Production backtest loop with:
    - Rebalance to target weights on rebalance days
    - Transaction cost based on turnover
    - DD scaling, consecutive loss scaling
    - Volatility targeting (rolling std on realized portfolio returns up to t-1)
    - CVaR tail-risk scaling (computed on rebalance days using rolling window)

    Look-ahead:
    - weights/targets must already be computed using <= t-1 info and applied at day t.
    - this loop uses day t target at start of day t then applies returns[t].
    """
    n_dates, n_assets = returns.shape
    port_rets = np.zeros(n_dates, dtype=np.float64)
    lever_series = np.zeros(n_dates, dtype=np.float64)
    turnover_series = np.zeros(n_dates, dtype=np.float64)

    current_w = np.zeros(n_assets, dtype=np.float64)

    cum_ret = 1.0
    peak = 1.0
    dd_scale = 1.0
    consecutive_losses = 0
    loss_scale = 1.0

    # rolling buffer for realized portfolio returns (for vol & CVaR scaling)
    vol_buf = np.empty(vol_window, dtype=np.float64)
    for i in range(vol_window):
        vol_buf[i] = 0.0
    vol_count = 0

    cvar_buf = np.empty(cvar_window, dtype=np.float64)
    for i in range(cvar_window):
        cvar_buf[i] = 0.0
    cvar_count = 0

    cvar_scale = 1.0

    for t in range(n_dates):
        # Update drawdown scale based on cum_ret (from previous day's close)
        if cum_ret > peak:
            peak = cum_ret
        dd = (cum_ret / peak) - 1.0
        if dd < dd_threshold:
            # dd_threshold negative. Example -0.15. dd / dd_threshold > 1 when worse than threshold
            # Scale goes to 0 as dd approaches 2*threshold, floor at 0.
            dd_scale = max(0.0, 1.0 + dd / dd_threshold)
        else:
            dd_scale = min(1.0, dd_scale + dd_recovery_step)

        # consecutive loss defense from realized returns
        if t > 0 and port_rets[t - 1] < -0.005:
            consecutive_losses += 1
        elif t > 0 and port_rets[t - 1] > 0.005:
            consecutive_losses = 0

        if consecutive_losses >= consec_limit:
            loss_scale = max(loss_scale_min, loss_scale - 0.15)
        else:
            loss_scale = min(1.0, loss_scale + recovery_rate)

        # Rebalance
        total_cost = 0.0
        if rebalance_flags[t] == 1:
            new_w = target_weights[t, :]
            # normalize if numeric drift (and avoid all-zero)
            s = 0.0
            for j in range(n_assets):
                if new_w[j] > 0:
                    s += new_w[j]
            if s > 0:
                for j in range(n_assets):
                    new_wj = new_w[j]
                    if new_wj > 0:
                        new_w[j] = new_wj / s
                    else:
                        new_w[j] = 0.0
            # turnover
            turnover = 0.0
            for j in range(n_assets):
                turnover += abs(new_w[j] - current_w[j])
            turnover_series[t] = turnover
            total_cost = turnover * tc_total * 0.5
            # update holdings
            for j in range(n_assets):
                current_w[j] = new_w[j]

            # update CVaR scale on rebalance days
            # Use trailing realized returns up to t-1 (already in buffer)
            if cvar_count >= 20:
                cvar = _cvar_from_buffer(cvar_buf, cvar_count, cvar_alpha)
                # If CVaR worse (more negative) than limit, scale down proportionally.
                if not np.isnan(cvar) and cvar < cvar_limit_daily:
                    # e.g., cvar=-0.05, limit=-0.03 => scale ~ 0.6
                    raw = abs(cvar_limit_daily) / max(abs(cvar), 1e-12)
                    cvar_scale = max(cvar_scale_min, min(1.0, raw))
                else:
                    cvar_scale = min(1.0, cvar_scale + 0.10)
            else:
                cvar_scale = 1.0

        # Volatility targeting based on trailing realized portfolio returns up to t-1
        vol_scale = 1.0
        if vol_count >= max(20, vol_window // 3):
            vol = _rolling_std_from_buffer(vol_buf, vol_count)
            if not np.isnan(vol) and vol > 1e-12:
                vol_scale = target_daily_vol / vol
                if vol_scale < vol_leverage_min:
                    vol_scale = vol_leverage_min
                if vol_scale > vol_leverage_max:
                    vol_scale = vol_leverage_max
            else:
                vol_scale = 1.0

        # Final leverage
        leverage = leverage_caps[t] * dd_scale * loss_scale * vol_scale * cvar_scale
        lever_series[t] = leverage

        # Portfolio return for day t
        pret = 0.0
        for j in range(n_assets):
            wj = current_w[j]
            if wj != 0.0:
                pret += wj * returns[t, j]
        pret = pret * leverage - total_cost
        port_rets[t] = pret
        cum_ret *= (1.0 + pret)

        # Update rolling buffers with realized return of day t
        # Vol buffer uses last vol_window returns
        if vol_window > 0:
            vol_buf[t % vol_window] = pret
            if vol_count < vol_window:
                vol_count += 1
        if cvar_window > 0:
            cvar_buf[t % cvar_window] = pret
            if cvar_count < cvar_window:
                cvar_count += 1

    return port_rets, lever_series, turnover_series


# =========================
# Orchestrator
# =========================

class ARESv11System:
    """
    End-to-end pipeline:
    - Load data
    - Compute factors (no lookahead)
    - Detect regimes (improved)
    - Compute IC_end + dynamic regime conditional ICIR weights
    - Build composite scores (dynamic weights + macro tilt)
    - Construct HRP portfolio on rebalance dates
    - Backtest with risk management (DD + loss defense + vol targeting + CVaR)
    """

    def __init__(self, config: ARESConfig, logger: logging.Logger = LOGGER):
        self.cfg = config
        self.log = logger

        self.loader = MarketDataLoader(config, logger)
        self.fe = FeatureEngineer(config, logger)
        self.reg = RegimeDetector(config, logger)
        self.icir = ICIRWeighter(config, logger)
        self.hrp = HRPAllocator(config, logger)
        self.model = CompositeSignalModel(config, logger)
        self.pc = PortfolioConstructor(config, self.hrp, logger)

    def run(self) -> Dict[str, object]:
        data = self.loader.load()
        prices = data["prices"]
        vix = data["vix"]
        credit = data["credit"]
        rates = data["rates"]

        returns = self.fe.compute_returns(prices)
        factors = self.fe.compute_factors(prices, returns)
        factor_z = self.fe.zscore_factors_cross_section(factors)

        regime_feats, regime = self.reg.run(prices, vix, credit, rates)

        # ICIR dynamic weights
        ic_end = self.icir.compute_ic_end_series(factor_z, returns, regime)
        dyn_w = self.icir.compute_dynamic_weights(ic_end, regime, self.cfg.default_regime_factor_weights)

        # Composite scores
        scores = self.model.build_scores(factor_z, dyn_w, regime_feats)

        # Portfolio construction
        target_w_df, rebalance_flags, lev_caps = self.pc.build_target_weights(scores, returns, regime)

        # Backtest arrays
        ret_arr = returns.values.astype(np.float64)
        target_arr = target_w_df.values.astype(np.float64)
        lev_arr = lev_caps.values.astype(np.float64)
        flags_arr = rebalance_flags.astype(np.uint8)

        target_daily_vol = self.cfg.target_annual_vol / math.sqrt(252.0)

        port_rets, lever_series, turnover_series = fast_backtest_ares_v11(
            ret_arr,
            target_arr,
            flags_arr,
            lev_arr,
            float(self.cfg.tc_total),
            float(self.cfg.dd_threshold),
            float(self.cfg.dd_recovery_step),
            int(self.cfg.consec_limit),
            float(self.cfg.loss_scale_min),
            float(self.cfg.recovery_rate),
            int(self.cfg.vol_window),
            float(target_daily_vol),
            float(self.cfg.vol_leverage_min),
            float(self.cfg.vol_leverage_max),
            int(self.cfg.cvar_window),
            float(self.cfg.cvar_alpha),
            float(self.cfg.cvar_limit_daily),
            float(self.cfg.cvar_scale_min)
        )

        port_rets_s = pd.Series(port_rets, index=prices.index, name="PortfolioReturn")
        eq = (1.0 + port_rets_s).cumprod()
        eq.name = "EquityCurve"

        lever_s = pd.Series(lever_series, index=prices.index, name="Leverage")
        turn_s = pd.Series(turnover_series, index=prices.index, name="Turnover")

        results = {
            "prices": prices,
            "returns": returns,
            "regime_features": regime_feats,
            "regime": regime,
            "factors_z": factor_z,
            "ic_end": pd.DataFrame(ic_end),
            "dynamic_factor_weights": dyn_w,
            "scores": scores,
            "target_weights": target_w_df,
            "rebalance_flags": pd.Series(rebalance_flags, index=prices.index, name="RebalanceFlag"),
            "leverage_caps": lev_caps,
            "portfolio_returns": port_rets_s,
            "equity_curve": eq,
            "leverage": lever_s,
            "turnover": turn_s
        }
        return results


# =========================
# Main / Report
# =========================

def main() -> None:
    cfg = ARESConfig()
    sys_ = ARESv11System(cfg, LOGGER)
    res = sys_.run()

    r = res["portfolio_returns"]
    eq = res["equity_curve"]

    sharpe = compute_sharpe(r)
    cagr = compute_cagr(eq)
    mdd = compute_mdd(eq)

    yr = yearly_performance(r)

    LOGGER.info("========== ARES v11 Backtest Summary ==========")
    LOGGER.info(f"Period: {eq.index.min().date()} -> {eq.index.max().date()}  (N={len(eq)})")
    LOGGER.info(f"Sharpe Ratio: {sharpe:.3f}")
    LOGGER.info(f"CAGR: {cagr*100:.2f}%")
    LOGGER.info(f"MDD: {mdd*100:.2f}%")
    LOGGER.info("Yearly Performance (Return, Sharpe):")
    with pd.option_context("display.max_rows", 50, "display.width", 140):
        print(yr)

    # Additional diagnostics
    lev = res["leverage"]
    turn = res["turnover"]
    LOGGER.info(f"Avg Leverage: {lev.mean():.3f} | Max Leverage: {lev.max():.3f}")
    LOGGER.info(f"Avg Daily Turnover: {turn.mean():.3f} | Rebalance Turnover (mean on rebalance days): "
                f"{turn[res['rebalance_flags'].astype(bool)].mean():.3f}")

    # Regime stats
    reg = res["regime"]
    reg_counts = reg.value_counts(normalize=True).sort_index()
    LOGGER.info("Regime Distribution:")
    print(reg_counts)

    # Save artifacts (optional)
    # eq.to_csv("ares_v11_equity.csv")
    # r.to_csv("ares_v11_returns.csv")
    # res["dynamic_factor_weights"].to_csv("ares_v11_dynamic_factor_weights.csv")


if __name__ == "__main__":
    main()