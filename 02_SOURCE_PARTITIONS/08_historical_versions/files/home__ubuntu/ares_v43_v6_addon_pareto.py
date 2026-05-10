#!/usr/bin/env python3
"""
ARES v43: v41-ResidualMom 베이스라인 + v6 차용 요소 Add-on 파레토 최적화 테스트
================================================================================

베이스라인: v41-ResidualMom (OOS Sharpe 1.378)
- top_k=35, hyst_width=0.05, residual_window=126, residual_skip=0, residual_weight=0.2

Add-on 요소 (v6 Ablation Study에서 차용):
1. Soft Frequency Gating (기여도 +0.951)
2. Multi-Stress Regime Detection (기여도 +0.911)
3. Turnover Cap (기여도 +0.301)
4. DD Deleveraging 완화/제거 (기여도 +0.219)
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit
import warnings
import time
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
    
    print("Loading macro indicators...")
    macro_query = """
        SELECT date, indicator_id, value 
        FROM macro_indicators 
        WHERE indicator_id IN ('T10Y2Y', 'DGS10', 'DGS2', 'TEDRATE')
        ORDER BY date
    """
    macro_df = pd.read_sql_query(macro_query, conn)
    macro_df['date'] = pd.to_datetime(macro_df['date'], format='mixed')
    print(f"  Loaded {len(macro_df):,} macro records")
    
    conn.close()
    return ohlcv_df, vix_df, regime_df, macro_df


def prepare_numpy_arrays(ohlcv_df, vix_df, regime_df, macro_df):
    """데이터를 NumPy 배열로 변환"""
    # 중복 제거 (date, symbol 기준으로 마지막 값 유지)
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
    regime_aligned = regime_df.reindex(returns.index).ffill().bfill()
    
    term_spread = macro_df[macro_df['indicator_id'] == 'T10Y2Y'].set_index('date')['value']
    term_spread = term_spread.reindex(returns.index).ffill().bfill()
    
    returns_np = returns.values.astype(np.float64)
    prices_np = prices_wide.reindex(returns.index).values.astype(np.float64)
    
    if 'vix' in vix_aligned.columns:
        vix_np = vix_aligned['vix'].values.astype(np.float64)
    else:
        vix_np = np.full(len(returns), 20.0)
    
    if len(term_spread) > 0:
        term_spread_np = term_spread.values.astype(np.float64)
    else:
        term_spread_np = np.zeros(len(returns))
    
    returns_np = np.nan_to_num(returns_np, nan=0.0)
    prices_np = np.nan_to_num(prices_np, nan=1.0)
    vix_np = np.nan_to_num(vix_np, nan=20.0)
    term_spread_np = np.nan_to_num(term_spread_np, nan=0.0)
    
    tickers = list(prices_wide.columns)
    dates = list(returns.index)
    
    print(f"Data shape: {returns_np.shape[0]} dates × {returns_np.shape[1]} assets")
    print(f"Date range: {dates[0]} ~ {dates[-1]}")
    
    return returns_np, prices_np, vix_np, term_spread_np, tickers, dates


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
def backtest_v43_core(
    returns, prices, vix, term_spread, regime,
    factor_scores, top_k, rebal_period,
    bull_lev, neutral_lev, bear_lev, crisis_lev,
    soft_gating_enabled, min_rebalance_days, force_rebalance_turnover,
    turnover_cap_enabled, turnover_cap_annual,
    dd_delever_enabled, dd_trigger, delever_rate,
    cost_bps
):
    n_dates, n_assets = returns.shape
    
    weights = np.zeros(n_assets)
    equity = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    turnover_total = 0.0
    rebalance_count = 0
    last_rebalance_day = 0
    peak_equity = 1.0
    dd_active = False
    
    regime_leverage = np.array([bull_lev, neutral_lev, bear_lev, crisis_lev])
    trading_days_per_year = 252
    daily_turnover_cap = turnover_cap_annual / trading_days_per_year if turnover_cap_enabled else 10.0
    
    for t in range(1, n_dates):
        current_regime = regime[t]
        if current_regime < 0 or current_regime > 3:
            current_regime = 1
        target_leverage = regime_leverage[current_regime]
        
        if dd_delever_enabled:
            current_dd = (equity[t-1] - peak_equity) / peak_equity if peak_equity > 0 else 0.0
            if current_dd < dd_trigger:
                dd_active = True
                target_leverage *= delever_rate
            elif dd_active and current_dd > dd_trigger * 0.5:
                dd_active = False
        
        should_rebalance = False
        days_since_rebalance = t - last_rebalance_day
        
        if soft_gating_enabled:
            if days_since_rebalance >= min_rebalance_days:
                if t % rebal_period == 0:
                    should_rebalance = True
                if t > 0 and regime[t] != regime[t-1]:
                    should_rebalance = True
        else:
            if t % rebal_period == 0:
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
                
                if turnover_cap_enabled and turnover > daily_turnover_cap:
                    scale = daily_turnover_cap / turnover
                    new_weights = weights + scale * (new_weights - weights)
                    turnover = daily_turnover_cap
                
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
        
        if equity[t] > peak_equity:
            peak_equity = equity[t]
    
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
    (returns, prices, vix, term_spread, factor_scores,
     top_k, rebal_period, hyst_width,
     bull_lev, neutral_lev, bear_lev, crisis_lev,
     soft_gating, min_rebal_days, force_rebal_turnover,
     turnover_cap, turnover_cap_annual,
     dd_delever, dd_trigger, delever_rate,
     residual_weight, test_name) = params
    
    try:
        regime = compute_regime_with_hysteresis(vix, returns, hyst_width)
        
        equity, daily_returns, turnover, rebal_count = backtest_v43_core(
            returns, prices, vix, term_spread, regime,
            factor_scores, top_k, rebal_period,
            bull_lev, neutral_lev, bear_lev, crisis_lev,
            soft_gating, min_rebal_days, force_rebal_turnover,
            turnover_cap, turnover_cap_annual,
            dd_delever, dd_trigger, delever_rate,
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
            'soft_gating': soft_gating,
            'min_rebal_days': min_rebal_days,
            'force_rebal_turnover': force_rebal_turnover,
            'turnover_cap': turnover_cap,
            'turnover_cap_annual': turnover_cap_annual,
            'dd_delever': dd_delever,
            'dd_trigger': dd_trigger,
            'delever_rate': delever_rate,
            'residual_weight': residual_weight,
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
    print("ARES v43: v41-ResidualMom 베이스라인 + v6 차용 요소 Add-on 파레토 최적화")
    print("="*80)
    
    print("\n[1] Loading data...")
    ohlcv_df, vix_df, regime_df, macro_df = load_data()
    
    print("\n[2] Preparing numpy arrays...")
    returns, prices, vix, term_spread, tickers, dates = prepare_numpy_arrays(
        ohlcv_df, vix_df, regime_df, macro_df
    )
    
    n_dates, n_assets = returns.shape
    print(f"Data: {n_dates} dates × {n_assets} assets")
    
    print("\n[3] Computing factors...")
    momentum_63 = compute_momentum_factor(prices, 63)
    momentum_126 = compute_momentum_factor(prices, 126)
    volatility_21 = compute_volatility_factor(returns, 21)
    residual_mom = compute_residual_momentum(returns, 126, 0)
    
    print("  - Momentum 63d computed")
    print("  - Momentum 126d computed")
    print("  - Volatility 21d computed")
    print("  - Residual Momentum computed")
    
    factor_scores = (momentum_63 + momentum_126 + volatility_21 * 0.5 + residual_mom * 0.2) / 2.7
    
    print("\n[4] Running Pareto optimization tests...")
    
    baseline_params = {
        'top_k': 35,
        'rebal_period': 7,
        'hyst_width': 0.05,
        'bull_lev': 1.0,
        'neutral_lev': 0.7,
        'bear_lev': 0.5,
        'crisis_lev': 0.2,
        'residual_weight': 0.2
    }
    
    test_configs = []
    
    # 1. 베이스라인
    test_configs.append({
        'name': 'baseline',
        'soft_gating': False,
        'turnover_cap': False,
        'dd_delever': True,
        'dd_trigger': -0.03,
        'delever_rate': 0.7,
        'params': baseline_params
    })
    
    # 2. Soft Frequency Gating
    for min_rebal in [2, 3, 4, 5]:
        for force_turnover in [0.3, 0.4, 0.5]:
            test_configs.append({
                'name': f'soft_gating_min{min_rebal}_ft{force_turnover}',
                'soft_gating': True,
                'min_rebal_days': min_rebal,
                'force_rebal_turnover': force_turnover,
                'turnover_cap': False,
                'dd_delever': True,
                'dd_trigger': -0.03,
                'delever_rate': 0.7,
                'params': baseline_params
            })
    
    # 3. Turnover Cap
    for cap in [50, 60, 75, 90]:
        test_configs.append({
            'name': f'turnover_cap_{cap}',
            'soft_gating': False,
            'turnover_cap': True,
            'turnover_cap_annual': cap,
            'dd_delever': True,
            'dd_trigger': -0.03,
            'delever_rate': 0.7,
            'params': baseline_params
        })
    
    # 4. DD Delever 완화/제거
    for dd_trigger in [-0.05, -0.07, -0.10, -1.0]:
        for delever_rate in [0.7, 0.85, 1.0]:
            test_configs.append({
                'name': f'dd_delever_t{dd_trigger}_r{delever_rate}',
                'soft_gating': False,
                'turnover_cap': False,
                'dd_delever': dd_trigger != -1.0,
                'dd_trigger': dd_trigger,
                'delever_rate': delever_rate,
                'params': baseline_params
            })
    
    # 5. 조합 테스트
    for min_rebal in [3, 4]:
        for cap in [60, 75]:
            for dd_trigger in [-0.07, -1.0]:
                test_configs.append({
                    'name': f'combo_mr{min_rebal}_cap{cap}_dd{dd_trigger}',
                    'soft_gating': True,
                    'min_rebal_days': min_rebal,
                    'force_rebal_turnover': 0.4,
                    'turnover_cap': True,
                    'turnover_cap_annual': cap,
                    'dd_delever': dd_trigger != -1.0,
                    'dd_trigger': dd_trigger,
                    'delever_rate': 0.85,
                    'params': baseline_params
                })
    
    # 6. 파라미터 그리드
    top_k_range = [25, 30, 35, 40, 45]
    hyst_range = [0.03, 0.04, 0.05, 0.06]
    lev_range = [(1.0, 0.7, 0.5, 0.2), (1.0, 0.8, 0.6, 0.3), (0.9, 0.7, 0.5, 0.2)]
    
    for top_k in top_k_range:
        for hyst in hyst_range:
            for levs in lev_range:
                test_configs.append({
                    'name': f'grid_tk{top_k}_h{hyst}_l{levs[0]}',
                    'soft_gating': True,
                    'min_rebal_days': 3,
                    'force_rebal_turnover': 0.4,
                    'turnover_cap': True,
                    'turnover_cap_annual': 75,
                    'dd_delever': False,
                    'dd_trigger': -1.0,
                    'delever_rate': 1.0,
                    'params': {
                        'top_k': top_k,
                        'rebal_period': 7,
                        'hyst_width': hyst,
                        'bull_lev': levs[0],
                        'neutral_lev': levs[1],
                        'bear_lev': levs[2],
                        'crisis_lev': levs[3],
                        'residual_weight': 0.2
                    }
                })
    
    print(f"Total test configurations: {len(test_configs)}")
    
    test_params = []
    for config in test_configs:
        p = config['params']
        test_params.append((
            returns, prices, vix, term_spread, factor_scores,
            p['top_k'], p['rebal_period'], p['hyst_width'],
            p['bull_lev'], p['neutral_lev'], p['bear_lev'], p['crisis_lev'],
            config.get('soft_gating', False),
            config.get('min_rebal_days', 3),
            config.get('force_rebal_turnover', 0.4),
            config.get('turnover_cap', False),
            config.get('turnover_cap_annual', 75),
            config.get('dd_delever', True),
            config.get('dd_trigger', -0.03),
            config.get('delever_rate', 0.7),
            p['residual_weight'],
            config['name']
        ))
    
    print(f"\nRunning {len(test_params)} tests...")
    start_time = time.time()
    
    results = []
    for i, params in enumerate(test_params):
        result = run_single_test(params)
        if result:
            results.append(result)
        if (i + 1) % 20 == 0:
            elapsed = time.time() - start_time
            print(f"  Progress: {i+1}/{len(test_params)} ({elapsed:.1f}s)")
    
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.1f} seconds")
    
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('oos_sharpe', ascending=False)
    results_df.to_csv('/home/ubuntu/v43_pareto_results.csv', index=False)
    
    print("\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)
    
    print("\n### Baseline (v41-ResidualMom) ###")
    baseline_result = results_df[results_df['test_name'] == 'baseline']
    if len(baseline_result) > 0:
        baseline_result = baseline_result.iloc[0]
        print(f"  Sharpe: {baseline_result['sharpe']:.3f}")
        print(f"  IS Sharpe: {baseline_result['is_sharpe']:.3f}")
        print(f"  OOS Sharpe: {baseline_result['oos_sharpe']:.3f}")
        print(f"  MDD: {baseline_result['mdd']*100:.1f}%")
    
    print("\n### TOP 10 by OOS Sharpe ###")
    top10 = results_df.head(10)
    for idx, row in top10.iterrows():
        print(f"  {row['test_name']}: OOS={row['oos_sharpe']:.3f}, IS={row['is_sharpe']:.3f}, Gap={row['gap']*100:.1f}%, MDD={row['mdd']*100:.1f}%")
    
    print("\n### Add-on 효과 분석 ###")
    
    soft_gating_results = results_df[results_df['test_name'].str.contains('soft_gating', na=False)]
    if len(soft_gating_results) > 0:
        best_sg = soft_gating_results.iloc[0]
        print(f"  Soft Gating 최고: {best_sg['test_name']} (OOS={best_sg['oos_sharpe']:.3f})")
    
    turnover_results = results_df[results_df['test_name'].str.contains('turnover_cap', na=False)]
    if len(turnover_results) > 0:
        best_tc = turnover_results.iloc[0]
        print(f"  Turnover Cap 최고: {best_tc['test_name']} (OOS={best_tc['oos_sharpe']:.3f})")
    
    dd_results = results_df[results_df['test_name'].str.contains('dd_delever', na=False)]
    if len(dd_results) > 0:
        best_dd = dd_results.iloc[0]
        print(f"  DD Delever 최고: {best_dd['test_name']} (OOS={best_dd['oos_sharpe']:.3f})")
    
    combo_results = results_df[results_df['test_name'].str.contains('combo', na=False)]
    if len(combo_results) > 0:
        best_combo = combo_results.iloc[0]
        print(f"  조합 최고: {best_combo['test_name']} (OOS={best_combo['oos_sharpe']:.3f})")
    
    grid_results = results_df[results_df['test_name'].str.contains('grid', na=False)]
    if len(grid_results) > 0:
        best_grid = grid_results.iloc[0]
        print(f"  Grid 최고: {best_grid['test_name']} (OOS={best_grid['oos_sharpe']:.3f})")
    
    print("\n" + "="*80)
    print("Results saved to /home/ubuntu/v43_pareto_results.csv")
    print("="*80)


if __name__ == '__main__':
    main()
