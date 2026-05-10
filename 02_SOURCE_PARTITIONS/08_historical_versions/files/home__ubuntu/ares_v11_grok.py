import numpy as np
import pandas as pd
import numba as nb
from numba import njit, prange
import yfinance as yf
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import pairwise_distances
import warnings
warnings.filterwarnings('ignore')

# Global constants
TC_TOTAL = 0.005  # 50bps roundtrip transaction cost
TOP_K = 20
REBALANCE_DAYS = 21  # Monthly rebalance
DD_THRESHOLD = -0.15
CONSEC_LIMIT = 4
LOSS_SCALE_MIN = 0.3
RECOVERY_RATE = 0.1
LOOKBACK_ICIR = 252  # 1 year for ICIR
VOL_TARGET = 0.15  # Annual vol target ~15%
CVAR_ALPHA = 0.05  # 5% CVaR
REGIME_LOOKBACK = 126  # 6 months for regime features

# Factor names
FACTORS = ['mom_12_1', 'mom_6_1', 'mom_3_1', 'reversal', 'low_vol']

# Regime leverage caps
LEVERAGE_CAPS_BASE = np.array([2.5, 1.5, 0.8, 0.5, 0.0])  # BULL=0, NORMAL=1, CAUTION=2, TIGHTENING=3, CRISIS=4

@njit(cache=True)
def compute_momentum(returns, lookback):
    """Compute momentum factor: log returns over lookback periods."""
    n, m = returns.shape
    mom = np.full((n, m), np.nan)
    for i in range(lookback, n):
        mom[i] = np.log(1 + returns[i-lookback:i].sum(axis=0))
    return mom

@njit(cache=True)
def compute_reversal(returns, short=5, long=1):
    """Short-term reversal: - (short-term mom)."""
    n, m = returns.shape
    rev = np.full((n, m), np.nan)
    short_ret = np.zeros(m)
    for i in range(short, n):
        short_ret = returns[i-short:i].sum(axis=0)
        rev[i] = -short_ret
    return rev

@njit(cache=True)
def compute_low_vol(returns, vol_lookback=63):
    """Low volatility: inverse of rolling std."""
    n, m = returns.shape
    low_vol = np.full((n, m), np.nan)
    for i in range(vol_lookback, n):
        vol = np.std(returns[i-vol_lookback:i], axis=0)
        low_vol[i] = 1.0 / (vol + 1e-8)
    return low_vol

@njit(cache=True)
def zscore_normalize(signals):
    """Z-score normalization per column."""
    n, m = signals.shape
    zsig = signals.copy()
    for j in range(m):
        valid = ~np.isnan(zsig[:, j])
        if valid.sum() > 10:
            mu = np.mean(zsig[valid, j])
            std = np.std(zsig[valid, j])
            if std > 0:
                zsig[valid, j] = (zsig[valid, j] - mu) / std
    return zsig

def get_sp500_tickers():
    """Fetch current S&P500 tickers."""
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    tables = pd.read_html(url)
    sp500 = tables[0]
    return [ticker.replace('.', '-') for ticker in sp500['Symbol'].tolist()[:100]]  # Top 100 for speed

def download_data(start='2015-01-01', end='2024-12-31', tickers=None):
    """Download adjusted close prices and macro data."""
    if tickers is None:
        tickers = get_sp500_tickers()
    
    # Stock data
    data = yf.download(tickers, start=start, end=end, auto_adjust=True)['Close']
    data = data.dropna(axis=1, thresh=len(data)*0.8)
    
    # Macro proxies
    vix = yf.download('^VIX', start=start, end=end)['Close']
    spy = yf.download('SPY', start=start, end=end)['Close']
    hyg = yf.download('HYG', start=start, end=end)['Close']  # High Yield ETF
    tlt = yf.download('TLT', start=start, end=end)['Close']  # Long Treasury
    tnx = yf.download('^TNX', start=start, end=end)['Close'] / 100  # 10Y Yield
    
    # Align dates
    common_idx = data.index.intersection(vix.index).intersection(spy.index)
    data = data.loc[common_idx]
    vix, spy, hyg, tlt, tnx = vix.loc[common_idx], spy.loc[common_idx], hyg.loc[common_idx], tlt.loc[common_idx], tnx.loc[common_idx]
    
    # Daily returns
    stock_returns = data.pct_change().dropna(how='all').fillna(0).values
    
    return {
        'returns': stock_returns,
        'vix': vix.values,
        'spy_ma200': spy.rolling(200).mean().fillna(method='bfill').values,
        'credit_spread': (hyg / tlt - 1).fillna(0).values,
        'tnx_mom': tnx.pct_change(63).fillna(0).values  # 3M yield mom
    }

def compute_factors(returns, dates_len):
    """Compute all factors."""
    n, m = returns.shape
    factors = np.full((n, len(FACTORS), m), np.nan)
    
    # Momenta
    factors[:, 0] = compute_momentum(returns, 252)  # 12M
    factors[:, 1] = compute_momentum(returns, 126)  # 6M
    factors[:, 2] = compute_momentum(returns, 21)   # 1M
    factors[:, 3] = compute_reversal(returns)
    factors[:, 4] = compute_low_vol(returns)
    
    return factors

def compute_icir(factors, returns, lookback=LOOKBACK_ICIR):
    """Rolling ICIR per factor per regime."""
    n, f, m = factors.shape
    icir = np.full((n, f), np.nan)
    
    for i in range(lookback, n):
        for k in range(f):
            past_factors = factors[i-lookback:i, k]
            future_rets = returns[i-lookback+1:i+1]
            
            valid = (~np.isnan(past_factors).any(axis=0)) & (~np.isnan(future_rets[:, 0]).any())  # Simplified
            if valid.sum() > 20:
                ranks_f = np.argsort(np.argsort(past_factors[-1, valid]))
                ranks_r = np.argsort(np.argsort(future_rets[-1, valid]))
                ic = np.corrcoef(ranks_f, ranks_r)[0,1]
                icir[i, k] = ic / (np.std(np.full(lookback, ic)) + 1e-8) if lookback > 1 else ic
    
    return zscore_normalize(icir)

def detect_regimes(vix, spy_ma200, credit_spread, tnx_mom, lookback=REGIME_LOOKBACK):
    """Improved regime detection: 5 regimes."""
    n = len(vix)
    regimes = np.zeros(n, dtype=np.int32)  # 0:BULL,1:NORMAL,2:CAUTION,3:TIGHTENING,4:CRISIS
    
    vix_norm = (vix - np.roll(vix, 10)) / (np.std(vix[-lookback:]) + 1e-8)  # VIX slope proxy
    vix_level = vix / spy_ma200
    cred_level = credit_spread
    rate_ramp = tnx_mom > np.percentile(tnx_mom, 70)
    
    score = np.zeros(n)
    score += (vix_level > 0.3).astype(float) * 1.0
    score += (vix_norm > 0).astype(float) * 0.8
    score += (cred_level > np.percentile(credit_spread, 70)).astype(float) * 0.7
    score += rate_ramp.astype(float) * 1.2  # Rate hike penalty
    
    regimes[score < 0.5] = 0  # BULL
    regimes[(score >= 0.5) & (score < 1.5)] = 1  # NORMAL
    regimes[(score >= 1.5) & (score < 2.5)] = 2  # CAUTION
    regimes[(score >= 2.5) & (score < 3.5)] = 3  # TIGHTENING (rate hikes)
    regimes[score >= 3.5] = 4  # CRISIS
    
    return regimes

def dynamic_factor_weights(icir, regimes, regime_id):
    """Dynamic weights based on ICIR per regime."""
    regime_icir = icir[regimes == regime_id][-252:]  # Recent 1Y
    if len(regime_icir) < 50:
        return np.array([0.25, 0.25, 0.2, 0.15, 0.15])  # Default
    
    mean_icir = np.nanmean(regime_icir, axis=0)
    weights = np.nan_to_num(mean_icir)
    weights /= weights.sum()
    return weights

def sector_rotation(signals, sectors, regime):
    """Sector rotation for defense in tightening regimes."""
    if regime < 3:  # No rotation in bull/normal/caution
        return signals
    # Boost defensives/value in tightening: assume sector labels 0-10 (hardcoded)
    sector_weights = np.array([0.8, 1.0, 1.2, 1.1, 0.9, 1.0, 1.3, 1.0, 0.9, 1.2, 1.1])  # Utilities, Staples up; Tech down
    sector_adj = np.zeros_like(signals)
    for s in range(11):  # 11 GICS sectors
        sector_adj[sectors == s] = signals[sectors == s] * sector_weights[s]
    return sector_adj

# HRP Implementation
def get_distance_matrix(cov):
    """Distance from corr matrix."""
    corr = np.corrcoef(cov)
    np.fill_diagonal(corr, 1)
    dist = ((1 - corr) / 2.0)**0.5
    return dist

def hrp_alloc(cov, max_iter=10):
    """Hierarchical Risk Parity allocation."""
    n = cov.shape[0]
    dist = get_distance_matrix(cov)
    link = linkage(squareform(dist), method='single')
    
    # Clustering
    cluster_idx = fcluster(link, n, criterion='maxclust')
    
    # Recursive bisection
    def rec_bisect(w, cidx):
        if len(np.unique(cidx)) == 1:
            return w
        sub_w = w.copy()
        i = np.argmax([np.sum(w[cidx == c]) for c in np.unique(cidx)])
        c1, c2 = np.unique(cidx)
        if i == 0:
            c1, c2 = c2, c1
        w1 = rec_bisect(w[cidx == c1], cidx[cidx == c1])
        w2 = rec_bisect(w[cidx == c2], cidx[cidx == c2])
        sub_w[cidx == c1] = w1 * np.sum(w[cidx == c1])
        sub_w[cidx == c2] = w2 * np.sum(w[cidx == c2])
        return sub_w
    
    w = np.ones(n) / n
    weights = rec_bisect(w, cluster_idx)
    return weights / weights.sum()

@njit(cache=True)
def compute_rolling_cov(returns, lookback=63):
    """Fast rolling covariance (simplified to var for speed, multi-asset slow)."""
    n, m = returns.shape
    cov = np.zeros((n, m, m))
    for i in range(lookback, n):
        win = returns[i-lookback:i]
        for j in prange(m):
            for k in prange(m):
                cov[i, j, k] = np.mean((win[:, j] - np.mean(win[:, j])) * (win[:, k] - np.mean(win[:, k])))
    return cov

@njit(cache=True)
def compute_rolling_vol(port_returns, lookback=21):
    """Rolling portfolio volatility."""
    n = len(port_returns)
    vol = np.zeros(n)
    for i in range(lookback, n):
        vol[i] = np.std(port_returns[i-lookback:i]) * np.sqrt(252)
    return vol

@njit(cache=True)
def compute_cvar(port_returns, lookback=252, alpha=CVAR_ALPHA):
    """Rolling CVaR."""
    n = len(port_returns)
    cvar = np.zeros(n)
    for i in range(lookback, n):
        tail = np.sort(port_returns[i-lookback:i])[:int(lookback * alpha)]
        cvar[i] = np.mean(tail)
    return cvar

def generate_signals(factors, icir, regimes, sectors=None):
    """Generate combined signals with dynamic weights and HRP."""
    n, f, m = factors.shape
    signals = np.full((n, m), np.nan)
    
    # Sectors dummy: assume 0-10 uniform for demo
    if sectors is None:
        sectors = np.random.randint(0, 11, m)
    
    cov_lookback = 63
    for i in range(cov_lookback, n):
        regime = regimes[i]
        weights = dynamic_factor_weights(icir, regimes[:i+1], regime)
        
        # Combine factors
        comb_sig = np.zeros(m)
        for k in range(f):
            comb_sig += weights[k] * factors[i, k]
        
        # Sector rotation
        comb_sig = sector_rotation(comb_sig, sectors, regime)
        
        # Top K candidates
        valid_mask = ~np.isnan(comb_sig)
        if valid_mask.sum() >= TOP_K:
            cand_idx = np.argsort(-comb_sig[valid_mask])[:TOP_K]
            cand_signals = comb_sig[valid_mask][cand_idx]
            
            # HRP: need cov, but njit limit, approximate with equal for jit compat, full HRP outside
            # For speed, use equal weights here, HRP in backtest precompute
            signals[i, valid_mask][cand_idx] = cand_signals / TOP_K
        else:
            signals[i] = 0.0
    
    return zscore_normalize(signals), sectors

@njit(cache=True)
def fast_backtest_v11(signals, returns, regimes, leverage_caps, tc_total, dd_threshold, 
                      consec_limit, loss_scale_min, recovery_rate, vol_target, cvar_alpha):
    """Enhanced backtest with CVaR, Vol targeting, HRP approx."""
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates)
    current_weights = np.zeros(n_assets)
    cum_ret = 1.0
    peak = 1.0
    dd_scale = 1.0
    consecutive_losses = 0
    loss_scale = 1.0
    vol_scale = 1.0
    cvar_scale = 1.0
    port_rets_buffer = np.zeros(252)  # Rolling window
    
    for i in range(n_dates):
        if cum_ret > peak:
            peak = cum_ret
        dd = (cum_ret / peak) - 1.0
        
        # DD scale
        if dd < dd_threshold:
            dd_scale = max(0.0, 1.0 + dd / dd_threshold * 1.5)  # Stronger
        else:
            dd_scale = min(1.0, dd_scale * 0.95 + 0.05)
        
        # Consec losses
        if i > 0 and portfolio_returns[i-1] < -0.005:
            consecutive_losses += 1
        elif i > 0 and portfolio_returns[i-1] > 0.005:
            consecutive_losses = 0
        
        if consecutive_losses >= consec_limit:
            loss_scale = max(loss_scale_min, loss_scale * 0.85)
        else:
            loss_scale = min(1.0, loss_scale + recovery_rate)
        
        # Vol targeting approx
        if i >= 21:
            recent_vol = np.std(portfolio_returns[i-21:i]) * np.sqrt(252)
            vol_scale = vol_target / (recent_vol + 1e-6)
            vol_scale = np.clip(vol_scale, 0.5, 2.0)
        
        # CVaR approx (simple tail mean)
        if i >= 252:
            tail = np.sort(portfolio_returns[i-252:i])[:int(252 * cvar_alpha)]
            cvar = np.mean(tail)
            cvar_scale = max(0.5, 1.0 + cvar * 2.0)
        
        regime_lever = leverage_caps[regimes[i]]
        leverage = regime_lever * dd_scale * loss_scale * vol_scale * cvar_scale
        
        if i % REBALANCE_DAYS == 0:
            day_signals = signals[i]
            valid_mask = ~np.isnan(day_signals) & (np.abs(day_signals) > 0.1)
            if valid_mask.sum() >= 10:  # Min for HRP approx
                sorted_idx = np.argsort(-day_signals[valid_mask])
                top_idx = np.where(valid_mask)[0][sorted_idx[:TOP_K]]
                new_weights = np.zeros(n_assets)
                ew = 1.0 / TOP_K
                for idx in top_idx:
                    new_weights[idx] = ew
                turnover = np.sum(np.abs(new_weights - current_weights))
                total_cost = turnover * tc_total
                current_weights = new_weights
            else:
                total_cost = 0.0
        else:
            total_cost = 0.0
        
        # Portfolio return
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0:
                port_ret += current_weights[j] * returns[i, j]
        
        portfolio_returns[i] = port_ret * leverage - total_cost
        
        # Update buffer for vol/cvar
        if i < 252:
            port_rets_buffer[i] = portfolio_returns[i]
        else:
            port_rets_buffer = np.roll(port_rets_buffer, -1)
            port_rets_buffer[-1] = portfolio_returns[i]
        
        cum_ret *= (1.0 + portfolio_returns[i])
    
    return portfolio_returns

def compute_metrics(returns):
    """Compute Sharpe, MDD, etc."""
    returns = pd.Series(returns)
    ann_ret = np.sqrt(252) * returns.mean()
    ann_vol = np.sqrt(252) * returns.std()
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cum_ret = (1 + returns).prod()
    peak = (1 + returns).expanding().max()
    dd = (cum_ret / peak) - 1
    mdd = dd.min()
    return {
        'Sharpe': sharpe,
        'Ann Return': ann_ret,
        'Ann Vol': ann_vol,
        'MDD': mdd,
        'Total Return': cum_ret - 1
    }

def main():
    """Full backtest pipeline."""
    print("Downloading data...")
    data = download_data(start='2015-12-01', end='2024-10-01')
    
    returns = data['returns']
    vix = data['vix']
    spy_ma200 = data['spy_ma200']
    credit_spread = data['credit_spread']
    tnx_mom = data['tnx_mom']
    
    n_dates = len(returns)
    print(f"Data shape: {returns.shape}")
    
    print("Computing factors...")
    factors = compute_factors(returns, n_dates)
    
    print("Computing ICIR...")
    icir = compute_icir(factors, returns)
    
    print("Detecting regimes...")
    regimes = detect_regimes(vix, spy_ma200, credit_spread, tnx_mom)
    
    print("Generating signals...")
    signals, sectors = generate_signals(factors, icir, regimes)
    
    print("Running enhanced backtest...")
    leverage_caps = LEVERAGE_CAPS_BASE[regimes.astype(np.int32)]
    port_returns = fast_backtest_v11(
        signals, returns, regimes, TC_TOTAL, DD_THRESHOLD, CONSEC_LIMIT,
        LOSS_SCALE_MIN, RECOVERY_RATE, VOL_TARGET, CVAR_ALPHA
    )
    
    metrics = compute_metrics(port_returns)
    print("\n=== ARES v11 Enhanced Performance ===")
    for k, v in metrics.items():
        print(f"{k}: {v:.4f}")
    
    # Yearly breakdown
    df = pd.DataFrame({'port': port_returns}, index=pd.date_range('2016-01-01', periods=n_dates, freq='B')[:n_dates])
    yearly = df.resample('Y').apply(lambda x: (1+x).prod() - 1)
    print("\nYearly Returns:")
    print(yearly)
    
    # IS/OOS
    is_end = np.where(pd.date_range('2016-01-01', periods=n_dates, freq='B') < '2021-01-01')[0][-1]
    is_metrics = compute_metrics(port_returns[:is_end])
    oos_metrics = compute_metrics(port_returns[is_end:])
    print(f"\nIS (2016-2020) Sharpe: {is_metrics['Sharpe']:.2f}")
    print(f"OOS (2021-2024) Sharpe: {oos_metrics['Sharpe']:.2f}")

if __name__ == "__main__":
    main()