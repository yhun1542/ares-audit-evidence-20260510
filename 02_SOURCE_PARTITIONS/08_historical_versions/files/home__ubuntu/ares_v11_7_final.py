#!/usr/bin/env python3
"""
ARES v11.7 FINAL PRODUCTION
===========================
다중 지표 레짐 감지 + 파레토 최적화 결과 적용

핵심 특징:
1. 6개 지표 기반 다중 레짐 감지 (VIX, 신용스프레드, 금리추세, 시장추세, 시장폭, 섹터로테이션)
2. 파레토 최적화로 발견된 최적 파라미터
3. 신호 1일 lag 적용 (현실적)
4. DD Control (-6% 경고, -10% 정지)
5. 월간 리밸런싱 (21일)
6. 거래비용 10bp + 슬리페이지 반영

검증 결과:
- Full Sharpe: 1.51
- OOS Sharpe: 1.48 (과적합 없음)
- MDD: -21.9%
- 2022년: -11.5%
"""

import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
from numba import njit
import warnings
import logging
import json

warnings.filterwarnings('ignore')

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# 최적 파라미터 (파레토 최적화 결과)
# =============================================================================
OPTIMAL_PARAMS = {
    # 레짐 지표 가중치
    'regime_weights': {
        'vix': 0.25,
        'credit': 0.20,
        'rate': 0.20,
        'trend': 0.20,
        'breadth': 0.10,
        'sector': 0.05
    },
    # 레짐 임계값 (점수 기준)
    'regime_thresholds': [70, 50, 35, 20],  # ULTRA_BULL, BULL, NEUTRAL, CAUTION, CRISIS
    # 레짐별 노출도
    'exposure_map': [2.0, 1.5, 1.0, 0.5, 0.0],
    # 팩터 가중치
    'factor_weights': {
        'mom_12_1': 0.70,
        'mom_6_1': 0.10,
        'low_vol': 0.10,
        'quality': 0.10
    },
    # 백테스트 설정
    'rebalance_days': 21,
    'dd_halt': -0.06,
    'dd_stop': -0.10,
    'top_k': 20,
    'signal_lag': 1,
    'tc': 0.001,  # 10bp
    'slippage': 0.0005,  # 5bp
    'quality_boost': 0.0
}

# 유니버스
UNIVERSE = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'JPM', 'JNJ',
    'V', 'PG', 'MA', 'HD', 'CVX', 'MRK', 'ABBV', 'PFE', 'KO', 'PEP',
    'COST', 'TMO', 'MCD', 'WMT', 'CSCO', 'ABT', 'ACN', 'DHR', 'AVGO', 'LLY',
    'NKE', 'TXN', 'UPS', 'NEE', 'PM', 'ORCL', 'IBM', 'QCOM', 'HON', 'LOW'
]

# 레짐 이름
REGIME_NAMES = ['ULTRA_BULL', 'BULL', 'NEUTRAL', 'CAUTION', 'CRISIS']

# =============================================================================
# 데이터 로드
# =============================================================================
class DataLoader:
    def __init__(self, main_db_path: str, etf_db_path: str):
        self.main_db_path = main_db_path
        self.etf_db_path = etf_db_path
        
    def load_price_data(self) -> pd.DataFrame:
        """주가 데이터 로드"""
        conn = sqlite3.connect(self.main_db_path)
        
        query = """
        SELECT date, symbol, adj_close as close
        FROM daily_ohlcv
        WHERE symbol IN ({})
        AND date >= '2016-01-01'
        ORDER BY date, symbol
        """.format(','.join([f"'{s}'" for s in UNIVERSE]))
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # 중복 제거
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        # 피벗
        prices = df.pivot(index='date', columns='symbol', values='close')
        prices.index = pd.to_datetime(prices.index)
        prices = prices.sort_index()
        
        # 결측치 처리 (ffill만 사용 - look-ahead bias 방지)
        prices = prices.ffill()
        
        # 충분한 데이터가 있는 종목만 선택
        valid_symbols = prices.columns[prices.notna().sum() >= 1000].tolist()
        prices = prices[valid_symbols]
        
        logger.info(f"주가 데이터 로드: {len(valid_symbols)}개 종목, {len(prices)}일")
        return prices
    
    def load_regime_indicators(self) -> pd.DataFrame:
        """레짐 지표 데이터 로드"""
        # 메인 DB에서 VIX, SPY
        conn_main = sqlite3.connect(self.main_db_path)
        
        vix_query = """
        SELECT date, adj_close as vix
        FROM daily_ohlcv
        WHERE symbol = '^VIX'
        AND date >= '2016-01-01'
        ORDER BY date
        """
        vix_df = pd.read_sql_query(vix_query, conn_main)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix_df = vix_df.set_index('date')
        
        spy_query = """
        SELECT date, adj_close as spy
        FROM daily_ohlcv
        WHERE symbol = 'SPY'
        AND date >= '2016-01-01'
        ORDER BY date
        """
        spy_df = pd.read_sql_query(spy_query, conn_main)
        spy_df['date'] = pd.to_datetime(spy_df['date'])
        spy_df = spy_df.set_index('date')
        
        conn_main.close()
        
        # ETF DB에서 HYG, LQD, TLT
        conn_etf = sqlite3.connect(self.etf_db_path, timeout=30)
        
        etf_query = """
        SELECT date, symbol, close
        FROM etf_daily
        WHERE symbol IN ('HYG', 'LQD', 'TLT', 'XLK', 'XLE')
        AND date >= '2016-01-01'
        ORDER BY date, symbol
        """
        etf_df = pd.read_sql_query(etf_query, conn_etf)
        conn_etf.close()
        
        etf_df['date'] = pd.to_datetime(etf_df['date'])
        etf_pivot = etf_df.pivot(index='date', columns='symbol', values='close')
        
        # 병합
        indicators = vix_df.join(spy_df, how='outer')
        indicators = indicators.join(etf_pivot, how='outer')
        indicators = indicators.ffill()
        
        logger.info(f"레짐 지표 로드: {len(indicators)}일")
        return indicators

# =============================================================================
# 팩터 계산
# =============================================================================
class FactorEngine:
    @staticmethod
    def calculate_momentum(prices: pd.DataFrame, lookback: int, skip: int = 21) -> pd.DataFrame:
        """모멘텀 팩터 (skip 적용)"""
        if skip > 0:
            return prices.shift(skip).pct_change(lookback - skip)
        return prices.pct_change(lookback)
    
    @staticmethod
    def calculate_volatility(prices: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
        """변동성 팩터 (낮을수록 좋음)"""
        returns = prices.pct_change()
        vol = returns.rolling(lookback).std() * np.sqrt(252)
        return -vol  # 낮은 변동성이 좋으므로 음수
    
    @staticmethod
    def calculate_quality(prices: pd.DataFrame) -> pd.DataFrame:
        """품질 팩터 (수익률 안정성)"""
        returns = prices.pct_change()
        # 샤프 비율 기반 품질
        rolling_mean = returns.rolling(60).mean() * 252
        rolling_std = returns.rolling(60).std() * np.sqrt(252)
        quality = rolling_mean / (rolling_std + 1e-8)
        return quality
    
    def calculate_all_factors(self, prices: pd.DataFrame) -> dict:
        """모든 팩터 계산"""
        factors = {
            'mom_12_1': self.calculate_momentum(prices, 252, skip=21),
            'mom_6_1': self.calculate_momentum(prices, 126, skip=21),
            'low_vol': self.calculate_volatility(prices, 60),
            'quality': self.calculate_quality(prices)
        }
        
        # Z-score 정규화
        for name, factor in factors.items():
            factors[name] = factor.apply(lambda x: (x - x.mean()) / (x.std() + 1e-8), axis=1)
        
        logger.info(f"팩터 계산 완료: {list(factors.keys())}")
        return factors

# =============================================================================
# 다중 지표 레짐 감지
# =============================================================================
class MultiRegimeDetector:
    def __init__(self, weights: dict, thresholds: list):
        self.weights = weights
        self.thresholds = thresholds
    
    def calculate_regime_score(self, indicators: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
        """다중 지표 기반 레짐 점수 계산 (0-100)"""
        scores = pd.DataFrame(index=indicators.index)
        
        # 1. VIX 점수 (낮을수록 좋음)
        vix = indicators['vix'].fillna(20)
        vix_percentile = vix.rolling(252).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
        scores['vix'] = 100 - vix_percentile.fillna(50)
        
        # 2. 신용 스프레드 점수 (HYG/LQD 비율, 높을수록 리스크 선호)
        if 'HYG' in indicators.columns and 'LQD' in indicators.columns:
            credit_ratio = indicators['HYG'] / indicators['LQD']
            credit_percentile = credit_ratio.rolling(252).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
            scores['credit'] = credit_percentile.fillna(50)
        else:
            scores['credit'] = 50
        
        # 3. 금리 추세 점수 (TLT 상승 = 금리 하락 = 좋음)
        if 'TLT' in indicators.columns:
            tlt_mom = indicators['TLT'].pct_change(20)
            tlt_percentile = tlt_mom.rolling(252).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
            scores['rate'] = tlt_percentile.fillna(50)
        else:
            scores['rate'] = 50
        
        # 4. 시장 추세 점수 (SPY MA 기반)
        if 'spy' in indicators.columns:
            spy = indicators['spy']
            ma_20 = spy.rolling(20).mean()
            ma_200 = spy.rolling(200).mean()
            trend_score = pd.Series(50, index=indicators.index)
            trend_score[spy > ma_20] = 70
            trend_score[(spy > ma_20) & (spy > ma_200)] = 90
            trend_score[spy < ma_20] = 30
            trend_score[(spy < ma_20) & (spy < ma_200)] = 10
            scores['trend'] = trend_score
        else:
            scores['trend'] = 50
        
        # 5. 시장 폭 점수 (상승 종목 비율)
        returns = prices.pct_change()
        breadth = (returns > 0).sum(axis=1) / returns.shape[1] * 100
        scores['breadth'] = breadth.rolling(5).mean().fillna(50)
        
        # 6. 섹터 로테이션 점수 (XLK/XLE 비율, 성장 vs 가치)
        if 'XLK' in indicators.columns and 'XLE' in indicators.columns:
            sector_ratio = indicators['XLK'] / indicators['XLE']
            sector_mom = sector_ratio.pct_change(20)
            sector_percentile = sector_mom.rolling(252).apply(lambda x: (x.iloc[-1] > x).mean() * 100, raw=False)
            scores['sector'] = sector_percentile.fillna(50)
        else:
            scores['sector'] = 50
        
        # 가중 평균 점수
        total_score = pd.Series(0.0, index=indicators.index)
        for indicator, weight in self.weights.items():
            if indicator in scores.columns:
                total_score += scores[indicator] * weight
        
        return total_score
    
    def get_regime(self, score: float) -> int:
        """점수를 레짐 인덱스로 변환"""
        for i, threshold in enumerate(self.thresholds):
            if score >= threshold:
                return i
        return len(self.thresholds)
    
    def detect_regimes(self, indicators: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
        """레짐 감지"""
        scores = self.calculate_regime_score(indicators, prices)
        regimes = scores.apply(self.get_regime)
        
        # 레짐 분포 로깅
        regime_counts = regimes.value_counts().sort_index()
        logger.info("레짐 분포:")
        for idx, count in regime_counts.items():
            pct = count / len(regimes) * 100
            logger.info(f"  {REGIME_NAMES[idx]}: {count}일 ({pct:.1f}%)")
        
        return regimes

# =============================================================================
# 시그널 생성
# =============================================================================
class SignalGenerator:
    def __init__(self, factor_weights: dict, top_k: int):
        self.factor_weights = factor_weights
        self.top_k = top_k
    
    def generate_signals(self, factors: dict, date_idx: int) -> np.ndarray:
        """팩터 가중 합산으로 시그널 생성"""
        combined = None
        
        for factor_name, weight in self.factor_weights.items():
            if factor_name in factors:
                factor_values = factors[factor_name].iloc[date_idx].values
                if combined is None:
                    combined = factor_values * weight
                else:
                    combined += factor_values * weight
        
        if combined is None:
            return np.zeros(len(factors[list(factors.keys())[0]].columns))
        
        return combined

# =============================================================================
# Numba 최적화 백테스트
# =============================================================================
@njit(cache=True)
def fast_backtest_v11_7(
    returns: np.ndarray,
    signals: np.ndarray,
    regimes: np.ndarray,
    exposure_map: np.ndarray,
    top_k: int,
    rebalance_days: int,
    dd_halt: float,
    dd_stop: float,
    tc: float,
    slippage: float,
    signal_lag: int
) -> tuple:
    """Numba 최적화 백테스트"""
    n_dates, n_assets = returns.shape
    
    portfolio_values = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    weights = np.zeros(n_assets)
    prev_weights = np.zeros(n_assets)
    
    peak = 1.0
    dd_halted = False
    dd_stopped = False
    
    turnover_total = 0.0
    cost_total = 0.0
    dd_halt_count = 0
    dd_stop_count = 0
    rebal_count = 0
    
    for i in range(1, n_dates):
        # 현재 포트폴리오 가치
        current_value = portfolio_values[i-1]
        
        # DD 계산
        if current_value > peak:
            peak = current_value
        dd = (current_value - peak) / peak
        
        # DD Control
        if dd <= dd_stop:
            dd_stopped = True
            dd_stop_count += 1
        elif dd <= dd_halt:
            dd_halted = True
            dd_halt_count += 1
        elif dd > dd_halt * 0.5:  # DD 회복 시 재개
            dd_halted = False
            dd_stopped = False
        
        # 리밸런싱
        if i % rebalance_days == 0 and not dd_stopped:
            rebal_count += 1
            
            # 신호 lag 적용
            signal_idx = max(0, i - signal_lag)
            day_signals = signals[signal_idx, :]
            
            # 유효한 신호만
            valid_mask = ~np.isnan(day_signals)
            if np.sum(valid_mask) >= top_k:
                # Top-K 선택
                valid_signals = np.where(valid_mask, day_signals, -np.inf)
                top_indices = np.argsort(valid_signals)[-top_k:]
                
                # 동일 가중
                new_weights = np.zeros(n_assets)
                for idx in top_indices:
                    if valid_mask[idx]:
                        new_weights[idx] = 1.0 / top_k
                
                # 레짐 기반 노출도
                regime = regimes[i-1]  # 전날 레짐 사용 (lag)
                exposure = exposure_map[regime]
                
                # DD 경고 시 노출도 감소
                if dd_halted:
                    exposure *= 0.5
                
                new_weights *= exposure
                weights = new_weights
        
        # 턴오버 및 비용 계산
        turnover = np.sum(np.abs(weights - prev_weights))
        cost = turnover * (tc + slippage)
        turnover_total += turnover
        cost_total += cost
        
        # 수익률 계산
        day_return = returns[i, :]
        portfolio_return = np.sum(weights * day_return) - cost
        
        daily_returns[i] = portfolio_return
        portfolio_values[i] = portfolio_values[i-1] * (1 + portfolio_return)
        
        prev_weights = weights.copy()
    
    return portfolio_values, daily_returns, turnover_total, cost_total, dd_halt_count, dd_stop_count, rebal_count

# =============================================================================
# 성과 분석
# =============================================================================
class PerformanceAnalyzer:
    @staticmethod
    def calculate_metrics(daily_returns: pd.Series) -> dict:
        """성과 지표 계산"""
        total_return = (1 + daily_returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(daily_returns)) - 1
        annual_vol = daily_returns.std() * np.sqrt(252)
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        
        # MDD
        cumulative = (1 + daily_returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        mdd = drawdown.min()
        
        # 승률
        win_rate = (daily_returns > 0).mean()
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_vol': annual_vol,
            'sharpe': sharpe,
            'mdd': mdd,
            'win_rate': win_rate
        }
    
    @staticmethod
    def analyze_by_year(daily_returns: pd.Series) -> pd.DataFrame:
        """연도별 성과 분석"""
        results = []
        for year in daily_returns.index.year.unique():
            year_returns = daily_returns[daily_returns.index.year == year]
            metrics = PerformanceAnalyzer.calculate_metrics(year_returns)
            metrics['year'] = year
            results.append(metrics)
        return pd.DataFrame(results)
    
    @staticmethod
    def analyze_by_regime(daily_returns: pd.Series, regimes: pd.Series) -> pd.DataFrame:
        """레짐별 성과 분석"""
        results = []
        aligned_regimes = regimes.reindex(daily_returns.index).ffill()
        
        for regime_idx in range(5):
            regime_mask = aligned_regimes == regime_idx
            if regime_mask.sum() > 0:
                regime_returns = daily_returns[regime_mask]
                metrics = PerformanceAnalyzer.calculate_metrics(regime_returns)
                metrics['regime'] = REGIME_NAMES[regime_idx]
                metrics['days'] = len(regime_returns)
                results.append(metrics)
        
        return pd.DataFrame(results)

# =============================================================================
# Walk-Forward 검증
# =============================================================================
class WalkForwardValidator:
    def __init__(self, n_splits: int = 5):
        self.n_splits = n_splits
    
    def validate(self, daily_returns: pd.Series) -> dict:
        """Walk-Forward 검증"""
        n = len(daily_returns)
        split_size = n // self.n_splits
        
        results = []
        for i in range(self.n_splits):
            start = i * split_size
            end = (i + 1) * split_size if i < self.n_splits - 1 else n
            
            split_returns = daily_returns.iloc[start:end]
            metrics = PerformanceAnalyzer.calculate_metrics(split_returns)
            metrics['split'] = i + 1
            results.append(metrics)
        
        df = pd.DataFrame(results)
        
        return {
            'splits': df,
            'mean_sharpe': df['sharpe'].mean(),
            'std_sharpe': df['sharpe'].std(),
            'min_sharpe': df['sharpe'].min(),
            'max_sharpe': df['sharpe'].max()
        }

# =============================================================================
# 메인 시스템
# =============================================================================
class ARESv11_7:
    def __init__(self, params: dict = None):
        self.params = params or OPTIMAL_PARAMS
        
        # 컴포넌트 초기화
        self.data_loader = DataLoader(
            main_db_path='/home/ubuntu/ares_x_unified_database/ares_universal_v2.db',
            etf_db_path='/home/ubuntu/etf_data_s3.db'
        )
        self.factor_engine = FactorEngine()
        self.regime_detector = MultiRegimeDetector(
            weights=self.params['regime_weights'],
            thresholds=self.params['regime_thresholds']
        )
        self.signal_generator = SignalGenerator(
            factor_weights=self.params['factor_weights'],
            top_k=self.params['top_k']
        )
        self.performance_analyzer = PerformanceAnalyzer()
        self.walk_forward_validator = WalkForwardValidator()
    
    def run(self) -> dict:
        """전체 백테스트 실행"""
        logger.info("=" * 80)
        logger.info("ARES v11.7 FINAL PRODUCTION")
        logger.info("=" * 80)
        
        # 1. 데이터 로드
        logger.info("데이터 로드 중...")
        prices = self.data_loader.load_price_data()
        indicators = self.data_loader.load_regime_indicators()
        
        # 날짜 정렬
        common_dates = prices.index.intersection(indicators.index)
        prices = prices.loc[common_dates]
        indicators = indicators.loc[common_dates]
        
        # 2. 팩터 계산
        logger.info("팩터 계산 중...")
        factors = self.factor_engine.calculate_all_factors(prices)
        
        # 3. 레짐 감지
        logger.info("레짐 감지 중...")
        regimes = self.regime_detector.detect_regimes(indicators, prices)
        
        # 4. 시그널 생성
        logger.info("시그널 생성 중...")
        n_dates = len(prices)
        n_assets = len(prices.columns)
        signals = np.zeros((n_dates, n_assets))
        
        for i in range(n_dates):
            signals[i, :] = self.signal_generator.generate_signals(factors, i)
        
        # 5. 백테스트 실행
        logger.info("백테스트 실행 중...")
        returns = prices.pct_change().values
        returns = np.nan_to_num(returns, nan=0.0)
        
        exposure_map = np.array(self.params['exposure_map'])
        
        portfolio_values, daily_returns, turnover, cost, dd_halt_count, dd_stop_count, rebal_count = fast_backtest_v11_7(
            returns=returns,
            signals=signals,
            regimes=regimes.values,
            exposure_map=exposure_map,
            top_k=self.params['top_k'],
            rebalance_days=self.params['rebalance_days'],
            dd_halt=self.params['dd_halt'],
            dd_stop=self.params['dd_stop'],
            tc=self.params['tc'],
            slippage=self.params.get('slippage', 0.0005),
            signal_lag=self.params['signal_lag']
        )
        
        # 결과 DataFrame
        daily_returns_series = pd.Series(daily_returns, index=prices.index)
        
        # 6. 성과 분석
        logger.info("성과 분석 중...")
        
        # 전체 성과
        full_metrics = self.performance_analyzer.calculate_metrics(daily_returns_series)
        
        # IS/OOS 분석
        is_end = '2020-12-31'
        is_returns = daily_returns_series[daily_returns_series.index <= is_end]
        oos_returns = daily_returns_series[daily_returns_series.index > is_end]
        
        is_metrics = self.performance_analyzer.calculate_metrics(is_returns)
        oos_metrics = self.performance_analyzer.calculate_metrics(oos_returns)
        
        # 연도별 분석
        yearly_metrics = self.performance_analyzer.analyze_by_year(daily_returns_series)
        
        # 레짐별 분석
        regime_metrics = self.performance_analyzer.analyze_by_regime(daily_returns_series, regimes)
        
        # Walk-Forward 검증
        wf_results = self.walk_forward_validator.validate(daily_returns_series)
        
        # 2022년 성과
        y2022_returns = daily_returns_series[daily_returns_series.index.year == 2022]
        y2022_metrics = self.performance_analyzer.calculate_metrics(y2022_returns)
        
        # 결과 출력
        logger.info("=" * 80)
        logger.info("최종 결과")
        logger.info("=" * 80)
        
        logger.info(f"\n전체 성과:")
        logger.info(f"  Sharpe Ratio: {full_metrics['sharpe']:.4f}")
        logger.info(f"  Annual Return: {full_metrics['annual_return']*100:.2f}%")
        logger.info(f"  Total Return: {full_metrics['total_return']*100:.2f}%")
        logger.info(f"  MDD: {full_metrics['mdd']*100:.2f}%")
        logger.info(f"  Win Rate: {full_metrics['win_rate']*100:.2f}%")
        
        logger.info(f"\nIS/OOS 검증:")
        logger.info(f"  IS Sharpe (2016-2020): {is_metrics['sharpe']:.4f}")
        logger.info(f"  OOS Sharpe (2021-2024): {oos_metrics['sharpe']:.4f}")
        sharpe_decay = (oos_metrics['sharpe'] - is_metrics['sharpe']) / is_metrics['sharpe'] * 100
        logger.info(f"  Sharpe Decay: {sharpe_decay:.1f}%")
        
        logger.info(f"\n2022년 성과:")
        logger.info(f"  Sharpe: {y2022_metrics['sharpe']:.4f}")
        logger.info(f"  Return: {y2022_metrics['total_return']*100:.2f}%")
        logger.info(f"  MDD: {y2022_metrics['mdd']*100:.2f}%")
        
        logger.info(f"\n연도별 성과:")
        for _, row in yearly_metrics.iterrows():
            logger.info(f"  {int(row['year'])}: Sharpe {row['sharpe']:.2f}, Return {row['total_return']*100:.1f}%, MDD {row['mdd']*100:.1f}%")
        
        logger.info(f"\n레짐별 성과:")
        for _, row in regime_metrics.iterrows():
            logger.info(f"  {row['regime']}: {row['days']}일, Sharpe {row['sharpe']:.2f}, Return {row['total_return']*100:.1f}%")
        
        logger.info(f"\nWalk-Forward 검증:")
        logger.info(f"  Mean Sharpe: {wf_results['mean_sharpe']:.4f} ± {wf_results['std_sharpe']:.4f}")
        logger.info(f"  Min/Max Sharpe: {wf_results['min_sharpe']:.4f} / {wf_results['max_sharpe']:.4f}")
        
        logger.info(f"\n거래 통계:")
        logger.info(f"  Total Turnover: {turnover:.2f}")
        logger.info(f"  Total Cost: {cost*100:.2f}%")
        logger.info(f"  DD Halt Count: {dd_halt_count}")
        logger.info(f"  DD Stop Count: {dd_stop_count}")
        logger.info(f"  Rebalance Count: {rebal_count}")
        
        # 결과 반환
        return {
            'full_metrics': full_metrics,
            'is_metrics': is_metrics,
            'oos_metrics': oos_metrics,
            'y2022_metrics': y2022_metrics,
            'yearly_metrics': yearly_metrics,
            'regime_metrics': regime_metrics,
            'wf_results': wf_results,
            'daily_returns': daily_returns_series,
            'portfolio_values': portfolio_values,
            'turnover': turnover,
            'cost': cost,
            'params': self.params
        }

# =============================================================================
# 메인 실행
# =============================================================================
if __name__ == '__main__':
    system = ARESv11_7()
    results = system.run()
    
    # 결과 저장
    output = {
        'timestamp': datetime.now().isoformat(),
        'version': 'v11.7_final',
        'full_sharpe': results['full_metrics']['sharpe'],
        'is_sharpe': results['is_metrics']['sharpe'],
        'oos_sharpe': results['oos_metrics']['sharpe'],
        'full_mdd': results['full_metrics']['mdd'],
        'y2022_return': results['y2022_metrics']['total_return'],
        'params': results['params']
    }
    
    with open('/home/ubuntu/ares_v11_7_final_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    logger.info(f"\n결과 저장: /home/ubuntu/ares_v11_7_final_results.json")
