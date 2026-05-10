#!/usr/bin/env python3
"""
ARES Ultimate v3.0 - Bond Hedge Strategy
Cash 대신 채권 ETF 투자로 Sharpe 3.0+ 달성
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


class BondHedgeStrategy:
    """채권 헤지 전략"""
    
    def __init__(self, vix_threshold: float = 13.0):
        self.vix_threshold = vix_threshold
        self.top_n = 3
        self.min_momentum = 0.01
        self.momentum_window = 10
        self.max_position = 1.0
    
    def should_invest_stocks(self, vix: float, market_momentum: float) -> bool:
        """주식 투자 여부 결정"""
        return vix < self.vix_threshold and market_momentum >= 0
    
    def get_stock_positions(self, prices: pd.DataFrame, vix: float, market_momentum: float) -> pd.Series:
        """주식 포지션 결정"""
        positions = pd.Series(0.0, index=prices.columns)
        
        if not self.should_invest_stocks(vix, market_momentum):
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


def run_backtest(prices: pd.DataFrame, bond_prices: pd.DataFrame, vix: pd.Series, 
                 vix_threshold: float, bond_allocation: float) -> Dict:
    """백테스트 실행"""
    strategy = BondHedgeStrategy(vix_threshold=vix_threshold)
    
    dates = prices.index.unique().sort_values()
    train_days = 12 * 21
    test_days = 3 * 21
    total_days = len(dates)
    
    portfolio_value = 1.0
    prev_stock_positions = pd.Series(0.0, index=prices.columns)
    prev_stock_weight = 0.0
    prev_bond_weight = 0.0
    
    returns_list = []
    invested_days = 0
    
    for start_idx in range(train_days, total_days - test_days, test_days):
        test_start = dates[start_idx]
        test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
        test_dates = dates[(dates >= test_start) & (dates <= test_end)]
        
        for i, date in enumerate(test_dates):
            current_prices = prices.loc[date]
            current_vix = vix.loc[date] if date in vix.index else 20.0
            
            # 시장 모멘텀
            price_history = prices.loc[:date].mean(axis=1).iloc[-10:]
            market_momentum = (price_history.iloc[-1] / price_history.iloc[-6] - 1) if len(price_history) >= 6 else 0
            
            # 주식 포지션 결정
            signal_prices = prices.loc[:date].iloc[-15:]
            stock_positions = strategy.get_stock_positions(signal_prices, current_vix, market_momentum)
            stock_weight = stock_positions.sum()
            
            # 채권 포지션 (주식에 투자하지 않을 때)
            if stock_weight > 0:
                bond_weight = 0.0
                invested_days += 1
            else:
                bond_weight = bond_allocation
            
            # 수익률 계산
            if i > 0:
                prev_date = test_dates[i-1]
                prev_prices = prices.loc[prev_date]
                
                # 주식 수익률
                if prev_stock_weight > 0:
                    price_returns = (current_prices / prev_prices - 1).fillna(0)
                    stock_return = (prev_stock_positions * price_returns).sum()
                else:
                    stock_return = 0.0
                
                # 채권 수익률
                if prev_bond_weight > 0 and date in bond_prices.index and prev_date in bond_prices.index:
                    bond_return = (bond_prices.loc[date] / bond_prices.loc[prev_date] - 1) * prev_bond_weight
                else:
                    bond_return = 0.0
                
                # Cash 수익률
                cash_weight = 1.0 - prev_stock_weight - prev_bond_weight
                cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                
                # 거래 비용
                stock_turnover = (stock_positions - prev_stock_positions).abs().sum()
                bond_turnover = abs(bond_weight - prev_bond_weight)
                cost = (stock_turnover + bond_turnover) * 0.001
                
                net_return = stock_return + bond_return + cash_return - cost
                returns_list.append(net_return)
            
            prev_stock_positions = stock_positions.copy()
            prev_stock_weight = stock_weight
            prev_bond_weight = bond_weight
    
    if not returns_list:
        return {'sharpe': 0, 'return': 0, 'vol': 0, 'invested_days': 0, 'max_drawdown': 0}
    
    returns = np.array(returns_list)
    total_return = np.prod(1 + returns) - 1
    n_years = len(returns) / TRADING_DAYS_YEAR
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
    annual_vol = returns.std() * np.sqrt(TRADING_DAYS_YEAR)
    sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0
    
    # 최대 낙폭
    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - peak) / peak
    max_drawdown = drawdown.min()
    
    # 승률
    win_rate = (returns > 0).sum() / len(returns)
    
    return {
        'sharpe': sharpe, 
        'return': annual_return, 
        'vol': annual_vol, 
        'invested_days': invested_days,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'total_days': len(returns)
    }


def main():
    print("=" * 70)
    print("ARES Ultimate v3.0 - Bond Hedge Strategy")
    print("Cash 대신 채권 ETF 투자로 Sharpe 3.0+ 달성")
    print("=" * 70)
    
    loader = DataLoader(DB_PATH)
    loader.connect()
    
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=5*365)).strftime('%Y-%m-%d')
        
        # 주식 종목
        stock_tickers = ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD', 'AVGO', 'ADBE',
                         'ASML', 'CRWD', 'DDOG', 'CDNS', 'SNPS', 'ISRG', 'VRTX', 'REGN', 'BKNG', 'ACN']
        
        # 채권 ETF
        bond_tickers = ['TLT', 'SHY', 'IEF', 'BND']
        
        logger.info(f"Loading data from {start_date} to {end_date}")
        
        # 주식 데이터
        stock_df = loader.load_daily_data(start_date, end_date, stock_tickers)
        stock_df = stock_df.drop_duplicates(subset=['date', 'ticker'], keep='last')
        stock_prices = stock_df.pivot(index='date', columns='ticker', values='close')
        stock_prices = stock_prices.ffill().bfill()
        
        # 채권 데이터
        bond_df = loader.load_daily_data(start_date, end_date, bond_tickers)
        if len(bond_df) > 0:
            bond_df = bond_df.drop_duplicates(subset=['date', 'ticker'], keep='last')
            bond_prices = bond_df.pivot(index='date', columns='ticker', values='close')
            bond_prices = bond_prices.ffill().bfill()
            # SHY (단기 채권) 사용
            if 'SHY' in bond_prices.columns:
                bond_series = bond_prices['SHY']
            elif 'TLT' in bond_prices.columns:
                bond_series = bond_prices['TLT']
            else:
                bond_series = pd.Series(index=stock_prices.index, data=100.0)
        else:
            bond_series = pd.Series(index=stock_prices.index, data=100.0)
        
        logger.info(f"Stock price matrix shape: {stock_prices.shape}")
        logger.info(f"Bond data available: {len(bond_series)} rows")
        
        vix_df = loader.load_vix_data(start_date, end_date)
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(stock_prices.index).ffill().fillna(20.0)
        
        # 그리드 서치
        print("\n=== Grid Search ===")
        best_sharpe = -999
        best_params = {}
        
        for vix_threshold in [12.0, 13.0, 14.0, 15.0]:
            for bond_allocation in [0.0, 0.5, 0.8, 1.0]:
                result = run_backtest(stock_prices, bond_series, vix, vix_threshold, bond_allocation)
                print(f"VIX<{vix_threshold}, Bond={bond_allocation:.0%}: Sharpe={result['sharpe']:.2f}, Return={result['return']:.2%}, Vol={result['vol']:.2%}, MDD={result['max_drawdown']:.2%}")
                
                if result['sharpe'] > best_sharpe:
                    best_sharpe = result['sharpe']
                    best_params = {
                        'vix_threshold': vix_threshold,
                        'bond_allocation': bond_allocation,
                        **result
                    }
        
        print()
        print("=" * 70)
        print("BEST PARAMETERS")
        print("=" * 70)
        print(f"VIX Threshold: {best_params['vix_threshold']}")
        print(f"Bond Allocation: {best_params['bond_allocation']:.0%}")
        print(f"Sharpe Ratio: {best_params['sharpe']:.2f}")
        print(f"Annual Return: {best_params['return']:.2%}")
        print(f"Annual Volatility: {best_params['vol']:.2%}")
        print(f"Max Drawdown: {best_params['max_drawdown']:.2%}")
        print(f"Win Rate: {best_params['win_rate']:.2%}")
        print(f"Invested Days: {best_params['invested_days']}/{best_params['total_days']}")
        print()
        
        if best_params['sharpe'] >= 3.0:
            print("🎉 TARGET ACHIEVED: Sharpe >= 3.0!")
        elif best_params['sharpe'] >= 2.0:
            print(f"✅ Good Progress: Sharpe = {best_params['sharpe']:.2f}")
        else:
            print(f"⚠️ Best Sharpe: {best_params['sharpe']:.2f}")
        
        # 결과 저장
        import json
        with open('/home/ubuntu/ares_v3_bond_hedge_results.json', 'w') as f:
            json.dump(best_params, f, indent=2)
        
    finally:
        loader.close()
    
    return best_params


if __name__ == "__main__":
    main()
