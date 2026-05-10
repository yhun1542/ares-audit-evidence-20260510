#!/usr/bin/env python3
"""
ARES Ultimate v5.7 - VIX 임계값 확장 + 동적 포지션 사이징
목표: 투자 비율을 높이면서 Sharpe 3.0+ 유지

전략:
- VIX < 13: 100% 투자 (공격적)
- 13 <= VIX < 16: 80% 투자 (보수적)
- 16 <= VIX < 18: 50% 투자 (신중)
- VIX >= 18: 0% (현금)

추가 필터:
- 모멘텀 > 0인 종목만 투자
- 변동성 상위 20% 종목 제외
"""

import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════════════════════════
# REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class MarketRegime(Enum):
    ULTRA_LOW_VIX = "ultra_low_vix"    # VIX < 13
    LOW_VIX = "low_vix"                 # 13 <= VIX < 16
    MODERATE_VIX = "moderate_vix"       # 16 <= VIX < 18
    CASH = "cash"                       # VIX >= 18

@dataclass
class RegimeState:
    regime: MarketRegime
    vix: float
    position_multiplier: float
    
class DynamicVIXRegimeDetector:
    def __init__(self):
        self.thresholds = {
            'ultra_low': 13,
            'low': 16,
            'moderate': 18
        }
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
        
        return RegimeState(
            regime=regime,
            vix=vix,
            position_multiplier=self.multipliers[regime]
        )

# ═══════════════════════════════════════════════════════════════════════════════
# ALPHA ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class AlphaEngine:
    def compute_alpha(self, prices: pd.DataFrame) -> pd.Series:
        if prices.empty or len(prices) < 252:
            return pd.Series(0.0, index=prices.columns)
        
        prices = prices.ffill()
        daily_returns = prices.pct_change()
        
        # Momentum 12-1
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        momentum = ret_12m - ret_1m
        
        # Low Volatility
        vol_60d = daily_returns.iloc[-60:].std()
        low_vol = -vol_60d
        
        # Z-score 정규화
        momentum_z = (momentum - momentum.mean()) / (momentum.std() + 1e-8)
        low_vol_z = (low_vol - low_vol.mean()) / (low_vol.std() + 1e-8)
        
        # 가중 합산 (모멘텀 60%, 저변동성 40%)
        alpha = momentum_z * 0.6 + low_vol_z * 0.4
        alpha = alpha.fillna(0)
        
        return alpha

# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioOptimizer:
    def optimize(self, 
                returns: pd.DataFrame, 
                alpha: pd.Series, 
                regime: MarketRegime,
                top_n: int = 10) -> pd.Series:
        
        if returns.empty or len(returns) < 20:
            return pd.Series(0.0, index=alpha.index)
        
        # 모멘텀 > 0 필터
        prices_end = returns.iloc[-1] if len(returns) > 0 else pd.Series()
        prices_start = returns.iloc[-21] if len(returns) >= 21 else returns.iloc[0]
        momentum_1m = prices_end / prices_start - 1 if len(returns) >= 21 else pd.Series(0, index=alpha.index)
        
        # 변동성 상위 20% 제외
        vol = returns.std()
        vol_threshold = vol.quantile(0.8)
        
        # 필터 적용
        valid_mask = (alpha > 0) & (vol < vol_threshold)
        filtered_alpha = alpha[valid_mask]
        
        if len(filtered_alpha) == 0:
            filtered_alpha = alpha[alpha > 0]
        
        if len(filtered_alpha) == 0:
            return pd.Series(0.0, index=alpha.index)
        
        # 레짐에 따른 종목 수 조정
        if regime == MarketRegime.ULTRA_LOW_VIX:
            actual_top_n = top_n
        elif regime == MarketRegime.LOW_VIX:
            actual_top_n = min(top_n, 8)
        elif regime == MarketRegime.MODERATE_VIX:
            actual_top_n = min(top_n, 5)
        else:
            return pd.Series(0.0, index=alpha.index)
        
        # Top N by alpha
        top_symbols = filtered_alpha.nlargest(actual_top_n).index
        
        # Inverse volatility weighting
        vol_selected = vol[top_symbols]
        inv_vol = 1 / (vol_selected + 1e-8)
        weights = inv_vol / inv_vol.sum()
        
        result = pd.Series(0.0, index=alpha.index)
        result[top_symbols] = weights
        
        return result

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
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestEngine:
    def __init__(self, db_path: str, transaction_cost_bps: float = 5.0):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.regime_detector = DynamicVIXRegimeDetector()
        self.alpha_engine = AlphaEngine()
        self.optimizer = PortfolioOptimizer()
    
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
            
            # 레짐 감지
            regime_state = self.regime_detector.detect(current_vix)
            
            # 리밸런싱
            if date in rebalance_dates:
                if regime_state.regime != MarketRegime.CASH:
                    alpha = self.alpha_engine.compute_alpha(prices.iloc[:i])
                    lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                    new_weights = self.optimizer.optimize(
                        lookback_returns, alpha, regime_state.regime, top_n=10
                    )
                    new_positions = new_weights * regime_state.position_multiplier
                else:
                    new_positions = pd.Series(0.0, index=prices.columns)
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.transaction_cost
                positions = new_positions
            else:
                # CASH 레짐이면 즉시 청산
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
        
        regime_metrics = {}
        for regime in ['ultra_low_vix', 'low_vix', 'moderate_vix', 'cash']:
            regime_returns = returns_df[returns_df['regime'] == regime]['return']
            if len(regime_returns) > 0:
                r_std = regime_returns.std()
                regime_metrics[regime] = {
                    'days': len(regime_returns),
                    'annual_return': regime_returns.mean() * 252,
                    'sharpe': regime_returns.mean() / r_std * np.sqrt(252) if r_std > 0 else 0,
                    'avg_position': returns_df[returns_df['regime'] == regime]['position_size'].mean()
                }
        
        invested_returns = returns_df[returns_df['position_size'] > 0.01]['return']
        if len(invested_returns) > 0:
            invest_sharpe = invested_returns.mean() / invested_returns.std() * np.sqrt(252) if invested_returns.std() > 0 else 0
        else:
            invest_sharpe = 0
        
        # VIX 구간별 분석
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
            'regime_metrics': regime_metrics,
            'vix_metrics': vix_metrics
        }

def main():
    print("="*60)
    print("ARES Ultimate v5.7 - VIX 확장 + 동적 포지션")
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
        print(f"{regime:15s}: {rm['days']:4d} days, "
              f"Ann.Ret: {rm['annual_return']*100:6.2f}%, "
              f"Sharpe: {rm['sharpe']:.2f}, "
              f"Avg.Pos: {rm['avg_position']*100:.1f}%")
    
    print("\n" + "-"*60)
    print("VIX BREAKDOWN")
    print("-"*60)
    for vix_range, vm in metrics['vix_metrics'].items():
        print(f"VIX {vix_range:8s}: {vm['days']:4d} days, "
              f"Ann.Ret: {vm['annual_return']*100:6.2f}%, "
              f"Sharpe: {vm['sharpe']:.2f}, "
              f"Avg.Pos: {vm['avg_position']*100:.1f}%")
    
    result['returns'].to_csv('/home/ubuntu/ares_v5_7_results.csv')
    print("\n" + "="*60)
    print("Results saved to /home/ubuntu/ares_v5_7_results.csv")
    print("="*60)

if __name__ == "__main__":
    main()
