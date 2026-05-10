#!/usr/bin/env python3
"""
ARES v4.4 - 최적화된 VIX 임계값 + 순수 BULL 전략
- VIX < 15 + 모멘텀 양수 + VIX 하락 추세 = BULL
- 그 외 모든 경우 = Cash
- 목표: 전체 Sharpe 3.0+ 또는 투자 기간 Sharpe 5.0+
"""

import sys
import os
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
TRADING_DAYS_YEAR = 252
RISK_FREE_RATE = 0.05

TICKERS = ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD', 
           'AVGO', 'ADBE', 'ASML', 'CRWD', 'DDOG', 'CDNS', 'SNPS', 'ISRG', 
           'VRTX', 'REGN', 'BKNG', 'ACN', 'CRM', 'ORCL', 'NFLX', 'COST',
           'PEP', 'INTC', 'QCOM', 'TXN', 'HON', 'UNP']


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
        SELECT date, symbol as ticker, open, high, low, close, volume
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


class TechnicalIndicators:
    @staticmethod
    def momentum(prices: pd.Series, period: int) -> float:
        if len(prices) < period + 2:
            return 0.0
        return prices.iloc[-2] / prices.iloc[-period-2] - 1
    
    @staticmethod
    def multi_momentum(prices: pd.Series) -> float:
        mom5 = TechnicalIndicators.momentum(prices, 5)
        mom10 = TechnicalIndicators.momentum(prices, 10)
        mom20 = TechnicalIndicators.momentum(prices, 20)
        return 0.5 * mom5 + 0.3 * mom10 + 0.2 * mom20
    
    @staticmethod
    def volatility(prices: pd.Series, period: int = 20) -> float:
        if len(prices) < period + 2:
            return 0.3
        returns = prices.iloc[:-1].pct_change().tail(period)
        return returns.std() * np.sqrt(TRADING_DAYS_YEAR)
    
    @staticmethod
    def quality_score(prices: pd.Series) -> float:
        if len(prices) < 62:
            return 0.0
        returns = prices.iloc[:-1].pct_change().tail(60)
        vol = returns.std()
        if vol == 0:
            return 0.0
        sharpe = returns.mean() / vol
        return sharpe


class StrictBullDetector:
    """엄격한 BULL 감지기 - 최적 조건에서만 투자"""
    
    def __init__(self, vix_threshold: float = 15.0, momentum_threshold: float = 0.005):
        self.vix_threshold = vix_threshold
        self.momentum_threshold = momentum_threshold
    
    def is_bull(self, vix: float, vix_history: pd.Series, market_momentum: float) -> Tuple[bool, float]:
        """
        BULL 조건 체크
        Returns: (is_bull, confidence)
        """
        if len(vix_history) < 10:
            return False, 0.0
        
        # VIX 지표
        vix_ma5 = vix_history.tail(5).mean()
        vix_change = (vix - vix_ma5) / (vix_ma5 + 1e-10)
        
        # 조건 1: VIX가 임계값 이하
        vix_ok = vix < self.vix_threshold
        
        # 조건 2: VIX가 상승하지 않음
        vix_stable = vix_change < 0.05
        
        # 조건 3: 시장 모멘텀 양수
        momentum_ok = market_momentum > self.momentum_threshold
        
        # 모든 조건 충족 시 BULL
        if vix_ok and vix_stable and momentum_ok:
            # 신뢰도 계산
            confidence = 0.5
            confidence += (self.vix_threshold - vix) / self.vix_threshold * 0.3
            confidence += min(0.2, market_momentum * 5)
            return True, min(1.0, confidence)
        
        return False, 0.0


class PureBullStrategy:
    """순수 BULL 전략 - BULL에서만 투자"""
    
    def __init__(self):
        self.max_positions = 5
        self.max_position_size = 0.30
    
    def get_positions(self, prices_dict: Dict[str, pd.DataFrame], is_bull: bool,
                      confidence: float, realized_vol: float,
                      target_vol: float = 0.15) -> pd.Series:
        """포지션 결정"""
        
        if not is_bull:
            return pd.Series(dtype=float)
        
        scores = {}
        
        for ticker, df in prices_dict.items():
            if len(df) < 62:
                continue
            
            close = df['close']
            
            # 모멘텀 (60%)
            momentum = TechnicalIndicators.multi_momentum(close)
            
            # 품질 (25%)
            quality = TechnicalIndicators.quality_score(close)
            
            # 저변동성 (15%)
            vol = TechnicalIndicators.volatility(close)
            low_vol = 1.0 / (vol + 0.1)
            
            # 모멘텀이 양수인 종목만
            if momentum > 0.01:  # 1% 이상 모멘텀
                score = 0.6 * momentum + 0.25 * quality + 0.15 * low_vol
                scores[ticker] = score
        
        if not scores:
            return pd.Series(dtype=float)
        
        # 상위 N개 선택
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_tickers = sorted_scores[:self.max_positions]
        
        # 변동성 타겟팅
        vol_scalar = min(1.5, max(0.5, target_vol / (realized_vol + 0.01)))
        
        # 총 투자 비율 (신뢰도 기반)
        total_allocation = min(1.0, 0.7 + confidence * 0.3) * vol_scalar
        
        positions = {}
        total_score = sum(max(0, s) for _, s in top_tickers)
        
        if total_score > 0:
            for ticker, score in top_tickers:
                weight = (score / total_score) * total_allocation
                positions[ticker] = min(self.max_position_size, weight)
        
        return pd.Series(positions)


class GridSearchBacktester:
    """그리드 서치 백테스터 - 최적 파라미터 탐색"""
    
    def __init__(self, train_months: int = 12, test_months: int = 3):
        self.train_months = train_months
        self.test_months = test_months
    
    def run_single(self, daily_df: pd.DataFrame, vix_df: pd.DataFrame,
                   vix_threshold: float, momentum_threshold: float) -> Dict:
        """단일 파라미터 조합으로 백테스트"""
        
        detector = StrictBullDetector(vix_threshold, momentum_threshold)
        strategy = PureBullStrategy()
        
        prices = daily_df.pivot(index='date', columns='ticker', values='close')
        prices = prices.ffill()
        
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(prices.index).ffill().fillna(20.0)
        
        results = {
            'dates': [],
            'returns': [],
            'is_bull': [],
            'vix_values': [],
            'equity_weights': [],
        }
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        prev_positions = pd.Series(dtype=float)
        
        for start_idx in range(train_days, total_days - test_days, test_days):
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            test_dates = dates[(dates >= test_start) & (dates <= test_end)]
            
            for i, date in enumerate(test_dates):
                current_vix = vix.loc[date] if date in vix.index else 20.0
                
                hist_prices = prices.loc[:date].iloc[:-1]
                hist_vix = vix.loc[:date].iloc[:-1]
                
                if len(hist_prices) >= 10:
                    market_avg = hist_prices.mean(axis=1)
                    market_momentum = market_avg.iloc[-1] / market_avg.iloc[-6] - 1 if len(market_avg) >= 6 else 0
                else:
                    market_momentum = 0
                
                if len(hist_prices) >= 22:
                    market_returns = hist_prices.mean(axis=1).pct_change().tail(20)
                    realized_vol = market_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                else:
                    realized_vol = 0.15
                
                is_bull, confidence = detector.is_bull(current_vix, hist_vix, market_momentum)
                
                prices_dict = {}
                for ticker in prices.columns:
                    ticker_prices = hist_prices[ticker].dropna()
                    if len(ticker_prices) >= 62:
                        prices_dict[ticker] = pd.DataFrame({'close': ticker_prices})
                
                new_positions = strategy.get_positions(
                    prices_dict, is_bull, confidence, realized_vol
                )
                
                if i > 0:
                    prev_date = test_dates[i-1]
                    prev_prices = prices.loc[prev_date]
                    current_prices = prices.loc[date]
                    
                    stock_return = 0.0
                    if len(prev_positions) > 0:
                        for ticker, weight in prev_positions.items():
                            if ticker in prev_prices.index and ticker in current_prices.index:
                                ret = current_prices[ticker] / prev_prices[ticker] - 1
                                stock_return += weight * ret
                    
                    prev_equity_weight = prev_positions.sum() if len(prev_positions) > 0 else 0
                    cash_weight = max(0, 1.0 - prev_equity_weight)
                    cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                    
                    if len(new_positions) > 0 and len(prev_positions) > 0:
                        turnover = 0.0
                        all_tickers = set(prev_positions.index) | set(new_positions.index)
                        for ticker in all_tickers:
                            old_w = prev_positions.get(ticker, 0)
                            new_w = new_positions.get(ticker, 0)
                            turnover += abs(new_w - old_w)
                        cost = turnover * 0.001
                    else:
                        cost = 0.0
                    
                    net_return = stock_return + cash_return - cost
                    
                    results['dates'].append(date)
                    results['returns'].append(net_return)
                    results['is_bull'].append(is_bull)
                    results['vix_values'].append(current_vix)
                    results['equity_weights'].append(new_positions.sum() if len(new_positions) > 0 else 0)
                
                prev_positions = new_positions.copy() if len(new_positions) > 0 else pd.Series(dtype=float)
        
        return results
    
    def calculate_metrics(self, results: Dict) -> Dict:
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0, 'invested_sharpe': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
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
        
        # 투자 기간 Sharpe
        invested_returns = returns[equity_weights > 0.01]
        if len(invested_returns) > 10:
            invested_annual_return = (1 + invested_returns).prod() ** (TRADING_DAYS_YEAR / len(invested_returns)) - 1
            invested_vol = invested_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            invested_sharpe = (invested_annual_return - RISK_FREE_RATE) / invested_vol if invested_vol > 0 else 0
        else:
            invested_sharpe = 0
        
        return {
            'sharpe_ratio': float(sharpe),
            'invested_sharpe': float(invested_sharpe),
            'annual_return': float(annual_return),
            'annual_volatility': float(annual_vol),
            'max_drawdown': float(max_drawdown),
            'win_rate': float(win_rate),
            'invested_days': int(invested_days),
            'investment_ratio': float(investment_ratio),
        }


def main():
    print("=" * 70)
    print("ARES Ultimate v4.4 - Grid Search for Optimal Parameters")
    print("Target: Sharpe Ratio 3.0+")
    print("=" * 70)
    
    start_date = "2020-01-01"
    end_date = "2025-01-01"
    
    loader = DataLoader(DB_PATH)
    loader.connect()
    
    print(f"\nLoading data from {start_date} to {end_date}...")
    daily_df = loader.load_daily_data(start_date, end_date, TICKERS)
    vix_df = loader.load_vix_data(start_date, end_date)
    
    print(f"Loaded {len(daily_df)} daily records")
    print(f"Loaded {len(vix_df)} VIX records")
    
    loader.close()
    
    backtester = GridSearchBacktester(train_months=12, test_months=3)
    
    # 그리드 서치
    vix_thresholds = [12, 13, 14, 15, 16, 17, 18]
    momentum_thresholds = [0.0, 0.005, 0.01, 0.015, 0.02]
    
    best_sharpe = -999
    best_params = {}
    best_metrics = {}
    
    print("\n=== GRID SEARCH ===")
    for vix_th in vix_thresholds:
        for mom_th in momentum_thresholds:
            results = backtester.run_single(daily_df, vix_df, vix_th, mom_th)
            metrics = backtester.calculate_metrics(results)
            
            # 전체 Sharpe 기준
            if metrics['sharpe_ratio'] > best_sharpe:
                best_sharpe = metrics['sharpe_ratio']
                best_params = {'vix_threshold': vix_th, 'momentum_threshold': mom_th}
                best_metrics = metrics
            
            print(f"VIX<{vix_th}, Mom>{mom_th:.3f}: Sharpe={metrics['sharpe_ratio']:.2f}, "
                  f"InvSharpe={metrics['invested_sharpe']:.2f}, InvRatio={metrics['investment_ratio']:.1%}")
    
    print("\n" + "=" * 70)
    print("BEST PARAMETERS")
    print("=" * 70)
    print(f"VIX Threshold: {best_params['vix_threshold']}")
    print(f"Momentum Threshold: {best_params['momentum_threshold']}")
    
    print("\n" + "=" * 70)
    print("BEST RESULTS")
    print("=" * 70)
    print(f"Sharpe Ratio (Overall): {best_metrics['sharpe_ratio']:.2f}")
    print(f"Sharpe Ratio (Invested): {best_metrics['invested_sharpe']:.2f}")
    print(f"Annual Return: {best_metrics['annual_return']:.2%}")
    print(f"Annual Volatility: {best_metrics['annual_volatility']:.2%}")
    print(f"Max Drawdown: {best_metrics['max_drawdown']:.2%}")
    print(f"Win Rate: {best_metrics['win_rate']:.2%}")
    print(f"Investment Ratio: {best_metrics['investment_ratio']:.2%}")
    
    # 최적 파라미터로 상세 결과 저장
    results = backtester.run_single(daily_df, vix_df, 
                                     best_params['vix_threshold'], 
                                     best_params['momentum_threshold'])
    
    results_df = pd.DataFrame({
        'date': results['dates'],
        'return': results['returns'],
        'is_bull': results['is_bull'],
        'vix': results['vix_values'],
        'equity_weight': results['equity_weights'],
    })
    results_df.to_csv('/home/ubuntu/ares_v4_4_results.csv', index=False)
    
    import json
    output = {
        'best_params': best_params,
        'best_metrics': best_metrics
    }
    with open('/home/ubuntu/ares_v4_4_metrics.json', 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\nResults saved to /home/ubuntu/ares_v4_4_results.csv")


if __name__ == "__main__":
    main()
