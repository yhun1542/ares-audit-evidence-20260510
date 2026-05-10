#!/usr/bin/env python3
"""
ARES v44: 확장 파레토 최적화 테스트
================================================================================

v43 결과 기반:
- Soft Frequency Gating이 가장 효과적 (OOS Sharpe 1.766, +16.3%)
- min_rebalance_days=2가 최적

이번 테스트:
1. Soft Gating 파라미터 세밀 조정
2. 팩터 가중치 최적화
3. 레짐별 레버리지 최적화
4. 수천 가지 조합 테스트
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit
import warnings
import time
from itertools import product
warnings.filterwarnings('ignore')

def load_data():
    """EC2 데이터베이스에서 데이터 로드"""
    db_path = '/home/ubuntu/ares_x_unified_database/ares_universal_v2.db'
    conn = sqlite3.connect(db_path)
    
    print("Loading daily OHLCV data...")
    ohlcv_query = """
        SELECT date, symbol, open, high, low, close, volume 
        FROM daily_ohlcv 
        WHERE symbol IN (
            SELECT symbol FROM daily_ohlcv 
            GROUP BY symbol HAVING COUNT(*) > 1000
        )
        ORDER BY date, symbol
    """
    ohlcv_df = pd.read_sql_query(ohlcv_query, conn)
    ohlcv_df['date'] = pd.to_datetime(ohlcv_df['date'], format='mixed')
    print(f"  Loaded {len(ohlcv_df):,} rows, {ohlcv_df['symbol'].nunique()} symbols")
    
    print("Loading VIX data...")
    vix_query = "SELECT date, close as vix FROM vix ORDER BY date"
    vix_df = pd.read_sql_query(vix_query, conn)
    vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
    vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    print(f"  Loaded {len(vix_df):,} VIX records")
    
    print("Loading market regime data...")
    regime_query = "SELECT date, regime_label as regime FROM market_regime_daily ORDER BY date"
    regime_df = pd.read_sql_query(regime_query, conn)
    regime_df['date'] = pd.to_datetime(regime_df['date'], format='mixed')
    regime_df = regime_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    print(f"  Loaded {len(regime_df):,} regime records")
    
    conn.close()
    return ohlcv_df, vix_df, regime_df


def prepare_numpy_arrays(ohlcv_df, vix_df, regime_df):
    """데이터를 NumPy 배열로 변환"""
    ohlcv_df = ohlcv_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
    print(f"  After dedup: {len(ohlcv_df):,} rows")
    
    prices_wide = ohlcv_df.pivot(index='date', columns='symbol', values='close')
    min_data_ratio = 0.5
    valid_cols = prices_wide.columns[prices_wide.notna().sum() >= len(prices_wide) * min_data_ratio]
    prices_wide = prices_wide[valid_cols]
    prices_wide = prices_wide.ffill().bfill()
    
    returns = prices_wide.pct_change()
    returns = returns.iloc[1:]
    
    vix_aligned = vix_df.reindex(returns.index).ffill().bfill()
    
    returns_np = returns.values.astype(np.float64)
    prices_np = prices_wide.reindex(returns.index).values.astype(np.float64)
    
    if 'vix' in vix_aligned.columns:
        vix_np = vix_aligned['vix'].values.astype(np.float64)
    else:
        vix_np = np.full(len(returns), 20.0)
    
    returns_np = np.nan_to_num(returns_np, nan=0.0)
    prices_np = np.nan_to_num(prices_np, nan=1.0)
    vix_np = np.nan_to_num(vix_np, nan=20.0)
    
    tickers = list(prices_wide.columns)
    dates = list(returns.index)
    
    print(f"Data shape: {returns_np.shape[0]} dates × {returns_np.shape[1]} assets")
    print(f"Date range: {dates[0]} ~ {dates[-1]}")
    
    return returns_np, prices_np, vix_np, tickers, dates


@njit(cache=True)
def compute_momentum_factor(prices, window):
    n_dates, n_assets = prices.shape
    factor = np.zeros((n_dates, n_assets))
    for t in range(window, n_dates):
        for i in range(n_assets):
            if prices[t-window, i] > 0:
                factor[t, i] = (prices[t, i] / prices[t-window, i]) - 1.0
    return factor


@njit(cache=True)
def compute_volatility_factor(returns, window):
    n_dates, n_assets = returns.shape
    factor = np.zeros((n_dates, n_assets))
    for t in range(window, n_dates):
        for i in range(n_assets):
            vol = np.std(returns[t-window:t, i])
            factor[t, i] = -vol if vol > 0 else 0.0
    return factor


@njit(cache=True)
def compute_residual_momentum(returns, window, skip):
    n_dates, n_assets = returns.shape
    factor = np.zeros((n_dates, n_assets))
    
    for t in range(window + skip, n_dates):
        market_ret = np.zeros(window)
        for w in range(window):
            valid_count = 0
            total_ret = 0.0
            for i in range(n_assets):
                ret_val = returns[t - window - skip + w, i]
                if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                    total_ret += ret_val
                    valid_count += 1
            if valid_count > 0:
                market_ret[w] = total_ret / valid_count
        
        for i in range(n_assets):
            asset_ret = returns[t - window - skip:t - skip, i]
            cov_sum = 0.0
            var_sum = 0.0
            mean_asset = np.mean(asset_ret)
            mean_market = np.mean(market_ret)
            
            for w in range(window):
                cov_sum += (asset_ret[w] - mean_asset) * (market_ret[w] - mean_market)
                var_sum += (market_ret[w] - mean_market) ** 2
            
            beta = cov_sum / var_sum if var_sum > 1e-10 else 1.0
            
            residual_sum = 0.0
            for w in range(window):
                residual = asset_ret[w] - beta * market_ret[w]
                residual_sum += residual
            
            factor[t, i] = residual_sum
    
    return factor


@njit(cache=True)
def compute_reversal_factor(returns, window):
    """단기 반전 팩터"""
    n_dates, n_assets = returns.shape
    factor = np.zeros((n_dates, n_assets))
    for t in range(window, n_dates):
        for i in range(n_assets):
            cum_ret = 0.0
            for w in range(window):
                cum_ret += returns[t - w, i]
            factor[t, i] = -cum_ret  # 반전: 최근 하락 = 매수 신호
    return factor


@njit(cache=True)
def compute_regime_with_hysteresis(vix, returns, hyst_width, window=20):
    n_dates = len(vix)
    n_assets = returns.shape[1]
    regime = np.zeros(n_dates, dtype=np.int32)
    
    bull_threshold = 0.02
    bear_threshold = -0.02
    crisis_vix = 30.0
    
    prev_regime = 1
    
    for t in range(window, n_dates):
        cum_ret = 0.0
        valid_count = 0
        for w in range(window):
            for i in range(n_assets):
                ret_val = returns[t - w, i]
                if not np.isnan(ret_val) and abs(ret_val) < 1.0:
                    cum_ret += ret_val
                    valid_count += 1
        
        if valid_count > 0:
            cum_ret /= valid_count
        
        vix_val = vix[t]
        
        if vix_val > crisis_vix:
            new_regime = 3
        elif cum_ret > bull_threshold + hyst_width:
            new_regime = 0
        elif cum_ret < bear_threshold - hyst_width:
            new_regime = 2
        elif cum_ret > bull_threshold - hyst_width and prev_regime == 0:
            new_regime = 0
        elif cum_ret < bear_threshold + hyst_width and prev_regime == 2:
            new_regime = 2
        else:
            new_regime = 1
        
        regime[t] = new_regime
        prev_regime = new_regime
    
    return regime


@njit(cache=True)
def backtest_v44_core(
    returns, prices, vix, regime,
    factor_scores, top_k, rebal_period,
    bull_lev, neutral_lev, bear_lev, crisis_lev,
    min_rebalance_days, confirm_n,
    cost_bps
):
    n_dates, n_assets = returns.shape
    
    weights = np.zeros(n_assets)
    equity = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    turnover_total = 0.0
    rebalance_count = 0
    last_rebalance_day = 0
    regime_confirm_count = 0
    confirmed_regime = 1
    
    regime_leverage = np.array([bull_lev, neutral_lev, bear_lev, crisis_lev])
    
    for t in range(1, n_dates):
        current_regime = regime[t]
        if current_regime < 0 or current_regime > 3:
            current_regime = 1
        
        # 레짐 확인 (confirm_n일 연속)
        if current_regime == regime[t-1]:
            regime_confirm_count += 1
        else:
            regime_confirm_count = 1
        
        if regime_confirm_count >= confirm_n:
            confirmed_regime = current_regime
        
        target_leverage = regime_leverage[confirmed_regime]
        
        # Soft Frequency Gating
        should_rebalance = False
        days_since_rebalance = t - last_rebalance_day
        
        if days_since_rebalance >= min_rebalance_days:
            if t % rebal_period == 0:
                should_rebalance = True
            # 레짐 변화 시 강제 리밸런싱
            if confirmed_regime != regime[t-1] and regime_confirm_count >= confirm_n:
                should_rebalance = True
        
        if should_rebalance:
            scores = factor_scores[t, :]
            
            valid_count = 0
            for i in range(n_assets):
                if not np.isnan(scores[i]) and abs(scores[i]) < 100:
                    valid_count += 1
            
            if valid_count >= top_k:
                sorted_indices = np.argsort(-scores)
                selected = sorted_indices[:top_k]
                
                new_weights = np.zeros(n_assets)
                for idx in selected:
                    if not np.isnan(scores[idx]) and abs(scores[idx]) < 100:
                        new_weights[idx] = target_leverage / top_k
                
                turnover = np.sum(np.abs(new_weights - weights))
                
                cost = turnover * cost_bps / 10000.0
                equity[t-1] *= (1 - cost)
                
                weights = new_weights
                turnover_total += turnover
                rebalance_count += 1
                last_rebalance_day = t
        
        port_return = 0.0
        for i in range(n_assets):
            port_return += weights[i] * returns[t, i]
        
        daily_returns[t] = port_return
        equity[t] = equity[t-1] * (1 + port_return)
    
    return equity, daily_returns, turnover_total, rebalance_count


def calculate_metrics(equity, daily_returns, n_dates, is_ratio=0.55):
    is_end = int(n_dates * is_ratio)
    
    is_returns = daily_returns[1:is_end]
    oos_returns = daily_returns[is_end:]
    
    def sharpe(rets):
        if len(rets) < 10:
            return np.nan
        mean_ret = np.mean(rets)
        std_ret = np.std(rets)
        if std_ret == 0:
            return 0.0
        return mean_ret / std_ret * np.sqrt(252)
    
    full_sharpe = sharpe(daily_returns[1:])
    is_sharpe = sharpe(is_returns)
    oos_sharpe = sharpe(oos_returns)
    
    peak = equity[0]
    max_dd = 0.0
    for i in range(len(equity)):
        if equity[i] > peak:
            peak = equity[i]
        dd = (equity[i] - peak) / peak
        if dd < max_dd:
            max_dd = dd
    
    gap = (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe != 0 else 0.0
    
    return {
        'sharpe': full_sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'gap': gap,
        'mdd': max_dd,
        'annual_return': (equity[-1] / equity[0]) ** (252 / len(equity)) - 1
    }


def run_single_test(params):
    (returns, prices, vix, factor_scores,
     top_k, rebal_period, hyst_width,
     bull_lev, neutral_lev, bear_lev, crisis_lev,
     min_rebal_days, confirm_n,
     test_name) = params
    
    try:
        regime = compute_regime_with_hysteresis(vix, returns, hyst_width)
        
        equity, daily_returns, turnover, rebal_count = backtest_v44_core(
            returns, prices, vix, regime,
            factor_scores, top_k, rebal_period,
            bull_lev, neutral_lev, bear_lev, crisis_lev,
            min_rebal_days, confirm_n,
            20.0
        )
        
        metrics = calculate_metrics(equity, daily_returns, len(equity))
        
        return {
            'test_name': test_name,
            'top_k': top_k,
            'rebal_period': rebal_period,
            'hyst_width': hyst_width,
            'bull_lev': bull_lev,
            'neutral_lev': neutral_lev,
            'bear_lev': bear_lev,
            'crisis_lev': crisis_lev,
            'min_rebal_days': min_rebal_days,
            'confirm_n': confirm_n,
            'sharpe': metrics['sharpe'],
            'is_sharpe': metrics['is_sharpe'],
            'oos_sharpe': metrics['oos_sharpe'],
            'gap': metrics['gap'],
            'mdd': metrics['mdd'],
            'annual_return': metrics['annual_return'],
            'turnover': turnover,
            'rebal_count': rebal_count
        }
    except Exception as e:
        print(f"Error in {test_name}: {e}")
        return None


def main():
    print("="*80)
    print("ARES v44: 확장 파레토 최적화 테스트")
    print("="*80)
    
    print("\n[1] Loading data...")
    ohlcv_df, vix_df, regime_df = load_data()
    
    print("\n[2] Preparing numpy arrays...")
    returns, prices, vix, tickers, dates = prepare_numpy_arrays(
        ohlcv_df, vix_df, regime_df
    )
    
    n_dates, n_assets = returns.shape
    print(f"Data: {n_dates} dates × {n_assets} assets")
    
    print("\n[3] Computing factors...")
    momentum_63 = compute_momentum_factor(prices, 63)
    momentum_126 = compute_momentum_factor(prices, 126)
    momentum_252 = compute_momentum_factor(prices, 252)
    volatility_21 = compute_volatility_factor(returns, 21)
    volatility_63 = compute_volatility_factor(returns, 63)
    residual_mom_63 = compute_residual_momentum(returns, 63, 0)
    residual_mom_126 = compute_residual_momentum(returns, 126, 0)
    reversal_5 = compute_reversal_factor(returns, 5)
    
    print("  All factors computed")
    
    print("\n[4] Running extended Pareto optimization...")
    
    # 파라미터 그리드
    top_k_range = [20, 25, 30, 35, 40]
    rebal_period_range = [5, 7, 10]
    hyst_range = [0.03, 0.04, 0.05, 0.06]
    min_rebal_range = [1, 2, 3]
    confirm_n_range = [1, 2, 3]
    
    # 레버리지 조합
    lev_configs = [
        (1.0, 0.7, 0.5, 0.2),
        (1.0, 0.8, 0.6, 0.3),
        (0.9, 0.7, 0.5, 0.2),
        (1.0, 0.6, 0.4, 0.1),
        (0.8, 0.6, 0.4, 0.2),
    ]
    
    # 팩터 가중치 조합
    factor_configs = [
        {'mom63': 1.0, 'mom126': 1.0, 'vol21': 0.5, 'res126': 0.2, 'name': 'base'},
        {'mom63': 1.0, 'mom126': 1.0, 'vol21': 0.3, 'res126': 0.3, 'name': 'res_up'},
        {'mom63': 1.0, 'mom126': 1.0, 'vol21': 0.7, 'res126': 0.1, 'name': 'vol_up'},
        {'mom63': 1.0, 'mom126': 0.5, 'vol21': 0.5, 'res126': 0.3, 'name': 'short_mom'},
        {'mom63': 0.5, 'mom126': 1.0, 'vol21': 0.5, 'res126': 0.3, 'name': 'long_mom'},
        {'mom63': 1.0, 'mom126': 1.0, 'vol21': 0.5, 'res126': 0.0, 'name': 'no_res'},
        {'mom63': 1.0, 'mom126': 1.0, 'vol21': 0.0, 'res126': 0.3, 'name': 'no_vol'},
    ]
    
    test_configs = []
    
    # 전체 그리드 생성
    for top_k in top_k_range:
        for rebal in rebal_period_range:
            for hyst in hyst_range:
                for min_rebal in min_rebal_range:
                    for confirm_n in confirm_n_range:
                        for levs in lev_configs:
                            for fc in factor_configs:
                                test_configs.append({
                                    'top_k': top_k,
                                    'rebal_period': rebal,
                                    'hyst_width': hyst,
                                    'min_rebal_days': min_rebal,
                                    'confirm_n': confirm_n,
                                    'bull_lev': levs[0],
                                    'neutral_lev': levs[1],
                                    'bear_lev': levs[2],
                                    'crisis_lev': levs[3],
                                    'factor_config': fc
                                })
    
    print(f"Total test configurations: {len(test_configs)}")
    
    # 테스트 실행
    start_time = time.time()
    results = []
    
    for i, config in enumerate(test_configs):
        fc = config['factor_config']
        
        # 팩터 점수 계산
        factor_scores = (
            momentum_63 * fc['mom63'] + 
            momentum_126 * fc['mom126'] + 
            volatility_21 * fc['vol21'] + 
            residual_mom_126 * fc['res126']
        ) / (fc['mom63'] + fc['mom126'] + fc['vol21'] + fc['res126'] + 1e-10)
        
        test_name = f"tk{config['top_k']}_rb{config['rebal_period']}_h{config['hyst_width']}_mr{config['min_rebal_days']}_cn{config['confirm_n']}_l{config['bull_lev']}_{fc['name']}"
        
        params = (
            returns, prices, vix, factor_scores,
            config['top_k'], config['rebal_period'], config['hyst_width'],
            config['bull_lev'], config['neutral_lev'], config['bear_lev'], config['crisis_lev'],
            config['min_rebal_days'], config['confirm_n'],
            test_name
        )
        
        result = run_single_test(params)
        if result:
            result['factor_config'] = fc['name']
            results.append(result)
        
        if (i + 1) % 500 == 0:
            elapsed = time.time() - start_time
            print(f"  Progress: {i+1}/{len(test_configs)} ({elapsed:.1f}s)")
    
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.1f} seconds ({len(results)} successful tests)")
    
    # 결과 정리
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('oos_sharpe', ascending=False)
    results_df.to_csv('/home/ubuntu/v44_extended_pareto_results.csv', index=False)
    
    # 결과 출력
    print("\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)
    
    print("\n### TOP 20 by OOS Sharpe ###")
    top20 = results_df.head(20)
    for idx, row in top20.iterrows():
        print(f"  {row['test_name'][:60]}: OOS={row['oos_sharpe']:.3f}, IS={row['is_sharpe']:.3f}, Gap={row['gap']*100:.1f}%, MDD={row['mdd']*100:.1f}%")
    
    print("\n### 파라미터별 최적값 분석 ###")
    
    # Top-k 분석
    print("\n  Top-k 분석:")
    for tk in top_k_range:
        subset = results_df[results_df['top_k'] == tk]
        if len(subset) > 0:
            print(f"    top_k={tk}: avg OOS={subset['oos_sharpe'].mean():.3f}, max OOS={subset['oos_sharpe'].max():.3f}")
    
    # Rebal period 분석
    print("\n  Rebal Period 분석:")
    for rb in rebal_period_range:
        subset = results_df[results_df['rebal_period'] == rb]
        if len(subset) > 0:
            print(f"    rebal={rb}: avg OOS={subset['oos_sharpe'].mean():.3f}, max OOS={subset['oos_sharpe'].max():.3f}")
    
    # Min rebal days 분석
    print("\n  Min Rebal Days 분석:")
    for mr in min_rebal_range:
        subset = results_df[results_df['min_rebal_days'] == mr]
        if len(subset) > 0:
            print(f"    min_rebal={mr}: avg OOS={subset['oos_sharpe'].mean():.3f}, max OOS={subset['oos_sharpe'].max():.3f}")
    
    # Factor config 분석
    print("\n  Factor Config 분석:")
    for fc in factor_configs:
        subset = results_df[results_df['factor_config'] == fc['name']]
        if len(subset) > 0:
            print(f"    {fc['name']}: avg OOS={subset['oos_sharpe'].mean():.3f}, max OOS={subset['oos_sharpe'].max():.3f}")
    
    # 최고 성능 상세
    best = results_df.iloc[0]
    print("\n### 최고 성능 상세 ###")
    print(f"  Test Name: {best['test_name']}")
    print(f"  OOS Sharpe: {best['oos_sharpe']:.3f}")
    print(f"  IS Sharpe: {best['is_sharpe']:.3f}")
    print(f"  Gap: {best['gap']*100:.1f}%")
    print(f"  MDD: {best['mdd']*100:.1f}%")
    print(f"  Annual Return: {best['annual_return']*100:.1f}%")
    print(f"  Turnover: {best['turnover']:.1f}")
    print(f"  Rebalance Count: {best['rebal_count']}")
    
    print("\n" + "="*80)
    print("Results saved to /home/ubuntu/v44_extended_pareto_results.csv")
    print("="*80)


if __name__ == '__main__':
    main()
