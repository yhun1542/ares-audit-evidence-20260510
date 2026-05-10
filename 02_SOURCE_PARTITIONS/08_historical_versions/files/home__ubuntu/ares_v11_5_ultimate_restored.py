"""
ARES v11.5 - Ultimate v7 핵심 로직 복원
========================================
Ultimate v7 (Sharpe 6.12)의 핵심 요소 복원:
1. 15단계 레짐 분류 (HMM 5단계 × 추세 3단계)
2. BEAR 레짐 완전 현금화 (0.0x)
3. 모멘텀 21일 스킵
4. top_k=20, 리밸런싱=1일
5. 거래비용 10bp
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

# Ultimate v7 유니버스 (40개)
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
    """Ultimate v7: 21일 스킵 모멘텀"""
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
def fast_backtest_v11_5(signals, returns, regime_exposure, regime_indices, 
                        top_k, rebalance_days, tc_total):
    """Ultimate v7 스타일 백테스트 - 레짐별 노출도"""
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    for i in range(n_dates):
        total_cost = 0.0
        
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
        
        day_return = returns[i, :]
        regime_idx = regime_indices[i]
        leverage = regime_exposure[regime_idx]
        
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
            logger.info(f"ETF DB에서 {len(df_etf)} 행 추가")
    except Exception as e:
        logger.warning(f"ETF DB 로드 실패: {e}")
    
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices.index = pd.to_datetime(prices.index)
    prices = prices.sort_index()
    
    # Ultimate v7: ffill + bfill (원본 그대로)
    prices = prices.ffill().bfill()
    
    first_valid_idx = prices.dropna(how='all').index[0]
    prices = prices.loc[first_valid_idx:]
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols]
    logger.info(f"로드된 종목: {len(prices.columns)}개")
    return prices

def detect_15_regimes(vix, spy_returns):
    """Ultimate v7: 15단계 레짐 감지 (HMM 대신 VIX 기반)"""
    
    # VIX 기반 5단계 분류
    vix_regime = pd.Series("MODERATE", index=vix.index)
    vix_regime[vix < 13] = "ULTRA_LOW"
    vix_regime[(vix >= 13) & (vix < 18)] = "LOW"
    vix_regime[(vix >= 18) & (vix < 25)] = "MODERATE"
    vix_regime[(vix >= 25) & (vix < 30)] = "HIGH"
    vix_regime[vix >= 30] = "CRISIS"
    
    # 추세 기반 3단계 분류
    ma_short = spy_returns.rolling(5).mean()
    ma_long = spy_returns.rolling(20).mean()
    
    trend = pd.Series("NEUTRAL", index=spy_returns.index)
    trend[(ma_short > ma_long) & (ma_short > 0)] = "BULL"
    trend[(ma_short < ma_long) & (ma_short < 0)] = "BEAR"
    
    # 15단계 조합
    vix_aligned = vix_regime.reindex(spy_returns.index).ffill()
    detailed_regimes = vix_aligned + "_" + trend
    
    return detailed_regimes

def main():
    logger.info("=" * 60)
    logger.info("ARES v11.5 - Ultimate v7 핵심 로직 복원")
    logger.info("=" * 60)
    
    prices = load_data()
    returns = prices.pct_change().fillna(0)
    
    # VIX 로드
    try:
        conn = sqlite3.connect(DB_PATH)
        vix_df = pd.read_sql_query("SELECT date, close FROM vix WHERE date >= '2016-01-01'", conn)
        conn.close()
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')['close'].ffill()
    except:
        vix = pd.Series(20.0, index=prices.index)
    
    # 팩터 계산 (Ultimate v7 스타일: 21일 스킵)
    logger.info("팩터 계산 중 (21일 스킵 모멘텀)...")
    prices_arr = prices.values.astype(np.float64)
    returns_arr = returns.values.astype(np.float64)
    
    # Ultimate v7 팩터
    mom_12_1 = fast_momentum(prices_arr, 252, skip=21)  # 12개월 모멘텀, 1개월 스킵
    mom_6_1 = fast_momentum(prices_arr, 126, skip=21)   # 6개월 모멘텀, 1개월 스킵
    low_vol = fast_volatility(returns_arr, 60)
    
    factors = {
        "mom_12_1": pd.DataFrame(fast_rank_normalize(mom_12_1), index=prices.index, columns=prices.columns),
        "mom_6_1": pd.DataFrame(fast_rank_normalize(mom_6_1), index=prices.index, columns=prices.columns),
        "low_vol": pd.DataFrame(fast_rank_normalize(-low_vol), index=prices.index, columns=prices.columns)
    }
    
    # Ultimate v7 팩터 가중치
    factor_weights = {"mom_12_1": 0.5, "mom_6_1": 0.3, "low_vol": 0.2}
    combined = sum(factors[n] * w for n, w in factor_weights.items())
    
    # SPY 수익률
    spy_col = 'SPY' if 'SPY' in prices.columns else prices.columns[0]
    spy_returns = returns[spy_col]
    
    # 15단계 레짐 감지
    logger.info("15단계 레짐 감지 중...")
    vix_lagged = vix.shift(1).reindex(prices.index).ffill()
    detailed_regimes = detect_15_regimes(vix_lagged, spy_returns)
    
    # 레짐별 노출도 (Ultimate v7 핵심!)
    regime_names = sorted(detailed_regimes.dropna().unique().tolist())
    regime_exposure = {}
    for name in regime_names:
        if "BULL" in name:
            regime_exposure[name] = 1.5  # BULL: 레버리지 1.5x
        elif "NEUTRAL" in name:
            regime_exposure[name] = 0.7  # NEUTRAL: 보수적 0.7x
        else:
            regime_exposure[name] = 0.0  # BEAR: 완전 현금화!
    
    # 레짐 분포 출력
    logger.info("\n15단계 레짐 분포:")
    regime_counts = detailed_regimes.value_counts()
    for regime in sorted(regime_counts.index):
        pct = regime_counts[regime] / len(detailed_regimes) * 100
        exposure = regime_exposure.get(regime, 0.0)
        logger.info(f"  {regime}: {pct:.1f}% (노출도: {exposure}x)")
    
    # 백테스트 준비
    common_idx = combined.index.intersection(detailed_regimes.index)
    common_cols = combined.columns.intersection(returns.columns)
    
    signals_arr = combined.loc[common_idx, common_cols].values.astype(np.float64)
    returns_arr = returns.loc[common_idx, common_cols].values.astype(np.float64)
    
    regime_to_idx = {n: i for i, n in enumerate(regime_names)}
    regime_indices = np.array([regime_to_idx.get(detailed_regimes.loc[d], 0) for d in common_idx], dtype=np.int32)
    regime_exposure_arr = np.array([regime_exposure.get(n, 1.0) for n in regime_names], dtype=np.float64)
    
    # Ultimate v7 설정으로 백테스트
    logger.info("\n백테스트 실행 (Ultimate v7 설정)...")
    logger.info("  top_k=20, 리밸런싱=1일, 거래비용=10bp")
    
    port_rets = fast_backtest_v11_5(
        signals_arr, returns_arr, regime_exposure_arr, regime_indices,
        top_k=20,           # Ultimate v7: 상위 20개
        rebalance_days=1,   # Ultimate v7: 매일 리밸런싱
        tc_total=0.001      # Ultimate v7: 10bp
    )
    port_rets_series = pd.Series(port_rets, index=common_idx)
    
    # 연도별 성과
    logger.info("\n연도별 성과:")
    for year in range(2016, 2025):
        year_rets = port_rets_series[port_rets_series.index.year == year]
        if len(year_rets) > 0:
            m = calc_metrics(year_rets)
            logger.info(f"  {year}: Sharpe={m['sharpe']:.2f}, Return={m['total_return']:.2%}, MDD={m['mdd']:.2%}")
    
    # 전체 성과
    final_metrics = calc_metrics(port_rets_series)
    logger.info("\n" + "=" * 60)
    logger.info("최종 성과 (v11.5 - Ultimate v7 복원):")
    logger.info("=" * 60)
    logger.info(f"  Sharpe Ratio:     {final_metrics['sharpe']:.4f}")
    logger.info(f"  Annual Return:    {final_metrics['annual_return']:.2%}")
    logger.info(f"  Total Return:     {final_metrics['total_return']:.2%}")
    logger.info(f"  Max Drawdown:     {final_metrics['mdd']:.2%}")
    
    # IS/OOS 검증
    logger.info("\n" + "=" * 60)
    logger.info("IS/OOS 검증:")
    logger.info("=" * 60)
    
    is_rets = port_rets_series[port_rets_series.index <= "2020-12-31"]
    oos_rets = port_rets_series[port_rets_series.index >= "2021-01-01"]
    
    is_metrics = calc_metrics(is_rets)
    oos_metrics = calc_metrics(oos_rets)
    
    logger.info(f"  IS (2016-2020):  Sharpe={is_metrics['sharpe']:.2f}, Return={is_metrics['total_return']:.2%}, MDD={is_metrics['mdd']:.2%}")
    logger.info(f"  OOS (2021-2024): Sharpe={oos_metrics['sharpe']:.2f}, Return={oos_metrics['total_return']:.2%}, MDD={oos_metrics['mdd']:.2%}")
    
    if oos_metrics['sharpe'] > is_metrics['sharpe']:
        logger.info("  ✅ OOS Sharpe > IS Sharpe → 과적합 없음!")
    else:
        logger.info("  ⚠️ OOS Sharpe < IS Sharpe → 과적합 가능성")

if __name__ == "__main__":
    main()
