#!/usr/bin/env python3
"""
ARES Ultimate v9.3 - 비용 최적화 + 턴오버 제어
==============================================

v9.2 대비 개선:
1. 월간 리밸런싱 (비용 절감)
2. 포지션 버퍼 도입 (5% 이하 변화 무시)
3. CRISIS 레짐 완전 현금 전환
4. 레짐 전환 시 점진적 조정

목표: Sharpe 2.2+, 비용 5% 이하
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')

@dataclass
class AresV93Config:
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # VIX Thresholds
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 20.0
    vix_high: float = 25.0
    vix_crisis: float = 30.0
    
    # Factor Weights
    momentum_weight: float = 0.85
    quality_weight: float = 0.15
    
    # LOW 레짐 조정
    low_regime_quality_boost: float = 2.0
    low_regime_momentum_reduce: float = 0.5
    
    # Vol Targeting
    target_vol: float = 0.12
    max_leverage: float = 1.5
    min_leverage: float = 0.2
    
    # Drawdown Control
    dd_halt_threshold: float = -0.10
    dd_recovery_threshold: float = 0.05
    
    # Regime Leverage
    leverage_ultra_low: float = 1.5
    leverage_low: float = 0.6
    leverage_moderate: float = 1.0
    leverage_high: float = 0.4
    leverage_crisis: float = 0.0
    
    # Portfolio
    n_stocks: int = 10
    max_position: float = 0.20
    
    # 턴오버 제어 (핵심!)
    rebalance_days: int = 21  # 월간
    position_buffer: float = 0.05  # 5% 이하 변화 무시
    max_turnover_per_rebal: float = 0.50  # 최대 50% 턴오버
    
    transaction_cost: float = 0.002

class RegimeDetector:
    def __init__(self, config: AresV93Config):
        self.config = config
        self.prev_regime = None
    
    def detect(self, vix: float) -> Tuple[str, float]:
        if vix < self.config.vix_ultra_low:
            regime, lev = 'ULTRA_LOW', self.config.leverage_ultra_low
        elif vix < self.config.vix_low:
            regime, lev = 'LOW', self.config.leverage_low
        elif vix < self.config.vix_moderate:
            regime, lev = 'MODERATE', self.config.leverage_moderate
        elif vix < self.config.vix_high:
            regime, lev = 'HIGH', self.config.leverage_high
        else:
            regime, lev = 'CRISIS', self.config.leverage_crisis
        
        # 레짐 전환 시 점진적 조정
        if self.prev_regime and self.prev_regime != regime:
            # 급격한 레버리지 변화 방지
            prev_lev = getattr(self.config, f'leverage_{self.prev_regime.lower()}', 1.0)
            lev = 0.7 * lev + 0.3 * prev_lev
        
        self.prev_regime = regime
        return regime, lev

class FactorEngine:
    def __init__(self, config: AresV93Config):
        self.config = config
    
    def calculate_momentum(self, prices: pd.DataFrame) -> pd.Series:
        if len(prices) < 252:
            return pd.Series(0, index=prices.columns)
        
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        mom = ret_12m - ret_1m
        
        mean, std = mom.mean(), mom.std()
        if std > 0:
            mom = (mom - mean) / std
        return mom
    
    def calculate_quality(self, prices: pd.DataFrame) -> pd.Series:
        if len(prices) < 252:
            return pd.Series(0, index=prices.columns)
        
        returns = prices.pct_change().iloc[-252:]
        quality = returns.mean() / (returns.std() + 1e-6)
        
        mean, std = quality.mean(), quality.std()
        if std > 0:
            quality = (quality - mean) / std
        return quality
    
    def calculate_volatility(self, prices: pd.DataFrame) -> pd.Series:
        if len(prices) < 60:
            return pd.Series(0.15, index=prices.columns)
        
        returns = prices.pct_change().iloc[-60:]
        return returns.std() * np.sqrt(252)
    
    def get_combined_score(self, prices: pd.DataFrame, regime: str) -> pd.Series:
        mom = self.calculate_momentum(prices)
        qual = self.calculate_quality(prices)
        
        mom_w = self.config.momentum_weight
        qual_w = self.config.quality_weight
        
        if regime == 'LOW':
            mom_w *= self.config.low_regime_momentum_reduce
            qual_w *= self.config.low_regime_quality_boost
            total = mom_w + qual_w
            mom_w /= total
            qual_w /= total
        
        return mom * mom_w + qual * qual_w

class PortfolioOptimizer:
    def __init__(self, config: AresV93Config):
        self.config = config
        self.peak_value = 1.0
        self.in_halt = False
    
    def select_stocks(self, scores: pd.Series, n: int) -> List[str]:
        valid_scores = scores.dropna()
        if len(valid_scores) == 0:
            return []
        return valid_scores.nlargest(n).index.tolist()
    
    def calculate_weights(self, selected: List[str], volatilities: pd.Series, leverage: float) -> pd.Series:
        if not selected:
            return pd.Series()
        
        weights = pd.Series(0.0, index=selected)
        for sym in selected:
            vol = volatilities.get(sym, 0.15)
            weights[sym] = 1 / (vol + 0.10)
        
        weights = weights / weights.sum() * leverage
        weights = np.clip(weights, 0, self.config.max_position)
        return weights
    
    def apply_position_buffer(self, new_positions: pd.Series, old_positions: pd.Series) -> pd.Series:
        """포지션 버퍼: 작은 변화 무시"""
        result = old_positions.copy()
        
        for sym in set(new_positions.index) | set(old_positions.index):
            new_w = new_positions.get(sym, 0)
            old_w = old_positions.get(sym, 0)
            
            # 변화가 버퍼보다 크면 업데이트
            if abs(new_w - old_w) > self.config.position_buffer:
                result[sym] = new_w
        
        return result
    
    def limit_turnover(self, new_positions: pd.Series, old_positions: pd.Series) -> pd.Series:
        """최대 턴오버 제한"""
        total_turnover = (new_positions - old_positions).abs().sum()
        
        if total_turnover > self.config.max_turnover_per_rebal:
            # 점진적 전환
            blend_ratio = self.config.max_turnover_per_rebal / total_turnover
            return old_positions * (1 - blend_ratio) + new_positions * blend_ratio
        
        return new_positions
    
    def apply_drawdown_control(self, weights: pd.Series, current_value: float) -> pd.Series:
        if current_value > self.peak_value:
            self.peak_value = current_value
        
        dd = (current_value / self.peak_value) - 1
        
        if dd < self.config.dd_halt_threshold:
            self.in_halt = True
        
        recovery = (current_value / (self.peak_value * 0.90)) - 1
        if recovery > self.config.dd_recovery_threshold:
            self.in_halt = False
        
        if self.in_halt:
            weights = weights * 0.5
        
        return weights

class AresV93Engine:
    def __init__(self, config: Optional[AresV93Config] = None):
        self.config = config or AresV93Config()
        self.regime_detector = RegimeDetector(self.config)
        self.factor_engine = FactorEngine(self.config)
        self.portfolio_optimizer = PortfolioOptimizer(self.config)
        
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
    
    def load_data(self, start: str, end: str) -> Tuple[pd.DataFrame, pd.Series]:
        conn = sqlite3.connect(self.config.db_path)
        
        symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'AVGO', 'ADBE',
            'NFLX', 'CRM', 'INTC', 'QCOM', 'TXN', 'AMAT', 'MU', 'LRCX', 'KLAC', 'SNPS',
            'NOW', 'PANW', 'CRWD', 'DDOG', 'ZS', 'FTNT', 'NET', 'SNOW', 'PLTR', 'COIN',
            'V', 'MA', 'JPM', 'BAC', 'GS', 'MS', 'BLK', 'SCHW', 'AXP', 'C',
            'UNH', 'JNJ', 'PFE', 'ABBV', 'MRK', 'LLY', 'TMO', 'ABT', 'DHR', 'BMY'
        ]
        
        symbols_str = ','.join([f"'{s}'" for s in symbols])
        
        query = f"""
        SELECT date, symbol, close
        FROM daily_ohlcv
        WHERE symbol IN ({symbols_str})
        AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date, symbol
        """
        df = pd.read_sql(query, conn)
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        prices = df.pivot(index='date', columns='symbol', values='close')
        prices = prices.ffill().dropna(axis=1, how='all')
        
        vix_query = f"""
        SELECT date, close as vix
        FROM daily_ohlcv
        WHERE symbol = 'VIX'
        AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date
        """
        vix_df = pd.read_sql(vix_query, conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix_df = vix_df.drop_duplicates(subset=['date'], keep='last')
        vix = vix_df.set_index('date')['vix']
        
        conn.close()
        
        common_dates = prices.index.intersection(vix.index)
        return prices.loc[common_dates], vix.loc[common_dates]
    
    def backtest(self, start: str = '2020-01-01', end: str = '2024-12-31') -> Dict:
        print(f"Loading data from {start} to {end}...")
        prices, vix = self.load_data(start, end)
        print(f"Loaded {len(prices)} days, {len(prices.columns)} symbols")
        
        warmup = 252
        dates = prices.index[warmup:]
        
        self.portfolio_value = 1.0
        self.values = [1.0]
        self.regime_stats = {}
        positions = pd.Series(0.0, index=prices.columns)
        total_costs = 0
        last_rebal = None
        regime = 'MODERATE'
        
        for i, date in enumerate(dates):
            should_rebal = (
                last_rebal is None or
                (date - last_rebal).days >= self.config.rebalance_days
            )
            
            if should_rebal:
                hist_prices = prices.loc[:date - timedelta(days=1)]
                current_vix = vix.loc[:date].iloc[-1] if len(vix.loc[:date]) > 0 else 20
                
                regime, leverage = self.regime_detector.detect(current_vix)
                
                if regime == 'CRISIS':
                    new_positions = pd.Series(0.0, index=prices.columns)
                else:
                    scores = self.factor_engine.get_combined_score(hist_prices, regime)
                    selected = self.portfolio_optimizer.select_stocks(scores, self.config.n_stocks)
                    
                    if selected:
                        vols = self.factor_engine.calculate_volatility(hist_prices)
                        new_positions = self.portfolio_optimizer.calculate_weights(selected, vols, leverage)
                        new_positions = self.portfolio_optimizer.apply_drawdown_control(new_positions, self.portfolio_value)
                        
                        full_positions = pd.Series(0.0, index=prices.columns)
                        for sym, w in new_positions.items():
                            if sym in full_positions.index:
                                full_positions[sym] = w
                        new_positions = full_positions
                    else:
                        new_positions = pd.Series(0.0, index=prices.columns)
                
                # 턴오버 제어
                new_positions = self.portfolio_optimizer.apply_position_buffer(new_positions, positions)
                new_positions = self.portfolio_optimizer.limit_turnover(new_positions, positions)
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.config.transaction_cost
                total_costs += cost
                
                positions = new_positions
                last_rebal = date
                
                if regime not in self.regime_stats:
                    self.regime_stats[regime] = {'days': 0, 'returns': []}
                self.regime_stats[regime]['days'] += 1
            
            if i > 0:
                prev_date = dates[i - 1]
                daily_returns = prices.loc[date] / prices.loc[prev_date] - 1
                daily_ret = (positions * daily_returns).sum()
                
                if should_rebal:
                    daily_ret -= cost
                
                self.portfolio_value *= (1 + daily_ret)
                self.values.append(self.portfolio_value)
                
                if regime in self.regime_stats:
                    self.regime_stats[regime]['returns'].append(daily_ret)
            
            if (i + 1) % 200 == 0:
                print(f"Progress: {i+1}/{len(dates)} days, Value: {self.portfolio_value:.4f}")
        
        returns = pd.Series(np.diff(self.values) / self.values[:-1])
        
        total_return = self.portfolio_value - 1
        years = len(dates) / 252
        annual_return = (1 + total_return) ** (1 / years) - 1
        
        rf_daily = 0.05 / 252
        excess_returns = returns - rf_daily
        sharpe = np.sqrt(252) * excess_returns.mean() / (excess_returns.std() + 1e-6)
        
        invested_returns = returns[returns.abs() > 1e-6]
        invested_sharpe = np.sqrt(252) * invested_returns.mean() / (invested_returns.std() + 1e-6) if len(invested_returns) > 0 else 0
        
        cumulative = np.cumprod(1 + returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        mdd = np.min(drawdown)
        
        invested_days = sum(1 for r in returns if abs(r) > 1e-6)
        investment_ratio = invested_days / len(returns)
        
        regime_performance = {}
        for regime, stats in self.regime_stats.items():
            if len(stats['returns']) > 0:
                rets = np.array(stats['returns'])
                regime_performance[regime] = {
                    'days': stats['days'],
                    'annual_return': np.mean(rets) * 252,
                    'sharpe': np.sqrt(252) * np.mean(rets) / (np.std(rets) + 1e-6),
                    'win_rate': np.mean(rets > 0)
                }
        
        return {
            'sharpe': sharpe,
            'invested_sharpe': invested_sharpe,
            'annual_return': annual_return,
            'total_return': total_return,
            'mdd': mdd,
            'investment_ratio': investment_ratio,
            'total_costs': total_costs,
            'regime_performance': regime_performance
        }

if __name__ == "__main__":
    print("="*60)
    print("ARES Ultimate v9.3 - 비용 최적화 + 턴오버 제어")
    print("="*60)
    
    config = AresV93Config()
    engine = AresV93Engine(config)
    
    results = engine.backtest('2020-01-01', '2024-12-31')
    
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"Sharpe Ratio: {results['sharpe']:.2f} (Target: 2.2)")
    print(f"Invested Sharpe: {results['invested_sharpe']:.2f} (Target: 3.1)")
    print(f"Annual Return: {results['annual_return']*100:.1f}%")
    print(f"Total Return: {results['total_return']*100:.1f}%")
    print(f"MDD: {results['mdd']*100:.1f}%")
    print(f"Investment Ratio: {results['investment_ratio']*100:.1f}%")
    print(f"Total Costs: {results['total_costs']*100:.2f}%")
    
    print("\n" + "="*60)
    print("REGIME PERFORMANCE")
    print("="*60)
    for regime, perf in sorted(results['regime_performance'].items()):
        print(f"{regime:12} | Days: {perf['days']:4} | Return: {perf['annual_return']*100:6.1f}% | Sharpe: {perf['sharpe']:5.2f} | Win: {perf['win_rate']*100:4.1f}%")
