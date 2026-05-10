#!/usr/bin/env python3
"""
ARES v4.0 백테스트 실행 스크립트
EC2 환경에 맞게 설정 조정
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

# EC2 설정
DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
TRADING_DAYS_YEAR = 252
RISK_FREE_RATE = 0.05

# 종목 리스트 (EC2 DB에 있는 종목)
TICKERS = ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AMD', 
           'AVGO', 'ADBE', 'ASML', 'CRWD', 'DDOG', 'CDNS', 'SNPS', 'ISRG', 
           'VRTX', 'REGN', 'BKNG', 'ACN', 'CRM', 'ORCL', 'NFLX', 'COST',
           'PEP', 'INTC', 'QCOM', 'TXN', 'HON', 'UNP']


class DataLoader:
    """EC2 DB에 맞춘 데이터 로더"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
    
    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        print(f"Connected to DB: {self.db_path}")
    
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
    """기술적 지표 - Look-ahead bias 없음"""
    
    @staticmethod
    def momentum(prices: pd.Series, period: int) -> float:
        """모멘텀 계산 (t-1 데이터 사용)"""
        if len(prices) < period + 2:
            return 0.0
        return prices.iloc[-2] / prices.iloc[-period-2] - 1
    
    @staticmethod
    def multi_momentum(prices: pd.Series) -> float:
        """다중 기간 모멘텀"""
        mom5 = TechnicalIndicators.momentum(prices, 5)
        mom10 = TechnicalIndicators.momentum(prices, 10)
        mom20 = TechnicalIndicators.momentum(prices, 20)
        return 0.5 * mom5 + 0.3 * mom10 + 0.2 * mom20
    
    @staticmethod
    def volatility(prices: pd.Series, period: int = 20) -> float:
        """변동성 계산"""
        if len(prices) < period + 2:
            return 0.3
        returns = prices.iloc[:-1].pct_change().tail(period)
        return returns.std() * np.sqrt(TRADING_DAYS_YEAR)
    
    @staticmethod
    def rsi(prices: pd.Series, period: int = 14) -> float:
        """RSI 계산"""
        if len(prices) < period + 2:
            return 50.0
        prices_shifted = prices.iloc[:-1]
        delta = prices_shifted.diff().tail(period)
        gain = (delta.where(delta > 0, 0)).mean()
        loss = (-delta.where(delta < 0, 0)).mean()
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def quality_score(prices: pd.Series, volume: pd.Series) -> float:
        """품질 점수 (변동성 조정 수익률)"""
        if len(prices) < 62:
            return 0.0
        returns = prices.iloc[:-1].pct_change().tail(60)
        vol = returns.std()
        if vol == 0:
            return 0.0
        sharpe = returns.mean() / vol
        return sharpe


class RegimeDetector:
    """빠른 레짐 감지기"""
    
    def __init__(self):
        self.vix_bull = 18.0
        self.vix_neutral = 25.0
        self.vix_bear = 35.0
    
    def detect(self, vix: float, vix_history: pd.Series, market_momentum: float) -> Tuple[str, float]:
        """레짐 감지 (t-1 데이터만 사용)"""
        if len(vix_history) < 10:
            return 'NEUTRAL', 0.5
        
        # VIX 변화율 (선행 지표)
        vix_ma5 = vix_history.tail(5).mean()
        vix_change = (vix - vix_ma5) / (vix_ma5 + 1e-10)
        
        # 점수 계산
        score = 0.0
        
        # VIX 레벨 (40%)
        if vix < self.vix_bull:
            score += 0.4
        elif vix < self.vix_neutral:
            score += 0.1
        elif vix < self.vix_bear:
            score -= 0.2
        else:
            score -= 0.4
        
        # VIX 변화율 (30%)
        if vix_change < -0.1:
            score += 0.3  # VIX 하락 → 긍정적
        elif vix_change > 0.2:
            score -= 0.3  # VIX 급등 → 부정적
        
        # 시장 모멘텀 (30%)
        score += np.clip(market_momentum * 3, -0.3, 0.3)
        
        # 레짐 결정
        if score > 0.3:
            regime = 'BULL'
        elif score > 0:
            regime = 'NEUTRAL'
        elif score > -0.3:
            regime = 'BEAR'
        else:
            regime = 'CRISIS'
        
        confidence = abs(score)
        return regime, confidence


class MultiFactorStrategy:
    """다중 팩터 전략"""
    
    def __init__(self):
        self.regime_configs = {
            'BULL': {'position_scalar': 1.0, 'max_positions': 5, 'momentum_weight': 0.5, 'quality_weight': 0.3, 'low_vol_weight': 0.2},
            'NEUTRAL': {'position_scalar': 0.6, 'max_positions': 4, 'momentum_weight': 0.3, 'quality_weight': 0.4, 'low_vol_weight': 0.3},
            'BEAR': {'position_scalar': 0.3, 'max_positions': 3, 'momentum_weight': 0.2, 'quality_weight': 0.4, 'low_vol_weight': 0.4},
            'CRISIS': {'position_scalar': 0.1, 'max_positions': 2, 'momentum_weight': 0.1, 'quality_weight': 0.5, 'low_vol_weight': 0.4}
        }
    
    def calculate_scores(self, prices_dict: Dict[str, pd.DataFrame], regime: str) -> Dict[str, float]:
        """종목별 점수 계산"""
        config = self.regime_configs[regime]
        scores = {}
        
        for ticker, df in prices_dict.items():
            if len(df) < 62:
                continue
            
            close = df['close']
            volume = df['volume'] if 'volume' in df.columns else pd.Series([1]*len(df))
            
            # 모멘텀 점수
            momentum = TechnicalIndicators.multi_momentum(close)
            
            # 품질 점수
            quality = TechnicalIndicators.quality_score(close, volume)
            
            # 저변동성 점수 (변동성의 역수)
            vol = TechnicalIndicators.volatility(close)
            low_vol = 1.0 / (vol + 0.1)
            
            # 가중 평균
            score = (config['momentum_weight'] * momentum +
                     config['quality_weight'] * quality +
                     config['low_vol_weight'] * low_vol)
            
            scores[ticker] = score
        
        return scores
    
    def get_positions(self, prices_dict: Dict[str, pd.DataFrame], regime: str, 
                      confidence: float, realized_vol: float, target_vol: float = 0.15) -> pd.Series:
        """포지션 결정"""
        config = self.regime_configs[regime]
        
        # 점수 계산
        scores = self.calculate_scores(prices_dict, regime)
        if not scores:
            return pd.Series(dtype=float)
        
        # 상위 N개 선택
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_tickers = sorted_scores[:config['max_positions']]
        
        # 변동성 타겟팅
        vol_scalar = min(2.0, max(0.5, target_vol / (realized_vol + 0.01)))
        
        # 포지션 계산
        positions = {}
        total_score = sum(max(0, s) for _, s in top_tickers)
        
        if total_score > 0:
            for ticker, score in top_tickers:
                if score > 0:
                    weight = (score / total_score) * config['position_scalar'] * vol_scalar * confidence
                    positions[ticker] = min(0.25, weight)  # 최대 25%
        
        return pd.Series(positions)


class WalkForwardBacktester:
    """Walk-Forward 백테스터"""
    
    def __init__(self, train_months: int = 12, test_months: int = 3):
        self.train_months = train_months
        self.test_months = test_months
        self.regime_detector = RegimeDetector()
        self.strategy = MultiFactorStrategy()
    
    def run(self, daily_df: pd.DataFrame, vix_df: pd.DataFrame) -> Dict:
        """백테스트 실행"""
        # 데이터 준비
        prices = daily_df.pivot(index='date', columns='ticker', values='close')
        prices = prices.ffill()
        
        vix = vix_df.set_index('date')['vix']
        vix = vix.reindex(prices.index).ffill().fillna(20.0)
        
        # 결과 저장
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
        print(f"Train: {train_days} days, Test: {test_days} days")
        
        portfolio_value = 1.0
        prev_positions = pd.Series(dtype=float)
        
        fold_count = 0
        for start_idx in range(train_days, total_days - test_days, test_days):
            fold_count += 1
            test_start = dates[start_idx]
            test_end = dates[min(start_idx + test_days - 1, total_days - 1)]
            
            print(f"\nFold {fold_count}: Test {test_start.date()} ~ {test_end.date()}")
            
            test_dates = dates[(dates >= test_start) & (dates <= test_end)]
            
            for i, date in enumerate(test_dates):
                current_vix = vix.loc[date] if date in vix.index else 20.0
                
                # 과거 데이터만 사용
                hist_prices = prices.loc[:date].iloc[:-1]  # t-1까지
                hist_vix = vix.loc[:date].iloc[:-1]
                
                # 시장 모멘텀
                if len(hist_prices) >= 10:
                    market_avg = hist_prices.mean(axis=1)
                    market_momentum = market_avg.iloc[-1] / market_avg.iloc[-6] - 1 if len(market_avg) >= 6 else 0
                else:
                    market_momentum = 0
                
                # 레짐 감지
                regime, confidence = self.regime_detector.detect(current_vix, hist_vix, market_momentum)
                
                # 실현 변동성
                if len(hist_prices) >= 22:
                    market_returns = hist_prices.mean(axis=1).pct_change().tail(20)
                    realized_vol = market_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                else:
                    realized_vol = 0.15
                
                # 종목별 데이터 준비
                prices_dict = {}
                for ticker in prices.columns:
                    ticker_prices = hist_prices[ticker].dropna()
                    if len(ticker_prices) >= 62:
                        prices_dict[ticker] = pd.DataFrame({'close': ticker_prices})
                
                # 포지션 결정
                new_positions = self.strategy.get_positions(prices_dict, regime, confidence, realized_vol)
                
                # 수익률 계산
                if i > 0 and len(prev_positions) > 0:
                    prev_date = test_dates[i-1]
                    prev_prices = prices.loc[prev_date]
                    current_prices = prices.loc[date]
                    
                    # 주식 수익률
                    stock_return = 0.0
                    for ticker, weight in prev_positions.items():
                        if ticker in prev_prices.index and ticker in current_prices.index:
                            ret = current_prices[ticker] / prev_prices[ticker] - 1
                            stock_return += weight * ret
                    
                    # Cash 수익률
                    cash_weight = max(0, 1.0 - prev_positions.sum())
                    cash_return = cash_weight * (RISK_FREE_RATE / TRADING_DAYS_YEAR)
                    
                    # 거래 비용
                    if len(new_positions) > 0:
                        turnover = 0.0
                        all_tickers = set(prev_positions.index) | set(new_positions.index)
                        for ticker in all_tickers:
                            old_w = prev_positions.get(ticker, 0)
                            new_w = new_positions.get(ticker, 0)
                            turnover += abs(new_w - old_w)
                        cost = turnover * 0.001  # 0.1% 거래 비용
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
        """성과 지표 계산"""
        if len(results['returns']) == 0:
            return {'sharpe_ratio': 0}
        
        returns = pd.Series(results['returns'], index=results['dates'])
        regimes = pd.Series(results['regimes'], index=results['dates'])
        equity_weights = pd.Series(results['equity_weights'], index=results['dates'])
        
        # 전체 지표
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
        
        # 투자 비율
        invested_days = (equity_weights > 0.01).sum()
        investment_ratio = invested_days / len(equity_weights)
        
        # 레짐별 성과
        regime_metrics = {}
        for regime in ['BULL', 'NEUTRAL', 'BEAR', 'CRISIS']:
            regime_returns = returns[regimes == regime]
            if len(regime_returns) > 10:
                regime_annual_return = (1 + regime_returns).prod() ** (TRADING_DAYS_YEAR / len(regime_returns)) - 1
                regime_vol = regime_returns.std() * np.sqrt(TRADING_DAYS_YEAR)
                regime_sharpe = (regime_annual_return - RISK_FREE_RATE) / regime_vol if regime_vol > 0 else 0
                regime_metrics[regime] = {
                    'days': len(regime_returns),
                    'sharpe': regime_sharpe,
                    'annual_return': regime_annual_return,
                    'volatility': regime_vol
                }
        
        return {
            'sharpe_ratio': sharpe,
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'total_days': len(returns),
            'invested_days': invested_days,
            'investment_ratio': investment_ratio,
            'regime_metrics': regime_metrics
        }


def main():
    print("=" * 70)
    print("ARES Ultimate v4.0 - Multi-Regime Adaptive Trading System")
    print("Target: Sharpe Ratio 3.0+")
    print("=" * 70)
    
    start_date = "2020-01-01"
    end_date = "2025-01-01"
    
    # 데이터 로드
    loader = DataLoader(DB_PATH)
    loader.connect()
    
    print(f"\nLoading data from {start_date} to {end_date}...")
    daily_df = loader.load_daily_data(start_date, end_date, TICKERS)
    vix_df = loader.load_vix_data(start_date, end_date)
    
    print(f"Loaded {len(daily_df)} daily records")
    print(f"Loaded {len(vix_df)} VIX records")
    print(f"Tickers: {daily_df['ticker'].nunique()}")
    
    loader.close()
    
    # 백테스트 실행
    backtester = WalkForwardBacktester(train_months=12, test_months=3)
    results = backtester.run(daily_df, vix_df)
    metrics = backtester.calculate_metrics(results)
    
    # 결과 출력
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)
    print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
    print(f"Annual Return: {metrics['annual_return']:.2%}")
    print(f"Annual Volatility: {metrics['annual_volatility']:.2%}")
    print(f"Max Drawdown: {metrics['max_drawdown']:.2%}")
    print(f"Win Rate: {metrics['win_rate']:.2%}")
    print(f"Investment Ratio: {metrics['investment_ratio']:.2%}")
    print(f"Total Days: {metrics['total_days']}")
    print(f"Invested Days: {metrics['invested_days']}")
    
    print("\n=== REGIME METRICS ===")
    for regime, rm in metrics.get('regime_metrics', {}).items():
        print(f"\n{regime}:")
        print(f"  Days: {rm['days']}")
        print(f"  Sharpe: {rm['sharpe']:.2f}")
        print(f"  Annual Return: {rm['annual_return']:.2%}")
        print(f"  Volatility: {rm['volatility']:.2%}")
    
    # 결과 저장
    results_df = pd.DataFrame({
        'date': results['dates'],
        'return': results['returns'],
        'regime': results['regimes'],
        'vix': results['vix_values'],
        'equity_weight': results['equity_weights'],
        'portfolio_value': results['portfolio_values']
    })
    results_df.to_csv('/home/ubuntu/ares_v4_results.csv', index=False)
    print(f"\nResults saved to /home/ubuntu/ares_v4_results.csv")
    
    import json
    with open('/home/ubuntu/ares_v4_metrics.json', 'w') as f:
        # regime_metrics를 직렬화 가능하게 변환
        metrics_serializable = {k: v for k, v in metrics.items() if k != 'regime_metrics'}
        metrics_serializable['regime_metrics'] = {
            regime: {k: float(v) if isinstance(v, (np.floating, float)) else v 
                     for k, v in rm.items()}
            for regime, rm in metrics.get('regime_metrics', {}).items()
        }
        json.dump(metrics_serializable, f, indent=2)
    print(f"Metrics saved to /home/ubuntu/ares_v4_metrics.json")


if __name__ == "__main__":
    main()
