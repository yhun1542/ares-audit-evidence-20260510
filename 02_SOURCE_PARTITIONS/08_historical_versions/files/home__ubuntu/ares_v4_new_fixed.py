#!/usr/bin/env python3
"""
ARES Ultimate v4.0 - Production Ready (Fixed)
Upgraded for Sharpe 3.0+ with multi-timeframe, fast regime detection, adaptive conditions.
Fixed: market_breadth table not required
"""

import sys
import os
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union
import logging
import json
import warnings
from collections import defaultdict
import math
from scipy import stats

warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
TRADING_DAYS_YEAR = 252
RISK_FREE_RATE = 0.05

CONFIG = {
    'initial_capital': 1000000.0,
    'transaction_cost': 0.001,
    'vix_threshold': 15.0,
    'momentum_threshold': 0.005,
    'top_n': 5,
    'max_position': 0.2,
    'tickers': ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD', 'AVGO', 'ADBE',
                'ASML', 'CRWD', 'DDOG', 'CDNS', 'SNPS', 'ISRG', 'VRTX', 'REGN', 'BKNG', 'ACN',
                'CRM', 'ORCL', 'NFLX', 'COST', 'PEP', 'INTC', 'QCOM', 'TXN', 'HON', 'UNP']
}


class DataLoader:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.cache = defaultdict(dict)
    
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        logger.info(f"Connected to DB: {self.db_path}")
    
    def close(self):
        if self.conn:
            self.conn.close()
    
    def load_daily_data(self, start_date: str, end_date: str, tickers: List[str]) -> pd.DataFrame:
        placeholders = ','.join(['?' for _ in tickers])
        query = f"""
        SELECT date, symbol as ticker, close, volume
        FROM daily_ohlcv
        WHERE date >= ? AND date <= ? AND symbol IN ({placeholders})
        ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date] + tickers)
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'ticker'], keep='last')
        return df
    
    def load_vix_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        query = "SELECT date, close as vix FROM vix WHERE date >= ? AND date <= ? ORDER BY date"
        df = pd.read_sql_query(query, self.conn, params=[start_date, end_date])
        df['date'] = pd.to_datetime(df['date'])
        return df


class FastRegimeDetector:
    """Fast Regime Detector - 5일 이내 레짐 감지"""
    
    def __init__(self):
        self.weights = {
            'vix_level': 0.35,
            'vix_change': 0.25,
            'momentum': 0.25,
            'volume_spike': 0.15
        }
    
    def detect(self, data: Dict) -> Tuple[str, float]:
        try:
            vix = data.get('vix', 20.0)
            vix_ma5 = data.get('vix_ma5', vix)
            market_momentum = data.get('market_momentum', 0.0)
            volume_ratio = data.get('volume_ratio', 1.0)
            
            # VIX Level Score
            if vix < 15:
                vix_score = 1.0
            elif vix < 20:
                vix_score = 0.5
            elif vix < 30:
                vix_score = -0.5
            else:
                vix_score = -1.0
            
            # VIX Change Score
            vix_change = (vix - vix_ma5) / (vix_ma5 + 1e-10)
            if vix_change < -0.1:
                change_score = 0.5
            elif vix_change > 0.2:
                change_score = -0.5
            else:
                change_score = 0.0
            
            # Momentum Score
            mom_score = np.clip(market_momentum * 10, -1, 1)
            
            # Volume Spike Score
            vol_score = -0.3 if volume_ratio > 2.0 else 0.0
            
            # Weighted Score
            total_score = (
                self.weights['vix_level'] * vix_score +
                self.weights['vix_change'] * change_score +
                self.weights['momentum'] * mom_score +
                self.weights['volume_spike'] * vol_score
            )
            
            # Regime Decision
            if total_score > 0.3:
                regime = 'BULL'
                confidence = min(1.0, 0.5 + total_score)
            elif total_score > 0:
                regime = 'NEUTRAL'
                confidence = 0.5
            elif total_score > -0.3:
                regime = 'BEAR'
                confidence = 0.5 - total_score
            else:
                regime = 'CRISIS'
                confidence = min(1.0, 0.5 - total_score)
            
            return regime, confidence
            
        except Exception as e:
            logger.error(f"Regime detection error: {e}")
            return 'NEUTRAL', 0.5


class MultiTimeframeSignal:
    """Multi-Timeframe Signal Generator"""
    
    def __init__(self):
        self.timeframes = [5, 10, 20]
        self.weights = [0.5, 0.3, 0.2]
    
    def generate(self, prices: pd.DataFrame) -> pd.Series:
        try:
            if len(prices) < 21:
                return pd.Series(dtype=float)
            
            signals = pd.Series(0.0, index=prices.columns)
            
            for tf, weight in zip(self.timeframes, self.weights):
                if len(prices) < tf + 1:
                    continue
                
                # Momentum
                returns = prices.iloc[-1] / prices.iloc[-tf-1] - 1
                
                # Volatility adjustment
                vol = prices.pct_change().tail(tf).std()
                vol_adj = 1.0 / (vol + 0.01)
                
                # Adjusted signal
                adj_signal = returns * vol_adj
                
                # Rank
                ranked = adj_signal.rank(pct=True)
                
                signals += ranked * weight
            
            # Final ranking
            return signals.rank(pct=True)
            
        except Exception as e:
            logger.error(f"Signal generation error: {e}")
            return pd.Series(dtype=float)


class AdaptiveInvestmentCondition:
    """Adaptive Investment Condition"""
    
    def __init__(self):
        self.base_vix_threshold = 16.0
        self.base_momentum_threshold = 0.005
    
    def should_invest(self, regime: str, data: Dict) -> Tuple[bool, float]:
        try:
            vix = data.get('vix', 20.0)
            vix_history = data.get('vix_history', pd.Series([20.0]))
            market_momentum = data.get('market_momentum', 0.0)
            
            # Adaptive thresholds based on regime
            if regime == 'BULL':
                vix_threshold = self.base_vix_threshold * 1.2  # 더 관대
                mom_threshold = self.base_momentum_threshold * 0.5
            elif regime == 'NEUTRAL':
                vix_threshold = self.base_vix_threshold
                mom_threshold = self.base_momentum_threshold
            else:
                vix_threshold = self.base_vix_threshold * 0.8  # 더 엄격
                mom_threshold = self.base_momentum_threshold * 2.0
            
            # VIX percentile
            vix_pct = (vix_history < vix).mean() if len(vix_history) > 0 else 0.5
            
            # Investment decision
            vix_ok = vix < vix_threshold
            mom_ok = market_momentum > mom_threshold
            vix_stable = vix_pct < 0.7  # VIX가 상위 30% 미만
            
            should_invest = vix_ok and mom_ok and vix_stable
            
            # Confidence
            if should_invest:
                confidence = 0.5 + (vix_threshold - vix) / vix_threshold * 0.3
                confidence += min(0.2, market_momentum * 5)
                confidence = min(1.0, confidence)
            else:
                confidence = 0.0
            
            return should_invest, confidence
            
        except Exception as e:
            logger.error(f"Investment condition error: {e}")
            return False, 0.0


class ProductionSystem:
    """Production Trading System"""
    
    def __init__(self):
        self.data_loader = DataLoader(DB_PATH)
        self.regime_detector = FastRegimeDetector()
        self.signal_generator = MultiTimeframeSignal()
        self.investment_condition = AdaptiveInvestmentCondition()
        
        self.portfolio_value = CONFIG['initial_capital']
        self.positions = pd.Series(dtype=float)
        self.results = []
    
    def _load_data(self, date: datetime, lookback_days: int = 60) -> Dict:
        end_date = date.strftime('%Y-%m-%d')
        start_date = (date - timedelta(days=lookback_days * 2)).strftime('%Y-%m-%d')
        
        prices_df = self.data_loader.load_daily_data(start_date, end_date, CONFIG['tickers'])
        vix_df = self.data_loader.load_vix_data(start_date, end_date)
        
        prices = prices_df.pivot(index='date', columns='ticker', values='close').ffill()
        volumes = prices_df.pivot(index='date', columns='ticker', values='volume').ffill()
        
        vix = vix_df.set_index('date')['vix'].reindex(prices.index).ffill().fillna(20.0)
        vix_ma5 = vix.rolling(5).mean().iloc[-1] if len(vix) >= 5 else vix.iloc[-1]
        vix_history = vix.tail(252)
        
        # Market momentum
        market_prices = prices.mean(axis=1)
        market_momentum = (market_prices.iloc[-1] / market_prices.iloc[-11] - 1) if len(market_prices) > 11 else 0
        
        # Volume ratio
        avg_volume = volumes.mean(axis=1)
        vol_ma20 = avg_volume.rolling(20).mean().iloc[-1] if len(avg_volume) >= 20 else avg_volume.iloc[-1]
        volume_ratio = avg_volume.iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
        
        return {
            'prices': prices,
            'vix': vix.iloc[-1],
            'vix_ma5': vix_ma5,
            'vix_history': vix_history,
            'market_momentum': market_momentum,
            'volume_ratio': volume_ratio
        }
    
    def run_daily(self, date: datetime) -> Dict:
        try:
            data = self._load_data(date)
            
            # Regime detection
            regime, regime_confidence = self.regime_detector.detect(data)
            
            # Investment decision
            should_invest, invest_confidence = self.investment_condition.should_invest(regime, data)
            
            # Position calculation
            if should_invest:
                signals = self.signal_generator.generate(data['prices'])
                
                # Top N selection
                top_signals = signals.nlargest(CONFIG['top_n'])
                top_signals = top_signals[top_signals > 0.5]  # 상위 50% 이상만
                
                if len(top_signals) > 0:
                    # Normalize
                    total_allocation = min(1.0, invest_confidence)
                    new_positions = top_signals / top_signals.sum() * total_allocation
                    new_positions = new_positions.clip(upper=CONFIG['max_position'])
                else:
                    new_positions = pd.Series(dtype=float)
            else:
                new_positions = pd.Series(dtype=float)
            
            self.positions = new_positions
            
            return {
                'date': date,
                'regime': regime,
                'regime_confidence': regime_confidence,
                'should_invest': should_invest,
                'invest_confidence': invest_confidence,
                'positions': new_positions.to_dict() if len(new_positions) > 0 else {},
                'equity_weight': new_positions.sum() if len(new_positions) > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Daily run error: {e}")
            return {'date': date, 'error': str(e)}


class WalkForwardBacktester:
    """Walk-Forward Backtester"""
    
    def __init__(self, train_months: int = 12, test_months: int = 3):
        self.train_months = train_months
        self.test_months = test_months
    
    def run(self, start_date: str, end_date: str) -> Dict:
        data_loader = DataLoader(DB_PATH)
        data_loader.connect()
        
        # Load all data
        prices_df = data_loader.load_daily_data(start_date, end_date, CONFIG['tickers'])
        vix_df = data_loader.load_vix_data(start_date, end_date)
        
        prices = prices_df.pivot(index='date', columns='ticker', values='close').ffill()
        volumes = prices_df.pivot(index='date', columns='ticker', values='volume').ffill()
        vix = vix_df.set_index('date')['vix'].reindex(prices.index).ffill().fillna(20.0)
        
        data_loader.close()
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        logger.info(f"Total trading days: {total_days}")
        
        results = {
            'dates': [],
            'returns': [],
            'regimes': [],
            'equity_weights': [],
            'vix_values': []
        }
        
        regime_detector = FastRegimeDetector()
        signal_generator = MultiTimeframeSignal()
        investment_condition = AdaptiveInvestmentCondition()
        
        prev_positions = pd.Series(dtype=float)
        
        fold_count = 0
        for start_idx in range(train_days, total_days - test_days, test_days):
            fold_count += 1
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            
            logger.info(f"Fold {fold_count}: Test {test_start.date()} ~ {test_end.date()}")
            
            test_dates = dates[(dates >= test_start) & (dates <= test_end)]
            
            for i, date in enumerate(test_dates):
                # Prepare data
                hist_prices = prices.loc[:date].iloc[:-1]
                hist_vix = vix.loc[:date].iloc[:-1]
                hist_volumes = volumes.loc[:date].iloc[:-1]
                
                if len(hist_prices) < 30:
                    continue
                
                current_vix = vix.loc[date] if date in vix.index else 20.0
                vix_ma5 = hist_vix.tail(5).mean() if len(hist_vix) >= 5 else current_vix
                
                market_prices = hist_prices.mean(axis=1)
                market_momentum = (market_prices.iloc[-1] / market_prices.iloc[-11] - 1) if len(market_prices) > 11 else 0
                
                avg_volume = hist_volumes.mean(axis=1)
                vol_ma20 = avg_volume.rolling(20).mean().iloc[-1] if len(avg_volume) >= 20 else avg_volume.iloc[-1]
                volume_ratio = avg_volume.iloc[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
                
                data = {
                    'prices': hist_prices,
                    'vix': current_vix,
                    'vix_ma5': vix_ma5,
                    'vix_history': hist_vix.tail(252),
                    'market_momentum': market_momentum,
                    'volume_ratio': volume_ratio
                }
                
                # Regime detection
                regime, regime_conf = regime_detector.detect(data)
                
                # Investment decision
                should_invest, invest_conf = investment_condition.should_invest(regime, data)
                
                # Position calculation
                if should_invest:
                    signals = signal_generator.generate(hist_prices)
                    top_signals = signals.nlargest(CONFIG['top_n'])
                    top_signals = top_signals[top_signals > 0.5]
                    
                    if len(top_signals) > 0:
                        total_allocation = min(1.0, invest_conf)
                        new_positions = top_signals / top_signals.sum() * total_allocation
                        new_positions = new_positions.clip(upper=CONFIG['max_position'])
                    else:
                        new_positions = pd.Series(dtype=float)
                else:
                    new_positions = pd.Series(dtype=float)
                
                # Calculate returns
                if i > 0:
                    prev_date = test_dates[i-1]
                    prev_prices_row = prices.loc[prev_date]
                    current_prices_row = prices.loc[date]
                    
                    stock_return = 0.0
                    if len(prev_positions) > 0:
                        for ticker, weight in prev_positions.items():
                            if ticker in prev_prices_row.index and ticker in current_prices_row.index:
                                ret = current_prices_row[ticker] / prev_prices_row[ticker] - 1
                                stock_return += weight * ret
                    
                    prev_equity_weight = prev_positions.sum() if len(prev_positions) > 0 else 0
                    cash_weight = max(0, 1.0 - prev_equity_weight)
                    cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                    
                    # Transaction cost
                    if len(new_positions) > 0 and len(prev_positions) > 0:
                        turnover = 0.0
                        all_tickers = set(prev_positions.index) | set(new_positions.index)
                        for ticker in all_tickers:
                            old_w = prev_positions.get(ticker, 0)
                            new_w = new_positions.get(ticker, 0)
                            turnover += abs(new_w - old_w)
                        cost = turnover * CONFIG['transaction_cost']
                    else:
                        cost = 0.0
                    
                    net_return = stock_return + cash_return - cost
                    
                    results['dates'].append(date)
                    results['returns'].append(net_return)
                    results['regimes'].append(regime)
                    results['equity_weights'].append(new_positions.sum() if len(new_positions) > 0 else 0)
                    results['vix_values'].append(current_vix)
                
                prev_positions = new_positions.copy() if len(new_positions) > 0 else pd.Series(dtype=float)
        
        return results
    
    def calculate_metrics(self, results: Dict) -> Dict:
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
        regimes = pd.Series(results['regimes'], index=results['dates'])
        equity_weights = pd.Series(results['equity_weights'], index=results['dates'])
        
        total_return = (1 + returns).prod() - 1
        n_years = len(returns) / TRADING_DAYS_YEAR
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        annual_vol = returns.std() * np.sqrt(TRADING_DAYS_YEAR)
        sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0
        
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        win_rate = (returns > 0).sum() / len(returns)
        
        invested_days = (equity_weights > 0.01).sum()
        investment_ratio = invested_days / len(equity_weights)
        
        # Invested period Sharpe
        invested_returns = returns[equity_weights > 0.01]
        if len(invested_returns) > 10:
            invested_annual_return = (1 + invested_returns).prod() ** (TRADING_DAYS_YEAR / len(invested_returns)) - 1
            invested_vol = invested_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            invested_sharpe = (invested_annual_return - RISK_FREE_RATE) / invested_vol if invested_vol > 0 else 0
        else:
            invested_sharpe = 0
        
        # Regime metrics
        regime_metrics = {}
        for regime in ['BULL', 'NEUTRAL', 'BEAR', 'CRISIS']:
            regime_returns = returns[regimes == regime]
            if len(regime_returns) > 10:
                regime_annual_return = (1 + regime_returns).prod() ** (TRADING_DAYS_YEAR / len(regime_returns)) - 1
                regime_vol = regime_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                regime_sharpe = (regime_annual_return - RISK_FREE_RATE) / regime_vol if regime_vol > 0 else 0
                regime_metrics[regime] = {
                    'days': int(len(regime_returns)),
                    'sharpe': float(regime_sharpe),
                    'annual_return': float(regime_annual_return),
                    'volatility': float(regime_vol)
                }
        
        return {
            'sharpe_ratio': float(sharpe),
            'invested_sharpe': float(invested_sharpe),
            'annual_return': float(annual_return),
            'annual_volatility': float(annual_vol),
            'max_drawdown': float(max_drawdown),
            'win_rate': float(win_rate),
            'total_days': int(len(returns)),
            'invested_days': int(invested_days),
            'investment_ratio': float(investment_ratio),
            'regime_metrics': regime_metrics
        }


def main():
    print("=" * 70)
    print("ARES Ultimate v4.0 - Multi-Timeframe + Fast Regime + Adaptive")
    print("Target: Sharpe Ratio 3.0+")
    print("=" * 70)
    
    start_date = "2019-01-01"
    end_date = "2025-01-01"
    
    backtester = WalkForwardBacktester(train_months=12, test_months=3)
    results = backtester.run(start_date, end_date)
    metrics = backtester.calculate_metrics(results)
    
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)
    print(f"Sharpe Ratio (Overall): {metrics['sharpe_ratio']:.2f}")
    print(f"Sharpe Ratio (Invested): {metrics['invested_sharpe']:.2f}")
    print(f"Annual Return: {metrics['annual_return']:.2%}")
    print(f"Annual Volatility: {metrics['annual_volatility']:.2%}")
    print(f"Max Drawdown: {metrics['max_drawdown']:.2%}")
    print(f"Win Rate: {metrics['win_rate']:.2%}")
    print(f"Investment Ratio: {metrics['investment_ratio']:.2%}")
    
    print("\n=== REGIME METRICS ===")
    for regime, rm in metrics.get('regime_metrics', {}).items():
        print(f"\n{regime}:")
        print(f"  Days: {rm['days']}")
        print(f"  Sharpe: {rm['sharpe']:.2f}")
        print(f"  Annual Return: {rm['annual_return']:.2%}")
        print(f"  Volatility: {rm['volatility']:.2%}")
    
    # Save results
    results_df = pd.DataFrame({
        'date': results['dates'],
        'return': results['returns'],
        'regime': results['regimes'],
        'vix': results['vix_values'],
        'equity_weight': results['equity_weights']
    })
    results_df.to_csv('/home/ubuntu/ares_v4_new_fixed_results.csv', index=False)
    
    with open('/home/ubuntu/ares_v4_new_fixed_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)
    
    print(f"\nResults saved to /home/ubuntu/ares_v4_new_fixed_*.csv/json")
    
    # Target achievement
    print("\n" + "=" * 70)
    print("TARGET ACHIEVEMENT")
    print("=" * 70)
    print(f"{'✅' if metrics['sharpe_ratio'] >= 3.0 else '❌'} Sharpe >= 3.0: {metrics['sharpe_ratio']:.2f}")
    print(f"{'✅' if metrics['investment_ratio'] >= 0.3 else '❌'} Investment >= 30%: {metrics['investment_ratio']:.1%}")
    print(f"{'✅' if metrics['max_drawdown'] >= -0.15 else '❌'} MDD >= -15%: {metrics['max_drawdown']:.2%}")


if __name__ == "__main__":
    main()
