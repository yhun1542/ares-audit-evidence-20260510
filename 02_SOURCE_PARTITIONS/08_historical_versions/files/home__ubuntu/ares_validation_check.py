#!/usr/bin/env python3
"""
ARES 최적 조합 검증: Look-ahead Bias 체크 + Walk-Forward 검증
================================================================================
- Look-ahead Bias 체크: 신호 생성과 체결 시점 분리 검증
- Walk-Forward 검증: 롤링 윈도우 방식으로 강건성 테스트
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
    
    dates = returns.index.tolist()
    
    return returns_np, prices_np, vix_np, dates


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
    n_dates = len(vix)
    regime = np.zeros(n_dates, dtype=np.int32)
    
    for t in range(n_dates):
        vix_val = vix[t]
        
        if vix_val >= vix_crisis:
            regime[t] = 3
        elif vix_val >= vix_bear:
            regime[t] = 2
        elif vix_val >= vix_bull:
            regime[t] = 1
        else:
            regime[t] = 0
    
    return regime


# ============================================================================
# Look-ahead Bias 체크 백테스트
# ============================================================================
@njit(cache=True)
def backtest_with_lag_check(
    returns, prices, vix, regime,
    factor_scores, top_k, rebal_period,
    bull_lev, neutral_lev, bear_lev, crisis_lev,
    min_rebalance_days, cost_bps,
    vol_target, vol_scale_min, vol_scale_max,
    signal_lag  # 신호 지연 (0=당일, 1=전일, 2=2일전)
):
    """
    Look-ahead Bias 체크를 위한 백테스트
    signal_lag: 신호 생성에 사용하는 데이터의 지연 일수
    - signal_lag=0: 당일 데이터로 신호 생성 (Look-ahead Bias 가능성)
    - signal_lag=1: 전일 데이터로 신호 생성 (정상)
    - signal_lag=2: 2일전 데이터로 신호 생성 (보수적)
    """
    n_dates, n_assets = returns.shape
    
    weights = np.zeros(n_assets)
    equity = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    
    realized_vol = 0.15
    regime_leverage = np.array([bull_lev, neutral_lev, bear_lev, crisis_lev])
    last_rebalance_day = 0
    
    for t in range(signal_lag + 1, n_dates):
        # 실현 변동성 계산 (전일까지의 데이터만 사용)
        if t > 21:
            vol_sum = 0.0
            for k in range(20):
                vol_sum += daily_returns[t-1-k] ** 2
            realized_vol = np.sqrt(vol_sum / 20 * 252)
            if realized_vol < 0.01:
                realized_vol = 0.01
        
        # 레짐 결정 (signal_lag 적용)
        regime_idx = t - signal_lag
        if regime_idx < 0:
            regime_idx = 0
        current_regime = regime[regime_idx]
        if current_regime < 0 or current_regime > 3:
            current_regime = 1
        
        target_leverage = regime_leverage[current_regime]
        
        # VOL_TARGETING (전일 변동성 사용)
        if vol_target > 0 and realized_vol > 0.01:
            vol_factor = vol_target / realized_vol
            vol_factor = max(vol_scale_min, min(vol_scale_max, vol_factor))
            target_leverage *= vol_factor
        
        # 리밸런싱
        should_rebalance = False
        days_since_rebalance = t - last_rebalance_day
        
        if days_since_rebalance >= min_rebalance_days:
            if t % rebal_period == 0:
                should_rebalance = True
        
        if should_rebalance:
            # 팩터 점수 (signal_lag 적용)
            score_idx = t - signal_lag
            if score_idx < 0:
                score_idx = 0
            scores = factor_scores[score_idx, :]
            
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
                last_rebalance_day = t
        
        # 포트폴리오 수익률 (당일 수익률 적용)
        port_return = 0.0
        for i in range(n_assets):
            port_return += weights[i] * returns[t, i]
        
        daily_returns[t] = port_return
        equity[t] = equity[t-1] * (1 + port_return)
    
    return equity, daily_returns


def calculate_sharpe(daily_returns, start_idx, end_idx):
    rets = daily_returns[start_idx:end_idx]
    if len(rets) < 10:
        return np.nan
    mean_ret = np.mean(rets)
    std_ret = np.std(rets)
    if std_ret == 0:
        return 0.0
    return mean_ret / std_ret * np.sqrt(252)


def calculate_mdd(equity, start_idx, end_idx):
    eq = equity[start_idx:end_idx]
    peak = eq[0]
    max_dd = 0.0
    for i in range(len(eq)):
        if eq[i] > peak:
            peak = eq[i]
        dd = (eq[i] - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


# ============================================================================
# 메인 함수
# ============================================================================
def main():
    print("=" * 80)
    print("ARES 최적 조합 검증: Look-ahead Bias 체크 + Walk-Forward 검증")
    print("=" * 80)
    
    results_dir = '/home/ubuntu/ares_validation_results'
    os.makedirs(results_dir, exist_ok=True)
    
    # 1. 데이터 로드
    print("\n[1] Loading data...")
    ohlcv_df, vix_df = load_data()
    returns, prices, vix, dates = prepare_numpy_arrays(ohlcv_df, vix_df)
    
    n_dates, n_assets = returns.shape
    print(f"Data: {n_dates} dates × {n_assets} assets")
    print(f"Period: {dates[0]} ~ {dates[-1]}")
    
    # 2. 팩터 계산
    print("\n[2] Computing factors...")
    momentum_63 = compute_momentum_factor(prices, 63)
    momentum_126 = compute_momentum_factor(prices, 126)
    volatility_21 = compute_volatility_factor(returns, 21)
    residual_mom = compute_residual_momentum(returns, 126, 0)
    
    factor_scores = (momentum_63 + momentum_126 + volatility_21 * 0.5 + residual_mom * 0.2) / 2.7
    
    # 3. VIX 기반 레짐 계산
    regime = compute_vix_regime(vix, vix_bull=15.0, vix_bear=25.0, vix_crisis=30.0)
    
    # 4. 최적 파라미터 설정
    params = {
        'top_k': 35,
        'rebal_period': 7,
        'bull_lev': 1.0,
        'neutral_lev': 0.7,
        'bear_lev': 0.5,
        'crisis_lev': 0.2,
        'min_rebal_days': 2,
        'cost_bps': 20.0,
        'vol_target': 0.10,
        'vol_scale_min': 0.3,
        'vol_scale_max': 2.0
    }
    
    # ========================================================================
    # Part 1: Look-ahead Bias 체크
    # ========================================================================
    print("\n" + "=" * 80)
    print("Part 1: Look-ahead Bias 체크")
    print("=" * 80)
    
    is_ratio = 0.55
    is_end = int(n_dates * is_ratio)
    
    lag_results = []
    
    for signal_lag in [0, 1, 2]:
        print(f"\n--- Signal Lag = {signal_lag}일 ---")
        
        equity, daily_returns = backtest_with_lag_check(
            returns, prices, vix, regime,
            factor_scores,
            params['top_k'], params['rebal_period'],
            params['bull_lev'], params['neutral_lev'],
            params['bear_lev'], params['crisis_lev'],
            params['min_rebal_days'], params['cost_bps'],
            params['vol_target'], params['vol_scale_min'], params['vol_scale_max'],
            signal_lag
        )
        
        is_sharpe = calculate_sharpe(daily_returns, 1, is_end)
        oos_sharpe = calculate_sharpe(daily_returns, is_end, n_dates)
        full_sharpe = calculate_sharpe(daily_returns, 1, n_dates)
        mdd = calculate_mdd(equity, 0, n_dates)
        
        print(f"  IS Sharpe: {is_sharpe:.3f}")
        print(f"  OOS Sharpe: {oos_sharpe:.3f}")
        print(f"  Full Sharpe: {full_sharpe:.3f}")
        print(f"  MDD: {mdd*100:.1f}%")
        
        lag_results.append({
            'signal_lag': signal_lag,
            'is_sharpe': is_sharpe,
            'oos_sharpe': oos_sharpe,
            'full_sharpe': full_sharpe,
            'mdd': mdd
        })
    
    # Look-ahead Bias 분석
    print("\n--- Look-ahead Bias 분석 ---")
    lag0_oos = lag_results[0]['oos_sharpe']
    lag1_oos = lag_results[1]['oos_sharpe']
    lag2_oos = lag_results[2]['oos_sharpe']
    
    bias_0_to_1 = (lag0_oos - lag1_oos) / lag1_oos * 100 if lag1_oos != 0 else 0
    bias_1_to_2 = (lag1_oos - lag2_oos) / lag2_oos * 100 if lag2_oos != 0 else 0
    
    print(f"  Lag 0 → 1 성능 변화: {bias_0_to_1:+.1f}%")
    print(f"  Lag 1 → 2 성능 변화: {bias_1_to_2:+.1f}%")
    
    if abs(bias_0_to_1) > 10:
        print("  ⚠️ 경고: Lag 0→1 성능 차이가 10% 이상! Look-ahead Bias 가능성 있음")
        lookahead_bias_detected = True
    else:
        print("  ✅ Lag 0→1 성능 차이가 10% 미만. Look-ahead Bias 낮음")
        lookahead_bias_detected = False
    
    # ========================================================================
    # Part 2: Walk-Forward 검증
    # ========================================================================
    print("\n" + "=" * 80)
    print("Part 2: Walk-Forward 검증")
    print("=" * 80)
    
    # Walk-Forward 설정
    train_window = 1260  # 5년
    test_window = 252    # 1년
    step_size = 126      # 6개월씩 이동
    
    wf_results = []
    fold = 0
    
    start_idx = 0
    while start_idx + train_window + test_window <= n_dates:
        fold += 1
        train_start = start_idx
        train_end = start_idx + train_window
        test_start = train_end
        test_end = min(train_end + test_window, n_dates)
        
        print(f"\n--- Fold {fold} ---")
        print(f"  Train: {dates[train_start].strftime('%Y-%m-%d')} ~ {dates[train_end-1].strftime('%Y-%m-%d')}")
        print(f"  Test: {dates[test_start].strftime('%Y-%m-%d')} ~ {dates[test_end-1].strftime('%Y-%m-%d')}")
        
        # 백테스트 (signal_lag=1 사용)
        equity, daily_returns = backtest_with_lag_check(
            returns, prices, vix, regime,
            factor_scores,
            params['top_k'], params['rebal_period'],
            params['bull_lev'], params['neutral_lev'],
            params['bear_lev'], params['crisis_lev'],
            params['min_rebal_days'], params['cost_bps'],
            params['vol_target'], params['vol_scale_min'], params['vol_scale_max'],
            signal_lag=1  # Look-ahead Bias 방지
        )
        
        train_sharpe = calculate_sharpe(daily_returns, train_start, train_end)
        test_sharpe = calculate_sharpe(daily_returns, test_start, test_end)
        test_mdd = calculate_mdd(equity, test_start, test_end)
        
        print(f"  Train Sharpe: {train_sharpe:.3f}")
        print(f"  Test Sharpe: {test_sharpe:.3f}")
        print(f"  Test MDD: {test_mdd*100:.1f}%")
        
        wf_results.append({
            'fold': fold,
            'train_start': dates[train_start].strftime('%Y-%m-%d'),
            'train_end': dates[train_end-1].strftime('%Y-%m-%d'),
            'test_start': dates[test_start].strftime('%Y-%m-%d'),
            'test_end': dates[test_end-1].strftime('%Y-%m-%d'),
            'train_sharpe': train_sharpe,
            'test_sharpe': test_sharpe,
            'test_mdd': test_mdd
        })
        
        start_idx += step_size
    
    # Walk-Forward 분석
    print("\n--- Walk-Forward 분석 ---")
    test_sharpes = [r['test_sharpe'] for r in wf_results if not np.isnan(r['test_sharpe'])]
    test_mdds = [r['test_mdd'] for r in wf_results if not np.isnan(r['test_mdd'])]
    
    if len(test_sharpes) > 0:
        avg_test_sharpe = np.mean(test_sharpes)
        std_test_sharpe = np.std(test_sharpes)
        min_test_sharpe = np.min(test_sharpes)
        max_test_sharpe = np.max(test_sharpes)
        pct_positive = sum(1 for s in test_sharpes if s > 0) / len(test_sharpes) * 100
        pct_above_1 = sum(1 for s in test_sharpes if s > 1.0) / len(test_sharpes) * 100
        
        print(f"  총 Fold 수: {len(wf_results)}")
        print(f"  평균 Test Sharpe: {avg_test_sharpe:.3f} (±{std_test_sharpe:.3f})")
        print(f"  최소/최대 Test Sharpe: {min_test_sharpe:.3f} / {max_test_sharpe:.3f}")
        print(f"  Sharpe > 0 비율: {pct_positive:.1f}%")
        print(f"  Sharpe > 1.0 비율: {pct_above_1:.1f}%")
        print(f"  평균 Test MDD: {np.mean(test_mdds)*100:.1f}%")
    
    # ========================================================================
    # 최종 결과 저장
    # ========================================================================
    print("\n" + "=" * 80)
    print("최종 검증 결과")
    print("=" * 80)
    
    validation_result = {
        'lookahead_bias': {
            'detected': lookahead_bias_detected,
            'lag_results': lag_results,
            'bias_0_to_1_pct': bias_0_to_1,
            'bias_1_to_2_pct': bias_1_to_2
        },
        'walk_forward': {
            'total_folds': len(wf_results),
            'avg_test_sharpe': avg_test_sharpe if len(test_sharpes) > 0 else None,
            'std_test_sharpe': std_test_sharpe if len(test_sharpes) > 0 else None,
            'min_test_sharpe': min_test_sharpe if len(test_sharpes) > 0 else None,
            'max_test_sharpe': max_test_sharpe if len(test_sharpes) > 0 else None,
            'pct_positive': pct_positive if len(test_sharpes) > 0 else None,
            'pct_above_1': pct_above_1 if len(test_sharpes) > 0 else None,
            'avg_test_mdd': np.mean(test_mdds) if len(test_mdds) > 0 else None,
            'fold_results': wf_results
        },
        'optimal_params': params,
        'recommendation': {
            'use_signal_lag': 1,
            'reason': 'Look-ahead Bias 방지를 위해 signal_lag=1 사용 권장'
        }
    }
    
    with open(f'{results_dir}/validation_result.json', 'w') as f:
        json.dump(validation_result, f, indent=2, default=str)
    
    # 요약
    print("\n=== 검증 요약 ===")
    print(f"1. Look-ahead Bias: {'⚠️ 감지됨' if lookahead_bias_detected else '✅ 없음'}")
    print(f"2. Walk-Forward 평균 Sharpe: {avg_test_sharpe:.3f} (±{std_test_sharpe:.3f})")
    print(f"3. Walk-Forward Sharpe > 1.0 비율: {pct_above_1:.1f}%")
    print(f"4. 권장 signal_lag: 1일")
    
    if not lookahead_bias_detected and pct_above_1 >= 70:
        print("\n✅ 검증 통과: 전략이 강건합니다!")
    elif not lookahead_bias_detected and pct_above_1 >= 50:
        print("\n⚠️ 부분 통과: 전략이 대체로 강건하지만 일부 기간에서 성능 저하")
    else:
        print("\n❌ 검증 실패: 전략 개선 필요")
    
    print(f"\n결과 저장 위치: {results_dir}")
    
    return validation_result


if __name__ == '__main__':
    result = main()
