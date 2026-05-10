#!/usr/bin/env python3
"""
ARES Ultimate v5.5 - 원래 시스템 구조 복원
- Traffic Light System (6개 지표)
- 다중 팩터 알파 (모멘텀, 저변동성, 품질, 가치)
- 동적 포지션 사이징
"""

import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════════════════════════
# TRAFFIC LIGHT SYSTEM (원래 ARES 구조 복원)
# ═══════════════════════════════════════════════════════════════════════════════

class TrafficLightColor(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

class MarketRegime(Enum):
    BULL = "bull"
    NORMAL = "normal"
    CAUTION = "caution"
    CRISIS = "crisis"

@dataclass
class TrafficLightState:
    color: TrafficLightColor
    regime: MarketRegime
    vix_signal: float
    momentum_signal: float
    volatility_signal: float
    trend_signal: float
    composite_score: float
    position_multiplier: float

class TrafficLightSystem:
    """
    원래 ARES Traffic Light System 복원
    6개 지표 기반 레짐 감지:
    1. VIX Level
    2. VIX Change (급등 감지)
    3. Market Momentum (SPY 20일 모멘텀)
    4. Market Volatility (실현 변동성)
    5. Trend Strength (SPY vs MA50)
    6. Breadth (상승 종목 비율)
    """
    
    def __init__(self, 
                 vix_green: float = 15,
                 vix_yellow: float = 20,
                 vix_red: float = 25,
                 momentum_threshold: float = 0.0,
                 volatility_threshold: float = 0.02,
                 hysteresis_days: int = 3):
        self.vix_green = vix_green
        self.vix_yellow = vix_yellow
        self.vix_red = vix_red
        self.momentum_threshold = momentum_threshold
        self.volatility_threshold = volatility_threshold
        self.hysteresis_days = hysteresis_days
        
        self.previous_color = TrafficLightColor.GREEN
        self.color_hold_days = 0
    
    def compute_signals(self, 
                       vix: float,
                       vix_change: float,
                       market_momentum: float,
                       market_volatility: float,
                       trend_strength: float,
                       breadth: float) -> Dict[str, float]:
        """각 지표별 신호 계산 (-1 ~ +1)"""
        
        # VIX Signal: 낮을수록 좋음
        if vix < self.vix_green:
            vix_signal = 1.0
        elif vix < self.vix_yellow:
            vix_signal = 0.5 - (vix - self.vix_green) / (self.vix_yellow - self.vix_green)
        elif vix < self.vix_red:
            vix_signal = -0.5 - (vix - self.vix_yellow) / (self.vix_red - self.vix_yellow) * 0.5
        else:
            vix_signal = -1.0
        
        # VIX Change Signal: 급등 감지
        if vix_change > 0.2:  # 20% 이상 급등
            vix_change_signal = -1.0
        elif vix_change > 0.1:
            vix_change_signal = -0.5
        elif vix_change < -0.1:
            vix_change_signal = 0.5
        else:
            vix_change_signal = 0.0
        
        # Momentum Signal
        momentum_signal = np.clip(market_momentum * 10, -1, 1)
        
        # Volatility Signal: 낮을수록 좋음
        if market_volatility < self.volatility_threshold:
            volatility_signal = 1.0
        elif market_volatility < self.volatility_threshold * 2:
            volatility_signal = 0.0
        else:
            volatility_signal = -1.0
        
        # Trend Signal
        trend_signal = np.clip(trend_strength * 5, -1, 1)
        
        # Breadth Signal
        breadth_signal = (breadth - 0.5) * 2  # 0.5 = neutral
        
        return {
            'vix': vix_signal,
            'vix_change': vix_change_signal,
            'momentum': momentum_signal,
            'volatility': volatility_signal,
            'trend': trend_signal,
            'breadth': breadth_signal
        }
    
    def determine_state(self,
                       vix: float,
                       vix_change: float,
                       market_momentum: float,
                       market_volatility: float,
                       trend_strength: float,
                       breadth: float) -> TrafficLightState:
        """Traffic Light 상태 결정"""
        
        signals = self.compute_signals(
            vix, vix_change, market_momentum, 
            market_volatility, trend_strength, breadth
        )
        
        # Composite Score (가중 평균)
        weights = {
            'vix': 0.30,
            'vix_change': 0.15,
            'momentum': 0.20,
            'volatility': 0.15,
            'trend': 0.10,
            'breadth': 0.10
        }
        
        composite_score = sum(signals[k] * weights[k] for k in weights)
        
        # Color 결정
        if composite_score > 0.3:
            new_color = TrafficLightColor.GREEN
        elif composite_score > -0.3:
            new_color = TrafficLightColor.YELLOW
        else:
            new_color = TrafficLightColor.RED
        
        # Hysteresis 적용
        if new_color != self.previous_color:
            self.color_hold_days += 1
            if self.color_hold_days < self.hysteresis_days:
                new_color = self.previous_color
            else:
                self.previous_color = new_color
                self.color_hold_days = 0
        else:
            self.color_hold_days = 0
        
        # Regime 결정
        if new_color == TrafficLightColor.GREEN:
            if composite_score > 0.6:
                regime = MarketRegime.BULL
            else:
                regime = MarketRegime.NORMAL
        elif new_color == TrafficLightColor.YELLOW:
            regime = MarketRegime.CAUTION
        else:
            regime = MarketRegime.CRISIS
        
        # Position Multiplier 결정
        if regime == MarketRegime.BULL:
            position_multiplier = 1.0
        elif regime == MarketRegime.NORMAL:
            position_multiplier = 0.8
        elif regime == MarketRegime.CAUTION:
            position_multiplier = 0.3
        else:  # CRISIS
            position_multiplier = 0.0
        
        return TrafficLightState(
            color=new_color,
            regime=regime,
            vix_signal=signals['vix'],
            momentum_signal=signals['momentum'],
            volatility_signal=signals['volatility'],
            trend_signal=signals['trend'],
            composite_score=composite_score,
            position_multiplier=position_multiplier
        )

# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-FACTOR ALPHA ENGINE (원래 ARES 구조 복원)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiFactorAlphaEngine:
    """
    다중 팩터 알파 엔진:
    1. Momentum (12-1)
    2. Low Volatility
    3. Quality (수익률 안정성)
    4. Value (가격 대비 성과)
    """
    
    def __init__(self, 
                 momentum_weight: float = 0.35,
                 low_vol_weight: float = 0.25,
                 quality_weight: float = 0.25,
                 value_weight: float = 0.15):
        self.weights = {
            'momentum': momentum_weight,
            'low_vol': low_vol_weight,
            'quality': quality_weight,
            'value': value_weight
        }
    
    def compute_factors(self, prices: pd.DataFrame) -> Dict[str, pd.Series]:
        """각 팩터 계산"""
        if prices.empty or len(prices) < 252:
            return {}
        
        prices = prices.ffill()
        daily_returns = prices.pct_change()
        
        # 1. Momentum (12-1)
        ret_12m = prices.iloc[-1] / prices.iloc[-252] - 1
        ret_1m = prices.iloc[-1] / prices.iloc[-21] - 1
        momentum = ret_12m - ret_1m
        
        # 2. Low Volatility
        vol_60d = daily_returns.iloc[-60:].std()
        low_vol = -vol_60d
        
        # 3. Quality (수익률 안정성 = Sharpe-like)
        mean_ret = daily_returns.iloc[-252:].mean()
        std_ret = daily_returns.iloc[-252:].std()
        quality = mean_ret / (std_ret + 1e-8)
        
        # 4. Value (52주 최고가 대비 현재가)
        high_52w = prices.iloc[-252:].max()
        current = prices.iloc[-1]
        value = -(current / high_52w - 1)  # 최고가 대비 낮을수록 좋음 (역발상)
        
        return {
            'momentum': momentum,
            'low_vol': low_vol,
            'quality': quality,
            'value': value
        }
    
    def compute_alpha(self, prices: pd.DataFrame) -> pd.Series:
        """종합 알파 계산"""
        factors = self.compute_factors(prices)
        
        if not factors:
            return pd.Series(1.0 / len(prices.columns), index=prices.columns)
        
        # Z-score 정규화
        normalized = {}
        for name, factor in factors.items():
            factor = factor.fillna(0)
            z = (factor - factor.mean()) / (factor.std() + 1e-8)
            normalized[name] = z
        
        # 가중 합산
        alpha = sum(normalized[k] * self.weights[k] for k in self.weights)
        alpha = alpha.fillna(0)
        
        return alpha

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
        
        # Inverse volatility weighting
        vol = returns[top_symbols].std()
        inv_vol = 1 / (vol + 1e-8)
        weights = inv_vol / inv_vol.sum()
        
        result = pd.Series(0.0, index=alpha.index)
        result[top_symbols] = weights
        
        return result

# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestEngine:
    def __init__(self, db_path: str, transaction_cost_bps: float = 5.0):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.traffic_light = TrafficLightSystem()
        self.alpha_engine = MultiFactorAlphaEngine()
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
            
            # Market indicators 계산
            if i >= 21:
                vix_change = (current_vix / vix.iloc[i-5] - 1) if i >= 5 else 0
                market_prices = prices.iloc[:i].mean(axis=1)
                market_momentum = (market_prices.iloc[-1] / market_prices.iloc[-21] - 1) if len(market_prices) >= 21 else 0
                market_volatility = prices.iloc[max(0,i-21):i].pct_change().std().mean()
                trend_strength = (market_prices.iloc[-1] / market_prices.iloc[-50:].mean() - 1) if len(market_prices) >= 50 else 0
                breadth = (prices.iloc[i] > prices.iloc[i-21]).mean() if i >= 21 else 0.5
            else:
                vix_change = 0
                market_momentum = 0
                market_volatility = 0.02
                trend_strength = 0
                breadth = 0.5
            
            # Traffic Light 상태 결정
            tl_state = self.traffic_light.determine_state(
                current_vix, vix_change, market_momentum,
                market_volatility, trend_strength, breadth
            )
            
            if date in rebalance_dates:
                alpha = self.alpha_engine.compute_alpha(prices.iloc[:i])
                lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                new_weights = self.optimizer.optimize(lookback_returns, alpha, top_n=10)
                new_positions = new_weights * tl_state.position_multiplier
                
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * self.transaction_cost
                
                positions = new_positions
            else:
                # CRISIS면 즉시 청산
                if tl_state.regime == MarketRegime.CRISIS:
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
                'regime': tl_state.regime.value,
                'traffic_light': tl_state.color.value,
                'vix': current_vix,
                'composite_score': tl_state.composite_score,
                'position_size': positions.sum(),
                'position_multiplier': tl_state.position_multiplier
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
        for regime in ['bull', 'normal', 'caution', 'crisis']:
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

def main():
    print("="*60)
    print("ARES Ultimate v5.5 - Traffic Light + Multi-Factor")
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
    
    returns_df = result['returns']
    print("\n" + "-"*60)
    print("TRAFFIC LIGHT BREAKDOWN")
    print("-"*60)
    for color in ['green', 'yellow', 'red']:
        color_returns = returns_df[returns_df['traffic_light'] == color]['return']
        if len(color_returns) > 0:
            c_std = color_returns.std()
            sharpe = color_returns.mean() / c_std * np.sqrt(252) if c_std > 0 else 0
            avg_pos = returns_df[returns_df['traffic_light'] == color]['position_size'].mean()
            print(f"{color.upper():12s}: {len(color_returns):4d} days, "
                  f"Ann.Ret: {color_returns.mean()*252*100:6.2f}%, "
                  f"Sharpe: {sharpe:.2f}, "
                  f"Avg.Pos: {avg_pos*100:.1f}%")
    
    returns_df.to_csv('/home/ubuntu/ares_v5_5_results.csv')
    print("\n" + "="*60)
    print("Results saved to /home/ubuntu/ares_v5_5_results.csv")
    print("="*60)

if __name__ == "__main__":
    main()
