#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
ARES v7.0 ULTIMATE — 4-AI Consensus Production Engine
================================================================================

[통합 계보]
  ARES Nexus v4.37 (9-Factor + sector_rot + GPU/CPU)
  + AUB V8 Champion Package (EVTRiskManager, CVaRTargetingOverlay, EntropyRegimeFilter)
  + 4ai_modules (CrossAssetMomentumDivergence, AlphaScorerV5)
  + nextgen2 Orchestrator (Production-grade 실행 엔진)
  + WalkForward v12.3.1 (백테스트 프레임워크)

[4-AI 합의 설계 원칙]
  - Claude (구현 전문가): 프로덕션 레벨 에러 처리, 폴백, 스무딩 강화
  - Grok (버그 검증): 엣지 케이스 분석, Cornish-Fisher 폴백, 모듈 통합 우선순위
  - Manus-GPT (전략 아키텍트): 6-Layer 파이프라인 아키텍처, 모듈 간 의존성 정의
  - Manus-Gemini (기준 감독): 룩어헤드 바이어스 방지, 과적합 방지, 성능 목표 설정

[v7.0 신규 통합 모듈]
  1. EVTRiskManager — 극단값 이론(GPD) 기반 꼬리 리스크 + Cornish-Fisher 폴백
  2. CVaRTargetingOverlay — CVaR 기반 노출도 조정 + VIX 게이팅 + 레짐 게이팅
  3. EntropyRegimeFilter — Shannon + Permutation 엔트로피 레짐 필터 (적응형 임계값)
  4. CrossAssetMomentumDivergence — 크로스에셋 모멘텀 다이버전스 (군집 효과 방지)
  5. AlphaScorerV5 — IC/IR 앙상블 기반 레짐 조건부 멀티팩터 스코어러
  6. sector_rot 팩터 — 헬스케어/필수소비재 vs 성장주 상대강도 (v4.37에서 이식)
  7. ICIREnsembleV8 — IC/IR 앙상블 v8 (v4.37에서 이식)
  8. WalkForwardBacktest v12 — 최신 WF 백테스트 프레임워크 통합

[성능 목표]
  - Walk-Forward OOS Sharpe > 2.5
  - Max Drawdown < -12%
  - 연간 수익률 > 30%

[아키텍처: 6-Layer Pipeline]
  L1: DataHandlerV70 — DB 로딩, 정제, 표준화
  L2: SystemicRiskEngineV70 — EVT + Entropy + Hybrid 레짐 통합
  L3: AlphaSuiteV70 — 9팩터 + AlphaScorerV5 + CrossAsset + sector_rot
  L4: PortfolioConstructorV70 — HRP + QP 최적화
  L5: PreTradeControllerV70 — CVaR + VolTarget + Almgren-Chriss
  L6: OrchestratorV70 — KIS API 연동 실행

Date: 2026-03-01
Author: Manus AI (4-AI Collaboration: GPT, Claude, Gemini, Grok)
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import time
import warnings
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster.hierarchy import linkage
from scipy.optimize import minimize
from scipy.sparse.csgraph import minimum_spanning_tree
from scipy.sparse import csr_matrix
from scipy.stats import spearmanr

warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────────────────────────────────────
# 선택적 임포트 (GPU 가속)
# ─────────────────────────────────────────────────────────────────────────────
try:
    import cupy as cp
    CUPY_AVAILABLE = True
except ImportError:
    CUPY_AVAILABLE = False

try:
    from numba import njit, prange
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False

try:
    from sklearn.covariance import LedoitWolf
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ARES_V70')


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 0: 공통 유틸리티 함수
# ═════════════════════════════════════════════════════════════════════════════

def nan_safe_scores(arr: np.ndarray, name: str = "") -> np.ndarray:
    """NaN/Inf를 0으로 대체하고 z-score 정규화 (클리핑 포함)"""
    arr = np.asarray(arr, dtype=np.float64)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    std = np.std(arr)
    if std < 1e-12:
        return np.zeros_like(arr)
    z = (arr - np.mean(arr)) / std
    return np.clip(z, -3.0, 3.0)


def fast_rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
    """빠른 롤링 표준편차 (Numba 없이도 동작)"""
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        result[i] = np.std(arr[i - window + 1:i + 1])
    return result


def fast_zscore(arr: np.ndarray, window: int, min_periods: int = 20) -> np.ndarray:
    """빠른 롤링 z-score"""
    result = np.full(len(arr), np.nan)
    for i in range(len(arr)):
        start = max(0, i - window + 1)
        sub = arr[start:i + 1]
        valid = sub[np.isfinite(sub)]
        if len(valid) >= min_periods:
            std = np.std(valid)
            if std > 1e-12:
                result[i] = (arr[i] - np.mean(valid)) / std
    return result


def fast_max_drawdown(returns: np.ndarray) -> float:
    """최대 낙폭 계산"""
    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - peak) / (peak + 1e-12)
    return float(np.min(drawdown))


def robust_zscore(x: np.ndarray, clip: float = 3.0) -> np.ndarray:
    """강건한 z-score (NaN 안전)"""
    x = np.asarray(x, dtype=float)
    m = np.nanmean(x)
    s = np.nanstd(x)
    if not np.isfinite(s) or s < 1e-12:
        return np.zeros_like(x)
    z = (x - m) / s
    z = np.clip(z, -clip, clip)
    z[~np.isfinite(z)] = 0.0
    return z


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 1: 데이터 핸들러
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """단일 시점의 시장 데이터 스냅샷"""
    date: Any
    prices: np.ndarray        # (N,) 현재 가격
    returns: np.ndarray       # (T, N) 과거 수익률
    volumes: Optional[np.ndarray]   # (T, N) 거래량
    opens: Optional[np.ndarray]     # (T, N) 시가
    vix: float = 20.0
    t10y2y: float = 1.0
    dgs2: float = 2.0
    symbols: List[str] = field(default_factory=list)


class DataHandlerV70:
    """
    v7.0 데이터 핸들러
    - SQLite DB에서 OHLCV + VIX + 매크로 데이터 로딩
    - 결측치 처리, 커버리지 필터링
    - 룩어헤드 바이어스 방지 (시점 t에서 t-1까지의 데이터만 제공)
    """

    def __init__(self, db_path: str, min_assets: int = 35, min_coverage: float = 0.5):
        self.db_path = db_path
        self.min_assets = min_assets
        self.min_coverage = min_coverage

        # 로드된 데이터
        self.symbols: List[str] = []
        self.dates: List[Any] = []
        self.prices: np.ndarray = None
        self.returns: np.ndarray = None
        self.opens: np.ndarray = None
        self.volumes: np.ndarray = None
        self.vix: np.ndarray = None
        self.t10y2y: np.ndarray = None
        self.dgs2: np.ndarray = None
        self.mkt_ret: np.ndarray = None

    # 챔피언 유니버스 (v6.7.0 기반 + 확장)
    CHAMPION_UNIVERSE = [
        'AAPL','ABBV','ADBE','AMD','AMGN','AMZN','ASML','AVGO','AXP',
        'BA','BAC','BIL','CMCSA','COP','COST','CRM','CRWD','CSCO',
        'CVX','DIS','GILD','GOOGL','GS','HD','IBM','INTC','JNJ','JPM',
        'KO','LLY','LMT','LOW','MA','MCD','META','MRK','MS','MSFT',
        'MU','NEE','NFLX','NKE','NVDA','OKTA','ORCL','PEP','PFE','PG',
        'PYPL','QCOM','QQQ','REGN','SBUX','SPY','TSLA','TXN','UNH',
        'UNP','V','VRTX','VZ','WFC','WMT','XOM',
        # 헤지/방어
        'SH','SDS','SQQQ','VIXY','TLT','SHV','BRK.B',
    ]

    def load(self, universe: Optional[List[str]] = None) -> 'DataHandlerV70':
        """DB에서 전체 데이터 로딩"""
        target_universe = universe or self.CHAMPION_UNIVERSE
        logger.info(f"[DataHandlerV70] Loading from {self.db_path} | Universe: {len(target_universe)} symbols")
        conn = sqlite3.connect(self.db_path, timeout=120)

        # 유니버스 플레이스홀더 생성
        placeholders = ','.join(['?' for _ in target_universe])

        try:
            df = pd.read_sql_query(f"""
                SELECT date, symbol, open, close, volume, adjusted_close
                FROM daily_ohlcv
                WHERE symbol IN ({placeholders})
                ORDER BY date, symbol
            """, conn, params=target_universe)

            df['date'] = pd.to_datetime(df['date'], format='mixed')
            df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')

            # adjusted_close 우선 사용, 없으면 close 폴백
            price_col = 'adjusted_close' if 'adjusted_close' in df.columns and df['adjusted_close'].notna().mean() > 0.3 else 'close'
            prices_pivot = df.pivot(index='date', columns='symbol', values=price_col).sort_index()
            opens_pivot = df.pivot(index='date', columns='symbol', values='open').sort_index()
            vol_pivot = df.pivot(index='date', columns='symbol', values='volume').sort_index()

            # 커버리지 필터: 최소 35개 자산이 있는 시점부터 시작
            coverage = prices_pivot.notna().sum(axis=1)
            for date, cov in coverage.items():
                if cov >= self.min_assets:
                    prices_pivot = prices_pivot.loc[date:]
                    opens_pivot = opens_pivot.loc[date:]
                    vol_pivot = vol_pivot.loc[date:]
                    break

            # 자산 필터: 50% 이상 데이터가 있는 자산만 유지
            data_ratio = prices_pivot.notna().mean()
            keep = data_ratio[data_ratio >= self.min_coverage].index.tolist()
            if len(keep) < self.min_assets:
                keep = data_ratio.nlargest(self.min_assets).index.tolist()

            prices_pivot = prices_pivot[keep].ffill(limit=3)
            opens_pivot = opens_pivot[keep].ffill(limit=3) if set(keep).issubset(opens_pivot.columns) else prices_pivot.copy()
            vol_pivot = vol_pivot[keep].ffill(limit=3) if set(keep).issubset(vol_pivot.columns) else None

            self.symbols = list(prices_pivot.columns)
            returns_df = prices_pivot.pct_change()

            self.dates = returns_df.index[1:].tolist()
            self.prices = prices_pivot.values[1:].astype(np.float64)
            self.returns = np.nan_to_num(returns_df.values[1:].astype(np.float64), nan=0.0)
            self.opens = opens_pivot.values[1:].astype(np.float64)
            self.volumes = vol_pivot.values[1:].astype(np.float64) if vol_pivot is not None else None
            self.mkt_ret = np.nanmean(self.returns, axis=1)

            # VIX 로딩
            try:
                vix_df = pd.read_sql_query("SELECT date, close as vix FROM vix ORDER BY date", conn)
                vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
                vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
                self.vix = vix_df['vix'].reindex(returns_df.index[1:]).ffill().fillna(20.0).values.astype(np.float64)
            except Exception:
                self.vix = np.full(len(self.returns), 20.0)

            # 매크로 데이터 로딩
            self.t10y2y = np.full(len(self.returns), 1.0)
            self.dgs2 = np.full(len(self.returns), 2.0)
            try:
                macro_df = pd.read_sql_query(
                    "SELECT date, series_id, value FROM fred_macro_daily WHERE series_id IN ('T10Y2Y','DGS2') ORDER BY date",
                    conn)
                macro_df['date'] = pd.to_datetime(macro_df['date'], format='mixed')
                macro_pivot = macro_df.pivot(index='date', columns='series_id', values='value')
                macro_pivot = macro_pivot.reindex(returns_df.index[1:]).ffill().bfill()
                if 'T10Y2Y' in macro_pivot.columns:
                    self.t10y2y = np.nan_to_num(macro_pivot['T10Y2Y'].values.astype(np.float64), nan=1.0)
                if 'DGS2' in macro_pivot.columns:
                    self.dgs2 = np.nan_to_num(macro_pivot['DGS2'].values.astype(np.float64), nan=2.0)
            except Exception as e:
                logger.warning(f"[DataHandlerV70] 매크로 데이터 로드 실패: {e}")

        finally:
            conn.close()

        logger.info(f"[DataHandlerV70] Loaded: {len(self.returns)} days, {len(self.symbols)} assets [{self.dates[0]}~{self.dates[-1]}]")
        return self

    def get_snapshot(self, t: int) -> MarketSnapshot:
        """시점 t의 스냅샷 반환 (t-1까지의 데이터 포함)"""
        return MarketSnapshot(
            date=self.dates[t],
            prices=self.prices[t],
            returns=self.returns[:t],
            volumes=self.volumes[:t] if self.volumes is not None else None,
            opens=self.opens[:t] if self.opens is not None else None,
            vix=float(self.vix[t]),
            t10y2y=float(self.t10y2y[t]),
            dgs2=float(self.dgs2[t]),
            symbols=self.symbols
        )


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 2: SYSTEMIC RISK ENGINE
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# 2-A: 레짐 분류 (v4.37 기반 + v7.0 확장)
# ─────────────────────────────────────────────────────────────────────────────

class RegimeLabel(IntEnum):
    CRISIS = 0
    BEAR = 1
    NORMAL = 2
    BULL = 3
    STRONG_BULL = 4
    INF_SHOCK = 5
    RATE_SHOCK = 6


REGIME_NAMES = {
    0: "CRISIS", 1: "BEAR", 2: "NORMAL", 3: "BULL",
    4: "STRONG_BULL", 5: "INF_SHOCK", 6: "RATE_SHOCK"
}


class RegimeDetectorV70:
    """
    v7.0 레짐 탐지기 (v4.37 기반 + 엔트로피 필터 통합)
    7개 레짐: CRISIS/BEAR/NORMAL/BULL/STRONG_BULL/INF_SHOCK/RATE_SHOCK
    """

    def __init__(self):
        self._regime_history: List[int] = []

    def detect(self, t: int, features: Dict[str, np.ndarray]) -> int:
        """
        시점 t의 레짐 탐지
        features: vix_z, vol_z, mom_z, dd_20, t10y2y, dgs2_change_20d, dgs2_change_5d, dgs2_level
        """
        vix_z = float(features['vix_z'][t]) if t < len(features['vix_z']) else 0.0
        vol_z = float(features['vol_z'][t]) if t < len(features['vol_z']) else 0.0
        mom_z = float(features['mom_z'][t]) if t < len(features['mom_z']) else 0.0
        dd_20 = float(features['dd_20'][t]) if t < len(features['dd_20']) else 0.0
        t10y2y = float(features['t10y2y'][t]) if t < len(features['t10y2y']) else 1.0
        dgs2_chg20 = float(features['dgs2_change_20d'][t]) if t < len(features['dgs2_change_20d']) else 0.0
        dgs2_chg5 = float(features['dgs2_change_5d'][t]) if t < len(features['dgs2_change_5d']) else 0.0
        dgs2_level = float(features['dgs2_level'][t]) if t < len(features['dgs2_level']) else 2.0

        # RATE_SHOCK: 금리 급등 (20일 +75bp 이상 또는 5일 +30bp + 레벨 4% 이상)
        if dgs2_chg20 >= 75 or (dgs2_chg5 >= 30 and dgs2_level >= 4.0):
            regime = int(RegimeLabel.RATE_SHOCK)
        # CRISIS: VIX 극단 + 급락
        elif vix_z >= 2.5 and dd_20 <= -0.08:
            regime = int(RegimeLabel.CRISIS)
        # BEAR: VIX 상승 + 하락 모멘텀
        elif vix_z >= 1.5 or (mom_z <= -1.5 and dd_20 <= -0.04):
            regime = int(RegimeLabel.BEAR)
        # INF_SHOCK: 수익률 곡선 역전 + 금리 상승
        elif t10y2y <= -0.5 and dgs2_chg20 >= 30:
            regime = int(RegimeLabel.INF_SHOCK)
        # STRONG_BULL: 강한 상승 모멘텀 + 낮은 변동성
        elif mom_z >= 1.5 and vol_z <= -0.5:
            regime = int(RegimeLabel.STRONG_BULL)
        # BULL: 상승 모멘텀
        elif mom_z >= 0.5:
            regime = int(RegimeLabel.BULL)
        # NORMAL: 기본
        else:
            regime = int(RegimeLabel.NORMAL)

        self._regime_history.append(regime)
        return regime

    def get_recent_regime_counts(self, window: int = 20) -> Dict[int, int]:
        """최근 window 기간의 레짐 분포"""
        recent = self._regime_history[-window:] if len(self._regime_history) >= window else self._regime_history
        counts = {i: 0 for i in range(7)}
        for r in recent:
            counts[r] = counts.get(r, 0) + 1
        return counts


# ─────────────────────────────────────────────────────────────────────────────
# 2-B: EVT 리스크 매니저 (EC2 실제 코드 기반 + Cornish-Fisher 폴백)
# ─────────────────────────────────────────────────────────────────────────────

class TailRegime(Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    EXTREME = "extreme"


@dataclass
class EVTConfig:
    lookback: int = 252
    threshold_percentile: float = 0.05
    var_confidence: float = 0.99
    es_confidence: float = 0.975
    min_tail_obs: int = 20
    update_freq: str = 'weekly'
    multiplier_normal: float = 1.0
    multiplier_elevated: float = 0.85
    multiplier_high: float = 0.70
    multiplier_extreme: float = 0.50


class EVTRiskManagerV70:
    """
    극단값 이론 기반 리스크 관리자 (v7.0 통합판)
    - GPD 피팅으로 꼬리 모델링
    - Cornish-Fisher 폴백 (GPD 실패 시)
    - 동적 VaR/ES 계산
    - 꼬리 레짐 분류 및 시그널 멀티플라이어 생성
    """

    def __init__(self, config: Optional[EVTConfig] = None):
        self.config = config or EVTConfig()
        self._gpd_params: Dict[int, Tuple[float, float]] = {}
        self._last_update_t: int = -999
        self._tail_probs: Dict[int, float] = {}

    def compute_multiplier(self, returns_1d: np.ndarray, t: int) -> float:
        """
        시점 t에서의 노출도 멀티플라이어 계산
        returns_1d: 포트폴리오 일별 수익률 (1D array)
        """
        if t < self.config.lookback:
            return 1.0

        recent = returns_1d[max(0, t - self.config.lookback):t]
        recent = recent[np.isfinite(recent)]

        if len(recent) < self.config.min_tail_obs * 2:
            return 1.0

        # GPD 피팅 업데이트 여부 확인
        if self._should_update(t):
            self._fit_gpd(recent, t)

        # 꼬리 확률 계산
        tail_prob = self._compute_tail_probability(recent, t)

        # 레짐 분류 및 멀티플라이어 반환
        regime = self._classify_regime(tail_prob)
        return self._get_multiplier(regime)

    def _should_update(self, t: int) -> bool:
        if self._last_update_t < 0:
            return True
        freq = self.config.update_freq
        if freq == 'daily':
            return True
        elif freq == 'weekly':
            return (t - self._last_update_t) >= 5
        elif freq == 'monthly':
            return (t - self._last_update_t) >= 21
        return True

    def _fit_gpd(self, returns: np.ndarray, t: int):
        """Generalized Pareto Distribution 피팅 (Cornish-Fisher 폴백 포함)"""
        losses = -returns[returns < 0]
        if len(losses) < self.config.min_tail_obs:
            self._gpd_params[t] = (0.0, float(np.std(returns)) + 1e-8)
            self._last_update_t = t
            return

        threshold = np.percentile(losses, (1 - self.config.threshold_percentile) * 100)
        excesses = losses[losses > threshold] - threshold

        if len(excesses) < self.config.min_tail_obs:
            # Cornish-Fisher 폴백
            sigma = float(np.std(returns))
            self._gpd_params[t] = (0.0, sigma + 1e-8)
            self._last_update_t = t
            return

        try:
            shape, loc, scale = stats.genpareto.fit(excesses, floc=0)
            if not np.isfinite(shape) or not np.isfinite(scale) or scale <= 0:
                raise ValueError("Invalid GPD params")
            self._gpd_params[t] = (float(shape), float(scale))
        except Exception:
            # Cornish-Fisher 폴백: 왜도/첨도 기반 VaR 조정
            sigma = float(np.std(returns))
            skew = float(stats.skew(returns)) if len(returns) >= 4 else 0.0
            kurt = float(stats.kurtosis(returns)) if len(returns) >= 4 else 0.0
            # Cornish-Fisher 보정 계수
            cf_adj = 1.0 + (skew / 6.0) * (-1.645) + (kurt / 24.0) * ((-1.645)**2 - 1)
            cf_adj = float(np.clip(cf_adj, 0.5, 3.0))
            self._gpd_params[t] = (0.0, sigma * cf_adj + 1e-8)

        self._last_update_t = t

    def _compute_tail_probability(self, returns: np.ndarray, t: int) -> float:
        """현재 시장 상태의 꼬리 확률 계산"""
        recent_20 = returns[-20:] if len(returns) >= 20 else returns
        threshold = np.percentile(returns, self.config.threshold_percentile * 100)
        extreme_count = float(np.sum(recent_20 < threshold))
        empirical_prob = extreme_count / len(recent_20)

        gpd_prob = 0.01
        if t in self._gpd_params:
            shape, scale = self._gpd_params[t]
            if scale > 0:
                current_vol = float(np.std(recent_20)) if len(recent_20) >= 3 else 0.01
                try:
                    gpd_prob = float(1 - stats.genpareto.cdf(current_vol * 2, shape, scale=scale))
                    gpd_prob = float(np.clip(gpd_prob, 0.0, 1.0))
                except Exception:
                    gpd_prob = 0.01

        tail_prob = 0.6 * empirical_prob + 0.4 * gpd_prob
        self._tail_probs[t] = tail_prob
        return float(np.clip(tail_prob, 0.0, 1.0))

    def _classify_regime(self, tail_prob: float) -> TailRegime:
        if tail_prob >= 0.05:
            return TailRegime.EXTREME
        elif tail_prob >= 0.03:
            return TailRegime.HIGH
        elif tail_prob >= 0.01:
            return TailRegime.ELEVATED
        else:
            return TailRegime.NORMAL

    def _get_multiplier(self, regime: TailRegime) -> float:
        return {
            TailRegime.NORMAL: self.config.multiplier_normal,
            TailRegime.ELEVATED: self.config.multiplier_elevated,
            TailRegime.HIGH: self.config.multiplier_high,
            TailRegime.EXTREME: self.config.multiplier_extreme,
        }.get(regime, 1.0)

    def get_var(self, returns: np.ndarray, confidence: float = 0.99) -> float:
        """VaR 계산"""
        valid = returns[np.isfinite(returns)]
        if len(valid) < 10:
            return 0.02
        return float(-np.percentile(valid, (1 - confidence) * 100))

    def get_es(self, returns: np.ndarray, confidence: float = 0.975) -> float:
        """Expected Shortfall 계산"""
        var = self.get_var(returns, confidence)
        tail = returns[returns < -var]
        if len(tail) == 0:
            return var * 1.2
        return float(-np.mean(tail))


# ─────────────────────────────────────────────────────────────────────────────
# 2-C: 엔트로피 레짐 필터 (EC2 실제 코드 기반 + 적응형 임계값)
# ─────────────────────────────────────────────────────────────────────────────

class EntropyRegimeState(Enum):
    LOW_ENTROPY = "low_entropy"
    MEDIUM_ENTROPY = "medium_entropy"
    HIGH_ENTROPY = "high_entropy"
    EXTREME_ENTROPY = "extreme_entropy"


@dataclass
class EntropyConfig:
    shannon_window: int = 20
    shannon_bins: int = 10
    perm_window: int = 20
    perm_order: int = 3
    perm_delay: int = 1
    shannon_weight: float = 0.6
    perm_weight: float = 0.4
    low_threshold_pct: float = 25.0
    high_threshold_pct: float = 75.0
    extreme_threshold_pct: float = 90.0
    low_entropy_multiplier: float = 1.2
    medium_entropy_multiplier: float = 1.0
    high_entropy_multiplier: float = 0.6
    extreme_entropy_multiplier: float = 0.3
    smoothing_window: int = 5
    adaptive_lookback: int = 252


class EntropyRegimeFilterV70:
    """
    엔트로피 기반 시장 레짐 필터 (v7.0 통합판)
    - Shannon 엔트로피 (분포 불확실성)
    - Permutation 엔트로피 (순서 패턴 복잡도)
    - 적응형 임계값 (과거 백분위수 기반)
    - 룩어헤드 방지: t일 신호는 t-1까지 데이터로
    """

    def __init__(self, config: Optional[EntropyConfig] = None):
        self.config = config or EntropyConfig()
        self._entropy_cache: List[float] = []

    def compute_entropy_multiplier(self, returns_1d: np.ndarray, t: int) -> float:
        """
        시점 t에서의 엔트로피 기반 노출도 멀티플라이어
        returns_1d: 포트폴리오 일별 수익률 (1D array)
        """
        cfg = self.config
        min_window = max(cfg.shannon_window, cfg.perm_window)

        if t < min_window + 1:
            return 1.0

        # t-1까지의 데이터만 사용 (룩어헤드 방지)
        window_data = returns_1d[max(0, t - min_window):t]
        window_data = window_data[np.isfinite(window_data)]

        if len(window_data) < min_window // 2:
            return 1.0

        # Shannon 엔트로피 계산
        shannon_e = self._calc_shannon_entropy(window_data, cfg.shannon_bins)

        # Permutation 엔트로피 계산
        perm_e = self._calc_permutation_entropy(window_data, cfg.perm_order, cfg.perm_delay)

        # 결합 엔트로피
        combined = cfg.shannon_weight * shannon_e + cfg.perm_weight * perm_e
        self._entropy_cache.append(combined)

        # 적응형 임계값 계산 (t-1까지의 엔트로피 히스토리 사용)
        hist_entropy = np.array(self._entropy_cache[:-1])  # 현재 제외
        if len(hist_entropy) < 20:
            low_t, high_t, extreme_t = 0.3, 0.6, 0.8
        else:
            lookback_hist = hist_entropy[-cfg.adaptive_lookback:]
            low_t = float(np.nanpercentile(lookback_hist, cfg.low_threshold_pct))
            high_t = float(np.nanpercentile(lookback_hist, cfg.high_threshold_pct))
            extreme_t = float(np.nanpercentile(lookback_hist, cfg.extreme_threshold_pct))

        # 레짐 분류
        if combined <= low_t:
            state = EntropyRegimeState.LOW_ENTROPY
        elif combined <= high_t:
            state = EntropyRegimeState.MEDIUM_ENTROPY
        elif combined <= extreme_t:
            state = EntropyRegimeState.HIGH_ENTROPY
        else:
            state = EntropyRegimeState.EXTREME_ENTROPY

        # 멀티플라이어 반환
        return {
            EntropyRegimeState.LOW_ENTROPY: cfg.low_entropy_multiplier,
            EntropyRegimeState.MEDIUM_ENTROPY: cfg.medium_entropy_multiplier,
            EntropyRegimeState.HIGH_ENTROPY: cfg.high_entropy_multiplier,
            EntropyRegimeState.EXTREME_ENTROPY: cfg.extreme_entropy_multiplier,
        }.get(state, 1.0)

    @staticmethod
    def _calc_shannon_entropy(data: np.ndarray, n_bins: int = 10) -> float:
        """Shannon 엔트로피 계산 (0-1 정규화)"""
        if len(data) < 5:
            return 0.5
        try:
            hist, _ = np.histogram(data, bins=n_bins, density=True)
            hist = hist + 1e-10
            hist = hist / hist.sum()
            entropy = -np.sum(hist * np.log2(hist + 1e-10))
            max_entropy = np.log2(n_bins)
            return float(np.clip(entropy / (max_entropy + 1e-12), 0.0, 1.0))
        except Exception:
            return 0.5

    @staticmethod
    def _calc_permutation_entropy(data: np.ndarray, order: int = 3, delay: int = 1) -> float:
        """Permutation 엔트로피 계산 (0-1 정규화)"""
        if len(data) < order * delay:
            return 0.5
        try:
            n = len(data)
            permutations = []
            for i in range(n - (order - 1) * delay):
                indices = [i + j * delay for j in range(order)]
                pattern = tuple(np.argsort([data[idx] for idx in indices]))
                permutations.append(pattern)

            if len(permutations) == 0:
                return 0.5

            pattern_counts = Counter(permutations)
            probs = np.array(list(pattern_counts.values())) / len(permutations)
            entropy = -np.sum(probs * np.log2(probs + 1e-10))
            max_entropy = np.log2(math.factorial(order))
            return float(np.clip(entropy / (max_entropy + 1e-12), 0.0, 1.0))
        except Exception:
            return 0.5


# ─────────────────────────────────────────────────────────────────────────────
# 2-D: CVaR 타겟팅 오버레이 (EC2 실제 코드 기반)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CVaROverlayConfig:
    window: int = 252
    alpha: float = 0.05
    target_cvar: float = 0.02
    min_scale: float = 0.0
    max_scale: float = 1.5
    eps: float = 1e-12
    smooth_span: int = 10
    vix_gate: bool = True
    vix_percentile_window: int = 252
    vix_min_gate: float = 0.5
    vix_max_gate: float = 1.0
    regime_gate: bool = True
    regime_max_scale: Optional[Dict[int, float]] = None
    clamp_nan_to: float = 1.0


class CVaRTargetingOverlayV70:
    """
    CVaR 기반 노출도 오버레이 (v7.0 통합판)
    - 롤링 히스토리컬 CVaR 추정
    - VIX 게이팅 (높은 VIX → 보수적)
    - 레짐 게이팅 (위기 레짐 → 최대 스케일 제한)
    - EWMA 스무딩 (휩소 방지)
    - 룩어헤드 방지: CVaR(t)는 t-1까지 데이터 사용
    """

    def __init__(self, config: Optional[CVaROverlayConfig] = None):
        self.config = config or CVaROverlayConfig()
        if self.config.regime_max_scale is None:
            # 기본 레짐별 최대 스케일
            self.config.regime_max_scale = {
                int(RegimeLabel.CRISIS): 0.5,
                int(RegimeLabel.BEAR): 0.75,
                int(RegimeLabel.RATE_SHOCK): 0.6,
                int(RegimeLabel.INF_SHOCK): 0.7,
            }

    def compute_scale(
        self,
        port_returns: np.ndarray,
        vix_series: np.ndarray,
        regime_series: np.ndarray,
        t: int
    ) -> float:
        """
        시점 t에서의 노출도 스케일 계산
        port_returns: 포트폴리오 일별 수익률 (1D)
        vix_series: VIX 시계열 (1D)
        regime_series: 레짐 시계열 (1D)
        """
        c = self.config

        if t < max(c.window // 2, 20):
            return float(c.clamp_nan_to)

        # t-1까지의 데이터만 사용 (룩어헤드 방지)
        r = port_returns[:t]
        r = r[np.isfinite(r)]

        if len(r) < 20:
            return float(c.clamp_nan_to)

        # 롤링 CVaR 계산 (최근 window 기간)
        window_r = r[-c.window:]
        est_cvar = self._compute_cvar(window_r, c.alpha)

        if not np.isfinite(est_cvar) or est_cvar < c.eps:
            return float(c.clamp_nan_to)

        # 기본 스케일
        raw_scale = float(np.clip(c.target_cvar / est_cvar, c.min_scale, c.max_scale))

        # VIX 게이팅
        if c.vix_gate and t > 0 and t <= len(vix_series):
            vix_val = float(vix_series[t - 1])  # t-1 사용
            vix_hist = vix_series[:t]
            vix_hist = vix_hist[np.isfinite(vix_hist)]
            if len(vix_hist) >= 20:
                vix_pct = float(np.mean(vix_hist <= vix_val))
                gate = float(c.vix_max_gate - (c.vix_max_gate - c.vix_min_gate) * vix_pct)
                raw_scale = float(np.clip(raw_scale * gate, c.min_scale, c.max_scale))

        # 레짐 게이팅
        if c.regime_gate and c.regime_max_scale and t > 0 and t <= len(regime_series):
            current_regime = int(regime_series[t - 1])  # t-1 사용
            if current_regime in c.regime_max_scale:
                regime_max = float(c.regime_max_scale[current_regime])
                raw_scale = float(np.clip(raw_scale, c.min_scale, regime_max))

        return float(np.clip(raw_scale, c.min_scale, c.max_scale))

    @staticmethod
    def _compute_cvar(returns: np.ndarray, alpha: float = 0.05) -> float:
        """히스토리컬 CVaR 계산 (양수 손실 크기 반환)"""
        valid = returns[np.isfinite(returns)]
        if len(valid) < 10:
            return np.nan
        q = float(np.quantile(valid, alpha))
        tail = valid[valid <= q]
        if len(tail) == 0:
            return abs(q)
        return float(-np.mean(tail))


# ─────────────────────────────────────────────────────────────────────────────
# 2-E: SystemicRiskEngineV70 (통합 리스크 엔진)
# ─────────────────────────────────────────────────────────────────────────────

class SystemicRiskEngineV70:
    """
    v7.0 통합 시스템 리스크 엔진
    - RegimeDetector: 7-레짐 탐지
    - EVTRiskManager: 극단값 이론 꼬리 리스크
    - EntropyRegimeFilter: 엔트로피 기반 레짐 필터
    - CVaRTargetingOverlay: CVaR 기반 노출도 조정
    """

    def __init__(
        self,
        evt_config: Optional[EVTConfig] = None,
        entropy_config: Optional[EntropyConfig] = None,
        cvar_config: Optional[CVaROverlayConfig] = None,
    ):
        self.regime_detector = RegimeDetectorV70()
        self.evt = EVTRiskManagerV70(evt_config)
        self.entropy = EntropyRegimeFilterV70(entropy_config)
        self.cvar = CVaRTargetingOverlayV70(cvar_config)

        # 레짐 히스토리
        self._regime_arr: np.ndarray = np.array([], dtype=int)

    def compute_features(self, data: 'DataHandlerV70') -> Dict[str, np.ndarray]:
        """레짐 탐지용 피처 계산"""
        port_ret = data.mkt_ret
        port_ret_clean = np.nan_to_num(port_ret, nan=0.0)
        vix_clean = np.nan_to_num(data.vix, nan=np.nanmean(data.vix))

        vix_z = fast_zscore(vix_clean, 252, 100)
        vix_z = np.nan_to_num(vix_z, nan=0.0)

        vol_21 = fast_rolling_std(port_ret_clean, 21) * np.sqrt(252)
        vol_21 = np.nan_to_num(vol_21, nan=0.15)
        vol_z = fast_zscore(vol_21, 252, 100)
        vol_z = np.nan_to_num(vol_z, nan=0.0)

        mom_126 = pd.Series(port_ret_clean).rolling(126, min_periods=20).sum().values
        mom_126 = np.nan_to_num(mom_126, nan=0.0)
        mom_z = fast_zscore(mom_126, 252, 100)
        mom_z = np.nan_to_num(mom_z, nan=0.0)

        dd_20 = np.zeros(len(port_ret))
        for i in range(20, len(port_ret)):
            dd_20[i] = fast_max_drawdown(port_ret_clean[i - 20:i])

        dgs2 = np.nan_to_num(data.dgs2, nan=2.0)
        dgs2_change_20d = np.zeros(len(dgs2))
        dgs2_change_5d = np.zeros(len(dgs2))
        for i in range(20, len(dgs2)):
            dgs2_change_20d[i] = (dgs2[i] - dgs2[i - 20]) * 100
        for i in range(5, len(dgs2)):
            dgs2_change_5d[i] = (dgs2[i] - dgs2[i - 5]) * 100

        return {
            'vix_z': vix_z, 'vol_z': vol_z, 'mom_z': mom_z, 'dd_20': dd_20,
            't10y2y': np.nan_to_num(data.t10y2y, nan=1.0),
            'dgs2_change_20d': dgs2_change_20d,
            'dgs2_change_5d': dgs2_change_5d,
            'dgs2_level': dgs2,
        }

    def compute_all_risk_multipliers(
        self,
        t: int,
        features: Dict[str, np.ndarray],
        port_returns: np.ndarray,
        vix_series: np.ndarray,
    ) -> Dict[str, float]:
        """
        시점 t에서의 모든 리스크 멀티플라이어 계산
        반환: {'regime': int, 'evt_mult': float, 'entropy_mult': float, 'cvar_scale': float, 'combined_mult': float}
        """
        # 레짐 탐지
        regime = self.regime_detector.detect(t, features)

        # 레짐 배열 업데이트
        if len(self._regime_arr) <= t:
            new_arr = np.full(t + 1, regime, dtype=int)
            if len(self._regime_arr) > 0:
                new_arr[:len(self._regime_arr)] = self._regime_arr
            self._regime_arr = new_arr
        else:
            self._regime_arr[t] = regime

        # EVT 멀티플라이어
        evt_mult = self.evt.compute_multiplier(port_returns, t)

        # 엔트로피 멀티플라이어
        entropy_mult = self.entropy.compute_entropy_multiplier(port_returns, t)

        # CVaR 스케일
        cvar_scale = self.cvar.compute_scale(port_returns, vix_series, self._regime_arr, t)

        # 통합 멀티플라이어 (기하 평균)
        combined = float(np.clip(
            evt_mult * entropy_mult * cvar_scale,
            0.0, 2.0
        ))

        return {
            'regime': regime,
            'evt_mult': evt_mult,
            'entropy_mult': entropy_mult,
            'cvar_scale': cvar_scale,
            'combined_mult': combined,
        }


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 3: ALPHA SUITE
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# 3-A: GNN Lite 스필오버 (v4.37 기반)
# ─────────────────────────────────────────────────────────────────────────────

class GNNLiteSpillover:
    """
    경량 GNN 스필오버 모듈
    MST(최소 신장 트리) 기반 네트워크 중심성으로 스필오버 점수 계산
    """

    def __init__(self, returns: np.ndarray, prices: np.ndarray, window: int = 60):
        self.returns = returns
        self.prices = prices
        self.window = window
        self._cache: Dict[int, np.ndarray] = {}

    def compute_spillover_score(self, t: int) -> np.ndarray:
        n_assets = self.returns.shape[1]
        if t < self.window:
            return np.full(n_assets, np.nan)

        cache_key = t // 5  # 5일마다 업데이트
        if cache_key in self._cache:
            return self._cache[cache_key]

        window_ret = self.returns[t - self.window:t]
        valid_mask = ~np.all(np.isnan(window_ret), axis=0)
        if np.sum(valid_mask) < 3:
            return np.full(n_assets, np.nan)

        ret_clean = np.nan_to_num(window_ret[:, valid_mask], nan=0.0)

        try:
            corr = np.corrcoef(ret_clean.T)
            corr = np.nan_to_num(corr, nan=0.0)
            np.fill_diagonal(corr, 1.0)

            # 거리 행렬 (1 - |corr|)
            dist = 1.0 - np.abs(corr)
            dist = np.clip(dist, 0.0, 1.0)

            # MST
            sparse_dist = csr_matrix(dist)
            mst = minimum_spanning_tree(sparse_dist)
            mst_arr = mst.toarray()

            # 중심성: MST 연결 수
            degree = np.sum(mst_arr > 0, axis=0) + np.sum(mst_arr > 0, axis=1)
            degree_norm = degree / (np.max(degree) + 1e-12)

            # 스필오버 점수: 높은 중심성 = 낮은 점수 (분산 효과)
            scores_valid = -degree_norm

            scores = np.full(n_assets, np.nan)
            valid_idx = np.where(valid_mask)[0]
            for i, idx in enumerate(valid_idx):
                scores[idx] = scores_valid[i]

            self._cache[cache_key] = scores
            return scores

        except Exception:
            return np.full(n_assets, np.nan)


# ─────────────────────────────────────────────────────────────────────────────
# 3-B: TDA Lite 위상 데이터 분석 (v4.37 기반)
# ─────────────────────────────────────────────────────────────────────────────

class TDALiteCalculator:
    """
    경량 위상 데이터 분석 (TDA Lite)
    Betti 수 근사를 통한 시장 위상 구조 변화 탐지
    """

    def __init__(self, returns: np.ndarray, window: int = 60):
        self.returns = returns
        self.window = window
        self._cache: Dict[int, float] = {}

    def compute_betti_score(self, t: int) -> float:
        """Betti 수 근사 (클러스터 수 기반)"""
        if t < self.window:
            return 0.0

        cache_key = t // 5
        if cache_key in self._cache:
            return self._cache[cache_key]

        window_ret = self.returns[t - self.window:t]
        ret_clean = np.nan_to_num(window_ret, nan=0.0)

        try:
            corr = np.corrcoef(ret_clean.T)
            corr = np.nan_to_num(corr, nan=0.0)
            np.fill_diagonal(corr, 1.0)

            # 임계값 이상 상관관계 연결 수 (위상 복잡도)
            threshold = 0.7
            connections = np.sum(np.abs(corr) > threshold) - corr.shape[0]
            max_connections = corr.shape[0] * (corr.shape[0] - 1)
            betti_score = float(connections / (max_connections + 1e-12))

            self._cache[cache_key] = betti_score
            return betti_score

        except Exception:
            return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 3-C: IC/IR 앙상블 V8 (v4.37 기반)
# ─────────────────────────────────────────────────────────────────────────────

class ICIREnsembleV8:
    """
    IC/IR 앙상블 v8 (v4.37 기반)
    팩터별 IC(정보 계수) 및 IR(정보 비율)을 추적하여
    동적으로 팩터 가중치를 조정
    """

    def __init__(self, n_factors: int, window: int = 63, min_periods: int = 20):
        self.n_factors = n_factors
        self.window = window
        self.min_periods = min_periods
        self._ic_history: Dict[str, deque] = {}
        self._weights: Optional[np.ndarray] = None

    def update(self, factor_scores: Dict[str, np.ndarray], realized_returns: np.ndarray):
        """
        팩터 스코어와 실현 수익률로 IC 업데이트
        factor_scores: {팩터명: 스코어 배열}
        realized_returns: 다음 기간 실현 수익률
        """
        for fname, scores in factor_scores.items():
            if fname not in self._ic_history:
                self._ic_history[fname] = deque(maxlen=self.window)

            valid = np.isfinite(scores) & np.isfinite(realized_returns)
            if np.sum(valid) >= 5:
                try:
                    ic, _ = spearmanr(scores[valid], realized_returns[valid])
                    if np.isfinite(ic):
                        self._ic_history[fname].append(float(ic))
                except Exception:
                    pass

    def compute_weights(self, factor_names: List[str]) -> np.ndarray:
        """
        IC/IR 기반 팩터 가중치 계산
        IR = mean(IC) / std(IC)
        """
        weights = np.ones(len(factor_names)) / len(factor_names)

        ir_scores = []
        for fname in factor_names:
            hist = list(self._ic_history.get(fname, []))
            if len(hist) >= self.min_periods:
                ic_mean = np.mean(hist)
                ic_std = np.std(hist)
                ir = ic_mean / (ic_std + 1e-8)
                ir_scores.append(float(ir))
            else:
                ir_scores.append(0.0)

        ir_arr = np.array(ir_scores)
        # 양수 IR만 사용, 음수는 0으로
        ir_arr = np.maximum(ir_arr, 0.0)

        total = np.sum(ir_arr)
        if total > 1e-8:
            weights = ir_arr / total
        else:
            weights = np.ones(len(factor_names)) / len(factor_names)

        return weights


# ─────────────────────────────────────────────────────────────────────────────
# 3-D: CrossAsset 모멘텀 다이버전스 (EC2 실제 코드 기반)
# ─────────────────────────────────────────────────────────────────────────────

class CrossAssetMomentumDivergenceV70:
    """
    크로스에셋 모멘텀 다이버전스 (v7.0 통합판)
    - 개별 자산 모멘텀 vs 시장 평균 모멘텀 다이버전스 탐지
    - 군집 효과(crowding) 방지: 과도한 다이버전스 → 가중치 감소
    - VIX 스케일링 지원
    - 룩어헤드 방지: t-1까지 데이터만 사용
    """

    def __init__(
        self,
        lookback_momentum: int = 20,
        divergence_threshold: float = 1.5,
        vix_scaling: bool = True,
        max_adjustment: float = 0.5,
        min_lookback: int = 10,
    ):
        self.lookback = max(lookback_momentum, min_lookback)
        self.divergence_threshold = divergence_threshold
        self.vix_scaling = vix_scaling
        self.max_adjustment = float(np.clip(max_adjustment, 0.0, 1.0))
        self._mom_history: List[np.ndarray] = []
        self._market_mom_history: List[float] = []

    def compute_weight_adjustment(
        self,
        prices: np.ndarray,  # (T, N)
        vix_val: float,
        t: int,
    ) -> np.ndarray:
        """
        시점 t에서의 가중치 조정 계수 계산
        prices: (T, N) 가격 배열
        반환: (N,) 가중치 조정 계수 (0.5 ~ 1.5)
        """
        n_assets = prices.shape[1]

        if t < self.lookback + 1:
            return np.ones(n_assets)

        # t-1까지의 데이터만 사용 (룩어헤드 방지)
        price_slice = prices[max(0, t - self.lookback - 1):t]

        if price_slice.shape[0] < self.lookback // 2:
            return np.ones(n_assets)

        # 로그 수익률 기반 모멘텀
        with np.errstate(divide='ignore', invalid='ignore'):
            log_ret = np.diff(np.log(np.where(price_slice > 0, price_slice, np.nan)), axis=0)
        log_ret = np.nan_to_num(log_ret, nan=0.0)

        if log_ret.shape[0] < 2:
            return np.ones(n_assets)

        # 개별 자산 모멘텀
        asset_mom = np.nanmean(log_ret[-min(self.lookback, log_ret.shape[0]):], axis=0)

        # 시장 평균 모멘텀
        market_mom = float(np.nanmean(asset_mom[np.isfinite(asset_mom)]))

        # 다이버전스 z-score
        self._mom_history.append(asset_mom.copy())
        self._market_mom_history.append(market_mom)

        if len(self._mom_history) < max(5, self.lookback // 4):
            return np.ones(n_assets)

        # 히스토리 기반 다이버전스 표준화
        hist_mom = np.array(self._mom_history[-self.lookback:])
        hist_market = np.array(self._market_mom_history[-self.lookback:])

        divergence = asset_mom - market_mom
        hist_div = hist_mom - hist_market[:, np.newaxis]
        div_std = np.nanstd(hist_div, axis=0)
        div_std = np.where(div_std < 1e-8, 1e-8, div_std)

        div_zscore = divergence / div_std

        # 조정 계수: 높은 다이버전스 → 가중치 감소
        adjustment = np.ones(n_assets)
        high_div_mask = np.abs(div_zscore) > self.divergence_threshold
        if np.any(high_div_mask):
            reduction = self.max_adjustment * np.clip(
                (np.abs(div_zscore[high_div_mask]) - self.divergence_threshold) / self.divergence_threshold,
                0.0, 1.0
            )
            adjustment[high_div_mask] = 1.0 - reduction

        # VIX 스케일링
        if self.vix_scaling and vix_val > 0:
            vix_factor = float(np.clip(20.0 / vix_val, 0.5, 1.5))
            adjustment = 1.0 + (adjustment - 1.0) * vix_factor

        return np.clip(adjustment, 0.5, 1.5)


# ─────────────────────────────────────────────────────────────────────────────
# 3-E: 멀티팩터 계산기 V22 (v4.37 기반 + v7.0 확장)
# ─────────────────────────────────────────────────────────────────────────────

class MultiFactorCalculatorV70:
    """
    v7.0 멀티팩터 계산기
    9팩터 체계: trend/mean_rev/bb_rev/gap_rev/amihud/pead/quality/gnn_spill/sector_rot
    + theme_conc (선택적)
    """

    # 섹터 분류 (v4.37에서 이식)
    DEFENSIVE_SYMBOLS = {
        'VRTX', 'ABBV', 'LLY', 'REGN', 'AMGN', 'GILD', 'ISRG',
        'KO', 'PEP', 'MCD', 'JNJ', 'WMT', 'COST', 'PG',
        'BRK.B', 'AXP', 'JPM',
    }
    GROWTH_SYMBOLS = {
        'NVDA', 'AMD', 'AMAT', 'LRCX', 'ASML', 'ADBE', 'CRWD', 'DDOG',
        'TSLA', 'NFLX', 'GOOGL', 'META', 'PYPL', 'OKTA',
    }

    def __init__(
        self,
        returns: np.ndarray,
        prices: np.ndarray,
        volumes: Optional[np.ndarray] = None,
        opens: Optional[np.ndarray] = None,
        symbols: Optional[List[str]] = None,
        gnn_spillover: Optional[GNNLiteSpillover] = None,
    ):
        self.returns = returns
        self.prices = prices
        self.volumes = volumes
        self.opens = opens
        self.symbols = symbols or []
        self.gnn = gnn_spillover
        self.n_assets = returns.shape[1]

        # 거래량 기반 달러 거래량 (Amihud 비유동성)
        if volumes is not None and prices is not None:
            self.dvol = volumes * prices
        else:
            self.dvol = None

        # 테마 집중 상태 추적
        self.last_theme_hhi = np.nan
        self.last_theme_indicator = 0.0

    def compute_trend(self, t: int) -> np.ndarray:
        """순수 중기 모멘텀 (12개월 - 1개월, 단기 역전 제거)"""
        if t < 252:
            return np.full(self.n_assets, np.nan)
        ret_12m = self.prices[t - 1] / (self.prices[t - 252] + 1e-10) - 1
        ret_1m = self.prices[t - 1] / (self.prices[t - 21] + 1e-10) - 1
        ret_3m = self.prices[t - 1] / (self.prices[t - 63] + 1e-10) - 1 if t >= 63 else ret_1m
        mom_pure = ret_12m - ret_1m
        return 0.60 * mom_pure + 0.40 * ret_3m

    def compute_mean_reversion(self, t: int, window: int = 5) -> np.ndarray:
        """단기 평균 회귀"""
        if t < window:
            return np.full(self.n_assets, np.nan)
        return -np.nansum(self.returns[t - window:t], axis=0)

    def compute_bb_rev(self, t: int, window: int = 20, n_std: float = 2.0) -> np.ndarray:
        """볼린저 밴드 역전"""
        if t < window:
            return np.full(self.n_assets, np.nan)
        prices_w = self.prices[t - window:t]
        ma = np.nanmean(prices_w, axis=0)
        std = np.nanstd(prices_w, axis=0)
        current_price = self.prices[t - 1]
        return -(current_price - ma) / (n_std * std + 1e-10)

    def compute_gap_rev(self, t: int, window: int = 5) -> np.ndarray:
        """갭 역전"""
        if self.opens is None or t < window + 1:
            return np.full(self.n_assets, np.nan)
        gap_scores = np.zeros(self.n_assets)
        count = 0
        for d in range(window):
            idx = t - 1 - d
            if idx < 1:
                continue
            prev_close = self.prices[idx - 1]
            open_t = self.opens[idx]
            close_t = self.prices[idx]
            gap = (open_t - prev_close) / (prev_close + 1e-10)
            intra_rev = (close_t - open_t) / (open_t + 1e-10)
            gap_scores += np.nan_to_num(-gap * np.sign(intra_rev), nan=0.0)
            count += 1
        return gap_scores / count if count > 0 else np.full(self.n_assets, np.nan)

    def compute_amihud(self, t: int, window: int = 20) -> np.ndarray:
        """Amihud 비유동성 (낮은 유동성 = 낮은 점수)"""
        if self.dvol is None or t < window:
            return np.full(self.n_assets, np.nan)
        illiq = np.zeros(self.n_assets)
        for i in range(self.n_assets):
            abs_ret = np.abs(self.returns[t - window:t, i])
            dvol = self.dvol[t - window:t, i]
            valid = (~np.isnan(abs_ret)) & (~np.isnan(dvol)) & (dvol > 0)
            if np.sum(valid) >= window // 2:
                illiq[i] = -np.mean(abs_ret[valid] / dvol[valid])
            else:
                illiq[i] = np.nan
        return illiq

    def compute_pead_base(self, t: int, window: int = 5) -> np.ndarray:
        """PEAD 기본 (큰 갭 추종 + 작은 갭 역전)"""
        if self.opens is None or t < window + 1:
            return np.full(self.n_assets, np.nan)
        gap_scores = np.zeros(self.n_assets)
        count = 0
        for d in range(window):
            idx = t - d
            if idx < 1:
                continue
            prev_close = self.prices[idx - 1]
            open_t = self.opens[idx]
            close_t = self.prices[idx]
            gap = (open_t - prev_close) / (prev_close + 1e-10)
            intra_move = (close_t - open_t) / (open_t + 1e-10)
            gap_abs = np.abs(gap)
            large_gap_mask = gap_abs > 0.005
            pead_signal = np.where(
                large_gap_mask,
                gap * (1 + np.sign(gap) * np.sign(intra_move) * 0.5),
                -gap
            )
            gap_scores += np.nan_to_num(pead_signal, nan=0.0)
            count += 1
        return gap_scores / count if count > 0 else np.full(self.n_assets, np.nan)

    def compute_pead_regime(self, t: int, regime: int = 2, window: int = 5) -> np.ndarray:
        """레짐별 PEAD (v4.33 기반)"""
        if self.opens is None or t < 4:
            return np.full(self.n_assets, np.nan)
        # INF_SHOCK/RATE_SHOCK: 제로 PEAD
        if regime in (5, 6):
            return np.zeros(self.n_assets)
        # CRISIS/BEAR: 단순 역전 (window=3)
        if regime in (0, 1):
            w = 3
            gap_scores = np.zeros(self.n_assets)
            count = 0
            for d in range(w):
                idx = t - d
                if idx < 1:
                    continue
                gap = (self.opens[idx] - self.prices[idx - 1]) / (self.prices[idx - 1] + 1e-10)
                gap_scores += -gap
                count += 1
            return gap_scores / count if count > 0 else np.full(self.n_assets, np.nan)
        # NORMAL/BULL/STRONG_BULL: 강화 PEAD
        gap_scores = np.zeros(self.n_assets)
        count = 0
        for d in range(window):
            idx = t - d
            if idx < 1:
                continue
            prev_close = self.prices[idx - 1]
            open_t = self.opens[idx]
            close_t = self.prices[idx]
            gap = (open_t - prev_close) / (prev_close + 1e-10)
            intra_move = (close_t - open_t) / (open_t + 1e-10)
            gap_abs = np.abs(gap)
            large_gap_mask = gap_abs > 0.005
            pead_signal = np.where(
                large_gap_mask,
                gap * (1 + np.sign(gap) * np.sign(intra_move) * 0.5),
                -gap
            )
            gap_scores += np.nan_to_num(pead_signal, nan=0.0)
            count += 1
        return gap_scores / count if count > 0 else np.full(self.n_assets, np.nan)

    def compute_quality(self, t: int, window: int = 20) -> np.ndarray:
        """품질 팩터 (거래량 일관성)"""
        if self.dvol is None or t < window:
            return np.full(self.n_assets, np.nan)
        dvol_window = self.dvol[t - window:t]
        dvol_mean = np.nanmean(dvol_window, axis=0)
        dvol_std = np.nanstd(dvol_window, axis=0)
        return -np.clip(dvol_std / (dvol_mean + 1e-10), -5, 5)

    def compute_gnn_spillover(self, t: int) -> np.ndarray:
        """GNN 스필오버 점수"""
        if self.gnn is not None:
            return self.gnn.compute_spillover_score(t)
        return np.full(self.n_assets, np.nan)

    def compute_sector_rotation(self, t: int, window: int = 252) -> np.ndarray:
        """
        섹터 로테이션 팩터 (v4.37에서 이식)
        헬스케어/필수소비재 vs 성장주 상대강도
        """
        if t < window or not self.symbols:
            return np.full(self.n_assets, np.nan)

        ret_1y = np.full(self.n_assets, np.nan)
        if t < len(self.prices):
            cur_prices = self.prices[t - 1]
            past_prices = self.prices[t - window]
            with np.errstate(divide='ignore', invalid='ignore'):
                r = np.where(past_prices > 1e-10, cur_prices / past_prices - 1.0, np.nan)
            ret_1y = r

        def_returns = []
        growth_returns = []
        for i, sym in enumerate(self.symbols):
            if not np.isnan(ret_1y[i]):
                if sym in self.DEFENSIVE_SYMBOLS:
                    def_returns.append(ret_1y[i])
                elif sym in self.GROWTH_SYMBOLS:
                    growth_returns.append(ret_1y[i])

        if not def_returns or not growth_returns:
            return np.full(self.n_assets, np.nan)

        def_avg = np.mean(def_returns)
        growth_avg = np.mean(growth_returns)
        sector_spread = def_avg - growth_avg

        scores = np.full(self.n_assets, np.nan)
        for i, sym in enumerate(self.symbols):
            if np.isnan(ret_1y[i]):
                continue
            if sym in self.DEFENSIVE_SYMBOLS:
                scores[i] = sector_spread * (1.0 + ret_1y[i] - def_avg)
            elif sym in self.GROWTH_SYMBOLS:
                scores[i] = -sector_spread * (1.0 + ret_1y[i] - growth_avg)
            else:
                scores[i] = 0.0

        return scores

    def compute_theme_concentration(self, t: int, lookback: int = 63, topn: int = 25) -> np.ndarray:
        """
        테마 집중 팩터 (v4.34에서 이식)
        HHI 기반 집중 랠리 포착
        """
        if t < lookback + 1:
            self.last_theme_hhi = np.nan
            self.last_theme_indicator = 0.0
            return np.full(self.n_assets, np.nan)

        mom = self.prices[t] / (self.prices[t - lookback] + 1e-12) - 1.0
        mom = np.nan_to_num(mom, nan=0.0)

        pos = np.clip(mom, 0.0, None)
        s = float(np.sum(pos))
        if s <= 1e-12:
            self.last_theme_hhi = 0.0
            self.last_theme_indicator = 0.0
            return np.zeros(self.n_assets)

        w = pos / s
        if topn < len(w):
            idx = np.argsort(-w)[:topn]
            w_top = w[idx]
        else:
            w_top = w
        hhi = float(np.sum(w_top ** 2))

        hhi_thr = 0.08
        hhi_max = 0.22
        ind = float(np.clip((hhi - hhi_thr) / (hhi_max - hhi_thr + 1e-12), 0.0, 1.0))

        self.last_theme_hhi = round(hhi, 4)
        self.last_theme_indicator = round(ind, 4)
        return mom * ind

    def compute_all_scores(
        self,
        t: int,
        regime: int = 2,
        pead_mode: str = 'ensemble',
        enable_theme_conc: bool = False,
    ) -> Dict[str, np.ndarray]:
        """모든 팩터 스코어 계산"""
        scores = {
            'trend':      nan_safe_scores(self.compute_trend(t), "trend"),
            'mean_rev':   nan_safe_scores(self.compute_mean_reversion(t), "mean_rev"),
            'bb_rev':     nan_safe_scores(self.compute_bb_rev(t), "bb_rev"),
            'gap_rev':    nan_safe_scores(self.compute_gap_rev(t), "gap_rev"),
            'amihud':     nan_safe_scores(self.compute_amihud(t), "amihud"),
            'quality':    nan_safe_scores(self.compute_quality(t), "quality"),
            'gnn_spill':  nan_safe_scores(self.compute_gnn_spillover(t), "gnn_spill"),
            'sector_rot': nan_safe_scores(self.compute_sector_rotation(t), "sector_rot"),
        }

        pm = str(pead_mode).lower().strip()
        if pm in ('ensemble', 'ens'):
            pead_base = nan_safe_scores(self.compute_pead_base(t), "pead_base")
            pead_reg = nan_safe_scores(self.compute_pead_regime(t, regime=regime), "pead_regime")
            scores['pead'] = 0.60 * pead_base + 0.40 * pead_reg
        elif pm in ('v433', 'regime', 'split'):
            scores['pead'] = nan_safe_scores(self.compute_pead_regime(t, regime=regime), "pead")
        else:
            scores['pead'] = nan_safe_scores(self.compute_pead_base(t), "pead")

        if enable_theme_conc:
            scores['theme_conc'] = nan_safe_scores(self.compute_theme_concentration(t), "theme_conc")

        return scores


# ─────────────────────────────────────────────────────────────────────────────
# 3-F: AlphaScorerV5 통합 (EC2 실제 코드 기반)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AlphaScorerV5Config:
    enabled: bool = True
    lookback_fast: int = 5
    lookback_mid: int = 20
    lookback_slow: int = 60
    vol_lookback: int = 20
    z_clip: float = 3.0
    tilt_strength: float = 0.15
    min_scored_assets: int = 5


class AlphaScorerV5:
    """
    레짐 조건부 멀티팩터 스코어러 (EC2 실제 코드 기반)
    팩터: mom, rev, breakout, lowvol, liquidity
    레짐별 틸트: trend, mean_revert, high_vol, risk_off, risk_on, neutral
    """

    BASE_WEIGHTS = {"mom": 1.00, "rev": 0.70, "breakout": 0.60, "lowvol": 0.50, "liquidity": 0.25}
    REGIME_TILTS = {
        "trend":       {"mom": 1.35, "rev": 0.50, "breakout": 1.25, "lowvol": 0.80, "liquidity": 1.00},
        "mean_revert": {"mom": 0.75, "rev": 1.45, "breakout": 0.70, "lowvol": 1.00, "liquidity": 1.05},
        "high_vol":    {"mom": 0.80, "rev": 0.85, "breakout": 0.70, "lowvol": 1.55, "liquidity": 1.15},
        "risk_off":    {"mom": 0.75, "rev": 0.65, "breakout": 0.60, "lowvol": 1.75, "liquidity": 1.10},
        "risk_on":     {"mom": 1.20, "rev": 0.80, "breakout": 1.10, "lowvol": 0.85, "liquidity": 1.05},
        "neutral":     {"mom": 1.00, "rev": 1.00, "breakout": 1.00, "lowvol": 1.00, "liquidity": 1.00},
    }

    def __init__(self, config: Optional[AlphaScorerV5Config] = None):
        self.cfg = config or AlphaScorerV5Config()

    def _regime_to_str(self, regime: int) -> str:
        if regime in (0, 1):
            return "risk_off"
        elif regime in (5, 6):
            return "high_vol"
        elif regime == 2:
            return "neutral"
        elif regime == 3:
            return "risk_on"
        elif regime == 4:
            return "trend"
        return "neutral"

    def compute_multiplier(self, returns: np.ndarray, t: int, regime: int) -> np.ndarray:
        """
        returns: (T, N) 수익률 배열
        t: 현재 시점
        regime: 현재 레짐
        반환: (N,) 가중치 멀티플라이어
        """
        cfg = self.cfg
        if not cfg.enabled or t < cfg.lookback_slow + 1:
            return np.ones(returns.shape[1])

        rets = returns[:t]
        T, N = rets.shape

        # 팩터 계산
        def _sum_last(lb: int) -> np.ndarray:
            if T < lb + 1:
                return np.full(N, np.nan)
            return np.nansum(rets[-lb:, :], axis=0)

        mom = _sum_last(cfg.lookback_mid)
        rev = -_sum_last(cfg.lookback_fast)
        slow = _sum_last(cfg.lookback_slow)
        breakout = mom - slow

        if T >= cfg.vol_lookback + 1:
            vol = np.nanstd(rets[-cfg.vol_lookback:, :], axis=0)
        else:
            vol = np.nanstd(rets, axis=0)
        lowvol = -vol

        dr = np.diff(np.nan_to_num(rets, nan=0.0), axis=0)
        liq = -np.nanstd(dr, axis=0)

        factors_raw = {"mom": mom, "rev": rev, "breakout": breakout, "lowvol": lowvol, "liquidity": liq}
        factors_z = {k: robust_zscore(v, cfg.z_clip) for k, v in factors_raw.items()}

        # 레짐 가중치
        regime_str = self._regime_to_str(regime)
        tilt = self.REGIME_TILTS.get(regime_str, self.REGIME_TILTS["neutral"])
        fw = {}
        for k, w in self.BASE_WEIGHTS.items():
            t_val = tilt.get(k, 1.0)
            fw[k] = w * t_val
        total_w = sum(abs(v) for v in fw.values()) + 1e-12
        fw = {k: v / total_w for k, v in fw.items()}

        # 종합 스코어
        score = np.zeros(N)
        for k, wk in fw.items():
            score += wk * factors_z[k]

        # 스코어 → 멀티플라이어
        score_norm = robust_zscore(score, 2.0)
        multiplier = 1.0 + cfg.tilt_strength * score_norm
        multiplier = np.clip(multiplier, 0.8, 1.2)

        # 커버리지 체크
        scored = int(np.sum(np.isfinite(score) & (np.abs(score) > 1e-6)))
        if scored < cfg.min_scored_assets:
            return np.ones(N)

        return multiplier


# ─────────────────────────────────────────────────────────────────────────────
# 3-G: AlphaSuiteV70 (통합 알파 엔진)
# ─────────────────────────────────────────────────────────────────────────────

# 레짐별 팩터 가중치 (v4.37 기반 + v7.0 조정)
REGIME_FACTOR_WEIGHTS: Dict[int, Dict[str, float]] = {
    int(RegimeLabel.CRISIS): {
        'trend': 0.05, 'mean_rev': 0.05, 'bb_rev': 0.10, 'gap_rev': 0.05,
        'amihud': 0.25, 'pead': 0.00, 'quality': 0.30, 'gnn_spill': 0.10, 'sector_rot': 0.10,
    },
    int(RegimeLabel.BEAR): {
        'trend': 0.10, 'mean_rev': 0.10, 'bb_rev': 0.15, 'gap_rev': 0.05,
        'amihud': 0.20, 'pead': 0.05, 'quality': 0.20, 'gnn_spill': 0.10, 'sector_rot': 0.05,
    },
    int(RegimeLabel.NORMAL): {
        'trend': 0.20, 'mean_rev': 0.15, 'bb_rev': 0.10, 'gap_rev': 0.10,
        'amihud': 0.10, 'pead': 0.10, 'quality': 0.10, 'gnn_spill': 0.10, 'sector_rot': 0.05,
    },
    int(RegimeLabel.BULL): {
        'trend': 0.30, 'mean_rev': 0.10, 'bb_rev': 0.05, 'gap_rev': 0.10,
        'amihud': 0.05, 'pead': 0.15, 'quality': 0.05, 'gnn_spill': 0.10, 'sector_rot': 0.10,
    },
    int(RegimeLabel.STRONG_BULL): {
        'trend': 0.35, 'mean_rev': 0.05, 'bb_rev': 0.05, 'gap_rev': 0.10,
        'amihud': 0.05, 'pead': 0.20, 'quality': 0.05, 'gnn_spill': 0.05, 'sector_rot': 0.10,
    },
    int(RegimeLabel.INF_SHOCK): {
        'trend': 0.10, 'mean_rev': 0.10, 'bb_rev': 0.10, 'gap_rev': 0.05,
        'amihud': 0.20, 'pead': 0.00, 'quality': 0.25, 'gnn_spill': 0.10, 'sector_rot': 0.10,
    },
    int(RegimeLabel.RATE_SHOCK): {
        'trend': 0.05, 'mean_rev': 0.05, 'bb_rev': 0.10, 'gap_rev': 0.05,
        'amihud': 0.25, 'pead': 0.00, 'quality': 0.30, 'gnn_spill': 0.10, 'sector_rot': 0.10,
    },
}


class AlphaSuiteV70:
    """
    v7.0 통합 알파 엔진
    - MultiFactorCalculatorV70: 9팩터 계산
    - ICIREnsembleV8: IC/IR 기반 동적 팩터 가중치
    - AlphaScorerV5: 레짐 조건부 멀티팩터 스코어러
    - CrossAssetMomentumDivergence: 군집 효과 방지
    """

    def __init__(
        self,
        data: 'DataHandlerV70',
        enable_gnn: bool = True,
        enable_tda: bool = True,
        enable_icir: bool = True,
        enable_alpha_scorer: bool = True,
        enable_cross_asset: bool = True,
        enable_theme_conc: bool = False,
        pead_mode: str = 'ensemble',
    ):
        self.data = data
        self.pead_mode = pead_mode
        self.enable_theme_conc = enable_theme_conc

        # GNN 스필오버
        gnn = GNNLiteSpillover(data.returns, data.prices) if enable_gnn else None

        # 팩터 계산기
        self.factor_calc = MultiFactorCalculatorV70(
            data.returns, data.prices,
            volumes=data.volumes, opens=data.opens,
            symbols=data.symbols, gnn_spillover=gnn,
        )

        # TDA
        self.tda = TDALiteCalculator(data.returns) if enable_tda else None

        # IC/IR 앙상블
        factor_names = ['trend', 'mean_rev', 'bb_rev', 'gap_rev', 'amihud', 'pead', 'quality', 'gnn_spill', 'sector_rot']
        self.icir = ICIREnsembleV8(len(factor_names)) if enable_icir else None
        self.factor_names = factor_names

        # AlphaScorerV5
        self.alpha_scorer = AlphaScorerV5() if enable_alpha_scorer else None

        # CrossAsset 다이버전스
        self.cross_asset = CrossAssetMomentumDivergenceV70() if enable_cross_asset else None

        # 이전 팩터 스코어 (IC/IR 업데이트용)
        self._prev_factor_scores: Optional[Dict[str, np.ndarray]] = None

    def compute_alpha(
        self,
        t: int,
        regime: int,
        top_k: int = 12,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        시점 t에서의 알파 신호 계산
        반환: (weights, debug_info)
        """
        n_assets = len(self.data.symbols)
        debug = {}

        # IC/IR 업데이트 (이전 팩터 스코어 + 현재 실현 수익률)
        if self.icir is not None and self._prev_factor_scores is not None and t > 0:
            realized_ret = self.data.returns[t - 1]
            self.icir.update(self._prev_factor_scores, realized_ret)

        # 팩터 스코어 계산
        factor_scores = self.factor_calc.compute_all_scores(
            t, regime=regime, pead_mode=self.pead_mode,
            enable_theme_conc=self.enable_theme_conc,
        )
        self._prev_factor_scores = factor_scores

        # 팩터 가중치 결정
        base_weights = REGIME_FACTOR_WEIGHTS.get(regime, REGIME_FACTOR_WEIGHTS[int(RegimeLabel.NORMAL)])

        if self.icir is not None:
            icir_weights_arr = self.icir.compute_weights(self.factor_names)
            icir_weights = {name: float(icir_weights_arr[i]) for i, name in enumerate(self.factor_names)}
            # IC/IR 가중치와 레짐 가중치 블렌딩 (0.4 IC/IR + 0.6 레짐)
            blended = {}
            for fname in self.factor_names:
                blended[fname] = 0.4 * icir_weights.get(fname, 0.0) + 0.6 * base_weights.get(fname, 0.0)
            total_w = sum(blended.values()) + 1e-12
            final_weights = {k: v / total_w for k, v in blended.items()}
            debug['icir_weights'] = icir_weights
        else:
            final_weights = base_weights

        # 종합 알파 스코어
        alpha = np.zeros(n_assets)
        for fname, w in final_weights.items():
            if fname in factor_scores and w > 0:
                alpha += w * factor_scores[fname]

        debug['factor_weights'] = final_weights
        debug['alpha_raw'] = alpha.copy()

        # AlphaScorerV5 멀티플라이어 적용
        if self.alpha_scorer is not None:
            as_mult = self.alpha_scorer.compute_multiplier(self.data.returns, t, regime)
            alpha = alpha * as_mult
            debug['alpha_scorer_mult'] = as_mult

        # Top-K 선택
        valid_mask = np.isfinite(alpha)
        if np.sum(valid_mask) < top_k:
            top_k = max(1, int(np.sum(valid_mask)))

        top_indices = np.argsort(alpha)[-top_k:]
        weights = np.zeros(n_assets)
        weights[top_indices] = alpha[top_indices]

        # 음수 가중치 제거
        weights = np.maximum(weights, 0.0)

        # CrossAsset 다이버전스 조정
        if self.cross_asset is not None and t > 0:
            vix_val = float(self.data.vix[t - 1])
            ca_adj = self.cross_asset.compute_weight_adjustment(self.data.prices, vix_val, t)
            weights = weights * ca_adj
            debug['cross_asset_adj'] = ca_adj

        # 정규화
        total = np.sum(weights)
        if total > 1e-8:
            weights = weights / total
        else:
            # 폴백: 균등 가중치
            weights = np.zeros(n_assets)
            weights[top_indices] = 1.0 / len(top_indices)

        debug['top_k'] = top_k
        debug['n_valid_factors'] = int(np.sum(valid_mask))

        return weights, debug


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 4: 포트폴리오 최적화
# ═════════════════════════════════════════════════════════════════════════════

class HierarchicalRiskParityV70:
    """
    HRP (계층적 리스크 패리티) 최적화기 (v4.37 기반)
    - 상관관계 기반 계층 클러스터링
    - 역분산 가중치
    - 최소 분산 폴백
    """

    def __init__(self, lookback: int = 252, min_periods: int = 63):
        self.lookback = lookback
        self.min_periods = min_periods

    def optimize(self, returns: np.ndarray, t: int, selected_indices: np.ndarray) -> np.ndarray:
        """
        HRP 최적화
        returns: (T, N) 전체 수익률
        t: 현재 시점
        selected_indices: 선택된 자산 인덱스
        반환: (len(selected_indices),) 가중치
        """
        n_sel = len(selected_indices)
        if n_sel <= 1:
            return np.ones(n_sel) / max(n_sel, 1)

        # t-1까지의 데이터만 사용
        start = max(0, t - self.lookback)
        ret_slice = returns[start:t, :][:, selected_indices]
        ret_slice = np.nan_to_num(ret_slice, nan=0.0)

        if ret_slice.shape[0] < self.min_periods:
            return np.ones(n_sel) / n_sel

        try:
            # 공분산 행렬
            cov = np.cov(ret_slice.T)
            if cov.ndim == 0:
                cov = np.array([[float(cov)]])

            # 상관관계 행렬
            std = np.sqrt(np.diag(cov))
            std = np.where(std < 1e-10, 1e-10, std)
            corr = cov / np.outer(std, std)
            corr = np.clip(corr, -1.0, 1.0)
            np.fill_diagonal(corr, 1.0)

            # 거리 행렬
            dist = np.sqrt(np.clip((1 - corr) / 2, 0.0, 1.0))

            # 계층 클러스터링
            dist_condensed = dist[np.triu_indices(n_sel, k=1)]
            link = linkage(dist_condensed, method='single')

            # 직렬화 순서
            order = self._get_quasi_diag(link, n_sel)

            # 재귀적 이분법
            weights = self._recursive_bisection(cov, order)
            weights = np.maximum(weights, 0.0)
            total = np.sum(weights)
            if total > 1e-8:
                return weights / total
            else:
                return np.ones(n_sel) / n_sel

        except Exception as e:
            logger.debug(f"[HRP] 최적화 실패, 역분산 폴백: {e}")
            # 역분산 폴백
            var = np.var(ret_slice, axis=0)
            var = np.where(var < 1e-12, 1e-12, var)
            inv_var = 1.0 / var
            return inv_var / np.sum(inv_var)

    def _get_quasi_diag(self, link: np.ndarray, n: int) -> List[int]:
        """계층 클러스터링 결과에서 직렬화 순서 추출"""
        link = link.astype(int)
        sort_ix = pd.Series([n * 2 - 2])
        while sort_ix.max() >= n:
            sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
            df0 = sort_ix[sort_ix >= n]
            i = df0.index
            j = df0.values - n
            sort_ix[i] = link[j, 0]
            df0 = pd.Series(link[j, 1], index=i + 1)
            sort_ix = pd.concat([sort_ix, df0]).sort_index()
            sort_ix.index = range(sort_ix.shape[0])
        return sort_ix.tolist()

    def _recursive_bisection(self, cov: np.ndarray, sort_ix: List[int]) -> np.ndarray:
        """재귀적 이분법으로 가중치 계산"""
        w = pd.Series(1.0, index=sort_ix)
        c_items = [sort_ix]

        while len(c_items) > 0:
            c_items = [
                i[j:k]
                for i in c_items
                for j, k in ((0, len(i) // 2), (len(i) // 2, len(i)))
                if len(i) > 1
            ]
            for i in range(0, len(c_items), 2):
                if i + 1 >= len(c_items):
                    break
                c_items_0 = c_items[i]
                c_items_1 = c_items[i + 1]

                alpha = 1 - self._cluster_var(cov, c_items_0) / (
                    self._cluster_var(cov, c_items_0) + self._cluster_var(cov, c_items_1) + 1e-12
                )
                alpha = float(np.clip(alpha, 0.0, 1.0))

                w[c_items_0] *= alpha
                w[c_items_1] *= (1 - alpha)

        return w.values

    @staticmethod
    def _cluster_var(cov: np.ndarray, c_items: List[int]) -> float:
        """클러스터 분산 계산"""
        cov_slice = cov[np.ix_(c_items, c_items)]
        w = np.ones(len(c_items)) / len(c_items)
        return float(w @ cov_slice @ w)


class QPOptimizerV70:
    """
    QP (이차 계획법) 포트폴리오 최적화기
    - 최대 샤프 비율 최적화
    - 제약 조건: 개별 자산 상한, 레버리지 제한
    - Ledoit-Wolf 공분산 추정 (sklearn 사용 가능 시)
    """

    def __init__(
        self,
        lookback: int = 252,
        min_periods: int = 63,
        risk_free: float = 0.05 / 252,
        per_asset_cap: float = 0.08,
        max_leverage: float = 1.5,
    ):
        self.lookback = lookback
        self.min_periods = min_periods
        self.risk_free = risk_free
        self.per_asset_cap = per_asset_cap
        self.max_leverage = max_leverage

    def optimize(
        self,
        returns: np.ndarray,
        t: int,
        selected_indices: np.ndarray,
        alpha_weights: np.ndarray,
    ) -> np.ndarray:
        """
        QP 최적화 (최대 샤프)
        alpha_weights: 알파 신호 기반 초기 가중치 (정규화된)
        """
        n_sel = len(selected_indices)
        if n_sel <= 1:
            return alpha_weights

        start = max(0, t - self.lookback)
        ret_slice = returns[start:t, :][:, selected_indices]
        ret_slice = np.nan_to_num(ret_slice, nan=0.0)

        if ret_slice.shape[0] < self.min_periods:
            return alpha_weights

        try:
            # 공분산 추정
            if SKLEARN_AVAILABLE and n_sel >= 3:
                lw = LedoitWolf()
                lw.fit(ret_slice)
                cov = lw.covariance_
            else:
                cov = np.cov(ret_slice.T)
                if cov.ndim == 0:
                    cov = np.array([[float(cov)]])

            # 기대 수익률 (알파 신호 기반)
            mu = alpha_weights * 0.001  # 알파 신호를 수익률 예측으로 변환

            # 최적화
            def neg_sharpe(w):
                port_ret = float(np.dot(w, mu))
                port_var = float(w @ cov @ w)
                if port_var < 1e-12:
                    return 0.0
                return -(port_ret - self.risk_free) / np.sqrt(port_var)

            # 제약 조건
            constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]
            bounds = [(0.0, self.per_asset_cap)] * n_sel

            w0 = alpha_weights.copy()
            w0 = np.maximum(w0, 0.0)
            total = np.sum(w0)
            if total > 1e-8:
                w0 = w0 / total
            else:
                w0 = np.ones(n_sel) / n_sel

            result = minimize(
                neg_sharpe, w0,
                method='SLSQP',
                bounds=bounds,
                constraints=constraints,
                options={'maxiter': 200, 'ftol': 1e-9}
            )

            if result.success:
                w_opt = np.maximum(result.x, 0.0)
                total = np.sum(w_opt)
                if total > 1e-8:
                    return w_opt / total

        except Exception as e:
            logger.debug(f"[QPOptimizer] 최적화 실패, 알파 가중치 사용: {e}")

        return alpha_weights


class PortfolioConstructorV70:
    """
    v7.0 포트폴리오 구성기
    - HRP + QP 블렌딩
    - 개별 자산 상한 적용
    - 레버리지 제어
    """

    def __init__(
        self,
        per_asset_cap: float = 0.08,
        max_leverage: float = 1.5,
        min_leverage: float = 0.70,
        hrp_weight: float = 0.5,
        qp_weight: float = 0.5,
    ):
        self.per_asset_cap = per_asset_cap
        self.max_leverage = max_leverage
        self.min_leverage = min_leverage
        self.hrp_weight = hrp_weight
        self.qp_weight = qp_weight

        self.hrp = HierarchicalRiskParityV70()
        self.qp = QPOptimizerV70(per_asset_cap=per_asset_cap, max_leverage=max_leverage)

    def construct(
        self,
        returns: np.ndarray,
        t: int,
        alpha_weights: np.ndarray,
        top_k: int = 12,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        포트폴리오 구성
        alpha_weights: (N,) 알파 신호 기반 가중치
        반환: (N,) 최종 가중치, debug_info
        """
        n_assets = len(alpha_weights)
        debug = {}

        # Top-K 선택
        nonzero = np.where(alpha_weights > 1e-8)[0]
        if len(nonzero) == 0:
            return np.zeros(n_assets), {'error': 'no_alpha'}

        selected = nonzero[:min(top_k, len(nonzero))]
        alpha_sel = alpha_weights[selected]
        alpha_sel = alpha_sel / (np.sum(alpha_sel) + 1e-12)

        # HRP 최적화
        hrp_weights = self.hrp.optimize(returns, t, selected)

        # QP 최적화
        qp_weights = self.qp.optimize(returns, t, selected, alpha_sel)

        # 블렌딩
        blended = self.hrp_weight * hrp_weights + self.qp_weight * qp_weights
        blended = np.maximum(blended, 0.0)
        total = np.sum(blended)
        if total > 1e-8:
            blended = blended / total
        else:
            blended = np.ones(len(selected)) / len(selected)

        # 개별 자산 상한 적용
        blended = np.clip(blended, 0.0, self.per_asset_cap)
        total = np.sum(blended)
        if total > 1e-8:
            blended = blended / total

        # 전체 포트폴리오 가중치
        weights = np.zeros(n_assets)
        weights[selected] = blended

        debug['selected_assets'] = selected.tolist()
        debug['n_selected'] = len(selected)
        debug['hrp_weights'] = hrp_weights.tolist()
        debug['qp_weights'] = qp_weights.tolist()

        return weights, debug


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 5: 사전 거래 제어 (Pre-Trade Controller)
# ═════════════════════════════════════════════════════════════════════════════

class VolatilityTargetingV70:
    """
    변동성 타겟팅 (v7.0)
    - EWMA 변동성 추정
    - 레버리지 조정
    - 최소/최대 레버리지 제한
    """

    def __init__(
        self,
        vol_target: float = 0.20,
        ewma_lambda: float = 0.94,
        max_leverage: float = 1.5,
        min_leverage: float = 0.70,
        lookback: int = 21,
    ):
        self.vol_target = vol_target
        self.ewma_lambda = ewma_lambda
        self.max_leverage = max_leverage
        self.min_leverage = min_leverage
        self.lookback = lookback
        self._ewma_var: float = (vol_target / np.sqrt(252)) ** 2

    def compute_leverage(self, port_returns: np.ndarray, t: int) -> float:
        """
        시점 t에서의 레버리지 계산
        port_returns: 포트폴리오 일별 수익률 (1D)
        """
        if t < 5:
            return 1.0

        # EWMA 분산 업데이트
        recent_ret = port_returns[max(0, t - self.lookback):t]
        recent_ret = recent_ret[np.isfinite(recent_ret)]

        if len(recent_ret) >= 2:
            for r in recent_ret:
                self._ewma_var = self.ewma_lambda * self._ewma_var + (1 - self.ewma_lambda) * r ** 2

        ewma_vol = np.sqrt(self._ewma_var * 252)
        if ewma_vol < 1e-6:
            return 1.0

        leverage = self.vol_target / ewma_vol
        return float(np.clip(leverage, self.min_leverage, self.max_leverage))


class AlmgrenChrissModelV70:
    """
    Almgren-Chriss 시장충격 비용 모델 (v7.0)
    - 선형 + 비선형 충격 비용
    - 일중 거래량 기반 충격 추정
    """

    def __init__(
        self,
        eta: float = 2.5e-7,    # 임시 충격 계수
        gamma: float = 2.5e-7,  # 영구 충격 계수
        sigma: float = 0.02,    # 일별 변동성
        adv_fraction: float = 0.1,  # 일평균거래량 대비 최대 거래 비율
    ):
        self.eta = eta
        self.gamma = gamma
        self.sigma = sigma
        self.adv_fraction = adv_fraction

    def compute_impact_cost(
        self,
        trade_size: float,  # 거래 수량 (주식 수)
        adv: float,         # 일평균거래량
        price: float,       # 현재 가격
        vol: float = None,  # 개별 변동성 (없으면 기본값 사용)
    ) -> float:
        """
        거래 충격 비용 계산 (달러 기준)
        """
        if adv < 1 or price < 1e-6:
            return 0.0

        sigma = vol if vol is not None and vol > 0 else self.sigma
        participation = abs(trade_size) / (adv + 1e-8)
        participation = min(participation, self.adv_fraction)

        # 임시 충격 (비선형)
        temp_impact = self.eta * sigma * np.sqrt(participation) * abs(trade_size) * price

        # 영구 충격 (선형)
        perm_impact = self.gamma * sigma * participation * abs(trade_size) * price

        return float(temp_impact + perm_impact)

    def compute_portfolio_cost(
        self,
        trades: np.ndarray,    # (N,) 거래 수량
        prices: np.ndarray,    # (N,) 현재 가격
        volumes: np.ndarray,   # (N,) 일평균거래량
        vols: Optional[np.ndarray] = None,  # (N,) 개별 변동성
    ) -> Tuple[float, np.ndarray]:
        """
        포트폴리오 전체 거래 비용 계산
        반환: (총 비용, 자산별 비용)
        """
        n = len(trades)
        costs = np.zeros(n)
        for i in range(n):
            if abs(trades[i]) > 1e-8:
                vol_i = float(vols[i]) if vols is not None and i < len(vols) else None
                costs[i] = self.compute_impact_cost(
                    float(trades[i]), float(volumes[i]), float(prices[i]), vol_i
                )
        return float(np.sum(costs)), costs


class PreTradeControllerV70:
    """
    v7.0 사전 거래 제어기
    - 변동성 타겟팅
    - CVaR 오버레이
    - EVT + 엔트로피 통합 리스크 멀티플라이어
    - Almgren-Chriss 시장충격 비용
    - 턴오버 제한
    - 낙폭 정책
    """

    def __init__(
        self,
        vol_target: float = 0.20,
        ewma_lambda: float = 0.94,
        max_leverage: float = 1.5,
        min_leverage: float = 0.70,
        cost_bps: float = 30.0,
        max_turnover_per_rebal: float = 0.30,
        drawdown_halt_threshold: float = -0.20,
        drawdown_reduce_threshold: float = -0.10,
    ):
        self.vol_targeting = VolatilityTargetingV70(
            vol_target=vol_target, ewma_lambda=ewma_lambda,
            max_leverage=max_leverage, min_leverage=min_leverage,
        )
        self.ac_model = AlmgrenChrissModelV70()
        self.cost_bps = cost_bps
        self.max_turnover = max_turnover_per_rebal
        self.drawdown_halt = drawdown_halt_threshold
        self.drawdown_reduce = drawdown_reduce_threshold

        # 낙폭 추적
        self._peak_equity: float = 1.0
        self._current_equity: float = 1.0
        self._daily_returns: List[float] = []
        self._halt_active: bool = False
        self._halt_recovery_days: int = 0
        self._halt_recovery_required: int = 10  # 낙폭 횟 회복에 필요한 최소 일수

    def update_equity(self, daily_return: float):
        """일별 수익률로 자산 가치 업데이트"""
        self._daily_returns.append(daily_return)
        self._current_equity *= (1 + daily_return)
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

    def get_drawdown(self) -> float:
        """
        현재 낙폭 (롤링 252일 기준)
        전체 피크 대비가 아닌, 최근 252일 내 피크 대비 낙폭
        """
        if len(self._daily_returns) < 5:
            return 0.0
        # 롤링 252일 내 낙폭
        window = min(252, len(self._daily_returns))
        recent_rets = np.array(self._daily_returns[-window:])
        cum = np.cumprod(1 + recent_rets)
        peak = np.maximum.accumulate(cum)
        dd_arr = (cum - peak) / (peak + 1e-12)
        return float(np.min(dd_arr))

    def apply_controls(
        self,
        target_weights: np.ndarray,
        current_weights: np.ndarray,
        port_returns: np.ndarray,
        risk_multipliers: Dict[str, float],
        t: int,
        prices: Optional[np.ndarray] = None,
        volumes: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        사전 거래 제어 적용
        target_weights: (N,) 목표 가중치
        current_weights: (N,) 현재 가중치
        risk_multipliers: {'combined_mult': float, 'cvar_scale': float, ...}
        반환: (조정된 가중치, debug_info)
        """
        debug = {}
        n_assets = len(target_weights)

        # 1. 낙폭 정송
        dd = self.get_drawdown()
        debug['drawdown'] = dd

        # 낙폭 복구 로직 (v7.0 개선)
        # 낙폭 횟 회복: 피크로부터 일정 일수 후 자동 재진입 (절대 낙폭 기준)
        if self._halt_active:
            self._halt_recovery_days += 1
            # 20일 동안 현금 유지 후 자동 재진입 (dd가 회복되지 않아도)
            if self._halt_recovery_days >= 20:
                self._halt_active = False
                self._halt_recovery_days = 0
                logger.info(f"[PreTrade] 20일 후 자동 재진입 허용 (dd={dd:.2%})")
            else:
                return np.zeros(n_assets), {'drawdown_halt': True, 'drawdown': dd, 'recovery_days': self._halt_recovery_days}

        # 낙폭 정지: -20% 이하일 때만 발동 (v7.0 조정: 더 엄격한 기준)
        if dd <= self.drawdown_halt and not self._halt_active:
            logger.warning(f"[PreTrade] 낙폭 정송 발동: {dd:.2%} — 20일간 현금화")
            self._halt_active = True
            self._halt_recovery_days = 0
            return np.zeros(n_assets), {'drawdown_halt': True, 'drawdown': dd}

        drawdown_mult = 1.0
        if dd <= self.drawdown_reduce:
            drawdown_mult = float(np.clip(1.0 + dd / self.drawdown_halt, 0.3, 1.0))
            debug['drawdown_reduce_mult'] = drawdown_mult

        # 2. 변동성 타겟팅 레버리지
        leverage = self.vol_targeting.compute_leverage(port_returns, t)
        debug['vol_leverage'] = leverage

        # 3. 리스크 멀티플라이어 통합
        combined_mult = float(risk_multipliers.get('combined_mult', 1.0))
        final_leverage = float(np.clip(leverage * combined_mult * drawdown_mult, 0.0, 1.5))
        debug['final_leverage'] = final_leverage

        # 4. 가중치 스케일링
        scaled_weights = target_weights * final_leverage

        # 5. 턴오버 제한
        turnover = float(np.sum(np.abs(scaled_weights - current_weights))) / 2.0
        debug['turnover'] = turnover
        if turnover > self.max_turnover:
            # 점진적 조정
            blend_ratio = self.max_turnover / (turnover + 1e-8)
            blend_ratio = float(np.clip(blend_ratio, 0.0, 1.0))
            scaled_weights = blend_ratio * scaled_weights + (1 - blend_ratio) * current_weights
            debug['turnover_blend'] = blend_ratio

        # 6. Almgren-Chriss 비용 추정 (로깅용)
        if prices is not None and volumes is not None:
            trades = scaled_weights - current_weights
            total_cost, _ = self.ac_model.compute_portfolio_cost(
                trades, prices, volumes
            )
            debug['ac_cost_estimate'] = total_cost

        return scaled_weights, debug


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 6: ARES v7.0 ULTIMATE 메인 엔진
# ═════════════════════════════════════════════════════════════════════════════

# 프로파일 정의
PROFILES = {
    'conservative': {
        'top_k': 8, 'per_asset_cap': 0.08, 'vol_target': 0.12,
        'max_leverage': 1.0, 'min_leverage': 0.50, 'cost_bps': 30.0,
        'rebal_normal': 15, 'rebal_crisis': 5, 'kelly_cap': 0.25,
    },
    'balanced': {
        'top_k': 10, 'per_asset_cap': 0.08, 'vol_target': 0.16,
        'max_leverage': 1.2, 'min_leverage': 0.60, 'cost_bps': 30.0,
        'rebal_normal': 10, 'rebal_crisis': 5, 'kelly_cap': 0.30,
    },
    'aggressive': {
        'top_k': 12, 'per_asset_cap': 0.08, 'vol_target': 0.20,
        'max_leverage': 1.5, 'min_leverage': 0.70, 'cost_bps': 30.0,
        'rebal_normal': 10, 'rebal_crisis': 5, 'kelly_cap': 0.35,
    },
}


class ARESv70Ultimate:
    """
    ARES v7.0 ULTIMATE — 4-AI 합의 프로덕션 엔진

    [6-Layer 파이프라인]
    L1: DataHandlerV70 — DB 로딩, 정제
    L2: SystemicRiskEngineV70 — EVT + Entropy + CVaR + 레짐
    L3: AlphaSuiteV70 — 9팩터 + AlphaScorerV5 + CrossAsset + sector_rot
    L4: PortfolioConstructorV70 — HRP + QP 블렌딩
    L5: PreTradeControllerV70 — VolTarget + Almgren-Chriss + 낙폭 정책
    L6: 백테스트 루프 + 성과 분석

    [v7.0 신규 통합]
    - EVTRiskManagerV70: GPD + Cornish-Fisher 폴백
    - CVaRTargetingOverlayV70: VIX/레짐 게이팅
    - EntropyRegimeFilterV70: Shannon + Permutation 엔트로피
    - CrossAssetMomentumDivergenceV70: 군집 효과 방지
    - AlphaScorerV5: IC/IR 앙상블 기반 레짐 조건부 스코어러
    - sector_rot 팩터: 헬스케어/필수소비재 vs 성장주
    - ICIREnsembleV8: 동적 팩터 가중치
    - WalkForwardBacktestV70: 최신 WF 프레임워크
    """

    def __init__(
        self,
        db_path: str,
        profile: str = 'aggressive',
        pead_mode: str = 'ensemble',
        warmup_days: int = 756,
        purge_days: int = 10,
        enable_gnn: bool = True,
        enable_tda: bool = True,
        enable_icir: bool = True,
        enable_alpha_scorer: bool = True,
        enable_cross_asset: bool = True,
        enable_theme_conc: bool = False,
        enable_evt: bool = True,
        enable_entropy: bool = True,
        enable_cvar: bool = True,
        save_daily_logs: Optional[str] = None,
    ):
        prof = PROFILES.get(profile, PROFILES['aggressive'])

        self.db_path = db_path
        self.profile_name = profile
        self.warmup_days = warmup_days
        self.purge_days = purge_days
        self.top_k = prof['top_k']
        self.per_asset_cap = prof['per_asset_cap']
        self.vol_target = prof['vol_target']
        self.max_leverage = prof['max_leverage']
        self.min_leverage = prof['min_leverage']
        self.cost_bps = prof['cost_bps']
        self.rebal_normal = prof['rebal_normal']
        self.rebal_crisis = prof['rebal_crisis']
        self.kelly_cap = prof['kelly_cap']
        self.save_daily_logs = save_daily_logs

        # L1: 데이터 핸들러
        self.data = DataHandlerV70(db_path)
        self.data.load()

        # L2: 시스템 리스크 엔진
        evt_cfg = EVTConfig() if enable_evt else None
        entropy_cfg = EntropyConfig() if enable_entropy else None
        cvar_cfg = CVaROverlayConfig() if enable_cvar else None
        self.risk_engine = SystemicRiskEngineV70(evt_cfg, entropy_cfg, cvar_cfg)

        # L3: 알파 스위트
        self.alpha_suite = AlphaSuiteV70(
            self.data,
            enable_gnn=enable_gnn,
            enable_tda=enable_tda,
            enable_icir=enable_icir,
            enable_alpha_scorer=enable_alpha_scorer,
            enable_cross_asset=enable_cross_asset,
            enable_theme_conc=enable_theme_conc,
            pead_mode=pead_mode,
        )

        # L4: 포트폴리오 구성기
        self.portfolio = PortfolioConstructorV70(
            per_asset_cap=self.per_asset_cap,
            max_leverage=self.max_leverage,
            min_leverage=self.min_leverage,
        )

        # L5: 사전 거래 제어기
        self.pretrade = PreTradeControllerV70(
            vol_target=self.vol_target,
            max_leverage=self.max_leverage,
            min_leverage=self.min_leverage,
            cost_bps=self.cost_bps,
        )

        # 레짐 피처 사전 계산
        self._features = self.risk_engine.compute_features(self.data)

        # 상태 변수
        self._current_weights = np.zeros(len(self.data.symbols))
        self._port_returns: List[float] = []
        self._regime_history: List[int] = []
        self._last_rebal_t: int = 0

        logger.info(
            f"[ARESv70] 초기화 완료 | 프로파일: {profile} | "
            f"자산: {len(self.data.symbols)} | 기간: {self.data.dates[0]}~{self.data.dates[-1]}"
        )

    def _should_rebalance(self, t: int, regime: int) -> bool:
        """리밸런싱 여부 결정"""
        rebal_freq = self.rebal_crisis if regime in (0, 1, 5, 6) else self.rebal_normal
        return (t - self._last_rebal_t) >= rebal_freq

    def run_backtest(self) -> Dict[str, Any]:
        """
        전체 백테스트 실행
        반환: 성과 지표 딕셔너리
        """
        T = len(self.data.returns)
        n_assets = len(self.data.symbols)

        portfolio_values = [1.0]
        daily_returns_list = []
        weights_history = []
        regime_history = []
        risk_mult_history = []
        debug_logs = []

        current_weights = np.zeros(n_assets)
        port_returns_arr = np.zeros(T)

        logger.info(f"[ARESv70] 백테스트 시작 | 워밍업: {self.warmup_days}일 | 총 기간: {T}일")

        for t in range(self.warmup_days, T):
            # ── L2: 리스크 멀티플라이어 계산 ──
            risk_mults = self.risk_engine.compute_all_risk_multipliers(
                t, self._features, port_returns_arr, self.data.vix
            )
            regime = risk_mults['regime']
            self._regime_history.append(regime)
            regime_history.append(regime)

            # ── 리밸런싱 여부 ──
            if self._should_rebalance(t, regime):
                # ── L3: 알파 계산 ──
                alpha_weights, alpha_debug = self.alpha_suite.compute_alpha(
                    t, regime, top_k=self.top_k
                )

                # ── L4: 포트폴리오 구성 ──
                target_weights, port_debug = self.portfolio.construct(
                    self.data.returns, t, alpha_weights, top_k=self.top_k
                )

                # ── L5: 사전 거래 제어 ──
                prices_t = self.data.prices[t - 1] if t > 0 else None
                volumes_t = self.data.volumes[t - 1] if self.data.volumes is not None and t > 0 else None

                controlled_weights, pretrade_debug = self.pretrade.apply_controls(
                    target_weights, current_weights,
                    port_returns_arr[:t], risk_mults, t,
                    prices=prices_t, volumes=volumes_t,
                )

                # 낙폭 정책 발동 시 현금화
                if pretrade_debug.get('drawdown_halt', False):
                    current_weights = np.zeros(n_assets)
                else:
                    current_weights = controlled_weights

                self._last_rebal_t = t

                if self.save_daily_logs:
                    debug_logs.append({
                        'date': str(self.data.dates[t]),
                        't': t,
                        'regime': REGIME_NAMES.get(regime, str(regime)),
                        'risk_mults': {k: round(v, 4) for k, v in risk_mults.items() if isinstance(v, float)},
                        'n_positions': int(np.sum(current_weights > 1e-4)),
                        'leverage': round(float(np.sum(current_weights)), 4),
                        'turnover': round(pretrade_debug.get('turnover', 0.0), 4),
                    })

            # ── 수익률 계산 ──
            day_ret = self.data.returns[t]
            port_ret = float(np.nansum(current_weights * day_ret))

            # 거래 비용 차감
            if t > 0:
                turnover = float(np.sum(np.abs(current_weights - weights_history[-1] if weights_history else current_weights))) / 2.0
                cost = turnover * self.cost_bps / 10000.0
                port_ret -= cost

            port_returns_arr[t] = port_ret
            self._port_returns.append(port_ret)
            self.pretrade.update_equity(port_ret)

            daily_returns_list.append(port_ret)
            weights_history.append(current_weights.copy())
            risk_mult_history.append(risk_mults)

            portfolio_values.append(portfolio_values[-1] * (1 + port_ret))

        # ── 성과 분석 ──
        results = self._compute_performance(
            np.array(daily_returns_list),
            np.array(portfolio_values),
            regime_history,
        )

        # 일별 로그 저장
        if self.save_daily_logs and debug_logs:
            import json
            with open(self.save_daily_logs, 'w') as f:
                json.dump(debug_logs, f, indent=2, default=str)
            logger.info(f"[ARESv70] 일별 로그 저장: {self.save_daily_logs}")

        return results

    def _compute_performance(
        self,
        returns: np.ndarray,
        portfolio_values: np.ndarray,
        regime_history: List[int],
    ) -> Dict[str, Any]:
        """성과 지표 계산"""
        returns = returns[np.isfinite(returns)]
        if len(returns) == 0:
            return {'error': 'no_returns'}

        ann_ret = float(np.mean(returns) * 252)
        ann_vol = float(np.std(returns) * np.sqrt(252))
        sharpe = ann_ret / (ann_vol + 1e-8)

        # 최대 낙폭
        cum = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / (peak + 1e-8)
        max_dd = float(np.min(dd))
        calmar = ann_ret / (abs(max_dd) + 1e-8)

        # 소르티노
        downside = returns[returns < 0]
        downside_vol = float(np.std(downside) * np.sqrt(252)) if len(downside) > 0 else 1e-8
        sortino = ann_ret / (downside_vol + 1e-8)

        # 월별 수익률
        n_months = max(1, len(returns) // 21)
        monthly_rets = [float(np.sum(returns[i * 21:(i + 1) * 21])) for i in range(n_months)]
        win_rate = float(np.mean([r > 0 for r in monthly_rets]))

        # 레짐별 성과
        regime_perf = {}
        for reg_id in range(7):
            reg_mask = np.array([r == reg_id for r in regime_history[:len(returns)]])
            if np.sum(reg_mask) > 0:
                reg_rets = returns[reg_mask]
                regime_perf[REGIME_NAMES.get(reg_id, str(reg_id))] = {
                    'ann_ret': round(float(np.mean(reg_rets) * 252), 4),
                    'ann_vol': round(float(np.std(reg_rets) * np.sqrt(252)), 4),
                    'n_days': int(np.sum(reg_mask)),
                }

        results = {
            'ann_ret': round(ann_ret, 4),
            'ann_vol': round(ann_vol, 4),
            'sharpe': round(sharpe, 4),
            'max_dd': round(max_dd, 4),
            'calmar': round(calmar, 4),
            'sortino': round(sortino, 4),
            'win_rate_monthly': round(win_rate, 4),
            'total_return': round(float(portfolio_values[-1] - 1.0), 4),
            'n_trading_days': len(returns),
            'regime_performance': regime_perf,
        }

        logger.info(
            f"\n{'='*60}\n"
            f"[ARESv70 백테스트 결과]\n"
            f"  연간 수익률:  {ann_ret:.2%}\n"
            f"  연간 변동성:  {ann_vol:.2%}\n"
            f"  샤프 비율:    {sharpe:.3f}\n"
            f"  최대 낙폭:    {max_dd:.2%}\n"
            f"  칼마 비율:    {calmar:.3f}\n"
            f"  소르티노:     {sortino:.3f}\n"
            f"  월별 승률:    {win_rate:.1%}\n"
            f"  총 수익률:    {portfolio_values[-1] - 1.0:.2%}\n"
            f"{'='*60}"
        )

        return results


# ═════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD BACKTEST V70
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class WalkForwardConfig:
    """Walk-Forward 백테스트 설정"""
    train_window: int = 756      # 훈련 기간 (3년)
    test_window: int = 126       # 테스트 기간 (6개월)
    step_size: int = 63          # 슬라이딩 스텝 (3개월)
    min_train_window: int = 504  # 최소 훈련 기간 (2년)
    purge_days: int = 10         # 훈련/테스트 간 퍼지 기간
    n_jobs: int = 1              # 병렬 처리 수


@dataclass
class WalkForwardFold:
    """단일 WF 폴드 결과"""
    fold_id: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_dates: Tuple[Any, Any]
    test_dates: Tuple[Any, Any]
    oos_returns: np.ndarray
    oos_sharpe: float
    oos_max_dd: float
    oos_ann_ret: float
    oos_ann_vol: float
    regime_dist: Dict[str, int]
    n_days: int


class WalkForwardBacktestV70:
    """
    Walk-Forward 백테스트 v7.0
    - 슬라이딩 윈도우 방식
    - 퍼지 기간으로 데이터 누출 방지
    - 폴드별 성과 분석
    - OOS 통합 성과 계산
    """

    def __init__(self, config: Optional[WalkForwardConfig] = None):
        self.config = config or WalkForwardConfig()

    def run(self, engine: ARESv70Ultimate) -> Dict[str, Any]:
        """
        Walk-Forward 백테스트 실행
        engine: 초기화된 ARESv70Ultimate 인스턴스
        """
        cfg = self.config
        T = len(engine.data.returns)
        features = engine._features

        folds = []
        fold_id = 0

        # 폴드 생성
        test_start = cfg.train_window + cfg.purge_days
        while test_start + cfg.test_window <= T:
            train_start = max(0, test_start - cfg.train_window - cfg.purge_days)
            train_end = test_start - cfg.purge_days
            test_end = min(test_start + cfg.test_window, T)

            if train_end - train_start < cfg.min_train_window:
                test_start += cfg.step_size
                continue

            folds.append({
                'fold_id': fold_id,
                'train_start': train_start,
                'train_end': train_end,
                'test_start': test_start,
                'test_end': test_end,
            })
            fold_id += 1
            test_start += cfg.step_size

        logger.info(f"[WalkForward] {len(folds)}개 폴드 생성")

        # 폴드별 백테스트 실행
        fold_results = []
        for fold in folds:
            result = self._run_fold(engine, fold, features)
            fold_results.append(result)
            logger.info(
                f"[WalkForward] Fold {result.fold_id}: "
                f"OOS Sharpe={result.oos_sharpe:.3f}, "
                f"MaxDD={result.oos_max_dd:.2%}, "
                f"AnnRet={result.oos_ann_ret:.2%}"
            )

        # 통합 OOS 성과
        all_oos_returns = np.concatenate([f.oos_returns for f in fold_results])
        summary = self._compute_oos_summary(fold_results, all_oos_returns)

        return {
            'folds': fold_results,
            'summary': summary,
            'n_folds': len(fold_results),
        }

    def _run_fold(
        self,
        engine: ARESv70Ultimate,
        fold: Dict[str, Any],
        features: Dict[str, np.ndarray],
    ) -> WalkForwardFold:
        """단일 폴드 OOS 백테스트"""
        fold_id = fold['fold_id']
        train_start = fold['train_start']
        train_end = fold['train_end']
        test_start = fold['test_start']
        test_end = fold['test_end']

        T_test = test_end - test_start
        n_assets = len(engine.data.symbols)

        oos_returns = np.zeros(T_test)
        current_weights = np.zeros(n_assets)
        port_returns_arr = np.zeros(test_end)
        last_rebal_t = test_start

        # 리스크 엔진 상태 초기화 (폴드별 독립)
        risk_engine = SystemicRiskEngineV70()

        for t_idx, t in enumerate(range(test_start, test_end)):
            # 리스크 멀티플라이어
            risk_mults = risk_engine.compute_all_risk_multipliers(
                t, features, port_returns_arr, engine.data.vix
            )
            regime = risk_mults['regime']

            # 리밸런싱
            rebal_freq = engine.rebal_crisis if regime in (0, 1, 5, 6) else engine.rebal_normal
            if (t - last_rebal_t) >= rebal_freq:
                alpha_weights, _ = engine.alpha_suite.compute_alpha(t, regime, top_k=engine.top_k)
                target_weights, _ = engine.portfolio.construct(engine.data.returns, t, alpha_weights, top_k=engine.top_k)
                controlled_weights, pretrade_debug = engine.pretrade.apply_controls(
                    target_weights, current_weights, port_returns_arr[:t], risk_mults, t
                )
                if not pretrade_debug.get('drawdown_halt', False):
                    current_weights = controlled_weights
                last_rebal_t = t

            # 수익률 계산
            day_ret = engine.data.returns[t]
            port_ret = float(np.nansum(current_weights * day_ret))
            turnover = float(np.sum(np.abs(current_weights))) * 0.01  # 단순 비용 추정
            port_ret -= turnover * engine.cost_bps / 10000.0

            port_returns_arr[t] = port_ret
            oos_returns[t_idx] = port_ret

        # 폴드 성과 계산
        valid_rets = oos_returns[np.isfinite(oos_returns)]
        if len(valid_rets) == 0:
            ann_ret, ann_vol, sharpe, max_dd = 0.0, 0.0, 0.0, 0.0
        else:
            ann_ret = float(np.mean(valid_rets) * 252)
            ann_vol = float(np.std(valid_rets) * np.sqrt(252))
            sharpe = ann_ret / (ann_vol + 1e-8)
            cum = np.cumprod(1 + valid_rets)
            peak = np.maximum.accumulate(cum)
            dd = (cum - peak) / (peak + 1e-8)
            max_dd = float(np.min(dd))

        # 레짐 분포
        regime_dist = {}
        for reg_id in range(7):
            name = REGIME_NAMES.get(reg_id, str(reg_id))
            regime_dist[name] = 0  # 폴드별 레짐 카운트는 별도 추적 필요

        return WalkForwardFold(
            fold_id=fold_id,
            train_start=train_start, train_end=train_end,
            test_start=test_start, test_end=test_end,
            train_dates=(engine.data.dates[train_start], engine.data.dates[train_end - 1]),
            test_dates=(engine.data.dates[test_start], engine.data.dates[test_end - 1]),
            oos_returns=oos_returns,
            oos_sharpe=sharpe,
            oos_max_dd=max_dd,
            oos_ann_ret=ann_ret,
            oos_ann_vol=ann_vol,
            regime_dist=regime_dist,
            n_days=T_test,
        )

    def _compute_oos_summary(
        self,
        folds: List[WalkForwardFold],
        all_oos_returns: np.ndarray,
    ) -> Dict[str, Any]:
        """OOS 통합 성과 계산"""
        valid = all_oos_returns[np.isfinite(all_oos_returns)]
        if len(valid) == 0:
            return {'error': 'no_oos_returns'}

        ann_ret = float(np.mean(valid) * 252)
        ann_vol = float(np.std(valid) * np.sqrt(252))
        sharpe = ann_ret / (ann_vol + 1e-8)

        cum = np.cumprod(1 + valid)
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / (peak + 1e-8)
        max_dd = float(np.min(dd))
        calmar = ann_ret / (abs(max_dd) + 1e-8)

        downside = valid[valid < 0]
        downside_vol = float(np.std(downside) * np.sqrt(252)) if len(downside) > 0 else 1e-8
        sortino = ann_ret / (downside_vol + 1e-8)

        # 폴드별 샤프 분포
        fold_sharpes = [f.oos_sharpe for f in folds]
        positive_folds = sum(1 for s in fold_sharpes if s > 0)

        summary = {
            'oos_ann_ret': round(ann_ret, 4),
            'oos_ann_vol': round(ann_vol, 4),
            'oos_sharpe': round(sharpe, 4),
            'oos_max_dd': round(max_dd, 4),
            'oos_calmar': round(calmar, 4),
            'oos_sortino': round(sortino, 4),
            'oos_total_return': round(float(cum[-1] - 1.0), 4),
            'oos_n_days': len(valid),
            'n_folds': len(folds),
            'positive_folds': positive_folds,
            'fold_sharpe_mean': round(float(np.mean(fold_sharpes)), 4),
            'fold_sharpe_std': round(float(np.std(fold_sharpes)), 4),
            'fold_sharpe_min': round(float(np.min(fold_sharpes)), 4),
            'fold_sharpe_max': round(float(np.max(fold_sharpes)), 4),
        }

        logger.info(
            f"\n{'='*60}\n"
            f"[WalkForward OOS 통합 성과]\n"
            f"  OOS 연간 수익률:  {ann_ret:.2%}\n"
            f"  OOS 연간 변동성:  {ann_vol:.2%}\n"
            f"  OOS 샤프 비율:    {sharpe:.3f}\n"
            f"  OOS 최대 낙폭:    {max_dd:.2%}\n"
            f"  OOS 칼마 비율:    {calmar:.3f}\n"
            f"  OOS 소르티노:     {sortino:.3f}\n"
            f"  폴드 수:          {len(folds)}\n"
            f"  양수 샤프 폴드:   {positive_folds}/{len(folds)}\n"
            f"  폴드 샤프 평균:   {np.mean(fold_sharpes):.3f} ± {np.std(fold_sharpes):.3f}\n"
            f"{'='*60}"
        )

        return summary


# ═════════════════════════════════════════════════════════════════════════════
# 메인 실행 엔트리포인트
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='ARES v7.0 ULTIMATE — 4-AI Consensus Production Engine')
    parser.add_argument('--db', type=str, default='/home/ubuntu/ares_universal_v2.db',
                        help='SQLite DB 경로')
    parser.add_argument('--profile', type=str, default='aggressive',
                        choices=['conservative', 'balanced', 'aggressive'],
                        help='프로파일 선택')
    parser.add_argument('--mode', type=str, default='backtest',
                        choices=['backtest', 'walkforward'],
                        help='실행 모드')
    parser.add_argument('--warmup', type=int, default=756,
                        help='워밍업 기간 (일)')
    parser.add_argument('--pead-mode', type=str, default='ensemble',
                        choices=['ensemble', 'v431', 'v433'],
                        help='PEAD 모드')
    parser.add_argument('--no-gnn', action='store_true', help='GNN 비활성화')
    parser.add_argument('--no-tda', action='store_true', help='TDA 비활성화')
    parser.add_argument('--no-icir', action='store_true', help='IC/IR 앙상블 비활성화')
    parser.add_argument('--no-evt', action='store_true', help='EVT 비활성화')
    parser.add_argument('--no-entropy', action='store_true', help='엔트로피 필터 비활성화')
    parser.add_argument('--no-cvar', action='store_true', help='CVaR 오버레이 비활성화')
    parser.add_argument('--enable-theme', action='store_true', help='테마 집중 팩터 활성화')
    parser.add_argument('--save-logs', type=str, default=None,
                        help='일별 로그 저장 경로')
    parser.add_argument('--output', type=str, default=None,
                        help='결과 저장 경로 (JSON)')
    args = parser.parse_args()

    logger.info(
        f"\n{'='*70}\n"
        f"  ARES v7.0 ULTIMATE — 4-AI Consensus Production Engine\n"
        f"  DB: {args.db}\n"
        f"  Profile: {args.profile} | Mode: {args.mode}\n"
        f"{'='*70}"
    )

    # 엔진 초기화
    engine = ARESv70Ultimate(
        db_path=args.db,
        profile=args.profile,
        pead_mode=args.pead_mode,
        warmup_days=args.warmup,
        enable_gnn=not args.no_gnn,
        enable_tda=not args.no_tda,
        enable_icir=not args.no_icir,
        enable_alpha_scorer=True,
        enable_cross_asset=True,
        enable_theme_conc=args.enable_theme,
        enable_evt=not args.no_evt,
        enable_entropy=not args.no_entropy,
        enable_cvar=not args.no_cvar,
        save_daily_logs=args.save_logs,
    )

    if args.mode == 'walkforward':
        # Walk-Forward 백테스트
        wf_config = WalkForwardConfig(
            train_window=756,
            test_window=126,
            step_size=63,
            purge_days=10,
        )
        wf = WalkForwardBacktestV70(wf_config)
        results = wf.run(engine)
        final_results = results['summary']
    else:
        # 전체 기간 백테스트
        final_results = engine.run_backtest()

    # 결과 저장
    if args.output:
        import json
        with open(args.output, 'w') as f:
            json.dump(final_results, f, indent=2, default=str)
        logger.info(f"[ARESv70] 결과 저장: {args.output}")

    return final_results


if __name__ == '__main__':
    main()
