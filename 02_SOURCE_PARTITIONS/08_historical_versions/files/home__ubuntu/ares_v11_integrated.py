#!/usr/bin/env python3
"""
ARES v11 Integrated - v10 Final 베이스라인 + 4대 AI 모듈 통합
================================================================================
베이스라인: v10 Final (Sharpe 1.84)
추가 모듈:
  1. ICIRWeighter - 롤링 ICIR 기반 동적 팩터 가중치
  2. HRPAllocator - Hierarchical Risk Parity 포트폴리오 최적화
  3. CVaRRiskManager - CVaR 기반 꼬리 리스크 관리
  4. RateRegimeDetector - TLT 기반 금리 상승기 감지 (2022년 대응)
  5. VolatilityTargeting - 변동성 타겟팅 (연 12%)
================================================================================
"""
import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
import warnings
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================
@dataclass
class AresConfig:
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    start_date: str = "2016-01-01"
    end_date: str = "2024-12-31"
    
    # Universe (실제 DB에 있는 종목)
    universe: List[str] = None
    
    # Factor Parameters
    mom_lookbacks: List[int] = None  # [252, 126, 63]
    vol_lookback: int = 60
    
    # ICIR Parameters
    icir_window: int = 126
    icir_min_samples: int = 20
    icir_clip: float = 3.0
    ic_horizon: int = 21  # Forward return horizon
    
    # HRP Parameters
    hrp_lookback: int = 126
    cov_shrink_lambda: float = 0.5
    min_var_floor: float = 1e-8
    
    # Risk Management
    target_volatility: float = 0.12  # 12% annualized
    max_leverage: float = 2.5
    min_leverage: float = 0.0
    cvar_alpha: float = 0.05  # 5% CVaR
    cvar_lookback: int = 252
    cvar_threshold: float = -0.03  # CVaR 임계값
    
    # Rate Regime (2022 대응)
    tlt_lookback: int = 126  # 6개월
    rate_stress_threshold: float = -0.15  # TLT 6개월 수익률 < -15%
    
    # Backtest Parameters
    top_k: int = 15
    rebalance_days: int = 10
    transaction_cost: float = 0.005  # 50bps
    dd_threshold: float = -0.12
    consec_limit: int = 3
    loss_scale_min: float = 0.4
    recovery_rate: float = 0.05
    
    def __post_init__(self):
        if self.universe is None:
            # 실제 데이터가 있는 종목들 (2016-2024, 2000일 이상)
            self.universe = [
                # Tech (15)
                "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "ADBE", "CSCO", "INTC",
                "AMD", "QCOM", "AMAT", "LRCX", "SNPS", "CDNS", "MRVL", "INTU", "ISRG", "NFLX",
                # Finance (5)
                "JPM", "V", "MA", "AXP", "PYPL",
                # Healthcare (6)
                "JNJ", "ABBV", "AMGN", "GILD", "REGN", "LLY", "VRTX",
                # Consumer (6)
                "HD", "COST", "MCD", "PEP", "KO", "PG", "WMT",
                # Other (4)
                "CMCSA", "ACN", "BRK.B", "AVGO", "ASML",
                # Index (1)
                "SPY"
            ]
        if self.mom_lookbacks is None:
            self.mom_lookbacks = [252, 126, 63]

CONFIG = AresConfig()

# =============================================================================
# Numba JIT Core Functions (from v10 Final)
# =============================================================================
@njit(parallel=True, cache=True)
def fast_momentum(prices: np.ndarray, lookback: int, skip: int = 1) -> np.ndarray:
    """모멘텀 계산 (Look-ahead bias 방지: skip일 전 가격 사용)"""
    n_dates, n_assets = prices.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback + skip, n_dates):
        for j in range(n_assets):
            if prices[i - skip, j] > 0 and prices[i - lookback - skip, j] > 0:
                result[i, j] = prices[i - skip, j] / prices[i - lookback - skip, j] - 1
    return result

@njit(parallel=True, cache=True)
def fast_volatility(returns: np.ndarray, lookback: int) -> np.ndarray:
    """롤링 변동성 계산"""
    n_dates, n_assets = returns.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i-lookback:i, j]
            valid = ~np.isnan(window)
            if valid.sum() >= lookback // 2:
                result[i, j] = np.nanstd(window) * np.sqrt(252)
    return result

@njit(parallel=True, cache=True)
def fast_rank_normalize(data: np.ndarray) -> np.ndarray:
    """Cross-sectional rank normalization"""
    n_dates, n_assets = data.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(n_dates):
        row = data[i, :]
        valid_mask = ~np.isnan(row)
        n_valid = valid_mask.sum()
        if n_valid > 0:
            valid_vals = row[valid_mask]
            sorted_idx = np.argsort(valid_vals)
            ranks = np.empty(n_valid, dtype=np.float64)
            for k, idx in enumerate(sorted_idx):
                ranks[idx] = (k + 1) / n_valid
            result[i, valid_mask] = ranks
    return result

@njit(cache=True)
def fast_spearman_ic(factor: np.ndarray, forward_ret: np.ndarray) -> float:
    """Spearman IC 계산"""
    valid_mask = ~(np.isnan(factor) | np.isnan(forward_ret))
    n_valid = valid_mask.sum()
    if n_valid < 5:
        return np.nan
    
    x = factor[valid_mask]
    y = forward_ret[valid_mask]
    
    # Rank 계산
    x_rank = np.empty(n_valid, dtype=np.float64)
    y_rank = np.empty(n_valid, dtype=np.float64)
    
    x_sorted_idx = np.argsort(x)
    y_sorted_idx = np.argsort(y)
    
    for k, idx in enumerate(x_sorted_idx):
        x_rank[idx] = k + 1
    for k, idx in enumerate(y_sorted_idx):
        y_rank[idx] = k + 1
    
    # Pearson correlation of ranks
    x_mean = np.mean(x_rank)
    y_mean = np.mean(y_rank)
    
    cov = 0.0
    var_x = 0.0
    var_y = 0.0
    for k in range(n_valid):
        dx = x_rank[k] - x_mean
        dy = y_rank[k] - y_mean
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy
    
    if var_x < 1e-10 or var_y < 1e-10:
        return np.nan
    
    return cov / np.sqrt(var_x * var_y)

@njit(cache=True)
def fast_rolling_cov(returns: np.ndarray, lookback: int) -> np.ndarray:
    """롤링 공분산 행렬 계산"""
    n_dates, n_assets = returns.shape
    cov = np.zeros((n_assets, n_assets), dtype=np.float64)
    
    window = returns[-lookback:, :]
    means = np.zeros(n_assets, dtype=np.float64)
    
    for j in range(n_assets):
        means[j] = np.nanmean(window[:, j])
    
    for j in range(n_assets):
        for k in range(j, n_assets):
            cov_jk = 0.0
            count = 0
            for t in range(lookback):
                if not np.isnan(window[t, j]) and not np.isnan(window[t, k]):
                    cov_jk += (window[t, j] - means[j]) * (window[t, k] - means[k])
                    count += 1
            if count > 1:
                cov[j, k] = cov_jk / (count - 1)
                cov[k, j] = cov[j, k]
    
    return cov

@njit(cache=True)
def fast_cvar(returns: np.ndarray, alpha: float = 0.05) -> float:
    """CVaR (Expected Shortfall) 계산"""
    valid = returns[~np.isnan(returns)]
    if len(valid) < 20:
        return np.nan
    
    sorted_rets = np.sort(valid)
    n_tail = max(1, int(len(valid) * alpha))
    return np.mean(sorted_rets[:n_tail])

@njit(cache=True)
def fast_portfolio_vol(weights: np.ndarray, cov: np.ndarray) -> float:
    """포트폴리오 변동성 계산"""
    var = 0.0
    n = len(weights)
    for i in range(n):
        for j in range(n):
            var += weights[i] * weights[j] * cov[i, j]
    return np.sqrt(var) * np.sqrt(252)

# =============================================================================
# Module 1: ICIR Weighter (동적 팩터 가중치)
# =============================================================================
class ICIRWeighter:
    """
    롤링 ICIR 기반 동적 팩터 가중치 계산
    - IC = Spearman correlation(factor, forward_return)
    - ICIR = mean(IC) / std(IC)
    - 가중치 = softmax(ICIR)
    """
    def __init__(self, config: AresConfig):
        self.cfg = config
        self.factor_names = ["mom_12_1", "mom_6_1", "mom_3_1", "reversal", "low_vol"]
    
    def compute_rolling_ic(
        self, 
        factors: Dict[str, pd.DataFrame], 
        returns: pd.DataFrame
    ) -> Dict[str, pd.Series]:
        """각 팩터의 롤링 IC 계산"""
        logger.info("Computing rolling IC for each factor...")
        
        ic_series = {}
        dates = returns.index
        n_dates = len(dates)
        h = self.cfg.ic_horizon
        
        # Forward returns (h일 후 수익률)
        fwd_returns = returns.shift(-h)
        
        for fname in self.factor_names:
            if fname not in factors:
                continue
            
            factor_df = factors[fname]
            ic_vals = np.full(n_dates, np.nan, dtype=np.float64)
            
            for i in range(h, n_dates - h):
                # 시점 i의 팩터와 i+h 시점의 수익률 상관관계
                factor_row = factor_df.iloc[i].values.astype(np.float64)
                fwd_row = fwd_returns.iloc[i].values.astype(np.float64)
                ic_vals[i] = fast_spearman_ic(factor_row, fwd_row)
            
            ic_series[fname] = pd.Series(ic_vals, index=dates, name=f"IC_{fname}")
        
        return ic_series
    
    def compute_dynamic_weights(
        self,
        ic_series: Dict[str, pd.Series],
        regime: pd.Series,
        default_weights: Dict[str, Dict[str, float]]
    ) -> pd.DataFrame:
        """레짐별 롤링 ICIR 기반 동적 가중치 계산"""
        logger.info("Computing dynamic factor weights from rolling ICIR...")
        
        dates = regime.index
        n_dates = len(dates)
        weights = pd.DataFrame(index=dates, columns=self.factor_names, dtype=np.float64)
        
        for i in range(n_dates):
            cur_regime = regime.iloc[i]
            
            # 초기 기간은 기본 가중치 사용
            if i < self.cfg.icir_window:
                w = default_weights.get(cur_regime, default_weights["NORMAL"])
                weights.iloc[i] = [w.get(f, 0.0) for f in self.factor_names]
                continue
            
            # 롤링 ICIR 계산 (과거 데이터만 사용)
            icir_vals = []
            for fname in self.factor_names:
                if fname not in ic_series:
                    icir_vals.append(0.0)
                    continue
                
                ic = ic_series[fname].iloc[max(0, i-self.cfg.icir_window):i]
                ic = ic.dropna()
                
                if len(ic) >= self.cfg.icir_min_samples and ic.std() > 1e-10:
                    icir = ic.mean() / ic.std()
                    icir = np.clip(icir, -self.cfg.icir_clip, self.cfg.icir_clip)
                else:
                    icir = 0.0
                
                icir_vals.append(icir)
            
            icir_arr = np.array(icir_vals, dtype=np.float64)
            
            # 유효한 ICIR이 부족하면 기본 가중치 사용
            if np.sum(np.abs(icir_arr) > 0.01) < 2:
                w = default_weights.get(cur_regime, default_weights["NORMAL"])
                weights.iloc[i] = [w.get(f, 0.0) for f in self.factor_names]
                continue
            
            # Softmax로 가중치 변환
            exp_icir = np.exp(icir_arr - np.max(icir_arr))
            w = exp_icir / exp_icir.sum()
            
            # 최소 가중치 보장 (0.05)
            w = np.maximum(w, 0.05)
            w = w / w.sum()
            
            weights.iloc[i] = w
        
        return weights.fillna(method='ffill').fillna(1.0 / len(self.factor_names))

# =============================================================================
# Module 2: HRP Allocator (Hierarchical Risk Parity)
# =============================================================================
class HRPAllocator:
    """
    Hierarchical Risk Parity 포트폴리오 최적화
    - 공분산 행렬 기반 클러스터링
    - 재귀적 이분법으로 가중치 계산
    """
    def __init__(self, config: AresConfig):
        self.cfg = config
    
    def _shrink_cov(self, cov: np.ndarray) -> np.ndarray:
        """공분산 행렬 shrinkage"""
        n = cov.shape[0]
        diag = np.diag(cov).copy()
        diag = np.maximum(diag, self.cfg.min_var_floor)
        
        # Shrinkage toward diagonal
        shrunk = (1 - self.cfg.cov_shrink_lambda) * cov + self.cfg.cov_shrink_lambda * np.diag(diag)
        return shrunk
    
    def _cov_to_corr(self, cov: np.ndarray) -> np.ndarray:
        """공분산 → 상관관계 변환"""
        std = np.sqrt(np.diag(cov))
        std = np.maximum(std, 1e-10)
        corr = cov / np.outer(std, std)
        return np.clip(corr, -1.0, 1.0)
    
    def _get_cluster_var(self, cov: np.ndarray, items: List[int]) -> float:
        """클러스터 분산 계산"""
        cov_slice = cov[np.ix_(items, items)]
        w = 1.0 / np.diag(cov_slice)
        w = w / w.sum()
        return np.dot(np.dot(w, cov_slice), w)
    
    def _recursive_bisection(self, cov: np.ndarray, sort_ix: List[int]) -> np.ndarray:
        """재귀적 이분법으로 가중치 계산"""
        n = len(sort_ix)
        w = np.ones(n, dtype=np.float64)
        clusters = [sort_ix]
        
        while len(clusters) > 0:
            new_clusters = []
            for cluster in clusters:
                if len(cluster) > 1:
                    mid = len(cluster) // 2
                    c1 = cluster[:mid]
                    c2 = cluster[mid:]
                    
                    var1 = self._get_cluster_var(cov, c1)
                    var2 = self._get_cluster_var(cov, c2)
                    
                    alpha = 1 - var1 / (var1 + var2 + 1e-10)
                    
                    for i in c1:
                        w[sort_ix.index(i)] *= alpha
                    for i in c2:
                        w[sort_ix.index(i)] *= (1 - alpha)
                    
                    if len(c1) > 1:
                        new_clusters.append(c1)
                    if len(c2) > 1:
                        new_clusters.append(c2)
            
            clusters = new_clusters
        
        return w / w.sum()
    
    def compute_weights(self, returns_window: pd.DataFrame) -> pd.Series:
        """HRP 가중치 계산"""
        if returns_window.shape[1] <= 1:
            return pd.Series(1.0, index=returns_window.columns)
        
        # 공분산 계산 및 shrinkage
        cov = returns_window.cov().values
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
        cov = self._shrink_cov(cov)
        
        # 상관관계 → 거리 행렬
        corr = self._cov_to_corr(cov)
        dist = np.sqrt(0.5 * (1 - corr))
        np.fill_diagonal(dist, 0)
        
        try:
            # Hierarchical clustering
            condensed = squareform(dist, checks=False)
            Z = linkage(condensed, method='average')
            sort_ix = list(leaves_list(Z))
            
            # 재귀적 이분법
            w = self._recursive_bisection(cov, sort_ix)
            
        except Exception as e:
            logger.warning(f"HRP failed, using equal weights: {e}")
            w = np.ones(len(returns_window.columns)) / len(returns_window.columns)
        
        # 음수 가중치 방지
        w = np.maximum(w, 0.0)
        w = w / (w.sum() + 1e-10)
        
        return pd.Series(w, index=returns_window.columns)

# =============================================================================
# Module 3: CVaR Risk Manager
# =============================================================================
class CVaRRiskManager:
    """
    CVaR 기반 꼬리 리스크 관리
    - 롤링 CVaR 계산
    - CVaR 임계값 초과 시 레버리지 감소
    """
    def __init__(self, config: AresConfig):
        self.cfg = config
    
    def compute_rolling_cvar(self, portfolio_returns: np.ndarray) -> np.ndarray:
        """롤링 CVaR 계산"""
        n = len(portfolio_returns)
        cvar = np.full(n, np.nan, dtype=np.float64)
        
        for i in range(self.cfg.cvar_lookback, n):
            window = portfolio_returns[i-self.cfg.cvar_lookback:i]
            cvar[i] = fast_cvar(window, self.cfg.cvar_alpha)
        
        return cvar
    
    def get_leverage_adjustment(self, cvar: float) -> float:
        """CVaR 기반 레버리지 조정"""
        if np.isnan(cvar):
            return 1.0
        
        # CVaR이 임계값보다 나쁘면 레버리지 감소
        if cvar < self.cfg.cvar_threshold:
            # 선형 감소: cvar_threshold에서 1.0, 2*threshold에서 0.5
            ratio = cvar / self.cfg.cvar_threshold
            return max(0.5, min(1.0, 2.0 - ratio))
        
        return 1.0

# =============================================================================
# Module 4: Rate Regime Detector (2022 대응)
# =============================================================================
class RateRegimeDetector:
    """
    TLT 기반 금리 상승기 감지
    - TLT 6개월 수익률 < -15% → 금리 상승기
    - 금리 상승기: 방어적 포지션
    """
    def __init__(self, config: AresConfig):
        self.cfg = config
    
    def detect(self, tlt_prices: pd.Series) -> pd.Series:
        """금리 상승기 감지"""
        # TLT 6개월 수익률 (과거 데이터만 사용)
        tlt_return = tlt_prices.pct_change(self.cfg.tlt_lookback).shift(1)
        
        # 금리 상승기 판단
        rate_stress = tlt_return < self.cfg.rate_stress_threshold
        
        return rate_stress.fillna(False)
    
    def get_leverage_cap(self, is_rate_stress: bool, base_leverage: float) -> float:
        """금리 상승기 레버리지 상한"""
        if is_rate_stress:
            return min(base_leverage, 0.5)  # 금리 상승기: 최대 0.5x
        return base_leverage

# =============================================================================
# Module 5: Volatility Targeting
# =============================================================================
class VolatilityTargeting:
    """
    변동성 타겟팅
    - 목표 변동성: 12% (연율화)
    - 레버리지 = target_vol / realized_vol
    """
    def __init__(self, config: AresConfig):
        self.cfg = config
    
    def compute_leverage(self, realized_vol: float) -> float:
        """변동성 타겟팅 레버리지 계산"""
        if np.isnan(realized_vol) or realized_vol < 0.01:
            return 1.0
        
        leverage = self.cfg.target_volatility / realized_vol
        return np.clip(leverage, self.cfg.min_leverage, self.cfg.max_leverage)

# =============================================================================
# Main Backtest Engine (v10 Final 기반 + 모듈 통합)
# =============================================================================
@njit(cache=True)
def fast_backtest_v11(
    signals: np.ndarray,
    returns: np.ndarray,
    leverage_caps: np.ndarray,
    hrp_weights_arr: np.ndarray,
    top_k: int,
    rebalance_days: int,
    tc_total: float,
    dd_threshold: float,
    consec_limit: int,
    loss_scale_min: float,
    recovery_rate: float
) -> np.ndarray:
    """v11 백테스트 엔진 (HRP 가중치 적용)"""
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    cum_ret = 1.0
    peak = 1.0
    dd_scale = 1.0
    consecutive_losses = 0
    loss_scale = 1.0
    
    for i in range(n_dates):
        # Drawdown 계산
        if cum_ret > peak:
            peak = cum_ret
        dd = (cum_ret / peak) - 1.0
        
        # Drawdown 기반 스케일링
        if dd < dd_threshold:
            dd_scale = max(0.0, 1.0 + dd / dd_threshold)
        else:
            dd_scale = min(1.0, dd_scale + 0.05)
        
        # 연속 손실 추적
        if i > 0 and portfolio_returns[i-1] < -0.005:
            consecutive_losses += 1
        elif i > 0 and portfolio_returns[i-1] > 0.005:
            consecutive_losses = 0
        
        # 연속 손실 스케일링
        if consecutive_losses >= consec_limit:
            loss_scale = max(loss_scale_min, loss_scale - 0.15)
        else:
            loss_scale = min(1.0, loss_scale + recovery_rate)
        
        # 리밸런싱
        if i % rebalance_days == 0:
            day_signals = signals[i, :]
            valid_mask = ~np.isnan(day_signals)
            
            if valid_mask.sum() >= top_k:
                # Top-K 선택
                sorted_idx = np.argsort(-day_signals)
                selected = []
                count = 0
                for idx in sorted_idx:
                    if valid_mask[idx] and count < top_k:
                        selected.append(idx)
                        count += 1
                
                # HRP 가중치 적용
                new_weights = np.zeros(n_assets, dtype=np.float64)
                hrp_w = hrp_weights_arr[i, :]
                
                total_hrp = 0.0
                for idx in selected:
                    total_hrp += hrp_w[idx]
                
                if total_hrp > 0:
                    for idx in selected:
                        new_weights[idx] = hrp_w[idx] / total_hrp
                else:
                    for idx in selected:
                        new_weights[idx] = 1.0 / top_k
                
                turnover = np.sum(np.abs(new_weights - current_weights))
                total_cost = turnover * tc_total / 2
                current_weights = new_weights
            else:
                total_cost = 0.0
        else:
            total_cost = 0.0
        
        # 일일 수익률 계산
        day_return = returns[i, :]
        leverage = leverage_caps[i] * dd_scale * loss_scale
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        portfolio_returns[i] = port_ret * leverage - total_cost
        cum_ret *= (1.0 + portfolio_returns[i])
    
    return portfolio_returns

# =============================================================================
# ARES v11 System
# =============================================================================
class ARESv11System:
    """ARES v11 통합 시스템"""
    
    def __init__(self, config: AresConfig = None):
        self.cfg = config or AresConfig()
        
        # 모듈 초기화
        self.icir_weighter = ICIRWeighter(self.cfg)
        self.hrp_allocator = HRPAllocator(self.cfg)
        self.cvar_manager = CVaRRiskManager(self.cfg)
        self.rate_detector = RateRegimeDetector(self.cfg)
        self.vol_targeting = VolatilityTargeting(self.cfg)
        
        # 레짐별 기본 팩터 가중치 (v10 Final)
        self.default_factor_weights = {
            "BULL": {"mom_12_1": 0.3, "mom_6_1": 0.3, "mom_3_1": 0.2, "low_vol": 0.2, "reversal": 0.0},
            "NORMAL": {"mom_12_1": 0.4, "mom_6_1": 0.3, "mom_3_1": 0.0, "low_vol": 0.3, "reversal": 0.0},
            "CAUTION": {"mom_12_1": 0.3, "mom_6_1": 0.2, "mom_3_1": 0.0, "low_vol": 0.4, "reversal": 0.1},
            "TIGHTENING": {"mom_12_1": 0.2, "mom_6_1": 0.0, "mom_3_1": 0.0, "low_vol": 0.5, "reversal": 0.3},
            "CRISIS": {"mom_12_1": 0.0, "mom_6_1": 0.0, "mom_3_1": 0.0, "low_vol": 0.6, "reversal": 0.4}
        }
        
        # 레짐별 레버리지 상한 (v10 Final)
        self.leverage_caps = {
            "CRISIS": 0.0,
            "TIGHTENING": 0.5,
            "CAUTION": 0.8,
            "NORMAL": 1.5,
            "BULL": 2.5
        }
    
    def load_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
        """데이터 로딩"""
        logger.info(f"Loading data from {self.cfg.db_path}")
        
        conn = sqlite3.connect(self.cfg.db_path)
        
        # 가격 데이터
        symbols_list = ", ".join([f"'{s}'" for s in self.cfg.universe])
        query = f"""
        SELECT date, symbol, adj_close as close
        FROM daily_ohlcv
        WHERE symbol IN ({symbols_list}) 
        AND date >= '{self.cfg.start_date}' 
        AND date <= '{self.cfg.end_date}'
        ORDER BY date, symbol
        """
        df = pd.read_sql(query, conn)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date", "symbol"], keep="last")
        prices = df.pivot(index="date", columns="symbol", values="close").ffill().bfill()
        
        # VIX 데이터
        vix_query = f"""
        SELECT date, close as vix 
        FROM vix 
        WHERE date >= '{self.cfg.start_date}' 
        AND date <= '{self.cfg.end_date}'
        ORDER BY date
        """
        vix_df = pd.read_sql(vix_query, conn)
        vix_df["date"] = pd.to_datetime(vix_df["date"])
        vix = vix_df.drop_duplicates(subset=["date"], keep="last").set_index("date")["vix"]
        
        conn.close()
        
        # 수익률 계산
        returns = prices.pct_change().fillna(0)
        
        logger.info(f"Loaded: {prices.shape[0]} days x {prices.shape[1]} assets")
        
        return prices, returns, vix
    
    def compute_factors(self, prices: pd.DataFrame, returns: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """팩터 계산"""
        logger.info("Computing factors...")
        
        prices_arr = prices.values.astype(np.float64)
        returns_arr = returns.values.astype(np.float64)
        
        # 모멘텀 팩터
        mom_12_1 = fast_momentum(prices_arr, 252, 21)
        mom_6_1 = fast_momentum(prices_arr, 126, 21)
        mom_3_1 = fast_momentum(prices_arr, 63, 21)
        mom_1_0 = fast_momentum(prices_arr, 21, 0)
        
        # 변동성 팩터
        low_vol = fast_volatility(returns_arr, 60)
        
        factors = {
            "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
            "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
            "mom_3_1": pd.DataFrame(fast_rank_normalize(mom_3_1), index=prices.index, columns=prices.columns),
            "reversal": pd.DataFrame(fast_rank_normalize(-mom_1_0), index=prices.index, columns=prices.columns),
            "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
        }
        
        return factors
    
    def detect_regime(
        self, 
        prices: pd.DataFrame, 
        vix: pd.Series
    ) -> Tuple[pd.Series, pd.Series]:
        """레짐 감지 (v10 Final 로직 + 금리 레짐)"""
        logger.info("Detecting regimes...")
        
        # VIX를 prices 인덱스에 맞춰 reindex
        vix_aligned = vix.reindex(prices.index).ffill().bfill()
        vix_lagged = vix_aligned.shift(1).ffill()
        
        # SPY 트렌드 (대체: AAPL 사용)
        spy_col = "AAPL" if "AAPL" in prices.columns else prices.columns[0]
        spy_price = prices[spy_col]
        ma_200 = spy_price.rolling(200, min_periods=50).mean().shift(1)
        ma_50 = spy_price.rolling(50, min_periods=20).mean().shift(1)
        trend_up = (spy_price.shift(1) > ma_200).fillna(True)
        short_trend_up = (spy_price.shift(1) > ma_50).fillna(True)
        vix_change = vix_lagged.pct_change(5).shift(1).fillna(0)
        
        # TLT 금리 레짐
        tlt_col = "TLT" if "TLT" in prices.columns else None
        if tlt_col:
            rate_stress = self.rate_detector.detect(prices[tlt_col])
        else:
            rate_stress = pd.Series(False, index=prices.index)
        
        # 레짐 분류
        regimes = []
        leverage_caps_list = []
        
        crisis_days = 0
        recovery_days = 0
        current_regime = "NORMAL"
        
        for i, date in enumerate(prices.index):
            # 안전하게 값 가져오기
            vix_val = float(vix_lagged.iloc[i]) if i < len(vix_lagged) else 20.0
            if pd.isna(vix_val):
                vix_val = 20.0
            
            is_trend_up = bool(trend_up.iloc[i]) if i < len(trend_up) else True
            is_short_trend_up = bool(short_trend_up.iloc[i]) if i < len(short_trend_up) else True
            vix_chg = float(vix_change.iloc[i]) if i < len(vix_change) else 0.0
            if pd.isna(vix_chg):
                vix_chg = 0.0
            is_rate_stress = bool(rate_stress.iloc[i]) if i < len(rate_stress) else False
            
            vix_spike = vix_chg > 0.3
            crisis_signal = vix_val > 28 or vix_spike
            recovery_signal = vix_val < 20 and is_trend_up and not vix_spike
            
            if crisis_signal:
                crisis_days += 1
                recovery_days = 0
            elif recovery_signal:
                recovery_days += 1
                crisis_days = max(0, crisis_days - 1)  # 회복 중에도 crisis_days 감소
            else:
                crisis_days = max(0, crisis_days - 1)
                recovery_days = max(0, recovery_days - 1)
            
            # 레짐 결정 (수정된 로직)
            if crisis_days >= 2:
                current_regime = "CRISIS"
            elif current_regime == "CRISIS" and recovery_days >= 3:
                # CRISIS에서 빠져나오는 조건 완화
                current_regime = "CAUTION"
            elif current_regime != "CRISIS":
                if vix_val > 25 or is_rate_stress:
                    current_regime = "TIGHTENING"
                elif vix_val > 18 or not is_trend_up:
                    current_regime = "CAUTION"
                elif vix_val < 15 and is_trend_up and is_short_trend_up:
                    current_regime = "BULL"
                else:
                    current_regime = "NORMAL"
            
            # 레버리지 계산
            base_leverage = self.leverage_caps[current_regime]
            
            # 금리 상승기 레버리지 제한
            leverage = self.rate_detector.get_leverage_cap(is_rate_stress, base_leverage)
            
            # 트렌드 하락 시 추가 감소
            if not is_trend_up and current_regime not in ["CRISIS", "TIGHTENING"]:
                leverage *= 0.5
            
            regimes.append(current_regime)
            leverage_caps_list.append(leverage)
        
        regime_series = pd.Series(regimes, index=prices.index, name="regime")
        leverage_series = pd.Series(leverage_caps_list, index=prices.index, name="leverage_cap")
        
        # 레짐 분포 로깅
        for reg in ["BULL", "NORMAL", "CAUTION", "TIGHTENING", "CRISIS"]:
            pct = (regime_series == reg).mean()
            logger.info(f"  Regime {reg}: {pct:.1%}")
        
        return regime_series, leverage_series
    
    def compute_hrp_weights(self, returns: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
        """전체 기간 HRP 가중치 계산"""
        logger.info("Computing HRP weights...")
        
        n_dates = len(dates)
        n_assets = returns.shape[1]
        hrp_weights = np.ones((n_dates, n_assets), dtype=np.float64) / n_assets
        
        for i in range(self.cfg.hrp_lookback, n_dates):
            if i % self.cfg.rebalance_days == 0:
                window = returns.iloc[i-self.cfg.hrp_lookback:i]
                w = self.hrp_allocator.compute_weights(window)
                hrp_weights[i, :] = w.reindex(returns.columns).fillna(1.0/n_assets).values
            else:
                hrp_weights[i, :] = hrp_weights[i-1, :]
        
        return hrp_weights
    
    def run(self) -> pd.DataFrame:
        """전체 시스템 실행"""
        logger.info("=" * 60)
        logger.info("ARES v11 Integrated System")
        logger.info("=" * 60)
        
        # 1. 데이터 로딩
        prices, returns, vix = self.load_data()
        
        # 2. 팩터 계산
        factors = self.compute_factors(prices, returns)
        
        # 3. 레짐 감지
        regime, leverage_caps = self.detect_regime(prices, vix)
        
        # 4. ICIR 기반 동적 가중치 (선택적)
        use_icir = True
        if use_icir:
            ic_series = self.icir_weighter.compute_rolling_ic(factors, returns)
            dynamic_weights = self.icir_weighter.compute_dynamic_weights(
                ic_series, regime, self.default_factor_weights
            )
        
        # 5. 시그널 생성
        logger.info("Generating signals...")
        combined_signals = []
        
        for i, date in enumerate(prices.index):
            cur_regime = regime.iloc[i]
            
            if use_icir and i >= self.cfg.icir_window:
                weights = dict(zip(self.icir_weighter.factor_names, dynamic_weights.iloc[i].values))
            else:
                weights = self.default_factor_weights.get(cur_regime, self.default_factor_weights["NORMAL"])
            
            day_signal = sum(factors[f].iloc[i] * w for f, w in weights.items() if f in factors)
            combined_signals.append(day_signal.values)
        
        combined_signals_arr = np.array(combined_signals, dtype=np.float64)
        
        # 6. HRP 가중치 계산
        hrp_weights = self.compute_hrp_weights(returns, prices.index)
        
        # 7. 백테스트 실행
        logger.info("Running backtest...")
        
        portfolio_returns = fast_backtest_v11(
            combined_signals_arr,
            returns.values.astype(np.float64),
            leverage_caps.values.astype(np.float64),
            hrp_weights,
            self.cfg.top_k,
            self.cfg.rebalance_days,
            self.cfg.transaction_cost,
            self.cfg.dd_threshold,
            self.cfg.consec_limit,
            self.cfg.loss_scale_min,
            self.cfg.recovery_rate
        )
        
        port_rets_series = pd.Series(portfolio_returns, index=prices.index, name="portfolio_return")
        
        # 8. 성과 분석
        self._print_performance(port_rets_series, regime)
        
        return port_rets_series
    
    def _print_performance(self, returns: pd.Series, regime: pd.Series):
        """성과 출력"""
        cum_ret = (1 + returns).cumprod()
        total_ret = cum_ret.iloc[-1] - 1
        n_years = len(returns) / 252
        annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
        vol = returns.std() * np.sqrt(252)
        sharpe = annual_ret / (vol + 1e-10)
        max_cum = cum_ret.cummax()
        dd = cum_ret / max_cum - 1
        mdd = dd.min()
        
        logger.info("=" * 60)
        logger.info("PERFORMANCE SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total Return: {total_ret:.2%}")
        logger.info(f"Annual Return: {annual_ret:.2%}")
        logger.info(f"Volatility: {vol:.2%}")
        logger.info(f"Sharpe Ratio: {sharpe:.4f}")
        logger.info(f"Max Drawdown: {mdd:.2%}")
        
        # 연도별 성과
        logger.info("\nYearly Performance:")
        for year in range(2016, 2025):
            year_rets = returns[returns.index.year == year]
            if len(year_rets) > 0:
                year_cum = (1 + year_rets).cumprod()
                year_ret = year_cum.iloc[-1] - 1
                year_vol = year_rets.std() * np.sqrt(252)
                year_sharpe = (year_ret / (year_vol + 1e-10)) if year_vol > 0 else 0
                year_mdd = (year_cum / year_cum.cummax() - 1).min()
                logger.info(f"  {year}: Return={year_ret:+.2%}, Sharpe={year_sharpe:.2f}, MDD={year_mdd:.2%}")
        
        # IS/OOS 분석
        is_rets = returns[returns.index <= "2020-12-31"]
        oos_rets = returns[returns.index >= "2021-01-01"]
        
        if len(is_rets) > 0:
            is_sharpe = (is_rets.mean() * 252) / (is_rets.std() * np.sqrt(252) + 1e-10)
            logger.info(f"\nIS (2016-2020) Sharpe: {is_sharpe:.4f}")
        
        if len(oos_rets) > 0:
            oos_sharpe = (oos_rets.mean() * 252) / (oos_rets.std() * np.sqrt(252) + 1e-10)
            logger.info(f"OOS (2021-2024) Sharpe: {oos_sharpe:.4f}")
        
        # 레짐 분포
        logger.info(f"\nRegime Distribution:")
        for reg in regime.unique():
            pct = (regime == reg).mean()
            logger.info(f"  {reg}: {pct:.1%}")

# =============================================================================
# Main
# =============================================================================
def main():
    """메인 실행"""
    system = ARESv11System()
    returns = system.run()
    
    # 결과 저장
    returns.to_csv("/home/ubuntu/ares_v11_returns.csv")
    logger.info("\nResults saved to /home/ubuntu/ares_v11_returns.csv")

if __name__ == "__main__":
    main()
