#!/usr/bin/env python3
"""
ARES Ultimate v6.4 - 더 공격적인 포지션 사이징
변경 사항:
1. 변동성 타겟 상향 (15% → 18%)
2. 포지션 멀티플라이어 추가 상향
3. 변동성 스케일 상한 증가
"""

import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

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
        # 더 공격적인 포지션
        self.multipliers = {
            MarketRegime.ULTRA_LOW_VIX: 1.5,  # 1.2 → 1.5
            MarketRegime.LOW_VIX: 1.2,         # 1.0 → 1.2
            MarketRegime.MODERATE_VIX: 0.8,    # 0.7 → 0.8
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

class AlphaEngine:
    def compute_alpha(self, prices: pd.DataFrame) -> pd.Series:
        if prices.empty or len(prices) < 252:
            return pd.Series(0.0, index=prices.columns)
        
        prices = prices.ffill()
        daily_returns = prices.pct_change()
        
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        momentum = ret_12m - ret_1m
        
        vol_60d = daily_returns.iloc[-60:].std()
        low_vol = -vol_60d
        
        momentum_z = (momentum - momentum.mean()) / (momentum.std() + 1e-8)
        low_vol_z = (low_vol - low_vol.mean()) / (low_vol.std() + 1e-8)
        
        alpha = momentum_z * 0.6 + low_vol_z * 0.4
        return alpha.fillna(0)

class DynamicVolatilityTargeting:
    def __init__(self, target_vol: float = 0.18):  # 15% → 18%
        self.target_vol = target_vol
    
    def compute_scale(self, forecast_vol: float) -> float:
        if forecast_vol <= 0:
            return 1.0
        scale = self.target_vol / forecast_vol
        return min(max(scale, 0.5), 2.5)  # 상한 2.0 → 2.5

class PortfolioOptimizer:
    def __init__(self, vol_targeting: DynamicVolatilityTargeting):
        self.vol_targeting = vol_targeting
    
    def optimize(self, returns: pd.DataFrame, alpha: pd.Series, regime: MarketRegime, top_n: int = 12) -> pd.Series:
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
            actual_top_n = min(top_n, 10)
        elif regime == MarketRegime.MODERATE_VIX:
            actual_top_n = min(top_n, 7)
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
        return prices.ffill()
    
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

class BacktestEngine:
    def __init__(self, db_path: str, transaction_cost_bps: float = 5.0):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.regime_detector = DynamicVIXRegimeDetector()
        self.alpha_engine = AlphaEngine()
        self.vol_targeting = DynamicVolatilityTargeting(target_vol=0.18)
        self.optimizer = PortfolioOptimizer(self.vol_targeting)
    
    def run(self, symbols: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
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
        
        rebalance_dates = prices.resample('W-FRI').last().index
        
        for i, date in enumerate(prices.index[1:], 1):
            current_vix = vix.loc[date] if date in vix.index else 20
            regime_state = self.regime_detector.detect(current_vix)
            
            if date in rebalance_dates:
                if regime_state.regime != MarketRegime.CASH:
                    alpha = self.alpha_engine.compute_alpha(prices.iloc[:i])
                    lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                    new_weights = self.optimizer.optimize(lookback_returns, alpha, regime_state.regime)
                    new_positions = new_weights * regime_state.position_multiplier
                else:
                    new_positions = pd.Series(0.0, index=prices.columns)
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.transaction_cost
                positions = new_positions
            else:
                if regime_state.regime == MarketRegime.CASH and positions.sum() > 0.01:
                    turnover = positions.abs().sum()
                    cost = turnover * self.transaction_cost
                    positions = pd.Series(0.0, index=prices.columns)
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
                'position_size': positions.sum()
            })
            
            portfolio_value.append(portfolio_value[-1] * (1 + portfolio_return))
        
        returns_df = pd.DataFrame(daily_returns).set_index('date')
        return {'metrics': self._compute_metrics(returns_df), 'returns': returns_df}
    
    def _compute_metrics(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        returns = returns_df['return']
        
        total_return = (1 + returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(returns)) - 1
        annual_vol = returns.std() * np.sqrt(252)
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        max_drawdown = ((cumulative - peak) / peak).min()
        
        invest_ratio = (returns_df['position_size'] > 0.01).mean()
        
        invested_returns = returns_df[returns_df['position_size'] > 0.01]['return']
        invest_sharpe = invested_returns.mean() / invested_returns.std() * np.sqrt(252) if len(invested_returns) > 0 and invested_returns.std() > 0 else 0
        
        vix_metrics = {}
        for low, high in [(0, 13), (13, 16), (16, 18), (18, 100)]:
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
            'invest_ratio': invest_ratio,
            'invest_sharpe': invest_sharpe,
            'vix_metrics': vix_metrics
        }

def main():
    print("="*60)
    print("ARES Ultimate v6.4 - 더 공격적인 포지션")
    print("="*60)
    
    DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    conn = sqlite3.connect(DB_PATH)
    symbols_df = pd.read_sql_query("""
        SELECT DISTINCT symbol FROM daily_ohlcv
        WHERE date >= '2020-01-01'
        GROUP BY symbol HAVING COUNT(*) > 500
        ORDER BY COUNT(*) DESC LIMIT 50
    """, conn)
    conn.close()
    
    symbols = symbols_df['symbol'].tolist()
    
    engine = BacktestEngine(DB_PATH)
    result = engine.run(symbols, "2020-01-01", "2024-12-31")
    
    if not result:
        return
    
    m = result['metrics']
    
    print(f"\nTotal Return:     {m['total_return']*100:.2f}%")
    print(f"Annual Return:    {m['annual_return']*100:.2f}%")
    print(f"Annual Volatility:{m['annual_vol']*100:.2f}%")
    print(f"Sharpe Ratio:     {m['sharpe']:.2f}")
    print(f"Max Drawdown:     {m['max_drawdown']*100:.2f}%")
    print(f"Investment Ratio: {m['invest_ratio']*100:.2f}%")
    print(f"Invest Sharpe:    {m['invest_sharpe']:.2f}")
    
    print("\nVIX Breakdown:")
    for vix_range, vm in m['vix_metrics'].items():
        print(f"  VIX {vix_range:8s}: {vm['days']:4d}d, Ann.Ret: {vm['annual_return']*100:6.2f}%, Sharpe: {vm['sharpe']:.2f}, Pos: {vm['avg_position']*100:.1f}%")

if __name__ == "__main__":
    main()
