#!/usr/bin/env python3
"""
ARES Ultimate v3.0 - Final Strategy
최적 파라미터: VIX < 13, Top 3 종목, 모멘텀 > 1%, 최대 포지션 100%
5년 백테스트
"""

import sys
import os
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List
import logging
import warnings
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
TRADING_DAYS_YEAR = 252
RISK_FREE_RATE = 0.05


class DataLoader:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
    
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
    
    def close(self):
        if self.conn:
            self.conn.close()
    
    def load_daily_data(self, start_date: str, end_date: str, tickers: List[str]) -> pd.DataFrame:
        placeholders = ','.join(['?' for _ in tickers])
        query = f"""
        SELECT date, symbol as ticker, close
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


class OptimalStrategy:
    """최적화된 전략"""
    
    def __init__(self):
        # 최적 파라미터 (v10 결과 기반)
        self.vix_threshold = 13.0
        self.top_n = 3
        self.min_momentum = 0.01
        self.max_position = 1.0
        self.momentum_window = 10
    
    def should_invest(self, vix: float, market_momentum: float) -> bool:
        """투자 여부 결정"""
        return vix < self.vix_threshold and market_momentum >= 0
    
    def get_positions(self, prices: pd.DataFrame, vix: float, market_momentum: float) -> pd.Series:
        """포지션 결정"""
        positions = pd.Series(0.0, index=prices.columns)
        
        if not self.should_invest(vix, market_momentum):
            return positions
        
        # 모멘텀 계산
        momentums = {}
        for ticker in prices.columns:
            price = prices[ticker].dropna()
            if len(price) < self.momentum_window + 1:
                continue
            
            mom = price.iloc[-1] / price.iloc[-self.momentum_window-1] - 1
            if mom >= self.min_momentum:
                momentums[ticker] = mom
        
        if not momentums:
            return positions
        
        # 상위 N개 선택
        sorted_moms = sorted(momentums.items(), key=lambda x: x[1], reverse=True)
        top_tickers = sorted_moms[:self.top_n]
        
        # VIX 기반 포지션 크기 조정
        vix_scale = max(0, (self.vix_threshold - vix) / self.vix_threshold)
        adjusted_max = self.max_position * (0.7 + 0.3 * vix_scale)
        
        # 모멘텀 가중 배분
        total_mom = sum(mom for _, mom in top_tickers)
        if total_mom > 0:
            for ticker, mom in top_tickers:
                weight = (mom / total_mom) * adjusted_max
                positions[ticker] = weight
        
        return positions


class WalkForwardBacktester:
    """Walk-Forward 백테스터"""
    
    def __init__(self, train_months: int = 12, test_months: int = 3, 
                 transaction_cost: float = 0.001):
        self.train_months = train_months
        self.test_months = test_months
        self.transaction_cost = transaction_cost
        self.strategy = OptimalStrategy()
    
    def run(self, prices: pd.DataFrame, vix: pd.Series) -> Dict:
        results = {
            'dates': [],
            'returns': [],
            'vix_values': [],
            'equity_weights': [],
            'portfolio_values': [],
            'invested': []
        }
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        logger.info(f"Total trading days: {total_days}")
        
        portfolio_value = 1.0
        prev_positions = pd.Series(0.0, index=prices.columns)
        prev_equity_weight = 0.0
        
        fold_count = 0
        for start_idx in range(train_days, total_days - test_days, test_days):
            fold_count += 1
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            
            logger.info(f"Fold {fold_count}: Test {test_start.date()} ~ {test_end.date()}")
            
            test_dates = dates[(dates >= test_start) & (dates <= test_end)]
            
            for i, date in enumerate(test_dates):
                current_prices = prices.loc[date]
                current_vix = vix.loc[date] if date in vix.index else 20.0
                
                # 시장 모멘텀
                price_history = prices.loc[:date].mean(axis=1).iloc[-10:]
                market_momentum = (price_history.iloc[-1] / price_history.iloc[-6] - 1) if len(price_history) >= 6 else 0
                
                # 포지션 결정
                signal_prices = prices.loc[:date].iloc[-15:]
                positions = self.strategy.get_positions(signal_prices, current_vix, market_momentum)
                equity_weight = positions.sum()
                invested = equity_weight > 0
                
                # 수익률 계산
                if i > 0:
                    prev_date = test_dates[i-1]
                    prev_prices = prices.loc[prev_date]
                    
                    if prev_equity_weight > 0:
                        price_returns = (current_prices / prev_prices - 1).fillna(0)
                        stock_return = (prev_positions * price_returns).sum()
                    else:
                        stock_return = 0.0
                    
                    cash_weight = 1.0 - prev_equity_weight
                    cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                    
                    turnover = (positions - prev_positions).abs().sum()
                    cost = turnover * self.transaction_cost
                    
                    net_return = stock_return + cash_return - cost
                    portfolio_value *= (1 + net_return)
                    
                    results['dates'].append(date)
                    results['returns'].append(net_return)
                    results['vix_values'].append(current_vix)
                    results['equity_weights'].append(equity_weight)
                    results['portfolio_values'].append(portfolio_value)
                    results['invested'].append(invested)
                
                prev_positions = positions.copy()
                prev_equity_weight = equity_weight
        
        return results
    
    def calculate_metrics(self, results: Dict) -> Dict:
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
        invested = pd.Series(results['invested'], index=results['dates'])
        
        total_return = (1 + returns).prod() - 1
        n_years = len(returns) / TRADING_DAYS_YEAR
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        annual_vol = returns.std() * np.sqrt(TRADING_DAYS_YEAR)
        sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0
        
        # 최대 낙폭
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        # 승률
        win_rate = (returns > 0).sum() / len(returns)
        
        # Calmar Ratio
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
        
        # 투자/비투자 기간별 지표
        invested_returns = returns[invested]
        cash_returns = returns[~invested]
        
        period_metrics = {}
        if len(invested_returns) > 0:
            i_mean = invested_returns.mean() * TRADING_DAYS_YEAR
            i_vol = invested_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            i_sharpe = (i_mean - RISK_FREE_RATE) / i_vol if i_vol > 0 else 0
            period_metrics['invested'] = {
                'count': len(invested_returns),
                'annual_return': i_mean,
                'sharpe': i_sharpe,
                'win_rate': (invested_returns > 0).sum() / len(invested_returns)
            }
        
        if len(cash_returns) > 0:
            c_mean = cash_returns.mean() * TRADING_DAYS_YEAR
            c_vol = cash_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            c_sharpe = (c_mean - RISK_FREE_RATE) / c_vol if c_vol > 0 else 0
            period_metrics['cash'] = {
                'count': len(cash_returns),
                'annual_return': c_mean,
                'sharpe': c_sharpe,
                'win_rate': (cash_returns > 0).sum() / len(cash_returns)
            }
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'calmar_ratio': calmar,
            'win_rate': win_rate,
            'num_trades': len(returns),
            'invested_days': invested.sum(),
            'cash_days': (~invested).sum(),
            'period_metrics': period_metrics
        }


def main():
    print("=" * 70)
    print("ARES Ultimate v3.0 - Final Strategy")
    print("최적 파라미터: VIX < 13, Top 3, 모멘텀 > 1%, 최대 포지션 100%")
    print("=" * 70)
    
    loader = DataLoader(DB_PATH)
    loader.connect()
    
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=5*365)).strftime('%Y-%m-%d')
        
        tickers = ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD', 'AVGO', 'ADBE',
                   'ASML', 'CRWD', 'DDOG', 'CDNS', 'SNPS', 'ISRG', 'VRTX', 'REGN', 'BKNG', 'ACN']
        
        logger.info(f"Loading data from {start_date} to {end_date}")
        
        daily_df = loader.load_daily_data(start_date, end_date, tickers)
        daily_df = daily_df.drop_duplicates(subset=['date', 'ticker'], keep='last')
        prices = daily_df.pivot(index='date', columns='ticker', values='close')
        prices = prices.ffill().bfill()
        
        logger.info(f"Price matrix shape: {prices.shape}")
        
        vix_df = loader.load_vix_data(start_date, end_date)
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(prices.index).ffill().fillna(20.0)
        
        backtester = WalkForwardBacktester(train_months=12, test_months=3, transaction_cost=0.001)
        
        logger.info("Running final backtest...")
        results = backtester.run(prices, vix)
        metrics = backtester.calculate_metrics(results)
        
        print()
        print("=" * 70)
        print("FINAL BACKTEST RESULTS")
        print("=" * 70)
        print(f"Total Return: {metrics['total_return']:.2%}")
        print(f"Annual Return: {metrics['annual_return']:.2%}")
        print(f"Annual Volatility: {metrics['annual_volatility']:.2%}")
        print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
        print(f"Max Drawdown: {metrics['max_drawdown']:.2%}")
        print(f"Calmar Ratio: {metrics['calmar_ratio']:.2f}")
        print(f"Win Rate: {metrics['win_rate']:.2%}")
        print(f"Number of Trades: {metrics['num_trades']}")
        print(f"Invested Days: {metrics['invested_days']}, Cash Days: {metrics['cash_days']}")
        print()
        print("Period Performance:")
        for period, pm in metrics.get('period_metrics', {}).items():
            print(f"  {period}: Days={pm['count']}, Return={pm['annual_return']:.2%}, Sharpe={pm['sharpe']:.2f}, WinRate={pm['win_rate']:.2%}")
        print()
        
        if metrics['sharpe_ratio'] >= 3.0:
            print("🎉 TARGET ACHIEVED: Sharpe >= 3.0!")
        elif metrics['sharpe_ratio'] >= 2.0:
            print(f"✅ Good Progress: Sharpe = {metrics['sharpe_ratio']:.2f}")
        else:
            print(f"⚠️ Current Sharpe: {metrics['sharpe_ratio']:.2f}")
        
        # 결과 저장
        pd.DataFrame(results).to_csv('/home/ubuntu/ares_v3_final_results.csv', index=False)
        
        import json
        with open('/home/ubuntu/ares_v3_final_metrics.json', 'w') as f:
            metrics_save = {k: float(v) if isinstance(v, (np.floating, np.integer)) else v 
                          for k, v in metrics.items() if k != 'period_metrics'}
            metrics_save['period_metrics'] = {
                p: {k: float(v) if isinstance(v, (np.floating, np.integer)) else v for k, v in pm.items()}
                for p, pm in metrics.get('period_metrics', {}).items()
            }
            json.dump(metrics_save, f, indent=2)
        
        logger.info("Results saved")
        
    finally:
        loader.close()
    
    return metrics


if __name__ == "__main__":
    main()
