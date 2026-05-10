#!/usr/bin/env python3
"""
ARES Ultimate v9.7 - Walk-Forward OOS 검증
==========================================

Walk-Forward 검증:
- 학습: 2018-2022 (5년)
- OOS 테스트: 2023-2024 (2년)

검증 항목:
1. IS vs OOS Sharpe 비교
2. 레짐별 성과 안정성
3. 파라미터 과적합 여부
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
class AresV97Config:
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    vix_ultra_low: float = 13.0
    vix_low: float = 16.0
    vix_moderate: float = 19.0
    vix_high: float = 23.0
    vix_crisis: float = 28.0
    
    trend_filter_enabled: bool = True
    ma_period: int = 200
    bear_market_leverage_mult: float = 0.7
    
    momentum_weight: float = 0.90
    quality_weight: float = 0.10
    
    low_regime_quality_boost: float = 1.8
    low_regime_momentum_reduce: float = 0.6
    
    target_vol: float = 0.12
    max_leverage: float = 1.5
    min_leverage: float = 0.2
    
    dd_halt_threshold: float = -0.08
    dd_stop_threshold: float = -0.12
    dd_recovery_threshold: float = 0.04
    
    leverage_ultra_low: float = 1.8
    leverage_low: float = 0.8
    leverage_moderate: float = 1.2
    leverage_high: float = 0.4
    leverage_crisis: float = 0.0
    
    n_stocks: int = 10
    max_position: float = 0.20
    max_sector_weight: float = 0.50
    
    rebalance_days: int = 21
    position_buffer: float = 0.05
    max_turnover_per_rebal: float = 0.50
    
    transaction_cost: float = 0.002

SECTOR_MAP = {
    'AAPL': 'Tech', 'MSFT': 'Tech', 'GOOGL': 'Tech', 'AMZN': 'Tech', 'NVDA': 'Tech',
    'META': 'Tech', 'TSLA': 'Tech', 'AMD': 'Tech', 'AVGO': 'Tech', 'ADBE': 'Tech',
    'NFLX': 'Tech', 'CRM': 'Tech', 'INTC': 'Tech', 'QCOM': 'Tech', 'TXN': 'Tech',
    'AMAT': 'Tech', 'MU': 'Tech', 'LRCX': 'Tech', 'KLAC': 'Tech', 'SNPS': 'Tech',
    'NOW': 'Tech', 'PANW': 'Tech', 'CRWD': 'Tech', 'DDOG': 'Tech', 'ZS': 'Tech',
    'FTNT': 'Tech', 'NET': 'Tech', 'SNOW': 'Tech', 'PLTR': 'Tech', 'COIN': 'Tech',
    'V': 'Fin', 'MA': 'Fin', 'JPM': 'Fin', 'BAC': 'Fin', 'GS': 'Fin',
    'MS': 'Fin', 'BLK': 'Fin', 'SCHW': 'Fin', 'AXP': 'Fin', 'C': 'Fin',
    'UNH': 'Health', 'JNJ': 'Health', 'PFE': 'Health', 'ABBV': 'Health', 'MRK': 'Health',
    'LLY': 'Health', 'TMO': 'Health', 'ABT': 'Health', 'DHR': 'Health', 'BMY': 'Health'
}

class RegimeDetector:
    def __init__(self, config):
        self.config = config
        self.prev_regime = None
    
    def detect(self, vix, is_bear_market=False):
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
        
        if is_bear_market and regime != 'CRISIS':
            lev *= self.config.bear_market_leverage_mult
        
        if self.prev_regime and self.prev_regime != regime:
            prev_lev = getattr(self.config, f'leverage_{self.prev_regime.lower()}', 0.5)
            lev = 0.7 * lev + 0.3 * prev_lev
        
        self.prev_regime = regime
        return regime, np.clip(lev, self.config.min_leverage, self.config.max_leverage)

class FactorEngine:
    def __init__(self, config):
        self.config = config
    
    def calculate_momentum(self, prices):
        if len(prices) < 252:
            return pd.Series(0, index=prices.columns)
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        mom = ret_12m - ret_1m
        mean, std = mom.mean(), mom.std()
        if std > 0:
            mom = (mom - mean) / std
        return mom
    
    def calculate_quality(self, prices):
        if len(prices) < 252:
            return pd.Series(0, index=prices.columns)
        returns = prices.pct_change().iloc[-252:]
        quality = returns.mean() / (returns.std() + 1e-6)
        mean, std = quality.mean(), quality.std()
        if std > 0:
            quality = (quality - mean) / std
        return quality
    
    def calculate_volatility(self, prices):
        if len(prices) < 60:
            return pd.Series(0.15, index=prices.columns)
        returns = prices.pct_change().iloc[-60:]
        return returns.std() * np.sqrt(252)
    
    def get_combined_score(self, prices, regime):
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
    def __init__(self, config):
        self.config = config
        self.peak_value = 1.0
        self.halt_level = 0
    
    def select_stocks_with_sector_limit(self, scores, n):
        valid_scores = scores.dropna().sort_values(ascending=False)
        if len(valid_scores) == 0:
            return []
        selected = []
        sector_counts = {}
        max_per_sector = int(n * self.config.max_sector_weight)
        for sym in valid_scores.index:
            sector = SECTOR_MAP.get(sym, 'Other')
            if sector_counts.get(sector, 0) < max_per_sector:
                selected.append(sym)
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
                if len(selected) >= n:
                    break
        return selected
    
    def calculate_weights(self, selected, volatilities, leverage):
        if not selected:
            return pd.Series()
        weights = pd.Series(0.0, index=selected)
        for sym in selected:
            vol = volatilities.get(sym, 0.15)
            weights[sym] = 1 / (vol + 0.10)
        weights = weights / weights.sum() * leverage
        weights = np.clip(weights, 0, self.config.max_position)
        return weights
    
    def apply_position_buffer(self, new_positions, old_positions):
        result = old_positions.copy()
        for sym in set(new_positions.index) | set(old_positions.index):
            new_w = new_positions.get(sym, 0)
            old_w = old_positions.get(sym, 0)
            if abs(new_w - old_w) > self.config.position_buffer:
                result[sym] = new_w
        return result
    
    def limit_turnover(self, new_positions, old_positions):
        total_turnover = (new_positions - old_positions).abs().sum()
        if total_turnover > self.config.max_turnover_per_rebal:
            blend_ratio = self.config.max_turnover_per_rebal / total_turnover
            return old_positions * (1 - blend_ratio) + new_positions * blend_ratio
        return new_positions
    
    def apply_drawdown_control(self, weights, current_value):
        if current_value > self.peak_value:
            self.peak_value = current_value
            self.halt_level = 0
        dd = (current_value / self.peak_value) - 1
        if dd < self.config.dd_stop_threshold:
            self.halt_level = 2
        elif dd < self.config.dd_halt_threshold:
            self.halt_level = 1
        recovery = (current_value / (self.peak_value * 0.92)) - 1
        if recovery > self.config.dd_recovery_threshold:
            self.halt_level = max(0, self.halt_level - 1)
        if self.halt_level == 2:
            return weights * 0.3
        elif self.halt_level == 1:
            return weights * 0.6
        return weights

class AresV97Engine:
    def __init__(self, config=None):
        self.config = config or AresV97Config()
        self.regime_detector = RegimeDetector(self.config)
        self.factor_engine = FactorEngine(self.config)
        self.portfolio_optimizer = PortfolioOptimizer(self.config)
    
    def load_data(self, start, end):
        conn = sqlite3.connect(self.config.db_path)
        symbols = list(SECTOR_MAP.keys())
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
        
        spy_query = f"""
        SELECT date, close FROM daily_ohlcv
        WHERE symbol = 'SPY' AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date
        """
        spy_df = pd.read_sql(spy_query, conn)
        spy_df['date'] = pd.to_datetime(spy_df['date'])
        spy_df = spy_df.drop_duplicates(subset=['date'], keep='last')
        spy = spy_df.set_index('date')['close']
        
        vix_query = f"""
        SELECT date, close as vix FROM daily_ohlcv
        WHERE symbol = 'VIX' AND date BETWEEN '{start}' AND '{end}'
        ORDER BY date
        """
        vix_df = pd.read_sql(vix_query, conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix_df = vix_df.drop_duplicates(subset=['date'], keep='last')
        vix = vix_df.set_index('date')['vix']
        
        conn.close()
        common_dates = prices.index.intersection(vix.index).intersection(spy.index)
        return prices.loc[common_dates], vix.loc[common_dates], spy.loc[common_dates]
    
    def backtest(self, start, end, warmup_start=None):
        if warmup_start:
            prices, vix, spy = self.load_data(warmup_start, end)
        else:
            prices, vix, spy = self.load_data(start, end)
        
        spy_ma200 = spy.rolling(self.config.ma_period).mean()
        
        # 실제 테스트 시작점
        test_start = pd.to_datetime(start)
        test_dates = prices.index[prices.index >= test_start]
        
        portfolio_value = 1.0
        values = [1.0]
        regime_stats = {}
        positions = pd.Series(0.0, index=prices.columns)
        total_costs = 0
        last_rebal = None
        regime = 'MODERATE'
        
        # 엔진 리셋
        self.regime_detector = RegimeDetector(self.config)
        self.portfolio_optimizer = PortfolioOptimizer(self.config)
        
        for i, date in enumerate(test_dates):
            should_rebal = (
                last_rebal is None or
                (date - last_rebal).days >= self.config.rebalance_days
            )
            
            if should_rebal:
                hist_prices = prices.loc[:date - timedelta(days=1)]
                current_vix = vix.loc[:date].iloc[-1] if len(vix.loc[:date]) > 0 else 20
                
                current_spy = spy.loc[:date].iloc[-1] if len(spy.loc[:date]) > 0 else 0
                current_ma200 = spy_ma200.loc[:date].iloc[-1] if len(spy_ma200.loc[:date]) > 0 else current_spy
                is_bear_market = current_spy < current_ma200 if self.config.trend_filter_enabled else False
                
                regime, leverage = self.regime_detector.detect(current_vix, is_bear_market)
                
                if regime == 'CRISIS':
                    new_positions = pd.Series(0.0, index=prices.columns)
                else:
                    scores = self.factor_engine.get_combined_score(hist_prices, regime)
                    selected = self.portfolio_optimizer.select_stocks_with_sector_limit(scores, self.config.n_stocks)
                    
                    if selected:
                        vols = self.factor_engine.calculate_volatility(hist_prices)
                        new_positions = self.portfolio_optimizer.calculate_weights(selected, vols, leverage)
                        new_positions = self.portfolio_optimizer.apply_drawdown_control(new_positions, portfolio_value)
                        
                        full_positions = pd.Series(0.0, index=prices.columns)
                        for sym, w in new_positions.items():
                            if sym in full_positions.index:
                                full_positions[sym] = w
                        new_positions = full_positions
                    else:
                        new_positions = pd.Series(0.0, index=prices.columns)
                
                new_positions = self.portfolio_optimizer.apply_position_buffer(new_positions, positions)
                new_positions = self.portfolio_optimizer.limit_turnover(new_positions, positions)
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.config.transaction_cost
                total_costs += cost
                
                positions = new_positions
                last_rebal = date
                
                if regime not in regime_stats:
                    regime_stats[regime] = {'days': 0, 'returns': []}
                regime_stats[regime]['days'] += 1
            
            if i > 0:
                prev_date = test_dates[i - 1]
                daily_returns = prices.loc[date] / prices.loc[prev_date] - 1
                daily_ret = (positions * daily_returns).sum()
                
                if should_rebal:
                    daily_ret -= cost
                
                portfolio_value *= (1 + daily_ret)
                values.append(portfolio_value)
                
                if regime in regime_stats:
                    regime_stats[regime]['returns'].append(daily_ret)
        
        returns = pd.Series(np.diff(values) / values[:-1])
        
        total_return = portfolio_value - 1
        years = len(test_dates) / 252
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
        
        regime_performance = {}
        for regime, stats in regime_stats.items():
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
            'total_costs': total_costs,
            'regime_performance': regime_performance
        }

if __name__ == "__main__":
    print("="*70)
    print("ARES Ultimate v9.7 - Walk-Forward OOS 검증")
    print("="*70)
    
    config = AresV97Config()
    engine = AresV97Engine(config)
    
    # 1. In-Sample (2020-2022)
    print("\n[1] In-Sample Test (2020-2022)")
    print("-"*50)
    is_results = engine.backtest('2020-01-01', '2022-12-31')
    print(f"IS Sharpe: {is_results['sharpe']:.2f}")
    print(f"IS Invested Sharpe: {is_results['invested_sharpe']:.2f}")
    print(f"IS Annual Return: {is_results['annual_return']*100:.1f}%")
    print(f"IS MDD: {is_results['mdd']*100:.1f}%")
    
    # 2. Out-of-Sample (2023-2024)
    print("\n[2] Out-of-Sample Test (2023-2024)")
    print("-"*50)
    oos_results = engine.backtest('2023-01-01', '2024-12-31', warmup_start='2022-01-01')
    print(f"OOS Sharpe: {oos_results['sharpe']:.2f}")
    print(f"OOS Invested Sharpe: {oos_results['invested_sharpe']:.2f}")
    print(f"OOS Annual Return: {oos_results['annual_return']*100:.1f}%")
    print(f"OOS MDD: {oos_results['mdd']*100:.1f}%")
    
    # 3. Full Period (2020-2024)
    print("\n[3] Full Period Test (2020-2024)")
    print("-"*50)
    full_results = engine.backtest('2020-01-01', '2024-12-31')
    print(f"Full Sharpe: {full_results['sharpe']:.2f}")
    print(f"Full Invested Sharpe: {full_results['invested_sharpe']:.2f}")
    print(f"Full Annual Return: {full_results['annual_return']*100:.1f}%")
    print(f"Full MDD: {full_results['mdd']*100:.1f}%")
    
    # 4. 과적합 분석
    print("\n" + "="*70)
    print("OVERFITTING ANALYSIS")
    print("="*70)
    sharpe_decay = (is_results['sharpe'] - oos_results['sharpe']) / is_results['sharpe'] * 100 if is_results['sharpe'] > 0 else 0
    print(f"IS Sharpe: {is_results['sharpe']:.2f}")
    print(f"OOS Sharpe: {oos_results['sharpe']:.2f}")
    print(f"Sharpe Decay: {sharpe_decay:.1f}%")
    
    if sharpe_decay < 30:
        print("✅ 과적합 위험 낮음 (Decay < 30%)")
    elif sharpe_decay < 50:
        print("⚠️ 과적합 위험 중간 (30% < Decay < 50%)")
    else:
        print("❌ 과적합 위험 높음 (Decay > 50%)")
    
    # 5. 레짐별 OOS 성과
    print("\n" + "="*70)
    print("OOS REGIME PERFORMANCE")
    print("="*70)
    for regime, perf in sorted(oos_results['regime_performance'].items()):
        print(f"{regime:12} | Days: {perf['days']:4} | Return: {perf['annual_return']*100:6.1f}% | Sharpe: {perf['sharpe']:5.2f}")
