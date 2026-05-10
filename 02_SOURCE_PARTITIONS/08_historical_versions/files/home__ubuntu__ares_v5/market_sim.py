"""
market_sim.py — 합성 시장 데이터 생성기 (Family 공통)
=====================================================
ENGINE_PROFILES / ENGINE_CORR / REGIME_TRANSITION은
backtest_walkforward_v12_3_1_fixed.py에서 추출한 실측 기반 값이다.
"""
from __future__ import annotations
from typing import Dict, List
import numpy as np

ENGINE_NAMES_ALL = [
    "champion_momentum", "defensive_carry", "crash_responsive_trend",
    "sector_rotation", "stat_arb_pca", "ml_ranker",
]

REGIME_LIST = ["bull_strong", "bull_weak", "bear", "crisis", "recovery"]

REGIME_TRANSITION = np.array([
    [0.70, 0.20, 0.05, 0.01, 0.04],
    [0.15, 0.55, 0.15, 0.05, 0.10],
    [0.02, 0.10, 0.55, 0.25, 0.08],
    [0.01, 0.04, 0.20, 0.55, 0.20],
    [0.20, 0.30, 0.05, 0.02, 0.43],
])

ENGINE_PROFILES = {
    "champion_momentum":      {"base_sharpe": 0.9, "ann_vol": 0.18, "regime_alpha": {"bull_strong": 0.06, "bull_weak": 0.03, "bear": -0.04, "crisis": -0.08, "recovery": 0.05}},
    "defensive_carry":        {"base_sharpe": 0.5, "ann_vol": 0.08, "regime_alpha": {"bull_strong": 0.01, "bull_weak": 0.02, "bear": 0.03, "crisis": 0.04, "recovery": 0.01}},
    "crash_responsive_trend": {"base_sharpe": 0.7, "ann_vol": 0.22, "regime_alpha": {"bull_strong": 0.02, "bull_weak": 0.02, "bear": 0.06, "crisis": 0.10, "recovery": 0.04}},
    "sector_rotation":        {"base_sharpe": 0.6, "ann_vol": 0.14, "regime_alpha": {"bull_strong": 0.03, "bull_weak": 0.02, "bear": -0.02, "crisis": -0.03, "recovery": 0.03}},
    "stat_arb_pca":           {"base_sharpe": 0.8, "ann_vol": 0.10, "regime_alpha": {"bull_strong": 0.02, "bull_weak": 0.02, "bear": 0.01, "crisis": -0.02, "recovery": 0.02}},
    "ml_ranker":              {"base_sharpe": 0.7, "ann_vol": 0.15, "regime_alpha": {"bull_strong": 0.04, "bull_weak": 0.03, "bear": -0.01, "crisis": 0.05, "recovery": 0.03}},
}

ENGINE_CORR = np.array([
    [1.00, 0.10, 0.25, 0.40, 0.15, 0.30],
    [0.10, 1.00, 0.05, 0.10, 0.20, 0.10],
    [0.25, 0.05, 1.00, 0.15, 0.10, 0.20],
    [0.40, 0.10, 0.15, 1.00, 0.20, 0.35],
    [0.15, 0.20, 0.10, 0.20, 1.00, 0.25],
    [0.30, 0.10, 0.20, 0.35, 0.25, 1.00],
])

# v9.x 계열에서 사용하는 "슬리브" 수익률 생성용 프로필
# CORE ≈ 고성장 (momentum, ml_ranker 계열)
# GROWTH ≈ 중간 (sector_rotation, crash_responsive_trend 계열)
# DEFENSIVE ≈ 방어 (defensive_carry, stat_arb_pca 계열)
SLEEVE_MAP = {
    "CORE": ["champion_momentum", "ml_ranker"],
    "GROWTH": ["sector_rotation", "crash_responsive_trend"],
    "DEFENSIVE": ["defensive_carry", "stat_arb_pca"],
}


class MarketSimulator:
    """주어진 seed로 n_weeks 분량의 레짐 + 엔진 수익률 생성."""

    def __init__(self, n_weeks: int = 163, seed: int = 42):
        self.n_weeks = n_weeks
        self.seed = seed
        self.rng = np.random.RandomState(seed)
        self.regimes = self._build_regimes()
        self._all_returns = self._precompute()

    def _build_regimes(self) -> List[str]:
        regimes = []
        state = 0
        for _ in range(self.n_weeks):
            regimes.append(REGIME_LIST[state])
            state = self.rng.choice(5, p=REGIME_TRANSITION[state])
        return regimes

    def _precompute(self) -> Dict[int, Dict[str, float]]:
        n = len(ENGINE_NAMES_ALL)
        all_ret = {}
        for wi in range(self.n_weeks):
            regime = self.regimes[wi]
            mu = np.zeros(n)
            vols = np.zeros(n)
            for j, e in enumerate(ENGINE_NAMES_ALL):
                prof = ENGINE_PROFILES[e]
                mu[j] = (prof["base_sharpe"] * prof["ann_vol"] + prof["regime_alpha"].get(regime, 0.0)) / 52.0
                vols[j] = prof["ann_vol"] / np.sqrt(52)
            D = np.diag(vols)
            Cov = D @ ENGINE_CORR @ D + np.eye(n) * 1e-10
            L = np.linalg.cholesky(Cov)
            raw = mu + L @ self.rng.randn(n)
            if self.rng.rand() < 0.05:
                idx = self.rng.randint(n)
                raw[idx] *= self.rng.choice([-2.5, -1.8, 2.0, 1.5])
            all_ret[wi] = {ENGINE_NAMES_ALL[j]: float(raw[j]) for j in range(n)}
        return all_ret

    def get_engine_returns(self, wi: int, engines: List[str]) -> Dict[str, float]:
        return {e: self._all_returns[wi].get(e, 0.0) for e in engines}

    def get_all_returns(self, wi: int) -> Dict[str, float]:
        return self._all_returns[wi]

    def get_sleeve_returns(self, wi: int) -> Dict[str, float]:
        """v9.x용: CORE/GROWTH/DEFENSIVE 슬리브별 평균 수익률."""
        all_r = self._all_returns[wi]
        sleeve_ret = {}
        for sleeve, engines in SLEEVE_MAP.items():
            vals = [all_r[e] for e in engines if e in all_r]
            sleeve_ret[sleeve] = float(np.mean(vals)) if vals else 0.0
        return sleeve_ret
