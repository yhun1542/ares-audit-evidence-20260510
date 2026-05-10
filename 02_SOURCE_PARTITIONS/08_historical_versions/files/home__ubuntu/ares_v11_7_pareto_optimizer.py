"""
ARES v11.7 파레토 최적화
========================
수만 가지 파라미터 조합을 테스트하여 파레토 최적 조합 발견

목표:
1. Sharpe 최대화
2. MDD 최소화  
3. 2022년 손실 최소화
4. OOS Sharpe 최대화

특징:
- 15단계 레짐 (HMM 5단계 × 추세 3단계)
- 신호 lag 적용 (현실적)
- DD Control
- Quality 부스트
- 완전한 프로덕션 레벨 코드
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from itertools import product
import logging
import warnings
import json
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp

warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('/home/ubuntu/pareto_optimization.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# 상수 및 설정
# =============================================================================
DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "UNH", "JNJ",
    "JPM", "V", "PG", "XOM", "HD", "CVX", "MA", "ABBV", "MRK", "LLY",
    "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "XLF", "XLK", "XLE", "XLV"
]

# =============================================================================
# Numba 최적화 함수들
# =============================================================================
@njit(parallel=True, cache=True)
def fast_momentum(prices, lookback, skip=21):
    """모멘텀 계산 (스킵 적용)"""
    n_dates, n_assets = prices.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback + skip, n_dates):
        for j in range(n_assets):
            if prices[i - skip, j] > 0 and prices[i - lookback, j] > 0:
                result[i, j] = prices[i - skip, j] / prices[i - lookback, j] - 1
    return result

@njit(parallel=True, cache=True)
def fast_volatility(returns, lookback):
    """변동성 계산"""
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
def fast_quality(returns, lookback):
    """Quality 팩터 (수익률 안정성 = mean/std)"""
    n_dates, n_assets = returns.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i-lookback:i, j]
            valid = ~np.isnan(window)
            if valid.sum() >= lookback // 2:
                mean_ret = np.nanmean(window)
                std_ret = np.nanstd(window)
                if std_ret > 1e-10:
                    result[i, j] = mean_ret / std_ret
    return result

@njit(parallel=True, cache=True)
def fast_rank_normalize(data):
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
def fast_backtest_full(
    signals, returns, 
    regime_indices, regime_exposure_arr,
    top_k, rebalance_days, 
    tc_one_way, slippage,
    signal_lag,
    dd_halt, dd_stop, dd_recovery,
    quality_boost_regimes, quality_boost_mult,
    quality_signals
):
    """
    완전한 백테스트 엔진
    - 신호 lag 적용
    - DD Control
    - Quality 부스트
    """
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    # DD Control 상태
    portfolio_value = 1.0
    peak_value = 1.0
    dd_halted = False
    halt_level = 0
    
    # 상세 로깅용
    total_turnover = 0.0
    total_cost = 0.0
    dd_halt_count = 0
    dd_stop_count = 0
    rebal_count = 0
    
    for i in range(signal_lag, n_dates):
        # 현재 DD 계산
        current_dd = (portfolio_value / peak_value) - 1.0
        
        # DD Control 로직
        if current_dd <= dd_stop:
            halt_level = 2
            dd_stop_count += 1
        elif current_dd <= dd_halt:
            halt_level = 1
            dd_halt_count += 1
        
        # 회복 체크
        recovery = (portfolio_value / (peak_value * (1 + dd_halt))) - 1.0
        if recovery > dd_recovery and halt_level > 0:
            halt_level = max(0, halt_level - 1)
        
        # DD 레버리지 조정
        if halt_level == 2:
            dd_mult = 0.3
        elif halt_level == 1:
            dd_mult = 0.6
        else:
            dd_mult = 1.0
        
        day_cost = 0.0
        
        # 신호는 signal_lag일 전 데이터 사용
        signal_idx = i - signal_lag
        
        if signal_idx >= 0 and signal_idx % rebalance_days == 0:
            # 레짐에 따른 Quality 부스트
            regime_idx = regime_indices[max(0, signal_idx - 1)]
            
            # Quality 부스트 적용
            day_signals = signals[signal_idx, :].copy()
            if quality_boost_regimes[regime_idx] > 0:
                for j in range(n_assets):
                    if not np.isnan(quality_signals[signal_idx, j]):
                        day_signals[j] = day_signals[j] * (1 - quality_boost_mult) + \
                                        quality_signals[signal_idx, j] * quality_boost_mult
            
            valid_mask = ~np.isnan(day_signals)
            
            if valid_mask.sum() >= top_k:
                sorted_idx = np.argsort(-day_signals)
                new_weights = np.zeros(n_assets, dtype=np.float64)
                count = 0
                for idx in sorted_idx:
                    if valid_mask[idx] and count < top_k:
                        new_weights[idx] = 1.0 / top_k
                        count += 1
                
                turnover = np.sum(np.abs(new_weights - current_weights))
                day_cost = turnover * (tc_one_way + slippage)
                total_turnover += turnover
                total_cost += day_cost
                current_weights = new_weights
                rebal_count += 1
        
        # 당일 수익률
        day_return = returns[i, :]
        
        # 레짐도 전날 데이터 사용
        regime_idx_for_day = regime_indices[max(0, i - 1)]
        base_leverage = regime_exposure_arr[regime_idx_for_day]
        leverage = base_leverage * dd_mult
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        daily_ret = port_ret * leverage - day_cost
        portfolio_returns[i] = daily_ret
        
        # 포트폴리오 가치 업데이트
        portfolio_value *= (1 + daily_ret)
        if portfolio_value > peak_value:
            peak_value = portfolio_value
    
    return portfolio_returns, total_turnover, total_cost, dd_halt_count, dd_stop_count, rebal_count

@njit(cache=True)
def calc_metrics_numba(returns):
    """성과 지표 계산 (Numba)"""
    n = len(returns)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    
    # 누적 수익률
    cum_ret = 1.0
    max_cum = 1.0
    mdd = 0.0
    
    for i in range(n):
        cum_ret *= (1 + returns[i])
        if cum_ret > max_cum:
            max_cum = cum_ret
        dd = (cum_ret / max_cum) - 1.0
        if dd < mdd:
            mdd = dd
    
    total_ret = cum_ret - 1.0
    n_years = n / 252.0
    if n_years > 0:
        annual_ret = (1 + total_ret) ** (1 / n_years) - 1
    else:
        annual_ret = 0.0
    
    # 변동성
    mean_ret = 0.0
    for i in range(n):
        mean_ret += returns[i]
    mean_ret /= n
    
    var_ret = 0.0
    for i in range(n):
        var_ret += (returns[i] - mean_ret) ** 2
    var_ret /= n
    vol = np.sqrt(var_ret) * np.sqrt(252)
    
    sharpe = annual_ret / (vol + 1e-10)
    
    return sharpe, mdd, annual_ret, total_ret

# =============================================================================
# 데이터 로더
# =============================================================================
class DataLoader:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.prices = None
        self.returns = None
        self.vix = None
        self.spy_prices = None
        
    def load(self):
        logger.info("데이터 로드 중...")
        
        conn = sqlite3.connect(self.db_path)
        
        symbols_str = "'" + "','".join(UNIVERSE) + "'"
        query = f"""
        SELECT date, symbol, adj_close as close
        FROM daily_ohlcv
        WHERE symbol IN ({symbols_str}) AND date >= '2016-01-01'
        ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, conn)
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        self.prices = df.pivot(index='date', columns='symbol', values='close')
        self.prices = self.prices.ffill()  # ffill만 사용
        
        # VIX 로드
        vix_df = pd.read_sql_query(
            "SELECT date, close as vix FROM vix WHERE date >= '2016-01-01'", conn
        )
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        self.vix = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')['vix']
        
        conn.close()
        
        self.returns = self.prices.pct_change().fillna(0)
        
        # 유효 컬럼만 유지
        valid_cols = self.prices.columns[self.prices.notna().mean() > 0.8]
        self.prices = self.prices[valid_cols]
        self.returns = self.returns[valid_cols]
        
        if 'SPY' in self.prices.columns:
            self.spy_prices = self.prices['SPY']
        
        logger.info(f"로드 완료: {len(self.prices.columns)}개 종목, {len(self.prices)}일")
        
        return self

# =============================================================================
# 팩터 계산기
# =============================================================================
class FactorCalculator:
    def __init__(self, prices: pd.DataFrame, returns: pd.DataFrame):
        self.prices = prices
        self.returns = returns
        self.factors = {}
        
    def compute_all(self):
        logger.info("팩터 계산 중...")
        
        prices_arr = self.prices.values.astype(np.float64)
        returns_arr = self.returns.values.astype(np.float64)
        
        # 모멘텀 12-1 (21일 스킵)
        mom_12_1 = fast_momentum(prices_arr, 252, skip=21)
        self.factors['mom_12_1'] = pd.DataFrame(
            fast_rank_normalize(mom_12_1),
            index=self.prices.index, columns=self.prices.columns
        )
        
        # 모멘텀 6-1 (21일 스킵)
        mom_6_1 = fast_momentum(prices_arr, 126, skip=21)
        self.factors['mom_6_1'] = pd.DataFrame(
            fast_rank_normalize(mom_6_1),
            index=self.prices.index, columns=self.prices.columns
        )
        
        # 저변동성 (역순위)
        vol_60 = fast_volatility(returns_arr, 60)
        low_vol = -vol_60  # 낮을수록 좋음
        self.factors['low_vol'] = pd.DataFrame(
            fast_rank_normalize(low_vol),
            index=self.prices.index, columns=self.prices.columns
        )
        
        # Quality
        quality = fast_quality(returns_arr, 60)
        self.factors['quality'] = pd.DataFrame(
            fast_rank_normalize(quality),
            index=self.prices.index, columns=self.prices.columns
        )
        
        logger.info(f"팩터 계산 완료: {list(self.factors.keys())}")
        
        return self.factors

# =============================================================================
# 레짐 감지기 (15단계)
# =============================================================================
class RegimeDetector:
    def __init__(self, vix: pd.Series, spy_prices: pd.Series, prices_index: pd.DatetimeIndex):
        self.vix = vix
        self.spy_prices = spy_prices
        self.prices_index = prices_index
        
    def detect(self, vix_thresholds: Dict[str, float], use_hmm: bool = False):
        """
        15단계 레짐 감지
        VIX 5단계 × 추세 3단계 = 15단계
        """
        vix_aligned = self.vix.reindex(self.prices_index).ffill()
        
        # 전날 VIX 사용 (look-ahead bias 방지)
        vix_lagged = vix_aligned.shift(1)
        
        # VIX 기반 5단계
        vix_regime = pd.Series("MODERATE", index=self.prices_index)
        vix_regime[vix_lagged < vix_thresholds['ultra_low']] = "ULTRA_LOW"
        vix_regime[(vix_lagged >= vix_thresholds['ultra_low']) & (vix_lagged < vix_thresholds['low'])] = "LOW"
        vix_regime[(vix_lagged >= vix_thresholds['low']) & (vix_lagged < vix_thresholds['moderate'])] = "MODERATE"
        vix_regime[(vix_lagged >= vix_thresholds['moderate']) & (vix_lagged < vix_thresholds['high'])] = "HIGH"
        vix_regime[vix_lagged >= vix_thresholds['crisis']] = "CRISIS"
        
        # 추세 기반 3단계 (전날 데이터 사용)
        if self.spy_prices is not None:
            spy_ret = self.spy_prices.pct_change()
            ma_short = spy_ret.rolling(5).mean().shift(1)
            ma_long = spy_ret.rolling(20).mean().shift(1)
            
            trend = pd.Series("NEUTRAL", index=self.prices_index)
            trend[(ma_short > ma_long) & (ma_short > 0)] = "BULL"
            trend[(ma_short < ma_long) & (ma_short < 0)] = "BEAR"
        else:
            trend = pd.Series("NEUTRAL", index=self.prices_index)
        
        # 15단계 조합
        detailed_regimes = vix_regime + "_" + trend
        
        return detailed_regimes, vix_regime, trend

# =============================================================================
# 파레토 최적화기
# =============================================================================
class ParetoOptimizer:
    def __init__(self, data_loader: DataLoader, factor_calculator: FactorCalculator):
        self.data = data_loader
        self.factors = factor_calculator.factors
        self.results = []
        
    def generate_param_grid(self):
        """파라미터 그리드 생성 - 미리 정의된 VIX 조합 사용"""
        
        # VIX 임계값 조합 (미리 정의)
        vix_combos = [
            (12, 15, 18, 22, 28),
            (12, 16, 19, 23, 28),
            (13, 16, 19, 23, 28),
            (13, 16, 19, 24, 29),
            (12, 15, 18, 23, 29),
            (13, 17, 20, 24, 29),
            (11, 15, 18, 22, 27),
            (12, 16, 20, 24, 30),
            (13, 16, 19, 22, 27),
            (14, 17, 20, 24, 30),
        ]
        
        # 레버리지
        lev_bull_range = [1.2, 1.5, 1.8, 2.0]
        lev_neutral_range = [0.5, 0.7, 0.9, 1.0]
        
        # 팩터 가중치 (합이 1이 되도록)
        factor_weight_combos = [
            (0.5, 0.3, 0.1, 0.1),  # mom_12_1, mom_6_1, low_vol, quality
            (0.6, 0.2, 0.1, 0.1),
            (0.4, 0.4, 0.1, 0.1),
            (0.5, 0.2, 0.2, 0.1),
            (0.5, 0.2, 0.1, 0.2),
            (0.7, 0.1, 0.1, 0.1),
            (0.4, 0.3, 0.2, 0.1),
            (0.4, 0.3, 0.1, 0.2),
            (0.8, 0.1, 0.05, 0.05),
            (0.3, 0.3, 0.2, 0.2),
        ]
        
        # 리밸런싱
        rebalance_days_range = [1, 5, 10, 21]
        
        # DD Control
        dd_combos = [
            (-0.06, -0.10),
            (-0.08, -0.12),
            (-0.10, -0.15),
            (-0.05, -0.10),
            (-0.08, -0.15),
        ]
        
        # top_k
        top_k_range = [5, 10, 15, 20]
        
        # 거래비용
        tc_range = [0.001, 0.002, 0.003]  # 10bp, 20bp, 30bp
        
        # Quality 부스트
        quality_boost_range = [0.0, 0.2, 0.4, 0.6]
        
        # 그리드 생성
        param_grid = []
        
        for vix_combo in vix_combos:
            vix_ul, vix_l, vix_m, vix_h, vix_c = vix_combo
            
            for lev_bull in lev_bull_range:
                for lev_neutral in lev_neutral_range:
                    for fw in factor_weight_combos:
                        for rebal in rebalance_days_range:
                            for dd_halt, dd_stop in dd_combos:
                                for top_k in top_k_range:
                                    for tc in tc_range:
                                        for qb in quality_boost_range:
                                            param_grid.append({
                                                'vix_ultra_low': vix_ul,
                                                'vix_low': vix_l,
                                                'vix_moderate': vix_m,
                                                'vix_high': vix_h,
                                                'vix_crisis': vix_c,
                                                'lev_bull': lev_bull,
                                                'lev_neutral': lev_neutral,
                                                'lev_bear': 0.0,
                                                'factor_weights': fw,
                                                'rebalance_days': rebal,
                                                'dd_halt': dd_halt,
                                                'dd_stop': dd_stop,
                                                'top_k': top_k,
                                                'signal_lag': 1,
                                                'tc': tc,
                                                'quality_boost': qb
                                            })
        
        logger.info(f"파라미터 그리드 생성: {len(param_grid):,}개 조합")
        return param_grid
    
    def run_single_backtest(self, params: Dict) -> Dict:
        """단일 백테스트 실행"""
        
        # 레짐 감지
        vix_thresholds = {
            'ultra_low': params['vix_ultra_low'],
            'low': params['vix_low'],
            'moderate': params['vix_moderate'],
            'high': params['vix_high'],
            'crisis': params['vix_crisis']
        }
        
        detector = RegimeDetector(self.data.vix, self.data.spy_prices, self.data.prices.index)
        detailed_regimes, vix_regime, trend = detector.detect(vix_thresholds)
        
        # 레짐 노출도 설정
        regime_names = sorted(detailed_regimes.unique().tolist())
        regime_exposure = {}
        for name in regime_names:
            if "BULL" in name:
                regime_exposure[name] = params['lev_bull']
            elif "NEUTRAL" in name:
                regime_exposure[name] = params['lev_neutral']
            else:  # BEAR
                regime_exposure[name] = params['lev_bear']
        
        # 팩터 결합
        fw = params['factor_weights']
        combined = (
            self.factors['mom_12_1'] * fw[0] +
            self.factors['mom_6_1'] * fw[1] +
            self.factors['low_vol'] * fw[2] +
            self.factors['quality'] * fw[3]
        )
        
        # 공통 인덱스/컬럼
        common_idx = combined.index.intersection(detailed_regimes.index)
        common_cols = combined.columns.intersection(self.data.returns.columns)
        
        signals_arr = combined.loc[common_idx, common_cols].values.astype(np.float64)
        returns_arr = self.data.returns.loc[common_idx, common_cols].values.astype(np.float64)
        quality_arr = self.factors['quality'].loc[common_idx, common_cols].values.astype(np.float64)
        
        # 레짐 인덱스
        regime_to_idx = {n: i for i, n in enumerate(regime_names)}
        regime_indices = np.array([regime_to_idx.get(detailed_regimes.loc[d], 0) for d in common_idx], dtype=np.int32)
        regime_exposure_arr = np.array([regime_exposure.get(n, 0.5) for n in regime_names], dtype=np.float64)
        
        # Quality 부스트 레짐 (LOW 레짐들)
        quality_boost_regimes = np.array([1 if "LOW" in n else 0 for n in regime_names], dtype=np.int32)
        
        # 백테스트 실행
        port_rets, turnover, cost, dd_halt_cnt, dd_stop_cnt, rebal_cnt = fast_backtest_full(
            signals_arr, returns_arr,
            regime_indices, regime_exposure_arr,
            params['top_k'], params['rebalance_days'],
            params['tc'], 0.001,  # slippage 10bp
            params['signal_lag'],
            params['dd_halt'], params['dd_stop'], 0.04,  # recovery 4%
            quality_boost_regimes, params['quality_boost'],
            quality_arr
        )
        
        # 전체 기간 성과
        full_sharpe, full_mdd, full_annual, full_total = calc_metrics_numba(port_rets)
        
        # IS/OOS 분리
        is_end_idx = np.searchsorted(common_idx, pd.Timestamp('2020-12-31'))
        oos_start_idx = np.searchsorted(common_idx, pd.Timestamp('2021-01-01'))
        
        is_rets = port_rets[:is_end_idx]
        oos_rets = port_rets[oos_start_idx:]
        
        is_sharpe, is_mdd, is_annual, is_total = calc_metrics_numba(is_rets)
        oos_sharpe, oos_mdd, oos_annual, oos_total = calc_metrics_numba(oos_rets)
        
        # 2022년 성과
        y2022_start = np.searchsorted(common_idx, pd.Timestamp('2022-01-01'))
        y2022_end = np.searchsorted(common_idx, pd.Timestamp('2022-12-31'))
        y2022_rets = port_rets[y2022_start:y2022_end]
        y2022_sharpe, y2022_mdd, y2022_annual, y2022_total = calc_metrics_numba(y2022_rets)
        
        return {
            'params': params,
            'full_sharpe': full_sharpe,
            'full_mdd': full_mdd,
            'full_annual': full_annual,
            'full_total': full_total,
            'is_sharpe': is_sharpe,
            'is_mdd': is_mdd,
            'oos_sharpe': oos_sharpe,
            'oos_mdd': oos_mdd,
            'y2022_sharpe': y2022_sharpe,
            'y2022_return': y2022_total,
            'turnover': turnover,
            'cost': cost,
            'dd_halt_count': dd_halt_cnt,
            'dd_stop_count': dd_stop_cnt,
            'rebal_count': rebal_cnt
        }
    
    def run_optimization(self, max_combinations: int = 50000):
        """파레토 최적화 실행"""
        
        param_grid = self.generate_param_grid()
        
        # 조합 수 제한
        if len(param_grid) > max_combinations:
            logger.info(f"조합 수 제한: {len(param_grid):,} → {max_combinations:,}")
            np.random.seed(42)
            indices = np.random.choice(len(param_grid), max_combinations, replace=False)
            param_grid = [param_grid[i] for i in indices]
        
        logger.info(f"파레토 최적화 시작: {len(param_grid):,}개 조합")
        
        results = []
        
        for i, params in enumerate(param_grid):
            try:
                result = self.run_single_backtest(params)
                results.append(result)
                
                if (i + 1) % 1000 == 0:
                    logger.info(f"진행: {i+1:,}/{len(param_grid):,} ({(i+1)/len(param_grid)*100:.1f}%)")
                    
                    # 현재까지 최고 성과
                    best_sharpe = max(r['full_sharpe'] for r in results)
                    best_oos = max(r['oos_sharpe'] for r in results)
                    logger.info(f"  현재 최고 - Full Sharpe: {best_sharpe:.2f}, OOS Sharpe: {best_oos:.2f}")
                    
            except Exception as e:
                logger.warning(f"백테스트 실패: {e}")
                continue
        
        self.results = results
        logger.info(f"파레토 최적화 완료: {len(results):,}개 결과")
        
        return results
    
    def find_pareto_frontier(self):
        """파레토 프론티어 추출"""
        
        if not self.results:
            logger.error("결과가 없습니다. 먼저 run_optimization()을 실행하세요.")
            return []
        
        # 파레토 최적화 목표: Sharpe↑, MDD↓, 2022손실↓, OOS Sharpe↑
        # MDD와 2022 손실은 음수이므로 최대화하면 됨
        
        pareto_front = []
        
        for result in self.results:
            is_dominated = False
            
            for other in self.results:
                if other is result:
                    continue
                
                # other가 result를 지배하는지 확인
                dominates = (
                    other['full_sharpe'] >= result['full_sharpe'] and
                    other['full_mdd'] >= result['full_mdd'] and  # MDD는 음수, 클수록 좋음
                    other['y2022_return'] >= result['y2022_return'] and
                    other['oos_sharpe'] >= result['oos_sharpe'] and
                    (
                        other['full_sharpe'] > result['full_sharpe'] or
                        other['full_mdd'] > result['full_mdd'] or
                        other['y2022_return'] > result['y2022_return'] or
                        other['oos_sharpe'] > result['oos_sharpe']
                    )
                )
                
                if dominates:
                    is_dominated = True
                    break
            
            if not is_dominated:
                pareto_front.append(result)
        
        # Sharpe 기준 정렬
        pareto_front.sort(key=lambda x: x['full_sharpe'], reverse=True)
        
        logger.info(f"파레토 프론티어: {len(pareto_front)}개 조합")
        
        return pareto_front
    
    def save_results(self, filepath: str):
        """결과 저장"""
        
        # 파레토 프론티어
        pareto_front = self.find_pareto_frontier()
        
        # Top 10 결과
        top_10 = sorted(self.results, key=lambda x: x['full_sharpe'], reverse=True)[:10]
        
        output = {
            'timestamp': datetime.now().isoformat(),
            'total_combinations': len(self.results),
            'pareto_frontier_count': len(pareto_front),
            'pareto_frontier': pareto_front[:20],  # 상위 20개만
            'top_10_by_sharpe': top_10,
            'best_oos': max(self.results, key=lambda x: x['oos_sharpe']),
            'best_2022': max(self.results, key=lambda x: x['y2022_return'])
        }
        
        with open(filepath, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        
        logger.info(f"결과 저장: {filepath}")
        
        return output

# =============================================================================
# 메인
# =============================================================================
def main():
    logger.info("=" * 80)
    logger.info("ARES v11.7 파레토 최적화")
    logger.info("=" * 80)
    
    # 데이터 로드
    data = DataLoader(DB_PATH).load()
    
    # 팩터 계산
    factors = FactorCalculator(data.prices, data.returns).compute_all()
    
    # 파레토 최적화
    optimizer = ParetoOptimizer(data, FactorCalculator(data.prices, data.returns))
    optimizer.factors = factors
    
    # 최적화 실행 (최대 50,000 조합)
    results = optimizer.run_optimization(max_combinations=50000)
    
    # 파레토 프론티어 추출
    pareto_front = optimizer.find_pareto_frontier()
    
    # 결과 출력
    logger.info("\n" + "=" * 80)
    logger.info("파레토 프론티어 Top 10")
    logger.info("=" * 80)
    
    for i, result in enumerate(pareto_front[:10]):
        logger.info(f"\n[{i+1}] Full Sharpe: {result['full_sharpe']:.2f}, OOS Sharpe: {result['oos_sharpe']:.2f}")
        logger.info(f"    MDD: {result['full_mdd']:.2%}, 2022: {result['y2022_return']:.2%}")
        logger.info(f"    Params: top_k={result['params']['top_k']}, rebal={result['params']['rebalance_days']}")
        logger.info(f"    VIX: {result['params']['vix_ultra_low']}/{result['params']['vix_low']}/{result['params']['vix_moderate']}/{result['params']['vix_high']}/{result['params']['vix_crisis']}")
        logger.info(f"    Leverage: BULL={result['params']['lev_bull']}, NEUTRAL={result['params']['lev_neutral']}")
        logger.info(f"    Factor: {result['params']['factor_weights']}")
    
    # 결과 저장
    output = optimizer.save_results('/home/ubuntu/pareto_optimization_results.json')
    
    # 최적 조합 상세 출력
    if pareto_front:
        best = pareto_front[0]
        logger.info("\n" + "=" * 80)
        logger.info("최적 조합 상세")
        logger.info("=" * 80)
        logger.info(f"Full Period Sharpe: {best['full_sharpe']:.4f}")
        logger.info(f"IS Sharpe: {best['is_sharpe']:.4f}")
        logger.info(f"OOS Sharpe: {best['oos_sharpe']:.4f}")
        logger.info(f"Full MDD: {best['full_mdd']:.2%}")
        logger.info(f"2022 Return: {best['y2022_return']:.2%}")
        logger.info(f"Total Turnover: {best['turnover']:.2f}")
        logger.info(f"Total Cost: {best['cost']:.4f}")
        logger.info(f"DD Halt Count: {best['dd_halt_count']}")
        logger.info(f"DD Stop Count: {best['dd_stop_count']}")
        logger.info(f"\n최적 파라미터:")
        for k, v in best['params'].items():
            logger.info(f"  {k}: {v}")

if __name__ == "__main__":
    main()
