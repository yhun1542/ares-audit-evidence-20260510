#!/usr/bin/env python3
import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
import warnings
import logging
import json

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"

@njit(parallel=True, cache=True)
def fast_momentum(prices, lookback, skip=1):
    n_dates, n_assets = prices.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback + skip, n_dates):
        for j in range(n_assets):
            if prices[i - skip, j] > 0 and prices[i - lookback, j] > 0:
                result[i, j] = prices[i - skip, j] / prices[i - lookback, j] - 1
    return result

@njit(parallel=True, cache=True)
def fast_volatility(returns, lookback):
    n_dates, n_assets = returns.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(lookback, n_dates):
        for j in range(n_assets):
            window = returns[i-lookback:i, j]
            valid = ~np.isnan(window)
            if valid.sum() >= lookback // 2:
                result[i, j] = np.nanstd(window) * np.sqrt(252)
    return result

@njit(parallel=True, cache=True)
def fast_rank_normalize(data):
    n_dates, n_assets = data.shape
    result = np.full((n_dates, n_assets), np.nan, dtype=np.float64)
    for i in prange(n_dates):
        row = data[i, :]
        valid_mask = ~np.isnan(row)
        n_valid = valid_mask.sum()
        if n_valid > 0:
            valid_vals = row[valid_mask]
            sorted_idx = np.argsort(valid_vals)
            ranks = np.empty(n_valid, dtype=np.float64)
            for k, idx in enumerate(sorted_idx):
                ranks[idx] = (k + 1) / n_valid
            result[i, valid_mask] = ranks
    return result

@njit(cache=True)
def fast_backtest_v5(signals, returns, leverage_caps, top_k, rebalance_days, tc_total):
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    for i in range(n_dates):
        if i % rebalance_days == 0:
            day_signals = signals[i, :]
            valid_mask = ~np.isnan(day_signals)
            if valid_mask.sum() >= top_k:
                sorted_idx = np.argsort(-day_signals)
                new_weights = np.zeros(n_assets, dtype=np.float64)
                count = 0
                for idx in sorted_idx:
                    if valid_mask[idx] and count < top_k:
                        new_weights[idx] = 1.0 / top_k
                        count += 1
                turnover = np.sum(np.abs(new_weights - current_weights))
                total_cost = turnover * tc_total / 2
                current_weights = new_weights
            else:
                total_cost = 0.0
        else:
            total_cost = 0.0
        
        day_return = returns[i, :]
        leverage = leverage_caps[i]
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        portfolio_returns[i] = port_ret * leverage - total_cost
    return portfolio_returns

def calc_metrics(returns):
    cum_ret = (1 + returns).cumprod()
    total_ret = cum_ret.iloc[-1] - 1
    n_years = len(returns) / 252
    annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
    vol = returns.std() * np.sqrt(252)
    sharpe = annual_ret / (vol + 1e-10)
    max_cum = cum_ret.cummax()
    dd = cum_ret / max_cum - 1
    mdd = dd.min()
    return {"sharpe": sharpe, "mdd": mdd, "annual_return": annual_ret, "total_return": total_ret}

def main():
    logger.info("ARES v5 통합 버전 - Multi-Source Regime + Hysteresis")
    
    UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "UNH", "JNJ",
        "JPM", "V", "PG", "XOM", "HD", "CVX", "MA", "ABBV", "MRK", "LLY",
        "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "XLF", "XLK", "XLE", "XLV"]
    
    conn = sqlite3.connect(DB_PATH)
    symbols_str = "'" + "','".join(UNIVERSE) + "'"
    query = f"SELECT date, symbol, adj_close as close FROM daily_ohlcv WHERE symbol IN ({symbols_str}) AND date >= '2016-01-01' AND date <= '2024-12-31' ORDER BY date, symbol"
    df = pd.read_sql(query, conn)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset=["date", "symbol"], keep="last")
    prices = df.pivot(index="date", columns="symbol", values="close").ffill().bfill()
    
    vix_query = "SELECT date, close as vix FROM vix WHERE date >= '2016-01-01' AND date <= '2024-12-31' ORDER BY date"
    vix_df = pd.read_sql(vix_query, conn)
    vix_df["date"] = pd.to_datetime(vix_df["date"])
    vix = vix_df.drop_duplicates(subset=["date"], keep="last").set_index("date")["vix"].ffill()
    conn.close()
    
    returns = prices.pct_change().fillna(0)
    prices_arr = prices.values.astype(np.float64)
    returns_arr = returns.values.astype(np.float64)
    
    mom_12_1 = fast_momentum(prices_arr, 252, 21)
    mom_6_1 = fast_momentum(prices_arr, 126, 21)
    low_vol = fast_volatility(returns_arr, 60)
    
    factors = {
        "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
        "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
        "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
    }
    
    vix_lagged = vix.shift(1).reindex(prices.index).ffill()
    spy_ret = returns["SPY"]
    ma_short = spy_ret.rolling(5).mean().shift(1)
    ma_long = spy_ret.rolling(20).mean().shift(1)
    
    LEVERAGE_CAPS = {"CRISIS": 0.0, "TIGHTENING": 0.5, "INFLATION": 0.8, "NORMAL": 1.0, "BULL": 1.2}
    
    crisis_days = 0
    recovery_days = 0
    current_regime = "NORMAL"
    leverage_caps = []
    regimes = []
    
    for date in prices.index:
        vix_val = vix_lagged.get(date, 20)
        ma_s = ma_short.get(date, 0) if date in ma_short.index else 0
        ma_l = ma_long.get(date, 0) if date in ma_long.index else 0
        
        crisis_signal = vix_val > 28
        recovery_signal = vix_val < 22
        
        if crisis_signal:
            crisis_days += 1
            recovery_days = 0
        elif recovery_signal:
            recovery_days += 1
            crisis_days = 0
        else:
            crisis_days = max(0, crisis_days - 1)
            recovery_days = max(0, recovery_days - 1)
        
        if crisis_days >= 3:
            current_regime = "CRISIS"
        elif recovery_days >= 5 and current_regime == "CRISIS":
            current_regime = "NORMAL"
        elif current_regime != "CRISIS":
            if vix_val > 22:
                current_regime = "TIGHTENING"
            elif vix_val < 15 and (pd.isna(ma_s) or ma_s > 0):
                current_regime = "BULL"
            else:
                current_regime = "NORMAL"
        
        if current_regime in ["NORMAL", "BULL"]:
            if not pd.isna(ma_s) and not pd.isna(ma_l) and ma_s < ma_l and ma_s < 0:
                leverage = LEVERAGE_CAPS[current_regime] * 0.5
            else:
                leverage = LEVERAGE_CAPS[current_regime]
        else:
            leverage = LEVERAGE_CAPS[current_regime]
        
        regimes.append(current_regime)
        leverage_caps.append(leverage)
    
    regimes = pd.Series(regimes, index=prices.index)
    leverage_caps = np.array(leverage_caps, dtype=np.float64)
    
    regime_counts = regimes.value_counts()
    logger.info("레짐 분포:")
    for regime, count in regime_counts.items():
        logger.info(f"  {regime}: {count}일 ({count/len(regimes)*100:.1f}%)")
    
    factor_weights = {"mom_12_1": 0.5, "mom_6_1": 0.3, "low_vol": 0.2}
    combined = sum(factors[n] * w for n, w in factor_weights.items())
    
    common_cols = combined.columns.intersection(returns.columns)
    signals_arr = combined[common_cols].values.astype(np.float64)
    returns_test = returns[common_cols].values.astype(np.float64)
    
    cost_scenarios = [{"name": "비용 없음", "tc": 0.0}, {"name": "10bps", "tc": 0.001}, {"name": "30bps", "tc": 0.003}, {"name": "50bps", "tc": 0.005}]
    
    logger.info("\n비용 시나리오별 성과:")
    for scenario in cost_scenarios:
        port_rets = fast_backtest_v5(signals_arr, returns_test, leverage_caps, 20, 5, scenario["tc"])
        port_rets_series = pd.Series(port_rets, index=prices.index)
        full_m = calc_metrics(port_rets_series)
        is_m = calc_metrics(port_rets_series[port_rets_series.index <= "2020-12-31"])
        oos_m = calc_metrics(port_rets_series[port_rets_series.index >= "2021-01-01"])
        logger.info(f"  {scenario['name']}: Full Sharpe={full_m['sharpe']:.4f}, MDD={full_m['mdd']:.2%}, IS={is_m['sharpe']:.4f}, OOS={oos_m['sharpe']:.4f}")
    
    logger.info("\n연도별 성과 (50bps):")
    port_rets = fast_backtest_v5(signals_arr, returns_test, leverage_caps, 20, 5, 0.005)
    port_rets_series = pd.Series(port_rets, index=prices.index)
    for year in range(2016, 2025):
        year_rets = port_rets_series[port_rets_series.index.year == year]
        if len(year_rets) > 0:
            m = calc_metrics(year_rets)
            logger.info(f"  {year}: Sharpe={m['sharpe']:.2f}, Return={m['total_return']:.2%}, MDD={m['mdd']:.2%}")

if __name__ == "__main__":
    main()
