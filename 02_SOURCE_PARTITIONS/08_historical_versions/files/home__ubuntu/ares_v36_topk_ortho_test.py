#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARES v36: top_k 변형 + Orthogonal 팩터 효과 테스트
================================================================================
top_k를 줄여서 팩터 기반 종목 선택이 실제로 작동하는지 확인
- top_k: 10, 15, 20, 25, 30, 35, 40
- Orthogonal 팩터 가중치: 다양한 조합
- 패치 조합: v33/v35 최적 조합 기반
================================================================================
"""

import numpy as np
import pandas as pd
import sqlite3
import json
import time
import os
from datetime import datetime
from typing import Tuple, Dict, List, Optional
import itertools
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("ARES v36: top_k 변형 + Orthogonal 팩터 효과 테스트")
print("=" * 80)
print(f"시작 시간: {datetime.now()}")

# ============================================================================
# 글로벌 데이터
# ============================================================================
PRICES_NP = None
RETURNS_NP = None
FACTORS_NP = None
ORTHO_FACTORS_NP = None
REGIME_SCORES_NP = None
FAST_REGIME_NP = None
ELIGIBILITY_NP = None
BETAS_NP = None
N_DAYS = 0
N_ASSETS = 0


def init_global_data():
    """데이터 초기화 + Orthogonal 팩터 계산"""
    global PRICES_NP, RETURNS_NP, FACTORS_NP, ORTHO_FACTORS_NP, REGIME_SCORES_NP
    global FAST_REGIME_NP, ELIGIBILITY_NP, BETAS_NP, N_DAYS, N_ASSETS
    
    print("\n[1] 데이터 로드 중...")
    
    # === 주식 데이터 로드 ===
    conn = sqlite3.connect('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE date >= '2016-01-01' AND date <= '2024-12-31'
    ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df = df.drop_duplicates(subset=['date', 'symbol'], keep='last')
    prices = df.pivot(index='date', columns='symbol', values='close')
    prices.index = pd.to_datetime(prices.index)
    
    valid_cols = prices.columns[prices.notna().mean() > 0.8]
    prices = prices[valid_cols].ffill()
    
    print("  포인트-인-타임 유니버스 계산 중...")
    avail = prices.notna().astype(float)
    ratio = avail.rolling(252, min_periods=252).mean()
    eligibility = (ratio >= 0.95) & prices.shift(1).notna()
    eligibility.iloc[:252] = False
    ELIGIBILITY_NP = eligibility.values.astype(np.float64)
    
    PRICES_NP = prices.values.astype(np.float64)
    
    returns = prices.pct_change()
    RETURNS_NP = returns.values.astype(np.float64)
    RETURNS_NP[0, :] = 0
    RETURNS_NP = np.nan_to_num(RETURNS_NP, nan=0.0)
    
    N_DAYS, N_ASSETS = PRICES_NP.shape
    print(f"  주식: {N_ASSETS}종목, {N_DAYS}일")
    
    # === ETF 데이터 로드 ===
    conn = sqlite3.connect('/home/ubuntu/etf_data_s3.db', timeout=30)
    query = """
    SELECT date, symbol, close FROM daily_ohlcv
    WHERE symbol IN ('QQQ', 'DIA', 'HYG', 'LQD', 'SPY')
    AND date >= '2015-01-01' ORDER BY date, symbol
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    etf = df.pivot(index='date', columns='symbol', values='close')
    etf.index = pd.to_datetime(etf.index)
    etf = etf.ffill()
    
    common_dates = prices.index.intersection(etf.index)
    
    # === 레짐 점수 계산 ===
    print("\n[2] 레짐 점수 계산 중...")
    mkt = etf['QQQ'].reindex(common_dates) if 'QQQ' in etf.columns else etf['DIA'].reindex(common_dates)
    mkt_ret = mkt.pct_change()
    sma_20 = mkt.rolling(20).mean()
    sma_50 = mkt.rolling(50).mean()
    sma_200 = mkt.rolling(200).mean()
    
    trend_score = pd.Series(0.5, index=common_dates)
    trend_score[mkt > sma_200] += 0.20
    trend_score[sma_50 > sma_200] += 0.15
    trend_score[mkt > sma_50] += 0.10
    trend_score[mkt > sma_20] += 0.05
    trend_score[mkt < sma_200] -= 0.20
    trend_score[(mkt < sma_200) & (sma_50 < sma_200)] -= 0.15
    trend_score[mkt < sma_20] -= 0.05
    trend_score = trend_score.clip(0, 1)
    
    realized_vol = mkt_ret.rolling(20).std() * np.sqrt(252)
    hist_vol = mkt_ret.rolling(252).std() * np.sqrt(252)
    vol_ratio = realized_vol / (hist_vol + 1e-8)
    vol_score = 1 - vol_ratio.clip(0.5, 2.0) / 2.0
    vol_score = vol_score.clip(0, 1)
    
    if 'HYG' in etf.columns and 'LQD' in etf.columns:
        hyg = etf['HYG'].reindex(common_dates)
        lqd = etf['LQD'].reindex(common_dates)
        credit_ratio = hyg / lqd
        credit_ma = credit_ratio.rolling(60).mean()
        credit_std = credit_ratio.rolling(252).std()
        credit_zscore = (credit_ratio - credit_ma) / (credit_std + 1e-8)
        credit_score = 0.5 + credit_zscore.clip(-2, 2) / 4
        credit_score = credit_score.clip(0, 1)
    else:
        credit_score = pd.Series(0.5, index=common_dates)
    
    cum_ret_5d = (1 + mkt_ret).rolling(5).apply(lambda x: x.prod() - 1, raw=True)
    fast_regime = pd.Series(0.5, index=common_dates)
    fast_regime[cum_ret_5d < -0.05] = 0.1
    fast_regime[cum_ret_5d < -0.03] = 0.25
    fast_regime[cum_ret_5d < -0.01] = 0.35
    fast_regime[cum_ret_5d > 0.03] = 0.8
    fast_regime[cum_ret_5d > 0.05] = 0.9
    
    sma_200_stk = prices.rolling(200).mean()
    breadth = (prices > sma_200_stk).mean(axis=1).clip(0, 1)
    
    regime_scores = pd.DataFrame({
        'trend': trend_score,
        'vol': vol_score,
        'credit': credit_score,
        'breadth': breadth.reindex(common_dates)
    }).reindex(prices.index).ffill().fillna(0.5)
    
    REGIME_SCORES_NP = regime_scores.values.astype(np.float64)
    
    fast_regime_aligned = fast_regime.reindex(prices.index).ffill().fillna(0.5)
    FAST_REGIME_NP = fast_regime_aligned.values.astype(np.float64)
    
    # === 기존 팩터 계산 ===
    print("\n[3] 기존 팩터 계산 중...")
    mom_12 = prices.pct_change(252)
    mom_1 = prices.pct_change(21)
    mom_6 = prices.pct_change(126)
    mom_3 = prices.pct_change(63)
    mom_5 = prices.pct_change(5)
    mom_10 = prices.pct_change(10)
    high_52w = prices.rolling(252).max()
    low_52w = prices.rolling(252).min()
    
    factors = {}
    factors['mom_12_1'] = (mom_12 - mom_1).rank(axis=1, pct=True)
    factors['mom_6_1'] = (mom_6 - mom_1).rank(axis=1, pct=True)
    factors['low_vol'] = (-returns.rolling(63).std()).rank(axis=1, pct=True)
    rolling_ret = returns.rolling(252).mean()
    rolling_vol = returns.rolling(252).std()
    factors['quality'] = (rolling_ret / (rolling_vol + 1e-8)).rank(axis=1, pct=True)
    factors['reversal'] = (-mom_5).rank(axis=1, pct=True)
    factors['near_high'] = (prices / high_52w).rank(axis=1, pct=True)
    mom_accel = mom_3 - mom_3.shift(21)
    factors['acceleration'] = mom_accel.rank(axis=1, pct=True)
    mkt_ret_series = returns.mean(axis=1)
    excess_ret = returns.sub(mkt_ret_series, axis=0).rolling(63).mean()
    factors['rel_strength'] = excess_ret.rank(axis=1, pct=True)
    factors['range_position'] = ((prices - low_52w) / (high_52w - low_52w + 1e-8)).rank(axis=1, pct=True)
    factors['mom_10'] = mom_10.rank(axis=1, pct=True)
    
    FACTORS_NP = np.stack([
        factors['mom_12_1'].values,
        factors['mom_6_1'].values,
        factors['low_vol'].values,
        factors['quality'].values,
        factors['reversal'].values,
        factors['near_high'].values,
        factors['acceleration'].values,
        factors['rel_strength'].values,
        factors['range_position'].values,
        factors['mom_10'].values
    ], axis=2).astype(np.float64)
    
    FACTORS_NP = np.nan_to_num(FACTORS_NP, nan=0.5)
    print(f"  기존 팩터: 10개")
    
    # === Orthogonal 팩터 계산 ===
    print("\n[4] Orthogonal 팩터 계산 중...")
    
    if 'SPY' in etf.columns:
        mkt_benchmark = etf['SPY'].reindex(prices.index).ffill()
    else:
        mkt_benchmark = etf['QQQ'].reindex(prices.index).ffill()
    mkt_benchmark_ret = mkt_benchmark.pct_change().fillna(0)
    
    print("  - 베타 계산...")
    betas = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    for col in prices.columns:
        stock_ret = returns[col]
        for t in range(252, len(prices)):
            window_stock = stock_ret.iloc[t-252:t].values
            window_mkt = mkt_benchmark_ret.iloc[t-252:t].values
            
            cov = np.cov(window_stock, window_mkt)[0, 1]
            var_mkt = np.var(window_mkt)
            if var_mkt > 1e-10:
                betas.iloc[t, betas.columns.get_loc(col)] = cov / var_mkt
            else:
                betas.iloc[t, betas.columns.get_loc(col)] = 1.0
    
    betas = betas.ffill().fillna(1.0)
    BETAS_NP = betas.values.astype(np.float64)
    
    print("  - E1: Residual Momentum 계산...")
    mkt_ret_expanded = mkt_benchmark_ret.values.reshape(-1, 1)
    expected_returns = mkt_ret_expanded * betas.values
    residual_returns = returns - expected_returns
    residual_mom_12 = residual_returns.rolling(252).sum()
    residual_mom_1 = residual_returns.rolling(21).sum()
    e1_residual_mom = (residual_mom_12 - residual_mom_1).rank(axis=1, pct=True)
    
    print("  - E2: Idiosyncratic Volatility 계산...")
    idio_vol = residual_returns.rolling(63).std()
    e2_idio_vol = (-idio_vol).rank(axis=1, pct=True)
    
    print("  - E3: BAB 계산...")
    e3_bab = (-betas).rank(axis=1, pct=True)
    
    ORTHO_FACTORS_NP = np.stack([
        e1_residual_mom.values,
        e2_idio_vol.values,
        e3_bab.values
    ], axis=2).astype(np.float64)
    
    ORTHO_FACTORS_NP = np.nan_to_num(ORTHO_FACTORS_NP, nan=0.5)
    print(f"  Orthogonal 팩터: 3개 (E1, E2, E3)")
    
    print(f"\n  데이터 준비 완료!")
    
    return N_DAYS, N_ASSETS


def fast_backtest(params: Dict) -> Dict:
    """순수 NumPy 백테스트"""
    
    n_days, n_assets = N_DAYS, N_ASSETS
    n_factors = 10
    
    trend_w = params['trend_w']
    vol_w = params['vol_w']
    credit_w = params['credit_w']
    breadth_w = params.get('breadth_w', 0.0)
    fast_w = params.get('fast_w', 0.0)
    
    crisis_thresh = params['crisis_thresh']
    bear_thresh = params['bear_thresh']
    neutral_thresh = params['neutral_thresh']
    bull_thresh = params['bull_thresh']
    
    ultra_bull_exp = params['ultra_bull_exp']
    bull_exp = params['bull_exp']
    neutral_exp = params['neutral_exp']
    bear_exp = params['bear_exp']
    crisis_exp = params['crisis_exp']
    
    fw_ultra_bull = np.array(params['fw_ultra_bull'])
    fw_bull = np.array(params['fw_bull'])
    fw_neutral = np.array(params['fw_neutral'])
    fw_bear = np.array(params['fw_bear'])
    fw_crisis = np.array(params['fw_crisis'])
    
    ortho_w = np.array(params.get('ortho_weights', [0.0, 0.0, 0.0]))
    
    top_k = params['top_k']
    rebal_days = params['rebal_days']
    target_vol = params['target_vol']
    max_lev = params['max_lev']
    min_lev = params['min_lev']
    cost_rate = params['cost_rate']
    dd_warning = params['dd_warning']
    dd_stop = params['dd_stop']
    
    use_pit_universe = params.get('use_pit_universe', False)
    use_graduated_dd = params.get('use_graduated_dd', False)
    use_emergency_riskoff = params.get('use_emergency_riskoff', False)
    use_cvar_limit = params.get('use_cvar_limit', False)
    use_real_port_vol = params.get('use_real_port_vol', False)
    use_hysteresis = params.get('use_hysteresis', False)
    use_fast_regime = params.get('use_fast_regime', False)
    use_breadth = params.get('use_breadth', False)
    use_adaptive_rebal = params.get('use_adaptive_rebal', False)
    use_regime_factor_weights = params.get('use_regime_factor_weights', False)
    use_orthogonal_factors = params.get('use_orthogonal_factors', False)
    
    hysteresis_width = params.get('hysteresis_width', 0.03)
    emergency_crisis_trigger = params.get('emergency_crisis_trigger', 0.75)
    emergency_loss_trigger = params.get('emergency_loss_trigger', -0.04)
    emergency_max_exp = params.get('emergency_max_exp', 0.25)
    cvar_limit = params.get('cvar_limit', -0.03)
    
    values = np.zeros(n_days)
    values[0] = 1.0
    peak = 1.0
    
    prev_weights = np.zeros(n_assets)
    current_weights = np.zeros(n_assets)
    
    daily_returns = np.zeros(n_days)
    
    port_ret_buffer = []
    realized_vol_current = 0.20
    
    current_regime_level = 2
    
    for t in range(1, n_days):
        composite = (REGIME_SCORES_NP[t-1, 0] * trend_w + 
                    REGIME_SCORES_NP[t-1, 1] * vol_w + 
                    REGIME_SCORES_NP[t-1, 2] * credit_w)
        
        if use_breadth and breadth_w > 0:
            composite += REGIME_SCORES_NP[t-1, 3] * breadth_w
        
        if use_fast_regime and fast_w > 0:
            total_w = trend_w + vol_w + credit_w + (breadth_w if use_breadth else 0)
            composite = composite / total_w * (1 - fast_w) + FAST_REGIME_NP[t-1] * fast_w
        else:
            total_w = trend_w + vol_w + credit_w + (breadth_w if use_breadth else 0)
            composite = composite / total_w
        
        crisis_prob = 1 - composite
        
        raw_level = 2
        if crisis_prob >= crisis_thresh:
            raw_level = 4
        elif crisis_prob >= bear_thresh:
            raw_level = 3
        elif crisis_prob >= neutral_thresh:
            raw_level = 2
        elif crisis_prob >= bull_thresh:
            raw_level = 1
        else:
            raw_level = 0
        
        if use_hysteresis:
            thresholds = [0.0, bull_thresh, neutral_thresh, bear_thresh, crisis_thresh]
            if raw_level > current_regime_level and current_regime_level < 4:
                next_thresh = thresholds[current_regime_level + 1]
                if crisis_prob >= next_thresh + hysteresis_width:
                    current_regime_level += 1
            elif raw_level < current_regime_level and current_regime_level > 0:
                curr_thresh = thresholds[current_regime_level]
                if crisis_prob < curr_thresh - hysteresis_width:
                    current_regime_level -= 1
        else:
            current_regime_level = raw_level
        
        if current_regime_level == 4:
            base_exp, fw = crisis_exp, fw_crisis
        elif current_regime_level == 3:
            base_exp, fw = bear_exp, fw_bear
        elif current_regime_level == 2:
            base_exp, fw = neutral_exp, fw_neutral
        elif current_regime_level == 1:
            base_exp, fw = bull_exp, fw_bull
        else:
            base_exp, fw = ultra_bull_exp, fw_ultra_bull
        
        if use_regime_factor_weights:
            if current_regime_level >= 3:
                fw = fw.copy()
                fw[2] *= 1.3
                fw[3] *= 1.3
                fw = fw / fw.sum()
        
        if use_adaptive_rebal:
            if realized_vol_current < 0.15:
                interval = rebal_days
            elif realized_vol_current < 0.25:
                interval = max(7, int(rebal_days * 0.7))
            else:
                interval = max(5, int(rebal_days * 0.5))
            should_rebalance = (t % interval == 0)
        else:
            should_rebalance = (t % rebal_days == 0)
        
        if should_rebalance:
            scores = np.zeros(n_assets)
            for i in range(n_assets):
                if use_pit_universe and ELIGIBILITY_NP[t-1, i] < 0.5:
                    scores[i] = -999.0
                else:
                    base_score = np.sum(FACTORS_NP[t-1, i, :] * fw)
                    
                    if use_orthogonal_factors and np.sum(np.abs(ortho_w)) > 0:
                        ortho_score = np.sum(ORTHO_FACTORS_NP[t-1, i, :] * ortho_w)
                        total_base_w = np.sum(fw)
                        total_ortho_w = np.sum(np.abs(ortho_w))
                        scores[i] = (base_score * total_base_w + ortho_score * total_ortho_w) / (total_base_w + total_ortho_w)
                    else:
                        scores[i] = base_score
            
            sorted_idx = np.argsort(-scores)
            top_idx = sorted_idx[:top_k]
            
            if use_real_port_vol and len(port_ret_buffer) >= 20:
                rv = np.std(port_ret_buffer[-20:]) * np.sqrt(252)
                realized_vol_current = rv
                if rv > 0.01:
                    vol_adj = min(2.0, max(0.5, target_vol / rv))
                else:
                    vol_adj = 1.0
            else:
                vol_adj = 1.0
            
            if use_cvar_limit and len(port_ret_buffer) >= 20:
                sorted_rets = np.sort(port_ret_buffer[-60:])
                cutoff = max(1, int(len(sorted_rets) * 0.05))
                cvar = np.mean(sorted_rets[:cutoff])
                if cvar < cvar_limit:
                    reduction = min(0.5, (cvar_limit - cvar) / abs(cvar_limit))
                    base_exp = base_exp * (1 - reduction)
            
            exposure = min(max_lev, max(min_lev, base_exp * vol_adj))
            
            current_weights = np.zeros(n_assets)
            valid_count = 0
            for idx in top_idx:
                if scores[idx] > -900:
                    valid_count += 1
            
            if exposure > 0.05 and valid_count > 0:
                weight_per_stock = exposure / valid_count
                for idx in top_idx:
                    if scores[idx] > -900:
                        current_weights[idx] = weight_per_stock
        
        current_dd = (values[t-1] - peak) / peak if peak > 0 else 0
        
        if use_graduated_dd:
            if current_dd >= 0:
                dd_scale = 1.0
            elif current_dd >= dd_warning:
                dd_scale = 1.0 - 0.3 * (current_dd / dd_warning)
            elif current_dd >= dd_stop:
                dd_scale = 0.7 - 0.4 * ((current_dd - dd_warning) / (dd_stop - dd_warning))
            else:
                dd_scale = 0.3
        else:
            if current_dd <= dd_stop:
                dd_scale = 0.3
            elif current_dd <= dd_warning:
                dd_scale = 0.7
            else:
                dd_scale = 1.0
        
        active_weights = current_weights * dd_scale
        
        if use_emergency_riskoff:
            prev_ret = daily_returns[t-1] if t > 1 else 0
            if crisis_prob >= emergency_crisis_trigger or prev_ret <= emergency_loss_trigger:
                total_exp = np.sum(active_weights)
                if total_exp > emergency_max_exp:
                    active_weights = active_weights * (emergency_max_exp / total_exp)
        
        turnover = np.sum(np.abs(active_weights - prev_weights))
        cost = turnover * cost_rate
        
        port_ret = np.sum(active_weights * RETURNS_NP[t, :]) - cost
        
        port_ret_buffer.append(port_ret)
        if len(port_ret_buffer) > 60:
            port_ret_buffer.pop(0)
        
        daily_returns[t] = port_ret
        values[t] = values[t-1] * (1 + port_ret)
        
        if values[t] > peak:
            peak = values[t]
        
        prev_weights = active_weights.copy()
    
    valid_returns = daily_returns[1:]
    mean_ret = np.mean(valid_returns)
    std_ret = np.std(valid_returns)
    sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 1e-10 else 0
    
    total_return = values[-1] / values[0] - 1
    n_years = n_days / 252.0
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
    
    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / running_max
    max_dd = np.min(drawdowns)
    
    is_end = int(n_days * 0.55)
    
    is_returns = daily_returns[1:is_end]
    if len(is_returns) > 10:
        is_mean = np.mean(is_returns)
        is_std = np.std(is_returns)
        is_sharpe = is_mean / is_std * np.sqrt(252) if is_std > 1e-10 else 0
    else:
        is_sharpe = 0
    
    oos_returns = daily_returns[is_end:]
    if len(oos_returns) > 10:
        oos_mean = np.mean(oos_returns)
        oos_std = np.std(oos_returns)
        oos_sharpe = oos_mean / oos_std * np.sqrt(252) if oos_std > 1e-10 else 0
    else:
        oos_sharpe = 0
    
    return {
        'sharpe': sharpe,
        'annual_return': annual_return,
        'mdd': max_dd,
        'is_sharpe': is_sharpe,
        'oos_sharpe': oos_sharpe,
        'is_oos_gap': (is_sharpe - oos_sharpe) / is_sharpe if is_sharpe > 0 else 0
    }


def generate_topk_ortho_combinations():
    """top_k + Orthogonal 팩터 조합 생성"""
    
    base_params = {
        'trend_w': 0.42, 'vol_w': 0.25, 'credit_w': 0.23, 'breadth_w': 0.10,
        'crisis_thresh': 0.70, 'bear_thresh': 0.50, 'neutral_thresh': 0.30, 'bull_thresh': 0.13,
        'ultra_bull_exp': 2.0, 'bull_exp': 1.7, 'neutral_exp': 1.2, 'bear_exp': 0.6, 'crisis_exp': 0.24,
        'fw_ultra_bull': [0.22, 0.18, 0.05, 0.07, 0.07, 0.13, 0.09, 0.08, 0.06, 0.05],
        'fw_bull': [0.20, 0.16, 0.07, 0.09, 0.09, 0.11, 0.09, 0.09, 0.05, 0.05],
        'fw_neutral': [0.11, 0.11, 0.18, 0.16, 0.11, 0.07, 0.09, 0.07, 0.05, 0.05],
        'fw_bear': [0.07, 0.07, 0.26, 0.23, 0.14, 0.05, 0.06, 0.05, 0.04, 0.03],
        'fw_crisis': [0.05, 0.05, 0.30, 0.26, 0.14, 0.05, 0.05, 0.05, 0.03, 0.02],
        'rebal_days': 14, 'target_vol': 0.29, 'max_lev': 2.0, 'min_lev': 0.3,
        'cost_rate': 0.002, 'dd_warning': -0.20, 'dd_stop': -0.35,
        # C1+C3 기본 적용
        'use_pit_universe': True,
        'use_graduated_dd': False,
        'use_emergency_riskoff': False,
        'use_cvar_limit': False,
        'use_real_port_vol': False,
        'use_hysteresis': True,
        'use_fast_regime': False,
        'use_breadth': True,
        'use_adaptive_rebal': False,
        'use_regime_factor_weights': False,
        'hysteresis_width': 0.03,
    }
    
    # top_k 옵션
    top_k_options = [10, 15, 20, 25, 30, 35, 40, 48]
    
    # Orthogonal 팩터 가중치 옵션
    ortho_weight_options = [
        ([0.0, 0.0, 0.0], False),       # 사용 안함
        ([0.10, 0.10, 0.10], True),     # 균등
        ([0.15, 0.10, 0.05], True),     # E1 (Residual Mom) 강조
        ([0.05, 0.15, 0.10], True),     # E2 (Idio Vol) 강조
        ([0.05, 0.10, 0.15], True),     # E3 (BAB) 강조
        ([0.20, 0.15, 0.15], True),     # 전체 강화
        ([0.25, 0.20, 0.20], True),     # 강한 전체 강화
        ([0.30, 0.10, 0.10], True),     # E1 매우 강조
    ]
    
    # 패치 조합 옵션
    patch_options = [
        # 기본 (C1+C3)
        {'use_pit_universe': True, 'use_graduated_dd': False, 'use_emergency_riskoff': False,
         'use_real_port_vol': False},
        # B1+B2 추가
        {'use_pit_universe': True, 'use_graduated_dd': True, 'use_emergency_riskoff': True,
         'use_real_port_vol': False, 'min_lev': 0.0},
        # B4 추가
        {'use_pit_universe': True, 'use_graduated_dd': False, 'use_emergency_riskoff': False,
         'use_real_port_vol': True},
        # D3 추가
        {'use_pit_universe': True, 'use_graduated_dd': False, 'use_emergency_riskoff': False,
         'use_real_port_vol': False, 'use_regime_factor_weights': True},
    ]
    
    param_list = []
    
    for top_k in top_k_options:
        for ortho_w, use_ortho in ortho_weight_options:
            for patch in patch_options:
                params = base_params.copy()
                params['top_k'] = top_k
                params['ortho_weights'] = ortho_w
                params['use_orthogonal_factors'] = use_ortho
                params.update(patch)
                param_list.append(params)
    
    return param_list


def main():
    """메인 실행"""
    
    init_global_data()
    
    param_list = generate_topk_ortho_combinations()
    print(f"\ntop_k + Orthogonal 조합 수: {len(param_list)}")
    
    print("\n[5] top_k + Orthogonal 팩터 테스트 시작...")
    
    results = []
    start_time = time.time()
    
    for i, params in enumerate(param_list):
        try:
            result = fast_backtest(params)
            result['params'] = params
            results.append(result)
        except Exception as e:
            print(f"  Error at {i}: {e}")
        
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            remaining = (len(param_list) - i - 1) / rate
            print(f"  진행: {i+1}/{len(param_list)} ({100*(i+1)/len(param_list):.1f}%) | "
                  f"유효: {len(results)} | 남은 시간: {remaining/60:.1f}분")
    
    print("\n" + "=" * 80)
    print("테스트 완료!")
    print("=" * 80)
    
    results_oos = sorted(results, key=lambda x: x['oos_sharpe'], reverse=True)
    
    print("\n[TOP 30 - OOS Sharpe 기준]")
    print("-" * 180)
    print(f"{'순위':>4} | {'Sharpe':>8} | {'IS':>8} | {'OOS':>8} | {'Gap':>8} | {'MDD':>8} | {'Annual':>8} | {'top_k':>6} | Ortho | 패치")
    print("-" * 180)
    
    for i, r in enumerate(results_oos[:30]):
        p = r['params']
        patches = []
        if p.get('use_pit_universe'): patches.append('A2')
        if p.get('use_graduated_dd'): patches.append('B1')
        if p.get('use_emergency_riskoff'): patches.append('B2')
        if p.get('use_real_port_vol'): patches.append('B4')
        if p.get('use_hysteresis'): patches.append('C1')
        if p.get('use_breadth'): patches.append('C3')
        if p.get('use_regime_factor_weights'): patches.append('D3')
        if p.get('use_orthogonal_factors'): patches.append('E')
        
        ortho_w = p.get('ortho_weights', [0,0,0])
        ortho_str = f"[{ortho_w[0]:.2f},{ortho_w[1]:.2f},{ortho_w[2]:.2f}]"
        
        print(f"{i+1:>4} | {r['sharpe']:>8.3f} | {r['is_sharpe']:>8.3f} | {r['oos_sharpe']:>8.3f} | "
              f"{r['is_oos_gap']*100:>7.1f}% | {r['mdd']*100:>7.1f}% | {r['annual_return']*100:>7.1f}% | "
              f"{p['top_k']:>6} | {ortho_str} | {'+'.join(patches)}")
    
    # top_k별 분석
    print("\n[top_k별 평균 OOS Sharpe]")
    print("-" * 60)
    for top_k in [10, 15, 20, 25, 30, 35, 40, 48]:
        tk_results = [r for r in results if r['params']['top_k'] == top_k]
        if tk_results:
            avg_oos = np.mean([r['oos_sharpe'] for r in tk_results])
            max_oos = max([r['oos_sharpe'] for r in tk_results])
            print(f"  top_k={top_k:>2}: 평균 OOS={avg_oos:.3f}, 최대 OOS={max_oos:.3f}")
    
    # Orthogonal 팩터 효과 분석
    print("\n[Orthogonal 팩터 효과 분석]")
    print("-" * 60)
    ortho_results = [r for r in results if r['params'].get('use_orthogonal_factors')]
    non_ortho_results = [r for r in results if not r['params'].get('use_orthogonal_factors')]
    
    if ortho_results and non_ortho_results:
        avg_oos_ortho = np.mean([r['oos_sharpe'] for r in ortho_results])
        avg_oos_non_ortho = np.mean([r['oos_sharpe'] for r in non_ortho_results])
        print(f"  Orthogonal 팩터 사용 평균 OOS Sharpe: {avg_oos_ortho:.3f}")
        print(f"  Orthogonal 팩터 미사용 평균 OOS Sharpe: {avg_oos_non_ortho:.3f}")
        print(f"  차이: {avg_oos_ortho - avg_oos_non_ortho:+.3f}")
    
    # top_k < 48에서 Orthogonal 효과
    print("\n[top_k < 48에서 Orthogonal 팩터 효과]")
    print("-" * 60)
    for top_k in [10, 15, 20, 25, 30]:
        tk_ortho = [r for r in results if r['params']['top_k'] == top_k and r['params'].get('use_orthogonal_factors')]
        tk_non_ortho = [r for r in results if r['params']['top_k'] == top_k and not r['params'].get('use_orthogonal_factors')]
        
        if tk_ortho and tk_non_ortho:
            avg_ortho = np.mean([r['oos_sharpe'] for r in tk_ortho])
            avg_non_ortho = np.mean([r['oos_sharpe'] for r in tk_non_ortho])
            print(f"  top_k={top_k:>2}: Ortho={avg_ortho:.3f}, Non-Ortho={avg_non_ortho:.3f}, 차이={avg_ortho - avg_non_ortho:+.3f}")
    
    # 결과 저장
    output = {
        'timestamp': datetime.now().isoformat(),
        'total_tests': len(results),
        'top_oos': results_oos[:100],
    }
    
    with open('/home/ubuntu/v36_topk_ortho_results.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n결과 저장: /home/ubuntu/v36_topk_ortho_results.json")
    print(f"완료 시간: {datetime.now()}")
    print(f"총 소요 시간: {(time.time() - start_time)/60:.1f}분")


if __name__ == "__main__":
    main()
