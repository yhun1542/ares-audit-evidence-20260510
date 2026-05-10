#!/usr/bin/env python3
"""
ARES v6 - 펀더멘털 팩터 + 매크로 오버레이 통합
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
def fast_backtest_v6(signals, returns, leverage_caps, top_k, rebalance_days, tc_total, dd_threshold):
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    cum_ret = 1.0
    peak = 1.0
    dd_scale = 1.0
    
    for i in range(n_dates):
        if cum_ret > peak:
            peak = cum_ret
        dd = (cum_ret / peak) - 1.0
        
        if dd < dd_threshold:
            dd_scale = max(0.0, 1.0 + dd / dd_threshold)
        else:
            dd_scale = min(1.0, dd_scale + 0.05)
        
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
        leverage = leverage_caps[i] * dd_scale
        
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
    logger.info("ARES v6 - 펀더멘털 + 매크로 통합")
    
    # 확장된 유니버스 (100개)
    UNIVERSE = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "UNH", "JNJ",
        "JPM", "V", "PG", "XOM", "HD", "CVX", "MA", "ABBV", "MRK", "LLY",
        "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
        "CRM", "ORCL", "NKE", "DHR", "TXN", "NEE", "PM", "RTX", "HON", "UNP",
        "IBM", "QCOM", "LOW", "SPGI", "INTU", "CAT", "BA", "GE", "AMAT", "BKNG",
        "ADP", "MDLZ", "GILD", "ADI", "TJX", "ISRG", "VRTX", "REGN", "LRCX", "MU",
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "XLF", "XLK", "XLE", "XLV",
        "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC", "GLD", "SLV", "TLT",
        "IEF", "LQD", "HYG", "EEM", "EFA", "VWO", "VEA", "IEMG", "VNQ", "VNQI"
    ]
    
    conn = sqlite3.connect(DB_PATH)
    
    # 가격 데이터
    logger.info("가격 데이터 로드...")
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
    logger.info(f"  가격 데이터: {prices.shape[0]}일 x {prices.shape[1]}종목")
    
    # VIX
    vix_query = "SELECT date, close as vix FROM vix WHERE date >= '2016-01-01' AND date <= '2024-12-31' ORDER BY date"
    vix_df = pd.read_sql(vix_query, conn)
    vix_df["date"] = pd.to_datetime(vix_df["date"])
    vix = vix_df.drop_duplicates(subset=["date"], keep="last").set_index("date")["vix"].ffill()
    
    # 펀더멘털 데이터
    logger.info("펀더멘털 데이터 로드...")
    fund_query = f"""
    SELECT date, ticker, value_raw, quality_raw, gross_margin, net_margin, revenue_growth
    FROM fundamentals_pit_daily
    WHERE ticker IN ({symbols_list}) AND date >= '2016-01-01' AND date <= '2024-12-31'
    ORDER BY date, ticker
    """
    fund_df = pd.read_sql(fund_query, conn)
    fund_df["date"] = pd.to_datetime(fund_df["date"])
    
    if len(fund_df) > 0:
        fund_df = fund_df.drop_duplicates(subset=["date", "ticker"], keep="last")
        value_pivot = fund_df.pivot(index="date", columns="ticker", values="value_raw")
        quality_pivot = fund_df.pivot(index="date", columns="ticker", values="gross_margin")
        
        # 가격 데이터와 정렬
        value_pivot = value_pivot.reindex(prices.index).ffill()
        quality_pivot = quality_pivot.reindex(prices.index).ffill()
        
        # 공통 컬럼만 사용
        common_fund = value_pivot.columns.intersection(prices.columns)
        logger.info(f"  펀더멘털 데이터: {len(common_fund)}종목 매칭")
    else:
        value_pivot = None
        quality_pivot = None
        common_fund = []
        logger.info("  펀더멘털 데이터 없음")
    
    # 매크로 지표
    logger.info("매크로 데이터 로드...")
    macro_query = """
    SELECT date, indicator_name, value FROM macro_indicators 
    WHERE date >= '2016-01-01' AND date <= '2024-12-31'
    ORDER BY date
    """
    macro_df = pd.read_sql(macro_query, conn)
    macro_df["date"] = pd.to_datetime(macro_df["date"])
    
    if len(macro_df) > 0:
        macro_df = macro_df.drop_duplicates(subset=["date", "indicator_name"], keep="last")
        macro_pivot = macro_df.pivot(index="date", columns="indicator_name", values="value")
        macro_pivot = macro_pivot.reindex(prices.index).ffill()
        logger.info(f"  매크로 지표: {len(macro_pivot.columns)}개")
    else:
        macro_pivot = None
        logger.info("  매크로 데이터 없음")
    
    conn.close()
    
    returns = prices.pct_change().fillna(0)
    prices_arr = prices.values.astype(np.float64)
    returns_arr = returns.values.astype(np.float64)
    
    # 기술적 팩터
    logger.info("팩터 계산...")
    mom_12_1 = fast_momentum(prices_arr, 252, 21)
    mom_6_1 = fast_momentum(prices_arr, 126, 21)
    low_vol = fast_volatility(returns_arr, 60)
    
    factors = {
        "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
        "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
        "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
    }
    
    # 펀더멘털 팩터 추가
    if value_pivot is not None and len(common_fund) > 0:
        value_rank = pd.DataFrame(
            fast_rank_normalize(value_pivot[common_fund].values.astype(np.float64)),
            index=prices.index, columns=common_fund
        )
        quality_rank = pd.DataFrame(
            fast_rank_normalize(quality_pivot[common_fund].values.astype(np.float64)),
            index=prices.index, columns=common_fund
        )
        
        # 전체 유니버스로 확장 (없는 종목은 0.5)
        for col in prices.columns:
            if col not in value_rank.columns:
                value_rank[col] = 0.5
            if col not in quality_rank.columns:
                quality_rank[col] = 0.5
        
        factors["value"] = value_rank[prices.columns]
        factors["quality"] = quality_rank[prices.columns]
        logger.info("  펀더멘털 팩터 추가 완료")
    
    # 레짐 감지
    logger.info("레짐 감지...")
    vix_lagged = vix.shift(1).reindex(prices.index).ffill()
    spy_price = prices["SPY"] if "SPY" in prices.columns else prices.iloc[:, 0]
    ma_200 = spy_price.rolling(200).mean().shift(1)
    trend_up = spy_price.shift(1) > ma_200
    
    # 매크로 오버레이
    if macro_pivot is not None and "HY_SPREAD" in macro_pivot.columns:
        hy_spread = macro_pivot["HY_SPREAD"].shift(1).ffill()
        has_hy = True
        logger.info("  HY Spread 오버레이 활성화")
    else:
        hy_spread = pd.Series(4.0, index=prices.index)
        has_hy = False
    
    LEVERAGE_CAPS = {"CRISIS": 0.0, "TIGHTENING": 0.3, "CAUTION": 0.5, "NORMAL": 0.8, "BULL": 1.2}
    
    crisis_days = 0
    recovery_days = 0
    current_regime = "NORMAL"
    leverage_caps = []
    
    for date in prices.index:
        vix_val = vix_lagged.get(date, 20)
        is_trend_up = trend_up.get(date, True) if date in trend_up.index else True
        hy_val = hy_spread.get(date, 4.0) if date in hy_spread.index else 4.0
        
        # Crisis 신호 (VIX > 28 또는 HY Spread > 6)
        crisis_signal = vix_val > 28 or (has_hy and hy_val > 6)
        recovery_signal = vix_val < 20 and is_trend_up and (not has_hy or hy_val < 4.5)
        
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
            if vix_val > 25 or (has_hy and hy_val > 5):
                current_regime = "TIGHTENING"
            elif vix_val > 18 or not is_trend_up:
                current_regime = "CAUTION"
            elif vix_val < 15 and is_trend_up:
                current_regime = "BULL"
            else:
                current_regime = "NORMAL"
        
        leverage = LEVERAGE_CAPS[current_regime]
        if not is_trend_up and current_regime not in ["CRISIS", "TIGHTENING"]:
            leverage *= 0.5
        
        leverage_caps.append(leverage)
    
    leverage_caps = np.array(leverage_caps, dtype=np.float64)
    
    # 팩터 조합 테스트
    logger.info("\n팩터 조합 테스트...")
    
    test_configs = [
        {"name": "기본 (Mom+LowVol)", "weights": {"mom_12_1": 0.4, "mom_6_1": 0.3, "low_vol": 0.3}},
        {"name": "펀더멘털 추가", "weights": {"mom_12_1": 0.3, "mom_6_1": 0.2, "low_vol": 0.2, "value": 0.15, "quality": 0.15}},
        {"name": "Quality 강조", "weights": {"mom_12_1": 0.3, "mom_6_1": 0.2, "low_vol": 0.1, "quality": 0.4}},
        {"name": "Value 강조", "weights": {"mom_12_1": 0.3, "mom_6_1": 0.2, "low_vol": 0.1, "value": 0.4}},
    ]
    
    best_result = None
    best_sharpe = -999
    
    for config in test_configs:
        # 사용 가능한 팩터만 필터링
        available_weights = {k: v for k, v in config["weights"].items() if k in factors}
        if not available_weights:
            continue
        
        # 가중치 정규화
        total_w = sum(available_weights.values())
        available_weights = {k: v/total_w for k, v in available_weights.items()}
        
        combined = sum(factors[n] * w for n, w in available_weights.items())
        
        common_cols = combined.columns.intersection(returns.columns)
        signals_arr = combined[common_cols].values.astype(np.float64)
        returns_test = returns[common_cols].values.astype(np.float64)
        
        port_rets = fast_backtest_v6(signals_arr, returns_test, leverage_caps, 20, 10, 0.005, -0.08)
        port_rets_series = pd.Series(port_rets, index=prices.index)
        
        full_m = calc_metrics(port_rets_series)
        is_m = calc_metrics(port_rets_series[port_rets_series.index <= "2020-12-31"])
        oos_m = calc_metrics(port_rets_series[port_rets_series.index >= "2021-01-01"])
        
        logger.info(f"  {config['name']}: Sharpe={full_m['sharpe']:.4f}, MDD={full_m['mdd']:.2%}, IS={is_m['sharpe']:.4f}, OOS={oos_m['sharpe']:.4f}")
        
        if full_m['sharpe'] > best_sharpe:
            best_sharpe = full_m['sharpe']
            best_result = {"config": config['name'], "full": full_m, "is": is_m, "oos": oos_m, "weights": available_weights}
    
    logger.info(f"\n최적 설정: {best_result['config']}")
    logger.info(f"  Full: Sharpe={best_result['full']['sharpe']:.4f}, MDD={best_result['full']['mdd']:.2%}")
    logger.info(f"  IS: Sharpe={best_result['is']['sharpe']:.4f}, OOS: Sharpe={best_result['oos']['sharpe']:.4f}")
    
    # 최적 설정으로 연도별 성과
    combined = sum(factors[n] * w for n, w in best_result['weights'].items())
    common_cols = combined.columns.intersection(returns.columns)
    signals_arr = combined[common_cols].values.astype(np.float64)
    returns_test = returns[common_cols].values.astype(np.float64)
    
    port_rets = fast_backtest_v6(signals_arr, returns_test, leverage_caps, 20, 10, 0.005, -0.08)
    port_rets_series = pd.Series(port_rets, index=prices.index)
    
    logger.info("\n연도별 성과:")
    for year in range(2016, 2025):
        year_rets = port_rets_series[port_rets_series.index.year == year]
        if len(year_rets) > 0:
            m = calc_metrics(year_rets)
            logger.info(f"  {year}: Sharpe={m['sharpe']:.2f}, Return={m['total_return']:.2%}, MDD={m['mdd']:.2%}")

if __name__ == "__main__":
    main()
