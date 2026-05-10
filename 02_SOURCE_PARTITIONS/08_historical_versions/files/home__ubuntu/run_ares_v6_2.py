#!/usr/bin/env python3
"""
ARES Ultimate v6.2 - v6.1 기반 + 추가 팩터 (Quality + Short-term Reversal)
"""

import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# REGIME DETECTOR (v5.7 유지)
# =============================================================================

class MarketRegime(Enum):
    ULTRA_LOW_VIX = "ultra_low_vix"
    LOW_VIX = "low_vix"
    MODERATE_VIX = "moderate_vix"
    CASH = "cash"

@dataclass
class RegimeState:
    regime: MarketRegime
    vix: float
    position_multiplier: float
    
class DynamicVIXRegimeDetector:
    def __init__(self):
        self.thresholds = {'ultra_low': 13, 'low': 16, 'moderate': 18}
        self.multipliers = {
            MarketRegime.ULTRA_LOW_VIX: 1.0,
            MarketRegime.LOW_VIX: 0.8,
            MarketRegime.MODERATE_VIX: 0.5,
            MarketRegime.CASH: 0.0
        }
    
    def detect(self, vix: float) -> RegimeState:
        if vix < self.thresholds['ultra_low']:
            regime = MarketRegime.ULTRA_LOW_VIX
        elif vix < self.thresholds['low']:
            regime = MarketRegime.LOW_VIX
        elif vix < self.thresholds['moderate']:
            regime = MarketRegime.MODERATE_VIX
        else:
            regime = MarketRegime.CASH
        return RegimeState(regime=regime, vix=vix, position_multiplier=self.multipliers[regime])

# =============================================================================
# ENHANCED ALPHA ENGINE (4팩터)
# =============================================================================

class EnhancedAlphaEngine:
    def __init__(self):
        # 팩터 가중치
        self.factor_weights = {
            'momentum_12_1': 0.40,
            'low_vol': 0.30,
            'quality': 0.15,
            'reversal': 0.15
        }
    
    def compute_alpha(self, prices: pd.DataFrame) -> pd.Series:
        if prices.empty or len(prices) < 252:
            return pd.Series(0.0, index=prices.columns)
        
        prices = prices.ffill()
        daily_returns = prices.pct_change()
        
        # 1. Momentum 12-1
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        momentum_12_1 = ret_12m - ret_1m
        
        # 2. Low Volatility
        vol_60d = daily_returns.iloc[-60:].std()
        low_vol = -vol_60d
        
        # 3. Quality (Risk-adjusted return)
        mean_ret = daily_returns.iloc[-126:].mean()
        std_ret = daily_returns.iloc[-126:].std()
        quality = mean_ret / (std_ret + 1e-8)
        
        # 4. Short-term Reversal (5일)
        ret_5d = prices.iloc[-1] / prices.iloc[-5] - 1
        reversal = -ret_5d  # 단기 하락 종목 선호
        
        # Z-score 정규화
        factors = {
            'momentum_12_1': momentum_12_1,
            'low_vol': low_vol,
            'quality': quality,
            'reversal': reversal
        }
        
        normalized = {}
        for name, factor in factors.items():
            factor = factor.fillna(0)
            z = (factor - factor.mean()) / (factor.std() + 1e-8)
            z = z.clip(-3, 3)
            normalized[name] = z
        
        # 가중 합산
        alpha = sum(normalized[k] * self.factor_weights[k] for k in self.factor_weights)
        alpha = alpha.fillna(0)
        
        return alpha

# =============================================================================
# DYNAMIC VOLATILITY TARGETING
# =============================================================================

class DynamicVolatilityTargeting:
    def __init__(self, target_vol: float = 0.12):
        self.target_vol = target_vol
    
    def compute_scale(self, forecast_vol: float) -> float:
        if forecast_vol <= 0:
            return 1.0
        scale = self.target_vol / forecast_vol
        return min(max(scale, 0.5), 1.5)

# =============================================================================
# PORTFOLIO OPTIMIZER
# =============================================================================

class PortfolioOptimizer:
    def __init__(self, vol_targeting: DynamicVolatilityTargeting):
        self.vol_targeting = vol_targeting
    
    def optimize(self, returns: pd.DataFrame, alpha: pd.Series, regime: MarketRegime, top_n: int = 10) -> pd.Series:
        if returns.empty or len(returns) < 20:
            return pd.Series(0.0, index=alpha.index)
        
        vol = returns.std()
        vol_threshold = vol.quantile(0.8)
        
        valid_mask = (alpha > 0) & (vol < vol_threshold)
        filtered_alpha = alpha[valid_mask]
        
        if len(filtered_alpha) == 0:
            filtered_alpha = alpha[alpha > 0]
        if len(filtered_alpha) == 0:
            return pd.Series(0.0, index=alpha.index)
        
        if regime == MarketRegime.ULTRA_LOW_VIX:
            actual_top_n = top_n
        elif regime == MarketRegime.LOW_VIX:
            actual_top_n = min(top_n, 8)
        elif regime == MarketRegime.MODERATE_VIX:
            actual_top_n = min(top_n, 5)
        else:
            return pd.Series(0.0, index=alpha.index)
        
        top_symbols = filtered_alpha.nlargest(actual_top_n).index
        vol_selected = vol[top_symbols]
        inv_vol = 1 / (vol_selected + 1e-8)
        weights = inv_vol / inv_vol.sum()
        
        portfolio_vol = returns[top_symbols].std().mean() * np.sqrt(252)
        vol_scale = self.vol_targeting.compute_scale(portfolio_vol)
        
        result = pd.Series(0.0, index=alpha.index)
        result[top_symbols] = weights * vol_scale
        return result

# =============================================================================
# DATA PIPELINE
# =============================================================================

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

# =============================================================================
# BACKTEST ENGINE
# =============================================================================

class BacktestEngine:
    def __init__(self, db_path: str, transaction_cost_bps: float = 5.0):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.regime_detector = DynamicVIXRegimeDetector()
        self.alpha_engine = EnhancedAlphaEngine()
        self.vol_targeting = DynamicVolatilityTargeting(target_vol=0.12)
        self.optimizer = PortfolioOptimizer(self.vol_targeting)
    
    def run(self, symbols: List[str], start_date: str, end_date: str, rebalance_freq: str = 'W') -> Dict[str, Any]:
        print(f"Loading data for {len(symbols)} symbols...")
        
        prices = self.pipeline.load_prices(symbols, start_date, end_date)
        vix = self.pipeline.load_vix(start_date, end_date)
        
        if prices.empty:
            return {}
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        vix = vix.reindex(prices.index).ffill().fillna(20)
        
        portfolio_value = [1.0]
        positions = pd.Series(0.0, index=prices.columns)
        daily_returns = []
        
        if rebalance_freq == 'W':
            rebalance_dates = prices.resample('W-FRI').last().index
        else:
            rebalance_dates = prices.index[::5]
        
        print(f"Running backtest with {len(rebalance_dates)} rebalance dates...")
        
        for i, date in enumerate(prices.index[1:], 1):
            current_vix = vix.loc[date] if date in vix.index else 20
            regime_state = self.regime_detector.detect(current_vix)
            
            if date in rebalance_dates:
                if regime_state.regime != MarketRegime.CASH:
                    alpha = self.alpha_engine.compute_alpha(prices.iloc[:i])
                    lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                    new_weights = self.optimizer.optimize(lookback_returns, alpha, regime_state.regime, top_n=10)
                    new_positions = new_weights * regime_state.position_multiplier
                else:
                    new_positions = pd.Series(0.0, index=prices.columns)
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.transaction_cost
                positions = new_positions
            else:
                if regime_state.regime == MarketRegime.CASH:
                    if positions.sum() > 0.01:
                        turnover = positions.abs().sum()
                        cost = turnover * self.transaction_cost
                        positions = pd.Series(0.0, index=prices.columns)
                    else:
                        cost = 0
                else:
                    cost = 0
            
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
        
        returns_df = pd.DataFrame(daily_returns)
        returns_df.set_index('date', inplace=True)
        
        metrics = self._compute_metrics(returns_df)
        
        return {'metrics': metrics, 'returns': returns_df, 'portfolio_value': portfolio_value}
    
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
        
        invested_returns = returns_df[returns_df['position_size'] > 0.01]['return']
        invest_sharpe = invested_returns.mean() / invested_returns.std() * np.sqrt(252) if len(invested_returns) > 0 and invested_returns.std() > 0 else 0
        
        vix_metrics = {}
        vix_bins = [(0, 13), (13, 16), (16, 18), (18, 20), (20, 25), (25, 100)]
        for low, high in vix_bins:
            mask = (returns_df['vix'] >= low) & (returns_df['vix'] < high)
            vix_returns = returns_df[mask]['return']
            if len(vix_returns) > 0:
                v_std = vix_returns.std()
                vix_metrics[f'{low}-{high}'] = {
                    'days': len(vix_returns),
                    'annual_return': vix_returns.mean() * 252,
                    'sharpe': vix_returns.mean() / v_std * np.sqrt(252) if v_std > 0 else 0,
                    'avg_position': returns_df[mask]['position_size'].mean()
                }
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_vol': annual_vol,
            'sharpe': sharpe,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'invest_ratio': invest_ratio,
            'invest_sharpe': invest_sharpe,
            'vix_metrics': vix_metrics
        }

def main():
    print("="*60)
    print("ARES Ultimate v6.2 - 4팩터 (Mom+LowVol+Quality+Reversal)")
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
    print("VIX BREAKDOWN")
    print("-"*60)
    for vix_range, vm in metrics['vix_metrics'].items():
        print(f"VIX {vix_range:8s}: {vm['days']:4d} days, "
              f"Ann.Ret: {vm['annual_return']*100:6.2f}%, "
              f"Sharpe: {vm['sharpe']:.2f}, "
              f"Avg.Pos: {vm['avg_position']*100:.1f}%")

if __name__ == "__main__":
    main()
