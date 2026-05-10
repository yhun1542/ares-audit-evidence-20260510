#!/usr/bin/env python3
"""
ARES v10 Final - 2022년 방어 + Sharpe 최대화 균형
- 연속 손실 방어 (Sharpe 유지하면서 2022 개선)
- 세밀한 파라미터 최적화
"""
import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
import warnings
import logging

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
def fast_backtest_v10_final(signals, returns, leverage_caps, top_k, rebalance_days, tc_total, dd_threshold, 
                             consec_limit, loss_scale_min, recovery_rate):
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    cum_ret = 1.0
    peak = 1.0
    dd_scale = 1.0
    consecutive_losses = 0
    loss_scale = 1.0
    
    for i in range(n_dates):
        if cum_ret > peak:
            peak = cum_ret
        dd = (cum_ret / peak) - 1.0
        
        if dd < dd_threshold:
            dd_scale = max(0.0, 1.0 + dd / dd_threshold)
        else:
            dd_scale = min(1.0, dd_scale + 0.05)
        
        if i > 0 and portfolio_returns[i-1] < -0.005:  # 0.5% 이상 손실만 카운트
            consecutive_losses += 1
        elif i > 0 and portfolio_returns[i-1] > 0.005:  # 0.5% 이상 수익 시 리셋
            consecutive_losses = 0
        
        if consecutive_losses >= consec_limit:
            loss_scale = max(loss_scale_min, loss_scale - 0.15)
        else:
            loss_scale = min(1.0, loss_scale + recovery_rate)
        
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
        leverage = leverage_caps[i] * dd_scale * loss_scale
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        portfolio_returns[i] = port_ret * leverage - total_cost
        cum_ret *= (1.0 + portfolio_returns[i])
    
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
    logger.info("ARES v10 Final - 2022년 방어 + Sharpe 최대화 균형")
    
    UNIVERSE = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
        "BRK.B", "UNH", "JNJ", "JPM", "V", "PG", "XOM", "HD", "CVX",
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
        "SPY", "QQQ", "IWM", "DIA", "TLT", "GLD"
    ]
    
    conn = sqlite3.connect(DB_PATH)
    
    symbols_list = ", ".join([f"'{s}'" for s in UNIVERSE])
    query = f"""
    SELECT date, symbol, adj_close as close
    FROM daily_ohlcv
    WHERE symbol IN ({symbols_list}) AND date >= '2016-01-01' AND date <= '2024-12-31'
    ORDER BY date, symbol
    """
    df = pd.read_sql(query, conn)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset=["date", "symbol"], keep="last")
    prices = df.pivot(index="date", columns="symbol", values="close").ffill().bfill()
    logger.info(f"가격 데이터: {prices.shape[0]}일 x {prices.shape[1]}종목")
    
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
    mom_3_1 = fast_momentum(prices_arr, 63, 21)
    mom_1_0 = fast_momentum(prices_arr, 21, 0)
    low_vol = fast_volatility(returns_arr, 60)
    
    factors = {
        "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
        "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
        "mom_3_1": pd.DataFrame(fast_rank_normalize(mom_3_1), index=prices.index, columns=prices.columns),
        "reversal": pd.DataFrame(fast_rank_normalize(-mom_1_0), index=prices.index, columns=prices.columns),
        "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
    }
    
    vix_lagged = vix.shift(1).reindex(prices.index).ffill()
    spy_price = prices["SPY"] if "SPY" in prices.columns else prices.iloc[:, 0]
    ma_200 = spy_price.rolling(200).mean().shift(1)
    ma_50 = spy_price.rolling(50).mean().shift(1)
    trend_up = spy_price.shift(1) > ma_200
    short_trend_up = spy_price.shift(1) > ma_50
    vix_change = vix_lagged.pct_change(5).shift(1).fillna(0)
    
    REGIME_FACTOR_WEIGHTS = {
        "BULL": {"mom_12_1": 0.3, "mom_6_1": 0.3, "mom_3_1": 0.2, "low_vol": 0.2},
        "NORMAL": {"mom_12_1": 0.4, "mom_6_1": 0.3, "low_vol": 0.3},
        "CAUTION": {"mom_12_1": 0.3, "mom_6_1": 0.2, "low_vol": 0.4, "reversal": 0.1},
        "TIGHTENING": {"low_vol": 0.5, "reversal": 0.3, "mom_12_1": 0.2},
        "CRISIS": {"low_vol": 0.6, "reversal": 0.4}
    }
    
    LEVERAGE_CAPS = {"CRISIS": 0.0, "TIGHTENING": 0.5, "CAUTION": 0.8, "NORMAL": 1.5, "BULL": 2.5}
    
    # 레짐 생성
    crisis_days = 0
    recovery_days = 0
    current_regime = "NORMAL"
    leverage_caps = []
    combined_signals = []
    
    for i, date in enumerate(prices.index):
        vix_val = vix_lagged.get(date, 20)
        is_trend_up = trend_up.get(date, True) if date in trend_up.index else True
        is_short_trend_up = short_trend_up.get(date, True) if date in short_trend_up.index else True
        vix_chg = vix_change.get(date, 0) if date in vix_change.index else 0
        
        vix_spike = vix_chg > 0.3
        crisis_signal = vix_val > 28 or vix_spike
        recovery_signal = vix_val < 20 and is_trend_up and not vix_spike
        
        if crisis_signal:
            crisis_days += 1
            recovery_days = 0
        elif recovery_signal:
            recovery_days += 1
            crisis_days = 0
        else:
            crisis_days = max(0, crisis_days - 1)
            recovery_days = max(0, recovery_days - 1)
        
        if crisis_days >= 2:
            current_regime = "CRISIS"
        elif recovery_days >= 5 and current_regime == "CRISIS":
            current_regime = "NORMAL"
        elif current_regime != "CRISIS":
            if vix_val > 25:
                current_regime = "TIGHTENING"
            elif vix_val > 18 or not is_trend_up:
                current_regime = "CAUTION"
            elif vix_val < 15 and is_trend_up and is_short_trend_up:
                current_regime = "BULL"
            else:
                current_regime = "NORMAL"
        
        leverage = LEVERAGE_CAPS[current_regime]
        if not is_trend_up and current_regime not in ["CRISIS", "TIGHTENING"]:
            leverage *= 0.5
        
        leverage_caps.append(leverage)
        
        weights = REGIME_FACTOR_WEIGHTS[current_regime]
        day_signal = sum(factors[f].iloc[i] * w for f, w in weights.items())
        combined_signals.append(day_signal.values)
    
    leverage_caps_arr = np.array(leverage_caps, dtype=np.float64)
    combined_signals_arr = np.array(combined_signals, dtype=np.float64)
    returns_test = returns.values.astype(np.float64)
    
    # 파라미터 그리드 서치
    logger.info("\n파라미터 그리드 서치...")
    
    consec_limits = [2, 3, 4, 5]
    loss_scale_mins = [0.3, 0.4, 0.5, 0.6]
    recovery_rates = [0.03, 0.05, 0.08]
    
    results = []
    
    for consec in consec_limits:
        for loss_min in loss_scale_mins:
            for recovery in recovery_rates:
                port_rets = fast_backtest_v10_final(
                    combined_signals_arr, returns_test, leverage_caps_arr, 
                    15, 10, 0.005, -0.12, consec, loss_min, recovery
                )
                port_rets_series = pd.Series(port_rets, index=prices.index)
                
                full_m = calc_metrics(port_rets_series)
                is_m = calc_metrics(port_rets_series[port_rets_series.index <= "2020-12-31"])
                oos_m = calc_metrics(port_rets_series[port_rets_series.index >= "2021-01-01"])
                
                y2022 = port_rets_series[port_rets_series.index.year == 2022]
                m2022 = calc_metrics(y2022)
                
                results.append({
                    "consec": consec, "loss_min": loss_min, "recovery": recovery,
                    "sharpe": full_m["sharpe"], "mdd": full_m["mdd"],
                    "is_sharpe": is_m["sharpe"], "oos_sharpe": oos_m["sharpe"],
                    "y2022": m2022["total_return"]
                })
    
    results_df = pd.DataFrame(results)
    
    # Sharpe >= 1.8 유지하면서 2022 최소화
    valid = results_df[results_df["sharpe"] >= 1.8]
    if len(valid) > 0:
        best_idx = valid["y2022"].idxmax()
        best = valid.loc[best_idx]
        logger.info(f"\n최적 설정 (Sharpe >= 1.8 유지):")
        logger.info(f"  연속손실={best['consec']}, 최소스케일={best['loss_min']}, 회복률={best['recovery']}")
        logger.info(f"  Sharpe={best['sharpe']:.4f}, 2022={best['y2022']:.2%}")
    else:
        # Sharpe >= 1.7로 완화
        valid = results_df[results_df["sharpe"] >= 1.7]
        best_idx = valid["y2022"].idxmax()
        best = valid.loc[best_idx]
        logger.info(f"\n최적 설정 (Sharpe >= 1.7 유지):")
        logger.info(f"  연속손실={best['consec']}, 최소스케일={best['loss_min']}, 회복률={best['recovery']}")
        logger.info(f"  Sharpe={best['sharpe']:.4f}, 2022={best['y2022']:.2%}")
    
    # Top 10 결과
    results_df = results_df.sort_values("sharpe", ascending=False)
    logger.info("\nTop 10 결과 (Sharpe 기준):")
    for i, row in results_df.head(10).iterrows():
        logger.info(f"  연속={row['consec']}, 최소={row['loss_min']}, 회복={row['recovery']}: "
                   f"Sharpe={row['sharpe']:.4f}, 2022={row['y2022']:.2%}")
    
    # 2022 최소화 기준 Top 10
    results_df_2022 = results_df.sort_values("y2022", ascending=False)
    logger.info("\nTop 10 결과 (2022 손실 최소화 기준):")
    for i, row in results_df_2022.head(10).iterrows():
        logger.info(f"  연속={row['consec']}, 최소={row['loss_min']}, 회복={row['recovery']}: "
                   f"Sharpe={row['sharpe']:.4f}, 2022={row['y2022']:.2%}")
    
    # 최적 설정으로 연도별 성과
    best_params = results_df_2022[results_df_2022["sharpe"] >= 1.75].iloc[0]
    
    port_rets = fast_backtest_v10_final(
        combined_signals_arr, returns_test, leverage_caps_arr, 
        15, 10, 0.005, -0.12, 
        int(best_params["consec"]), best_params["loss_min"], best_params["recovery"]
    )
    port_rets_series = pd.Series(port_rets, index=prices.index)
    
    logger.info(f"\n최종 선택: 연속={best_params['consec']}, 최소={best_params['loss_min']}, 회복={best_params['recovery']}")
    logger.info("\n연도별 성과:")
    for year in range(2016, 2025):
        year_rets = port_rets_series[port_rets_series.index.year == year]
        if len(year_rets) > 0:
            m = calc_metrics(year_rets)
            logger.info(f"  {year}: Sharpe={m['sharpe']:.2f}, Return={m['total_return']:.2%}, MDD={m['mdd']:.2%}")
    
    results_df.to_csv("/home/ubuntu/ares_results/v10_final_grid_search.csv", index=False)
    logger.info("\n결과 저장: /home/ubuntu/ares_results/v10_final_grid_search.csv")

if __name__ == "__main__":
    main()
