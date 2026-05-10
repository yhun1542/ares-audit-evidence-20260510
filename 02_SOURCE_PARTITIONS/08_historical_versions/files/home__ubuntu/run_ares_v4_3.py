#!/usr/bin/env python3
"""
ARES v4.3 - 레짐 전환 손실 방지 전략
- 선행 청산: VIX 상승 조짐 시 미리 청산
- 점진적 포지션 축소: 급격한 전환 방지
- 트레일링 스탑: 이익 보호
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


class EarlyWarningRegimeDetector:
    """선행 경고 레짐 감지기 - 위험 신호 조기 감지"""
    
    def __init__(self):
        self.vix_bull = 16.0
        self.vix_warning = 18.0  # 경고 임계값
        self.vix_exit = 20.0     # 청산 임계값
        self.vix_crisis = 28.0
    
    def detect(self, vix: float, vix_history: pd.Series, market_momentum: float) -> Tuple[str, float, bool]:
        """
        레짐 감지 + 선행 경고
        Returns: (regime, confidence, early_warning)
        """
        if len(vix_history) < 20:
            return 'NEUTRAL', 0.5, False
        
        # VIX 지표들
        vix_ma3 = vix_history.tail(3).mean()
        vix_ma5 = vix_history.tail(5).mean()
        vix_ma10 = vix_history.tail(10).mean()
        
        # VIX 변화율 (1일, 3일)
        vix_change_1d = (vix - vix_history.iloc[-1]) / (vix_history.iloc[-1] + 1e-10)
        vix_change_3d = (vix - vix_ma3) / (vix_ma3 + 1e-10)
        
        # VIX 가속도 (변화율의 변화)
        if len(vix_history) >= 5:
            prev_change = (vix_history.iloc[-1] - vix_history.iloc[-4]) / (vix_history.iloc[-4] + 1e-10)
            vix_acceleration = vix_change_3d - prev_change
        else:
            vix_acceleration = 0
        
        # 선행 경고 조건
        early_warning = False
        
        # 경고 1: VIX가 경고 임계값 이상이고 상승 중
        if vix >= self.vix_warning and vix_change_1d > 0.02:
            early_warning = True
        
        # 경고 2: VIX 급등 (1일 5% 이상)
        if vix_change_1d > 0.05:
            early_warning = True
        
        # 경고 3: VIX 가속도 양수 (상승 가속)
        if vix_acceleration > 0.03 and vix >= self.vix_warning:
            early_warning = True
        
        # 경고 4: 시장 모멘텀 급락
        if market_momentum < -0.02:
            early_warning = True
        
        # 레짐 결정
        if vix >= self.vix_crisis or vix_change_3d > 0.25:
            return 'CRISIS', 0.9, True
        
        if vix >= self.vix_exit or (vix >= self.vix_warning and vix_change_3d > 0.1):
            return 'BEAR', 0.7, True
        
        if vix < self.vix_bull and vix_change_3d < 0.05 and market_momentum > 0:
            confidence = 0.8 + (self.vix_bull - vix) / self.vix_bull * 0.2
            return 'BULL', min(1.0, confidence), early_warning
        
        if vix < self.vix_warning and market_momentum > 0.01:
            return 'BULL', 0.6, early_warning
        
        return 'NEUTRAL', 0.5, early_warning


class AdaptiveStrategy:
    """적응형 전략 - 선행 경고 시 점진적 청산"""
    
    def __init__(self):
        self.max_positions = 5
        self.max_position_size = 0.25
        self.trailing_stop_pct = 0.08  # 8% 트레일링 스탑
    
    def get_positions(self, prices_dict: Dict[str, pd.DataFrame], regime: str,
                      confidence: float, realized_vol: float, 
                      early_warning: bool, prev_positions: pd.Series,
                      peak_values: Dict[str, float],
                      target_vol: float = 0.15) -> Tuple[pd.Series, Dict[str, float]]:
        """
        포지션 결정 + 트레일링 스탑
        Returns: (positions, updated_peak_values)
        """
        
        # BULL이 아니면 Cash
        if regime != 'BULL':
            return pd.Series(dtype=float), {}
        
        # 선행 경고 시 포지션 50% 축소
        position_scalar = 0.5 if early_warning else 1.0
        
        scores = {}
        
        for ticker, df in prices_dict.items():
            if len(df) < 62:
                continue
            
            close = df['close']
            current_price = close.iloc[-1]
            
            # 트레일링 스탑 체크
            if ticker in peak_values:
                peak = peak_values[ticker]
                if current_price < peak * (1 - self.trailing_stop_pct):
                    # 트레일링 스탑 발동 - 이 종목 제외
                    continue
            
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
            return pd.Series(dtype=float), {}
        
        # 상위 N개 선택
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_tickers = sorted_scores[:self.max_positions]
        
        # 변동성 타겟팅
        vol_scalar = min(1.5, max(0.5, target_vol / (realized_vol + 0.01)))
        
        # 총 투자 비율
        total_allocation = min(1.0, 0.6 + confidence * 0.4) * vol_scalar * position_scalar
        
        positions = {}
        new_peaks = {}
        total_score = sum(max(0, s) for _, s in top_tickers)
        
        if total_score > 0:
            for ticker, score in top_tickers:
                weight = (score / total_score) * total_allocation
                positions[ticker] = min(self.max_position_size, weight)
                
                # 피크 값 업데이트
                if ticker in prices_dict:
                    current_price = prices_dict[ticker]['close'].iloc[-1]
                    if ticker in peak_values:
                        new_peaks[ticker] = max(peak_values[ticker], current_price)
                    else:
                        new_peaks[ticker] = current_price
        
        return pd.Series(positions), new_peaks


class WalkForwardBacktester:
    def __init__(self, train_months: int = 12, test_months: int = 3):
        self.train_months = train_months
        self.test_months = test_months
        self.regime_detector = EarlyWarningRegimeDetector()
        self.strategy = AdaptiveStrategy()
    
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
            'early_warnings': [],
        }
        
        dates = prices.index.unique().sort_values()
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        total_days = len(dates)
        
        print(f"Total trading days: {total_days}")
        
        portfolio_value = 1.0
        prev_positions = pd.Series(dtype=float)
        peak_values = {}
        
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
                
                regime, confidence, early_warning = self.regime_detector.detect(
                    current_vix, hist_vix, market_momentum
                )
                
                prices_dict = {}
                for ticker in prices.columns:
                    ticker_prices = hist_prices[ticker].dropna()
                    if len(ticker_prices) >= 62:
                        prices_dict[ticker] = pd.DataFrame({'close': ticker_prices})
                
                new_positions, peak_values = self.strategy.get_positions(
                    prices_dict, regime, confidence, realized_vol,
                    early_warning, prev_positions, peak_values
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
                    results['early_warnings'].append(early_warning)
                
                prev_positions = new_positions.copy() if len(new_positions) > 0 else pd.Series(dtype=float)
        
        return results
    
    def calculate_metrics(self, results: Dict) -> Dict:
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
        regimes = pd.Series(results['regimes'], index=results['dates'])
        equity_weights = pd.Series(results['equity_weights'], index=results['dates'])
        early_warnings = pd.Series(results['early_warnings'], index=results['dates'])
        
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
        
        # 경고 발생 횟수
        warning_count = early_warnings.sum()
        
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
            'warning_count': int(warning_count),
            'regime_metrics': regime_metrics
        }


def main():
    print("=" * 70)
    print("ARES Ultimate v4.3 - Early Warning + Adaptive Strategy")
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
    print(f"Early Warning Count: {metrics['warning_count']}")
    
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
        'portfolio_value': results['portfolio_values'],
        'early_warning': results['early_warnings']
    })
    results_df.to_csv('/home/ubuntu/ares_v4_3_results.csv', index=False)
    
    import json
    with open('/home/ubuntu/ares_v4_3_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)
    
    print(f"\nResults saved to /home/ubuntu/ares_v4_3_results.csv")


if __name__ == "__main__":
    main()
