"""
ARES v11.1 - GPT-5.2 피드백 반영 개선 버전
==========================================
수정사항:
1. DD 스케일: Smoothstep 곡선으로 완만한 감소
2. 거래비용: 턴오버 기반으로만 적용
3. min_scale > 0으로 회복 가능성 확보
4. 히스테리시스: 빠른 축소 / 느린 회복
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit, prange
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# 설정
# ============================================================
DB_PATH = "/home/ubuntu/ares_x_unified_database/ares_universal_v2.db"
ETF_DB_PATH = "/home/ubuntu/etf_data_s3.db"

UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "BRK.B", "JNJ", "JPM", "V", "PG", "HD",
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
    "TLT", "GLD", "IEF", "SHY", "HYG", "LQD"
]

LEVERAGE_CAPS = {
    "BULL": 1.5,
    "NORMAL": 1.2,
    "CAUTION": 0.8,
    "TIGHTENING": 0.5,
    "CRISIS": 0.0
}

REGIME_FACTOR_WEIGHTS = {
    "BULL": {"mom_63": 0.35, "mom_126": 0.25, "vol_inv": 0.25, "quality": 0.15},
    "NORMAL": {"mom_63": 0.30, "mom_126": 0.25, "vol_inv": 0.30, "quality": 0.15},
    "CAUTION": {"mom_63": 0.20, "mom_126": 0.20, "vol_inv": 0.40, "quality": 0.20},
    "TIGHTENING": {"mom_63": 0.15, "mom_126": 0.15, "vol_inv": 0.45, "quality": 0.25},
    "CRISIS": {"mom_63": 0.10, "mom_126": 0.10, "vol_inv": 0.50, "quality": 0.30}
}

# ============================================================
# Numba 최적화 함수
# ============================================================
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
def smoothstep_dd_scale(dd, start_dd, max_dd, min_scale):
    """
    GPT 권장 Smoothstep DD 스케일
    - dd >= start_dd: 스케일 1.0 (알파 존중)
    - dd <= max_dd: 스케일 min_scale
    - 그 사이: Smoothstep 곡선으로 부드럽게 감소
    """
    if dd >= start_dd:
        return 1.0
    elif dd <= max_dd:
        return min_scale
    else:
        # Smoothstep: 0~1 범위로 정규화
        x = (dd - start_dd) / (max_dd - start_dd)
        x = min(max(x, 0.0), 1.0)
        # Smoothstep: 3x² - 2x³
        s = 1.0 - (3.0 * x * x - 2.0 * x * x * x)
        return min_scale + (1.0 - min_scale) * s

@njit(cache=True)
def fast_backtest_v11_1(signals, returns, leverage_caps, top_k, rebalance_days, 
                        one_way_bp, start_dd, max_dd, min_scale,
                        consec_limit, loss_scale_min, recovery_rate,
                        cut_speed, recover_speed):
    """
    v11.1 백테스트 - GPT 피드백 반영
    
    수정사항:
    1. Smoothstep DD 스케일 (완만한 감소)
    2. 턴오버 기반 거래비용만 적용
    3. 히스테리시스 (빠른 축소 / 느린 회복)
    4. min_scale > 0으로 회복 가능성 확보
    """
    n_dates, n_assets = signals.shape
    portfolio_returns = np.zeros(n_dates, dtype=np.float64)
    invested_ratios = np.zeros(n_dates, dtype=np.float64)
    current_weights = np.zeros(n_assets, dtype=np.float64)
    
    cum_ret = 1.0
    peak = 1.0
    dd_scale = 1.0
    dd_scale_smooth = 1.0  # 히스테리시스 적용된 스케일
    consecutive_losses = 0
    loss_scale = 1.0
    
    for i in range(n_dates):
        # Peak 및 Drawdown 계산
        if cum_ret > peak:
            peak = cum_ret
        dd = (cum_ret / peak) - 1.0
        
        # ✅ GPT 권장: Smoothstep DD 스케일
        dd_scale_raw = smoothstep_dd_scale(dd, start_dd, max_dd, min_scale)
        
        # ✅ GPT 권장: 히스테리시스 (빠른 축소 / 느린 회복)
        if dd_scale_raw < dd_scale_smooth:
            # 리스크 오프는 빠르게
            dd_scale_smooth = dd_scale_smooth + cut_speed * (dd_scale_raw - dd_scale_smooth)
        else:
            # 회복은 천천히
            dd_scale_smooth = dd_scale_smooth + recover_speed * (dd_scale_raw - dd_scale_smooth)
        
        dd_scale = dd_scale_smooth
        
        # 연속 손실 추적
        if i > 0 and portfolio_returns[i-1] < -0.005:
            consecutive_losses += 1
        elif i > 0 and portfolio_returns[i-1] > 0.005:
            consecutive_losses = 0
        
        if consecutive_losses >= consec_limit:
            loss_scale = max(loss_scale_min, loss_scale - 0.15)
        else:
            loss_scale = min(1.0, loss_scale + recovery_rate)
        
        # 리밸런싱 (신호 lag 없음 - v10 방식 유지)
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
                
                # ✅ GPT 권장: 턴오버 기반 거래비용만 적용
                turnover = np.sum(np.abs(new_weights - current_weights))
                if turnover > 0.01:  # 최소 턴오버 임계값
                    total_cost = turnover * one_way_bp / 10000.0
                
                current_weights = new_weights
        
        invested_ratios[i] = np.sum(np.abs(current_weights))
        
        # 일일 수익률 계산
        day_return = returns[i, :]
        leverage = leverage_caps[i] * dd_scale * loss_scale
        
        port_ret = 0.0
        for j in range(n_assets):
            if current_weights[j] > 0 and not np.isnan(day_return[j]):
                port_ret += current_weights[j] * day_return[j]
        
        portfolio_returns[i] = port_ret * leverage - total_cost
        cum_ret *= (1.0 + portfolio_returns[i])
    
    return portfolio_returns, invested_ratios

def calc_metrics(returns, invested_ratios=None):
    cum_ret = (1 + returns).cumprod()
    total_ret = cum_ret.iloc[-1] - 1
    n_years = len(returns) / 252
    annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
    vol = returns.std() * np.sqrt(252)
    sharpe = annual_ret / (vol + 1e-10)
    max_cum = cum_ret.cummax()
    dd = cum_ret / max_cum - 1
    mdd = dd.min()
    
    result = {
        "sharpe": sharpe, 
        "mdd": mdd, 
        "annual_return": annual_ret, 
        "total_return": total_ret,
        "volatility": vol
    }
    
    if invested_ratios is not None:
        result["avg_invested_ratio"] = np.mean(invested_ratios)
        result["invested_days_ratio"] = np.mean(invested_ratios > 0.01)
    
    return result

def load_data():
    logger.info("데이터 로드 중...")
    
    conn = sqlite3.connect(DB_PATH)
    symbols_list = ", ".join([f"'{s}'" for s in UNIVERSE])
    query = f"""
    SELECT date, symbol, adj_close as close
    FROM daily_ohlcv
    WHERE symbol IN ({symbols_list})
    AND date >= '2016-01-01'
    ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # ETF DB에서 추가 데이터 로드
    try:
        conn_etf = sqlite3.connect(ETF_DB_PATH)
        etf_query = """
        SELECT date, symbol, close
        FROM daily_ohlcv
        WHERE date >= '2016-01-01'
        ORDER BY date, symbol
        """
        df_etf = pd.read_sql_query(etf_query, conn_etf)
        conn_etf.close()
        
        if len(df_etf) > 0:
            df = pd.concat([df, df_etf], ignore_index=True)
            df = df.drop_duplicates(subset=['date', 'symbol'], keep='first')
            logger.info(f"ETF DB에서 {len(df_etf)} 행 추가")
    except Exception as e:
        logger.warning(f"ETF DB 로드 실패 (무시): {e}")
    
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices.index = pd.to_datetime(prices.index)
    prices = prices.sort_index()
    
    # ffill만 사용 (bfill 제거)
    prices = prices.ffill()
    
    first_valid_idx = prices.dropna(how='all').index[0]
    prices = prices.loc[first_valid_idx:]
    
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols]
    
    logger.info(f"로드된 종목: {len(prices.columns)}개, 기간: {prices.index[0]} ~ {prices.index[-1]}")
    
    return prices

def main():
    logger.info("=" * 60)
    logger.info("ARES v11.1 - GPT-5.2 피드백 반영 개선 버전")
    logger.info("=" * 60)
    
    prices = load_data()
    returns = prices.pct_change()
    
    # VIX 로드
    try:
        conn = sqlite3.connect(DB_PATH)
        vix_query = """
        SELECT date, close FROM vix
        WHERE date >= '2016-01-01'
        ORDER BY date
        """
        vix_df = pd.read_sql_query(vix_query, conn)
        conn.close()
        vix_df['date'] = pd.to_datetime(vix_df['date'])
        vix = vix_df.set_index('date')['close']
    except:
        logger.warning("VIX 데이터 로드 실패, 기본값 사용")
        vix = pd.Series(20.0, index=prices.index)
    
    # 팩터 계산
    logger.info("팩터 계산 중...")
    prices_arr = prices.values.astype(np.float64)
    returns_arr = returns.values.astype(np.float64)
    
    mom_63 = fast_momentum(prices_arr, 63, skip=1)
    mom_126 = fast_momentum(prices_arr, 126, skip=1)
    vol_20 = fast_volatility(returns_arr, 20)
    
    factors = {
        "mom_63": pd.DataFrame(fast_rank_normalize(mom_63), index=prices.index, columns=prices.columns),
        "mom_126": pd.DataFrame(fast_rank_normalize(mom_126), index=prices.index, columns=prices.columns),
        "vol_inv": pd.DataFrame(fast_rank_normalize(-vol_20), index=prices.index, columns=prices.columns),
        "quality": pd.DataFrame(fast_rank_normalize(mom_63 / (vol_20 + 1e-10)), index=prices.index, columns=prices.columns)
    }
    
    # 레짐 감지
    logger.info("레짐 감지 중...")
    vix_aligned = vix.reindex(prices.index).ffill()
    vix_lagged = vix_aligned.shift(1)
    
    spy_col = 'SPY' if 'SPY' in prices.columns else prices.columns[0]
    spy_prices = prices[spy_col]
    ma_200 = spy_prices.rolling(200, min_periods=50).mean()
    ma_50 = spy_prices.rolling(50, min_periods=20).mean()
    
    leverage_caps = []
    combined_signals = []
    regime_history = []
    
    for i in range(len(prices)):
        vix_val = vix_lagged.iloc[i] if pd.notna(vix_lagged.iloc[i]) else 20.0
        is_trend_up = spy_prices.iloc[i] > ma_200.iloc[i] if pd.notna(ma_200.iloc[i]) else True
        is_short_trend_up = spy_prices.iloc[i] > ma_50.iloc[i] if pd.notna(ma_50.iloc[i]) else True
        
        if vix_val > 28:
            current_regime = "CRISIS"
        elif vix_val > 25:
            current_regime = "TIGHTENING"
        elif vix_val > 18 or not is_trend_up:
            current_regime = "CAUTION"
        elif vix_val < 15 and is_trend_up and is_short_trend_up:
            current_regime = "BULL"
        else:
            current_regime = "NORMAL"
        
        regime_history.append(current_regime)
        
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
    
    # 레짐 분포 출력
    regime_series = pd.Series(regime_history, index=prices.index)
    logger.info("\n레짐 분포:")
    for regime in ["BULL", "NORMAL", "CAUTION", "TIGHTENING", "CRISIS"]:
        pct = (regime_series == regime).mean() * 100
        logger.info(f"  {regime}: {pct:.1f}%")
    
    # 파라미터 그리드 서치 (GPT 권장 파라미터 포함)
    logger.info("\n파라미터 그리드 서치 (GPT 권장 설정)...")
    
    # GPT 권장 파라미터
    start_dds = [-0.03, -0.05, -0.08]  # DD 시작점
    max_dds = [-0.15, -0.20, -0.25]    # DD 최대점
    min_scales = [0.2, 0.25, 0.3]      # 최소 스케일
    cut_speeds = [0.8, 1.0]            # 축소 속도
    recover_speeds = [0.1, 0.2]        # 회복 속도
    
    results = []
    
    for start_dd in start_dds:
        for max_dd in max_dds:
            for min_scale in min_scales:
                for cut_speed in cut_speeds:
                    for recover_speed in recover_speeds:
                        port_rets, invested_ratios = fast_backtest_v11_1(
                            combined_signals_arr, returns_test, leverage_caps_arr, 
                            15, 10, 
                            25.0,  # one_way_bp
                            start_dd, max_dd, min_scale,
                            3, 0.4, 0.05,  # consec, loss_min, recovery
                            cut_speed, recover_speed
                        )
                        port_rets_series = pd.Series(port_rets, index=prices.index)
                        
                        full_m = calc_metrics(port_rets_series, invested_ratios)
                        
                        y2022 = port_rets_series[port_rets_series.index.year == 2022]
                        m2022 = calc_metrics(y2022)
                        
                        results.append({
                            "start_dd": start_dd, "max_dd": max_dd, "min_scale": min_scale,
                            "cut_speed": cut_speed, "recover_speed": recover_speed,
                            "sharpe": full_m["sharpe"], "mdd": full_m["mdd"],
                            "y2022": m2022["total_return"],
                            "avg_invested": full_m.get("avg_invested_ratio", 0)
                        })
    
    results_df = pd.DataFrame(results)
    
    # 최적 설정 선택 (Sharpe 기준)
    best_idx = results_df["sharpe"].idxmax()
    best = results_df.loc[best_idx]
    
    logger.info(f"\n최적 설정 (Sharpe 기준):")
    logger.info(f"  start_dd={best['start_dd']}, max_dd={best['max_dd']}, min_scale={best['min_scale']}")
    logger.info(f"  cut_speed={best['cut_speed']}, recover_speed={best['recover_speed']}")
    logger.info(f"  Sharpe={best['sharpe']:.4f}, MDD={best['mdd']:.2%}, 2022={best['y2022']:.2%}")
    
    # 최적 설정으로 최종 백테스트
    port_rets, invested_ratios = fast_backtest_v11_1(
        combined_signals_arr, returns_test, leverage_caps_arr, 
        15, 10, 25.0,
        best["start_dd"], best["max_dd"], best["min_scale"],
        3, 0.4, 0.05,
        best["cut_speed"], best["recover_speed"]
    )
    port_rets_series = pd.Series(port_rets, index=prices.index)
    
    # 연도별 성과
    logger.info("\n연도별 성과:")
    for year in range(2016, 2025):
        year_rets = port_rets_series[port_rets_series.index.year == year]
        if len(year_rets) > 0:
            m = calc_metrics(year_rets)
            logger.info(f"  {year}: Sharpe={m['sharpe']:.2f}, Return={m['total_return']:.2%}, MDD={m['mdd']:.2%}")
    
    # 전체 성과
    final_metrics = calc_metrics(port_rets_series, invested_ratios)
    logger.info("\n" + "=" * 60)
    logger.info("최종 성과 (v11.1 - GPT 피드백 반영):")
    logger.info("=" * 60)
    logger.info(f"  Sharpe Ratio:     {final_metrics['sharpe']:.4f}")
    logger.info(f"  Annual Return:    {final_metrics['annual_return']:.2%}")
    logger.info(f"  Total Return:     {final_metrics['total_return']:.2%}")
    logger.info(f"  Volatility:       {final_metrics['volatility']:.2%}")
    logger.info(f"  Max Drawdown:     {final_metrics['mdd']:.2%}")
    logger.info(f"  Avg Invested:     {final_metrics.get('avg_invested_ratio', 0):.2%}")
    
    # 결과 저장
    results_df.to_csv("/home/ubuntu/ares_results/v11_1_gpt_feedback.csv", index=False)
    logger.info("\n결과 저장: /home/ubuntu/ares_results/v11_1_gpt_feedback.csv")

if __name__ == "__main__":
    main()
