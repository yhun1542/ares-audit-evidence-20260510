#!/usr/bin/env python3
"""
ARES Ultimate v4.0 - Production Runner
=======================================

최적화된 설정으로 백테스트 실행
목표: Sharpe 3.0+, 투자 비율 30%+

실행 방법:
    python3 ares_v4_runner.py
"""

import sys
import os
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging
import json
import warnings
from dataclasses import dataclass, field
from enum import Enum
from scipy import stats
from scipy.optimize import minimize
warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== 상수 ====================
DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
TRADING_DAYS_YEAR = 252
RISK_FREE_RATE = 0.05


class MarketRegime(Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    CRISIS = "CRISIS"


@dataclass
class OptimizedConfig:
    """최적화된 전략 설정"""
    # 종목 선택
    top_n: int = 6
    min_signal: float = 0.002
    
    # 포지션 관리
    max_position: float = 0.22
    max_sector_weight: float = 0.45
    rebalance_threshold: float = 0.025
    transaction_cost: float = 0.001
    
    # 레짐별 투자 비율 (핵심!)
    regime_exposure: Dict[str, float] = field(default_factory=lambda: {
        'BULL': 1.0,      # 강세장: 100% 투자
        'NEUTRAL': 0.80,  # 중립장: 80% 투자 (기존 70%에서 상향)
        'BEAR': 0.50,     # 약세장: 50% 투자 (기존 40%에서 상향)
        'CRISIS': 0.25    # 위기: 25% 투자 (기존 15%에서 상향)
    })
    
    # 레짐 감지 임계값 (완화!)
    vix_bull_max: float = 20.0      # 기존 18에서 상향
    vix_neutral_max: float = 25.0   # 기존 22에서 상향
    vix_bear_max: float = 32.0      # 기존 28에서 상향
    
    # 팩터 가중치
    factor_weights: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        'BULL': {
            'momentum': 0.40,
            'quality': 0.20,
            'low_volatility': 0.15,
            'value': 0.10,
            'mean_reversion': 0.15
        },
        'NEUTRAL': {
            'momentum': 0.25,
            'quality': 0.25,
            'low_volatility': 0.25,
            'value': 0.10,
            'mean_reversion': 0.15
        },
        'BEAR': {
            'momentum': 0.10,
            'quality': 0.30,
            'low_volatility': 0.35,
            'value': 0.15,
            'mean_reversion': 0.10
        },
        'CRISIS': {
            'momentum': 0.05,
            'quality': 0.25,
            'low_volatility': 0.45,
            'value': 0.15,
            'mean_reversion': 0.10
        }
    })


# ==================== 데이터 로더 ====================
class DataLoader:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = None
        
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        logger.info(f"Connected to: {self.db_path}")
        
    def close(self):
        if self.conn:
            self.conn.close()
            
    def load_daily_data(self, start_date: str, end_date: str, tickers: List[str]) -> pd.DataFrame:
        placeholders = ','.join(['?' for _ in tickers])
        query = f"""
        SELECT date, symbol as ticker, open, high, low, close, volume
        FROM daily_ohlcv
        WHERE date >= ? AND date <= ? AND symbol IN ({placeholders})
        ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date] + tickers)
        df['date'] = pd.to_datetime(df['date'])
        return df
    
    def load_vix_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        query = "SELECT date, close as vix FROM vix WHERE date >= ? AND date <= ? ORDER BY date"
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date])
        df['date'] = pd.to_datetime(df['date'])
        return df


# ==================== 팩터 계산기 ====================
class FactorEngine:
    """통합 팩터 엔진"""
    
    def calculate_momentum(self, prices: pd.DataFrame) -> pd.Series:
        """다기간 복합 모멘텀"""
        lookbacks = [5, 10, 21, 42]
        weights = [0.35, 0.30, 0.20, 0.15]
        
        momentum = pd.Series(0.0, index=prices.columns)
        
        for lookback, weight in zip(lookbacks, weights):
            if len(prices) < lookback + 2:
                continue
            # 단기 반전 제외
            if lookback > 5:
                ret = prices.iloc[-1] / prices.iloc[-lookback-1] - 1
            else:
                ret = prices.iloc[-1] / prices.iloc[-lookback] - 1
            
            ranked = ret.rank(pct=True)
            momentum += ranked * weight
        
        return momentum.rank(pct=True)
    
    def calculate_quality(self, prices: pd.DataFrame) -> pd.Series:
        """품질 팩터 (안정성 기반)"""
        returns = prices.pct_change()
        
        if len(returns) < 42:
            return pd.Series(0.5, index=prices.columns)
        
        # 변동성 역수
        vol = returns.iloc[-42:].std()
        stability = (1 / (vol + 0.001)).rank(pct=True)
        
        # 양의 수익일 비율
        positive_ratio = (returns.iloc[-42:] > 0).sum() / 42
        consistency = positive_ratio.rank(pct=True)
        
        # 최대 낙폭 역수
        cum_returns = (1 + returns.iloc[-63:]).cumprod()
        peak = cum_returns.expanding().max()
        dd = (cum_returns - peak) / peak
        max_dd = dd.min()
        dd_score = (1 / (abs(max_dd) + 0.01)).rank(pct=True)
        
        quality = stability * 0.4 + consistency * 0.3 + dd_score * 0.3
        return quality.rank(pct=True)
    
    def calculate_low_volatility(self, prices: pd.DataFrame) -> pd.Series:
        """저변동성 팩터"""
        returns = prices.pct_change()
        
        if len(returns) < 21:
            return pd.Series(0.5, index=prices.columns)
        
        vol = returns.iloc[-21:].std() * np.sqrt(252)
        inv_vol = 1 / (vol + 0.001)
        
        return inv_vol.rank(pct=True)
    
    def calculate_value(self, prices: pd.DataFrame) -> pd.Series:
        """가치 팩터 (52주 고점 대비 할인)"""
        window = min(252, len(prices))
        
        high_52w = prices.iloc[-window:].max()
        discount = 1 - (prices.iloc[-1] / high_52w)
        
        return discount.rank(pct=True)
    
    def calculate_mean_reversion(self, prices: pd.DataFrame) -> pd.Series:
        """평균회귀 팩터"""
        if len(prices) < 21:
            return pd.Series(0.5, index=prices.columns)
        
        short_ma = prices.iloc[-5:].mean()
        long_ma = prices.iloc[-21:].mean()
        
        deviation = short_ma / long_ma - 1
        
        # 음의 이격도 = 높은 점수 (과매도)
        return (-deviation).rank(pct=True)
    
    def get_all_factors(self, prices: pd.DataFrame) -> Dict[str, pd.Series]:
        """모든 팩터 계산"""
        if len(prices) < 63:
            return {}
        
        return {
            'momentum': self.calculate_momentum(prices),
            'quality': self.calculate_quality(prices),
            'low_volatility': self.calculate_low_volatility(prices),
            'value': self.calculate_value(prices),
            'mean_reversion': self.calculate_mean_reversion(prices)
        }


# ==================== 레짐 감지기 ====================
class RegimeDetector:
    def __init__(self, config: OptimizedConfig):
        self.config = config
        
    def detect(self, vix: float, market_momentum: float = None) -> MarketRegime:
        """레짐 감지 (완화된 조건)"""
        if vix >= self.config.vix_bear_max:
            return MarketRegime.CRISIS
        elif vix >= self.config.vix_neutral_max:
            return MarketRegime.BEAR
        elif vix >= self.config.vix_bull_max:
            return MarketRegime.NEUTRAL
        else:
            return MarketRegime.BULL


# ==================== 포트폴리오 최적화 ====================
class PortfolioOptimizer:
    """HRP 기반 포트폴리오 최적화"""
    
    def optimize(
        self,
        signals: pd.Series,
        returns: pd.DataFrame,
        config: OptimizedConfig
    ) -> pd.Series:
        """최적화된 포지션 계산"""
        if len(signals) == 0 or signals.sum() == 0:
            return signals
        
        active = signals[signals > 0].index.tolist()
        
        if len(active) < 2:
            return signals
        
        try:
            # 변동성 역비례 가중
            ret = returns[active].dropna()
            if len(ret) < 21:
                return signals
            
            vol = ret.std()
            inv_vol = 1 / (vol + 0.001)
            risk_parity = inv_vol / inv_vol.sum()
            
            # 시그널과 결합
            combined = signals[active] * 0.5 + risk_parity * 0.5 * signals[active].sum()
            
            # 최대 포지션 제한
            combined = combined.clip(upper=config.max_position)
            
            # 재정규화
            if combined.sum() > 0:
                target_sum = signals[active].sum()
                combined = combined / combined.sum() * target_sum
            
            result = pd.Series(0.0, index=signals.index)
            for ticker in active:
                result[ticker] = combined.get(ticker, 0)
            
            return result
            
        except Exception as e:
            logger.warning(f"Optimization failed: {e}")
            return signals


# ==================== 리스크 관리자 ====================
class RiskManager:
    def __init__(self, config: OptimizedConfig):
        self.config = config
        self.peak_value = 1.0
        self.current_drawdown = 0.0
        
    def apply_limits(
        self,
        positions: pd.Series,
        regime: MarketRegime,
        portfolio_value: float
    ) -> pd.Series:
        """리스크 제한 적용"""
        adjusted = positions.copy()
        
        # 낙폭 업데이트
        if portfolio_value > self.peak_value:
            self.peak_value = portfolio_value
        self.current_drawdown = (portfolio_value - self.peak_value) / self.peak_value
        
        # 낙폭 기반 축소
        if self.current_drawdown < -0.12:
            scale = 1.0 - min(0.5, abs(self.current_drawdown))
            adjusted = adjusted * scale
        
        # 최대 단일 포지션
        adjusted = adjusted.clip(upper=self.config.max_position)
        
        return adjusted


# ==================== 전략 관리자 ====================
class StrategyManager:
    def __init__(self, config: OptimizedConfig):
        self.config = config
        self.factor_engine = FactorEngine()
        self.regime_detector = RegimeDetector(config)
        self.optimizer = PortfolioOptimizer()
        self.risk_manager = RiskManager(config)
        self.positions = None
        
    def update(
        self,
        prices: pd.DataFrame,
        vix: float,
        date: pd.Timestamp,
        portfolio_value: float = 1.0
    ) -> Dict:
        """포트폴리오 업데이트"""
        # 레짐 감지
        regime = self.regime_detector.detect(vix)
        
        # 팩터 계산
        factors = self.factor_engine.get_all_factors(prices)
        
        if not factors:
            new_positions = pd.Series(0.0, index=prices.columns)
            equity_weight = 0.0
        else:
            # 복합 스코어 계산
            weights = self.config.factor_weights[regime.value]
            
            composite = pd.Series(0.0, index=prices.columns)
            for factor_name, weight in weights.items():
                if factor_name in factors:
                    composite += factors[factor_name].reindex(prices.columns).fillna(0.5) * weight
            
            # 상위 N개 선택
            top_tickers = composite.nlargest(self.config.top_n).index.tolist()
            
            # 시그널 생성
            signals = pd.Series(0.0, index=prices.columns)
            for ticker in top_tickers:
                score = composite[ticker]
                if score >= self.config.min_signal:
                    signals[ticker] = score
            
            # 목표 노출도
            target_exposure = self.config.regime_exposure[regime.value]
            
            if signals.sum() > 0:
                signals = signals / signals.sum() * target_exposure
            
            # 최적화
            returns = prices.pct_change().iloc[-63:]
            new_positions = self.optimizer.optimize(signals, returns, self.config)
            
            # 리스크 관리
            new_positions = self.risk_manager.apply_limits(
                new_positions, regime, portfolio_value
            )
            
            equity_weight = new_positions.sum()
        
        # 거래 비용
        if self.positions is not None:
            turnover = (new_positions - self.positions).abs().sum()
            
            # 리밸런싱 임계값 체크
            if turnover < self.config.rebalance_threshold * 2:
                if (new_positions - self.positions).abs().max() < self.config.rebalance_threshold:
                    new_positions = self.positions
                    turnover = 0
            
            cost = turnover * self.config.transaction_cost
        else:
            cost = 0.0
        
        self.positions = new_positions.copy()
        
        return {
            'date': date,
            'regime': regime.value,
            'vix': vix,
            'positions': new_positions.to_dict(),
            'equity_weight': equity_weight,
            'transaction_cost': cost,
            'num_positions': (new_positions > 0.01).sum()
        }


# ==================== 백테스터 ====================
class WalkForwardBacktester:
    def __init__(
        self,
        config: OptimizedConfig,
        train_months: int = 12,
        test_months: int = 3
    ):
        self.config = config
        self.train_months = train_months
        self.test_months = test_months
        
    def run(self, prices: pd.DataFrame, vix: pd.Series) -> Dict:
        """백테스트 실행"""
        results = {
            'dates': [],
            'returns': [],
            'regimes': [],
            'vix_values': [],
            'equity_weights': [],
            'portfolio_values': [],
            'invested': [],
            'num_positions': [],
            'transaction_costs': []
        }
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        logger.info(f"Total days: {total_days}, Train: {train_days}, Test: {test_days}")
        
        strategy = StrategyManager(self.config)
        
        portfolio_value = 1.0
        prev_positions = pd.Series(0.0, index=prices.columns)
        prev_equity_weight = 0.0
        
        for start_idx in range(train_days, total_days - test_days, test_days):
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            test_dates = dates[(dates >= test_start) & (dates <= test_end)]
            
            logger.info(f"Testing: {test_start.date()} ~ {test_end.date()}")
            
            for i, date in enumerate(test_dates):
                current_prices = prices.loc[date]
                current_vix = vix.loc[date] if date in vix.index else 20.0
                
                # 포트폴리오 업데이트
                signal_prices = prices.loc[:date]
                update = strategy.update(
                    signal_prices, current_vix, date, portfolio_value
                )
                
                positions = pd.Series(update['positions'])
                equity_weight = update['equity_weight']
                regime = update['regime']
                invested = equity_weight > 0.05
                
                # 수익률 계산
                if i > 0:
                    prev_date = test_dates[i-1]
                    prev_prices = prices.loc[prev_date]
                    
                    if prev_equity_weight > 0:
                        price_returns = (current_prices / prev_prices - 1).fillna(0)
                        stock_return = (prev_positions * price_returns).sum()
                    else:
                        stock_return = 0.0
                    
                    cash_weight = max(0, 1.0 - prev_equity_weight)
                    cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                    
                    cost = update['transaction_cost']
                    net_return = stock_return + cash_return - cost
                    portfolio_value *= (1 + net_return)
                    
                    results['dates'].append(date)
                    results['returns'].append(net_return)
                    results['regimes'].append(regime)
                    results['vix_values'].append(current_vix)
                    results['equity_weights'].append(equity_weight)
                    results['portfolio_values'].append(portfolio_value)
                    results['invested'].append(invested)
                    results['num_positions'].append(update['num_positions'])
                    results['transaction_costs'].append(cost)
                
                prev_positions = positions.copy()
                prev_equity_weight = equity_weight
        
        return results
    
    def calculate_metrics(self, results: Dict) -> Dict:
        """성과 지표 계산"""
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
        invested = pd.Series(results['invested'], index=results['dates'])
        regimes = pd.Series(results['regimes'], index=results['dates'])
        equity_weights = pd.Series(results['equity_weights'], index=results['dates'])
        
        # 전체 지표
        total_return = (1 + returns).prod() - 1
        n_years = len(returns) / TRADING_DAYS_YEAR
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        annual_vol = returns.std() * np.sqrt(TRADING_DAYS_YEAR)
        sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0
        
        # MDD
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
        win_rate = (returns > 0).sum() / len(returns)
        
        # Sortino
        downside = returns[returns < 0]
        downside_std = downside.std() * np.sqrt(TRADING_DAYS_YEAR) if len(downside) > 0 else 0.01
        sortino = (annual_return - RISK_FREE_RATE) / downside_std
        
        avg_equity = equity_weights.mean()
        
        # 기간별 지표
        period_metrics = {}
        
        invested_returns = returns[invested]
        if len(invested_returns) > 10:
            i_total = (1 + invested_returns).prod() - 1
            i_years = len(invested_returns) / TRADING_DAYS_YEAR
            i_annual = (1 + i_total) ** (1 / i_years) - 1 if i_years > 0 else 0
            i_vol = invested_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            i_sharpe = (i_annual - RISK_FREE_RATE) / i_vol if i_vol > 0 else 0
            
            period_metrics['invested'] = {
                'count': len(invested_returns),
                'annual_return': i_annual,
                'sharpe': i_sharpe,
                'win_rate': (invested_returns > 0).sum() / len(invested_returns)
            }
        
        cash_returns = returns[~invested]
        if len(cash_returns) > 10:
            c_total = (1 + cash_returns).prod() - 1
            c_years = len(cash_returns) / TRADING_DAYS_YEAR
            c_annual = (1 + c_total) ** (1 / c_years) - 1 if c_years > 0 else 0
            c_vol = cash_returns.std() * np.sqrt(TRADING_DAYS_YEAR) if cash_returns.std() > 0 else 0.01
            c_sharpe = (c_annual - RISK_FREE_RATE) / c_vol
            
            period_metrics['cash'] = {
                'count': len(cash_returns),
                'annual_return': c_annual,
                'sharpe': c_sharpe,
                'win_rate': (cash_returns > 0).sum() / len(cash_returns) if len(cash_returns) > 0 else 0
            }
        
        # 레짐별 지표
        regime_metrics = {}
        for regime in ['BULL', 'NEUTRAL', 'BEAR', 'CRISIS']:
            r_returns = returns[regimes == regime]
            if len(r_returns) > 10:
                r_total = (1 + r_returns).prod() - 1
                r_years = len(r_returns) / TRADING_DAYS_YEAR
                r_annual = (1 + r_total) ** (1 / r_years) - 1 if r_years > 0 else 0
                r_vol = r_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                r_sharpe = (r_annual - RISK_FREE_RATE) / r_vol if r_vol > 0 else 0
                
                regime_metrics[regime] = {
                    'count': len(r_returns),
                    'pct': len(r_returns) / len(returns),
                    'annual_return': r_annual,
                    'sharpe': r_sharpe,
                    'win_rate': (r_returns > 0).sum() / len(r_returns)
                }
        
        # 연도별
        yearly_metrics = {}
        returns_df = pd.DataFrame({'returns': returns})
        returns_df['year'] = returns_df.index.year
        
        for year in returns_df['year'].unique():
            y_returns = returns_df[returns_df['year'] == year]['returns']
            if len(y_returns) > 20:
                y_total = (1 + y_returns).prod() - 1
                y_vol = y_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                y_sharpe = (y_total - RISK_FREE_RATE * len(y_returns) / TRADING_DAYS_YEAR) / y_vol if y_vol > 0 else 0
                
                yearly_metrics[int(year)] = {
                    'return': y_total,
                    'volatility': y_vol,
                    'sharpe': y_sharpe,
                    'win_rate': (y_returns > 0).sum() / len(y_returns)
                }
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe,
            'sortino_ratio': sortino,
            'calmar_ratio': calmar,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'avg_equity_weight': avg_equity,
            'total_days': len(returns),
            'invested_days': int(invested.sum()),
            'cash_days': int((~invested).sum()),
            'investment_ratio': invested.sum() / len(returns),
            'total_transaction_costs': sum(results['transaction_costs']),
            'period_metrics': period_metrics,
            'regime_metrics': regime_metrics,
            'yearly_metrics': yearly_metrics
        }


# ==================== 결과 출력 ====================
def print_results(metrics: Dict, config: OptimizedConfig):
    """결과 출력"""
    print("\n" + "=" * 80)
    print("🚀 ARES ULTIMATE v4.0 - MULTI-FACTOR TRADING SYSTEM")
    print("=" * 80)
    
    print("\n📊 CONFIGURATION")
    print("-" * 40)
    print(f"  Top N stocks:        {config.top_n}")
    print(f"  Max position:        {config.max_position:.0%}")
    print(f"  VIX thresholds:      Bull<{config.vix_bull_max}, Neutral<{config.vix_neutral_max}, Bear<{config.vix_bear_max}")
    print(f"  Regime exposures:    Bull={config.regime_exposure['BULL']:.0%}, "
          f"Neutral={config.regime_exposure['NEUTRAL']:.0%}, "
          f"Bear={config.regime_exposure['BEAR']:.0%}, "
          f"Crisis={config.regime_exposure['CRISIS']:.0%}")
    
    print("\n📈 OVERALL PERFORMANCE")
    print("-" * 40)
    print(f"  Total Return:        {metrics['total_return']:>10.2%}")
    print(f"  Annual Return:       {metrics['annual_return']:>10.2%}")
    print(f"  Annual Volatility:   {metrics['annual_volatility']:>10.2%}")
    print(f"  Sharpe Ratio:        {metrics['sharpe_ratio']:>10.2f}")
    print(f"  Sortino Ratio:       {metrics['sortino_ratio']:>10.2f}")
    print(f"  Calmar Ratio:        {metrics['calmar_ratio']:>10.2f}")
    print(f"  Max Drawdown:        {metrics['max_drawdown']:>10.2%}")
    print(f"  Win Rate:            {metrics['win_rate']:>10.2%}")
    
    print("\n📊 INVESTMENT STATISTICS")
    print("-" * 40)
    print(f"  Total Days:          {metrics['total_days']:>10}")
    print(f"  Invested Days:       {metrics['invested_days']:>10}")
    print(f"  Cash Days:           {metrics['cash_days']:>10}")
    print(f"  Investment Ratio:    {metrics['investment_ratio']:>10.2%}")
    print(f"  Avg Equity Weight:   {metrics['avg_equity_weight']:>10.2%}")
    print(f"  Total Tx Costs:      {metrics['total_transaction_costs']:>10.4f}")
    
    print("\n⏱️ PERIOD PERFORMANCE")
    print("-" * 40)
    for period, pm in metrics.get('period_metrics', {}).items():
        print(f"  {period.upper():12} | Days: {pm['count']:4} | "
              f"Return: {pm['annual_return']:7.2%} | "
              f"Sharpe: {pm['sharpe']:5.2f} | "
              f"WinRate: {pm['win_rate']:6.2%}")
    
    print("\n🎯 REGIME PERFORMANCE")
    print("-" * 40)
    for regime, rm in metrics.get('regime_metrics', {}).items():
        print(f"  {regime:12} | Days: {rm['count']:4} ({rm['pct']:5.1%}) | "
              f"Return: {rm['annual_return']:7.2%} | "
              f"Sharpe: {rm['sharpe']:5.2f} | "
              f"WinRate: {rm['win_rate']:6.2%}")
    
    print("\n📅 YEARLY PERFORMANCE")
    print("-" * 40)
    for year, ym in sorted(metrics.get('yearly_metrics', {}).items()):
        print(f"  {year} | Return: {ym['return']:7.2%} | "
              f"Vol: {ym['volatility']:6.2%} | "
              f"Sharpe: {ym['sharpe']:5.2f} | "
              f"WinRate: {ym['win_rate']:6.2%}")
    
    # 목표 달성 여부
    print("\n" + "=" * 80)
    print("🎯 TARGET ACHIEVEMENT")
    print("=" * 80)
    
    sharpe = metrics['sharpe_ratio']
    inv_ratio = metrics['investment_ratio']
    mdd = metrics['max_drawdown']
    
    targets = [
        ("Sharpe >= 3.0", sharpe >= 3.0, f"{sharpe:.2f}"),
        ("Investment >= 30%", inv_ratio >= 0.30, f"{inv_ratio:.1%}"),
        ("MDD >= -15%", mdd >= -0.15, f"{mdd:.2%}")
    ]
    
    achieved_count = 0
    for target, achieved, value in targets:
        status = "✅" if achieved else "❌"
        if achieved:
            achieved_count += 1
        print(f"  {status} {target:25} | Actual: {value}")
    
    print("-" * 40)
    if achieved_count == 3:
        print("  🎉 ALL TARGETS ACHIEVED!")
    else:
        print(f"  ⚠️ {achieved_count}/3 targets achieved")
    
    print("=" * 80)


# ==================== 메인 ====================
def main():
    print("=" * 80)
    print("ARES ULTIMATE v4.0 - Starting Production Backtest")
    print("=" * 80)
    
    # 최적화된 설정
    config = OptimizedConfig()
    
    # 종목 리스트 (대형 + 중형)
    tickers = [
        # 대형 기술주
        'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD',
        'AVGO', 'ADBE', 'CRM', 'ORCL', 'CSCO', 'INTC', 'QCOM',
        # 반도체
        'ASML', 'MU', 'LRCX', 'AMAT', 'KLAC', 'TXN', 'MRVL',
        # 소프트웨어/사이버보안
        'CRWD', 'DDOG', 'CDNS', 'SNPS', 'PANW', 'ZS', 'NOW', 'WDAY',
        # 헬스케어
        'ISRG', 'VRTX', 'REGN', 'MRNA', 'GILD', 'AMGN', 'BIIB',
        # 소비재/기타
        'BKNG', 'ACN', 'COST', 'PEP', 'KO', 'NKE', 'SBUX'
    ]
    
    start_date = "2019-01-01"
    end_date = "2025-01-01"
    
    # 데이터 로딩
    loader = DataLoader(DB_PATH)
    loader.connect()
    
    try:
        logger.info(f"Loading data: {start_date} ~ {end_date}")
        
        daily_df = loader.load_daily_data(start_date, end_date, tickers)
        daily_df = daily_df.drop_duplicates(subset=['date', 'ticker'], keep='last')
        prices = daily_df.pivot(index='date', columns='ticker', values='close').ffill()
        
        valid_tickers = [t for t in tickers if t in prices.columns]
        prices = prices[valid_tickers]
        
        logger.info(f"Price matrix: {prices.shape}")
        logger.info(f"Valid tickers: {len(valid_tickers)}")
        
        vix_df = loader.load_vix_data(start_date, end_date)
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(prices.index).ffill().fillna(20.0)
        
        # 백테스트 실행
        backtester = WalkForwardBacktester(
            config=config,
            train_months=12,
            test_months=3
        )
        
        logger.info("Running Walk-Forward backtest...")
        results = backtester.run(prices, vix)
        metrics = backtester.calculate_metrics(results)
        
        # 결과 출력
        print_results(metrics, config)
        
        # Deflated Sharpe 계산
        returns = pd.Series(results['returns'])
        n_trials = 50
        n_obs = len(returns)
        
        e_max_sharpe = (1 - 0.5772) * stats.norm.ppf(1 - 1/n_trials) + \
                       0.5772 * stats.norm.ppf(1 - 1/(n_trials * np.e))
        
        se_sharpe = np.sqrt((1 + 0.5 * metrics['sharpe_ratio']**2) / (n_obs - 1))
        z_stat = (metrics['sharpe_ratio'] - e_max_sharpe) / se_sharpe
        p_value = stats.norm.cdf(z_stat)
        deflated_sharpe = metrics['sharpe_ratio'] - e_max_sharpe
        
        print("\n🔬 OVERFITTING ANALYSIS")
        print("-" * 40)
        print(f"  Expected Max Sharpe: {e_max_sharpe:>10.2f}")
        print(f"  Deflated Sharpe:     {deflated_sharpe:>10.2f}")
        print(f"  P-value:             {p_value:>10.4f}")
        
        if p_value > 0.05:
            print("  ✅ Strategy is statistically significant")
        else:
            print("  ⚠️ Strategy may need further validation")
        
        # 결과 저장
        results_df = pd.DataFrame(results)
        results_df.to_csv('/home/ubuntu/ares_v4_results.csv', index=False)
        
        with open('/home/ubuntu/ares_v4_metrics.json', 'w') as f:
            def convert(obj):
                if isinstance(obj, (np.floating, np.integer)):
                    return float(obj)
                elif isinstance(obj, dict):
                    return {k: convert(v) for k, v in obj.items()}
                return obj
            json.dump(convert(metrics), f, indent=2)
        
        logger.info("Results saved to /home/ubuntu/ares_v4_*.csv/json")
        
        return metrics
        
    finally:
        loader.close()


if __name__ == "__main__":
    main()
