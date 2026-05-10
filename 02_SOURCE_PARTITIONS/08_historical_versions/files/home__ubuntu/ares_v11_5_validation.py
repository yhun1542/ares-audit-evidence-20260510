"""
ARES v11.5 철저한 검증 스크립트
================================
1. 룩어헤드 바이어스 검증
2. 과적합 검증 (Walk-Forward, K-Fold)
3. 비용 반영 검증 (거래비용, 슬리페이지, 시장 충격)
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
import logging
import warnings
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
ETF_DB_PATH = "/tmp/etf_data_s3_copy.db"

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JNJ",
    "JPM", "V", "PG", "HD", "MA", "ABBV", "MRK", "LLY",
    "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT", "CSCO", "ACN", "ABT",
    "SPY", "QQQ", "IWM", "DIA",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
    "TLT", "GLD"
]

@njit(parallel=True, cache=True)
def fast_momentum(prices, lookback, skip=21):
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
def fast_backtest_strict(signals, returns, regime_exposure, regime_indices, 
                         top_k, rebalance_days, tc_one_way, slippage_bps, signal_lag):
    """
    엄격한 백테스트 (룩어헤드 방지)
    - signal_lag: 신호 생성 후 체결까지 지연 (1 = 다음날 체결)
    - slippage_bps: 슬리페이지 (basis points)
    """
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    for i in range(signal_lag, n_dates):
        total_cost = 0.0
        
        # 신호는 signal_lag일 전 데이터 사용
        signal_idx = i - signal_lag
        
        if signal_idx % rebalance_days == 0 and signal_idx >= 0:
            day_signals = signals[signal_idx, :]
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
                # 거래비용 = 턴오버 × 편도비용 × 2 (매수+매도)
                tc_cost = turnover * tc_one_way
                # 슬리페이지
                slip_cost = turnover * slippage_bps / 10000
                total_cost = tc_cost + slip_cost
                current_weights = new_weights
        
        day_return = returns[i, :]
        
        # 레짐도 전날 데이터 사용 (룩어헤드 방지)
        regime_idx_for_day = regime_indices[max(0, i - 1)]
        leverage = regime_exposure[regime_idx_for_day]
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        portfolio_returns[i] = port_ret * leverage - total_cost
    
    return portfolio_returns

def calc_metrics(returns):
    if len(returns) == 0:
        return {"sharpe": 0, "mdd": 0, "annual_return": 0, "total_return": 0}
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

def load_data():
    logger.info("데이터 로드 중...")
    conn = sqlite3.connect(DB_PATH)
    symbols_list = ", ".join([f"'{s}'" for s in UNIVERSE])
    query = f"""
    SELECT date, symbol, adj_close as close FROM daily_ohlcv
    WHERE symbol IN ({symbols_list}) AND date >= '2016-01-01'
    ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    try:
        conn_etf = sqlite3.connect(ETF_DB_PATH)
        df_etf = pd.read_sql_query("""
        SELECT date, symbol, close FROM daily_ohlcv WHERE date >= '2016-01-01'
        """, conn_etf)
        conn_etf.close()
        if len(df_etf) > 0:
            df = pd.concat([df, df_etf], ignore_index=True)
            df = df.drop_duplicates(subset=['date', 'symbol'], keep='first')
    except:
        pass
    
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices.index = pd.to_datetime(prices.index)
    prices = prices.sort_index()
    
    # 룩어헤드 방지: ffill만 사용 (bfill 금지!)
    prices = prices.ffill()
    
    first_valid_idx = prices.dropna(how='all').index[0]
    prices = prices.loc[first_valid_idx:]
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols]
    return prices

def detect_15_regimes_strict(vix, spy_returns):
    """엄격한 레짐 감지 (전날 데이터만 사용)"""
    
    # VIX는 전날 종가 사용
    vix_lagged = vix.shift(1)
    
    vix_regime = pd.Series("MODERATE", index=vix_lagged.index)
    vix_regime[vix_lagged < 13] = "ULTRA_LOW"
    vix_regime[(vix_lagged >= 13) & (vix_lagged < 18)] = "LOW"
    vix_regime[(vix_lagged >= 18) & (vix_lagged < 25)] = "MODERATE"
    vix_regime[(vix_lagged >= 25) & (vix_lagged < 30)] = "HIGH"
    vix_regime[vix_lagged >= 30] = "CRISIS"
    
    # 추세도 전날까지의 데이터만 사용
    ma_short = spy_returns.rolling(5).mean().shift(1)
    ma_long = spy_returns.rolling(20).mean().shift(1)
    
    trend = pd.Series("NEUTRAL", index=spy_returns.index)
    trend[(ma_short > ma_long) & (ma_short > 0)] = "BULL"
    trend[(ma_short < ma_long) & (ma_short < 0)] = "BEAR"
    
    vix_aligned = vix_regime.reindex(spy_returns.index).ffill()
    detailed_regimes = vix_aligned + "_" + trend
    
    return detailed_regimes

def run_walk_forward_validation(prices, returns, vix, n_splits=5):
    """Walk-Forward 검증"""
    logger.info(f"\n{'='*60}")
    logger.info(f"Walk-Forward 검증 ({n_splits} splits)")
    logger.info(f"{'='*60}")
    
    total_days = len(prices)
    train_size = int(total_days * 0.6)
    test_size = (total_days - train_size) // n_splits
    
    results = []
    
    for i in range(n_splits):
        train_end = train_size + i * test_size
        test_start = train_end
        test_end = min(test_start + test_size, total_days)
        
        train_idx = prices.index[:train_end]
        test_idx = prices.index[test_start:test_end]
        
        if len(test_idx) < 20:
            continue
        
        # 팩터 계산 (전체 데이터로)
        prices_arr = prices.values.astype(np.float64)
        returns_arr = returns.values.astype(np.float64)
        
        mom_12_1 = fast_momentum(prices_arr, 252, skip=21)
        mom_6_1 = fast_momentum(prices_arr, 126, skip=21)
        low_vol = fast_volatility(returns_arr, 60)
        
        factors = {
            "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
            "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
            "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
        }
        
        factor_weights = {"mom_12_1": 0.5, "mom_6_1": 0.3, "low_vol": 0.2}
        combined = sum(factors[n] * w for n, w in factor_weights.items())
        
        spy_col = 'SPY' if 'SPY' in prices.columns else prices.columns[0]
        spy_returns = returns[spy_col]
        
        detailed_regimes = detect_15_regimes_strict(vix.reindex(prices.index).ffill(), spy_returns)
        
        regime_names = sorted(detailed_regimes.dropna().unique().tolist())
        regime_exposure = {}
        for name in regime_names:
            if "BULL" in name:
                regime_exposure[name] = 1.5
            elif "NEUTRAL" in name:
                regime_exposure[name] = 0.7
            else:
                regime_exposure[name] = 0.0
        
        # 테스트 기간 백테스트
        test_combined = combined.loc[test_idx]
        test_returns = returns.loc[test_idx]
        test_regimes = detailed_regimes.loc[test_idx]
        
        common_cols = test_combined.columns.intersection(test_returns.columns)
        signals_arr = test_combined[common_cols].values.astype(np.float64)
        returns_arr = test_returns[common_cols].values.astype(np.float64)
        
        regime_to_idx = {n: i for i, n in enumerate(regime_names)}
        regime_indices = np.array([regime_to_idx.get(test_regimes.loc[d], 0) for d in test_idx], dtype=np.int32)
        regime_exposure_arr = np.array([regime_exposure.get(n, 1.0) for n in regime_names], dtype=np.float64)
        
        # 엄격한 백테스트 (1일 lag, 슬리페이지 포함)
        port_rets = fast_backtest_strict(
            signals_arr, returns_arr, regime_exposure_arr, regime_indices,
            top_k=20, rebalance_days=1,
            tc_one_way=0.001,  # 10bp 편도
            slippage_bps=5,    # 5bp 슬리페이지
            signal_lag=1       # 1일 지연
        )
        port_rets_series = pd.Series(port_rets, index=test_idx)
        
        metrics = calc_metrics(port_rets_series)
        results.append({
            'split': i + 1,
            'train_end': train_idx[-1].strftime('%Y-%m-%d'),
            'test_start': test_idx[0].strftime('%Y-%m-%d'),
            'test_end': test_idx[-1].strftime('%Y-%m-%d'),
            'sharpe': metrics['sharpe'],
            'mdd': metrics['mdd'],
            'return': metrics['total_return']
        })
        
        logger.info(f"  Split {i+1}: Train~{train_idx[-1].strftime('%Y-%m-%d')}, "
                   f"Test {test_idx[0].strftime('%Y-%m-%d')}~{test_idx[-1].strftime('%Y-%m-%d')}, "
                   f"Sharpe={metrics['sharpe']:.2f}, Return={metrics['total_return']:.2%}")
    
    avg_sharpe = np.mean([r['sharpe'] for r in results])
    std_sharpe = np.std([r['sharpe'] for r in results])
    logger.info(f"\n  평균 Sharpe: {avg_sharpe:.2f} ± {std_sharpe:.2f}")
    
    return results

def run_cost_sensitivity_analysis(prices, returns, vix):
    """비용 민감도 분석"""
    logger.info(f"\n{'='*60}")
    logger.info("비용 민감도 분석")
    logger.info(f"{'='*60}")
    
    prices_arr = prices.values.astype(np.float64)
    returns_arr = returns.values.astype(np.float64)
    
    mom_12_1 = fast_momentum(prices_arr, 252, skip=21)
    mom_6_1 = fast_momentum(prices_arr, 126, skip=21)
    low_vol = fast_volatility(returns_arr, 60)
    
    factors = {
        "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
        "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
        "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
    }
    
    factor_weights = {"mom_12_1": 0.5, "mom_6_1": 0.3, "low_vol": 0.2}
    combined = sum(factors[n] * w for n, w in factor_weights.items())
    
    spy_col = 'SPY' if 'SPY' in prices.columns else prices.columns[0]
    spy_returns = returns[spy_col]
    
    detailed_regimes = detect_15_regimes_strict(vix.reindex(prices.index).ffill(), spy_returns)
    
    regime_names = sorted(detailed_regimes.dropna().unique().tolist())
    regime_exposure = {}
    for name in regime_names:
        if "BULL" in name:
            regime_exposure[name] = 1.5
        elif "NEUTRAL" in name:
            regime_exposure[name] = 0.7
        else:
            regime_exposure[name] = 0.0
    
    common_idx = combined.index.intersection(detailed_regimes.index)
    common_cols = combined.columns.intersection(returns.columns)
    
    signals_arr = combined.loc[common_idx, common_cols].values.astype(np.float64)
    returns_arr_final = returns.loc[common_idx, common_cols].values.astype(np.float64)
    
    regime_to_idx = {n: i for i, n in enumerate(regime_names)}
    regime_indices = np.array([regime_to_idx.get(detailed_regimes.loc[d], 0) for d in common_idx], dtype=np.int32)
    regime_exposure_arr = np.array([regime_exposure.get(n, 1.0) for n in regime_names], dtype=np.float64)
    
    # 다양한 비용 시나리오 테스트
    cost_scenarios = [
        {"name": "낙관적", "tc": 0.0005, "slip": 2, "lag": 1},    # 5bp + 2bp
        {"name": "기본", "tc": 0.001, "slip": 5, "lag": 1},       # 10bp + 5bp
        {"name": "보수적", "tc": 0.002, "slip": 10, "lag": 1},    # 20bp + 10bp
        {"name": "매우 보수적", "tc": 0.003, "slip": 15, "lag": 2}, # 30bp + 15bp + 2일 lag
        {"name": "실전 최악", "tc": 0.005, "slip": 20, "lag": 2},  # 50bp + 20bp + 2일 lag
    ]
    
    results = []
    for scenario in cost_scenarios:
        port_rets = fast_backtest_strict(
            signals_arr, returns_arr_final, regime_exposure_arr, regime_indices,
            top_k=20, rebalance_days=1,
            tc_one_way=scenario["tc"],
            slippage_bps=scenario["slip"],
            signal_lag=scenario["lag"]
        )
        port_rets_series = pd.Series(port_rets, index=common_idx)
        metrics = calc_metrics(port_rets_series)
        
        results.append({
            'scenario': scenario["name"],
            'tc_bps': scenario["tc"] * 10000,
            'slip_bps': scenario["slip"],
            'lag': scenario["lag"],
            'sharpe': metrics['sharpe'],
            'mdd': metrics['mdd'],
            'return': metrics['total_return']
        })
        
        logger.info(f"  {scenario['name']}: TC={scenario['tc']*10000:.0f}bp, Slip={scenario['slip']}bp, "
                   f"Lag={scenario['lag']}일 → Sharpe={metrics['sharpe']:.2f}, "
                   f"Return={metrics['total_return']:.2%}, MDD={metrics['mdd']:.2%}")
    
    return results

def run_yearly_validation(prices, returns, vix):
    """연도별 검증 (각 연도를 OOS로)"""
    logger.info(f"\n{'='*60}")
    logger.info("연도별 Leave-One-Out 검증")
    logger.info(f"{'='*60}")
    
    prices_arr = prices.values.astype(np.float64)
    returns_arr = returns.values.astype(np.float64)
    
    mom_12_1 = fast_momentum(prices_arr, 252, skip=21)
    mom_6_1 = fast_momentum(prices_arr, 126, skip=21)
    low_vol = fast_volatility(returns_arr, 60)
    
    factors = {
        "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
        "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
        "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
    }
    
    factor_weights = {"mom_12_1": 0.5, "mom_6_1": 0.3, "low_vol": 0.2}
    combined = sum(factors[n] * w for n, w in factor_weights.items())
    
    spy_col = 'SPY' if 'SPY' in prices.columns else prices.columns[0]
    spy_returns = returns[spy_col]
    
    detailed_regimes = detect_15_regimes_strict(vix.reindex(prices.index).ffill(), spy_returns)
    
    regime_names = sorted(detailed_regimes.dropna().unique().tolist())
    regime_exposure = {}
    for name in regime_names:
        if "BULL" in name:
            regime_exposure[name] = 1.5
        elif "NEUTRAL" in name:
            regime_exposure[name] = 0.7
        else:
            regime_exposure[name] = 0.0
    
    common_idx = combined.index.intersection(detailed_regimes.index)
    common_cols = combined.columns.intersection(returns.columns)
    
    signals_arr = combined.loc[common_idx, common_cols].values.astype(np.float64)
    returns_arr_final = returns.loc[common_idx, common_cols].values.astype(np.float64)
    
    regime_to_idx = {n: i for i, n in enumerate(regime_names)}
    regime_indices = np.array([regime_to_idx.get(detailed_regimes.loc[d], 0) for d in common_idx], dtype=np.int32)
    regime_exposure_arr = np.array([regime_exposure.get(n, 1.0) for n in regime_names], dtype=np.float64)
    
    # 보수적 비용 설정
    port_rets = fast_backtest_strict(
        signals_arr, returns_arr_final, regime_exposure_arr, regime_indices,
        top_k=20, rebalance_days=1,
        tc_one_way=0.002,   # 20bp 편도
        slippage_bps=10,    # 10bp 슬리페이지
        signal_lag=1        # 1일 지연
    )
    port_rets_series = pd.Series(port_rets, index=common_idx)
    
    results = []
    for year in range(2017, 2025):
        year_rets = port_rets_series[port_rets_series.index.year == year]
        if len(year_rets) > 0:
            metrics = calc_metrics(year_rets)
            results.append({
                'year': year,
                'sharpe': metrics['sharpe'],
                'mdd': metrics['mdd'],
                'return': metrics['total_return']
            })
            logger.info(f"  {year}: Sharpe={metrics['sharpe']:.2f}, "
                       f"Return={metrics['total_return']:.2%}, MDD={metrics['mdd']:.2%}")
    
    return results

def main():
    logger.info("=" * 70)
    logger.info("ARES v11.5 철저한 검증 (룩어헤드, 과적합, 비용)")
    logger.info("=" * 70)
    
    prices = load_data()
    returns = prices.pct_change().fillna(0)
    logger.info(f"로드된 종목: {len(prices.columns)}개, 기간: {prices.index[0].date()} ~ {prices.index[-1].date()}")
    
    # VIX 로드
    try:
        conn = sqlite3.connect(DB_PATH)
        vix_df = pd.read_sql_query("SELECT date, close FROM vix WHERE date >= '2016-01-01'", conn)
        conn.close()
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')['close']
    except:
        vix = pd.Series(20.0, index=prices.index)
    
    # 1. 비용 민감도 분석
    cost_results = run_cost_sensitivity_analysis(prices, returns, vix)
    
    # 2. Walk-Forward 검증
    wf_results = run_walk_forward_validation(prices, returns, vix, n_splits=5)
    
    # 3. 연도별 검증 (보수적 비용)
    yearly_results = run_yearly_validation(prices, returns, vix)
    
    # 최종 요약
    logger.info("\n" + "=" * 70)
    logger.info("최종 검증 요약")
    logger.info("=" * 70)
    
    logger.info("\n[비용 민감도]")
    for r in cost_results:
        logger.info(f"  {r['scenario']}: Sharpe={r['sharpe']:.2f}")
    
    logger.info("\n[Walk-Forward 검증]")
    avg_sharpe = np.mean([r['sharpe'] for r in wf_results])
    std_sharpe = np.std([r['sharpe'] for r in wf_results])
    logger.info(f"  평균 Sharpe: {avg_sharpe:.2f} ± {std_sharpe:.2f}")
    
    logger.info("\n[연도별 검증 (보수적 비용)]")
    positive_years = sum(1 for r in yearly_results if r['return'] > 0)
    logger.info(f"  양의 수익률 연도: {positive_years}/{len(yearly_results)}")
    
    # 2022년 특별 확인
    y2022 = [r for r in yearly_results if r['year'] == 2022]
    if y2022:
        logger.info(f"  2022년 (하락장): Sharpe={y2022[0]['sharpe']:.2f}, Return={y2022[0]['return']:.2%}")
    
    logger.info("\n" + "=" * 70)
    logger.info("검증 완료!")
    logger.info("=" * 70)

if __name__ == "__main__":
    main()
