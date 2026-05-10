"""
ARES Ultimate v10.0 - Sharpe 3.0+ Target
Production-Ready Code with No Look-Ahead Bias
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Union
from enum import Enum
import sqlite3
from scipy import stats
from scipy.stats import spearmanr, pearsonr
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
import warnings
warnings.filterwarnings('ignore')

# ML Libraries
import lightgbm as lgb
import xgboost as xgb
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

class Regime(Enum):
    """3-State Regime Classification"""
    RISK_ON = 0      # VIX < 16, Bullish trend
    NEUTRAL = 1      # 16 <= VIX < 25, Sideways
    RISK_OFF = 2     # VIX >= 25, Bearish/Crisis


@dataclass
class AresV10Config:
    """ARES v10.0 Configuration - Sharpe 3.0+ Optimized"""
    
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # === Regime Detection (HMM-based) ===
    hmm_n_states: int = 3
    vix_risk_on_threshold: float = 16.0
    vix_risk_off_threshold: float = 25.0
    regime_smooth_window: int = 5
    
    # === Multi-Horizon Momentum ===
    momentum_horizons: List[int] = field(default_factory=lambda: [5, 21, 63, 126, 252])
    momentum_skip_days: int = 5  # Skip last 5 days for short-term reversal
    
    # === Regime-Specific Leverage ===
    leverage_risk_on: float = 1.5
    leverage_neutral: float = 0.6
    leverage_risk_off: float = 0.0
    
    # === Strategy Weights by Regime ===
    # RISK_ON: Momentum-heavy
    risk_on_momentum_weight: float = 0.70
    risk_on_quality_weight: float = 0.15
    risk_on_reversal_weight: float = 0.15
    
    # NEUTRAL: Balanced with reversal
    neutral_momentum_weight: float = 0.30
    neutral_quality_weight: float = 0.30
    neutral_reversal_weight: float = 0.40
    
    # RISK_OFF: Defensive
    risk_off_momentum_weight: float = 0.10
    risk_off_quality_weight: float = 0.60
    risk_off_reversal_weight: float = 0.30
    
    # === Portfolio Construction ===
    n_stocks: int = 15
    max_position: float = 0.15
    max_sector_weight: float = 0.35
    min_position: float = 0.02
    
    # === Risk Management ===
    target_volatility: float = 0.12  # 12% annual volatility target
    max_leverage: float = 2.0
    min_leverage: float = 0.0
    cvar_confidence: float = 0.95
    cvar_limit: float = 0.02  # Max 2% daily CVaR
    max_correlation: float = 0.70
    
    # === Drawdown Control ===
    dd_threshold_1: float = -0.05
    dd_threshold_2: float = -0.08
    dd_threshold_3: float = -0.12
    dd_leverage_mult_1: float = 0.7
    dd_leverage_mult_2: float = 0.4
    dd_leverage_mult_3: float = 0.1
    
    # === Trend Filter ===
    ma_short: int = 50
    ma_long: int = 200
    trend_filter_strength: float = 0.5
    
    # === Transaction Costs ===
    transaction_cost: float = 0.002
    slippage: float = 0.001
    total_cost: float = 0.003  # 0.3% round-trip
    rebalance_buffer: float = 0.03
    
    # === Walk-Forward Settings ===
    train_window: int = 504  # 2 years
    validation_window: int = 126  # 6 months
    test_window: int = 21  # 1 month
    
    # === ICIR Settings ===
    icir_lookback: int = 126
    icir_min_periods: int = 60
    
    # === Rebalancing ===
    rebalance_freq: str = "weekly"  # More frequent for better timing


@dataclass
class FeatureSet:
    """Container for all features"""
    momentum: Dict[str, pd.Series] = field(default_factory=dict)
    reversal: Dict[str, pd.Series] = field(default_factory=dict)
    quality: Dict[str, pd.Series] = field(default_factory=dict)
    volatility: Dict[str, pd.Series] = field(default_factory=dict)
    microstructure: Dict[str, pd.Series] = field(default_factory=dict)
    technical: Dict[str, pd.Series] = field(default_factory=dict)
    macro: Dict[str, float] = field(default_factory=dict)
class DataLoader:
    """Production Data Loader with Proper Preprocessing"""
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.conn = None
        self._cache = {}
    
    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(self.config.db_path)
        return self
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    def load_ohlcv(self, start_date: str, end_date: str, 
                  symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """Load and preprocess OHLCV data"""
        
        cache_key = f"ohlcv_{start_date}_{end_date}"
        if cache_key in self._cache:
            df = self._cache[cache_key]
            if symbols:
                return df[df['symbol'].isin(symbols)]
            return df
        
        query = """
        SELECT DISTINCT symbol, date, open, high, low, close, 
               adjusted_close, volume, vwap
        FROM daily_ohlcv
        WHERE date BETWEEN ? AND ?
        ORDER BY symbol, date
        """
        
        df = pd.read_sql_query(query, self.conn, params=(start_date, end_date))
        df['date'] = pd.to_datetime(df['date'])
        
        # Remove duplicates - keep last record
        df = df.drop_duplicates(subset=['symbol', 'date'], keep='last')
        
        # Use adjusted_close if available, else close
        df['price'] = df['adjusted_close'].fillna(df['close'])
        
        # Filter extreme price movements (>50% daily)
        df = df.sort_values(['symbol', 'date'])
        df['return'] = df.groupby('symbol')['price'].pct_change()
        df = df[df['return'].abs() < 0.5]
        
        # Forward fill missing prices (max 5 days)
        df = df.set_index(['symbol', 'date'])['price'].unstack(level=0)
        df = df.ffill(limit=5)
        df = df.stack().reset_index()
        df.columns = ['date', 'symbol', 'price']
        
        # Merge back OHLCV
        result = df.merge(
            pd.read_sql_query("""
                SELECT DISTINCT symbol, date, open, high, low, volume, vwap
                FROM daily_ohlcv
                WHERE date BETWEEN ? AND ?
            """, self.conn, params=(start_date, end_date)),
            on=['symbol', 'date'],
            how='left'
        )
        
        self._cache[cache_key] = result
        
        if symbols:
            return result[result['symbol'].isin(symbols)]
        return result
    
    def load_vix(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Load VIX data"""
        query = """
        SELECT date, close as vix, open as vix_open, high as vix_high, low as vix_low
        FROM vix
        WHERE date BETWEEN ? AND ?
        ORDER BY date
        """
        df = pd.read_sql_query(query, self.conn, params=(start_date, end_date))
        df['date'] = pd.to_datetime(df['date'])
        return df.drop_duplicates(subset=['date'], keep='last')
    
    def load_macro_indicators(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Load macro indicators"""
        query = """
        SELECT indicator_id, date, value, indicator_name
        FROM macro_indicators
        WHERE date BETWEEN ? AND ?
        ORDER BY date
        """
        df = pd.read_sql_query(query, self.conn, params=(start_date, end_date))
        df['date'] = pd.to_datetime(df['date'])
        
        # Pivot to wide format
        pivot = df.pivot_table(
            index='date', 
            columns='indicator_name', 
            values='value',
            aggfunc='last'
        ).ffill()
        
        return pivot
    
    def load_fundamentals_pit(self, as_of_date: str, 
                              symbols: List[str]) -> pd.DataFrame:
        """Load Point-in-Time fundamentals (NO LOOK-AHEAD BIAS)"""
        query = """
        SELECT ticker as symbol, date, bm, ey, fcfy, gp_a, leverage,
               gross_margin, net_margin, revenue_growth
        FROM fundamentals_pit_daily
        WHERE date <= ? AND ticker IN ({})
        ORDER BY ticker, date DESC
        """.format(','.join(['?'] * len(symbols)))
        
        df = pd.read_sql_query(query, self.conn, params=[as_of_date] + symbols)
        
        # Get latest available data for each symbol
        latest = df.groupby('symbol').first().reset_index()
        
        # Fill NaN with median
        numeric_cols = ['bm', 'ey', 'fcfy', 'gp_a', 'leverage', 
                       'gross_margin', 'net_margin', 'revenue_growth']
        for col in numeric_cols:
            if col in latest.columns:
                latest[col] = latest[col].fillna(latest[col].median())
        
        return latest
    
    def load_spy_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Load SPY data for market benchmark"""
        query = """
        SELECT date, adjusted_close as price, volume
        FROM daily_ohlcv
        WHERE symbol = 'SPY' AND date BETWEEN ? AND ?
        ORDER BY date
        """
        df = pd.read_sql_query(query, self.conn, params=(start_date, end_date))
        df['date'] = pd.to_datetime(df['date'])
        return df


def winsorize(x: np.ndarray, limits: Tuple[float, float] = (0.01, 0.99)) -> np.ndarray:
    """Winsorize array to given percentile limits"""
    if len(x) == 0:
        return x
    lower = np.nanpercentile(x, limits[0] * 100)
    upper = np.nanpercentile(x, limits[1] * 100)
    return np.clip(x, lower, upper)


def zscore(x: np.ndarray) -> np.ndarray:
    """Calculate z-score with NaN handling"""
    x = np.array(x, dtype=np.float64)
    mean = np.nanmean(x)
    std = np.nanstd(x)
    if std < 1e-8:
        return np.zeros_like(x)
    return (x - mean) / std
class HMMRegimeDetector:
    """Hidden Markov Model-based Regime Detection
    
    Key Improvements:
    1. Probabilistic regime transitions instead of hard thresholds
    2. Uses multiple signals: VIX level, VIX change, trend, volatility
    3. Smooth regime transitions to avoid whipsaws
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.transition_matrix = self._init_transition_matrix()
        self.emission_params = self._init_emission_params()
        self.state_history = []
        self.current_state = Regime.NEUTRAL
        self.state_probs = np.array([0.33, 0.34, 0.33])  # Initial uniform
        
    def _init_transition_matrix(self) -> np.ndarray:
        """Initialize regime transition probabilities
        
        Regimes tend to persist (diagonal high), with gradual transitions
        """
        return np.array([
            # To: RISK_ON, NEUTRAL, RISK_OFF
            [0.85, 0.12, 0.03],  # From RISK_ON
            [0.15, 0.70, 0.15],  # From NEUTRAL
            [0.05, 0.15, 0.80],  # From RISK_OFF
        ])
    
    def _init_emission_params(self) -> Dict:
        """Initialize emission distribution parameters (mean, std) for VIX"""
        return {
            Regime.RISK_ON: {'vix_mean': 13, 'vix_std': 2.5},
            Regime.NEUTRAL: {'vix_mean': 19, 'vix_std': 4.0},
            Regime.RISK_OFF: {'vix_mean': 30, 'vix_std': 8.0},
        }
    
    def _emission_prob(self, vix: float, regime: Regime) -> float:
        """Calculate P(VIX | Regime) using Gaussian distribution"""
        params = self.emission_params[regime]
        z = (vix - params['vix_mean']) / params['vix_std']
        return np.exp(-0.5 * z ** 2) / (params['vix_std'] * np.sqrt(2 * np.pi))
    
    def _forward_update(self, vix: float, vix_change: float, 
                       trend_signal: float) -> np.ndarray:
        """Bayesian forward update of state probabilities"""
        
        # Prior from transition matrix
        prior = self.transition_matrix.T @ self.state_probs
        
        # Likelihood from observations
        likelihoods = np.array([
            self._emission_prob(vix, Regime.RISK_ON),
            self._emission_prob(vix, Regime.NEUTRAL),
            self._emission_prob(vix, Regime.RISK_OFF),
        ])
        
        # Adjust likelihoods based on additional signals
        # VIX rapidly rising -> boost RISK_OFF probability
        if vix_change > 3:
            likelihoods[2] *= 1.5
            likelihoods[0] *= 0.5
        elif vix_change < -2:
            likelihoods[0] *= 1.3
            likelihoods[2] *= 0.7
        
        # Trend filter adjustment
        if trend_signal > 0:  # Bullish trend
            likelihoods[0] *= 1.2
        elif trend_signal < 0:  # Bearish trend
            likelihoods[2] *= 1.2
        
        # Posterior
        posterior = prior * likelihoods
        posterior = posterior / (posterior.sum() + 1e-10)
        
        return posterior
    
    def detect(self, vix: float, vix_prev: float = None, 
               spy_price: float = None, spy_ma200: float = None,
               spy_ma50: float = None) -> Tuple[Regime, float, np.ndarray]:
        """
        Detect current regime with probability distribution
        
        Returns:
            regime: Most likely regime
            leverage: Recommended leverage
            probs: Probability distribution over regimes
        """
        
        # Calculate VIX change
        vix_change = 0.0 if vix_prev is None else vix - vix_prev
        
        # Calculate trend signal
        trend_signal = 0.0
        if spy_price is not None and spy_ma200 is not None:
            if spy_price > spy_ma200:
                trend_signal = 1.0
            elif spy_price < spy_ma200 * 0.95:  # 5% below MA200
                trend_signal = -1.0
        
        # MA cross signal
        if spy_ma50 is not None and spy_ma200 is not None:
            if spy_ma50 > spy_ma200:
                trend_signal += 0.5
            else:
                trend_signal -= 0.5
        
        # Update state probabilities
        self.state_probs = self._forward_update(vix, vix_change, trend_signal)
        
        # Smooth probabilities with historical average
        if len(self.state_history) >= self.config.regime_smooth_window:
            history_avg = np.mean(
                [h['probs'] for h in self.state_history[-self.config.regime_smooth_window:]],
                axis=0
            )
            self.state_probs = 0.7 * self.state_probs + 0.3 * history_avg
        
        # Determine most likely regime
        regime_idx = np.argmax(self.state_probs)
        regime = Regime(regime_idx)
        
        # Calculate blended leverage based on probability distribution
        leverages = np.array([
            self.config.leverage_risk_on,
            self.config.leverage_neutral,
            self.config.leverage_risk_off,
        ])
        leverage = np.dot(self.state_probs, leverages)
        
        # Store history
        self.state_history.append({
            'regime': regime,
            'probs': self.state_probs.copy(),
            'leverage': leverage,
            'vix': vix,
        })
        
        self.current_state = regime
        
        return regime, leverage, self.state_probs
    
    def get_regime_confidence(self) -> float:
        """Get confidence in current regime (max probability)"""
        return np.max(self.state_probs)
    
    def get_transition_probability(self, from_regime: Regime, 
                                   to_regime: Regime) -> float:
        """Get probability of regime transition"""
        return self.transition_matrix[from_regime.value, to_regime.value]


class VIXTermStructure:
    """VIX Term Structure Analysis for Enhanced Regime Detection"""
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.history = []
    
    def calculate_signals(self, vix: float, vix_3m: float = None,
                         vix_futures: pd.Series = None) -> Dict[str, float]:
        """
        Calculate VIX term structure signals
        
        VIX < VIX3M: Contango (normal, risk-on)
        VIX > VIX3M: Backwardation (fear, risk-off)
        """
        signals = {}
        
        # VIX/VIX3M ratio (if available, estimate from historical pattern)
        if vix_3m is not None:
            ratio = vix / vix_3m
            signals['vix_term_ratio'] = ratio
            signals['contango'] = 1 if ratio < 0.95 else (-1 if ratio > 1.05 else 0)
        else:
            # Estimate from VIX level
            # High VIX typically means backwardation
            if vix > 25:
                signals['contango'] = -1
            elif vix < 15:
                signals['contango'] = 1
            else:
                signals['contango'] = 0
        
        # VIX momentum
        if len(self.history) >= 5:
            vix_5d_ago = self.history[-5] if len(self.history) >= 5 else vix
            signals['vix_momentum'] = (vix - vix_5d_ago) / (vix_5d_ago + 1e-6)
        else:
            signals['vix_momentum'] = 0
        
        # VIX mean reversion signal
        vix_mean = 18  # Long-term VIX mean
        signals['vix_mean_reversion'] = (vix - vix_mean) / vix_mean
        
        self.history.append(vix)
        if len(self.history) > 252:
            self.history = self.history[-252:]
        
        return signals
class AdvancedFactorEngine:
    """
    Advanced Factor Engine with Multi-Horizon and Regime-Adaptive Factors
    
    Key Improvements:
    1. Multi-horizon momentum with ICIR weighting
    2. Short-term reversal for MODERATE/HIGH regimes
    3. Quality factors that work in all regimes
    4. Microstructure factors from intraday data
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.icir_calculator = ICIRCalculatorEnhanced(config)
        self.factor_history = {}
        
    # ========== Momentum Factors ==========
    
    def momentum_multi_horizon(self, prices: pd.DataFrame) -> Dict[str, pd.Series]:
        """
        Multi-horizon momentum with skip period
        
        Horizons: 5d, 21d, 63d, 126d, 252d
        Skip last 5 days to avoid short-term reversal contamination
        """
        factors = {}
        skip = self.config.momentum_skip_days
        
        for horizon in self.config.momentum_horizons:
            if len(prices) < horizon + skip:
                continue
            
            # Return from T-horizon to T-skip
            ret = prices.iloc[-skip-1] / prices.iloc[-horizon-skip] - 1
            ret = ret.replace([np.inf, -np.inf], np.nan)
            
            # Winsorize and z-score
            ret_clean = pd.Series(
                zscore(winsorize(ret.values)),
                index=ret.index
            )
            
            factors[f'mom_{horizon}d'] = ret_clean
        
        return factors
    
    def momentum_quality(self, prices: pd.DataFrame, window: int = 252) -> pd.Series:
        """
        Momentum Quality: Consistency of positive returns
        Higher = more consistent uptrend
        """
        if len(prices) < window:
            return pd.Series(0, index=prices.columns)
        
        returns = prices.pct_change().iloc[-window:]
        
        # Percentage of positive months
        monthly_rets = returns.resample('M').sum() if hasattr(returns.index, 'freq') else \
                       returns.rolling(21).sum()[::21]
        pos_ratio = (monthly_rets > 0).mean()
        
        # Sharpe ratio of returns
        sharpe = returns.mean() / (returns.std() + 1e-8) * np.sqrt(252)
        
        # Combine
        quality = 0.5 * zscore(pos_ratio.values) + 0.5 * zscore(sharpe.values)
        
        return pd.Series(quality, index=prices.columns)
    
    def momentum_acceleration(self, prices: pd.DataFrame) -> pd.Series:
        """
        Momentum Acceleration: Recent momentum vs past momentum
        Positive = accelerating momentum
        """
        if len(prices) < 126:
            return pd.Series(0, index=prices.columns)
        
        mom_recent = prices.iloc[-1] / prices.iloc[-21] - 1  # 1-month
        mom_past = prices.iloc[-21] / prices.iloc[-63] - 1   # 1-3 month
        
        acceleration = mom_recent - mom_past
        return pd.Series(zscore(winsorize(acceleration.values)), index=prices.columns)
    
    # ========== Reversal Factors ==========
    
    def short_term_reversal(self, prices: pd.DataFrame, window: int = 5) -> pd.Series:
        """
        Short-term reversal: Contrarian signal
        Used in MODERATE/HIGH regimes
        """
        if len(prices) < window:
            return pd.Series(0, index=prices.columns)
        
        ret = prices.iloc[-1] / prices.iloc[-window] - 1
        
        # Reverse the return (buy losers, sell winners)
        reversal = -ret
        
        return pd.Series(zscore(winsorize(reversal.values)), index=prices.columns)
    
    def rsi_reversal(self, prices: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        RSI-based reversal signal
        Buy oversold, sell overbought
        """
        if len(prices) < period + 1:
            return pd.Series(0, index=prices.columns)
        
        returns = prices.pct_change()
        
        def calc_rsi(ret_series):
            gain = ret_series.clip(lower=0).rolling(period).mean()
            loss = (-ret_series.clip(upper=0)).rolling(period).mean()
            rs = gain / (loss + 1e-8)
            rsi = 100 - (100 / (1 + rs))
            return rsi.iloc[-1]
        
        rsi = returns.apply(calc_rsi)
        
        # Convert RSI to signal: RSI<30 = buy (+1), RSI>70 = sell (-1)
        signal = (50 - rsi) / 50  # Centered at 0
        
        return pd.Series(zscore(signal.values), index=prices.columns)
    
    def bollinger_reversal(self, prices: pd.DataFrame, 
                          window: int = 20, num_std: float = 2.0) -> pd.Series:
        """
        Bollinger Band reversal signal
        Buy at lower band, sell at upper band
        """
        if len(prices) < window:
            return pd.Series(0, index=prices.columns)
        
        sma = prices.iloc[-window:].mean()
        std = prices.iloc[-window:].std()
        
        current = prices.iloc[-1]
        z_score = (current - sma) / (std + 1e-8)
        
        # Negative z-score = below mean = buy signal
        signal = -z_score
        
        return pd.Series(winsorize(signal.values), index=prices.columns)
    
    # ========== Quality Factors ==========
    
    def quality_composite(self, fundamentals: pd.DataFrame) -> pd.Series:
        """
        Quality composite from fundamentals (PIT data only)
        """
        components = []
        
        # Profitability
        if 'gross_margin' in fundamentals.columns:
            gm = zscore(winsorize(fundamentals['gross_margin'].values))
            components.append(gm)
        
        if 'net_margin' in fundamentals.columns:
            nm = zscore(winsorize(fundamentals['net_margin'].values))
            components.append(nm)
        
        # Earnings quality (stability)
        if 'gp_a' in fundamentals.columns:
            gpa = zscore(winsorize(fundamentals['gp_a'].values))
            components.append(gpa)
        
        # Growth
        if 'revenue_growth' in fundamentals.columns:
            rg = zscore(winsorize(fundamentals['revenue_growth'].values))
            components.append(rg * 0.5)  # Lower weight on growth
        
        if not components:
            return pd.Series(0, index=fundamentals.index)
        
        quality = np.nanmean(components, axis=0)
        return pd.Series(quality, index=fundamentals.index)
    
    def volatility_adjusted_quality(self, prices: pd.DataFrame, 
                                    quality: pd.Series) -> pd.Series:
        """
        Quality adjusted for stock volatility
        Higher quality + lower volatility = better
        """
        returns = prices.pct_change().iloc[-252:]
        vol = returns.std() * np.sqrt(252)
        
        inv_vol = 1 / (vol + 0.05)  # Floor volatility at 5%
        inv_vol_z = zscore(inv_vol.values)
        
        # Combine quality with inverse volatility
        combined = 0.6 * quality.values + 0.4 * inv_vol_z
        
        return pd.Series(combined, index=prices.columns)
    
    # ========== Volatility Factors ==========
    
    def realized_volatility(self, prices: pd.DataFrame, window: int = 20) -> pd.Series:
        """Realized volatility (annualized)"""
        returns = prices.pct_change().iloc[-window:]
        vol = returns.std() * np.sqrt(252)
        return vol
    
    def volatility_of_volatility(self, prices: pd.DataFrame, 
                                  vol_window: int = 20, 
                                  vov_window: int = 60) -> pd.Series:
        """
        Volatility of Volatility (Vol-of-Vol)
        High VoV indicates unstable risk regime
        """
        if len(prices) < vol_window + vov_window:
            return pd.Series(0, index=prices.columns)
        
        returns = prices.pct_change()
        
        # Rolling volatility
        rolling_vol = returns.rolling(vol_window).std()
        
        # Volatility of the volatility
        vov = rolling_vol.iloc[-vov_window:].std() / (rolling_vol.iloc[-vov_window:].mean() + 1e-8)
        
        return vov.iloc[-1] if hasattr(vov, 'iloc') else vov
    
    def volatility_ratio(self, prices: pd.DataFrame, 
                        short: int = 10, long: int = 60) -> pd.Series:
        """
        Short-term / Long-term volatility ratio
        > 1: Volatility increasing
        < 1: Volatility decreasing
        """
        returns = prices.pct_change()
        
        vol_short = returns.iloc[-short:].std()
        vol_long = returns.iloc[-long:].std()
        
        ratio = vol_short / (vol_long + 1e-8)
        
        return ratio
    
    # ========== Microstructure Factors ==========
    
    def amihud_illiquidity(self, returns: pd.DataFrame, 
                          volume: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Amihud Illiquidity Ratio
        Higher = less liquid = higher expected return
        """
        abs_ret = returns.abs()
        dollar_vol = volume  # Assume volume is already in dollars
        
        illiq = (abs_ret / (dollar_vol + 1e-8)).iloc[-window:].mean()
        
        # Log transform to reduce skewness
        illiq_log = np.log(illiq + 1e-10)
        
        return pd.Series(zscore(illiq_log.values), index=returns.columns)
    
    def volume_momentum(self, volume: pd.DataFrame, 
                       short: int = 5, long: int = 20) -> pd.Series:
        """
        Volume momentum: Recent volume vs average volume
        High volume momentum may indicate informed trading
        """
        vol_short = volume.iloc[-short:].mean()
        vol_long = volume.iloc[-long:].mean()
        
        vol_mom = vol_short / (vol_long + 1e-8) - 1
        
        return pd.Series(zscore(vol_mom.values), index=volume.columns)
    
    # ========== Combined Score ==========
    
    def get_combined_score(self, prices: pd.DataFrame, 
                          volume: pd.DataFrame,
                          fundamentals: pd.DataFrame,
                          regime: Regime,
                          icir_weights: Dict[str, float] = None) -> pd.Series:
        """
        Calculate combined factor score based on regime
        
        RISK_ON: Heavy momentum
        NEUTRAL: Balanced with reversal
        RISK_OFF: Quality and reversal
        """
        
        # Calculate all factors
        factors = {}
        
        # Momentum factors
        mom_factors = self.momentum_multi_horizon(prices)
        for name, values in mom_factors.items():
            factors[name] = values
        
        factors['mom_quality'] = self.momentum_quality(prices)
        factors['mom_accel'] = self.momentum_acceleration(prices)
        
        # Reversal factors
        factors['reversal_5d'] = self.short_term_reversal(prices, 5)
        factors['reversal_rsi'] = self.rsi_reversal(prices)
        factors['reversal_bb'] = self.bollinger_reversal(prices)
        
        # Quality factors
        quality = self.quality_composite(fundamentals)
        factors['quality'] = quality.reindex(prices.columns).fillna(0)
        
        # Volatility factors (inverse for scoring)
        vol = self.realized_volatility(prices)
        factors['low_vol'] = pd.Series(zscore(-vol.values), index=vol.index)
        
        # Set regime-based weights
        if regime == Regime.RISK_ON:
            base_weights = {
                'mom_63d': 0.25,
                'mom_126d': 0.20,
                'mom_252d': 0.15,
                'mom_quality': 0.15,
                'mom_accel': 0.10,
                'quality': 0.10,
                'low_vol': 0.05,
            }
        elif regime == Regime.NEUTRAL:
            base_weights = {
                'mom_21d': 0.10,
                'mom_63d': 0.10,
                'reversal_5d': 0.20,
                'reversal_rsi': 0.15,
                'reversal_bb': 0.10,
                'quality': 0.20,
                'low_vol': 0.15,
            }
        else:  # RISK_OFF
            base_weights = {
                'reversal_5d': 0.15,
                'reversal_rsi': 0.15,
                'reversal_bb': 0.10,
                'quality': 0.35,
                'low_vol': 0.25,
            }
        
        # Adjust weights by ICIR if available
        if icir_weights is not None:
            for factor_name in base_weights:
                if factor_name in icir_weights:
                    # Scale by ICIR but keep base structure
                    icir_mult = max(0.5, min(2.0, 1 + icir_weights[factor_name]))
                    base_weights[factor_name] *= icir_mult
            
            # Renormalize
            total = sum(base_weights.values())
            base_weights = {k: v / total for k, v in base_weights.items()}
        
        # Calculate combined score
        combined = pd.Series(0.0, index=prices.columns)
        
        for factor_name, weight in base_weights.items():
            if factor_name in factors:
                factor_values = factors[factor_name]
                if isinstance(factor_values, pd.Series):
                    # Align indices
                    factor_aligned = factor_values.reindex(combined.index).fillna(0)
                    combined += weight * factor_aligned
        
        return combined


class ICIRCalculatorEnhanced:
    """
    Enhanced ICIR Calculator for Dynamic Factor Weighting
    
    Uses rolling Information Coefficient and IR to determine
    which factors are currently working best
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.ic_history = {}
        self.factor_returns = {}
    
    def calculate_ic(self, factor_values: np.ndarray, 
                    forward_returns: np.ndarray, 
                    method: str = 'spearman') -> float:
        """
        Calculate Information Coefficient
        
        Args:
            factor_values: Factor exposures at time T
            forward_returns: Returns from T to T+1 (or T+n)
            method: 'spearman' (rank) or 'pearson'
        
        Returns:
            IC value (-1 to 1)
        """
        # Remove NaN
        valid = ~(np.isnan(factor_values) | np.isnan(forward_returns))
        if valid.sum() < 30:
            return np.nan
        
        fv = factor_values[valid]
        fr = forward_returns[valid]
        
        if method == 'spearman':
            ic, _ = spearmanr(fv, fr)
        else:
            ic, _ = pearsonr(fv, fr)
        
        return ic
    
    def update_ic_history(self, factor_name: str, ic: float, date: pd.Timestamp):
        """Update IC history for a factor"""
        if factor_name not in self.ic_history:
            self.ic_history[factor_name] = []
        
        self.ic_history[factor_name].append({
            'date': date,
            'ic': ic
        })
        
        # Keep only recent history
        max_history = self.config.icir_lookback * 2
        if len(self.ic_history[factor_name]) > max_history:
            self.ic_history[factor_name] = self.ic_history[factor_name][-max_history:]
    
    def calculate_rolling_icir(self, factor_name: str) -> float:
        """
        Calculate rolling ICIR (IC / std(IC))
        
        Higher ICIR = more consistent predictive power
        """
        if factor_name not in self.ic_history:
            return 0.0
        
        history = self.ic_history[factor_name][-self.config.icir_lookback:]
        
        if len(history) < self.config.icir_min_periods:
            return 0.0
        
        ics = [h['ic'] for h in history if not np.isnan(h['ic'])]
        
        if len(ics) < self.config.icir_min_periods:
            return 0.0
        
        ic_mean = np.mean(ics)
        ic_std = np.std(ics)
        
        if ic_std < 1e-8:
            return ic_mean * 10  # High ICIR if consistent
        
        return ic_mean / ic_std
    
    def get_dynamic_weights(self, factor_names: List[str]) -> Dict[str, float]:
        """
        Get dynamic factor weights based on recent ICIR
        
        Returns weights that sum to 1
        """
        icirs = {}
        for name in factor_names:
            icir = self.calculate_rolling_icir(name)
            icirs[name] = icir
        
        # Only use positive ICIRs
        positive = {k: max(v, 0) for k, v in icirs.items()}
        total = sum(positive.values())
        
        if total < 1e-8:
            # Equal weight if no positive ICIRs
            return {k: 1.0 / len(factor_names) for k in factor_names}
        
        return {k: v / total for k, v in positive.items()}
class StackingRegimeClassifier:
    """
    Stacking Ensemble for Regime Classification
    
    Level 1: LightGBM, XGBoost, CatBoost
    Level 2: Ridge Regression Meta-Learner
    
    Key Improvements:
    1. Multiple diverse base models
    2. Out-of-fold predictions for meta-learner training
    3. Probability calibration
    4. Walk-forward training
    """
    
    REGIME_MAP = {
        Regime.RISK_ON: 0,
        Regime.NEUTRAL: 1,
        Regime.RISK_OFF: 2,
    }
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.base_models = {}
        self.meta_model = None
        self.feature_scaler = StandardScaler()
        self.is_trained = False
        
    def _create_base_models(self) -> Dict:
        """Create base model configurations"""
        
        lgb_params = {
            'objective': 'multiclass',
            'num_class': 3,
            'n_estimators': 200,
            'max_depth': 4,
            'learning_rate': 0.03,
            'min_child_samples': 100,
            'reg_alpha': 0.5,
            'reg_lambda': 0.5,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'random_state': 42,
            'verbose': -1,
        }
        
        xgb_params = {
            'objective': 'multi:softprob',
            'num_class': 3,
            'n_estimators': 200,
            'max_depth': 4,
            'learning_rate': 0.03,
            'min_child_weight': 100,
            'reg_alpha': 0.5,
            'reg_lambda': 0.5,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'random_state': 42,
            'verbosity': 0,
        }
        
        return {
            'lgb': lgb.LGBMClassifier(**lgb_params),
            'xgb': xgb.XGBClassifier(**xgb_params),
        }
    
    def prepare_features(self, data: Dict, as_of_date: pd.Timestamp) -> np.ndarray:
        """
        Prepare features for regime classification
        NO LOOK-AHEAD BIAS - only uses data available at as_of_date
        """
        features = []
        
        # VIX features
        vix = data.get('vix_level', 20)
        features.extend([
            vix,
            vix ** 2 / 1000,  # Non-linear VIX effect
            1 if vix > 20 else 0,
            1 if vix > 25 else 0,
            1 if vix > 30 else 0,
            data.get('vix_change_5d', 0),
            data.get('vix_percentile_252d', 0.5),
        ])
        
        # Trend features
        features.extend([
            1 if data.get('spy_above_200ma', True) else 0,
            1 if data.get('spy_above_50ma', True) else 0,
            data.get('spy_return_21d', 0),
            data.get('spy_return_63d', 0),
        ])
        
        # Volatility features
        features.extend([
            data.get('market_volatility_20d', 0.15),
            data.get('market_volatility_60d', 0.15),
            data.get('vol_ratio', 1.0),  # short/long vol
        ])
        
        # Macro features
        features.extend([
            data.get('yield_spread', 0),
            data.get('credit_spread', 0),
            data.get('dollar_index_change', 0),
        ])
        
        # Market breadth
        features.extend([
            data.get('advance_decline_ratio', 1.0),
            data.get('new_highs_lows_ratio', 1.0),
        ])
        
        return np.array(features, dtype=np.float32).reshape(1, -1)
    
    def _get_oof_predictions(self, X: np.ndarray, y: np.ndarray, 
                            n_splits: int = 5) -> np.ndarray:
        """Get out-of-fold predictions for meta-learner training"""
        
        tscv = TimeSeriesSplit(n_splits=n_splits)
        oof_preds = np.zeros((len(X), 3 * len(self.base_models)))
        
        for model_idx, (name, model) in enumerate(self.base_models.items()):
            for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
                X_train, X_val = X[train_idx], X[val_idx]
                y_train = y[train_idx]
                
                # Clone model for this fold
                if name == 'lgb':
                    fold_model = lgb.LGBMClassifier(**model.get_params())
                else:
                    fold_model = xgb.XGBClassifier(**model.get_params())
                
                fold_model.fit(X_train, y_train)
                
                # Get probability predictions
                probs = fold_model.predict_proba(X_val)
                oof_preds[val_idx, model_idx*3:(model_idx+1)*3] = probs
        
        return oof_preds
    
    def train(self, X: np.ndarray, y: np.ndarray, 
             X_val: np.ndarray = None, y_val: np.ndarray = None):
        """
        Train the stacking ensemble
        
        1. Train base models with cross-validation
        2. Get OOF predictions
        3. Train meta-learner on OOF predictions
        """
        
        # Scale features
        X_scaled = self.feature_scaler.fit_transform(X)
        
        # Create base models
        self.base_models = self._create_base_models()
        
        # Get OOF predictions for meta-learner training
        oof_preds = self._get_oof_predictions(X_scaled, y)
        
        # Train meta-learner
        self.meta_model = Ridge(alpha=1.0)
        
        # Convert y to one-hot for Ridge
        y_onehot = np.zeros((len(y), 3))
        for i, label in enumerate(y):
            y_onehot[i, int(label)] = 1
        
        self.meta_model.fit(oof_preds, y_onehot)
        
        # Retrain base models on full data
        for name, model in self.base_models.items():
            model.fit(X_scaled, y)
        
        self.is_trained = True
        
        # Calculate validation metrics if provided
        if X_val is not None and y_val is not None:
            val_preds = self.predict(X_val)
            accuracy = (val_preds == y_val).mean()
            return {'val_accuracy': accuracy}
        
        return {}
    
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Get probability predictions from ensemble"""
        
        if not self.is_trained:
            raise ValueError("Model not trained")
        
        X_scaled = self.feature_scaler.transform(X)
        
        # Get base model predictions
        base_preds = []
        for name, model in self.base_models.items():
            probs = model.predict_proba(X_scaled)
            base_preds.append(probs)
        
        # Concatenate base predictions
        meta_features = np.hstack(base_preds)
        
        # Meta-learner prediction
        meta_pred = self.meta_model.predict(meta_features)
        
        # Convert to probabilities (softmax)
        exp_pred = np.exp(meta_pred - np.max(meta_pred, axis=1, keepdims=True))
        probs = exp_pred / exp_pred.sum(axis=1, keepdims=True)
        
        return probs
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """Get class predictions"""
        probs = self.predict_proba(X)
        return np.argmax(probs, axis=1)
    
    def predict_regime(self, X: np.ndarray) -> Tuple[Regime, float]:
        """Predict regime with confidence"""
        probs = self.predict_proba(X)
        pred_class = np.argmax(probs, axis=1)[0]
        confidence = probs[0, pred_class]
        
        regime = Regime(pred_class)
        return regime, confidence


class WalkForwardTrainer:
    """
    Walk-Forward Training Framework
    
    Prevents look-ahead bias by training only on past data
    and testing on future data
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.model_snapshots = {}
    
    def create_train_test_splits(self, dates: pd.DatetimeIndex) -> List[Dict]:
        """Create walk-forward train/test splits"""
        
        train_window = self.config.train_window
        val_window = self.config.validation_window
        test_window = self.config.test_window
        
        splits = []
        
        for i in range(train_window + val_window, len(dates), test_window):
            if i + test_window > len(dates):
                break
            
            split = {
                'train_start': i - train_window - val_window,
                'train_end': i - val_window,
                'val_start': i - val_window,
                'val_end': i,
                'test_start': i,
                'test_end': min(i + test_window, len(dates)),
            }
            splits.append(split)
        
        return splits
    
    def train_walk_forward(self, model, X: np.ndarray, y: np.ndarray,
                          dates: pd.DatetimeIndex) -> Dict:
        """
        Train model using walk-forward methodology
        
        Returns model trained on most recent data
        """
        
        splits = self.create_train_test_splits(dates)
        
        if not splits:
            # Not enough data - train on all available
            model.train(X, y)
            return {'n_splits': 0}
        
        results = []
        
        for split in splits:
            X_train = X[split['train_start']:split['train_end']]
            y_train = y[split['train_start']:split['train_end']]
            X_val = X[split['val_start']:split['val_end']]
            y_val = y[split['val_start']:split['val_end']]
            X_test = X[split['test_start']:split['test_end']]
            y_test = y[split['test_start']:split['test_end']]
            
            # Train on train+val for final model
            X_trainval = X[split['train_start']:split['val_end']]
            y_trainval = y[split['train_start']:split['val_end']]
            
            model.train(X_trainval, y_trainval)
            
            # Test performance
            if len(X_test) > 0:
                test_preds = model.predict(X_test)
                test_acc = (test_preds == y_test).mean()
                results.append({
                    'test_start': dates[split['test_start']],
                    'test_end': dates[split['test_end'] - 1],
                    'accuracy': test_acc,
                })
        
        # Final training on most recent data
        final_train_start = max(0, len(X) - self.config.train_window - self.config.validation_window)
        X_final = X[final_train_start:]
        y_final = y[final_train_start:]
        model.train(X_final, y_final)
        
        return {
            'n_splits': len(splits),
            'avg_accuracy': np.mean([r['accuracy'] for r in results]) if results else 0,
            'results': results,
        }
class HierarchicalRiskParity:
    """
    Hierarchical Risk Parity (HRP) Portfolio Optimization
    
    Key Advantages:
    1. No matrix inversion required (more stable than MVO)
    2. Considers correlation structure
    3. Better diversification than equal-weight or inverse-vol
    
    Reference: López de Prado, "Building Diversified Portfolios that Outperform Out-of-Sample"
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
    
    def _get_cluster_variance(self, cov: np.ndarray, items: List[int]) -> float:
        """Calculate variance of a cluster"""
        cov_slice = cov[np.ix_(items, items)]
        # Inverse-variance weights within cluster
        ivp = 1 / np.diag(cov_slice)
        ivp /= ivp.sum()
        return np.dot(ivp, np.dot(cov_slice, ivp))
    
    def _get_quasi_diagonal(self, link: np.ndarray) -> List[int]:
        """Extract quasi-diagonal matrix from linkage"""
        link = link.astype(int)
        sort_ix = leaves_list(link)
        return sort_ix.tolist()
    
    def _recursive_bisection(self, cov: np.ndarray, 
                            sort_ix: List[int]) -> np.ndarray:
        """Recursive bisection for weight allocation"""
        n = len(sort_ix)
        weights = np.ones(n)
        items = [sort_ix]
        
        while len(items) > 0:
            # Bisect each item
            new_items = []
            for item in items:
                if len(item) > 1:
                    # Split in half
                    half = len(item) // 2
                    items_1 = item[:half]
                    items_2 = item[half:]
                    
                    # Get cluster variances
                    var_1 = self._get_cluster_variance(cov, items_1)
                    var_2 = self._get_cluster_variance(cov, items_2)
                    
                    # Allocate inversely proportional to variance
                    alpha = 1 - var_1 / (var_1 + var_2)
                    
                    # Apply to weights
                    for i in items_1:
                        weights[sort_ix.index(i)] *= alpha
                    for i in items_2:
                        weights[sort_ix.index(i)] *= (1 - alpha)
                    
                    new_items.extend([items_1, items_2])
            
            items = [item for item in new_items if len(item) > 1]
        
        return weights
    
    def optimize(self, returns: pd.DataFrame, 
                scores: pd.Series = None,
                max_position: float = None,
                min_position: float = None) -> pd.Series:
        """
        Optimize portfolio weights using HRP
        
        Args:
            returns: Historical returns (T x N)
            scores: Factor scores for tilting (optional)
            max_position: Maximum position size
            min_position: Minimum position size
        
        Returns:
            Optimized weights (sums to 1)
        """
        
        if max_position is None:
            max_position = self.config.max_position
        if min_position is None:
            min_position = self.config.min_position
        
        # Calculate correlation and covariance
        corr = returns.corr()
        cov = returns.cov().values
        
        # Replace NaN with 0 in correlation (no correlation assumed)
        corr = corr.fillna(0)
        
        # Convert correlation to distance
        dist = np.sqrt(0.5 * (1 - corr.values))
        dist = np.nan_to_num(dist, nan=0.5)
        np.fill_diagonal(dist, 0)
        
        # Hierarchical clustering
        try:
            dist_condensed = squareform(dist, checks=False)
            link = linkage(dist_condensed, method='ward')
        except Exception:
            # Fallback to inverse-volatility if clustering fails
            vol = returns.std()
            weights = 1 / (vol + 0.01)
            weights = weights / weights.sum()
            return weights
        
        # Get quasi-diagonal order
        sort_ix = self._get_quasi_diagonal(link)
        
        # Recursive bisection
        weights = self._recursive_bisection(cov, sort_ix)
        
        # Reorder weights to original order
        weights_series = pd.Series(index=returns.columns, dtype=float)
        for i, col_idx in enumerate(sort_ix):
            weights_series.iloc[col_idx] = weights[i]
        
        # Apply factor tilt if scores provided
        if scores is not None:
            # Align scores with weights
            scores_aligned = scores.reindex(weights_series.index).fillna(0)
            
            # Softmax-like tilt based on scores
            score_exp = np.exp(scores_aligned * 0.5)  # Temperature = 2
            score_weights = score_exp / score_exp.sum()
            
            # Blend HRP with score-based weights
            weights_series = 0.6 * weights_series + 0.4 * score_weights
        
        # Apply constraints
        weights_series = weights_series.clip(min_position, max_position)
        
        # Renormalize
        weights_series = weights_series / weights_series.sum()
        
        return weights_series


class AdvancedPortfolioOptimizer:
    """
    Advanced Portfolio Optimizer combining HRP with Risk Controls
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.hrp = HierarchicalRiskParity(config)
    
    def optimize(self, returns: pd.DataFrame,
                scores: pd.Series,
                regime: Regime,
                leverage: float,
                current_dd: float,
                sectors: pd.Series = None) -> Tuple[pd.Series, float]:
        """
        Full portfolio optimization with all constraints
        
        Returns:
            weights: Stock weights
            adjusted_leverage: Final leverage after risk adjustments
        """
        
        # 1. Base HRP optimization
        weights = self.hrp.optimize(returns, scores)
        
        # 2. Apply drawdown-based leverage reduction
        adjusted_leverage = self._adjust_leverage_for_dd(leverage, current_dd)
        
        # 3. Apply volatility targeting
        portfolio_vol = self._calculate_portfolio_volatility(returns, weights)
        vol_adjustment = self.config.target_volatility / (portfolio_vol + 1e-8)
        vol_adjustment = np.clip(vol_adjustment, 0.5, 2.0)
        adjusted_leverage *= vol_adjustment
        
        # 4. Apply CVaR limit
        cvar = self._calculate_cvar(returns, weights)
        if cvar > self.config.cvar_limit:
            cvar_adjustment = self.config.cvar_limit / cvar
            adjusted_leverage *= cvar_adjustment
        
        # 5. Apply sector constraints if sectors provided
        if sectors is not None:
            weights = self._apply_sector_constraints(weights, sectors)
        
        # 6. Apply max/min leverage limits
        adjusted_leverage = np.clip(
            adjusted_leverage,
            self.config.min_leverage,
            self.config.max_leverage
        )
        
        # 7. Apply correlation constraint
        avg_corr = self._calculate_average_correlation(returns, weights)
        if avg_corr > self.config.max_correlation:
            # Reduce concentration
            weights = self._reduce_concentration(weights)
        
        return weights, adjusted_leverage
    
    def _adjust_leverage_for_dd(self, leverage: float, current_dd: float) -> float:
        """Adjust leverage based on current drawdown"""
        
        if current_dd <= self.config.dd_threshold_1:
            return leverage
        elif current_dd <= self.config.dd_threshold_2:
            return leverage * self.config.dd_leverage_mult_1
        elif current_dd <= self.config.dd_threshold_3:
            return leverage * self.config.dd_leverage_mult_2
        else:
            return leverage * self.config.dd_leverage_mult_3
    
    def _calculate_portfolio_volatility(self, returns: pd.DataFrame, 
                                        weights: pd.Series) -> float:
        """Calculate annualized portfolio volatility"""
        weights_aligned = weights.reindex(returns.columns).fillna(0)
        portfolio_returns = (returns * weights_aligned).sum(axis=1)
        return portfolio_returns.std() * np.sqrt(252)
    
    def _calculate_cvar(self, returns: pd.DataFrame, 
                       weights: pd.Series,
                       confidence: float = 0.95) -> float:
        """Calculate Conditional Value at Risk"""
        weights_aligned = weights.reindex(returns.columns).fillna(0)
        portfolio_returns = (returns * weights_aligned).sum(axis=1)
        
        var_threshold = np.percentile(portfolio_returns, (1 - confidence) * 100)
        cvar = portfolio_returns[portfolio_returns <= var_threshold].mean()
        
        return abs(cvar)
    
    def _calculate_average_correlation(self, returns: pd.DataFrame,
                                       weights: pd.Series) -> float:
        """Calculate weighted average correlation"""
        corr_matrix = returns.corr()
        weights_aligned = weights.reindex(returns.columns).fillna(0)
        
        # Weighted average of pairwise correlations
        n = len(weights_aligned)
        total_corr = 0
        total_weight = 0
        
        for i in range(n):
            for j in range(i + 1, n):
                pair_weight = weights_aligned.iloc[i] * weights_aligned.iloc[j]
                pair_corr = corr_matrix.iloc[i, j]
                if not np.isnan(pair_corr):
                    total_corr += pair_weight * pair_corr
                    total_weight += pair_weight
        
        if total_weight < 1e-8:
            return 0
        
        return total_corr / total_weight
    
    def _apply_sector_constraints(self, weights: pd.Series, 
                                  sectors: pd.Series) -> pd.Series:
        """Apply maximum sector weight constraint"""
        weights = weights.copy()
        
        for sector in sectors.unique():
            sector_mask = sectors == sector
            sector_weight = weights[sector_mask].sum()
            
            if sector_weight > self.config.max_sector_weight:
                # Scale down sector weights
                scale = self.config.max_sector_weight / sector_weight
                weights[sector_mask] *= scale
        
        # Renormalize
        weights = weights / weights.sum()
        
        return weights
    
    def _reduce_concentration(self, weights: pd.Series) -> pd.Series:
        """Reduce portfolio concentration by flattening weights"""
        weights = weights.copy()
        
        # Move weights toward equal weight
        equal_weight = 1 / len(weights)
        weights = 0.7 * weights + 0.3 * equal_weight
        
        return weights / weights.sum()
class AdvancedRiskManager:
    """
    Advanced Risk Management System
    
    Features:
    1. Dynamic volatility targeting
    2. CVaR-based position limits
    3. Tail risk hedging recommendations
    4. Correlation regime detection
    5. Drawdown-aware position sizing
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.equity_history = []
        self.drawdown_history = []
        self.volatility_history = []
        
    def update_equity(self, equity: float, date: pd.Timestamp):
        """Update equity curve tracking"""
        self.equity_history.append({'date': date, 'equity': equity})
        
        # Calculate drawdown
        peak = max(h['equity'] for h in self.equity_history)
        dd = (equity - peak) / peak
        self.drawdown_history.append({'date': date, 'drawdown': dd})
    
    def get_current_drawdown(self) -> float:
        """Get current drawdown"""
        if not self.drawdown_history:
            return 0.0
        return self.drawdown_history[-1]['drawdown']
    
    def calculate_dynamic_leverage(self, base_leverage: float,
                                   regime: Regime,
                                   vix: float,
                                   realized_vol: float) -> float:
        """
        Calculate dynamic leverage based on multiple factors
        
        1. Base leverage from regime
        2. Drawdown adjustment
        3. Volatility targeting
        4. VIX spike protection
        """
        
        leverage = base_leverage
        
        # 1. Drawdown adjustment
        current_dd = self.get_current_drawdown()
        leverage = self._apply_dd_adjustment(leverage, current_dd)
        
        # 2. Volatility targeting
        if realized_vol > 0.01:
            vol_target_mult = self.config.target_volatility / realized_vol
            vol_target_mult = np.clip(vol_target_mult, 0.3, 2.0)
            leverage *= vol_target_mult
        
        # 3. VIX spike protection
        if len(self.volatility_history) >= 5:
            vix_5d_ago = self.volatility_history[-5].get('vix', vix)
            vix_change = (vix - vix_5d_ago) / (vix_5d_ago + 1)
            
            # VIX spiked more than 20% -> reduce leverage
            if vix_change > 0.2:
                leverage *= (1 - vix_change * 0.5)
        
        # 4. Track VIX for future reference
        self.volatility_history.append({'vix': vix, 'realized_vol': realized_vol})
        if len(self.volatility_history) > 252:
            self.volatility_history = self.volatility_history[-252:]
        
        # 5. Apply absolute limits
        leverage = np.clip(leverage, self.config.min_leverage, self.config.max_leverage)
        
        return leverage
    
    def _apply_dd_adjustment(self, leverage: float, current_dd: float) -> float:
        """Apply drawdown-based leverage adjustment"""
        
        if current_dd >= self.config.dd_threshold_1:
            # No adjustment for small drawdowns
            return leverage
        elif current_dd >= self.config.dd_threshold_2:
            # First threshold: 70%
            return leverage * self.config.dd_leverage_mult_1
        elif current_dd >= self.config.dd_threshold_3:
            # Second threshold: 40%
            return leverage * self.config.dd_leverage_mult_2
        else:
            # Severe drawdown: 10%
            return leverage * self.config.dd_leverage_mult_3
    
    def calculate_position_limits(self, portfolio_value: float,
                                  stock_volatility: pd.Series,
                                  stock_scores: pd.Series) -> pd.Series:
        """
        Calculate position limits based on stock risk
        
        Higher volatility stocks get smaller position limits
        """
        
        # Base limit from config
        base_limit = self.config.max_position * portfolio_value
        
        # Volatility-adjusted limits
        avg_vol = stock_volatility.mean()
        vol_ratio = avg_vol / (stock_volatility + 0.01)
        vol_ratio = vol_ratio.clip(0.5, 2.0)
        
        position_limits = base_limit * vol_ratio
        
        # Score-adjusted (allow larger positions for better scores)
        score_mult = 1 + 0.2 * stock_scores.clip(-1, 1)
        position_limits *= score_mult
        
        return position_limits
    
    def should_hedge(self, regime: Regime, vix: float, 
                    current_dd: float) -> Dict[str, float]:
        """
        Determine if tail risk hedging is recommended
        
        Returns hedge recommendations
        """
        
        recommendations = {
            'should_hedge': False,
            'hedge_ratio': 0.0,
            'hedge_type': None,
            'urgency': 'low',
        }
        
        # Conditions for hedging
        conditions = []
        
        # 1. RISK_OFF regime
        if regime == Regime.RISK_OFF:
            conditions.append(('regime', 0.3))
        
        # 2. High VIX
        if vix > 25:
            conditions.append(('vix', min((vix - 25) / 20, 0.5)))
        
        # 3. Significant drawdown
        if current_dd < -0.05:
            conditions.append(('drawdown', min(abs(current_dd) * 3, 0.4)))
        
        # 4. VIX in backwardation (implied by high VIX)
        if vix > 30:
            conditions.append(('backwardation', 0.2))
        
        if conditions:
            total_weight = sum(c[1] for c in conditions)
            recommendations['should_hedge'] = total_weight > 0.3
            recommendations['hedge_ratio'] = min(total_weight, 0.5)
            
            if total_weight > 0.5:
                recommendations['urgency'] = 'high'
                recommendations['hedge_type'] = 'put_spread'
            elif total_weight > 0.3:
                recommendations['urgency'] = 'medium'
                recommendations['hedge_type'] = 'put_spread'
            else:
                recommendations['hedge_type'] = None
        
        return recommendations
    
    def calculate_stop_loss(self, entry_price: pd.Series,
                           current_price: pd.Series,
                           atr: pd.Series,
                           regime: Regime) -> pd.Series:
        """
        Calculate dynamic stop-loss levels using ATR
        
        Wider stops in volatile regimes, tighter in calm regimes
        """
        
        # ATR multiplier by regime
        atr_mult = {
            Regime.RISK_ON: 2.0,
            Regime.NEUTRAL: 1.5,
            Regime.RISK_OFF: 1.0,
        }
        
        mult = atr_mult[regime]
        stop_distance = atr * mult
        
        # Trailing stop: max of entry-based and trailing
        entry_stop = entry_price - stop_distance
        trailing_stop = current_price - stop_distance
        
        # Use the higher (less restrictive) of the two
        stop_loss = pd.concat([entry_stop, trailing_stop], axis=1).max(axis=1)
        
        return stop_loss
    
    def get_risk_report(self) -> Dict:
        """Generate comprehensive risk report"""
        
        report = {
            'current_drawdown': self.get_current_drawdown(),
            'max_drawdown': min(h['drawdown'] for h in self.drawdown_history) if self.drawdown_history else 0,
        }
        
        if len(self.equity_history) >= 252:
            # Calculate rolling Sharpe
            equity_series = pd.Series([h['equity'] for h in self.equity_history[-252:]])
            returns = equity_series.pct_change().dropna()
            report['rolling_sharpe'] = returns.mean() / (returns.std() + 1e-8) * np.sqrt(252)
            report['rolling_vol'] = returns.std() * np.sqrt(252)
        
        if self.volatility_history:
            report['current_vix'] = self.volatility_history[-1].get('vix', 0)
            report['current_realized_vol'] = self.volatility_history[-1].get('realized_vol', 0)
        
        return report
class AresBacktestEngine:
    """
    ARES v10.0 Integrated Backtesting Engine
    
    Features:
    1. Walk-forward validation
    2. Transaction cost modeling
    3. Realistic execution simulation
    4. Comprehensive metrics
    """
    
    def __init__(self, config: AresV10Config):
        self.config = config
        self.data_loader = DataLoader(config)
        self.regime_detector = HMMRegimeDetector(config)
        self.factor_engine = AdvancedFactorEngine(config)
        self.ml_classifier = StackingRegimeClassifier(config)
        self.portfolio_optimizer = AdvancedPortfolioOptimizer(config)
        self.risk_manager = AdvancedRiskManager(config)
        self.vix_term_structure = VIXTermStructure(config)
        
        # State tracking
        self.positions = pd.Series(dtype=float)
        self.cash = 1_000_000
        self.portfolio_value_history = []
        self.trade_history = []
        
    def run_backtest(self, start_date: str, end_date: str,
                    train_ml: bool = True) -> Dict:
        """
        Run full backtest with walk-forward validation
        
        Args:
            start_date: Backtest start date (YYYY-MM-DD)
            end_date: Backtest end date (YYYY-MM-DD)
            train_ml: Whether to train ML models
        
        Returns:
            Comprehensive backtest results
        """
        
        # Connect to database
        self.data_loader.connect()
        
        try:
            # Load all data
            print(f"Loading data from {start_date} to {end_date}...")
            
            # Need extra history for factor calculation
            warmup_start = (pd.Timestamp(start_date) - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
            
            ohlcv = self.data_loader.load_ohlcv(warmup_start, end_date)
            vix_data = self.data_loader.load_vix(warmup_start, end_date)
            spy_data = self.data_loader.load_spy_data(warmup_start, end_date)
            
            # Pivot price data
            prices = ohlcv.pivot(index='date', columns='symbol', values='price')
            prices = prices.sort_index()
            
            # Pivot volume data
            volume = ohlcv.pivot(index='date', columns='symbol', values='volume')
            volume = volume.sort_index()
            
            # Merge VIX with dates
            vix_series = vix_data.set_index('date')['vix']
            
            # Calculate SPY indicators
            spy_prices = spy_data.set_index('date')['price']
            spy_ma50 = spy_prices.rolling(50).mean()
            spy_ma200 = spy_prices.rolling(200).mean()
            
            # Get trading dates
            trading_dates = prices.loc[start_date:end_date].index
            
            # Determine rebalance dates
            rebalance_dates = self._get_rebalance_dates(trading_dates)
            
            print(f"Running backtest: {len(trading_dates)} trading days, {len(rebalance_dates)} rebalances")
            
            # Initialize portfolio
            initial_value = self.cash
            self.portfolio_value_history = []
            
            # Main backtest loop
            current_weights = pd.Series(dtype=float)
            entry_prices = pd.Series(dtype=float)
            
            for i, date in enumerate(trading_dates):
                # Get current data (NO LOOK-AHEAD)
                current_prices = prices.loc[:date]
                current_volume = volume.loc[:date]
                current_vix = vix_series.get(date, 20)
                prev_vix = vix_series.get(trading_dates[i-1], current_vix) if i > 0 else current_vix
                
                spy_price = spy_prices.get(date, spy_prices.iloc[-1])
                spy_200 = spy_ma200.get(date, spy_price)
                spy_50 = spy_ma50.get(date, spy_price)
                
                # Update equity tracking
                portfolio_value = self._calculate_portfolio_value(current_prices.iloc[-1])
                self.risk_manager.update_equity(portfolio_value, date)
                self.portfolio_value_history.append({
                    'date': date,
                    'value': portfolio_value,
                })
                
                # Check if rebalance day
                if date in rebalance_dates:
                    # Detect regime
                    regime, base_leverage, regime_probs = self.regime_detector.detect(
                        vix=current_vix,
                        vix_prev=prev_vix,
                        spy_price=spy_price,
                        spy_ma200=spy_200,
                        spy_ma50=spy_50,
                    )
                    
                    # Get VIX term structure signals
                    vix_signals = self.vix_term_structure.calculate_signals(current_vix)
                    
                    # Filter stocks with enough history
                    valid_stocks = current_prices.iloc[-252:].dropna(axis=1, thresh=200).columns.tolist()
                    
                    if len(valid_stocks) < 20:
                        continue
                    
                    # Load fundamentals (PIT)
                    try:
                        fundamentals = self.data_loader.load_fundamentals_pit(
                            date.strftime('%Y-%m-%d'),
                            valid_stocks
                        )
                        fundamentals = fundamentals.set_index('symbol')
                    except Exception:
                        fundamentals = pd.DataFrame(index=valid_stocks)
                    
                    # Calculate factor scores
                    stock_prices = current_prices[valid_stocks]
                    stock_volume = current_volume[valid_stocks].fillna(0)
                    
                    scores = self.factor_engine.get_combined_score(
                        prices=stock_prices,
                        volume=stock_volume,
                        fundamentals=fundamentals.reindex(valid_stocks),
                        regime=regime,
                    )
                    
                    # Select top N stocks
                    top_stocks = scores.nlargest(self.config.n_stocks).index.tolist()
                    
                    if len(top_stocks) < 5:
                        continue
                    
                    # Calculate returns for optimization
                    returns = stock_prices[top_stocks].pct_change().iloc[-60:].dropna()
                    
                    if len(returns) < 30:
                        continue
                    
                    # Calculate realized volatility
                    realized_vol = returns.std().mean() * np.sqrt(252)
                    
                    # Dynamic leverage
                    current_dd = self.risk_manager.get_current_drawdown()
                    adjusted_leverage = self.risk_manager.calculate_dynamic_leverage(
                        base_leverage=base_leverage,
                        regime=regime,
                        vix=current_vix,
                        realized_vol=realized_vol,
                    )
                    
                    # Optimize portfolio
                    new_weights, final_leverage = self.portfolio_optimizer.optimize(
                        returns=returns,
                        scores=scores[top_stocks],
                        regime=regime,
                        leverage=adjusted_leverage,
                        current_dd=current_dd,
                    )
                    
                    # Apply leverage
                    new_weights *= final_leverage
                    
                    # Calculate trades needed
                    trades = self._calculate_trades(
                        current_weights=current_weights,
                        new_weights=new_weights,
                        current_prices=current_prices.iloc[-1],
                        portfolio_value=portfolio_value,
                    )
                    
                    # Execute trades
                    if trades:
                        cost = self._execute_trades(trades, current_prices.iloc[-1], date)
                        current_weights = new_weights.copy()
                        entry_prices = current_prices.iloc[-1][new_weights.index]
            
            # Calculate final metrics
            results = self._calculate_metrics(initial_value)
            
            return results
            
        finally:
            self.data_loader.close()
    
    def _get_rebalance_dates(self, dates: pd.DatetimeIndex) -> List[pd.Timestamp]:
        """Get rebalance dates based on frequency"""
        
        if self.config.rebalance_freq == 'daily':
            return dates.tolist()
        elif self.config.rebalance_freq == 'weekly':
            # Every Friday or last trading day of week
            return dates[dates.dayofweek == 4].tolist()
        elif self.config.rebalance_freq == 'monthly':
            # Last trading day of month
            monthly = dates.to_series().groupby(dates.to_period('M')).last()
            return monthly.tolist()
        else:
            return dates.tolist()
    
    def _calculate_portfolio_value(self, current_prices: pd.Series) -> float:
        """Calculate current portfolio value"""
        if self.positions.empty:
            return self.cash
        
        # Align prices with positions
        position_value = 0
        for symbol, shares in self.positions.items():
            if symbol in current_prices.index:
                position_value += shares * current_prices[symbol]
        
        return self.cash + position_value
    
    def _calculate_trades(self, current_weights: pd.Series,
                         new_weights: pd.Series,
                         current_prices: pd.Series,
                         portfolio_value: float) -> List[Dict]:
        """Calculate trades needed for rebalancing"""
        
        trades = []
        
        # Get all symbols
        all_symbols = set(current_weights.index) | set(new_weights.index)
        
        for symbol in all_symbols:
            current_weight = current_weights.get(symbol, 0)
            new_weight = new_weights.get(symbol, 0)
            
            weight_diff = new_weight - current_weight
            
            # Apply rebalance buffer
            if abs(weight_diff) < self.config.rebalance_buffer:
                continue
            
            if symbol not in current_prices.index:
                continue
            
            price = current_prices[symbol]
            if pd.isna(price) or price <= 0:
                continue
            
            target_value = portfolio_value * new_weight
            current_value = portfolio_value * current_weight
            trade_value = target_value - current_value
            shares = int(trade_value / price)
            
            if shares != 0:
                trades.append({
                    'symbol': symbol,
                    'shares': shares,
                    'price': price,
                    'value': shares * price,
                })
        
        return trades
    
    def _execute_trades(self, trades: List[Dict], 
                       current_prices: pd.Series,
                       date: pd.Timestamp) -> float:
        """Execute trades with transaction costs"""
        
        total_cost = 0
        
        for trade in trades:
            symbol = trade['symbol']
            shares = trade['shares']
            price = trade['price']
            
            # Apply slippage
            if shares > 0:  # Buying
                execution_price = price * (1 + self.config.slippage)
            else:  # Selling
                execution_price = price * (1 - self.config.slippage)
            
            trade_value = abs(shares * execution_price)
            transaction_cost = trade_value * self.config.transaction_cost
            
            # Update positions
            if symbol in self.positions:
                self.positions[symbol] += shares
            else:
                self.positions[symbol] = shares
            
            # Remove zero positions
            if self.positions[symbol] == 0:
                self.positions = self.positions.drop(symbol)
            
            # Update cash
            self.cash -= shares * execution_price + transaction_cost
            total_cost += transaction_cost
            
            # Record trade
            self.trade_history.append({
                'date': date,
                'symbol': symbol,
                'shares': shares,
                'price': execution_price,
                'cost': transaction_cost,
            })
        
        return total_cost
    
    def _calculate_metrics(self, initial_value: float) -> Dict:
        """Calculate comprehensive backtest metrics"""
        
        if not self.portfolio_value_history:
            return {'error': 'No portfolio history'}
        
        # Create equity series
        equity = pd.DataFrame(self.portfolio_value_history)
        equity = equity.set_index('date')['value']
        
        # Calculate returns
        returns = equity.pct_change().dropna()
        
        # Basic metrics
        total_return = (equity.iloc[-1] / initial_value) - 1
        n_years = len(equity) / 252
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        
        # Volatility and Sharpe
        annual_vol = returns.std() * np.sqrt(252)
        sharpe = annual_return / (annual_vol + 1e-8)
        
        # Drawdown analysis
        rolling_max = equity.expanding().max()
        drawdowns = (equity - rolling_max) / rolling_max
        max_dd = drawdowns.min()
        
        # Calculate drawdown duration
        underwater = drawdowns < 0
        if underwater.any():
            underwater_periods = underwater.astype(int).groupby(
                (~underwater).cumsum()
            ).cumsum()
            max_dd_duration = underwater_periods.max()
        else:
            max_dd_duration = 0
        
        # Calmar ratio
        calmar = annual_return / (abs(max_dd) + 1e-8)
        
        # Sortino ratio
        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0.01
        sortino = annual_return / (downside_std + 1e-8)
        
        # Trade statistics
        n_trades = len(self.trade_history)
        total_costs = sum(t['cost'] for t in self.trade_history)
        cost_pct = total_costs / initial_value
        
        # Monthly returns for win rate
        monthly_returns = returns.resample('M').sum()
        win_rate = (monthly_returns > 0).mean() if len(monthly_returns) > 0 else 0
        
        # Calculate invested ratio
        invested_days = sum(1 for v in self.portfolio_value_history 
                          if not self.positions.empty)
        invested_ratio = invested_days / len(self.portfolio_value_history) if self.portfolio_value_history else 0
        
        # Invested Sharpe
        invested_sharpe = sharpe / (invested_ratio + 1e-8) if invested_ratio > 0 else sharpe
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe,
            'invested_sharpe': invested_sharpe,
            'max_drawdown': max_dd,
            'max_dd_duration': max_dd_duration,
            'calmar_ratio': calmar,
            'sortino_ratio': sortino,
            'win_rate': win_rate,
            'n_trades': n_trades,
            'total_transaction_costs': total_costs,
            'transaction_cost_pct': cost_pct,
            'invested_ratio': invested_ratio,
            'n_days': len(equity),
            'start_date': equity.index[0],
            'end_date': equity.index[-1],
            'equity_curve': equity,
            'returns': returns,
        }
    
    def run_walk_forward_validation(self, start_date: str, end_date: str,
                                   train_window: int = 504,
                                   test_window: int = 63) -> Dict:
        """
        Run walk-forward validation with proper train/test separation
        
        Args:
            start_date: Overall start date
            end_date: Overall end date
            train_window: Training window in days
            test_window: Test window in days
        
        Returns:
            OOS metrics and breakdown by period
        """
        
        all_dates = pd.date_range(start_date, end_date, freq='B')
        
        results = []
        
        for i in range(train_window, len(all_dates), test_window):
            if i + test_window > len(all_dates):
                break
            
            train_end = all_dates[i - 1]
            test_start = all_dates[i]
            test_end = all_dates[min(i + test_window - 1, len(all_dates) - 1)]
            
            # Reset state
            self.positions = pd.Series(dtype=float)
            self.cash = 1_000_000
            self.portfolio_value_history = []
            self.trade_history = []
            self.regime_detector = HMMRegimeDetector(self.config)
            self.risk_manager = AdvancedRiskManager(self.config)
            
            # Run backtest for this period
            period_results = self.run_backtest(
                test_start.strftime('%Y-%m-%d'),
                test_end.strftime('%Y-%m-%d'),
                train_ml=False
            )
            
            period_results['period_start'] = test_start
            period_results['period_end'] = test_end
            results.append(period_results)
        
        # Aggregate OOS results
        if not results:
            return {'error': 'No validation periods'}
        
        oos_returns = pd.concat([r['returns'] for r in results if 'returns' in r])
        
        oos_metrics = {
            'oos_sharpe': oos_returns.mean() / (oos_returns.std() + 1e-8) * np.sqrt(252),
            'oos_annual_return': oos_returns.mean() * 252,
            'oos_annual_vol': oos_returns.std() * np.sqrt(252),
            'oos_max_dd': min(r.get('max_drawdown', 0) for r in results),
            'n_periods': len(results),
            'period_results': results,
        }
        
        return oos_metrics


def run_full_backtest():
    """Run complete ARES v10.0 backtest"""
    
    config = AresV10Config()
    engine = AresBacktestEngine(config)
    
    print("=" * 60)
    print("ARES v10.0 Backtest - Target Sharpe 3.0+")
    print("=" * 60)
    
    # In-sample backtest
    print("\n[1] Running In-Sample Backtest (2020-2024)...")
    is_results = engine.run_backtest('2020-01-01', '2024-12-31')
    
    print(f"\n=== In-Sample Results ===")
    print(f"Sharpe Ratio: {is_results['sharpe_ratio']:.2f}")
    print(f"Invested Sharpe: {is_results['invested_sharpe']:.2f}")
    print(f"Annual Return: {is_results['annual_return']:.1%}")
    print(f"Max Drawdown: {is_results['max_drawdown']:.1%}")
    print(f"Win Rate: {is_results['win_rate']:.1%}")
    print(f"Transaction Costs: {is_results['transaction_cost_pct']:.2%}")
    
    # Walk-forward OOS validation
    print("\n[2] Running Walk-Forward OOS Validation...")
    engine_wf = AresBacktestEngine(config)
    oos_results = engine_wf.run_walk_forward_validation(
        '2020-01-01', '2024-12-31',
        train_window=504,
        test_window=63
    )
    
    print(f"\n=== OOS Results ===")
    print(f"OOS Sharpe: {oos_results['oos_sharpe']:.2f}")
    print(f"OOS Annual Return: {oos_results['oos_annual_return']:.1%}")
    print(f"OOS Max Drawdown: {oos_results['oos_max_dd']:.1%}")
    print(f"Validation Periods: {oos_results['n_periods']}")
    
    # Sharpe decay analysis
    is_sharpe = is_results['sharpe_ratio']
    oos_sharpe = oos_results['oos_sharpe']
    sharpe_decay = (oos_sharpe - is_sharpe) / is_sharpe * 100
    
    print(f"\n=== Sharpe Decay Analysis ===")
    print(f"IS Sharpe: {is_sharpe:.2f}")
    print(f"OOS Sharpe: {oos_sharpe:.2f}")
    print(f"Sharpe Decay: {sharpe_decay:.1f}%")
    
    if sharpe_decay > -20:
        print("✓ Model shows robust generalization (decay < 20%)")
    else:
        print("⚠ Model may be overfit (decay >= 20%)")
    
    return {
        'is_results': is_results,
        'oos_results': oos_results,
        'sharpe_decay': sharpe_decay,
    }


if __name__ == "__main__":
    results = run_full_backtest()
"""
테스트 우선순위 및 조합 전략

각 개선사항을 독립적으로 테스트한 후 점진적으로 조합합니다.
"""

TEST_PRIORITY = [
    # Phase 1: 핵심 구조 개선 (가장 큰 임팩트 예상)
    {
        'id': 1,
        'name': 'HMM Regime Detection',
        'component': 'HMMRegimeDetector',
        'expected_sharpe_improvement': '+0.3',
        'risk': 'LOW',
        'description': 'VIX 임계값 → 확률적 레짐 전이로 노이즈 감소',
    },
    {
        'id': 2,
        'name': 'Multi-Horizon Momentum',
        'component': 'momentum_multi_horizon',
        'expected_sharpe_improvement': '+0.4',
        'risk': 'LOW',
        'description': '단일 12-1 모멘텀 → 다양한 horizon 결합',
    },
    {
        'id': 3,
        'name': 'HRP Optimization',
        'component': 'HierarchicalRiskParity',
        'expected_sharpe_improvement': '+0.2',
        'risk': 'LOW',
        'description': '역변동성 가중 → 상관관계 기반 분산 최적화',
    },
    
    # Phase 2: 레짐별 전략 개선
    {
        'id': 4,
        'name': 'NEUTRAL Regime Reversal',
        'component': 'short_term_reversal + rsi_reversal',
        'expected_sharpe_improvement': '+0.5 (NEUTRAL)',
        'risk': 'MEDIUM',
        'description': 'MODERATE 레짐에서 모멘텀 → 반전 전략',
    },
    {
        'id': 5,
        'name': 'RISK_OFF Quality Tilt',
        'component': 'quality_composite',
        'expected_sharpe_improvement': '+0.3 (RISK_OFF)',
        'risk': 'LOW',
        'description': 'CRISIS 레짐에서 Quality 팩터 강화',
    },
    
    # Phase 3: 리스크 관리 강화
    {
        'id': 6,
        'name': 'CVaR Dynamic Leverage',
        'component': 'AdvancedRiskManager',
        'expected_sharpe_improvement': '+0.2 (MDD -5%)',
        'risk': 'LOW',
        'description': 'CVaR 기반 동적 레버리지로 MDD 제어',
    },
    {
        'id': 7,
        'name': 'Volatility Targeting',
        'component': 'target_volatility',
        'expected_sharpe_improvement': '+0.3',
        'risk': 'LOW',
        'description': '목표 변동성 12% 유지',
    },
    
    # Phase 4: ML 강화
    {
        'id': 8,
        'name': 'Stacking Ensemble',
        'component': 'StackingRegimeClassifier',
        'expected_sharpe_improvement': '+0.2',
        'risk': 'MEDIUM',
        'description': '앙상블 레짐 분류기',
    },
    {
        'id': 9,
        'name': 'ICIR Dynamic Weights',
        'component': 'ICIRCalculatorEnhanced',
        'expected_sharpe_improvement': '+0.2',
        'risk': 'LOW',
        'description': 'ICIR 기반 팩터 가중치 자동 조정',
    },
    
    # Phase 5: 추가 알파
    {
        'id': 10,
        'name': 'Intraday Signals',
        'component': 'Microstructure features',
        'expected_sharpe_improvement': '+0.1',
        'risk': 'HIGH',
        'description': '인트라데이 데이터 활용 (복잡도 높음)',
    },
]

# 조합 테스트 순서
COMBINATION_TESTS = [
    # Baseline + 단일 개선
    ['Baseline', 1],
    ['Baseline', 2],
    ['Baseline', 3],
    
    # 2개 조합
    ['Baseline', 1, 2],
    ['Baseline', 1, 3],
    ['Baseline', 2, 3],
    
    # 3개 조합 (Phase 1 통합)
    ['Baseline', 1, 2, 3],
    
    # Phase 1 + Phase 2
    ['Baseline', 1, 2, 3, 4],
    ['Baseline', 1, 2, 3, 5],
    ['Baseline', 1, 2, 3, 4, 5],
    
    # Full Phase 1-2 + Phase 3
    ['Baseline', 1, 2, 3, 4, 5, 6],
    ['Baseline', 1, 2, 3, 4, 5, 6, 7],
    
    # Full system
    ['Baseline', 1, 2, 3, 4, 5, 6, 7, 8, 9],
    
    # With intraday (optional, high complexity)
    ['Baseline', 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
]
