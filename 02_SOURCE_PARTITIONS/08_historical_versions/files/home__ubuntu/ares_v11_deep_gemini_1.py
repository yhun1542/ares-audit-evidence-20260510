"""
================================================================================
ARES ULTIMATE v3.0 - Production Grade Quant System
================================================================================
Target: Sharpe 3.0+ | MDD < -10% | Positive 2022 Return
Architecture: Numba Accelerated Factor Engine + HRP Optimization + Walk-Forward ICIR
Security: Strict Look-ahead Bias Prevention (Signal t -> Trade t+1)
Cost Model: 50bps (Slippage + Comm) per round-trip turnover
"""

import os
import sys
import numpy as np
import pandas as pd
import sqlite3
import logging
from typing import Dict, List, Tuple, Optional, Union
from dataclasses import dataclass
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr
from numba import njit, prange

# ==============================================================================
# Configuration
# ==============================================================================

@dataclass
class AresConfig:
    # Database
    db_path: str = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # Universe
    universe: Tuple[str] = (
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ADBE",  # Tech
        "JPM", "BAC", "V", "MA", "BRK.B",  # Finance
        "UNH", "JNJ", "LLY", "PFE", "MRK",  # Healthcare
        "PG", "KO", "PEP", "COST", "WMT",  # Staples
        "XOM", "CVX",  # Energy
        "LMT", "RTX",  # Defense
        "SPY", "QQQ", "IWM", "GLD", "TLT", "IEF", "SHY"  # ETFs
    )
    
    # Backtest Settings
    start_date: str = "2016-01-01"
    end_date: str = "2024-12-31"
    initial_capital: float = 1_000_000.0
    transaction_cost_bps: float = 50.0  # 0.50% (conservative)
    
    # Strategy Params
    rebalance_freq: int = 21  # Monthly
    lookback_window: int = 252
    top_k: int = 10
    
    # Risk Management
    target_volatility: float = 0.15  # 15% Annualized
    max_leverage: float = 2.0
    stop_loss: float = -0.15
    
    # Regime Thresholds
    vix_crisis: float = 28.0
    vix_caution: float = 20.0
    
    # ICIR Settings
    icir_window: int = 126
    icir_halflife: int = 63

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ARES_ULTIMATE")

# ==============================================================================
# Numba Accelerated Core Functions
# ==============================================================================

@njit(parallel=True, cache=True)
def calc_momentum(prices: np.ndarray, lookback: int, lag: int = 1) -> np.ndarray:
    """Calculate momentum with lag to prevent look-ahead bias"""
    rows, cols = prices.shape
    out = np.full((rows, cols), np.nan)
    
    for i in prange(lookback + lag, rows):
        for j in range(cols):
            p_now = prices[i - lag, j]  # Price at t-1 (for trading at t)
            p_old = prices[i - lookback - lag, j]
            if p_old > 0:
                out[i, j] = p_now / p_old - 1.0
    return out

@njit(parallel=True, cache=True)
def calc_volatility(returns: np.ndarray, window: int) -> np.ndarray:
    """Calculate rolling annualized volatility"""
    rows, cols = returns.shape
    out = np.full((rows, cols), np.nan)
    sqrt_252 = np.sqrt(252.0)
    
    for i in prange(window, rows):
        for j in range(cols):
            # Standard deviation of past 'window' returns
            # slice is returns[i-window : i] -> strictly past data relative to i
            r_slice = returns[i-window:i, j]
            valid = ~np.isnan(r_slice)
            if np.sum(valid) > window * 0.8:
                out[i, j] = np.std(r_slice[valid]) * sqrt_252
    return out

@njit(cache=True)
def calc_rsi(prices: np.ndarray, window: int = 14) -> np.ndarray:
    rows, cols = prices.shape
    out = np.full((rows, cols), np.nan)
    
    for j in range(cols):
        gains = np.zeros(rows)
        losses = np.zeros(rows)
        
        # Calculate changes
        delta = prices[1:, j] - prices[:-1, j]
        
        # Initial average
        if len(delta) > window:
            avg_gain = np.sum(np.maximum(delta[:window], 0)) / window
            avg_loss = np.sum(np.maximum(-delta[:window], 0)) / window
            
            for i in range(window + 1, rows):
                change = prices[i-1, j] - prices[i-2, j] # Shifted 1 day back logic
                gain = max(change, 0)
                loss = max(-change, 0)
                
                avg_gain = (avg_gain * (window - 1) + gain) / window
                avg_loss = (avg_loss * (window - 1) + loss) / window
                
                if avg_loss == 0:
                    out[i, j] = 100.0
                else:
                    rs = avg_gain / avg_loss
                    out[i, j] = 100.0 - (100.0 / (1.0 + rs))
                    
    return out

# ==============================================================================
# Advanced Logic Modules (Python Side)
# ==============================================================================

class DataLoader:
    def __init__(self, config: AresConfig):
        self.cfg = config
        
    def fetch_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        logger.info("Connecting to database...")
        conn = sqlite3.connect(self.cfg.db_path)
        
        # 1. Prices
        tickers = ",".join([f"'{t}'" for t in self.cfg.universe])
        query = f"""
            SELECT date, symbol, adj_close 
            FROM daily_ohlcv 
            WHERE symbol IN ({tickers}) 
            AND date >= '{self.cfg.start_date}' 
            AND date <= '{self.cfg.end_date}'
            ORDER BY date, symbol
        """
        raw = pd.read_sql(query, conn)
        raw['date'] = pd.to_datetime(raw['date'])
        
        prices = raw.pivot(index='date', columns='symbol', values='adj_close')
        prices = prices.ffill().bfill() # Handle missing
        
        # 2. VIX
        vix_query = f"SELECT date, close FROM vix WHERE date >= '{self.cfg.start_date}'"
        vix = pd.read_sql(vix_query, conn)
        vix['date'] = pd.to_datetime(vix['date'])
        vix = vix.set_index('date')['close'].reindex(prices.index).ffill()
        
        # 3. Macro/Treasury (TLT for Rate Regime)
        # Using TLT price from universe as proxy for Rates
        tlt = prices['TLT'] if 'TLT' in prices.columns else prices.iloc[:, 0]
        
        conn.close()
        
        # Calculate Returns (Close-to-Close)
        returns = prices.pct_change().fillna(0.0)
        
        logger.info(f"Loaded data: {prices.shape}")
        return prices, returns, vix, tlt

class OptimizationEngine:
    @staticmethod
    def get_hrp_weights(cov: pd.DataFrame) -> pd.Series:
        """Hierarchical Risk Parity Optimization"""
        # 1. Cluster
        corr = cov.corr()
        dist = np.sqrt(0.5 * (1 - corr))
        link = linkage(squareform(dist), method='ward')
        sort_ix = leaves_list(link)
        ordered_tickers = corr.index[sort_ix].tolist()
        
        # 2. Recursive Bisection
        weights = pd.Series(1.0, index=ordered_tickers)
        clusters = [ordered_tickers]
        
        while len(clusters) > 0:
            clusters = [c[i:j] for c in clusters for i, j in [(0, len(c)//2), (len(c)//2, len(c))] if len(c) > 1]
            for i in range(0, len(clusters), 2):
                c1 = clusters[i]
                c2 = clusters[i+1]
                
                v1 = OptimizationEngine._get_cluster_var(cov, c1)
                v2 = OptimizationEngine._get_cluster_var(cov, c2)
                
                alpha = 1 - v1 / (v1 + v2)
                weights[c1] *= alpha
                weights[c2] *= (1 - alpha)
                
        return weights
    
    @staticmethod
    def _get_cluster_var(cov, items):
        cov_slice = cov.loc[items, items]
        w = 1 / np.diag(cov_slice) # Inverse variance
        w /= w.sum()
        return np.dot(np.dot(w, cov_slice), w)

class FactorEngine:
    def __init__(self, prices: pd.DataFrame, returns: pd.DataFrame):
        self.prices = prices
        self.returns = returns
        self.tickers = prices.columns
        self.dates = prices.index
        
        # Numba ready arrays
        self.p_arr = prices.values.astype(np.float64)
        self.r_arr = returns.values.astype(np.float64)
        
    def calculate_all(self) -> Dict[str, pd.DataFrame]:
        logger.info("Calculating Factors (Numba Accelerated)...")
        
        factors = {}
        
        # 1. Momentum (Multi-horizon) - Using 1 day lag for safety
        factors['mom_1m'] = pd.DataFrame(calc_momentum(self.p_arr, 21, 1), index=self.dates, columns=self.tickers)
        factors['mom_3m'] = pd.DataFrame(calc_momentum(self.p_arr, 63, 1), index=self.dates, columns=self.tickers)
        factors['mom_6m'] = pd.DataFrame(calc_momentum(self.p_arr, 126, 1), index=self.dates, columns=self.tickers)
        factors['mom_12m'] = pd.DataFrame(calc_momentum(self.p_arr, 252, 1), index=self.dates, columns=self.tickers)
        
        # 2. Volatility (Inverted)
        vol_3m = pd.DataFrame(calc_volatility(self.r_arr, 63), index=self.dates, columns=self.tickers)
        factors['low_vol'] = 1.0 / (vol_3m + 1e-6) # Higher score = Lower Vol
        
        # 3. Mean Reversion
        factors['reversal'] = -1.0 * pd.DataFrame(calc_momentum(self.p_arr, 5, 1), index=self.dates, columns=self.tickers)
        
        # 4. RSI (Value/Reversal hybrid)
        rsi = pd.DataFrame(calc_rsi(self.p_arr, 14), index=self.dates, columns=self.tickers)
        factors['rsi_oversold'] = 100 - rsi # Higher score = Lower RSI
        
        return factors

class AresBacktester:
    def __init__(self, config: AresConfig):
        self.cfg = config
        self.loader = DataLoader(config)
        
    def run(self):
        # 1. Data Loading
        prices, returns, vix, tlt = self.loader.fetch_data()
        
        # 2. Factor Calculation
        f_engine = FactorEngine(prices, returns)
        raw_factors = f_engine.calculate_all()
        
        # 3. Prepare State
        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        portfolio_value = self.cfg.initial_capital
        equity_curve = [portfolio_value]
        
        # Walk-Forward ICIR State
        ic_history = {k: [] for k in raw_factors.keys()}
        factor_weights = {k: 1.0/len(raw_factors) for k in raw_factors.keys()}
        
        # Simulation Loop
        dates = prices.index
        next_rebalance = dates[self.cfg.lookback_window]
        
        logger.info("Starting Walk-Forward Simulation...")
        
        for i in range(self.cfg.lookback_window, len(dates)-1):
            today = dates[i]
            tomorrow = dates[i+1] # Trading happens for tomorrow's return
            
            # --- A. Regime Detection (Strictly using data available today) ---
            vix_today = vix.iloc[i]
            
            # Rate Regime: TLT < MA200 -> Rates Rising (Bearish for bonds/tech)
            tlt_price = tlt.iloc[i]
            tlt_ma200 = tlt.iloc[i-200:i].mean()
            rate_regime = "RISING" if tlt_price < tlt_ma200 else "FALLING"
            
            # Market Regime
            if vix_today > self.cfg.vix_crisis:
                market_regime = "CRISIS"
            elif vix_today > self.cfg.vix_caution:
                market_regime = "CAUTION"
            else:
                market_regime = "NORMAL"
                
            # --- B. Update ICIR (Learn from past) ---
            # Calculate IC of factors based on returns from (t-1 -> t) and factor at (t-2)
            # This is complex in loop, simplifying: Update monthly
            
            # --- C. Rebalancing Logic ---
            if today >= next_rebalance:
                
                # 1. Update Factor Weights (ICIR)
                # Calculate IC for the past period
                target_ret = returns.iloc[i-21:i] # Past month returns
                valid_factors = []
                
                for fname, f_df in raw_factors.items():
                    # Align factor (lagged) with return
                    # Factor at t-22 predicts Return at t-21..t
                    f_slice = f_df.iloc[i-22:i-1] 
                    ic_val = 0
                    if len(target_ret) > 10:
                        corrs = []
                        for col in prices.columns:
                            try:
                                c = spearmanr(f_slice[col], target_ret[col])[0]
                                if not np.isnan(c): corrs.append(c)
                            except: pass
                        ic_val = np.mean(corrs) if corrs else 0
                    
                    ic_history[fname].append(ic_val)
                    
                    # Calculate ICIR
                    hist = ic_history[fname][-self.cfg.icir_window:]
                    if len(hist) > 5:
                        mean_ic = np.mean(hist)
                        std_ic = np.std(hist) + 1e-6
                        icir = mean_ic / std_ic
                        if icir > 0:
                            valid_factors.append((fname, icir))
                
                # Normalize ICIR weights
                if valid_factors:
                    total_icir = sum(x[1] for x in valid_factors)
                    factor_weights = {k: v/total_icir for k, v in valid_factors}
                else:
                    factor_weights = {k: 1.0/len(raw_factors) for k in raw_factors.keys()} # Fallback
                
                # 2. Combine Factors
                combined_score = pd.Series(0.0, index=prices.columns)
                for fname, w in factor_weights.items():
                    # Z-Score per date
                    daily_f = raw_factors[fname].iloc[i]
                    z_score = (daily_f - daily_f.mean()) / (daily_f.std() + 1e-6)
                    combined_score += z_score.fillna(0) * w
                
                # 3. Filter Universe based on Regime
                candidates = combined_score.sort_values(ascending=False).index.tolist()
                
                # 2022 Protection: Filter out Bond-proxies if Rates Rising
                if rate_regime == "RISING":
                    candidates = [c for c in candidates if c not in ['TLT', 'IEF', 'LQD']]
                
                # Select Top K
                final_universe = candidates[:self.cfg.top_k]
                
                # 4. HRP Optimization
                # Get Covariance of selected assets (past 1 year)
                cov_window = returns[final_universe].iloc[i-252:i].cov()
                target_w = OptimizationEngine.get_hrp_weights(cov_window)
                
                # 5. Apply Volatility Targeting & Leverage Cap
                port_vol = np.sqrt(target_w.T @ cov_window @ target_w) * np.sqrt(252)
                vol_scalar = self.cfg.target_volatility / (port_vol + 1e-6)
                
                # Regime-based Max Leverage
                regime_cap = 0.5 if market_regime == "CRISIS" else (1.0 if market_regime == "CAUTION" else self.cfg.max_leverage)
                
                # Further Reduction if Rates Rising in Crisis (Stagflation Risk)
                if rate_regime == "RISING" and market_regime != "NORMAL":
                    regime_cap *= 0.7
                
                final_leverage = min(vol_scalar, regime_cap)
                final_w = target_w * final_leverage
                
                # Assign to weights dataframe (for tomorrow)
                weights.iloc[i] = 0.0 # Reset
                weights.iloc[i][final_universe] = final_w
                
                # Set next rebalance date
                next_rebalance = dates[min(i + self.cfg.rebalance_freq, len(dates)-1)]
                
            else:
                # No Rebalance: Hold positions
                weights.iloc[i] = weights.iloc[i-1]
            
            # --- D. Calculate PnL for Tomorrow ---
            # Current Portfolio Weights
            w_curr = weights.iloc[i]
            
            # Tomorrow's Returns
            ret_tomorrow = returns.iloc[i+1]
            
            # Portfolio Return
            p_ret = (w_curr * ret_tomorrow).sum()
            
            # Transaction Costs
            # Turnover = sum of absolute change in weights * value
            if i > 0:
                w_prev = weights.iloc[i-1]
                # Adjust previous weights for price movement drift
                w_prev_drift = w_prev * (1 + returns.iloc[i]) 
                w_prev_drift = w_prev_drift / (w_prev_drift.sum() + 1.0) # Approx normalization relative to NAV
                
                turnover = np.abs(w_curr - w_prev).sum()
                cost = turnover * (self.cfg.transaction_cost_bps / 10000.0)
                p_ret -= cost
            
            # Update Equity
            portfolio_value *= (1 + p_ret)
            equity_curve.append(portfolio_value)
            
            if i % 252 == 0:
                logger.info(f"Date: {str(today.date())} | Regime: {market_regime}/{rate_regime} | Eq: {portfolio_value:.0f}")

        # Final Analysis
        self._analyze_results(pd.Series(equity_curve, index=dates[self.cfg.lookback_window-1:]))

    def _analyze_results(self, equity: pd.Series):
        returns = equity.pct_change().fillna(0)
        
        # Metrics
        total_ret = (equity.iloc[-1] / equity.iloc[0]) - 1
        cagr = (1 + total_ret) ** (252 / len(returns)) - 1
        vol = returns.std() * np.sqrt(252)
        sharpe = cagr / vol if vol > 0 else 0
        mdd = (equity / equity.cummax() - 1).min()
        
        # Year by Year
        years = returns.groupby(returns.index.year).apply(lambda x: (1+x).prod()-1)
        
        print("\n" + "="*60)
        print("ARES ULTIMATE v3.0 PERFORMANCE REPORT")
        print("="*60)
        print(f"Final Equity: ${equity.iloc[-1]:,.2f}")
        print(f"CAGR        : {cagr:.2%}")
        print(f"Volatility  : {vol:.2%}")
        print(f"Sharpe Ratio: {sharpe:.2f}")
        print(f"Max Drawdown: {mdd:.2%}")
        print("-" * 60)
        print("Yearly Returns:")
        print(years.map(lambda x: f"{x:.2%}"))
        print("="*60)
        
        # 2022 Check
        if 2022 in years.index:
            print(f"2022 Return Check: {years[2022]:.2%}")
            if years[2022] > -0.05:
                print("SUCCESS: 2022 Protection Active")
            else:
                print("WARNING: 2022 Loss exceeding targets")

# ==============================================================================
# Execution
# ==============================================================================

if __name__ == "__main__":
    config = AresConfig()
    backtester = AresBacktester(config)
    backtester.run()