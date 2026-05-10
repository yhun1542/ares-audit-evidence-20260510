"""
ARES v11.7 다중 지표 레짐 감지 + 파레토 최적화
==============================================
VIX만이 아닌 다중 지표를 활용한 정교한 레짐 감지

레짐 지표:
1. VIX - 변동성 공포 지수
2. 신용 스프레드 - HYG/LQD (리스크 선호도)
3. 금리 추세 - TLT 모멘텀 (금리 상승/하락)
4. 시장 추세 - SPY MA 크로스
5. 시장 폭 - 상승 종목 비율
6. 섹터 로테이션 - XLK/XLE (성장 vs 가치)

특징:
- 완전한 프로덕션 레벨 코드
- 상세 로깅
- 신호 lag 적용 (현실적)
- DD Control
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

warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('/home/ubuntu/multi_regime_pareto.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# 상수 및 설정
# =============================================================================
MAIN_DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
ETF_DB_PATH = "/tmp/etf_check.db"  # 복사본 사용

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JNJ",
    "JPM", "V", "PG", "HD", "MA", "ABBV", "MRK", "LLY",
    "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
    "SPY", "QQQ", "IWM", "DIA"
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
    """Quality 팩터 (수익률 안정성)"""
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
def fast_rolling_mean(data, window):
    """롤링 평균"""
    n = len(data)
    result = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        result[i] = np.nanmean(data[i - window + 1:i + 1])
    return result

@njit(cache=True)
def fast_rolling_std(data, window):
    """롤링 표준편차"""
    n = len(data)
    result = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        result[i] = np.nanstd(data[i - window + 1:i + 1])
    return result

@njit(cache=True)
def fast_backtest_multi_regime(
    signals, returns, 
    regime_scores, regime_thresholds,
    exposure_map,
    top_k, rebalance_days, 
    tc_one_way, slippage,
    signal_lag,
    dd_halt, dd_stop, dd_recovery,
    quality_boost_low_regime,
    quality_signals
):
    """
    다중 지표 레짐 기반 백테스트
    - regime_scores: 복합 레짐 점수 (0-100)
    - regime_thresholds: 레짐 구분 임계값
    - exposure_map: 레짐별 노출도
    """
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    # DD Control 상태
    portfolio_value = 1.0
    peak_value = 1.0
    halt_level = 0
    
    # 상세 로깅용
    total_turnover = 0.0
    total_cost = 0.0
    dd_halt_count = 0
    dd_stop_count = 0
    rebal_count = 0
    regime_counts = np.zeros(5, dtype=np.int32)  # 5단계 레짐
    
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
        signal_idx = i - signal_lag
        
        if signal_idx >= 0 and signal_idx % rebalance_days == 0:
            # 레짐 점수로 레짐 결정 (전날 데이터 사용)
            regime_score = regime_scores[max(0, signal_idx - 1)]
            
            # 레짐 결정 (5단계)
            if regime_score >= regime_thresholds[0]:
                regime_idx = 0  # ULTRA_BULL
            elif regime_score >= regime_thresholds[1]:
                regime_idx = 1  # BULL
            elif regime_score >= regime_thresholds[2]:
                regime_idx = 2  # NEUTRAL
            elif regime_score >= regime_thresholds[3]:
                regime_idx = 3  # CAUTION
            else:
                regime_idx = 4  # CRISIS
            
            regime_counts[regime_idx] += 1
            
            # Quality 부스트 (CAUTION 레짐에서)
            day_signals = signals[signal_idx, :].copy()
            if regime_idx == 3 and quality_boost_low_regime > 0:
                for j in range(n_assets):
                    if not np.isnan(quality_signals[signal_idx, j]):
                        day_signals[j] = day_signals[j] * (1 - quality_boost_low_regime) + \
                                        quality_signals[signal_idx, j] * quality_boost_low_regime
            
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
        
        # 레짐 노출도 (전날 데이터 사용)
        regime_score_for_day = regime_scores[max(0, i - 1)]
        
        if regime_score_for_day >= regime_thresholds[0]:
            base_leverage = exposure_map[0]
        elif regime_score_for_day >= regime_thresholds[1]:
            base_leverage = exposure_map[1]
        elif regime_score_for_day >= regime_thresholds[2]:
            base_leverage = exposure_map[2]
        elif regime_score_for_day >= regime_thresholds[3]:
            base_leverage = exposure_map[3]
        else:
            base_leverage = exposure_map[4]
        
        leverage = base_leverage * dd_mult
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        daily_ret = port_ret * leverage - day_cost
        portfolio_returns[i] = daily_ret
        
        portfolio_value *= (1 + daily_ret)
        if portfolio_value > peak_value:
            peak_value = portfolio_value
    
    return portfolio_returns, total_turnover, total_cost, dd_halt_count, dd_stop_count, rebal_count, regime_counts

@njit(cache=True)
def calc_metrics_numba(returns):
    """성과 지표 계산"""
    n = len(returns)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    
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
class MultiSourceDataLoader:
    def __init__(self, main_db: str, etf_db: str):
        self.main_db = main_db
        self.etf_db = etf_db
        self.prices = None
        self.returns = None
        self.vix = None
        self.spy_prices = None
        self.hyg_prices = None
        self.lqd_prices = None
        self.tlt_prices = None
        self.xlk_prices = None
        self.xle_prices = None
        
    def load(self):
        logger.info("다중 소스 데이터 로드 중...")
        
        # 메인 DB에서 주식 데이터
        conn = sqlite3.connect(self.main_db)
        
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
        self.prices = self.prices.ffill()
        
        # VIX 로드
        vix_df = pd.read_sql_query(
            "SELECT date, close as vix FROM vix WHERE date >= '2016-01-01'", conn
        )
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        self.vix = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')['vix']
        
        conn.close()
        
        # ETF DB에서 레짐 지표 데이터
        try:
            etf_conn = sqlite3.connect(self.etf_db)
            
            for symbol, attr in [('HYG', 'hyg_prices'), ('LQD', 'lqd_prices'), 
                                 ('TLT', 'tlt_prices'), ('XLK', 'xlk_prices'), 
                                 ('XLE', 'xle_prices')]:
                query = f"""
                SELECT date, close FROM daily_ohlcv 
                WHERE symbol = '{symbol}' AND date >= '2016-01-01'
                ORDER BY date
                """
                etf_df = pd.read_sql_query(query, etf_conn)
                etf_df['date'] = pd.to_datetime(etf_df['date'])
                setattr(self, attr, etf_df.set_index('date')['close'])
            
            etf_conn.close()
            logger.info("ETF 레짐 지표 데이터 로드 완료")
        except Exception as e:
            logger.warning(f"ETF 데이터 로드 실패: {e}")
        
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
# 다중 지표 레짐 감지기
# =============================================================================
class MultiIndicatorRegimeDetector:
    def __init__(self, data: MultiSourceDataLoader):
        self.data = data
        
    def compute_regime_score(self, weights: Dict[str, float]) -> pd.Series:
        """
        다중 지표 기반 복합 레짐 점수 계산 (0-100)
        높을수록 BULL, 낮을수록 CRISIS
        """
        index = self.data.prices.index
        scores = pd.DataFrame(index=index)
        
        # 1. VIX 점수 (0-100, 낮을수록 좋음)
        vix_aligned = self.data.vix.reindex(index).ffill()
        vix_score = 100 - np.clip((vix_aligned - 10) / 40 * 100, 0, 100)
        scores['vix'] = vix_score
        
        # 2. 신용 스프레드 점수 (HYG/LQD, 높을수록 리스크 선호)
        if self.data.hyg_prices is not None and self.data.lqd_prices is not None:
            hyg_aligned = self.data.hyg_prices.reindex(index).ffill()
            lqd_aligned = self.data.lqd_prices.reindex(index).ffill()
            credit_ratio = hyg_aligned / lqd_aligned
            credit_ma = credit_ratio.rolling(20).mean()
            credit_score = np.clip((credit_ratio / credit_ma - 0.95) / 0.1 * 50 + 50, 0, 100)
            scores['credit'] = credit_score
        else:
            scores['credit'] = 50  # 기본값
        
        # 3. 금리 추세 점수 (TLT 모멘텀, 상승=금리하락=좋음)
        if self.data.tlt_prices is not None:
            tlt_aligned = self.data.tlt_prices.reindex(index).ffill()
            tlt_mom = tlt_aligned.pct_change(20)
            tlt_score = np.clip(tlt_mom * 500 + 50, 0, 100)
            scores['rate'] = tlt_score
        else:
            scores['rate'] = 50
        
        # 4. 시장 추세 점수 (SPY MA 크로스)
        if self.data.spy_prices is not None:
            spy = self.data.spy_prices
            ma_5 = spy.rolling(5).mean()
            ma_20 = spy.rolling(20).mean()
            ma_200 = spy.rolling(200).mean()
            
            # MA5 > MA20 > MA200 = 100점, 반대 = 0점
            trend_score = pd.Series(50, index=index)
            trend_score[(ma_5 > ma_20) & (ma_20 > ma_200)] = 100
            trend_score[(ma_5 > ma_20) & (ma_20 <= ma_200)] = 70
            trend_score[(ma_5 <= ma_20) & (ma_20 > ma_200)] = 40
            trend_score[(ma_5 <= ma_20) & (ma_20 <= ma_200)] = 10
            scores['trend'] = trend_score
        else:
            scores['trend'] = 50
        
        # 5. 시장 폭 점수 (상승 종목 비율)
        daily_rets = self.data.returns
        up_ratio = (daily_rets > 0).sum(axis=1) / daily_rets.shape[1]
        up_ratio_ma = up_ratio.rolling(5).mean()
        breadth_score = np.clip(up_ratio_ma * 100, 0, 100)
        scores['breadth'] = breadth_score
        
        # 6. 섹터 로테이션 점수 (XLK/XLE, 성장 선호 = 좋음)
        if self.data.xlk_prices is not None and self.data.xle_prices is not None:
            xlk_aligned = self.data.xlk_prices.reindex(index).ffill()
            xle_aligned = self.data.xle_prices.reindex(index).ffill()
            sector_ratio = xlk_aligned / xle_aligned
            sector_ma = sector_ratio.rolling(20).mean()
            sector_score = np.clip((sector_ratio / sector_ma - 0.95) / 0.1 * 50 + 50, 0, 100)
            scores['sector'] = sector_score
        else:
            scores['sector'] = 50
        
        # 가중 평균 점수
        composite_score = (
            scores['vix'] * weights.get('vix', 0.3) +
            scores['credit'] * weights.get('credit', 0.15) +
            scores['rate'] * weights.get('rate', 0.15) +
            scores['trend'] * weights.get('trend', 0.2) +
            scores['breadth'] * weights.get('breadth', 0.1) +
            scores['sector'] * weights.get('sector', 0.1)
        )
        
        # 전날 데이터 사용 (look-ahead bias 방지)
        composite_score = composite_score.shift(1).fillna(50)
        
        return composite_score, scores

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
        
        # 저변동성
        vol_60 = fast_volatility(returns_arr, 60)
        low_vol = -vol_60
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
# 파레토 최적화기
# =============================================================================
class MultiRegimeParetoOptimizer:
    def __init__(self, data: MultiSourceDataLoader, factors: Dict):
        self.data = data
        self.factors = factors
        self.regime_detector = MultiIndicatorRegimeDetector(data)
        self.results = []
        
    def generate_param_grid(self):
        """파라미터 그리드 생성"""
        
        # 레짐 지표 가중치 조합
        regime_weight_combos = [
            {'vix': 0.4, 'credit': 0.15, 'rate': 0.15, 'trend': 0.15, 'breadth': 0.1, 'sector': 0.05},
            {'vix': 0.3, 'credit': 0.2, 'rate': 0.2, 'trend': 0.15, 'breadth': 0.1, 'sector': 0.05},
            {'vix': 0.3, 'credit': 0.15, 'rate': 0.15, 'trend': 0.25, 'breadth': 0.1, 'sector': 0.05},
            {'vix': 0.25, 'credit': 0.2, 'rate': 0.2, 'trend': 0.2, 'breadth': 0.1, 'sector': 0.05},
            {'vix': 0.35, 'credit': 0.1, 'rate': 0.1, 'trend': 0.3, 'breadth': 0.1, 'sector': 0.05},
            {'vix': 0.2, 'credit': 0.2, 'rate': 0.2, 'trend': 0.2, 'breadth': 0.15, 'sector': 0.05},
        ]
        
        # 레짐 임계값 (점수 기준)
        regime_threshold_combos = [
            [80, 60, 40, 25],  # ULTRA_BULL > 80, BULL > 60, NEUTRAL > 40, CAUTION > 25, else CRISIS
            [75, 55, 40, 20],
            [85, 65, 45, 25],
            [70, 50, 35, 20],
            [80, 55, 35, 20],
        ]
        
        # 레짐별 노출도
        exposure_combos = [
            [1.8, 1.3, 0.8, 0.4, 0.0],  # ULTRA_BULL, BULL, NEUTRAL, CAUTION, CRISIS
            [2.0, 1.5, 1.0, 0.5, 0.0],
            [1.5, 1.2, 0.8, 0.3, 0.0],
            [1.8, 1.4, 0.9, 0.5, 0.1],
            [2.0, 1.5, 0.7, 0.3, 0.0],
            [1.5, 1.0, 0.7, 0.4, 0.0],
        ]
        
        # 팩터 가중치
        factor_weight_combos = [
            (0.5, 0.3, 0.1, 0.1),
            (0.6, 0.2, 0.1, 0.1),
            (0.4, 0.4, 0.1, 0.1),
            (0.5, 0.2, 0.2, 0.1),
            (0.7, 0.1, 0.1, 0.1),
            (0.4, 0.3, 0.2, 0.1),
        ]
        
        # 기타 파라미터
        rebalance_days_range = [5, 10, 21]
        dd_combos = [(-0.06, -0.10), (-0.08, -0.12), (-0.10, -0.15)]
        top_k_range = [10, 15, 20]
        tc_range = [0.001, 0.002]
        quality_boost_range = [0.0, 0.3]
        
        # 그리드 생성
        param_grid = []
        
        for rw in regime_weight_combos:
            for rt in regime_threshold_combos:
                for exp in exposure_combos:
                    for fw in factor_weight_combos:
                        for rebal in rebalance_days_range:
                            for dd_halt, dd_stop in dd_combos:
                                for top_k in top_k_range:
                                    for tc in tc_range:
                                        for qb in quality_boost_range:
                                            param_grid.append({
                                                'regime_weights': rw,
                                                'regime_thresholds': rt,
                                                'exposure_map': exp,
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
        
        # 레짐 점수 계산
        regime_scores, score_components = self.regime_detector.compute_regime_score(params['regime_weights'])
        
        # 팩터 결합
        fw = params['factor_weights']
        combined = (
            self.factors['mom_12_1'] * fw[0] +
            self.factors['mom_6_1'] * fw[1] +
            self.factors['low_vol'] * fw[2] +
            self.factors['quality'] * fw[3]
        )
        
        # 공통 인덱스/컬럼
        common_idx = combined.index.intersection(regime_scores.index)
        common_cols = combined.columns.intersection(self.data.returns.columns)
        
        signals_arr = combined.loc[common_idx, common_cols].values.astype(np.float64)
        returns_arr = self.data.returns.loc[common_idx, common_cols].values.astype(np.float64)
        quality_arr = self.factors['quality'].loc[common_idx, common_cols].values.astype(np.float64)
        regime_scores_arr = regime_scores.loc[common_idx].values.astype(np.float64)
        
        # 백테스트 실행
        port_rets, turnover, cost, dd_halt_cnt, dd_stop_cnt, rebal_cnt, regime_counts = fast_backtest_multi_regime(
            signals_arr, returns_arr,
            regime_scores_arr, np.array(params['regime_thresholds'], dtype=np.float64),
            np.array(params['exposure_map'], dtype=np.float64),
            params['top_k'], params['rebalance_days'],
            params['tc'], 0.001,
            params['signal_lag'],
            params['dd_halt'], params['dd_stop'], 0.04,
            params['quality_boost'],
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
            'rebal_count': rebal_cnt,
            'regime_counts': regime_counts.tolist()
        }
    
    def run_optimization(self, max_combinations: int = 50000):
        """파레토 최적화 실행"""
        
        param_grid = self.generate_param_grid()
        
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
                
                if (i + 1) % 500 == 0:
                    logger.info(f"진행: {i+1:,}/{len(param_grid):,} ({(i+1)/len(param_grid)*100:.1f}%)")
                    
                    best_sharpe = max(r['full_sharpe'] for r in results)
                    best_oos = max(r['oos_sharpe'] for r in results)
                    best_2022 = max(r['y2022_return'] for r in results)
                    logger.info(f"  현재 최고 - Sharpe: {best_sharpe:.2f}, OOS: {best_oos:.2f}, 2022: {best_2022:.2%}")
                    
            except Exception as e:
                logger.warning(f"백테스트 실패: {e}")
                continue
        
        self.results = results
        logger.info(f"파레토 최적화 완료: {len(results):,}개 결과")
        
        return results
    
    def find_pareto_frontier(self):
        """파레토 프론티어 추출"""
        
        if not self.results:
            return []
        
        pareto_front = []
        
        for result in self.results:
            is_dominated = False
            
            for other in self.results:
                if other is result:
                    continue
                
                dominates = (
                    other['full_sharpe'] >= result['full_sharpe'] and
                    other['full_mdd'] >= result['full_mdd'] and
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
        
        pareto_front.sort(key=lambda x: x['full_sharpe'], reverse=True)
        
        logger.info(f"파레토 프론티어: {len(pareto_front)}개 조합")
        
        return pareto_front
    
    def save_results(self, filepath: str):
        """결과 저장"""
        
        pareto_front = self.find_pareto_frontier()
        top_10 = sorted(self.results, key=lambda x: x['full_sharpe'], reverse=True)[:10]
        
        output = {
            'timestamp': datetime.now().isoformat(),
            'total_combinations': len(self.results),
            'pareto_frontier_count': len(pareto_front),
            'pareto_frontier': pareto_front[:20],
            'top_10_by_sharpe': top_10,
            'best_oos': max(self.results, key=lambda x: x['oos_sharpe']) if self.results else None,
            'best_2022': max(self.results, key=lambda x: x['y2022_return']) if self.results else None
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
    logger.info("ARES v11.7 다중 지표 레짐 + 파레토 최적화")
    logger.info("=" * 80)
    
    # 데이터 로드
    data = MultiSourceDataLoader(MAIN_DB_PATH, ETF_DB_PATH).load()
    
    # 팩터 계산
    factor_calc = FactorCalculator(data.prices, data.returns)
    factors = factor_calc.compute_all()
    
    # 파레토 최적화
    optimizer = MultiRegimeParetoOptimizer(data, factors)
    
    # 최적화 실행
    results = optimizer.run_optimization(max_combinations=30000)
    
    # 파레토 프론티어 추출
    pareto_front = optimizer.find_pareto_frontier()
    
    # 결과 출력
    logger.info("\n" + "=" * 80)
    logger.info("파레토 프론티어 Top 10")
    logger.info("=" * 80)
    
    for i, result in enumerate(pareto_front[:10]):
        logger.info(f"\n[{i+1}] Full Sharpe: {result['full_sharpe']:.2f}, OOS Sharpe: {result['oos_sharpe']:.2f}")
        logger.info(f"    MDD: {result['full_mdd']:.2%}, 2022: {result['y2022_return']:.2%}")
        logger.info(f"    IS Sharpe: {result['is_sharpe']:.2f}, Sharpe Decay: {(result['oos_sharpe']/result['is_sharpe']-1)*100:.1f}%")
        logger.info(f"    Params: top_k={result['params']['top_k']}, rebal={result['params']['rebalance_days']}")
        logger.info(f"    Regime Counts: {result['regime_counts']}")
        logger.info(f"    DD Halt/Stop: {result['dd_halt_count']}/{result['dd_stop_count']}")
    
    # 결과 저장
    output = optimizer.save_results('/home/ubuntu/multi_regime_pareto_results.json')
    
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
        logger.info(f"2022 Sharpe: {best['y2022_sharpe']:.2f}")
        logger.info(f"\n레짐 분포:")
        regime_names = ['ULTRA_BULL', 'BULL', 'NEUTRAL', 'CAUTION', 'CRISIS']
        for name, count in zip(regime_names, best['regime_counts']):
            logger.info(f"  {name}: {count}일")
        logger.info(f"\n최적 파라미터:")
        for k, v in best['params'].items():
            logger.info(f"  {k}: {v}")

if __name__ == "__main__":
    main()
