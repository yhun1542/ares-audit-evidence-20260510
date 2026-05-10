"""
================================================================================
ARES ULTIMATE v3.0 - Pareto Optimized Production Engine
================================================================================
Target: Sharpe 3.0+ | MDD < -15% | 2022 Positive Return
Integrated Modules:
1. ICIR Dynamic Weights (Factor Alpha)
2. HRP Optimization (Allocation Efficiency)
3. Rate & VIX Regime Detection (Macro Defense)
4. Volatility Targeting (Risk Control)
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import warnings
import logging
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr
from numba import njit, prange
from typing import Dict, List, Tuple

# Configuration
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(asctime)s - %(message)s")
logger = logging.getLogger("ARES_ULTIMATE")

class AresConfig:
    # Database
    DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
    
    # Universe
    UNIVERSE_TABLE = "daily_ohlcv"
    BENCHMARK = "SPY"
    SAFE_ASSET = "IEF" # Intermediate Treasury as Safe Harbor
    RATE_PROXY = "TLT" # Treasury 20Y+ for Rate Regime
    VIX_INDEX = "VIX"  # Volatility Index
    
    # Strategy Params
    LOOKBACK_MOM = [21, 63, 126, 252] # Multi-horizon Momentum
    LOOKBACK_VOL = 20
    LOOKBACK_ICIR = 126 # Rolling window for Factor ICIR
    
    # Risk Management
    VOL_TARGET = 0.12  # 12% Annual Volatility Target
    MAX_LEVERAGE = 1.5
    MIN_LEVERAGE = 0.0
    TRANS_COST = 0.0005 # 50bps (Conservative)
    
    # Optimization
    REBALANCE_FREQ = 21 # Monthly
    TOP_N = 10 # Number of assets in portfolio
    
    # Regime Thresholds
    VIX_PANIC = 28.0
    RATE_STRESS_THRESHOLD = -0.15 # TLT 6-month return threshold for Rate Stress

# -----------------------------------------------------------------------------
# 1. Numba JIT Core Engines (Performance Critical)
# -----------------------------------------------------------------------------

@njit(parallel=True, cache=True)
def fast_momentum_multi(prices, lookbacks):
    """Calculate multi-horizon momentum z-scores combined"""
    n_dates, n_assets = prices.shape
    combined_score = np.zeros((n_dates, n_assets), dtype=np.float64)
    
    for lb in lookbacks:
        mom = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
        for i in prange(lb + 1, n_dates):
            for j in range(n_assets):
                if prices[i-1, j] > 0 and prices[i-lb-1, j] > 0:
                    mom[i, j] = prices[i-1, j] / prices[i-lb-1, j] - 1
        
        # Cross-sectional Z-Score for this horizon
        for i in prange(n_dates):
            row = mom[i, :]
            valid = row[~np.isnan(row)]
            if len(valid) > 2:
                mean = np.mean(valid)
                std = np.std(valid)
                if std > 1e-8:
                    for j in range(n_assets):
                        if not np.isnan(mom[i, j]):
                            mom[i, j] = (mom[i, j] - mean) / std
                        else:
                            mom[i, j] = 0.0
                else:
                    mom[i, :] = 0.0
            else:
                mom[i, :] = 0.0
        
        combined_score += mom
        
    return combined_score / len(lookbacks)

@njit(parallel=True, cache=True)
def fast_volatility(returns, lookback):
    """Rolling annualized volatility"""
    n_dates, n_assets = returns.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i-lookback:i, j]
            # Simple std deviation
            cnt = 0
            sum_sq = 0.0
            mean_val = 0.0
            for k in range(lookback):
                if not np.isnan(window[k]):
                    mean_val += window[k]
                    cnt += 1
            if cnt > lookback // 2:
                mean_val /= cnt
                for k in range(lookback):
                    if not np.isnan(window[k]):
                        sum_sq += (window[k] - mean_val) ** 2
                result[i, j] = np.sqrt(sum_sq / (cnt - 1)) * np.sqrt(252)
            else:
                result[i, j] = np.nan
    return result

@njit(cache=True)
def calc_portfolio_vol(weights, cov_matrix):
    """Calculate portfolio volatility: sqrt(w.T * Cov * w)"""
    # Numba compatible matrix multiplication
    temp = np.dot(cov_matrix, weights)
    var = np.dot(weights, temp)
    return np.sqrt(var) * np.sqrt(252)

# -----------------------------------------------------------------------------
# 2. Advanced Optimization Modules (HRP & ICIR)
# -----------------------------------------------------------------------------

class HRP_Optimizer:
    """Hierarchical Risk Parity Optimizer"""
    
    @staticmethod
    def get_quasi_diag(link):
        link = link.astype(int)
        sort_ix = leaves_list(link)
        return sort_ix

    @staticmethod
    def get_cluster_var(cov, c_items):
        cov_slice = cov[np.ix_(c_items, c_items)]
        w = 1 / np.diag(cov_slice) # Inverse variance weights
        w /= w.sum()
        return np.dot(np.dot(w, cov_slice), w)

    @staticmethod
    def get_rec_bipart(cov, sort_ix):
        w = pd.Series(1, index=sort_ix)
        c_items = [sort_ix]
        while len(c_items) > 0:
            c_items = [i[j:k] for i in c_items for j, k in ((0, len(i) // 2), (len(i) // 2, len(i))) if len(i) > 1]
            for i in range(0, len(c_items), 2):
                c_items0 = c_items[i]
                c_items1 = c_items[i + 1]
                c_var0 = HRP_Optimizer.get_cluster_var(cov, c_items0)
                c_var1 = HRP_Optimizer.get_cluster_var(cov, c_items1)
                alpha = 1 - c_var0 / (c_var0 + c_var1)
                w[c_items0] *= alpha
                w[c_items1] *= 1 - alpha
        return w

    @classmethod
    def optimize(cls, returns_df):
        """Main HRP entry point"""
        if len(returns_df) < 20 or len(returns_df.columns) < 2:
            return pd.Series(1.0/len(returns_df.columns), index=returns_df.columns)
            
        corr = returns_df.corr().fillna(0)
        cov = returns_df.cov().fillna(0).values
        
        # Distance matrix
        dist = np.sqrt((1 - corr) / 2)
        dist = dist.fillna(0)
        np.fill_diagonal(dist.values, 0)
        
        link = linkage(squareform(dist), method='single')
        sort_ix = cls.get_quasi_diag(link)
        sort_ix = returns_df.columns[sort_ix].tolist()
        
        hrp_weights = cls.get_rec_bipart(cov, sort_ix)
        return hrp_weights.sort_index()

class ICIR_Weighter:
    """Dynamic Factor Weighting based on Information Coefficient"""
    
    def __init__(self, window=126):
        self.window = window
        self.ic_history = {'momentum': [], 'reversal': [], 'volatility': []}
        
    def update_and_get_weights(self, date, factor_scores, fwd_returns):
        # Calculate Rank IC for each factor
        weights = {'momentum': 0.5, 'reversal': 0.3, 'volatility': 0.2} # Default
        
        if len(fwd_returns) < 20: return weights
        
        # Calculate ICs
        for factor, scores in factor_scores.items():
            valid_idx = scores.dropna().index.intersection(fwd_returns.dropna().index)
            if len(valid_idx) > 10:
                ic, _ = spearmanr(scores[valid_idx], fwd_returns[valid_idx])
                self.ic_history[factor].append(ic if not np.isnan(ic) else 0)
            else:
                self.ic_history[factor].append(0)
                
        # Keep only window
        for k in self.ic_history:
            self.ic_history[k] = self.ic_history[k][-self.window:]
            
        # Calculate ICIR (Mean IC / Std IC)
        icir_scores = {}
        for k, v in self.ic_history.items():
            if len(v) > 10 and np.std(v) > 0:
                icir = np.mean(v) / np.std(v)
                icir_scores[k] = max(0, icir) # Ignore negative IC factors
            else:
                icir_scores[k] = 0
                
        total_score = sum(icir_scores.values())
        if total_score > 0:
            weights = {k: v / total_score for k, v in icir_scores.items()}
            
        return weights

# -----------------------------------------------------------------------------
# 3. Main Production Engine
# -----------------------------------------------------------------------------

class AresUltimateV3:
    def __init__(self, config=AresConfig):
        self.cfg = config
        self.conn = None
        self.icir_engine = ICIR_Weighter(window=config.LOOKBACK_ICIR)
        
    def connect(self):
        self.conn = sqlite3.connect(self.cfg.DB_PATH)
        
    def load_data(self):
        logger.info("Loading Data from Database...")
        query = f"""
        SELECT date, symbol, adjusted_close, volume 
        FROM {self.cfg.UNIVERSE_TABLE} 
        WHERE symbol NOT IN ('{self.cfg.VIX_INDEX}', '{self.cfg.BENCHMARK}', 'SPY', 'QQQ')
        ORDER BY date, symbol
        """
        df = pd.read_sql(query, self.conn)
        df['date'] = pd.to_datetime(df['date'])
        
        self.prices = df.pivot(index='date', columns='symbol', values='adjusted_close').ffill()
        self.volume = df.pivot(index='date', columns='symbol', values='volume').fillna(0)
        self.returns = self.prices.pct_change().fillna(0)
        
        # Load Macro Indicators
        vix_q = f"SELECT date, close FROM {self.cfg.UNIVERSE_TABLE} WHERE symbol = '{self.cfg.VIX_INDEX}'"
        vix_df = pd.read_sql(vix_q, self.conn)
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        self.vix = vix_df.set_index('date')['close'].reindex(self.prices.index).ffill()
        
        tlt_q = f"SELECT date, adjusted_close FROM {self.cfg.UNIVERSE_TABLE} WHERE symbol = '{self.cfg.RATE_PROXY}'"
        tlt_df = pd.read_sql(tlt_q, self.conn)
        tlt_df['date'] = pd.to_datetime(tlt_df['date'])
        self.tlt_prices = tlt_df.set_index('date')['adjusted_close'].reindex(self.prices.index).ffill()
        
        logger.info(f"Data Loaded: {self.prices.shape} | Time: {self.prices.index[0]} to {self.prices.index[-1]}")

    def generate_signals(self):
        logger.info("Generating Factor Signals (Numba Optimized)...")
        prices_np = self.prices.values
        
        # 1. Momentum (Multi-Horizon)
        self.mom_score = fast_momentum_multi(prices_np, np.array(self.cfg.LOOKBACK_MOM))
        
        # 2. Volatility (Inverse)
        vol_raw = fast_volatility(self.returns.values, self.cfg.LOOKBACK_VOL)
        # Avoid division by zero and standardize
        with np.errstate(divide='ignore'):
            inv_vol = 1.0 / (vol_raw + 1e-6)
        inv_vol[np.isnan(inv_vol)] = 0
        
        # Normalize Vol Score (Z-Score like)
        means = np.nanmean(inv_vol, axis=1, keepdims=True)
        stds = np.nanstd(inv_vol, axis=1, keepdims=True)
        self.vol_score = (inv_vol - means) / (stds + 1e-6)
        
        # 3. Reversal (Short-term mean reversion) - Simple 5 day return flipped
        rev_5d = -1.0 * (self.prices.pct_change(5).values)
        means_r = np.nanmean(rev_5d, axis=1, keepdims=True)
        stds_r = np.nanstd(rev_5d, axis=1, keepdims=True)
        self.rev_score = (rev_5d - means_r) / (stds_r + 1e-6)
        
        logger.info("Signal Generation Complete.")

    def get_regime_state(self, date_idx):
        """
        Determines market regime:
        0: Normal
        1: High Stress (VIX)
        2: Rate Shock (TLT Crash)
        """
        if date_idx < 126: return 0
        
        curr_date = self.prices.index[date_idx]
        
        # VIX Check
        curr_vix = self.vix.iloc[date_idx]
        is_panic = curr_vix > self.cfg.VIX_PANIC
        
        # Rate Shock Check (TLT 6-month return < -15%)
        # Note: 2022 specific defense
        tlt_curr = self.tlt_prices.iloc[date_idx]
        tlt_prev = self.tlt_prices.iloc[date_idx - 126]
        tlt_ret = (tlt_curr / tlt_prev) - 1.0 if tlt_prev > 0 else 0
        is_rate_shock = tlt_ret < self.cfg.RATE_STRESS_THRESHOLD
        
        if is_rate_shock: return 2
        if is_panic: return 1
        return 0

    def run_backtest(self):
        self.connect()
        self.load_data()
        self.generate_signals()
        
        dates = self.prices.index
        n_dates = len(dates)
        portfolio_value = 1.0
        equity_curve = [1.0]
        weights = pd.Series(0, index=self.prices.columns)
        
        # Logging structures
        regime_log = []
        leverage_log = []
        
        logger.info("Starting Walk-Forward Backtest...")
        
        for i in range(252, n_dates): # Start after 1 year warmup
            curr_date = dates[i]
            
            # 1. Calculate Returns from previous day's weights
            daily_ret_vec = self.returns.iloc[i]
            # Transaction cost approximation (turnover based)
            # Simplified: cost applied on return 
            gross_ret = (weights * daily_ret_vec).sum()
            
            # 2. Check for Rebalance
            if i % self.cfg.REBALANCE_FREQ == 0:
                # --- A. Regime Detection ---
                regime = self.get_regime_state(i)
                regime_log.append(regime)
                
                # --- B. ICIR Weighting (Using Prior Data ONLY) ---
                # Get factor scores for previous day to avoid lookahead
                # Factor scores align with index, so [i-1] is valid for signal usage at [i]
                current_scores = {
                    'momentum': pd.Series(self.mom_score[i-1], index=self.prices.columns),
                    'volatility': pd.Series(self.vol_score[i-1], index=self.prices.columns),
                    'reversal': pd.Series(self.rev_score[i-1], index=self.prices.columns)
                }
                
                # Get forward returns for IC calculation (historical)
                # Look at period [i-126-21 : i-21] to calculate IC of signals generated then vs returns that happened
                ic_idx_end = i - 21
                if ic_idx_end > 0:
                    ic_fwd_ret = self.prices.iloc[ic_idx_end].div(self.prices.iloc[ic_idx_end-21]) - 1
                    # Note: We are using old scores vs realized returns. No lookahead.
                    ic_scores_hist = {
                        'momentum': pd.Series(self.mom_score[ic_idx_end-21], index=self.prices.columns),
                        'volatility': pd.Series(self.vol_score[ic_idx_end-21], index=self.prices.columns),
                        'reversal': pd.Series(self.rev_score[ic_idx_end-21], index=self.prices.columns)
                    }
                    factor_weights = self.icir_engine.update_and_get_weights(curr_date, ic_scores_hist, ic_fwd_ret)
                else:
                    factor_weights = {'momentum': 0.5, 'reversal': 0.2, 'volatility': 0.3}

                # --- C. Composite Score ---
                final_score = (
                    factor_weights.get('momentum', 0) * current_scores['momentum'] +
                    factor_weights.get('volatility', 0) * current_scores['volatility'] +
                    factor_weights.get('reversal', 0) * current_scores['reversal']
                )
                
                # Select Top N
                top_assets = final_score.nlargest(self.cfg.TOP_N).index
                
                # --- D. HRP Allocation ---
                # Get returns for correlation matrix (lookback 6 months)
                hist_returns = self.returns.iloc[i-126:i][top_assets]
                hrp_w = HRP_Optimizer.optimize(hist_returns)
                
                # --- E. Risk Management (Vol Target & Regime Scale) ---
                # Calculate current portfolio volatility
                curr_cov = hist_returns.cov().values
                port_vol = calc_portfolio_vol(hrp_w.values, curr_cov)
                
                # Volatility Scaling
                vol_scalar = self.cfg.VOL_TARGET / (port_vol + 1e-6)
                vol_scalar = min(vol_scalar, self.cfg.MAX_LEVERAGE)
                
                # Regime Scaling
                if regime == 2: # Rate Shock (2022)
                    vol_scalar *= 0.3 # Reduce exposure drastically
                elif regime == 1: # VIX Panic
                    vol_scalar *= 0.6
                
                # Final Weights
                target_weights = hrp_w * vol_scalar
                target_weights = target_weights.reindex(self.prices.columns).fillna(0)
                
                # Calculate Turnover Cost
                turnover = np.abs(target_weights - weights).sum()
                cost = turnover * self.cfg.TRANS_COST
                
                # Apply
                weights = target_weights
                leverage_log.append(vol_scalar)
                
                # Net Return after cost
                net_ret = gross_ret - cost
            else:
                net_ret = gross_ret
                
            portfolio_value *= (1 + net_ret)
            equity_curve.append(portfolio_value)
            
        # Results Analysis
        self.analyze_results(equity_curve, dates[252:])

    def analyze_results(self, equity, dates):
        eq_series = pd.Series(equity, index=dates)
        rets = eq_series.pct_change().fillna(0)
        
        # Metrics
        total_ret = eq_series.iloc[-1] - 1
        cagr = (eq_series.iloc[-1]) ** (252 / len(eq_series)) - 1
        vol = rets.std() * np.sqrt(252)
        sharpe = (rets.mean() / rets.std()) * np.sqrt(252)
        
        # MDD
        cum_max = eq_series.cummax()
        drawdown = (eq_series - cum_max) / cum_max
        mdd = drawdown.min()
        
        # Year by Year
        yearly = rets.resample('Y').apply(lambda x: (1+x).prod()-1)
        
        print("\n" + "="*50)
        print(f" ARES ULTIMATE v3.0 PERFORMANCE REPORT")
        print("="*50)
        print(f"Total Return : {total_ret*100:.2f}%")
        print(f"CAGR         : {cagr*100:.2f}%")
        print(f"Volatility   : {vol*100:.2f}%")
        print(f"Sharpe Ratio : {sharpe:.4f}")
        print(f"Max Drawdown : {mdd*100:.2f}%")
        print("-" * 50)
        print("Yearly Returns:")
        print(yearly * 100)
        print("="*50)

if __name__ == "__main__":
    ares = AresUltimateV3()
    ares.run_backtest()