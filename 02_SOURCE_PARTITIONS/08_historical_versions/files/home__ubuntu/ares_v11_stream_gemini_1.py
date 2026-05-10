"""
ARES v11.0 ULTIMATE - Complete Negotiation
Target: Sharpe 3.0+, MDD < -15%, Positive 2022 Return
Architecture: Event-Driven Backtest with Numba Acceleration & Regime Adaptation
"""

import os
import sqlite3
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Union
from enum import Enum
import warnings
import logging
from scipy.optimize import minimize
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy import stats
from numba import njit, prange

# Configuration for Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("ARES_v11")
warnings.filterwarnings('ignore')

# ==================================================================================
# 1. CORE CONFIGURATION & CONSTANTS
# ==================================================================================

class RegimeType(Enum):
    BULL_QUIET = 1      # Low Vol, Uptrend (Max Leverage)
    BULL_VOLATILE = 2   # High Vol, Uptrend (Reduced Leverage)
    BEAR_PROTECTIVE = 3 # Downtrend, Neg Correlation (Bonds Hedge)
    BEAR_INFLATION = 4  # Downtrend, Pos Correlation (Cash Hedge) - 2022 Scenario
    CRISIS_PANIC = 5    # Extreme Vol (Cash/Put Hedge)

@dataclass
class AresConfig:
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # Universe
    universe_tickers: List[str] = field(default_factory=lambda: [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO", "PEP", "COST", # Tech/Growth
        "JPM", "BAC", "UNH", "JNJ", "XOM", "CVX", "PG", "KO",                           # Value/Defensive
        "SPY", "QQQ", "IWM", "EEM",                                                     # Equity ETFs
        "TLT", "IEF", "SHY", "BIL", "GLD", "DBC"                                        # Asset Class ETFs
    ])
    benchmark: str = "SPY"
    risk_free_rate: float = 0.04
    
    # Strategy Settings
    lookback_window: int = 252
    rebalance_freq: int = 5  # Weekly
    
    # Risk Management
    target_volatility: float = 0.12  # 12% Annualized
    max_leverage: float = 2.0
    stop_loss_threshold: float = -0.07 # Asset level stop loss
    
    # Transaction Costs
    cost_bps: float = 0.0010  # 10 bps per side
    slippage_bps: float = 0.0005 # 5 bps
    
    # Regime Thresholds
    vix_low: float = 15.0
    vix_high: float = 25.0
    corr_threshold: float = 0.3 # Correlation threshold for Stock/Bond decoupling

CONFIG = AresConfig()

# ==================================================================================
# 2. NUMBA ACCELERATED MATH KERNEL (NO LOOK-AHEAD BIAS)
# ==================================================================================

@njit(parallel=True, cache=True)
def calc_rolling_zscore(values: np.ndarray, window: int) -> np.ndarray:
    """Calculate rolling z-score efficiently preventing look-ahead bias"""
    rows, cols = values.shape
    result = np.full((rows, cols), np.nan)
    
    for c in prange(cols):
        for i in range(window, rows):
            # Slice strictly [i-window : i] (past data only)
            slice_data = values[i-window:i, c]
            mean = np.nanmean(slice_data)
            std = np.nanstd(slice_data)
            
            if std > 1e-8:
                result[i, c] = (values[i, c] - mean) / std
            else:
                result[i, c] = 0.0
    return result

@njit(parallel=True, cache=True)
def calc_rolling_volatility(returns: np.ndarray, window: int) -> np.ndarray:
    """Annualized rolling volatility"""
    rows, cols = returns.shape
    result = np.full((rows, cols), np.nan)
    sqrt_252 = np.sqrt(252.0)
    
    for c in prange(cols):
        for i in range(window, rows):
            slice_data = returns[i-window:i, c]
            result[i, c] = np.nanstd(slice_data) * sqrt_252
    return result

@njit(cache=True)
def calc_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Fast correlation between two arrays"""
    mask = np.isfinite(a) & np.isfinite(b)
    if np.sum(mask) < 2:
        return 0.0
    
    a_masked = a[mask]
    b_masked = b[mask]
    
    return np.corrcoef(a_masked, b_masked)[0, 1]

@njit(cache=True)
def calc_rolling_corr(asset_rets: np.ndarray, bench_rets: np.ndarray, window: int) -> np.ndarray:
    """Rolling correlation vs benchmark"""
    rows = len(asset_rets)
    result = np.full(rows, np.nan)
    
    for i in range(window, rows):
        a = asset_rets[i-window:i]
        b = bench_rets[i-window:i]
        result[i] = calc_correlation(a, b)
    return result

@njit(parallel=True, cache=True)
def calc_rsi(prices: np.ndarray, window: int) -> np.ndarray:
    """Relative Strength Index"""
    rows, cols = prices.shape
    result = np.full((rows, cols), np.nan)
    
    for c in prange(cols):
        deltas = np.diff(prices[:, c])
        seed = deltas[:window+1]
        up = seed[seed >= 0].sum()/window
        down = -seed[seed < 0].sum()/window
        rs = up/down if down != 0 else 0
        result[window, c] = 100 - 100/(1+rs)

        for i in range(window+1, rows-1): # fix index size
            delta = deltas[i-1]
            if delta > 0:
                upval = delta
                downval = 0.
            else:
                upval = 0.
                downval = -delta

            up = (up*(window-1) + upval)/window
            down = (down*(window-1) + downval)/window
            rs = up/down if down != 0 else 0
            result[i, c] = 100 - 100/(1+rs)
            
    return result

# ==================================================================================
# 3. DATA & FEATURE ENGINEERING
# ==================================================================================

class DataManager:
    """Robust Data Loader with SQLite"""
    
    def __init__(self, config: AresConfig):
        self.config = config
        self.conn = None
        
    def connect(self):
        if not os.path.exists(self.config.db_path):
            raise FileNotFoundError(f"Database not found at {self.config.db_path}")
        self.conn = sqlite3.connect(self.config.db_path)
        
    def load_market_data(self, start_date: str, end_date: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Loads Prices and VIX"""
        self.connect()
        
        tickers_str = "'" + "','".join(self.config.universe_tickers) + "'"
        query = f"""
            SELECT date, symbol, adjusted_close 
            FROM daily_ohlcv 
            WHERE symbol IN ({tickers_str}) 
            AND date >= '{start_date}' AND date <= '{end_date}'
            ORDER BY date
        """
        
        df = pd.read_sql(query, self.conn)
        df['date'] = pd.to_datetime(df['date'])
        
        # Pivot
        prices = df.pivot(index='date', columns='symbol', values='adjusted_close')
        prices = prices.ffill().bfill() # Handle holidays/missing data
        
        # VIX
        vix_query = f"""
            SELECT date, close as vix 
            FROM vix 
            WHERE date >= '{start_date}' AND date <= '{end_date}'
        """
        vix_df = pd.read_sql(vix_query, self.conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix_df = vix_df.set_index('date').reindex(prices.index).ffill()
        
        self.conn.close()
        return prices, vix_df

class FeatureEngine:
    """Generates Alpha Factors using Numba Kernel"""
    
    def __init__(self, prices: pd.DataFrame):
        self.prices = prices
        self.returns = prices.pct_change().fillna(0)
        self.assets = prices.columns
        self.dates = prices.index
        
    def generate_features(self) -> Dict[str, pd.DataFrame]:
        p_values = self.prices.values
        r_values = self.returns.values
        
        logger.info("Computing Technical Factors (Numba)...")
        
        # 1. Multi-Horizon Momentum (Z-Scored)
        mom_1m = calc_rolling_zscore(p_values / np.roll(p_values, 21, axis=0) - 1, 252)
        mom_3m = calc_rolling_zscore(p_values / np.roll(p_values, 63, axis=0) - 1, 252)
        mom_6m = calc_rolling_zscore(p_values / np.roll(p_values, 126, axis=0) - 1, 252)
        mom_12m = calc_rolling_zscore(p_values / np.roll(p_values, 252, axis=0) - 1, 252)
        
        # 2. Volatility Factors (Inverse is better)
        vol_1m = calc_rolling_volatility(r_values, 21)
        vol_3m = calc_rolling_volatility(r_values, 63)
        
        # 3. Mean Reversion
        rsi = calc_rsi(p_values, 14)
        rsi_z = calc_rolling_zscore(rsi, 252)
        
        # Store as DataFrames
        features = {
            'mom_1m': pd.DataFrame(mom_1m, index=self.dates, columns=self.assets),
            'mom_3m': pd.DataFrame(mom_3m, index=self.dates, columns=self.assets),
            'mom_6m': pd.DataFrame(mom_6m, index=self.dates, columns=self.assets),
            'mom_12m': pd.DataFrame(mom_12m, index=self.dates, columns=self.assets),
            'vol_1m': pd.DataFrame(vol_1m, index=self.dates, columns=self.assets),
            'vol_3m': pd.DataFrame(vol_3m, index=self.dates, columns=self.assets),
            'rsi_z': pd.DataFrame(rsi_z, index=self.dates, columns=self.assets)
        }
        
        return features

# ==================================================================================
# 4. ADVANCED REGIME DETECTION (THE 2022 FIX)
# ==================================================================================

class RegimeDetector:
    """
    Detects market regimes including the critical 'Inflation Bear' (Stock-Bond Correlation > 0)
    """
    
    def __init__(self, config: AresConfig):
        self.config = config
        
    def detect_regimes(self, prices: pd.DataFrame, vix: pd.DataFrame) -> pd.Series:
        logger.info("Detecting Market Regimes...")
        
        spy = prices['SPY']
        tlt = prices['TLT'] if 'TLT' in prices.columns else prices[prices.columns[0]] # Fallback
        
        # 1. Trends
        spy_ma200 = spy.rolling(200).mean()
        trend = (spy > spy_ma200).astype(int) # 1 = Bull, 0 = Bear
        
        # 2. VIX Regime
        vix_ma20 = vix['vix'].rolling(20).mean()
        vix_regime = pd.Series(0, index=vix.index)
        vix_regime[vix_ma20 < self.config.vix_low] = 0 # Low Vol
        vix_regime[(vix_ma20 >= self.config.vix_low) & (vix_ma20 < self.config.vix_high)] = 1 # Med Vol
        vix_regime[vix_ma20 >= self.config.vix_high] = 2 # High Vol
        
        # 3. Stock-Bond Correlation (Critical for 2022)
        # Using 60-day rolling correlation
        spy_rets = spy.pct_change().fillna(0).values
        tlt_rets = tlt.pct_change().fillna(0).values
        
        corr_vals = calc_rolling_corr(spy_rets, tlt_rets, 60)
        corr_series = pd.Series(corr_vals, index=prices.index).fillna(0)
        
        # 4. Determine Composite Regime
        regimes = []
        for i in range(len(prices)):
            date = prices.index[i]
            is_bull = trend.iloc[i] == 1
            v_level = vix_regime.iloc[i]
            correlation = corr_series.iloc[i]
            
            if is_bull:
                if v_level == 0:
                    r = RegimeType.BULL_QUIET
                else:
                    r = RegimeType.BULL_VOLATILE
            else: # Bear
                if v_level == 2:
                    r = RegimeType.CRISIS_PANIC
                elif correlation > self.config.corr_threshold:
                    # Stocks down, Bonds correlated -> Bonds won't hedge -> INFLATION BEAR
                    r = RegimeType.BEAR_INFLATION 
                else:
                    # Standard Bear -> Bonds likely hedge
                    r = RegimeType.BEAR_PROTECTIVE
            
            regimes.append(r)
            
        return pd.Series(regimes, index=prices.index)

# ==================================================================================
# 5. PORTFOLIO OPTIMIZATION (HRP + VOL TARGETING)
# ==================================================================================

class PortfolioOptimizer:
    """
    Hierarchical Risk Parity (HRP) with Volatility Targeting
    """
    
    def __init__(self, config: AresConfig):
        self.config = config
        
    def get_hrp_weights(self, cov_matrix: pd.DataFrame) -> pd.Series:
        """Computes HRP weights"""
        # 1. Cluster
        corr = cov_matrix.corr()
        dist = np.sqrt((1 - corr) / 2)
        link = linkage(squareform(dist), 'single')
        sort_ix = leaves_list(link)
        ordered_tickers = corr.index[sort_ix].tolist()
        
        # 2. Recursive Bisection
        weights = self._get_recursive_bisection(cov_matrix, ordered_tickers)
        return weights
        
    def _get_recursive_bisection(self, cov, sort_ix):
        w = pd.Series(1, index=sort_ix)
        items = [sort_ix]
        
        while len(items) > 0:
            items = [i for i in items if len(i) > 1]
            for item in items:
                split = len(item) // 2
                left = item[:split]
                right = item[split:]
                
                var_left = self._get_cluster_var(cov, left)
                var_right = self._get_cluster_var(cov, right)
                
                alpha = 1 - var_left / (var_left + var_right)
                
                w[left] *= alpha
                w[right] *= (1 - alpha)
                
            items = [item[:len(item)//2] for item in items] + \
                    [item[len(item)//2:] for item in items]
        return w
    
    def _get_cluster_var(self, cov, items):
        cov_slice = cov.loc[items, items]
        w = 1 / np.diag(cov_slice) # Inverse Variance
        w /= w.sum()
        return np.dot(np.dot(w, cov_slice), w)

    def optimize_allocation(self, 
                          current_date: pd.Timestamp,
                          regime: RegimeType, 
                          candidates: List[str], 
                          returns_window: pd.DataFrame) -> Dict[str, float]:
        
        # Regime-based Asset Selection & Constraints
        final_allocation = {}
        
        # 1. Define Safe Assets based on Regime
        if regime == RegimeType.BEAR_INFLATION:
            # 2022 Mode: Bonds are dangerous. Go to Cash/Commodities.
            safe_assets = ['BIL', 'SHY', 'GLD', 'DBC'] 
            risk_budget = 0.2 # Very Low equity exposure
        elif regime == RegimeType.CRISIS_PANIC:
            # Panic Mode: Cash is King, maybe Long Vol if available (not in list)
            safe_assets = ['BIL', 'IEF']
            risk_budget = 0.1
        elif regime == RegimeType.BEAR_PROTECTIVE:
            # Standard Deflationary Bust: Long TLT works
            safe_assets = ['TLT', 'IEF']
            risk_budget = 0.4
        else:
            # Bull Markets
            safe_assets = []
            risk_budget = 1.0

        # 2. Equity Allocation (Risk Component)
        if risk_budget > 0 and len(candidates) > 0:
            # Calculate HRP weights for equity candidates
            sub_cov = returns_window[candidates].cov()
            eq_weights = self.get_hrp_weights(sub_cov)
            
            # Apply Risk Budget
            for ticker, w in eq_weights.items():
                final_allocation[ticker] = w * risk_budget
        
        # 3. Safe Asset Allocation
        safe_budget = 1.0 - sum(final_allocation.values())
        if safe_budget > 0.01:
            # Equal weight safe assets available in universe
            available_safe = [t for t in safe_assets if t in returns_window.columns]
            if not available_safe:
                 # Fallback to BIL or nothing
                 available_safe = ['BIL'] if 'BIL' in returns_window.columns else []
            
            if available_safe:
                w_each = safe_budget / len(available_safe)
                for sa in available_safe:
                    final_allocation[sa] = w_each

        return final_allocation

# ==================================================================================
# 6. BACKTEST ENGINE (EVENT DRIVEN)
# ==================================================================================

class AresBacktester:
    def __init__(self, config: AresConfig):
        self.config = config
        self.dm = DataManager(config)
        self.rd = RegimeDetector(config)
        self.po = PortfolioOptimizer(config)
        
    def run(self, start_date: str, end_date: str):
        logger.info(f"Starting ARES v11 Backtest: {start_date} to {end_date}")
        
        # 1. Load Data
        prices, vix = self.dm.load_market_data("2010-01-01", end_date) # Load extra for warmup
        
        # 2. Features
        fe = FeatureEngine(prices)
        features = fe.generate_features()
        
        # 3. Regimes
        regimes = self.rd.detect_regimes(prices, vix)
        
        # 4. Simulation Loop
        dates = prices.index[prices.index >= start_date]
        portfolio_value = 100000.0
        positions = {}
        history = []
        
        logger.info("Running Simulation Loop...")
        for i, date in enumerate(dates):
            if i % 100 == 0: logger.info(f"Processing {date.date()}...")
            
            # Get data available UP TO yesterday (No look-ahead)
            # In live trading, we run this before market open using yesterday's close
            curr_prices = prices.loc[date]
            prev_regime = regimes.loc[:date].iloc[-2] # Regime decided based on yesterday's close
            
            # Rebalance Check
            if i % self.config.rebalance_freq == 0:
                # 1. Score Assets
                # Simple Composite Score: Momentum (60%) + Low Vol (20%) + RSI Reversion (20%)
                # Only score equities
                equities = [c for c in self.config.universe_tickers if c not in ['TLT','IEF','SHY','BIL','GLD','DBC','SPY','QQQ','IWM','EEM']]
                
                scores = {}
                for tick in equities:
                    try:
                        # Fetch latest known feature values (iloc -1 of the slice ending yesterday)
                        # To be strictly robust, we use .loc[:date].shift(1).iloc[-1] logic implicitly handled by feature gen
                        idx_loc = prices.index.get_loc(date) - 1
                        
                        m12 = features['mom_12m'].iloc[idx_loc][tick]
                        m6 = features['mom_6m'].iloc[idx_loc][tick]
                        vol = features['vol_3m'].iloc[idx_loc][tick]
                        
                        # Score: High Mom, Low Vol
                        score = (0.5 * m12) + (0.3 * m6) - (0.2 * vol)
                        scores[tick] = score
                    except:
                        scores[tick] = -999
                
                # Select Top N
                sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                top_n = sorted_scores[:10] # Top 10 stocks
                candidates = [x[0] for x in top_n if x[1] > 0] # Must have positive score
                
                # 2. Optimize
                # Use returns window up to yesterday
                hist_window = prices.pct_change().loc[:date].iloc[:-1].tail(126) # 6 month correlation window
                
                target_weights = self.po.optimize_allocation(
                    date, prev_regime, candidates, hist_window
                )
                
                # 3. Volatility Targeting (Dynamic Leverage)
                # Calculate Portfolio Historic Volatility using target weights
                if len(target_weights) > 0:
                    cov = hist_window[list(target_weights.keys())].cov() * 252
                    w_vec = np.array(list(target_weights.values()))
                    port_var = np.dot(w_vec.T, np.dot(cov, w_vec))
                    port_vol = np.sqrt(port_var)
                    
                    # Target Vol / Realized Vol
                    vol_scalar = self.config.target_volatility / port_vol if port_vol > 0.01 else 1.0
                    vol_scalar = min(vol_scalar, self.config.max_leverage)
                    
                    # Apply scalar
                    for k in target_weights:
                        target_weights[k] *= vol_scalar
                
                # 4. Execute (Calculate shares)
                # Transaction Costs Logic
                new_positions = {}
                cash = portfolio_value
                
                # Sell first
                for ticker, size in positions.items():
                    if ticker not in target_weights:
                        # Full Sell
                        price = curr_prices[ticker]
                        cash += size * price * (1 - self.config.cost_bps - self.config.slippage_bps)
                
                # Buy/Adjust
                for ticker, weight in target_weights.items():
                    target_amt = portfolio_value * weight
                    price = curr_prices[ticker]
                    
                    current_shares = positions.get(ticker, 0)
                    current_amt = current_shares * price
                    
                    diff = target_amt - current_amt
                    
                    if diff > 0: # Buy
                        cost = diff * (self.config.cost_bps + self.config.slippage_bps)
                        shares_to_buy = (diff - cost) / price
                        new_positions[ticker] = current_shares + shares_to_buy
                        cash -= diff
                    elif diff < 0: # Sell
                        sell_amt = abs(diff)
                        cost = sell_amt * (self.config.cost_bps + self.config.slippage_bps)
                        shares_to_sell = sell_amt / price
                        new_positions[ticker] = current_shares - shares_to_sell
                        cash += sell_amt - cost # Cash increases by sell amount minus cost
                    else:
                        new_positions[ticker] = current_shares
                
                # Update State
                positions = new_positions
                
            # Update Value
            equity_val = sum([pos * curr_prices[tick] for tick, pos in positions.items()])
            # Cash return (Risk Free Rate approx daily)
            # In complex sim, cash tracks BIL, but here we simplify or assume invested in BIL
            portfolio_value = equity_val + cash # Simplified cash drag
            
            history.append({
                'date': date,
                'value': portfolio_value,
                'regime': prev_regime.name,
                'leverage': sum(target_weights.values()) if 'target_weights' in locals() else 0
            })
            
        return pd.DataFrame(history).set_index('date')

# ==================================================================================
# 7. VALIDATION & STATS (DEFLATED SHARPE)
# ==================================================================================

def calculate_dsr(returns: pd.Series, n_strategies: int = 10) -> float:
    """Calculates Probabilistic Deflated Sharpe Ratio"""
    sharpe = returns.mean() / returns.std() * np.sqrt(252)
    skew = stats.skew(returns)
    kurt = stats.kurtosis(returns)
    T = len(returns)
    
    # Variance of the estimated Sharpe ratio
    var_sr = (1 / (T - 1)) * (1 + 0.5 * sharpe**2 - skew * sharpe + (kurt / 4) * sharpe**2)
    
    # Expected Maximum Sharpe (from multiple trials)
    gamma = 0.5772156649
    exp_max_sr = ((1 - gamma) * stats.norm.ppf(1 - 1/n_strategies) + 
                  gamma * stats.norm.ppf(1 - 1/(n_strategies * np.e))) * np.sqrt(1/252) # approximated
    
    # Probabilistic Sharpe Ratio
    dsr = stats.norm.cdf((sharpe - exp_max_sr) / np.sqrt(var_sr))
    return dsr

def analyze_performance(history: pd.DataFrame):
    rets = history['value'].pct_change().dropna()
    
    # 1. Basic Stats
    total_ret = (history['value'].iloc[-1] / history['value'].iloc[0]) - 1
    cagr = (history['value'].iloc[-1] / history['value'].iloc[0]) ** (252/len(history)) - 1
    vol = rets.std() * np.sqrt(252)
    sharpe = cagr / vol
    
    # 2. Drawdown
    cum_ret = (1 + rets).cumprod()
    running_max = cum_ret.cummax()
    drawdown = (cum_ret - running_max) / running_max
    max_dd = drawdown.min()
    
    # 3. 2022 Specific
    y2022 = rets['2022']
    ret_2022 = (1 + y2022).prod() - 1
    
    print("\n" + "="*50)
    print("ARES v11 ULTIMATE PERFORMANCE REPORT")
    print("="*50)
    print(f"Total Return : {total_ret*100:.2f}%")
    print(f"CAGR         : {cagr*100:.2f}%")
    print(f"Volatility   : {vol*100:.2f}%")
    print(f"Sharpe Ratio : {sharpe:.4f}")
    print(f"Max Drawdown : {max_dd*100:.2f}%")
    print(f"2022 Return  : {ret_2022*100:.2f}% (Target: > 0%)")
    print("-" * 50)
    
    # DSR Check
    dsr = calculate_dsr(rets)
    print(f"Deflated Sharpe Probability: {dsr:.4f}")
    if dsr > 0.95:
        print(">> Strategy is statistically robust (95% Conf).")
    else:
        print(">> Warning: High probability of overfitting.")
    print("="*50)

# ==================================================================================
# 8. EXECUTION
# ==================================================================================

if __name__ == "__main__":
    try:
        # Initialize
        bt = AresBacktester(CONFIG)
        
        # Run Backtest (Includes 2022 for validation)
        # Note: In production, start date should be adjusted to allow for 1-year lookback warmup
        results = bt.run("2018-01-01", "2023-12-31")
        
        # Analyze
        analyze_performance(results)
        
        # Save Results
        results.to_csv("/home/ubuntu/ares_results/v11_ultimate_results.csv")
        logger.info("Backtest Complete. Results saved.")
        
    except Exception as e:
        logger.error(f"Critical Failure: {str(e)}")
        raise e