#!/usr/bin/env python3
"""
ARES v43 원본 기반 Add-on 테스트
================================================================================
- v43 원본 백테스트 로직 100% 유지
- v46~v47.12 Add-on만 개별적으로 추가 테스트
- 베이스라인 대비 효과 측정
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

warnings.filterwarnings('ignore')

# ============================================================================
# v43 원본 데이터 로드 함수 (100% 동일)
# ============================================================================
def load_data():
    """EC2 데이터베이스에서 데이터 로드 (v43 원본과 동일)"""
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
    """데이터를 NumPy 배열로 변환 (v43 원본과 동일)"""
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


# ============================================================================
# v43 원본 팩터 계산 함수 (100% 동일)
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


# ============================================================================
# v43 원본 레짐 분류 (100% 동일)
# ============================================================================
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


# ============================================================================
# v43 원본 백테스트 코어 (100% 동일)
# ============================================================================
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


# ============================================================================
# v43 원본 성과 계산 (100% 동일)
# ============================================================================
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


# ============================================================================
# 레짐별 Sharpe 계산 (추가)
# ============================================================================
def calculate_regime_sharpes(daily_returns, regime, n_dates):
    """레짐별 Sharpe 계산"""
    regime_sharpes = {}
    regime_names = ['Bull', 'Neutral', 'Bear', 'Crisis']
    
    for r in range(4):
        regime_rets = []
        for t in range(1, n_dates):
            if regime[t] == r:
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
    
    return regime_sharpes


# ============================================================================
# v46~v47.12 Add-on 정의
# ============================================================================
ADDONS = {
    # v46 SLOW_RISK 로직
    'A1_SLOW_RISK': {
        'description': 'v46 SLOW_RISK 진입/탈출 로직',
        'params': {
            'slow60': [-0.02, -0.03, -0.04, -0.05, -0.06],
            'slow120': [-0.04, -0.06, -0.08, -0.10, -0.12],
            'exit_ret20': [0.01, 0.015, 0.02, 0.025, 0.03],
            'cap_slow': [0.2, 0.25, 0.3, 0.35, 0.4]
        }
    },
    # v46 Crisis 감지
    'A2_CRISIS_DETECTION': {
        'description': 'v46 Crisis VIX 감지',
        'params': {
            'crisis_vix': [25, 28, 30, 35, 40],
            'crisis_vix_change': [0.1, 0.15, 0.2, 0.25, 0.3]
        }
    },
    # v46 Bull Guard
    'A3_BULL_GUARD': {
        'description': 'v46 Bull 익스포저 상한',
        'params': {
            'bull_cap': [0.6, 0.7, 0.8, 0.9, 1.0]
        }
    },
    # v47.12 Vol Targeting
    'B1_VOL_TARGETING': {
        'description': 'v47.12 변동성 타겟팅',
        'params': {
            'vol_target': [0.08, 0.10, 0.12, 0.15],
            'vol_scale_min': [0.3, 0.5],
            'vol_scale_max': [1.5, 2.0]
        }
    },
    # v47.12 MR Boost
    'B2_MR_NEUTRAL_BOOST': {
        'description': 'v47.12 Neutral MR 강화',
        'params': {
            'mr_boost': [0.0, 0.2, 0.3, 0.5, 0.7]
        }
    },
    # 4대 AI 제안: Fast Track Exit
    'C1_FAST_EXIT': {
        'description': '4대 AI 제안: Fast Track Exit',
        'params': {
            'fast_exit_ma': [10, 15, 20, 30],
            'fast_exit_threshold': [0.5, 0.6, 0.7]
        }
    },
    # 4대 AI 제안: Graduated Recovery
    'C2_GRADUATED_RECOVERY': {
        'description': '4대 AI 제안: 단계적 회복',
        'params': {
            'recovery_steps': [2, 3, 4, 5]
        }
    }
}


# ============================================================================
# 확장된 백테스트 (Add-on 포함)
# ============================================================================
@njit(cache=True)
def backtest_with_addons(
    returns, prices, vix, term_spread, regime,
    factor_scores, top_k, rebal_period,
    bull_lev, neutral_lev, bear_lev, crisis_lev,
    soft_gating_enabled, min_rebalance_days, force_rebalance_turnover,
    turnover_cap_enabled, turnover_cap_annual,
    dd_delever_enabled, dd_trigger, delever_rate,
    cost_bps,
    # Add-on 파라미터
    slow_risk_enabled, slow60, slow120, exit_ret20, cap_slow,
    crisis_detection_enabled, crisis_vix_threshold, crisis_vix_change_threshold,
    bull_guard_enabled, bull_cap,
    vol_targeting_enabled, vol_target, vol_scale_min, vol_scale_max,
    mr_boost_enabled, mr_boost,
    fast_exit_enabled, fast_exit_ma, fast_exit_threshold,
    graduated_recovery_enabled, recovery_steps,
    cumret_60, cumret_120, cumret_20, ma_20
):
    n_dates, n_assets = returns.shape
    
    weights = np.zeros(n_assets)
    equity = np.ones(n_dates)
    daily_returns = np.zeros(n_dates)
    regime_arr = np.zeros(n_dates, dtype=np.int32)
    turnover_total = 0.0
    rebalance_count = 0
    last_rebalance_day = 0
    peak_equity = 1.0
    dd_active = False
    
    # SLOW_RISK 상태
    in_slow_risk = False
    exit_confirm_count = 0
    recovery_step = 0
    
    # 실현 변동성
    realized_vol = 0.15
    
    regime_leverage = np.array([bull_lev, neutral_lev, bear_lev, crisis_lev])
    trading_days_per_year = 252
    daily_turnover_cap = turnover_cap_annual / trading_days_per_year if turnover_cap_enabled else 10.0
    
    for t in range(1, n_dates):
        # 실현 변동성 계산
        if t > 21:
            vol_sum = 0.0
            for k in range(20):
                vol_sum += daily_returns[t-1-k] ** 2
            realized_vol = np.sqrt(vol_sum / 20 * 252)
            if realized_vol < 0.01:
                realized_vol = 0.01
        
        # 기본 레짐
        current_regime = regime[t]
        if current_regime < 0 or current_regime > 3:
            current_regime = 1
        
        # Crisis Detection Add-on
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
                recovery_step = 0
            elif in_slow_risk:
                exit_condition = cumret_20[t-1] >= exit_ret20
                
                # Fast Exit Add-on
                if fast_exit_enabled:
                    above_ma_count = 0
                    total_count = 0
                    for i in range(n_assets):
                        if prices[t-1, i] > 0 and ma_20[t-1, i] > 0:
                            if prices[t-1, i] > ma_20[t-1, i]:
                                above_ma_count += 1
                            total_count += 1
                    if total_count > 0 and above_ma_count / total_count > fast_exit_threshold:
                        exit_condition = True
                
                if exit_condition:
                    exit_confirm_count += 1
                    if exit_confirm_count >= 1:
                        if graduated_recovery_enabled and recovery_steps > 1:
                            recovery_step += 1
                            if recovery_step >= recovery_steps:
                                in_slow_risk = False
                                recovery_step = 0
                        else:
                            in_slow_risk = False
                else:
                    exit_confirm_count = 0
        
        # 레짐별 레버리지
        if in_slow_risk:
            target_leverage = cap_slow
            if graduated_recovery_enabled and recovery_step > 0:
                recovery_ratio = recovery_step / recovery_steps
                target_leverage = cap_slow + (neutral_lev - cap_slow) * recovery_ratio
            current_regime = 4  # SlowRisk (확장)
        else:
            target_leverage = regime_leverage[current_regime]
        
        # Bull Guard Add-on
        if bull_guard_enabled and current_regime == 0:
            target_leverage = min(target_leverage, bull_cap)
        
        # Vol Targeting Add-on
        if vol_targeting_enabled and realized_vol > 0.01:
            vol_factor = vol_target / realized_vol
            vol_factor = max(vol_scale_min, min(vol_scale_max, vol_factor))
            target_leverage *= vol_factor
        
        # DD Deleveraging
        if dd_delever_enabled:
            current_dd = (equity[t-1] - peak_equity) / peak_equity if peak_equity > 0 else 0.0
            if current_dd < dd_trigger:
                dd_active = True
                target_leverage *= delever_rate
            elif dd_active and current_dd > dd_trigger * 0.5:
                dd_active = False
        
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
            scores = factor_scores[t, :].copy()
            
            # MR Boost Add-on (Neutral에서)
            if mr_boost_enabled and current_regime == 1:
                for i in range(n_assets):
                    if not np.isnan(scores[i]):
                        # 역추세 점수 추가
                        mr_score = -factor_scores[t, i]  # 역추세
                        scores[i] = scores[i] * (1 - mr_boost * 0.3) + mr_score * mr_boost * 0.3
            
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
    
    return equity, daily_returns, regime_arr, turnover_total, rebalance_count


# ============================================================================
# 추가 팩터 계산
# ============================================================================
@njit(cache=True)
def compute_cumret(returns, window):
    """누적 수익률 계산"""
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


@njit(cache=True)
def compute_ma(prices, window):
    """이동 평균 계산"""
    n_dates, n_assets = prices.shape
    result = np.zeros((n_dates, n_assets))
    
    for t in range(window, n_dates):
        for i in range(n_assets):
            ma_sum = 0.0
            for k in range(window):
                ma_sum += prices[t - window + k, i]
            result[t, i] = ma_sum / window
    
    return result


# ============================================================================
# 메인 함수
# ============================================================================
def main():
    print("=" * 80)
    print("ARES v43 원본 기반 Add-on 테스트")
    print("=" * 80)
    
    # 결과 디렉토리
    results_dir = '/home/ubuntu/ares_v43_addon_results'
    os.makedirs(results_dir, exist_ok=True)
    
    # 1. 데이터 로드
    print("\n[1] Loading data...")
    ohlcv_df, vix_df, regime_df, macro_df = load_data()
    
    print("\n[2] Preparing numpy arrays...")
    returns, prices, vix, term_spread, tickers, dates = prepare_numpy_arrays(
        ohlcv_df, vix_df, regime_df, macro_df
    )
    
    n_dates, n_assets = returns.shape
    print(f"Data: {n_dates} dates × {n_assets} assets")
    
    # 2. 팩터 계산 (v43 원본)
    print("\n[3] Computing factors...")
    momentum_63 = compute_momentum_factor(prices, 63)
    momentum_126 = compute_momentum_factor(prices, 126)
    volatility_21 = compute_volatility_factor(returns, 21)
    residual_mom = compute_residual_momentum(returns, 126, 0)
    
    # v43 원본 팩터 점수
    factor_scores = (momentum_63 + momentum_126 + volatility_21 * 0.5 + residual_mom * 0.2) / 2.7
    
    # 추가 팩터 (Add-on용)
    cumret_20 = compute_cumret(returns, 20)
    cumret_60 = compute_cumret(returns, 60)
    cumret_120 = compute_cumret(returns, 120)
    ma_20 = compute_ma(prices, 20)
    
    print("  - Factors computed")
    
    # 3. 레짐 계산 (v43 원본)
    hyst_width = 0.05
    regime = compute_regime_with_hysteresis(vix, returns, hyst_width)
    
    # 4. v43 베이스라인 테스트
    print("\n[4] Running v43 baseline test...")
    
    baseline_params = {
        'top_k': 35,
        'rebal_period': 7,
        'bull_lev': 1.0,
        'neutral_lev': 0.7,
        'bear_lev': 0.5,
        'crisis_lev': 0.2,
        'soft_gating': True,
        'min_rebal_days': 2,
        'force_rebal_turnover': 0.3,
        'turnover_cap': False,
        'turnover_cap_annual': 75,
        'dd_delever': True,
        'dd_trigger': -0.03,
        'delever_rate': 0.7,
        'cost_bps': 20.0
    }
    
    # 베이스라인 실행
    equity, daily_returns, turnover, rebal_count = backtest_v43_core(
        returns, prices, vix, term_spread, regime,
        factor_scores,
        baseline_params['top_k'],
        baseline_params['rebal_period'],
        baseline_params['bull_lev'],
        baseline_params['neutral_lev'],
        baseline_params['bear_lev'],
        baseline_params['crisis_lev'],
        baseline_params['soft_gating'],
        baseline_params['min_rebal_days'],
        baseline_params['force_rebal_turnover'],
        baseline_params['turnover_cap'],
        baseline_params['turnover_cap_annual'],
        baseline_params['dd_delever'],
        baseline_params['dd_trigger'],
        baseline_params['delever_rate'],
        baseline_params['cost_bps']
    )
    
    baseline_metrics = calculate_metrics(equity, daily_returns, n_dates)
    baseline_regime_sharpes = calculate_regime_sharpes(daily_returns, regime, n_dates)
    
    print(f"\n=== v43 베이스라인 결과 ===")
    print(f"  IS Sharpe: {baseline_metrics['is_sharpe']:.3f}")
    print(f"  OOS Sharpe: {baseline_metrics['oos_sharpe']:.3f}")
    print(f"  MDD: {baseline_metrics['mdd']*100:.1f}%")
    print(f"  Annual Return: {baseline_metrics['annual_return']*100:.1f}%")
    print(f"  Bull Sharpe: {baseline_regime_sharpes.get('Bull', 0):.2f}")
    print(f"  Neutral Sharpe: {baseline_regime_sharpes.get('Neutral', 0):.2f}")
    print(f"  Bear Sharpe: {baseline_regime_sharpes.get('Bear', 0):.2f}")
    print(f"  Crisis Sharpe: {baseline_regime_sharpes.get('Crisis', 0):.2f}")
    
    # 5. 개별 Add-on A/B 테스트
    print("\n[5] Running Add-on A/B tests...")
    
    all_results = []
    addon_summaries = []
    
    for addon_name, addon_config in ADDONS.items():
        print(f"\n--- Testing {addon_name}: {addon_config['description']} ---")
        
        # 파라미터 그리드 생성
        param_keys = list(addon_config['params'].keys())
        param_values = list(addon_config['params'].values())
        param_grid = list(product(*param_values))
        
        print(f"  Parameter combinations: {len(param_grid)}")
        
        addon_results = []
        
        for params in param_grid:
            param_dict = {k: v for k, v in zip(param_keys, params)}
            
            # Add-on 파라미터 설정
            addon_params = {
                'slow_risk_enabled': addon_name == 'A1_SLOW_RISK',
                'slow60': param_dict.get('slow60', -0.04),
                'slow120': param_dict.get('slow120', -0.08),
                'exit_ret20': param_dict.get('exit_ret20', 0.02),
                'cap_slow': param_dict.get('cap_slow', 0.35),
                'crisis_detection_enabled': addon_name == 'A2_CRISIS_DETECTION',
                'crisis_vix_threshold': param_dict.get('crisis_vix', 30.0),
                'crisis_vix_change_threshold': param_dict.get('crisis_vix_change', 0.2),
                'bull_guard_enabled': addon_name == 'A3_BULL_GUARD',
                'bull_cap': param_dict.get('bull_cap', 1.0),
                'vol_targeting_enabled': addon_name == 'B1_VOL_TARGETING',
                'vol_target': param_dict.get('vol_target', 0.0),
                'vol_scale_min': param_dict.get('vol_scale_min', 0.5),
                'vol_scale_max': param_dict.get('vol_scale_max', 1.5),
                'mr_boost_enabled': addon_name == 'B2_MR_NEUTRAL_BOOST',
                'mr_boost': param_dict.get('mr_boost', 0.0),
                'fast_exit_enabled': addon_name == 'C1_FAST_EXIT',
                'fast_exit_ma': param_dict.get('fast_exit_ma', 20),
                'fast_exit_threshold': param_dict.get('fast_exit_threshold', 0.6),
                'graduated_recovery_enabled': addon_name == 'C2_GRADUATED_RECOVERY',
                'recovery_steps': param_dict.get('recovery_steps', 3)
            }
            
            try:
                equity, daily_returns, regime_arr, turnover, rebal_count = backtest_with_addons(
                    returns, prices, vix, term_spread, regime,
                    factor_scores,
                    baseline_params['top_k'],
                    baseline_params['rebal_period'],
                    baseline_params['bull_lev'],
                    baseline_params['neutral_lev'],
                    baseline_params['bear_lev'],
                    baseline_params['crisis_lev'],
                    baseline_params['soft_gating'],
                    baseline_params['min_rebal_days'],
                    baseline_params['force_rebal_turnover'],
                    baseline_params['turnover_cap'],
                    baseline_params['turnover_cap_annual'],
                    baseline_params['dd_delever'],
                    baseline_params['dd_trigger'],
                    baseline_params['delever_rate'],
                    baseline_params['cost_bps'],
                    addon_params['slow_risk_enabled'],
                    addon_params['slow60'],
                    addon_params['slow120'],
                    addon_params['exit_ret20'],
                    addon_params['cap_slow'],
                    addon_params['crisis_detection_enabled'],
                    addon_params['crisis_vix_threshold'],
                    addon_params['crisis_vix_change_threshold'],
                    addon_params['bull_guard_enabled'],
                    addon_params['bull_cap'],
                    addon_params['vol_targeting_enabled'],
                    addon_params['vol_target'],
                    addon_params['vol_scale_min'],
                    addon_params['vol_scale_max'],
                    addon_params['mr_boost_enabled'],
                    addon_params['mr_boost'],
                    addon_params['fast_exit_enabled'],
                    addon_params['fast_exit_ma'],
                    addon_params['fast_exit_threshold'],
                    addon_params['graduated_recovery_enabled'],
                    addon_params['recovery_steps'],
                    cumret_60, cumret_120, cumret_20, ma_20
                )
                
                metrics = calculate_metrics(equity, daily_returns, n_dates)
                
                result = {
                    'addon': addon_name,
                    'params': str(param_dict),
                    'is_sharpe': metrics['is_sharpe'],
                    'oos_sharpe': metrics['oos_sharpe'],
                    'mdd': metrics['mdd'],
                    'annual_return': metrics['annual_return'],
                    'oos_improvement': (metrics['oos_sharpe'] - baseline_metrics['oos_sharpe']) / abs(baseline_metrics['oos_sharpe']) * 100,
                    'mdd_change': (metrics['mdd'] - baseline_metrics['mdd']) / abs(baseline_metrics['mdd']) * 100
                }
                
                addon_results.append(result)
                all_results.append(result)
                
            except Exception as e:
                print(f"    Error: {e}")
        
        # Add-on 요약
        if addon_results:
            df = pd.DataFrame(addon_results)
            pareto = df[df['oos_improvement'] > 0].sort_values('oos_sharpe', ascending=False)
            
            if len(pareto) > 0:
                best = pareto.iloc[0]
                print(f"  Best: OOS={best['oos_sharpe']:.3f} (+{best['oos_improvement']:.1f}%), MDD={best['mdd']*100:.1f}%")
                addon_summaries.append({
                    'addon': addon_name,
                    'best_params': best['params'],
                    'oos_sharpe': best['oos_sharpe'],
                    'oos_improvement': best['oos_improvement'],
                    'mdd': best['mdd'],
                    'effective': True
                })
            else:
                print(f"  No improvement found")
                addon_summaries.append({
                    'addon': addon_name,
                    'best_params': 'N/A',
                    'oos_sharpe': baseline_metrics['oos_sharpe'],
                    'oos_improvement': 0,
                    'mdd': baseline_metrics['mdd'],
                    'effective': False
                })
    
    # 6. 결과 저장
    print("\n[6] Saving results...")
    
    all_df = pd.DataFrame(all_results)
    all_df.to_csv(f'{results_dir}/all_addon_results.csv', index=False)
    
    summary_df = pd.DataFrame(addon_summaries)
    summary_df = summary_df.sort_values('oos_improvement', ascending=False)
    summary_df.to_csv(f'{results_dir}/addon_summary.csv', index=False)
    
    # 7. 최종 요약
    print("\n" + "=" * 80)
    print("최종 요약")
    print("=" * 80)
    
    print(f"\n베이스라인 (v43 Soft Gating):")
    print(f"  OOS Sharpe: {baseline_metrics['oos_sharpe']:.3f}")
    print(f"  MDD: {baseline_metrics['mdd']*100:.1f}%")
    
    print(f"\n효과 있는 Add-on (OOS 개선 > 0%):")
    effective = summary_df[summary_df['effective']]
    for _, row in effective.iterrows():
        print(f"  {row['addon']}: OOS +{row['oos_improvement']:.1f}%")
    
    print(f"\n효과 없는 Add-on:")
    ineffective = summary_df[~summary_df['effective']]
    for _, row in ineffective.iterrows():
        print(f"  {row['addon']}")
    
    print(f"\n결과 저장 위치: {results_dir}")
    
    return summary_df


if __name__ == '__main__':
    results = main()
