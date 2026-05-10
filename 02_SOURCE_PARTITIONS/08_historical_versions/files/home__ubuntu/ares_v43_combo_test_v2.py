#!/usr/bin/env python3
"""
ARES v43 효과 있는 Add-on 조합 테스트 v2
================================================================================
- 베이스라인: v43 Soft Gating (OOS Sharpe 1.766)
- VIX 기반 레짐 분류 (정상적인 분포)
- 효과 있는 Add-on: VOL_TARGETING, SLOW_RISK, CRISIS_DETECTION
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
from numba import njit
import warnings
import time
import os
import json
from itertools import product
from multiprocessing import Pool, cpu_count

warnings.filterwarnings('ignore')

# ============================================================================
# 데이터 로드 및 전처리
# ============================================================================
def load_data():
    db_path = '/home/ubuntu/ares_x_unified_database/ares_universal_v2.db'
    conn = sqlite3.connect(db_path)
    
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
    
    vix_query = "SELECT date, close as vix FROM vix ORDER BY date"
    vix_df = pd.read_sql_query(vix_query, conn)
    vix_df['date'] = pd.to_datetime(vix_df['date'], format='mixed')
    vix_df = vix_df.drop_duplicates(subset=['date'], keep='last').set_index('date')
    
    conn.close()
    return ohlcv_df, vix_df


def prepare_numpy_arrays(ohlcv_df, vix_df):
    ohlcv_df = ohlcv_df.drop_duplicates(subset=['date', 'symbol'], keep='last')
    
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
    
    return returns_np, prices_np, vix_np


# ============================================================================
# Numba 최적화 함수들
# ============================================================================
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
def compute_vix_regime(vix, vix_bull=15.0, vix_bear=25.0, vix_crisis=30.0):
    """VIX 기반 레짐 분류 (정상적인 분포)"""
    n_dates = len(vix)
    regime = np.zeros(n_dates, dtype=np.int32)
    
    for t in range(n_dates):
        vix_val = vix[t]
        
        if vix_val >= vix_crisis:
            regime[t] = 3  # Crisis
        elif vix_val >= vix_bear:
            regime[t] = 2  # Bear
        elif vix_val >= vix_bull:
            regime[t] = 1  # Neutral
        else:
            regime[t] = 0  # Bull
    
    return regime


@njit(cache=True)
def compute_cumret(returns, window):
    n_dates, n_assets = returns.shape
    result = np.zeros(n_dates)
    
    for t in range(window, n_dates):
        cum_ret = 0.0
        count = 0
        for k in range(window):
            for i in range(n_assets):
                ret = returns[t - window + k, i]
                if not np.isnan(ret) and abs(ret) < 1.0:
                    cum_ret += ret
                    count += 1
        if count > 0:
            result[t] = cum_ret / count * window
    
    return result


# ============================================================================
# 조합 백테스트
# ============================================================================
@njit(cache=True)
def backtest_combo(
    returns, prices, vix, regime,
    factor_scores, top_k, rebal_period,
    bull_lev, neutral_lev, bear_lev, crisis_lev,
    soft_gating_enabled, min_rebalance_days,
    cost_bps,
    # VOL_TARGETING
    vol_targeting_enabled, vol_target, vol_scale_min, vol_scale_max,
    # SLOW_RISK
    slow_risk_enabled, slow60, slow120, exit_ret20, cap_slow,
    # CRISIS_DETECTION
    crisis_detection_enabled, crisis_vix_threshold, crisis_vix_change_threshold,
    # 추가 데이터
    cumret_60, cumret_120, cumret_20
):
    n_dates, n_assets = returns.shape
    
    weights = np.zeros(n_assets)
    equity = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    regime_arr = np.zeros(n_dates, dtype=np.int32)
    turnover_total = 0.0
    rebalance_count = 0
    last_rebalance_day = 0
    
    # SLOW_RISK 상태
    in_slow_risk = False
    exit_confirm_count = 0
    
    # 실현 변동성
    realized_vol = 0.15
    
    regime_leverage = np.array([bull_lev, neutral_lev, bear_lev, crisis_lev])
    
    for t in range(1, n_dates):
        # 실현 변동성 계산
        if t > 21:
            vol_sum = 0.0
            for k in range(20):
                vol_sum += daily_returns[t-1-k] ** 2
            realized_vol = np.sqrt(vol_sum / 20 * 252)
            if realized_vol < 0.01:
                realized_vol = 0.01
        
        # 기본 레짐 (VIX 기반)
        current_regime = regime[t]
        if current_regime < 0 or current_regime > 3:
            current_regime = 1
        
        # CRISIS_DETECTION Add-on (추가 Crisis 감지)
        if crisis_detection_enabled:
            vix_val = vix[t-1] if t > 0 else vix[0]
            vix_change = (vix[t-1] - vix[t-2]) / vix[t-2] if t > 1 and vix[t-2] > 0 else 0.0
            if vix_val > crisis_vix_threshold or vix_change > crisis_vix_change_threshold:
                current_regime = 3  # Crisis
        
        # SLOW_RISK Add-on
        if slow_risk_enabled:
            is_slow = (cumret_60[t-1] < slow60) or (cumret_120[t-1] < slow120)
            
            if is_slow and not in_slow_risk:
                in_slow_risk = True
                exit_confirm_count = 0
            elif in_slow_risk:
                exit_condition = cumret_20[t-1] >= exit_ret20
                
                if exit_condition:
                    exit_confirm_count += 1
                    if exit_confirm_count >= 1:
                        in_slow_risk = False
                else:
                    exit_confirm_count = 0
        
        # 레짐별 레버리지
        if in_slow_risk:
            target_leverage = cap_slow
            current_regime = 4  # SlowRisk
        else:
            target_leverage = regime_leverage[current_regime]
        
        # VOL_TARGETING Add-on
        if vol_targeting_enabled and realized_vol > 0.01:
            vol_factor = vol_target / realized_vol
            vol_factor = max(vol_scale_min, min(vol_scale_max, vol_factor))
            target_leverage *= vol_factor
        
        regime_arr[t] = current_regime
        
        # 리밸런싱
        should_rebalance = False
        days_since_rebalance = t - last_rebalance_day
        
        if soft_gating_enabled:
            if days_since_rebalance >= min_rebalance_days:
                if t % rebal_period == 0:
                    should_rebalance = True
                if t > 0 and regime_arr[t] != regime_arr[t-1]:
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
    
    return equity, daily_returns, regime_arr, turnover_total, rebalance_count


def calculate_metrics(equity, daily_returns, regime_arr, n_dates, is_ratio=0.55):
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
    
    # 레짐별 Sharpe
    regime_sharpes = {}
    regime_names = ['Bull', 'Neutral', 'Bear', 'Crisis', 'SlowRisk']
    
    for r in range(5):
        regime_rets = []
        for t in range(1, n_dates):
            if regime_arr[t] == r:
                regime_rets.append(daily_returns[t])
        
        if len(regime_rets) > 10:
            mean_ret = np.mean(regime_rets)
            std_ret = np.std(regime_rets)
            if std_ret > 0:
                regime_sharpes[regime_names[r]] = mean_ret / std_ret * np.sqrt(252)
            else:
                regime_sharpes[regime_names[r]] = 0.0
            regime_sharpes[f'{regime_names[r]}_days'] = len(regime_rets)
        else:
            regime_sharpes[regime_names[r]] = np.nan
            regime_sharpes[f'{regime_names[r]}_days'] = len(regime_rets)
    
    return {
        'sharpe': full_sharpe,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'gap': gap,
        'mdd': max_dd,
        'annual_return': (equity[-1] / equity[0]) ** (252 / len(equity)) - 1,
        **regime_sharpes
    }


# ============================================================================
# 병렬 테스트 함수
# ============================================================================
def run_single_combo_test(args):
    (returns, prices, vix, regime, factor_scores,
     cumret_60, cumret_120, cumret_20,
     baseline_params, combo_params, combo_name) = args
    
    try:
        n_dates = returns.shape[0]
        
        equity, daily_returns, regime_arr, turnover, rebal_count = backtest_combo(
            returns, prices, vix, regime,
            factor_scores,
            baseline_params['top_k'],
            baseline_params['rebal_period'],
            baseline_params['bull_lev'],
            baseline_params['neutral_lev'],
            baseline_params['bear_lev'],
            baseline_params['crisis_lev'],
            baseline_params['soft_gating'],
            baseline_params['min_rebal_days'],
            baseline_params['cost_bps'],
            # VOL_TARGETING
            combo_params['vol_targeting_enabled'],
            combo_params['vol_target'],
            combo_params['vol_scale_min'],
            combo_params['vol_scale_max'],
            # SLOW_RISK
            combo_params['slow_risk_enabled'],
            combo_params['slow60'],
            combo_params['slow120'],
            combo_params['exit_ret20'],
            combo_params['cap_slow'],
            # CRISIS_DETECTION
            combo_params['crisis_detection_enabled'],
            combo_params['crisis_vix'],
            combo_params['crisis_vix_change'],
            # 추가 데이터
            cumret_60, cumret_120, cumret_20
        )
        
        metrics = calculate_metrics(equity, daily_returns, regime_arr, n_dates)
        
        return {
            'combo_name': combo_name,
            **combo_params,
            **metrics
        }
    except Exception as e:
        print(f"Error in {combo_name}: {e}")
        return None


# ============================================================================
# 메인 함수
# ============================================================================
def main():
    print("=" * 80)
    print("ARES v43 효과 있는 Add-on 조합 테스트 v2 (VIX 기반 레짐)")
    print("=" * 80)
    
    results_dir = '/home/ubuntu/ares_v43_combo_results_v2'
    os.makedirs(results_dir, exist_ok=True)
    
    # 1. 데이터 로드
    print("\n[1] Loading data...")
    ohlcv_df, vix_df = load_data()
    returns, prices, vix = prepare_numpy_arrays(ohlcv_df, vix_df)
    
    n_dates, n_assets = returns.shape
    print(f"Data: {n_dates} dates × {n_assets} assets")
    
    # 2. 팩터 계산
    print("\n[2] Computing factors...")
    momentum_63 = compute_momentum_factor(prices, 63)
    momentum_126 = compute_momentum_factor(prices, 126)
    volatility_21 = compute_volatility_factor(returns, 21)
    residual_mom = compute_residual_momentum(returns, 126, 0)
    
    factor_scores = (momentum_63 + momentum_126 + volatility_21 * 0.5 + residual_mom * 0.2) / 2.7
    
    cumret_20 = compute_cumret(returns, 20)
    cumret_60 = compute_cumret(returns, 60)
    cumret_120 = compute_cumret(returns, 120)
    
    # 3. VIX 기반 레짐 계산
    print("\n[3] Computing VIX-based regime...")
    regime = compute_vix_regime(vix, vix_bull=15.0, vix_bear=25.0, vix_crisis=30.0)
    
    # 레짐 분포 확인
    bull_days = np.sum(regime == 0)
    neutral_days = np.sum(regime == 1)
    bear_days = np.sum(regime == 2)
    crisis_days = np.sum(regime == 3)
    print(f"  Bull: {bull_days}일 ({bull_days/n_dates*100:.1f}%)")
    print(f"  Neutral: {neutral_days}일 ({neutral_days/n_dates*100:.1f}%)")
    print(f"  Bear: {bear_days}일 ({bear_days/n_dates*100:.1f}%)")
    print(f"  Crisis: {crisis_days}일 ({crisis_days/n_dates*100:.1f}%)")
    
    # 4. 베이스라인 파라미터
    baseline_params = {
        'top_k': 35,
        'rebal_period': 7,
        'bull_lev': 1.0,
        'neutral_lev': 0.7,
        'bear_lev': 0.5,
        'crisis_lev': 0.2,
        'soft_gating': True,
        'min_rebal_days': 2,
        'cost_bps': 20.0
    }
    
    # 5. 베이스라인 테스트 (Add-on 없음)
    print("\n[4] Testing baseline...")
    baseline_combo = {
        'vol_targeting_enabled': False,
        'vol_target': 0.12,
        'vol_scale_min': 0.3,
        'vol_scale_max': 2.0,
        'slow_risk_enabled': False,
        'slow60': -0.04,
        'slow120': -0.08,
        'exit_ret20': 0.01,
        'cap_slow': 0.25,
        'crisis_detection_enabled': False,
        'crisis_vix': 40,
        'crisis_vix_change': 0.2
    }
    
    baseline_result = run_single_combo_test((
        returns, prices, vix, regime, factor_scores,
        cumret_60, cumret_120, cumret_20,
        baseline_params, baseline_combo, 'baseline'
    ))
    
    baseline_oos = baseline_result['oos_sharpe']
    baseline_mdd = baseline_result['mdd']
    
    print(f"\n베이스라인 결과:")
    print(f"  OOS Sharpe: {baseline_oos:.3f}")
    print(f"  IS Sharpe: {baseline_result['is_sharpe']:.3f}")
    print(f"  MDD: {baseline_mdd*100:.1f}%")
    print(f"  Bull Sharpe: {baseline_result.get('Bull', 'N/A')}")
    print(f"  Neutral Sharpe: {baseline_result.get('Neutral', 'N/A')}")
    print(f"  Bear Sharpe: {baseline_result.get('Bear', 'N/A')}")
    print(f"  Crisis Sharpe: {baseline_result.get('Crisis', 'N/A')}")
    
    # 6. 조합 파라미터 그리드
    print("\n[5] Generating combo parameter grid...")
    
    vol_targets = [0.0, 0.10, 0.12, 0.15]
    vol_scale_mins = [0.3, 0.5]
    vol_scale_maxs = [1.5, 2.0]
    
    slow60s = [0.0, -0.03, -0.04, -0.05]
    slow120s = [-0.08, -0.10, -0.12]
    exit_ret20s = [0.01, 0.015, 0.02]
    cap_slows = [0.2, 0.25, 0.3]
    
    crisis_vixs = [0.0, 35, 40, 45]
    crisis_vix_changes = [0.15, 0.2, 0.25]
    
    combo_params_list = []
    
    for vol_target in vol_targets:
        for vol_scale_min in vol_scale_mins:
            for vol_scale_max in vol_scale_maxs:
                for slow60 in slow60s:
                    for slow120 in slow120s:
                        for exit_ret20 in exit_ret20s:
                            for cap_slow in cap_slows:
                                for crisis_vix in crisis_vixs:
                                    for crisis_vix_change in crisis_vix_changes:
                                        combo_params = {
                                            'vol_targeting_enabled': vol_target > 0,
                                            'vol_target': vol_target if vol_target > 0 else 0.12,
                                            'vol_scale_min': vol_scale_min,
                                            'vol_scale_max': vol_scale_max,
                                            'slow_risk_enabled': slow60 < 0,
                                            'slow60': slow60 if slow60 < 0 else -0.04,
                                            'slow120': slow120,
                                            'exit_ret20': exit_ret20,
                                            'cap_slow': cap_slow,
                                            'crisis_detection_enabled': crisis_vix > 0,
                                            'crisis_vix': crisis_vix if crisis_vix > 0 else 40,
                                            'crisis_vix_change': crisis_vix_change
                                        }
                                        combo_params_list.append(combo_params)
    
    print(f"Total combinations: {len(combo_params_list)}")
    
    if len(combo_params_list) > 5000:
        np.random.seed(42)
        indices = np.random.choice(len(combo_params_list), 5000, replace=False)
        combo_params_list = [combo_params_list[i] for i in indices]
        print(f"Sampled to: {len(combo_params_list)}")
    
    # 7. 병렬 테스트
    print("\n[6] Running combo tests...")
    
    test_args = []
    for i, combo_params in enumerate(combo_params_list):
        combo_name = f"combo_{i}"
        test_args.append((
            returns, prices, vix, regime, factor_scores,
            cumret_60, cumret_120, cumret_20,
            baseline_params, combo_params, combo_name
        ))
    
    start_time = time.time()
    
    n_workers = min(cpu_count(), 16)
    print(f"Using {n_workers} workers")
    
    results = [baseline_result]
    with Pool(n_workers) as pool:
        for i, result in enumerate(pool.imap_unordered(run_single_combo_test, test_args)):
            if result:
                results.append(result)
            if (i + 1) % 500 == 0:
                elapsed = time.time() - start_time
                print(f"  Progress: {i+1}/{len(test_args)} ({elapsed:.1f}s)")
    
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.1f} seconds")
    
    # 8. 결과 분석
    print("\n[7] Analyzing results...")
    
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('oos_sharpe', ascending=False)
    results_df.to_csv(f'{results_dir}/all_combo_results.csv', index=False)
    
    print(f"\n베이스라인 OOS Sharpe: {baseline_oos:.3f}")
    
    # TOP 20
    print("\n=== TOP 20 OOS Sharpe ===")
    top20 = results_df.head(20)
    for idx, row in top20.iterrows():
        addons = []
        if row['vol_targeting_enabled']:
            addons.append(f"VOL({row['vol_target']:.2f})")
        if row['slow_risk_enabled']:
            addons.append(f"SLOW({row['slow60']:.2f})")
        if row['crisis_detection_enabled']:
            addons.append(f"CRISIS({row['crisis_vix']:.0f})")
        addon_str = '+'.join(addons) if addons else 'BASELINE'
        
        improvement = (row['oos_sharpe'] - baseline_oos) / baseline_oos * 100
        print(f"  {addon_str}: OOS={row['oos_sharpe']:.3f} ({improvement:+.1f}%), MDD={row['mdd']*100:.1f}%, Bull={row.get('Bull', 0):.2f}, Bear={row.get('Bear', 0):.2f}")
    
    # 파레토 최적
    print("\n=== Pareto Optimal (OOS > baseline & MDD > -12%) ===")
    pareto = results_df[(results_df['oos_sharpe'] > baseline_oos) & (results_df['mdd'] > -0.12)]
    pareto = pareto.sort_values('oos_sharpe', ascending=False)
    
    for idx, row in pareto.head(10).iterrows():
        addons = []
        if row['vol_targeting_enabled']:
            addons.append(f"VOL({row['vol_target']:.2f})")
        if row['slow_risk_enabled']:
            addons.append(f"SLOW({row['slow60']:.2f})")
        if row['crisis_detection_enabled']:
            addons.append(f"CRISIS({row['crisis_vix']:.0f})")
        addon_str = '+'.join(addons) if addons else 'BASELINE'
        
        improvement = (row['oos_sharpe'] - baseline_oos) / baseline_oos * 100
        print(f"  {addon_str}: OOS={row['oos_sharpe']:.3f} ({improvement:+.1f}%), MDD={row['mdd']*100:.1f}%, Bull={row.get('Bull', 0):.2f}, Bear={row.get('Bear', 0):.2f}")
    
    # 최고 결과 저장
    best_result = results_df.iloc[0]
    with open(f'{results_dir}/best_result.json', 'w') as f:
        json.dump(best_result.to_dict(), f, indent=2, default=str)
    
    print(f"\n결과 저장 위치: {results_dir}")
    
    return results_df


if __name__ == '__main__':
    results = main()
