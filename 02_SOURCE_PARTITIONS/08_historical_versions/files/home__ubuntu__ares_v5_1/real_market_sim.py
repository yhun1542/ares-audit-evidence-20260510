"""
real_market_sim.py — 실데이터 기반 MarketSimulator (원본 인터페이스 100% 호환)
================================================================================
daily_bars_historical.parquet + regime_history.parquet + macro_daily.parquet 사용.

원본 MarketSimulator와 동일한 인터페이스:
  - .n_weeks
  - .regimes[wi]
  - .get_engine_returns(wi, engines)
  - .get_all_returns(wi)
  - .get_sleeve_returns(wi)
  - .rng (RandomState)

6개 엔진 수익률을 실데이터에서 구성:
  champion_momentum  = 상위 모멘텀 종목 포트폴리오 수익률
  defensive_carry    = 방어주(XLP,XLU,XLV) + 채권(TLT,IEF) 블렌드
  crash_responsive_trend = 추세추종(SPY 모멘텀) + 위기헤지(VIXY,GLD)
  sector_rotation    = 섹터 ETF 로테이션 수익률
  stat_arb_pca       = 시장중립 스프레드 (롱숏 페어)
  ml_ranker          = 팩터 기반 랭킹 포트폴리오

레짐 매핑 (실데이터 10종 → 토너먼트 5종):
  GOLDILOCKS, RISK_ON         → bull_strong
  NEUTRAL                     → bull_weak
  RATE_SPIKE, INFLATION_SHOCK, STAGFLATION, GROWTH_SCARE, DEFLATION → bear
  CRISIS, CREDIT_STRESS       → crisis
  (recovery는 crisis 직후 4주)
"""
from __future__ import annotations
from typing import Dict, List
import numpy as np
import pandas as pd
import os

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

# 실데이터 레짐 → 토너먼트 레짐 매핑
REGIME_MAP_REAL = {
    "GOLDILOCKS": "bull_strong",
    "RISK_ON": "bull_strong",
    "NEUTRAL": "bull_weak",
    "RATE_SPIKE": "bear",
    "INFLATION_SHOCK": "bear",
    "STAGFLATION": "bear",
    "GROWTH_SCARE": "bear",
    "DEFLATION": "bear",
    "CRISIS": "crisis",
    "CREDIT_STRESS": "crisis",
}

# 엔진별 구성 티커
MOMENTUM_TICKERS = ["NVDA", "META", "AAPL", "MSFT", "AMZN", "GOOGL", "TSLA"]
DEFENSIVE_TICKERS = ["XLP", "XLU", "XLV", "TLT", "IEF", "PG", "JNJ", "KO"]
TREND_TICKERS = ["SPY", "QQQ", "VIXY", "GLD"]
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP"]
STAT_ARB_LONG = ["V", "MA", "UNH", "COST", "HD"]
STAT_ARB_SHORT = ["SPY"]  # market neutral vs SPY
ML_TICKERS = ["AAPL", "MSFT", "NVDA", "JPM", "XOM", "BAC", "ABBV", "PFE", "AMZN", "META"]

ALL_NEEDED_TICKERS = sorted(set(
    MOMENTUM_TICKERS + DEFENSIVE_TICKERS + TREND_TICKERS +
    SECTOR_ETFS + STAT_ARB_LONG + STAT_ARB_SHORT + ML_TICKERS
))


class RealMarketSimulator:
    """실데이터 기반 MarketSimulator — 원본과 100% 인터페이스 호환."""

    def __init__(self, n_weeks: int = 163, seed: int = 42,
                 data_dir: str = "/home/ubuntu/alpha_prod/data"):
        self.n_weeks = n_weeks
        self.seed = seed
        self.rng = np.random.RandomState(seed)

        # Load data
        bars_path = os.path.join(data_dir, "silver", "daily_bars_historical.parquet")
        regime_path = os.path.join(data_dir, "gold", "regime_history.parquet")
        macro_path = os.path.join(data_dir, "silver", "macro_daily.parquet")

        self._load_data(bars_path, regime_path, macro_path)
        self._build_weekly_data()

    def _load_data(self, bars_path, regime_path, macro_path):
        """Load and prepare raw data."""
        # Price data — only needed tickers
        df = pd.read_parquet(bars_path)
        df["date"] = pd.to_datetime(df["date"])
        self._bars = df[df["ticker"].isin(ALL_NEEDED_TICKERS)].copy()
        self._bars = self._bars.sort_values(["ticker", "date"])

        # Regime data
        regime = pd.read_parquet(regime_path)
        regime["date"] = pd.to_datetime(regime["date"])
        self._regime_df = regime.sort_values("date")

        # Macro data (VIX etc)
        macro = pd.read_parquet(macro_path)
        macro["date"] = pd.to_datetime(macro["date"])
        self._macro_df = macro.sort_values("date")

    def _build_weekly_data(self):
        """Build weekly returns and regime sequence."""
        # Pivot to wide format: date × ticker → close
        pivot = self._bars.pivot_table(index="date", columns="ticker",
                                        values="close", aggfunc="last")
        pivot = pivot.sort_index()

        # Resample to weekly (Friday close)
        weekly_close = pivot.resample("W-FRI").last()
        weekly_close = weekly_close.dropna(how="all")

        # Weekly returns
        weekly_ret = weekly_close.pct_change()
        weekly_ret = weekly_ret.iloc[1:]  # drop first NaN row

        # Use last n_weeks
        if len(weekly_ret) > self.n_weeks:
            weekly_ret = weekly_ret.iloc[-self.n_weeks:]
            weekly_close = weekly_close.iloc[-(self.n_weeks+1):]

        self._weekly_ret = weekly_ret
        self._weekly_dates = weekly_ret.index.tolist()
        actual_weeks = len(self._weekly_dates)
        if actual_weeks < self.n_weeks:
            self.n_weeks = actual_weeks

        # Build regime sequence
        self.regimes = []
        regime_daily = self._regime_df.set_index("date")["regime"]
        prev_regime = "bull_weak"
        crisis_countdown = 0

        for wi, wdate in enumerate(self._weekly_dates):
            # Find regime for this week (closest date <= wdate)
            mask = regime_daily.index <= wdate
            if mask.any():
                raw_regime = regime_daily[mask].iloc[-1]
            else:
                raw_regime = "NEUTRAL"

            # Map to tournament regime
            mapped = REGIME_MAP_REAL.get(raw_regime, "bull_weak")

            # Recovery detection: 4 weeks after crisis
            if mapped == "crisis":
                crisis_countdown = 4
            elif crisis_countdown > 0:
                mapped = "recovery"
                crisis_countdown -= 1

            self.regimes.append(mapped)
            prev_regime = mapped

        # Precompute engine returns for each week
        self._all_returns = {}
        for wi in range(self.n_weeks):
            self._all_returns[wi] = self._compute_engine_returns(wi)

    def _compute_engine_returns(self, wi: int) -> Dict[str, float]:
        """Compute 6 engine returns from real data for week wi."""
        ret_row = self._weekly_ret.iloc[wi]
        regime = self.regimes[wi]

        def _safe_mean(tickers):
            vals = [ret_row.get(t, np.nan) for t in tickers]
            vals = [v for v in vals if not np.isnan(v)]
            return float(np.mean(vals)) if vals else 0.0

        def _safe_get(ticker):
            v = ret_row.get(ticker, np.nan)
            return float(v) if not np.isnan(v) else 0.0

        # 1. champion_momentum: top momentum stocks equal-weighted
        mom_ret = _safe_mean(MOMENTUM_TICKERS)

        # 2. defensive_carry: defensive stocks + bonds blend
        def_stocks = _safe_mean(["XLP", "XLU", "XLV", "PG", "JNJ", "KO"])
        def_bonds = _safe_mean(["TLT", "IEF"])
        defensive_ret = 0.5 * def_stocks + 0.5 * def_bonds

        # 3. crash_responsive_trend: trend following + crisis hedge
        spy_ret = _safe_get("SPY")
        vixy_ret = _safe_get("VIXY")
        gld_ret = _safe_get("GLD")
        if regime in ("crisis", "bear"):
            # In crisis: heavy VIXY + GLD, light SPY
            trend_ret = 0.2 * spy_ret + 0.4 * vixy_ret + 0.4 * gld_ret
        else:
            # Normal: follow SPY trend, small GLD hedge
            trend_ret = 0.7 * spy_ret + 0.1 * vixy_ret + 0.2 * gld_ret

        # 4. sector_rotation: best 3 of 7 sector ETFs (hindsight-free: use prev week momentum)
        sector_rets = {etf: _safe_get(etf) for etf in SECTOR_ETFS}
        if wi > 0:
            prev_ret = self._weekly_ret.iloc[wi-1]
            prev_sector = {etf: float(prev_ret.get(etf, 0)) for etf in SECTOR_ETFS}
            # Pick top 3 by previous week return
            top3 = sorted(prev_sector, key=prev_sector.get, reverse=True)[:3]
        else:
            top3 = SECTOR_ETFS[:3]
        sector_rot_ret = np.mean([sector_rets.get(e, 0.0) for e in top3])

        # 5. stat_arb_pca: market-neutral long/short
        long_ret = _safe_mean(STAT_ARB_LONG)
        short_ret = _safe_get("SPY")
        stat_arb_ret = long_ret - short_ret  # dollar-neutral

        # 6. ml_ranker: factor-ranked portfolio (use momentum + mean-reversion blend)
        ml_rets = []
        for t in ML_TICKERS:
            r = _safe_get(t)
            ml_rets.append(r)
        if ml_rets:
            # Rank by previous week, equal-weight top half
            if wi > 0:
                prev_ret = self._weekly_ret.iloc[wi-1]
                scored = [(t, float(prev_ret.get(t, 0))) for t in ML_TICKERS]
                scored.sort(key=lambda x: x[1], reverse=True)
                top_half = [t for t, _ in scored[:len(scored)//2]]
                ml_ret = _safe_mean(top_half) if top_half else _safe_mean(ML_TICKERS)
            else:
                ml_ret = _safe_mean(ML_TICKERS)
        else:
            ml_ret = 0.0

        # Add small noise to prevent identical returns across engines
        noise_scale = 0.0005
        returns = {
            "champion_momentum": mom_ret + self.rng.randn() * noise_scale,
            "defensive_carry": defensive_ret + self.rng.randn() * noise_scale,
            "crash_responsive_trend": trend_ret + self.rng.randn() * noise_scale,
            "sector_rotation": float(sector_rot_ret) + self.rng.randn() * noise_scale,
            "stat_arb_pca": stat_arb_ret + self.rng.randn() * noise_scale,
            "ml_ranker": ml_ret + self.rng.randn() * noise_scale,
        }
        return returns

    def get_engine_returns(self, wi: int, engines: List[str]) -> Dict[str, float]:
        """Get returns for specified engines at week wi."""
        return {e: self._all_returns[wi].get(e, 0.0) for e in engines}

    def get_all_returns(self, wi: int) -> Dict[str, float]:
        """Get all 6 engine returns at week wi."""
        return self._all_returns[wi]

    def get_sleeve_returns(self, wi: int) -> Dict[str, float]:
        """v9.x용: CORE/GROWTH/DEFENSIVE 슬리브별 평균 수익률."""
        all_r = self._all_returns[wi]
        sleeve_ret = {}
        for sleeve, engines in SLEEVE_MAP.items():
            vals = [all_r[e] for e in engines if e in all_r]
            sleeve_ret[sleeve] = float(np.mean(vals)) if vals else 0.0
        return sleeve_ret


# Multi-seed variant: uses different noise seeds but same market data
class RealMarketSimulatorMultiSeed(RealMarketSimulator):
    """Phase 4용: 동일 시장 데이터 + 다른 noise seed."""

    def __init__(self, n_weeks: int = 163, seed: int = 42,
                 data_dir: str = "/home/ubuntu/alpha_prod/data"):
        # Call parent but override seed for noise
        super().__init__(n_weeks=n_weeks, seed=seed, data_dir=data_dir)
        # Re-randomize noise with different seed
        self.rng = np.random.RandomState(seed)
        for wi in range(self.n_weeks):
            base = self._all_returns[wi].copy()
            noise_scale = 0.001  # slightly larger noise for robustness testing
            for e in ENGINE_NAMES_ALL:
                base[e] = base[e] + self.rng.randn() * noise_scale
            self._all_returns[wi] = base
