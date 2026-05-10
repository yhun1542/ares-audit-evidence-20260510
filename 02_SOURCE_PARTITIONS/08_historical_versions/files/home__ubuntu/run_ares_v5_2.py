#!/usr/bin/env python3
"""
ARES Ultimate v5.2 - CRISIS 방어 완전 수정
문제: v5.1에서 CRISIS 레짐에서도 손실 발생
해결: 레짐 감지 후 포지션 계산 로직 수정
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class MarketRegime(Enum):
    CRISIS = "crisis"
    NORMAL = "normal"
    INFLATION = "inflation"
    BULL = "bull"

class TrafficLight(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

@dataclass
class RegimeState:
    regime: MarketRegime
    traffic_light: TrafficLight
    vix_level: float
    position_multiplier: float  # 직접 포지션 배수 지정

# ═══════════════════════════════════════════════════════════════════════════════
# DATA PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

class PITDataPipeline:
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def load_prices(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
            SELECT symbol, date, close
            FROM daily_ohlcv
            WHERE symbol IN ({placeholders})
            AND date >= ? AND date <= ?
            ORDER BY date, symbol
        """
        df = pd.read_sql_query(query, conn, params=symbols + [start_date, end_date])
        conn.close()
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        prices = df.pivot(index='date', columns='symbol', values='close')
        prices = prices.ffill()
        return prices
    
    def load_vix(self, start_date: str, end_date: str) -> pd.Series:
        conn = sqlite3.connect(self.db_path)
        query = """
            SELECT date, close as vix
            FROM vix
            WHERE date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, conn, params=[start_date, end_date])
        conn.close()
        
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df['vix'].ffill()

# ═══════════════════════════════════════════════════════════════════════════════
# SIMPLIFIED REGIME DETECTOR (VIX 기반)
# ═══════════════════════════════════════════════════════════════════════════════

class SimpleRegimeDetector:
    """
    단순하고 명확한 레짐 감지:
    - VIX >= 25: CRISIS → position_multiplier = 0 (완전 Cash)
    - VIX >= 20: YELLOW → position_multiplier = 0.3
    - VIX >= 15: NORMAL → position_multiplier = 0.7
    - VIX < 15: BULL → position_multiplier = 1.0
    """
    
    def detect(self, vix: float, momentum: float = 0) -> RegimeState:
        # CRISIS: VIX >= 25 → 완전 Cash
        if vix >= 25:
            return RegimeState(
                regime=MarketRegime.CRISIS,
                traffic_light=TrafficLight.RED,
                vix_level=vix,
                position_multiplier=0.0  # 완전 Cash
            )
        
        # HIGH VIX: 20-25 → 30% 포지션
        if vix >= 20:
            return RegimeState(
                regime=MarketRegime.NORMAL,
                traffic_light=TrafficLight.YELLOW,
                vix_level=vix,
                position_multiplier=0.3
            )
        
        # NORMAL: 15-20 → 70% 포지션
        if vix >= 15:
            return RegimeState(
                regime=MarketRegime.NORMAL,
                traffic_light=TrafficLight.GREEN,
                vix_level=vix,
                position_multiplier=0.7
            )
        
        # BULL: VIX < 15 → 100% 포지션
        if momentum > 0:
            return RegimeState(
                regime=MarketRegime.BULL,
                traffic_light=TrafficLight.GREEN,
                vix_level=vix,
                position_multiplier=1.0
            )
        else:
            return RegimeState(
                regime=MarketRegime.INFLATION,
                traffic_light=TrafficLight.GREEN,
                vix_level=vix,
                position_multiplier=0.9
            )

# ═══════════════════════════════════════════════════════════════════════════════
# ALPHA ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class AlphaEngine:
    def compute_alpha(self, prices: pd.DataFrame) -> pd.Series:
        if prices.empty or len(prices) < 252:
            return pd.Series(1.0 / len(prices.columns), index=prices.columns)
        
        prices = prices.ffill()
        
        # Momentum (12-1)
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        momentum = ret_12m - ret_1m
        
        # Low volatility
        daily_ret = prices.pct_change()
        vol = daily_ret.iloc[-60:].std()
        low_vol = -vol
        
        # Normalize
        momentum = (momentum - momentum.mean()) / (momentum.std() + 1e-8)
        low_vol = (low_vol - low_vol.mean()) / (low_vol.std() + 1e-8)
        
        # Combine
        alpha = 0.6 * momentum + 0.4 * low_vol
        alpha = alpha.fillna(0)
        
        return alpha

# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioOptimizer:
    def optimize(self, returns: pd.DataFrame, alpha: pd.Series, top_n: int = 10) -> pd.Series:
        if returns.empty or len(returns) < 20:
            n = len(alpha)
            if n > 0:
                return pd.Series(1.0 / n, index=alpha.index)
            return pd.Series()
        
        # Top N by alpha
        top_symbols = alpha.nlargest(top_n).index
        
        # Equal weight among top N
        weights = pd.Series(0.0, index=alpha.index)
        weights[top_symbols] = 1.0 / top_n
        
        return weights

# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestEngine:
    def __init__(self, db_path: str, transaction_cost_bps: float = 5.0):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.regime_detector = SimpleRegimeDetector()
        self.alpha_engine = AlphaEngine()
        self.optimizer = PortfolioOptimizer()
    
    def run(self, symbols: List[str], start_date: str, end_date: str, rebalance_freq: str = 'W') -> Dict[str, Any]:
        print(f"Loading data for {len(symbols)} symbols...")
        
        prices = self.pipeline.load_prices(symbols, start_date, end_date)
        vix = self.pipeline.load_vix(start_date, end_date)
        
        if prices.empty:
            print("No price data available")
            return {}
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        vix = vix.reindex(prices.index).ffill().fillna(20)
        
        # Initialize
        portfolio_value = [1.0]
        positions = pd.Series(0.0, index=prices.columns)
        daily_returns = []
        
        # Rebalance dates
        if rebalance_freq == 'W':
            rebalance_dates = prices.resample('W-FRI').last().index
        else:
            rebalance_dates = prices.index[::5]
        
        print(f"Running backtest with {len(rebalance_dates)} rebalance dates...")
        
        # Run backtest
        for i, date in enumerate(prices.index[1:], 1):
            current_vix = vix.loc[date] if date in vix.index else 20
            
            # Momentum for regime
            if i >= 21:
                momentum = (prices.iloc[i] / prices.iloc[i-21] - 1).mean()
            else:
                momentum = 0
            
            # Detect regime
            regime_state = self.regime_detector.detect(current_vix, momentum)
            
            # Rebalance
            if date in rebalance_dates:
                # Compute alpha
                alpha = self.alpha_engine.compute_alpha(prices.iloc[:i])
                
                # Optimize portfolio
                lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                new_weights = self.optimizer.optimize(lookback_returns, alpha, top_n=10)
                
                # Apply position multiplier (핵심: 레짐에 따른 포지션 조절)
                new_positions = new_weights * regime_state.position_multiplier
                
                # Transaction costs
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.transaction_cost
                
                positions = new_positions
            else:
                # 리밸런싱이 아닌 날에도 레짐에 따라 포지션 조절
                # CRISIS면 즉시 청산
                if regime_state.regime == MarketRegime.CRISIS:
                    if positions.sum() > 0.01:
                        turnover = positions.abs().sum()
                        cost = turnover * self.transaction_cost
                        positions = pd.Series(0.0, index=prices.columns)
                    else:
                        cost = 0
                else:
                    cost = 0
            
            # Compute daily return
            daily_price_return = prices.iloc[i] / prices.iloc[i-1] - 1
            daily_price_return = daily_price_return.fillna(0)
            
            portfolio_return = (positions * daily_price_return).sum() - cost
            
            daily_returns.append({
                'date': date,
                'return': portfolio_return,
                'regime': regime_state.regime.value,
                'vix': current_vix,
                'position_size': positions.sum(),
                'position_multiplier': regime_state.position_multiplier
            })
            
            portfolio_value.append(portfolio_value[-1] * (1 + portfolio_return))
        
        # Compute metrics
        returns_df = pd.DataFrame(daily_returns)
        returns_df.set_index('date', inplace=True)
        
        metrics = self._compute_metrics(returns_df)
        
        return {
            'metrics': metrics,
            'returns': returns_df,
            'portfolio_value': portfolio_value
        }
    
    def _compute_metrics(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        returns = returns_df['return']
        
        total_return = (1 + returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(returns)) - 1 if len(returns) > 0 else 0
        annual_vol = returns.std() * np.sqrt(252)
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        win_rate = (returns > 0).mean()
        invest_ratio = (returns_df['position_size'] > 0.01).mean()
        
        # Regime breakdown
        regime_metrics = {}
        for regime in ['bull', 'normal', 'crisis', 'inflation']:
            regime_returns = returns_df[returns_df['regime'] == regime]['return']
            if len(regime_returns) > 0:
                r_std = regime_returns.std()
                regime_metrics[regime] = {
                    'days': len(regime_returns),
                    'annual_return': regime_returns.mean() * 252,
                    'sharpe': regime_returns.mean() / r_std * np.sqrt(252) if r_std > 0 else 0,
                    'avg_position': returns_df[returns_df['regime'] == regime]['position_size'].mean()
                }
        
        # Investment period metrics
        invested_returns = returns_df[returns_df['position_size'] > 0.01]['return']
        if len(invested_returns) > 0:
            invest_sharpe = invested_returns.mean() / invested_returns.std() * np.sqrt(252) if invested_returns.std() > 0 else 0
        else:
            invest_sharpe = 0
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_vol': annual_vol,
            'sharpe': sharpe,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'invest_ratio': invest_ratio,
            'invest_sharpe': invest_sharpe,
            'regime_metrics': regime_metrics
        }

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("="*60)
    print("ARES Ultimate v5.2 - CRISIS Defense Fixed")
    print("="*60)
    
    DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    START_DATE = "2020-01-01"
    END_DATE = "2024-12-31"
    
    conn = sqlite3.connect(DB_PATH)
    symbols_df = pd.read_sql_query("""
        SELECT DISTINCT symbol, COUNT(*) as cnt
        FROM daily_ohlcv
        WHERE date >= '2020-01-01'
        GROUP BY symbol
        HAVING cnt > 500
        ORDER BY cnt DESC
        LIMIT 50
    """, conn)
    conn.close()
    
    symbols = symbols_df['symbol'].tolist()
    print(f"Selected {len(symbols)} symbols")
    
    engine = BacktestEngine(DB_PATH)
    result = engine.run(symbols, START_DATE, END_DATE, rebalance_freq='W')
    
    if not result:
        print("Backtest failed")
        return
    
    metrics = result['metrics']
    
    print("\n" + "="*60)
    print("BACKTEST RESULTS")
    print("="*60)
    print(f"Total Return:     {metrics['total_return']*100:.2f}%")
    print(f"Annual Return:    {metrics['annual_return']*100:.2f}%")
    print(f"Annual Volatility:{metrics['annual_vol']*100:.2f}%")
    print(f"Sharpe Ratio:     {metrics['sharpe']:.2f}")
    print(f"Max Drawdown:     {metrics['max_drawdown']*100:.2f}%")
    print(f"Win Rate:         {metrics['win_rate']*100:.2f}%")
    print(f"Investment Ratio: {metrics['invest_ratio']*100:.2f}%")
    print(f"Invest Period Sharpe: {metrics['invest_sharpe']:.2f}")
    
    print("\n" + "-"*60)
    print("REGIME BREAKDOWN")
    print("-"*60)
    for regime, rm in metrics['regime_metrics'].items():
        print(f"{regime.upper():12s}: {rm['days']:4d} days, "
              f"Ann.Ret: {rm['annual_return']*100:6.2f}%, "
              f"Sharpe: {rm['sharpe']:.2f}, "
              f"Avg.Pos: {rm['avg_position']*100:.1f}%")
    
    # VIX 구간별 분석
    returns_df = result['returns']
    print("\n" + "-"*60)
    print("VIX BREAKDOWN")
    print("-"*60)
    
    vix_bins = [(0, 15, 'VIX<15'), (15, 20, '15<=VIX<20'), (20, 25, '20<=VIX<25'), (25, 100, 'VIX>=25')]
    for low, high, name in vix_bins:
        mask = (returns_df['vix'] >= low) & (returns_df['vix'] < high)
        vix_returns = returns_df[mask]['return']
        if len(vix_returns) > 0:
            v_std = vix_returns.std()
            sharpe = vix_returns.mean() / v_std * np.sqrt(252) if v_std > 0 else 0
            avg_pos = returns_df[mask]['position_size'].mean()
            print(f"{name:12s}: {len(vix_returns):4d} days, "
                  f"Ann.Ret: {vix_returns.mean()*252*100:6.2f}%, "
                  f"Sharpe: {sharpe:.2f}, "
                  f"Avg.Pos: {avg_pos*100:.1f}%")
    
    returns_df.to_csv('/home/ubuntu/ares_v5_2_results.csv')
    print("\n" + "="*60)
    print("Results saved to /home/ubuntu/ares_v5_2_results.csv")
    print("="*60)

if __name__ == "__main__":
    main()
