#!/usr/bin/env python3
"""
ARES Ultimate v5.0 - Claude 버전 실행 스크립트
NaN 처리 및 DB 연동 수정
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
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
    TIGHTENING = "tightening"

class VolRegime(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"

class TrafficLight(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

@dataclass
class RegimeState:
    regime: MarketRegime
    vol_regime: VolRegime
    traffic_light: TrafficLight
    vix_level: float
    confidence: float = 0.8

# ═══════════════════════════════════════════════════════════════════════════════
# DATA PIPELINE (PIT-SAFE, NO LOOKAHEAD)
# ═══════════════════════════════════════════════════════════════════════════════

class PITDataPipeline:
    """Point-in-Time Safe Data Pipeline - ffill only, no bfill"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def load_prices(self, symbols: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """Load daily OHLCV data"""
        conn = sqlite3.connect(self.db_path)
        
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
            SELECT symbol, date, open, high, low, close, volume
            FROM daily_ohlcv
            WHERE symbol IN ({placeholders})
            AND date >= ? AND date <= ?
            ORDER BY date, symbol
        """
        
        df = pd.read_sql_query(query, conn, params=symbols + [start_date, end_date])
        conn.close()
        
        df['date'] = pd.to_datetime(df['date'])
        
        # 중복 제거 (날짜+심볼 기준 마지막 값 유지)
        df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
        
        # Pivot to wide format
        prices = df.pivot(index='date', columns='symbol', values='close')
        
        # NaN 처리: ffill만 사용 (bfill 절대 금지)
        prices = prices.ffill()
        
        return prices
    
    def load_vix(self, start_date: str, end_date: str) -> pd.Series:
        """Load VIX data"""
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
        
        # NaN 처리: ffill만 사용
        return df['vix'].ffill()
    
    def compute_features(self, prices: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all features with PIT compliance
        Critical: ffill() only, never bfill()
        """
        if prices.empty:
            return pd.DataFrame()
        
        # NaN 처리: 먼저 ffill
        prices = prices.ffill()
        
        features = pd.DataFrame(index=prices.index)
        
        # Momentum Features (PIT-safe)
        for period, name in [(21, '1m'), (63, '3m'), (126, '6m'), (252, '12m')]:
            ret = prices.pct_change(period)
            # NaN 처리: ffill 후 0으로 채움
            features[f'ret_{name}'] = ret.mean(axis=1).ffill().fillna(0)
        
        # Residual momentum (12-1)
        features['momentum_12_1'] = features['ret_12m'] - features['ret_1m']
        
        # Volatility Features
        daily_ret = prices.pct_change()
        daily_ret = daily_ret.ffill().fillna(0)
        
        features['vol_20d'] = daily_ret.rolling(20, min_periods=5).std().mean(axis=1) * np.sqrt(252)
        features['vol_60d'] = daily_ret.rolling(60, min_periods=20).std().mean(axis=1) * np.sqrt(252)
        features['vol_ratio'] = features['vol_20d'] / (features['vol_60d'] + 1e-8)
        
        # NaN 최종 처리
        features = features.ffill().fillna(0)
        
        return features

# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-SOURCE REGIME DETECTOR (Traffic Light + Fast Detection)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiSourceRegimeDetector:
    """
    Multi-source regime detection with fast response (< 5 days)
    Uses: VIX, momentum, volatility ratio
    """
    
    def __init__(self):
        self.history = []
        self.current_regime = MarketRegime.NORMAL
        self.regime_days = 0
        
        # Fast detection thresholds (5일 이하 반응)
        self.vix_crisis = 30
        self.vix_high = 25
        self.vix_normal = 18
        self.vix_low = 15
        
        # Hysteresis (짧게 설정 - 빠른 반응)
        self.confirm_days = 2  # 레짐 확정까지 2일
        self.exit_days = 3     # 레짐 탈출까지 3일
        
    def detect(self, vix: float, momentum: float = 0, vol_ratio: float = 1.0) -> RegimeState:
        """Detect current regime with fast response"""
        
        self.history.append({
            'vix': vix,
            'momentum': momentum,
            'vol_ratio': vol_ratio,
            'timestamp': datetime.now()
        })
        
        # Keep only recent history
        if len(self.history) > 60:
            self.history = self.history[-60:]
        
        # Determine vol regime
        if vix >= 35:
            vol_regime = VolRegime.EXTREME
        elif vix >= 25:
            vol_regime = VolRegime.HIGH
        elif vix >= 15:
            vol_regime = VolRegime.NORMAL
        else:
            vol_regime = VolRegime.LOW
        
        # Determine market regime (fast detection)
        new_regime = self._detect_regime_fast(vix, momentum, vol_ratio)
        
        # Apply hysteresis
        if new_regime != self.current_regime:
            self.regime_days += 1
            if self.regime_days >= self.confirm_days:
                self.current_regime = new_regime
                self.regime_days = 0
        else:
            self.regime_days = 0
        
        # Determine traffic light
        traffic_light = self._get_traffic_light(self.current_regime, vol_regime)
        
        return RegimeState(
            regime=self.current_regime,
            vol_regime=vol_regime,
            traffic_light=traffic_light,
            vix_level=vix,
            confidence=self._calculate_confidence()
        )
    
    def _detect_regime_fast(self, vix: float, momentum: float, vol_ratio: float) -> MarketRegime:
        """Fast regime detection (< 5 days response)"""
        
        # Crisis: VIX > 30 OR (VIX > 25 AND momentum < -0.1)
        if vix >= self.vix_crisis or (vix >= self.vix_high and momentum < -0.1):
            return MarketRegime.CRISIS
        
        # Tightening: VIX rising fast (vol_ratio > 1.5)
        if vol_ratio > 1.5 and vix > self.vix_normal:
            return MarketRegime.TIGHTENING
        
        # Inflation: Moderate VIX with positive momentum
        if self.vix_normal <= vix < self.vix_high and momentum > 0.05:
            return MarketRegime.INFLATION
        
        # Normal
        return MarketRegime.NORMAL
    
    def _get_traffic_light(self, regime: MarketRegime, vol_regime: VolRegime) -> TrafficLight:
        """Get traffic light based on regime"""
        
        if regime == MarketRegime.CRISIS or vol_regime == VolRegime.EXTREME:
            return TrafficLight.RED
        elif regime == MarketRegime.TIGHTENING or vol_regime == VolRegime.HIGH:
            return TrafficLight.YELLOW
        else:
            return TrafficLight.GREEN
    
    def _calculate_confidence(self) -> float:
        """Calculate regime confidence"""
        if len(self.history) < 5:
            return 0.5
        
        recent = self.history[-5:]
        vix_std = np.std([h['vix'] for h in recent])
        
        # Lower volatility = higher confidence
        confidence = max(0.3, min(0.95, 1.0 - vix_std / 20))
        return confidence

# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-FACTOR ALPHA ENGINE (7-Sleeve Simplified)
# ═══════════════════════════════════════════════════════════════════════════════

class MultiFactorAlphaEngine:
    """
    Multi-factor alpha generation with regime-conditional weights
    Simplified 7-sleeve system
    """
    
    def __init__(self):
        # Regime-based factor weights
        self.regime_weights = {
            MarketRegime.NORMAL: {
                'momentum': 0.30,
                'value': 0.20,
                'quality': 0.20,
                'low_vol': 0.15,
                'mean_reversion': 0.15
            },
            MarketRegime.CRISIS: {
                'momentum': 0.05,
                'value': 0.10,
                'quality': 0.30,
                'low_vol': 0.40,
                'mean_reversion': 0.15
            },
            MarketRegime.INFLATION: {
                'momentum': 0.35,
                'value': 0.25,
                'quality': 0.15,
                'low_vol': 0.10,
                'mean_reversion': 0.15
            },
            MarketRegime.TIGHTENING: {
                'momentum': 0.15,
                'value': 0.20,
                'quality': 0.25,
                'low_vol': 0.25,
                'mean_reversion': 0.15
            }
        }
    
    def compute_alpha(
        self,
        prices: pd.DataFrame,
        regime: MarketRegime
    ) -> pd.Series:
        """Compute multi-factor alpha scores"""
        
        if prices.empty:
            return pd.Series()
        
        # NaN 처리
        prices = prices.ffill()
        
        # Compute individual factors
        factors = {}
        
        # 1. Momentum (12-1)
        ret_12m = prices.pct_change(252).iloc[-1]
        ret_1m = prices.pct_change(21).iloc[-1]
        factors['momentum'] = (ret_12m - ret_1m).fillna(0)
        
        # 2. Value (inverse of recent performance - mean reversion proxy)
        ret_3m = prices.pct_change(63).iloc[-1]
        factors['value'] = -ret_3m.fillna(0)
        
        # 3. Quality (stability of returns)
        daily_ret = prices.pct_change()
        factors['quality'] = -daily_ret.rolling(60).std().iloc[-1].fillna(1)
        
        # 4. Low Volatility
        vol = daily_ret.rolling(20).std().iloc[-1]
        factors['low_vol'] = -vol.fillna(1)
        
        # 5. Mean Reversion (short-term)
        ret_5d = prices.pct_change(5).iloc[-1]
        factors['mean_reversion'] = -ret_5d.fillna(0)
        
        # Normalize factors (cross-sectional z-score)
        for name in factors:
            f = factors[name]
            factors[name] = (f - f.mean()) / (f.std() + 1e-8)
        
        # Combine with regime weights
        weights = self.regime_weights.get(regime, self.regime_weights[MarketRegime.NORMAL])
        
        alpha = pd.Series(0.0, index=prices.columns)
        for name, weight in weights.items():
            alpha += weight * factors[name]
        
        return alpha

# ═══════════════════════════════════════════════════════════════════════════════
# RISK MANAGER (Vol Targeting + Drawdown Protection)
# ═══════════════════════════════════════════════════════════════════════════════

class IntegratedRiskManager:
    """
    Integrated risk management with:
    - Volatility targeting
    - Drawdown protection
    - Regime-based leverage caps
    """
    
    def __init__(
        self,
        target_vol: float = 0.15,
        max_drawdown: float = 0.15,
        max_leverage: float = 1.0
    ):
        self.target_vol = target_vol
        self.max_drawdown = max_drawdown
        self.max_leverage = max_leverage
        
        self.peak_value = 1.0
        self.current_value = 1.0
        
        # Regime-based leverage caps
        self.regime_leverage = {
            MarketRegime.NORMAL: 1.0,
            MarketRegime.CRISIS: 0.25,
            MarketRegime.INFLATION: 0.75,
            MarketRegime.TIGHTENING: 0.50
        }
        
        # Traffic light multipliers
        self.traffic_multiplier = {
            TrafficLight.GREEN: 1.0,
            TrafficLight.YELLOW: 0.5,
            TrafficLight.RED: 0.0
        }
    
    def compute_position_size(
        self,
        current_vol: float,
        regime_state: RegimeState,
        current_drawdown: float = 0.0
    ) -> float:
        """Compute position size with all risk controls"""
        
        # 1. Volatility targeting
        if current_vol > 0:
            vol_scalar = self.target_vol / current_vol
        else:
            vol_scalar = 1.0
        
        # 2. Regime-based cap
        regime_cap = self.regime_leverage.get(regime_state.regime, 0.5)
        
        # 3. Traffic light multiplier
        traffic_mult = self.traffic_multiplier.get(regime_state.traffic_light, 0.5)
        
        # 4. Drawdown protection
        if current_drawdown > self.max_drawdown * 0.5:
            dd_scalar = max(0, 1 - (current_drawdown - self.max_drawdown * 0.5) / (self.max_drawdown * 0.5))
        else:
            dd_scalar = 1.0
        
        # Combine all
        position_size = min(
            vol_scalar * traffic_mult * dd_scalar,
            regime_cap,
            self.max_leverage
        )
        
        return max(0, position_size)
    
    def update_value(self, daily_return: float):
        """Update portfolio value for drawdown tracking"""
        self.current_value *= (1 + daily_return)
        self.peak_value = max(self.peak_value, self.current_value)
    
    def get_current_drawdown(self) -> float:
        """Get current drawdown"""
        if self.peak_value > 0:
            return (self.peak_value - self.current_value) / self.peak_value
        return 0.0

# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO OPTIMIZER (Risk Parity + HRP)
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioOptimizer:
    """
    Portfolio optimization with:
    - Risk Parity
    - Hierarchical Risk Parity (HRP)
    """
    
    def __init__(self, method: str = 'risk_parity'):
        self.method = method
    
    def optimize(
        self,
        returns: pd.DataFrame,
        alpha_scores: pd.Series
    ) -> pd.Series:
        """Optimize portfolio weights"""
        
        if returns.empty or len(returns) < 20:
            # Equal weight fallback
            n = len(alpha_scores)
            if n > 0:
                return pd.Series(1.0 / n, index=alpha_scores.index)
            return pd.Series()
        
        # NaN 처리
        returns = returns.ffill().fillna(0)
        
        if self.method == 'risk_parity':
            return self._risk_parity(returns, alpha_scores)
        elif self.method == 'hrp':
            return self._hrp(returns, alpha_scores)
        else:
            return self._alpha_weighted(alpha_scores)
    
    def _risk_parity(self, returns: pd.DataFrame, alpha_scores: pd.Series) -> pd.Series:
        """Risk Parity optimization"""
        
        # Compute volatilities
        vols = returns.std() * np.sqrt(252)
        vols = vols.replace(0, 1e-8)
        
        # Inverse volatility weights
        inv_vol = 1.0 / vols
        
        # Alpha tilt
        alpha_rank = alpha_scores.rank(pct=True)
        
        # Combine
        weights = inv_vol * (0.5 + 0.5 * alpha_rank)
        
        # Normalize
        weights = weights / weights.sum()
        
        return weights
    
    def _hrp(self, returns: pd.DataFrame, alpha_scores: pd.Series) -> pd.Series:
        """Hierarchical Risk Parity (simplified)"""
        
        # Compute correlation matrix
        corr = returns.corr()
        
        # Fill NaN with identity
        corr = corr.fillna(0)
        np.fill_diagonal(corr.values, 1)
        
        # Distance matrix
        dist = np.sqrt(0.5 * (1 - corr))
        
        # Compute volatilities
        vols = returns.std() * np.sqrt(252)
        vols = vols.replace(0, 1e-8)
        
        # Inverse volatility weights (simplified HRP)
        weights = 1.0 / vols
        
        # Alpha tilt
        alpha_rank = alpha_scores.rank(pct=True)
        weights = weights * (0.5 + 0.5 * alpha_rank)
        
        # Normalize
        weights = weights / weights.sum()
        
        return weights
    
    def _alpha_weighted(self, alpha_scores: pd.Series) -> pd.Series:
        """Simple alpha-weighted portfolio"""
        
        # Rank-based weights
        ranks = alpha_scores.rank(pct=True)
        
        # Top 30% get weight
        weights = (ranks > 0.7).astype(float)
        
        if weights.sum() > 0:
            weights = weights / weights.sum()
        else:
            weights = pd.Series(1.0 / len(alpha_scores), index=alpha_scores.index)
        
        return weights

# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestEngine:
    """
    Backtest engine with:
    - Transaction costs
    - Slippage
    - Realistic execution
    """
    
    def __init__(
        self,
        db_path: str,
        transaction_cost_bps: float = 5.0,
        slippage_bps: float = 2.0
    ):
        self.db_path = db_path
        self.transaction_cost = transaction_cost_bps / 10000
        self.slippage = slippage_bps / 10000
        
        self.pipeline = PITDataPipeline(db_path)
        self.regime_detector = MultiSourceRegimeDetector()
        self.alpha_engine = MultiFactorAlphaEngine()
        self.risk_manager = IntegratedRiskManager()
        self.optimizer = PortfolioOptimizer(method='risk_parity')
    
    def run(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        rebalance_freq: str = 'W'
    ) -> Dict[str, Any]:
        """Run backtest"""
        
        print(f"Loading data for {len(symbols)} symbols...")
        
        # Load data
        prices = self.pipeline.load_prices(symbols, start_date, end_date)
        vix = self.pipeline.load_vix(start_date, end_date)
        
        if prices.empty:
            print("No price data available")
            return {}
        
        print(f"Data loaded: {len(prices)} days, {len(prices.columns)} symbols")
        
        # Align VIX with prices
        vix = vix.reindex(prices.index).ffill().fillna(20)
        
        # Compute features
        features = self.pipeline.compute_features(prices)
        
        # Initialize
        portfolio_value = [1.0]
        positions = pd.Series(0.0, index=prices.columns)
        daily_returns = []
        regime_history = []
        
        # Rebalance dates
        if rebalance_freq == 'W':
            rebalance_dates = prices.resample('W-FRI').last().index
        elif rebalance_freq == 'M':
            rebalance_dates = prices.resample('M').last().index
        else:
            rebalance_dates = prices.index[::5]  # Every 5 days
        
        print(f"Running backtest with {len(rebalance_dates)} rebalance dates...")
        
        # Run backtest
        for i, date in enumerate(prices.index[1:], 1):
            # Get VIX
            current_vix = vix.loc[date] if date in vix.index else 20
            
            # Get momentum for regime detection
            if i >= 21:
                momentum = (prices.iloc[i] / prices.iloc[i-21] - 1).mean()
            else:
                momentum = 0
            
            # Get vol ratio
            if i >= 60:
                vol_20 = prices.iloc[i-20:i].pct_change().std().mean()
                vol_60 = prices.iloc[i-60:i].pct_change().std().mean()
                vol_ratio = vol_20 / (vol_60 + 1e-8)
            else:
                vol_ratio = 1.0
            
            # Detect regime
            regime_state = self.regime_detector.detect(current_vix, momentum, vol_ratio)
            regime_history.append({
                'date': date,
                'regime': regime_state.regime.value,
                'traffic_light': regime_state.traffic_light.value,
                'vix': current_vix
            })
            
            # Rebalance
            if date in rebalance_dates:
                # Compute alpha
                alpha = self.alpha_engine.compute_alpha(
                    prices.iloc[:i],
                    regime_state.regime
                )
                
                # Optimize portfolio
                lookback_returns = prices.iloc[max(0, i-60):i].pct_change().dropna()
                new_weights = self.optimizer.optimize(lookback_returns, alpha)
                
                # Risk management
                current_vol = lookback_returns.std().mean() * np.sqrt(252) if len(lookback_returns) > 0 else 0.2
                position_size = self.risk_manager.compute_position_size(
                    current_vol,
                    regime_state,
                    self.risk_manager.get_current_drawdown()
                )
                
                # Apply position size
                new_positions = new_weights * position_size
                
                # Transaction costs
                turnover = (new_positions - positions).abs().sum()
                cost = turnover * (self.transaction_cost + self.slippage)
                
                positions = new_positions
            
            # Compute daily return
            daily_price_return = prices.iloc[i] / prices.iloc[i-1] - 1
            daily_price_return = daily_price_return.fillna(0)
            
            portfolio_return = (positions * daily_price_return).sum()
            
            # Apply costs on rebalance days
            if date in rebalance_dates:
                portfolio_return -= cost
            
            daily_returns.append({
                'date': date,
                'return': portfolio_return,
                'regime': regime_state.regime.value,
                'position_size': positions.sum()
            })
            
            # Update risk manager
            self.risk_manager.update_value(portfolio_return)
            
            portfolio_value.append(portfolio_value[-1] * (1 + portfolio_return))
        
        # Compute metrics
        returns_df = pd.DataFrame(daily_returns)
        returns_df.set_index('date', inplace=True)
        
        metrics = self._compute_metrics(returns_df, regime_history)
        
        return {
            'metrics': metrics,
            'returns': returns_df,
            'portfolio_value': portfolio_value,
            'regime_history': regime_history
        }
    
    def _compute_metrics(self, returns_df: pd.DataFrame, regime_history: List[Dict]) -> Dict[str, float]:
        """Compute performance metrics"""
        
        returns = returns_df['return']
        
        # Basic metrics
        total_return = (1 + returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(returns)) - 1 if len(returns) > 0 else 0
        annual_vol = returns.std() * np.sqrt(252)
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        
        # Drawdown
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min()
        
        # Win rate
        win_rate = (returns > 0).mean()
        
        # Regime breakdown
        regime_metrics = {}
        for regime in ['normal', 'crisis', 'inflation', 'tightening']:
            regime_returns = returns_df[returns_df['regime'] == regime]['return']
            if len(regime_returns) > 0:
                regime_metrics[regime] = {
                    'days': len(regime_returns),
                    'annual_return': regime_returns.mean() * 252,
                    'sharpe': regime_returns.mean() / regime_returns.std() * np.sqrt(252) if regime_returns.std() > 0 else 0
                }
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_vol': annual_vol,
            'sharpe': sharpe,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'regime_metrics': regime_metrics
        }

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Run ARES v5.0 backtest"""
    
    print("="*60)
    print("ARES Ultimate v5.0 - Multi-Factor Multi-Regime System")
    print("="*60)
    
    # Configuration
    DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    START_DATE = "2020-01-01"
    END_DATE = "2024-12-31"
    
    # Get symbols from database
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
    
    # Run backtest
    engine = BacktestEngine(DB_PATH)
    result = engine.run(symbols, START_DATE, END_DATE, rebalance_freq='W')
    
    if not result:
        print("Backtest failed")
        return
    
    # Print results
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
    
    print("\n" + "-"*60)
    print("REGIME BREAKDOWN")
    print("-"*60)
    for regime, rm in metrics['regime_metrics'].items():
        print(f"{regime.upper():12s}: {rm['days']:4d} days, "
              f"Ann.Ret: {rm['annual_return']*100:6.2f}%, "
              f"Sharpe: {rm['sharpe']:.2f}")
    
    # Save results
    returns_df = result['returns']
    returns_df.to_csv('/home/ubuntu/ares_v5_results.csv')
    
    import json
    with open('/home/ubuntu/ares_v5_metrics.json', 'w') as f:
        # Convert regime_metrics for JSON serialization
        metrics_json = {k: v for k, v in metrics.items() if k != 'regime_metrics'}
        metrics_json['regime_metrics'] = {k: dict(v) for k, v in metrics['regime_metrics'].items()}
        json.dump(metrics_json, f, indent=2)
    
    print("\n" + "="*60)
    print("Results saved to /home/ubuntu/ares_v5_results.csv")
    print("="*60)

if __name__ == "__main__":
    main()
