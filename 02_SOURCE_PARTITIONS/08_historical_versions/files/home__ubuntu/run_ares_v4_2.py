#!/usr/bin/env python3
"""
ARES v4.2 - BULL 집중 전략 + 최적화된 레짐 감지
- BULL: 공격적 모멘텀 + 품질 전략 (최대 100% 투자)
- NEUTRAL/BEAR/CRISIS: Cash 100%
- 핵심: BULL 레짐 감지 정확도 향상
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


class OptimizedRegimeDetector:
    """최적화된 레짐 감지기 - BULL 감지 정확도 향상"""
    
    def __init__(self):
        # 최적화된 임계값
        self.vix_bull_strict = 16.0  # 엄격한 BULL 조건
        self.vix_bull_loose = 20.0   # 느슨한 BULL 조건 (모멘텀 강할 때)
        self.vix_crisis = 30.0
    
    def detect(self, vix: float, vix_history: pd.Series, market_momentum: float,
               market_vol: float) -> Tuple[str, float]:
        """
        최적화된 레짐 감지
        - VIX 레벨 + 변화율 + 시장 모멘텀 + 시장 변동성 종합 고려
        """
        if len(vix_history) < 20:
            return 'NEUTRAL', 0.5
        
        # VIX 지표들
        vix_ma5 = vix_history.tail(5).mean()
        vix_ma20 = vix_history.tail(20).mean()
        vix_change_5d = (vix - vix_ma5) / (vix_ma5 + 1e-10)
        vix_trend = vix_ma5 / vix_ma20 - 1
        
        # VIX 백분위 (역사적 위치)
        vix_percentile = (vix_history < vix).mean()
        
        # BULL 조건 체크
        is_bull = False
        confidence = 0.0
        
        # 조건 1: VIX가 매우 낮고 안정적
        if vix < self.vix_bull_strict and vix_change_5d < 0.1:
            is_bull = True
            confidence = 0.8 + (self.vix_bull_strict - vix) / self.vix_bull_strict * 0.2
        
        # 조건 2: VIX가 적당히 낮고 모멘텀이 강함
        elif vix < self.vix_bull_loose and market_momentum > 0.02 and vix_change_5d < 0.05:
            is_bull = True
            confidence = 0.6 + market_momentum * 5
        
        # 조건 3: VIX가 하락 추세이고 모멘텀이 양수
        elif vix < 22 and vix_trend < -0.1 and market_momentum > 0.01:
            is_bull = True
            confidence = 0.5
        
        # CRISIS 조건
        if vix >= self.vix_crisis or vix_change_5d > 0.3:
            return 'CRISIS', abs(vix_change_5d)
        
        # BEAR 조건
        if vix >= 25 or (vix >= 20 and vix_change_5d > 0.1):
            return 'BEAR', 0.5 + vix_change_5d
        
        if is_bull:
            return 'BULL', min(1.0, confidence)
        else:
            return 'NEUTRAL', 0.5


class BullFocusedStrategy:
    """BULL 집중 전략 - BULL에서만 투자"""
    
    def __init__(self):
        self.max_positions = 5
        self.max_position_size = 0.25
    
    def get_positions(self, prices_dict: Dict[str, pd.DataFrame], regime: str,
                      confidence: float, realized_vol: float, 
                      target_vol: float = 0.15) -> pd.Series:
        """포지션 결정 - BULL에서만 투자"""
        
        # BULL이 아니면 Cash
        if regime != 'BULL':
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
            if momentum > 0:
                score = 0.6 * momentum + 0.25 * quality + 0.15 * low_vol
                scores[ticker] = score
        
        if not scores:
            return pd.Series(dtype=float)
        
        # 상위 N개 선택
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_tickers = sorted_scores[:self.max_positions]
        
        # 변동성 타겟팅
        vol_scalar = min(1.5, max(0.5, target_vol / (realized_vol + 0.01)))
        
        # 신뢰도 기반 총 투자 비율
        total_allocation = min(1.0, 0.6 + confidence * 0.4) * vol_scalar
        
        positions = {}
        total_score = sum(max(0, s) for _, s in top_tickers)
        
        if total_score > 0:
            for ticker, score in top_tickers:
                weight = (score / total_score) * total_allocation
                positions[ticker] = min(self.max_position_size, weight)
        
        return pd.Series(positions)


class WalkForwardBacktester:
    def __init__(self, train_months: int = 12, test_months: int = 3):
        self.train_months = train_months
        self.test_months = test_months
        self.regime_detector = OptimizedRegimeDetector()
        self.strategy = BullFocusedStrategy()
    
    def run(self, daily_df: pd.DataFrame, vix_df: pd.DataFrame) -> Dict:
        prices = daily_df.pivot(index='date', columns='ticker', values='close')
        prices = prices.ffill()
        
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(prices.index).ffill().fillna(20.0)
        
        results = {
            'dates': [],
            'returns': [],
            'regimes': [],
            'vix_values': [],
            'equity_weights': [],
            'portfolio_values': [],
        }
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        print(f"Total trading days: {total_days}")
        
        portfolio_value = 1.0
        prev_positions = pd.Series(dtype=float)
        
        fold_count = 0
        for start_idx in range(train_days, total_days - test_days, test_days):
            fold_count += 1
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            
            print(f"Fold {fold_count}: Test {test_start.date()} ~ {test_end.date()}")
            
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
                
                regime, confidence = self.regime_detector.detect(
                    current_vix, hist_vix, market_momentum, realized_vol
                )
                
                prices_dict = {}
                for ticker in prices.columns:
                    ticker_prices = hist_prices[ticker].dropna()
                    if len(ticker_prices) >= 62:
                        prices_dict[ticker] = pd.DataFrame({'close': ticker_prices})
                
                new_positions = self.strategy.get_positions(
                    prices_dict, regime, confidence, realized_vol
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
                    portfolio_value *= (1 + net_return)
                    
                    results['dates'].append(date)
                    results['returns'].append(net_return)
                    results['regimes'].append(regime)
                    results['vix_values'].append(current_vix)
                    results['equity_weights'].append(new_positions.sum() if len(new_positions) > 0 else 0)
                    results['portfolio_values'].append(portfolio_value)
                
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
        
        # 투자 기간 Sharpe
        invested_returns = returns[equity_weights > 0.01]
        if len(invested_returns) > 10:
            invested_annual_return = (1 + invested_returns).prod() ** (TRADING_DAYS_YEAR / len(invested_returns)) - 1
            invested_vol = invested_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
            invested_sharpe = (invested_annual_return - RISK_FREE_RATE) / invested_vol if invested_vol > 0 else 0
        else:
            invested_sharpe = 0
        
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
    print("ARES Ultimate v4.2 - BULL Focused Strategy")
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
    
    backtester = WalkForwardBacktester(train_months=12, test_months=3)
    results = backtester.run(daily_df, vix_df)
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
    
    results_df = pd.DataFrame({
        'date': results['dates'],
        'return': results['returns'],
        'regime': results['regimes'],
        'vix': results['vix_values'],
        'equity_weight': results['equity_weights'],
        'portfolio_value': results['portfolio_values']
    })
    results_df.to_csv('/home/ubuntu/ares_v4_2_results.csv', index=False)
    
    import json
    with open('/home/ubuntu/ares_v4_2_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)
    
    print(f"\nResults saved to /home/ubuntu/ares_v4_2_results.csv")


if __name__ == "__main__":
    main()
